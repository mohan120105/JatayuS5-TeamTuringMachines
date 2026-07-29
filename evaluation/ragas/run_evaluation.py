from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from query_copilot import build_embeddings_model, build_groq_llm


DATASET_PATH = Path(__file__).resolve().parent / "baseline_ragas_dataset.jsonl"
DEFAULT_API_URL = "http://127.0.0.1:8000/chat"


@dataclass(frozen=True)
class BenchmarkRow:
    id: str
    question: str
    reference_answer: str
    contexts: list[str]
    source_documents: list[str]
    topic: str
    question_type: str
    notes: str


class EmbeddingAdapter:
    def __init__(self, base_embeddings: Any) -> None:
        self._base_embeddings = base_embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._base_embeddings.embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._base_embeddings.embed_query(text) for text in texts]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a separate RAGAS evaluation pass for Sentinel.")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH, help="Path to the benchmark JSONL file.")
    parser.add_argument("--api-url", type=str, default=DEFAULT_API_URL, help="Sentinel chat endpoint URL.")
    parser.add_argument("--session-id", type=str, default="ragas-demo", help="Session ID used for API calls.")
    parser.add_argument("--employee-id", type=str, default="1001", help="Employee ID used for API calls.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for evaluation artifacts.")
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout per question.")
    return parser.parse_args()


def load_rows(dataset_path: Path) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    with dataset_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            payload = json.loads(raw_line)
            rows.append(
                BenchmarkRow(
                    id=payload["id"],
                    question=payload["question"],
                    reference_answer=payload["reference_answer"],
                    contexts=list(payload["contexts"]),
                    source_documents=list(payload["source_documents"]),
                    topic=payload["topic"],
                    question_type=payload["question_type"],
                    notes=payload["notes"],
                )
            )
    return rows


def build_chat_url(api_url: str) -> str:
    cleaned = api_url.rstrip("/")
    if cleaned.endswith("/chat"):
        return cleaned
    return f"{cleaned}/chat"


def call_chat_api(api_url: str, session_id: str, employee_id: str, question: str, timeout: float) -> dict[str, Any]:
    target_url = build_chat_url(api_url)
    try:
        response = requests.post(
            target_url,
            json={
                "session_id": session_id,
                "user_question": question,
                "employee_id": employee_id,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "answer": payload.get("answer", ""),
            "citations": payload.get("citations", []),
            "retrieval_tier": payload.get("retrieval_tier", ""),
            "sentinel_reasoning": payload.get("sentinel_reasoning", ""),
            "route_source": payload.get("route_source", ""),
            "route_reason": payload.get("route_reason", ""),
        }
    except requests.exceptions.ConnectionError as exc:
        raise SystemExit(
            f"\n[ERROR] Could not connect to Sentinel API endpoint at '{target_url}'.\n"
            f"Make sure the FastAPI backend is running ('python -m uvicorn api:app --port 8000') "
            f"or pass '--api-url https://sentinel-hybridrag.onrender.com'."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        err_detail = response.text if 'response' in locals() else str(exc)
        raise SystemExit(
            f"\n[ERROR] Sentinel API request failed for '{target_url}': {err_detail}"
        ) from exc


def collect_live_responses(rows: list[BenchmarkRow], api_url: str, session_id: str, employee_id: str, timeout: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        print(f"[{index}/{len(rows)}] {row.id}")
        live = call_chat_api(api_url, session_id, employee_id, row.question, timeout)
        results.append(
            {
                "id": row.id,
                "question": row.question,
                "reference_answer": row.reference_answer,
                "contexts": row.contexts,
                "source_documents": row.source_documents,
                "topic": row.topic,
                "question_type": row.question_type,
                "notes": row.notes,
                **live,
            }
        )
    return results


def build_ragas_dataset(rows: list[dict[str, Any]]) -> Dataset:
    from datasets import Dataset

    return Dataset.from_list(
        [
            {
                "question": row["question"],
                "answer": row["answer"],
                "contexts": row["contexts"],
                "ground_truth": row["reference_answer"],
            }
            for row in rows
        ]
    )


def evaluate_with_ragas(dataset: Dataset, output_dir: Path) -> pd.DataFrame:
    try:
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    except Exception as exc:
        raise SystemExit(
            "RAGAS is not installed in this environment. Install evaluation/ragas/requirements.txt first."
        ) from exc

    raw_llm = build_groq_llm()
    raw_embeddings = build_embeddings_model()

    try:
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        llm = LangchainLLMWrapper(raw_llm)
        embeddings = LangchainEmbeddingsWrapper(raw_embeddings)
    except Exception:
        llm = raw_llm
        embeddings = EmbeddingAdapter(raw_embeddings)

    result = evaluate(
        dataset=dataset,
        metrics=[answer_relevancy, faithfulness, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
    )

    result_frame = result.to_pandas()
    result_frame.to_csv(output_dir / "ragas_scores.csv", index=False)
    return result_frame


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> int:
    args = parse_args()
    rows = load_rows(args.dataset)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or (Path(__file__).resolve().parent / "runs" / timestamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    live_rows = collect_live_responses(rows, args.api_url, args.session_id, args.employee_id, args.timeout)
    write_jsonl(output_dir / "responses.jsonl", live_rows)

    try:
        ragas_dataset = build_ragas_dataset(live_rows)
        scores = evaluate_with_ragas(ragas_dataset, output_dir)

        summary = {
            column: float(scores[column].mean())
            for column in scores.columns
            if pd.api.types.is_numeric_dtype(scores[column])
        }
        (output_dir / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print(json.dumps(summary, indent=2))
        print(f"Artifacts written to {output_dir}")
    except (ImportError, ModuleNotFoundError) as exc:
        print(
            f"\n[SUCCESS] Collected {len(live_rows)} responses from Sentinel API.\n"
            f"Artifacts written to: {output_dir / 'responses.jsonl'}\n"
            f"Note: To compute full RAGAS LLM scores, install dependencies: pip install datasets ragas pandas ({exc})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
