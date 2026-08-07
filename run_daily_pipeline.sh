#!/bin/bash
set -e

echo "Starting Daily Pipeline..."

# 1. Collect new tweets for all MKs
echo "Step 1: Collecting X (Twitter) data..."
cd x_api_collect
if [ -z "$X_BEARER_TOKEN" ]; then
    echo "Warning: X_BEARER_TOKEN is not set. This step might fail."
fi
uv run x-api-collect --all-current-mks --push-bigquery
cd ..

# 2. Collect new bills
echo "Step 2: Collecting Knesset Data (Bills, Votes, etc.)..."
uv run python download_knesset_data/upload_over_to_bigquery.py

# 3. Evaluate Bills for Issues
echo "Step 3: Evaluating Bills for Issues..."
cd bill_issues
if [ -z "$GEMINI_API_KEY" ]; then
    echo "Warning: GEMINI_API_KEY is not set. This step might fail if Vertex AI fallback is not configured or fails."
fi
uv run bill-issues
cd ..

# 4. Use the pipeline.py thing (Embeddings & Clustering)
echo "Step 4: Running Embeddings and Clustering Pipeline..."
uv run python big_query_to_data.py

# 5. Perform the summary thing
echo "Step 5: Generating Summaries..."
# Extract the current project ID from the environment
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?GOOGLE_CLOUD_PROJECT must be set}"

uv run mk-summary run-db \
  --project "$PROJECT_ID" \
  --dataset "mk_tracking" \
  --top-posts-per-issue 5 \
  --output-dir summary_creation/output/full-run \
  --checkpoints summary_creation/checkpoints

echo "Daily Pipeline Completed Successfully!"
