#!/bin/bash
set -e

# The serving layer is PostgreSQL. Steps that still target BigQuery have not
# been ported yet and are OFF by default — running them would write to a
# warehouse this project no longer uses. Set MK_TRACKING_ALLOW_BIGQUERY=1 to
# run them anyway while the port is in progress.
#
# The database comes from DATABASE_URL (e.g.
# postgresql://mk:mk@127.0.0.1:5432/mk_tracking) or from the discrete PG*
# variables — see src/mk_tracking/db_config.py. The steps below resolve it
# themselves and fail with instructions if neither is set, so it is never passed
# on a command line, where `ps` would expose the password.

ALLOW_BQ="${MK_TRACKING_ALLOW_BIGQUERY:-0}"

skip_unported() {
    echo "SKIPPED: $1"
    echo "         TODO: still writes to BigQuery; port to PostgreSQL."
    echo "         Set MK_TRACKING_ALLOW_BIGQUERY=1 to run it regardless."
}

echo "Starting Daily Pipeline..."

# 1. Collect new tweets for all MKs
echo "Step 1: Collecting X (Twitter) data..."
if [ "$ALLOW_BQ" = "1" ]; then
    cd x_api_collect
    if [ -z "$X_BEARER_TOKEN" ]; then
        echo "Warning: X_BEARER_TOKEN is not set. This step might fail."
    fi
    uv run x-api-collect --all-current-mks --push-bigquery
    cd ..
else
    skip_unported "x-api-collect (--push-bigquery has no PostgreSQL counterpart)"
fi

# 2. Collect Knesset data — ported: writes to PostgreSQL.
echo "Step 2: Collecting Knesset Data (Bills, Votes, etc.)..."
uv run python -m mk_tracking.download_knesset_data.upload_over_to_postgres

# 3. Evaluate Bills for Issues
echo "Step 3: Evaluating Bills for Issues..."
if [ "$ALLOW_BQ" = "1" ]; then
    cd bill_issues
    if [ -z "$GEMINI_API_KEY" ]; then
        echo "Warning: GEMINI_API_KEY is not set. This step might fail if Vertex AI fallback is not configured or fails."
    fi
    uv run bill-issues
    cd ..
else
    skip_unported "bill-issues (INSERTs into the BigQuery mk_tracking.bill_issue table)"
fi

# 4. Embeddings & clustering
echo "Step 4: Running Embeddings and Clustering Pipeline..."
if [ "$ALLOW_BQ" = "1" ]; then
    uv run python big_query_to_data.py
else
    skip_unported "big_query_to_data.py (reads and writes BigQuery)"
fi

# 5. Summaries
echo "Step 5: Generating Summaries..."
if [ "$ALLOW_BQ" = "1" ]; then
    PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?GOOGLE_CLOUD_PROJECT must be set}"
    uv run mk-summary run-db \
      --project "$PROJECT_ID" \
      --dataset "mk_tracking" \
      --top-posts-per-issue 5 \
      --output-dir summary_creation/output/full-run \
      --checkpoints summary_creation/checkpoints
else
    skip_unported "mk-summary run-db (BigQuerySource + BigQuerySink)"
fi

echo "Daily Pipeline Completed."
