"""Sentinel: Retrieval and Grounded Generation Core for Active Policy Q&A."""

from __future__ import annotations

import json
import os
import hashlib
from typing import Any, Dict, List, Optional, Sequence

from dotenv import find_dotenv, load_dotenv
from langchain_groq import ChatGroq
from neo4j import Driver
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import BaseModel, Field
import requests

from connect import build_neo4j_driver as _build_neo4j_driver_from_connect
from init_graph import CATEGORY_VALUES

# Default model lookup paths (env override supported)
FASTTEXT_MODEL_ENV = os.getenv("FASTTEXT_LANG_MODEL")
DEFAULT_FASTTEXT_PATHS = [
    FASTTEXT_MODEL_ENV,
    os.path.join(os.path.dirname(__file__), "models", "lid.176.bin"),
    os.path.join(os.path.dirname(__file__), "lid.176.bin"),
]

# Minimal ISO code -> language name mapping
LANG_CODE_TO_NAME = {
    "en": "English",
    "hi": "Hindi",
    "te": "Telugu",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ar": "Arabic",
    "bn": "Bengali",
    "pa": "Punjabi",
    "mr": "Marathi",
    "ta": "Tamil",
    "kn": "Kannada",
    "ml": "Malayalam",
    "gu": "Gujarati",
    "ur": "Urdu",
}

_FASTTEXT_MODEL: Any = None


def _find_fasttext_model() -> str | None:
    """Return path to FastText model if present, else None."""

    candidates: list[str] = []
    for model_path in DEFAULT_FASTTEXT_PATHS:
        if not model_path:
            continue
        candidates.append(model_path)
        root, ext = os.path.splitext(model_path)
        if not ext:
            candidates.append(root + ".bin")
            candidates.append(root + ".ftz")
        elif ext.lower() == ".bin":
            candidates.append(root + ".ftz")
        elif ext.lower() == ".ftz":
            candidates.append(root + ".bin")

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def ensure_fasttext_model() -> str | None:
    """Load FastText model if available and return its resolved path."""

    global _FASTTEXT_MODEL
    if _FASTTEXT_MODEL is not None:
        return _find_fasttext_model()

    model_path = _find_fasttext_model()
    if not model_path:
        return None

    try:
        import fasttext as _fasttext  # type: ignore

        _FASTTEXT_MODEL = _fasttext.load_model(model_path)  # type: ignore
        return model_path
    except Exception:
        _FASTTEXT_MODEL = None
        return None


class SupervisorFilters(BaseModel):
    """Strict supervisor output used to route policy retrieval."""

    categories: List[str] = Field(
        default_factory=list,
        description="Ontology category filters selected from the fixed Sentinel category set.",
    )
    customer_types: List[str] = Field(
        default_factory=list,
        description="Customer segment filters such as NRI, MSME, or Corporate.",
    )
    document_names: List[str] = Field(
        default_factory=list,
        description="Exact policy document names when the query refers to a known policy title.",
    )
    required_docs: List[str] = Field(
        default_factory=list,
        description="Required document filters derived from the query.",
    )
    focus_terms: List[str] = Field(
        default_factory=list,
        description="Short topic phrases that help disambiguate the supervised intent.",
    )
    only_latest: bool = Field(
        default=True,
        description="Whether the retrieval should prefer only active non-superseded policy truth.",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of policy rows to retrieve.",
    )


def _dedupe_preserve_order(values: Sequence[str]) -> List[str]:
    """Return a stable de-duplicated list of strings."""

    seen: set[str] = set()
    cleaned: List[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    """Parse a JSON object from model output with lightweight cleanup."""

    cleaned_text = (raw_text or "").strip()
    if not cleaned_text:
        return {}

    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        cleaned_text = cleaned_text.replace("json\n", "", 1).strip()

    try:
        parsed = json.loads(cleaned_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start_index = cleaned_text.find("{")
        end_index = cleaned_text.rfind("}")
        if start_index >= 0 and end_index > start_index:
            try:
                parsed = json.loads(cleaned_text[start_index : end_index + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def _canonicalize_category(value: str) -> str | None:
    """Map a supervisor category to the canonical ontology label."""

    normalized = str(value).strip()
    if not normalized:
        return None

    for category in CATEGORY_VALUES:
        if category == normalized or category.lower() == normalized.lower():
            return category
    return None


def _normalize_supervisor_filters(raw_filters: dict[str, Any]) -> dict[str, Any]:
    """Coerce supervisor JSON into the canonical filter schema."""

    parsed = SupervisorFilters.model_validate(raw_filters or {})
    categories = _dedupe_preserve_order(
        [
            canonical
            for canonical in (
                _canonicalize_category(value) for value in parsed.categories
            )
            if canonical is not None
        ]
    )
    customer_types = _dedupe_preserve_order(parsed.customer_types)
    document_names = _dedupe_preserve_order(parsed.document_names)
    required_docs = _dedupe_preserve_order(parsed.required_docs)
    focus_terms = _dedupe_preserve_order(parsed.focus_terms)

    return {
        "categories": categories,
        "customer_types": customer_types,
        "document_names": document_names,
        "required_docs": required_docs,
        "focus_terms": focus_terms,
        "only_latest": bool(parsed.only_latest),
        "top_k": int(parsed.top_k),
    }


def _invoke_llm_json(llm: ChatGroq, prompt_text: str) -> str:
    """Invoke Groq with JSON mode when available, then return raw text."""

    try:
        runnable: Any = llm.bind(response_format={"type": "json_object"})
        response = runnable.invoke(prompt_text)
        content = getattr(response, "content", response)
        return str(content).strip()
    except Exception:
        pass

    try:
        response = llm.invoke(prompt_text, response_format={"type": "json_object"})
        content = getattr(response, "content", response)
        return str(content).strip()
    except Exception:
        pass

    response = llm.invoke(prompt_text)
    content = getattr(response, "content", response)
    return str(content).strip()


def _invoke_llm_text(llm: ChatGroq, prompt_text: str) -> str:
    """Invoke Groq and normalize the response to plain text."""

    response = llm.invoke(prompt_text)
    content = getattr(response, "content", response)
    return str(content).strip()


def _render_policy_context(active_context: Sequence[ActivePolicy]) -> str:
    """Render retrieved policies into a compact worker context block."""

    context_blocks: List[str] = []
    for item in active_context:
        context_blocks.append(
            (
                f"Document: {item.document_name}\n"
                f"Category: {item.category}\n"
                f"Applies To: {', '.join(item.customer_types) if item.customer_types else 'None'}\n"
                f"Requires: {', '.join(item.required_docs) if item.required_docs else 'None'}\n"
                f"Rule: {item.extracted_rule}"
            )
        )
    return "\n\n".join(context_blocks)


def _build_worker_prompt(
    active_context: Sequence[ActivePolicy],
    user_question: str,
    detected_language: str,
    high_value_instruction: str,
) -> str:
    """Build the worker-stage messages for grounded compliance generation."""

    system_prompt = f"""
You are the Worker stage in a Supervisor-Worker compliance pipeline.
Answer only from the provided retrieved policy context.
Do not browse the graph, infer missing facts, or introduce outside policy knowledge.
If the context does not support the answer, return exactly:
"{STRICT_NO_ANSWER}"

The user's query is in {detected_language}. You must answer only in {detected_language}.
Keep technical acronyms like TDS, KYC, NEFT, and RBI in English for regulatory clarity.
Use the retrieved policy text as the only source of truth.
When you quote numbers, preserve the exact numeric formatting from the context.
{high_value_instruction}
""".strip()

    user_prompt = f"""
Retrieved policy context:
{_render_policy_context(active_context)}

User question:
{user_question}
""".strip()

    return f"{system_prompt}\n\n{user_prompt}"


def supervisor_extract_filters(user_query: str, llm: ChatGroq | None = None) -> dict[str, Any]:
    """Extract strict policy retrieval filters from the user query."""

    query_text = (user_query or "").strip()
    if not query_text:
        return {
            "categories": [],
            "customer_types": [],
            "document_names": [],
            "required_docs": [],
            "focus_terms": [],
            "only_latest": True,
            "top_k": 5,
        }

    llm_client = llm or build_groq_llm()

    system_prompt = f"""
You are Sentinel Supervisor, an intent router for a banking compliance GraphRAG.
Your job is to convert the user query into a strict JSON object that can be used for deterministic Neo4j retrieval.

Rules:
- Output JSON only. No markdown, no commentary, no code fences.
- Use only the fixed ontology categories below.
- If a field is not present in the query, return an empty list for that field.
- Do not invent document names, customer types, or categories.
- Prefer the narrowest valid category set.
- If the query is broad, still return the best matching category filters rather than free text.
- Preserve original wording for customer types and document names when they are explicit.

Allowed ontology categories:
{', '.join(CATEGORY_VALUES)}

Return this exact schema:
{{
  "categories": ["..."],
  "customer_types": ["..."],
  "document_names": ["..."],
  "required_docs": ["..."],
  "focus_terms": ["..."],
  "only_latest": true,
  "top_k": 5
}}
""".strip()

    prompt_text = f"{system_prompt}\n\nUser query:\n{query_text}".strip()
    raw_response = _invoke_llm_json(llm_client, prompt_text)
    parsed_response = _extract_json_object(raw_response)
    normalized = _normalize_supervisor_filters(parsed_response)

    if not any(
        normalized[field]
        for field in ("categories", "customer_types", "document_names", "required_docs", "focus_terms")
    ):
        normalized.setdefault("only_latest", True)

    return normalized



STRICT_NO_ANSWER = (
    "I cannot find a verified active policy for this in the current database."
)

# Embedding dimensionality expected from e5-small multilingual embeddings (384-dim)
# Model: intfloat/multilingual-e5-small deployed on HF Spaces (mohan1201/sentinel-embedding-server)
EMBEDDING_DIM = 384

# High-value exposure thresholds (INR)
HIGH_VALUE_EXPOSURE_THRESHOLD = 10_000_000  # ₹10 Crore


def detect_high_value_exposure(text: str, threshold: int = HIGH_VALUE_EXPOSURE_THRESHOLD) -> List[Dict[str, Any]]:
    """Detect high-value financial exposures (INR amounts > threshold) in text.
    
    Uses regex to extract currency amounts in INR and flags those exceeding
    the configured threshold for executive-level risk flagging.
    
    Args:
        text: Source text to scan for currency amounts (typically from active_context).
        threshold: INR amount threshold for flagging (default: ₹10 Crore).
    
    Returns:
        List[Dict]: Each dict contains {'amount': <float>, 'raw_text': <str>, 'risk_level': <str>}
    """
    import re
    
    if not text:
        return []
    
    findings = []
    
    # Regex patterns for INR amounts with various formats
    patterns = [
        r'[₹]\s*([0-9,]+(?:\.[0-9]{2})?)',  # ₹ symbol
        r'INR\s+([0-9,]+(?:\.[0-9]{2})?)',  # INR prefix
        r'Rs\.?\s+([0-9,]+(?:\.[0-9]{2})?)',  # Rs. or Rs prefix
        r'Cr(?:ore)?\s+([0-9,]+(?:\.[0-9]{2})?)',  # Crore amounts
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            amount_str = match.group(1).replace(',', '')
            try:
                amount = float(amount_str)
                
                # Scale crore amounts to base INR
                if 'crore' in match.group(0).lower() or 'cr' in match.group(0).lower():
                    amount = amount * 10_000_000
                
                if amount >= threshold:
                    risk_level = 'CRITICAL_TIER_1' if amount >= threshold else 'WARNING'
                    findings.append({
                        'amount': amount,
                        'raw_text': match.group(0),
                        'risk_level': risk_level,
                        'position': match.start(),
                    })
            except (ValueError, TypeError):
                continue
    
    return sorted(findings, key=lambda x: x['position'], reverse=True)  # Sort descending by position


class ActivePolicy(BaseModel):
    """Verified active policy context returned from Neo4j retrieval."""

    document_name: str = Field(..., description="Policy document identifier.")
    category: str = Field(..., description="SME-governed ontology category.")
    customer_types: List[str] = Field(
        default_factory=list,
        description="Customer types explicitly connected through APPLIES_TO edges.",
    )
    required_docs: List[str] = Field(
        default_factory=list,
        description="Required documents explicitly connected through REQUIRES edges.",
    )
    extracted_rule: str = Field(..., description="Normalized policy rule summary.")
    source_text: str = Field(..., description="Original policy source text.")
    score: float = Field(..., description="Raw hybrid retrieval score from Neo4j.")
    match_confidence: float = Field(
        ...,
        description="Normalized retrieval confidence percentage for UI display.",
    )
    version_status: str = Field(
        ...,
        description="Version status flag for response metadata.",
    )


def _normalize_match_confidence(score: float, max_score: float) -> float:
    """Normalize raw retrieval scores for stable UI confidence display.

    Args:
        score: Raw score for a candidate policy.
        max_score: Highest score in the retrieved result set.

    Returns:
        float: Bounded confidence value scaled for analyst readability.
    """

    if max_score <= 0:
        return 0.0
    normalized = max(0.0, min(score / max_score, 1.0))
    return round(normalized * 96.5, 1)


def detect_user_language(text: str) -> str:
    """Detect user language using FastText with langdetect fallback.

    Returns a full language name (e.g., 'Telugu', 'Hindi', 'Spanish').
    Defaults to 'English' on low confidence or errors.
    """
    text = (text or "").strip()
    if not text:
        return "English"

    # 1) FastText path
    try:
        model_path = ensure_fasttext_model()
        if model_path and _FASTTEXT_MODEL is not None:
            labels, probs = _FASTTEXT_MODEL.predict(text, k=1)  # type: ignore
            if labels and probs:
                code = labels[0].replace("__label__", "")
                confidence = float(probs[0])
                if confidence >= 0.50:
                    return LANG_CODE_TO_NAME.get(code, code)
                # low confidence -> fall through to fallback
    except Exception:
        pass

    # 2) langdetect fallback
    try:
        from langdetect import detect as _langdetect_detect  # type: ignore

        if _langdetect_detect is not None:
            code = _langdetect_detect(text)  # type: ignore
            return LANG_CODE_TO_NAME.get(code, code)
    except Exception:
        pass

    # Default
    return "English"


def load_environment() -> None:
    """Load environment variables and sanitize credential formatting.

    Returns:
        None: Environment is updated in process memory.
    """

    dotenv_path = find_dotenv()
    load_dotenv(dotenv_path=dotenv_path, override=True)

    # Normalize values to avoid quoted strings breaking auth/uri parsing.
    for key in (
        "GROQ_API_KEY",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
    ):
        value = os.environ.get(key)
        if value is not None:
            os.environ[key] = value.strip().strip('"').strip("'")

    neo4j_user = os.environ.get("NEO4J_USER")
    neo4j_username = os.environ.get("NEO4J_USERNAME")
    if neo4j_user and not neo4j_username:
        os.environ["NEO4J_USERNAME"] = neo4j_user
    elif neo4j_username and not neo4j_user:
        os.environ["NEO4J_USER"] = neo4j_username


def _load_and_sanitize_env() -> None:
    """Backward-compatible alias for legacy callers.

    Returns:
        None: Delegates to load_environment.
    """

    load_environment()


def _to_bolt_uri(uri: str) -> str:
    """Convert Neo4j routing URI formats into direct Bolt transport forms.

    Args:
        uri: Original configured Neo4j URI.

    Returns:
        str: Direct URI variant suitable for non-clustered deployments.
    """

    if uri.startswith("neo4j://"):
        return uri.replace("neo4j://", "bolt://", 1)
    if uri.startswith("neo4j+s://"):
        return uri.replace("neo4j+s://", "bolt+s://", 1)
    if uri.startswith("neo4j+ssc://"):
        return uri.replace("neo4j+ssc://", "bolt+ssc://", 1)
    return uri



def build_neo4j_driver() -> Driver:
    """Create Neo4j driver from the shared environment-driven factory.

    Returns:
        Driver: Verified Neo4j driver instance.
    """

    return _build_neo4j_driver_from_connect()


def build_groq_llm() -> ChatGroq:
    """Build Groq LLM client used for grounded response synthesis.

    Returns:
        ChatGroq: Configured deterministic LLM client.

    Raises:
        ValueError: If GROQ_API_KEY is absent.
    """

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set. Export it before running this script.")

    return ChatGroq(model="llama-3.3-70b-versatile", temperature=0, api_key=api_key)


def build_embeddings_model():
    """Build a Gradio Space-backed embeddings client.

    The returned object exposes `embed_query(text)` so the rest of the
    retrieval pipeline can stay unchanged.
    """

    class _FallbackEmbeddings:
        def embed_query(self, text: str):
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector: list[float] = []
            for index in range(EMBEDDING_DIM):
                byte_value = digest[index % len(digest)]
                vector.append(byte_value / 255.0)
            return vector

    from gradio_client import Client

    space_name = os.getenv("HF_EMBEDDING_SPACE", "mohan1201/sentinel-embedding-server")
    try:
        client = Client(space_name)
    except Exception as exc:
        print(f"[WARNING] HF embeddings client init failed for {space_name}: {exc}. Using local fallback embeddings.")
        return _FallbackEmbeddings()

    # Default to '/embed' endpoint; allow override via HF_EMBEDDING_API_NAME.
    configured_api_name = os.getenv("HF_EMBEDDING_API_NAME", "/embed").strip()

    def get_embedding(text: str):
        api_candidates = []
        if configured_api_name:
            api_candidates.append(configured_api_name)
            if configured_api_name.startswith("/"):
                api_candidates.append(configured_api_name.lstrip("/"))
            else:
                api_candidates.append(f"/{configured_api_name}")

        # Deduplicate while preserving order.
        seen = set()
        unique_candidates = [name for name in api_candidates if not (name in seen or seen.add(name))]

        last_error: Exception | None = None
        for candidate in unique_candidates:
            try:
                return client.predict(text, api_name=candidate)
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        return client.predict(text)

    class _GradioSpaceEmbeddings:
        def embed_query(self, text: str):
            result = get_embedding(text)
            if isinstance(result, list):
                if result and isinstance(result[0], (int, float)):
                    return result
                if result and isinstance(result[0], list):
                    return result[0]
            return result

    return _GradioSpaceEmbeddings()


def retrieve_active_policy(
    driver: Driver,
    user_question: str,
    question_embedding: Sequence[float],
    top_k: int = 5,
    only_latest: bool = True,
    user_tier: int = 1,
    similarity_threshold: float = 0.5,
    supervisor_filters: dict[str, Any] | None = None,
) -> List[ActivePolicy]:
    """Retrieve active policies using supervisor-extracted exact filters."""

    filters = supervisor_filters or supervisor_extract_filters(user_question)
    normalized_filters = _normalize_supervisor_filters(filters)

    categories = normalized_filters["categories"]
    customer_types = normalized_filters["customer_types"]
    document_names = normalized_filters["document_names"]
    required_docs = normalized_filters["required_docs"]
    focus_terms = normalized_filters["focus_terms"]
    resolved_only_latest = bool(normalized_filters.get("only_latest", only_latest))
    resolved_top_k = int(normalized_filters.get("top_k", top_k))

    if not any((categories, customer_types, document_names, required_docs, focus_terms)):
        return []

    cypher_query = """
    MATCH (p:Policy)
    WHERE ($user_tier = 1 OR p.access_code = 2)
    OPTIONAL MATCH (superseder)-[supersedes_rel]->(p)
    WHERE type(supersedes_rel) = 'SUPERSEDES'
    OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
    OPTIONAL MATCH (p)-[:APPLIES_TO]->(ct:CustomerType)
    OPTIONAL MATCH (p)-[:REQUIRES]->(dr:DocumentRequirement)
    WITH
        p,
        c,
        collect(DISTINCT ct.name) AS customer_types,
        collect(DISTINCT dr.name) AS required_docs,
        count(supersedes_rel) AS supersedes_count
    WITH
        p,
        c,
        [item IN customer_types WHERE item IS NOT NULL] AS customer_types,
        [item IN required_docs WHERE item IS NOT NULL] AS required_docs,
        supersedes_count
    WHERE ($only_latest = false OR supersedes_count = 0)
      AND (size($categories) = 0 OR coalesce(c.name, "") IN $categories)
      AND (size($customer_types) = 0 OR any(item IN customer_types WHERE item IN $customer_types))
      AND (size($required_docs) = 0 OR any(item IN required_docs WHERE item IN $required_docs))
      AND (size($document_names) = 0 OR any(item IN $document_names WHERE toLower(item) = toLower(p.name)))
      AND (
            size($focus_terms) = 0
            OR any(term IN $focus_terms WHERE
                toLower(coalesce(p.name, "")) CONTAINS toLower(term)
                OR toLower(coalesce(p.extracted_rule, "")) CONTAINS toLower(term)
                OR toLower(coalesce(p.source_text, "")) CONTAINS toLower(term)
            )
      )
    WITH
        p,
        c,
        customer_types,
        required_docs,
        supersedes_count,
        (
            CASE WHEN coalesce(c.name, "") IN $categories THEN 4 ELSE 0 END +
            CASE WHEN any(item IN customer_types WHERE item IN $customer_types) THEN 3 ELSE 0 END +
            CASE WHEN any(item IN required_docs WHERE item IN $required_docs) THEN 2 ELSE 0 END +
            CASE WHEN any(item IN $document_names WHERE toLower(item) = toLower(p.name)) THEN 5 ELSE 0 END +
            CASE WHEN size($focus_terms) > 0 AND any(term IN $focus_terms WHERE
                toLower(coalesce(p.name, "")) CONTAINS toLower(term)
                OR toLower(coalesce(p.extracted_rule, "")) CONTAINS toLower(term)
                OR toLower(coalesce(p.source_text, "")) CONTAINS toLower(term)
            ) THEN 1 ELSE 0 END
        ) AS score
    RETURN
        p.name AS document_name,
        coalesce(c.name, "General") AS category,
        coalesce(p.extracted_rule, "") AS extracted_rule,
        coalesce(p.source_text, "") AS source_text,
        customer_types,
        required_docs,
        score,
        CASE WHEN supersedes_count = 0 THEN "LATEST" ELSE "SUPERSEDED" END AS version_status
    ORDER BY score DESC, document_name ASC
    LIMIT $top_k
    """

    try:
        with driver.session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(
                        cypher_query,
                        user_tier=user_tier,
                        only_latest=resolved_only_latest,
                        top_k=resolved_top_k,
                        categories=categories,
                        customer_types=customer_types,
                        document_names=document_names,
                        required_docs=required_docs,
                        focus_terms=focus_terms,
                    )
                )
            )

        if not records:
            return []

        max_score = max(float(record["score"]) for record in records) if records else 0.0
        policies: List[ActivePolicy] = []

        for record in records:
            raw_score = float(record["score"])
            policies.append(
                ActivePolicy(
                    document_name=record["document_name"],
                    category=record["category"],
                    customer_types=[
                        value
                        for value in (record.get("customer_types") or [])
                        if value is not None
                    ],
                    required_docs=[
                        value
                        for value in (record.get("required_docs") or [])
                        if value is not None
                    ],
                    extracted_rule=record["extracted_rule"],
                    source_text=record["source_text"],
                    score=raw_score,
                    match_confidence=_normalize_match_confidence(raw_score, max_score),
                    version_status=record["version_status"],
                )
            )

        return policies
    except ServiceUnavailable as error:
        print(f"Neo4j connection dropped during retrieval: {error}")
        return []
    except Neo4jError as error:
        print(f"Neo4j query error during retrieval: {error}")
        return []
    except Exception as error:
        print(f"Unexpected retrieval error: {error}")
        return []


def _is_missing_fulltext_index(error: Neo4jError) -> bool:
    """Detect missing full-text index errors for graceful fallback."""

    error_text = str(error).lower()
    return (
        "policy_keywords" in error_text
        and "index" in error_text
        and (
            "does not exist" in error_text
            or "not found" in error_text
            or "unknown" in error_text
            or "there is no such" in error_text
        )
    )


def _retrieve_active_policy_hybrid(
    driver: Driver,
    user_question: str,
    question_embedding: Sequence[float],
    top_k: int = 5,
    only_latest: bool = True,
    user_tier: int = 1,
    similarity_threshold: float = 0.5,
) -> List[ActivePolicy]:
    """Fallback to the legacy hybrid vector/full-text retrieval path."""

    cypher_query = """
    CALL {
        WITH $question_embedding AS qe, $user_question AS uq, $top_k AS tk, $user_tier AS user_tier, $similarity_threshold AS similarity_threshold

        CALL {
            WITH qe, user_tier, similarity_threshold
            MATCH (p:Policy)
            SEARCH p IN (
                VECTOR INDEX policy_embeddings
                FOR qe
                LIMIT 25
            ) SCORE AS vector_score
            WHERE (user_tier = 1 OR p.access_code = 2) AND vector_score > similarity_threshold
            WITH p, vector_score
            ORDER BY vector_score DESC
            WITH collect({p: p, score: vector_score}) AS vector_hits
            UNWIND range(0, size(vector_hits) - 1) AS idx
            WITH vector_hits[idx].p AS p, (1.0 / (60.0 + idx + 1)) AS rrf_score
            RETURN collect({p: p, rrf_score: rrf_score}) AS vector_rows
        }

        CALL {
            CALL db.index.fulltext.queryNodes('policy_keywords', $user_question, {limit: $top_k})
            YIELD node AS p, score AS raw_text_score
            WHERE ($user_tier = 1 OR p.access_code = 2)
            WITH p, raw_text_score
            ORDER BY raw_text_score DESC
            WITH collect({p: p, score: raw_text_score}) AS text_hits
            UNWIND range(0, size(text_hits) - 1) AS idx
            WITH text_hits[idx].p AS p, (1.0 / (60.0 + idx + 1)) AS rrf_score
            RETURN collect({p: p, rrf_score: rrf_score}) AS text_rows
        }

        WITH vector_rows + text_rows AS rows
        UNWIND rows AS row
        RETURN row.p AS p, row.rrf_score AS rrf_score
    }
    WITH p, sum(rrf_score) AS combined_score
    WHERE combined_score > 0
    OPTIONAL MATCH (superseder)-[supersedes_rel]->(p)
    WHERE type(supersedes_rel) = 'SUPERSEDES'
    WITH p, combined_score, count(supersedes_rel) AS supersedes_count, $only_latest AS only_latest
    WHERE (NOT only_latest) OR supersedes_count = 0
    OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
    OPTIONAL MATCH (p)-[:APPLIES_TO]->(ct:CustomerType)
    OPTIONAL MATCH (p)-[:REQUIRES]->(dr:DocumentRequirement)
    WITH p, c, combined_score, supersedes_count, collect(DISTINCT ct.name) AS customer_types, collect(DISTINCT dr.name) AS required_docs
    RETURN p.name AS document_name,
           coalesce(c.name, "General") AS category,
           coalesce(p.extracted_rule, "") AS extracted_rule,
           coalesce(p.source_text, "") AS source_text,
           customer_types,
           required_docs,
           combined_score AS score,
           CASE WHEN supersedes_count = 0 THEN "LATEST" ELSE "SUPERSEDED" END AS version_status
    ORDER BY score DESC
    LIMIT $top_k
    """

    try:
        with driver.session() as session:
            query_params = {
                "user_question": user_question,
                "question_embedding": [float(value) for value in question_embedding],
                "top_k": top_k,
                "only_latest": only_latest,
                "user_tier": user_tier,
                "similarity_threshold": float(similarity_threshold),
            }
            try:
                records = session.execute_read(
                    lambda tx: list(tx.run(cypher_query, **query_params))
                )
            except Neo4jError as error:
                if not _is_missing_fulltext_index(error):
                    raise
                print(
                    "Full-text index 'policy_keywords' is unavailable; falling back to vector-only retrieval."
                )
                vector_only_query = """
                MATCH (p:Policy)
                SEARCH p IN (
                    VECTOR INDEX policy_embeddings
                    FOR $question_embedding
                    LIMIT 25
                ) SCORE AS score
                WHERE ($user_tier = 1 OR p.access_code = 2) AND score > $similarity_threshold
                OPTIONAL MATCH (superseder)-[supersedes_rel]->(p)
                WHERE type(supersedes_rel) = 'SUPERSEDES'
                WITH p, score, count(supersedes_rel) AS supersedes_count, $only_latest AS only_latest
                WHERE (NOT only_latest) OR supersedes_count = 0

                OPTIONAL MATCH (p)-[:BELONGS_TO]->(c:Category)
                OPTIONAL MATCH (p)-[:APPLIES_TO]->(ct:CustomerType)
                OPTIONAL MATCH (p)-[:REQUIRES]->(dr:DocumentRequirement)
                WITH p, c, score, supersedes_count, collect(DISTINCT ct.name) AS customer_types, collect(DISTINCT dr.name) AS required_docs
                RETURN p.name AS document_name,
                       coalesce(c.name, "General") AS category,
                       coalesce(p.extracted_rule, "") AS extracted_rule,
                       coalesce(p.source_text, "") AS source_text,
                       customer_types,
                       required_docs,
                       score,
                       CASE WHEN supersedes_count = 0 THEN "LATEST" ELSE "SUPERSEDED" END AS version_status
                ORDER BY score DESC
                LIMIT $top_k
                """
                records = session.execute_read(
                    lambda tx: list(
                        tx.run(
                            vector_only_query,
                            question_embedding=query_params["question_embedding"],
                            top_k=top_k,
                            only_latest=only_latest,
                            user_tier=user_tier,
                            similarity_threshold=query_params["similarity_threshold"],
                        )
                    )
                )

        if not records:
            return []

        hf_sim_endpoint = os.getenv("HF_SIMILARITY_ENDPOINT")
        hf_token = os.getenv("HF_TOKEN")
        if hf_sim_endpoint and hf_token:
            try:
                sentences = [str(rec.get("source_text", "")) for rec in records]
                payload = {"inputs": {"source_sentence": f"{user_question}", "sentences": sentences}}
                headers = {"Authorization": f"Bearer {hf_token}", "Content-Type": "application/json"}
                resp = requests.post(hf_sim_endpoint, headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                sim_results = resp.json()
                filtered = []
                for rec, score in zip(records, sim_results if isinstance(sim_results, list) else []):
                    try:
                        s = float(score)
                    except Exception:
                        if isinstance(score, dict) and "score" in score:
                            s = float(score["score"])
                        else:
                            s = 0.0
                    if s >= float(similarity_threshold):
                        filtered.append(rec)
                records = filtered
            except Exception as _sim_err:
                print(f"Similarity endpoint filtering failed: {_sim_err}")

        max_score = max(float(record["score"]) for record in records) if records else 0.0
        policies: List[ActivePolicy] = []
        for record in records:
            raw_score = float(record["score"])
            policies.append(
                ActivePolicy(
                    document_name=record["document_name"],
                    category=record["category"],
                    customer_types=[value for value in (record.get("customer_types") or []) if value is not None],
                    required_docs=[value for value in (record.get("required_docs") or []) if value is not None],
                    extracted_rule=record["extracted_rule"],
                    source_text=record["source_text"],
                    score=raw_score,
                    match_confidence=_normalize_match_confidence(raw_score, max_score),
                    version_status=record["version_status"],
                )
            )
        return policies
    except ServiceUnavailable as error:
        print(f"Neo4j connection dropped during retrieval: {error}")
        return []
    except Neo4jError as error:
        print(f"Neo4j query error during retrieval: {error}")
        return []
    except Exception as error:
        print(f"Unexpected retrieval error: {error}")
        return []


def generate_answer(
    llm: ChatGroq,
    active_context: Sequence[ActivePolicy],
    user_question: str,
    detected_language: str = "English",
    retrieval_tier: str | None = None,
) -> str:
    """Generate a grounded compliance answer from verified policy evidence."""

    if not active_context or (retrieval_tier or "").strip().lower() == "no_match":
        return STRICT_NO_ANSWER

    context_text = _render_policy_context(active_context)

    high_value_findings = detect_high_value_exposure(context_text)
    high_value_instruction = ""
    if high_value_findings:
        amounts_str = ", ".join(
            f"**INR {finding['amount']:,.0f}** (Flagged: {finding['risk_level']})"
            for finding in high_value_findings
        )
        high_value_instruction = (
            f"\n⚠️ CRITICAL ALERT: High-value exposures detected in context: {amounts_str}\n"
            "If these amounts are relevant to the user query, you must bold them prominently and flag them as Critical Tier 1 Risk."
        )

    try:
        prompt_text = _build_worker_prompt(
            active_context=active_context,
            user_question=user_question,
            detected_language=detected_language,
            high_value_instruction=high_value_instruction,
        )
        response_text = _invoke_llm_text(llm, prompt_text)
        return response_text or STRICT_NO_ANSWER
    except Exception as error:
        error_text = str(error)
        if "429" in error_text or "rate" in error_text.lower():
            return (
                "Groq API rate limit encountered while generating response. "
                "Please retry in a few seconds."
            )
        return f"Failed to generate response from Groq: {error}"


def run_router_worker_pipeline(
    driver: Driver,
    llm: ChatGroq,
    user_question: str,
    detected_language: str,
    user_tier: int = 1,
) -> tuple[dict[str, Any], List[ActivePolicy], str]:
    """Run the full supervisor-worker pipeline end to end."""

    filters = supervisor_extract_filters(user_question, llm=llm)
    active_context = retrieve_active_policy(
        driver=driver,
        user_question=user_question,
        question_embedding=[],
        top_k=int(filters.get("top_k", 5)),
        only_latest=bool(filters.get("only_latest", True)),
        user_tier=user_tier,
        supervisor_filters=filters,
    )
    if not active_context:
        try:
            fallback_embeddings = build_embeddings_model()
            question_embedding = fallback_embeddings.embed_query(f"query: {user_question}")
            active_context = _retrieve_active_policy_hybrid(
                driver=driver,
                user_question=user_question,
                question_embedding=question_embedding,
                top_k=int(filters.get("top_k", 5)),
                only_latest=bool(filters.get("only_latest", True)),
                user_tier=user_tier,
                similarity_threshold=0.75,
            )
        except Exception as error:
            print(f"Hybrid fallback retrieval failed: {error}")
    answer = generate_answer(
        llm=llm,
        active_context=active_context,
        user_question=user_question,
        detected_language=detected_language,
        retrieval_tier="matched" if active_context else "no_match",
    )
    return filters, active_context, answer


def print_response(answer: str, active_context: Sequence[ActivePolicy]) -> None:
    """Print answer text along with evidence snapshot for operators.

    Args:
        answer: Final generated answer.
        active_context: Retrieved evidence records used for grounding.

    Returns:
        None: Writes formatted output to console.
    """

    if active_context:
        evidence = ", ".join(
            f"{item.document_name} [{item.category}] (score={item.score:.4f})"
            for item in active_context
        )
    else:
        evidence = "None"

    print("\nAnswer:")
    print(answer)
    print(f"Source: {evidence}\n")


def main() -> None:
    """Run interactive CLI loop for Sentinel retrieval and generation.

    Returns:
        None: Runs until explicit user exit.
    """

    load_environment()

    try:
        driver = build_neo4j_driver()
    except ServiceUnavailable as error:
        print("Neo4j is not reachable. Start Neo4j and confirm Bolt is enabled.")
        expected_endpoint = os.getenv("NEO4J_URI") or "<unset>"
        print(f"Expected endpoint: {expected_endpoint}")
        print(f"Connection error: {error}")
        return
    except Neo4jError as error:
        print(f"Neo4j startup check failed: {error}")
        return

    try:
        llm = build_groq_llm()
        print("Sentinel Co-Pilot is ready. Type 'exit' to quit.")

        while True:
            user_question = input("\nAsk Sentinel> ").strip()
            if user_question.lower() in {"exit", "quit", "q"}:
                print("Exiting Sentinel Co-Pilot.")
                break

            # Detect user's language (FastText preferred, langdetect fallback)
            detected_language = detect_user_language(user_question)

            _, active_context, answer = run_router_worker_pipeline(
                driver=driver,
                llm=llm,
                user_question=user_question,
                detected_language=detected_language,
            )
            print_response(answer, active_context)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
