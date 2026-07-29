"""Sentinel: Edge AI Prompt Modifier for Enterprise Hybrid GraphRAG Banking.

Architectural purpose:
- Implements a remote prompt refinement stage that transforms terse user text
    into retrieval-grade search intent for downstream GraphRAG components.
- Uses a Hugging Face-hosted router microservice to avoid local model storage
    and local inference dependencies on the application server.
- Keeps the service import-light so deployment can succeed without GGUF files
    or llama-cpp-python present on disk.

Compliance relevance:
- Supports Strict Retrieval Constraint workflows by improving intent precision
    before graph/vector retrieval orchestration.
- Contributes to Stateful Auditability through deterministic, bounded prompt
    generation behavior suitable for reproducible incident review.
"""

from __future__ import annotations

import os
import time

import requests

HF_ROUTER_URL = os.getenv("HF_ROUTER_URL", "https://mohan1201-sentinel-gemma-router.hf.space/optimize_prompt").strip() or None
HF_API_TOKEN = os.getenv("HF_API_TOKEN", "").strip() or None

if not HF_ROUTER_URL:
    import sys
    print(f"[WARNING] HF_ROUTER_URL not configured. Prompt enhancement disabled.", file=sys.stderr)

def enhance_query_for_graphrag(user_query: str) -> str:
    """Rewrite user input into a retrieval-optimized GraphRAG query string.

    The function performs controlled prompt normalization to increase retrieval
    precision for Sentinel's hybrid graph and vector stack while minimizing
    hallucination risk through domain-grounded constraints.

    Args:
        user_query: Raw user question or shorthand intent text.

    Returns:
        str: A compact, professional query phrasing suitable for downstream
        retrieval and ranking.

    Falls back to the original user query when the router is unavailable,
    slow, or returns an unexpected payload.
    """
    if not HF_ROUTER_URL:
        error_msg = "HF_ROUTER_URL environment variable is not set. Returning the original query."
        print(f"[WARNING] {error_msg}")
        return user_query.strip()

    start_time = time.time()
    headers = {"Content-Type": "application/json"}
    if HF_API_TOKEN:
        headers["Authorization"] = f"Bearer {HF_API_TOKEN}"

    try:
        response = requests.post(
            HF_ROUTER_URL,
            json={"prompt": user_query},
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        error_msg = f"HuggingFace router request failed (URL: {HF_ROUTER_URL}): {exc}"
        print(f"[WARNING] {error_msg}. Returning the original query.")
        return user_query.strip()

    try:
        payload = response.json()
    except Exception as exc:
        error_msg = f"HuggingFace router response is not valid JSON: {exc}"
        print(f"[WARNING] {error_msg}. Returning the original query.")
        return user_query.strip()

    optimized_query = payload.get("optimized_query")
    if not optimized_query:
        error_msg = f"HuggingFace router response missing 'optimized_query' field. Got: {payload}"
        print(f"[WARNING] {error_msg}. Returning the original query.")
        return user_query.strip()

    print(f"⚡ Modifier ran in {round(time.time() - start_time, 2)}s")
    return str(optimized_query).strip()


if __name__ == "__main__":
    raw_1 = "nri docs needed"
    print(f"Raw Input 1: {raw_1}")
    print(f"Enhanced 1:  {enhance_query_for_graphrag(raw_1)}")

    raw_2 = "fd rates for senior"
    print(f"Raw Input 2: {raw_2}")
    print(f"Enhanced 2:  {enhance_query_for_graphrag(raw_2)}")
