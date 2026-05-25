# Sentinel Feature Inventory

Generated: May 12, 2026

This document inventories Sentinel GraphRAG features organized by category, with implementation notes and business value for enterprise banking customers.

---

**Backend & Architectural Features**

- Feature Name: Microservice Decoupling
  - Technical Implementation: Offloads embedding inference to a remote HF Space (`mohan1201/sentinel-embedding-server`) via `build_embeddings_model()` in `query_copilot.py`.
  - Business Value (The "Why"): Reduces host compute, enables independent scaling and rapid model iteration without redeploying the core service.

- Feature Name: Vector-Graph Hybrid Storage
  - Technical Implementation: Stores document chunks and 384-d vectors as `Policy` nodes with relationships in Neo4j; `create_policy_vector_index()` creates the vector index (cosine) used by retrieval.
  - Business Value: Relationship-first model plus vector search yields precise, auditable retrieval required for compliance workloads.

- Feature Name: Asymmetric Retrieval Design
  - Technical Implementation: Applies `query:` prefix for user questions and `passage:` prefix for document embeddings before calling the HF Space embedding endpoint.
  - Business Value: Improves intent vs. content separation in embeddings, increasing relevance and reducing cross-talk in retrieval.

- Feature Name: Industrial Scaling & Contracts
  - Technical Implementation: Pydantic models (`GraphAction`, `ChatRequest`, `ChatResponse`) enforce strong contracts; project is Docker-ready for containerized deployment.
  - Business Value: Simplifies secure deployment, auditing, and runtime validation in regulated banking environments.

- Feature Name: Embedding Fault-Tolerance & Fallbacks
  - Technical Implementation: `build_embeddings_model()` wraps `gradio_client.Client` creation in try/except with endpoint name overrides and non-fatal fallback behavior.
  - Business Value: Prevents a remote HF Space outage from taking down the whole `/chat` API and enables graceful degradation.

---

**AI & NLP Intelligence Features**

- Feature Name: Multilingual Linguistic Grounding
  - Technical Implementation: Dual-stage detection using local FastText (`ensure_fasttext_model()` / `detect_user_language()`) with `langdetect` fallback and a 0.50 confidence threshold.
  - Business Value: Low-latency, private language routing enabling native-language responses without third-party translation APIs.

- Feature Name: Cross-Lingual Retrieval (Multilingual Embeddings)
  - Technical Implementation: Uses `intfloat/multilingual-e5-small` deployed on HF Spaces to produce 384-d embeddings for 50+ languages; same vector space for queries and passages.
  - Business Value: Users can query in local languages (Hindi, Telugu, Spanish) and retrieve English-source policies accurately.

- Feature Name: Executive Synthesis (Auditor Personality) with High-Value Exposure Detection
  - Technical Implementation: System prompt injection in `generate_answer()` with `detect_high_value_exposure()` regex-based detection flags financial amounts exceeding ₹10 Crore as "Critical Tier 1 Risk". LLM is instructed to bold and prominently flag all high-value exposures.
  - Business Value: Automated detection of critical financial thresholds produces audit-friendly summaries that surface enterprise risks immediately.

- Feature Name: Auto-Follow-up Generation
  - Technical Implementation: `_build_followup_suggestions()` creates localized clarifying prompts under a short time budget using the LLM.
  - Business Value: Increases first-contact resolution and reduces manual back-and-forth with automated clarifying questions.

- Feature Name: Dynamic Prompt Augmentation (Prompt Modifier)
  - Technical Implementation: `prompt_modifier.py` injects run-time metadata (User Tier / Access Code, Current Date, Detected Language, Region) into system instructions prior to LLM invocation.
  - Business Value: Ensures context-aware output (e.g., bank manager vs teller receives appropriately detailed responses) and enforces role-based behavior.

- Feature Name: Hybrid Search Fusion (Reciprocal Rank Fusion)
  - Technical Implementation: Retrieval fuses vector similarity and BM25 full-text within the Cypher/Neo4j pipeline using rank-based RRF, which avoids raw score scaling issues.
  - Business Value: Eliminates semantic drift and returns exact regulatory text for explicit queries like "TDS Section 194" while preserving semantic recall.

- Feature Name: Table-Aware Structural Extraction
  - Technical Implementation: Gemini Vision (Gemini 1.5 Pro/Flash) reconstructs complex tables into Markdown/JSON with preserved row/column structure before chunking.
  - Business Value: Enables accurate extraction and retrieval of tabular financial data (e.g., rate comparisons across tenors) that normal RAG systems miss.

- Feature Name: Conversational Context Compression
  - Technical Implementation: `api.py` manages chat history by summarizing prior turns into short context tokens to keep the LLM context window small and low-cost.
  - Business Value: Reduces token costs and prevents confusion in long multi-turn audit sessions while preserving essential context.

- Feature Name: Hybrid Retrieval Fusion (RRF)
  - Technical Implementation: Rank-based hybrid fusion via Reciprocal Rank Fusion (RRF), combining vector and BM25 result ranks with configurable thresholds to discard low-confidence results.
  - Business Value: Tunable precision/recall trade-offs for different user tiers or regulatory scenarios.

- Feature Name: Prompt-specified Acronym Preservation
  - Technical Implementation: System prompt instructs the LLM to keep technical acronyms (`TDS`, `KYC`, `NEFT`) in English for regulatory clarity.
  - Business Value: Prevents mistranslation of critical domain terms, preserving auditability and legal meaning.

---

**Governance & Security (GLAC) Features**

- Feature Name: Least-Privilege Retrieval (GLAC)
  - Technical Implementation: Runtime Cypher filters `p.access_code <= $user_access_code` and segment-based constraints applied during retrieval in `retrieve_active_policy()`.
  - Business Value: Enforces enterprise least-privilege access controls, preventing unauthorized exposure of sensitive policies.

- Feature Name: Temporal Versioning & Supersession
  - Technical Implementation: Ingestion MERGE logic writes `SUPERSEDES` relationships and toggles `active: true/false` flags to indicate the authoritative policy.
  - Business Value: Ensures users see the current policy while preserving historical lineage for audits.

- Feature Name: Fact-Strict Grounding / No-Hallucination
  - Technical Implementation: LLM prompts include explicit instructions to cite `active_context` and refuse to answer outside retrieved evidence; post-processing cross-checks answers against citations.
  - Business Value: Mitigates regulatory and legal risk by avoiding unsupported claims.

- Feature Name: Audit Trail & Provenance Metadata
  - Technical Implementation: Each `ChatResponse` includes `citations` with document names, chunk offsets and retrieval scores stored in Neo4j and session logs.
  - Business Value: Makes every claim traceable to the source document for compliance reviews.

- Feature Name: Credential & Env Sanitization
  - Technical Implementation: `init_graph.py` sanitizes env vars (GROQ_API_KEY, HF_TOKEN) to avoid quoting/formatting issues during runtime.
  - Business Value: Reduces injection and misconfiguration risks in production.

---

**UI/UX & Operational Features**

- Feature Name: Universal Ingestion (Gemini Vision)
  - Technical Implementation: `render_universal_ingestion()` and server-side Gemini calls to extract text, tables, and structure from PDFs and images.
  - Business Value: Fast onboarding of legacy and vendor-supplied policy documents without manual reformatting.

- Feature Name: Interactive Citation Mapping
  - Technical Implementation: Frontend `CitationMap.jsx` + API endpoints produce visual graphs mapping Policy → Category → Rule and chunk-level evidence.
  - Business Value: Enables SMEs and auditors to quickly validate where an answer originated.

- Feature Name: Executive Summary Formatting
  - Technical Implementation: Response formatter (`format_response_with_citations()`) auto-bolds numerical figures and generates structured bullet summaries.
  - Business Value: Delivers concise, skimmable outputs for decision-makers.

- Feature Name: Ingestion Review & Human-in-the-Loop
  - Technical Implementation: Streamlit ingestion UI surfaces Curator extractions for SME correction before committing to Neo4j.
  - Business Value: Prevents garbage-in and maintains high-quality search targets for compliance.

- Feature Name: Observability & Graceful Degradation
  - Technical Implementation: Structured logs, try/except around third-party calls (HF Space, Gemini, Groq) and fallback behaviors to avoid full outages.
  - Business Value: Maintains SLAs and predictable behavior under external service degradation.

---

## Additional Similar Features (not previously enumerated)

- Feature Name: Session Persistence & Replay
  - Technical Implementation: Session messages saved to Neo4j (`_save_messages_tx()`) enabling replay and offline analysis.
  - Business Value: Supports investigations, model improvement, and audit replay.

- Feature Name: Follow-up Budgeting & Latency Controls
  - Technical Implementation: Time-boxed thread pools for quick follow-up suggestion generation to avoid request tail-latency.
  - Business Value: Ensures snappy UX and predictable response SLAs.

---

## Comparison Table

| Feature Category | Sentinel Implementation | Standard RAG (Competitors) |
|---|---:|---|
| Security | GLAC (Graph-Level Access Control) enforced at retrieval | Simple text filtering or role checks (often post-filtering) |
| Retrieval | Hybrid Fusion (Vector + BM25 + RRF) with asymmetric prefixes | Vector-only (semantic search) or keyword-only (BM25) |
| Architecture | Decoupled Microservices (HF Spaces, Gradio client) | Monolithic (embedding & retrieval tied to same stack) |
| Languages | Local FastText detection + langdetect fallback (private) | Google Translate / third-party (data egress risk) |
| Versioning | Temporal Supersession (`active: true`, `SUPERSEDES`) | Last-in wins or manual archival (less deterministic) |
| Prompting | Dynamic Prompt Augmentation (`prompt_modifier.py`) for role & date | Static prompts or per-request manual composition |
| Tables | Table-aware extraction (Gemini reconstructs tables to JSON/Markdown) | OCR plain text (loses rows/columns) |
| Context | Conversational Context Compression (summaries) | Full-history tokens leading to token bloat |
| Fault Tolerance | Embedding client fallbacks and timeboxed follow-ups | External API failures may cause service outage |

---

If you want this committed as a workspace file, it's saved at: `docs/FEATURE_INVENTORY.md`.

Would you like file/line citations added for each implementation reference (e.g., `query_copilot.py#L280-L330`)?