#!/bin/bash
set -e

echo "Starting Daily Pipeline..."

# 1. Collection: X (Twitter) Data
echo "[Collection] Collecting X (Twitter) data..."
if [ -z "$X_BEARER_TOKEN" ]; then
    echo "Warning: X_BEARER_TOKEN is not set. This step might fail."
fi
uv run collect-x --all-current-mks --push-bigquery

# 2. Collection: Knesset Data (Bills, Votes, etc.)
echo "[Collection] Collecting Knesset Data (Bills, Votes, etc.)..."
uv run collect-knesset

# 3. Processing: Evaluate Bills for Policy Issues
echo "[Processing] Evaluating Bills for Issues..."
if [ -z "$GEMINI_API_KEY" ]; then
    echo "Warning: GEMINI_API_KEY is not set. This step might fail if Vertex AI fallback is not configured or fails."
fi
uv run process-bill-issues

# 4. Processing: Embeddings & Clustering Pipeline
echo "[Processing] Running Embeddings and Clustering Pipeline..."
uv run process-embeddings

# 5. Processing: Issue Similarity Scoring
echo "[Processing] Running Multilingual Issue Scoring..."
uv run process-issue-scoring

# 6. Processing: Stance Summaries
echo "[Processing] Generating Stance Summaries..."
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?GOOGLE_CLOUD_PROJECT must be set}"

uv run process-summaries run-db \
  --project "$PROJECT_ID" \
  --dataset "mk_tracking" \
  --top-posts-per-issue 5 \
  --output-dir src/mk_tracking/process_summaries/output/full-run \
  --checkpoints src/mk_tracking/process_summaries/checkpoints

echo "Daily Pipeline Completed Successfully!"
