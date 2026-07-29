# RAGAS Evaluation Pipeline

This directory is a standalone evaluation path for Sentinel. It does not modify the core chat or retrieval code.

## What is included

- A baseline gold dataset derived from the text-extractable documents in `v1_baseline_docs/`.
- A separate runner that can call the existing `/chat` API, capture live answers, and score them with RAGAS.
- A minimal dependency file so you can install evaluation packages separately from the main app.

## Dataset columns

The dataset file uses these fields:

- `id`: stable row identifier
- `question`: the benchmark prompt
- `reference_answer`: the expected grounded answer
- `contexts`: evidence snippets used by RAGAS
- `source_documents`: the document or documents used to build the row
- `topic`: policy area or control area
- `question_type`: factoid, numeric, supersession, or multi-hop style label
- `notes`: short human-readable explanation

## Install

From the repository root:

```bash
pip install -r evaluation/ragas/requirements.txt
```

## Run the evaluator

Start the normal backend first, then run:

```bash
python evaluation/ragas/run_evaluation.py --api-url http://127.0.0.1:8000 --session-id ragas-demo --employee-id 1001
```

The script will:

1. Load the gold dataset.
2. Call the chat API for each question.
3. Build a RAGAS-ready dataset with `question`, `answer`, `contexts`, and `ground_truth`.
4. Compute the selected metrics and write results into `evaluation/ragas/runs/<timestamp>/`.

## Why this is separate

Faculty can review this folder as a self-contained assessment lane. The core RAG system stays unchanged, while this pipeline provides a repeatable way to explain how the output is assessed.
