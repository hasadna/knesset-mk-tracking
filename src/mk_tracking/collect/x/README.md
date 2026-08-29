# x-api-collect

Python module managed with `uv` for fetching X (Twitter) account data cleanly and pushing to BigQuery.

## Setup

Set environment variables for X API and Google Cloud:

```bash
# PowerShell
$env:X_BEARER_TOKEN="your_x_bearer_token"
$env:GOOGLE_APPLICATION_CREDENTIALS="path/to/gcp_credentials.json"

# Bash / Zsh
export X_BEARER_TOKEN="your_x_bearer_token"
export GOOGLE_APPLICATION_CREDENTIALS="path/to/gcp_credentials.json"
```

## Usage

### 1. Fetch Latest Tweets for an Account
```bash
uv run x-api-collect --account knesset_il --count 100
```

### 2. Fetch Tweets in a Date Range (`--since` / `--until`)
```bash
uv run x-api-collect --account knesset_il --since 2026-07-01 --until 2026-07-30
```

### 3. Fetch Tweets & Export to BigQuery (`--push-bigquery`)
```bash
uv run x-api-collect --account knesset_il --since 2026-07-01 --push-bigquery
```

Specify a custom BigQuery table if needed:
```bash
uv run x-api-collect --account knesset_il --push-bigquery --bq-table "project_id.dataset_id.table_id"
```

## Acquiring fresh posts without duplicates

The stable identity of an X post is:

```text
(platform = "twitter", platform_post_id)
```

Do not use the generated `social_post.id`, the MK handle, or `posted_at` as the
deduplication key. BigQuery does not enforce uniqueness, so every production
collection must batch-load into a staging table and `MERGE` into
`social_post`. Plain append loads can create duplicates.

> **Current CLI limitation:** `--push-bigquery` uses `WRITE_APPEND`. It is
> useful for local/manual testing, but it is not the production-safe
> non-duplicate ingestion path described below.

### Select the accounts

Only collect an account when it has a resolved `mk.id` and
`mk_social_account.id`. Never put a handle in either foreign-key column.

Examples of useful target sets:

- New current MKs: `mk.is_current`, active Twitter account, and no Twitter
  rows in `social_post`.
- New historical people: non-current record, active Twitter account, and no
  Twitter rows.
- Ongoing refresh: any in-scope person with an active Twitter account.

### Choose a per-account time boundary

Use a separate boundary for every MK. A single global date creates uneven
coverage.

```sql
SELECT
  mk_id,
  MIN(posted_at) AS earliest_posted_at,
  MAX(posted_at) AS latest_posted_at
FROM `$GOOGLE_CLOUD_PROJECT.mk_tracking.social_post`
WHERE platform = 'twitter'
GROUP BY mk_id;
```

For a historical backfill, request posts with:

```text
end_time = that MK's earliest_posted_at
```

X treats `end_time` as exclusive, so the next batch is older than everything
already stored for that MK.

For a forward refresh, request posts with:

```text
start_time = that MK's latest_posted_at
```

Use a small overlap at the boundary if desired. The natural-key `MERGE` makes
the overlap safe.

For an account with no posts, omit both boundaries to retrieve its newest
posts.

### Fetch an exact, balanced amount

`max_results` is the X page size, not a guarantee that a response contains
that many posts. X may return fewer rows while still having another page.

To acquire `N` new posts per MK:

1. Request one page at that MK's boundary.
2. Remove IDs already present in `social_post`.
3. Keep unseen rows and follow `meta.next_token` if fewer than `N` remain.
4. Stop at `N`, or when X has no next page.
5. Record accounts that cannot resolve, return no posts, or exhaust their
   timeline early.

When filling a short first response, recalculate the MK's earliest timestamp
after the successful merge and continue from that new exclusive boundary.
Verify the final count against an absolute target instead of relying on a
recent-time window. For example, if an MK had 10 posts and the job adds 20,
verify that the MK has 30 distinct post IDs.

### Stage and merge

Batch-load the fetched rows into a temporary table created with the
`social_post` schema. Preserve that schema during the load: autodetection can
infer numeric-looking X IDs as `INT64`, while `platform_post_id` is `STRING`.

The serving-table merge key is:

```sql
MERGE `$GOOGLE_CLOUD_PROJECT.mk_tracking.social_post` AS target
USING `$GOOGLE_CLOUD_PROJECT.mk_tracking.stg_x_posts` AS source
ON target.platform = source.platform
AND target.platform_post_id = source.platform_post_id

WHEN MATCHED THEN UPDATE SET
  url = source.url,
  posted_at = source.posted_at,
  text = source.text,
  language = source.language,
  engagement = source.engagement,
  is_deleted = FALSE,
  fetched_at = source.fetched_at

WHEN NOT MATCHED THEN INSERT (
  id, mk_id, account_id, platform, platform_post_id, url, posted_at,
  text, language, engagement, is_deleted, fetched_at, created_at
)
VALUES (
  source.id, source.mk_id, source.account_id, source.platform,
  source.platform_post_id, source.url, source.posted_at, source.text,
  source.language, source.engagement, source.is_deleted, source.fetched_at,
  source.created_at
);
```

Use a batch load job, not `insertAll` or `insert_rows_json`, and delete the
temporary staging table after the merge. The general ingestion requirements
also apply; see [`docs/INGESTION_CONTRACT.md`](../docs/INGESTION_CONTRACT.md).

### Verify every run

Capture and report:

- targeted accounts;
- posts returned by X;
- unique staged post IDs;
- inserted and updated row counts from the `MERGE`;
- unresolved handles, empty timelines, and accounts with fewer than `N`;
- total rows and distinct X post IDs after the run.

The merge should normally report zero updates for a historical backfill. An
update is safe and non-duplicating, but indicates that X returned an existing
post and may have charged for rereading it.

Existing historical duplicates can be measured separately:

```sql
SELECT COUNT(*) AS duplicated_post_keys
FROM (
  SELECT platform, platform_post_id
  FROM `$GOOGLE_CLOUD_PROJECT.mk_tracking.social_post`
  GROUP BY platform, platform_post_id
  HAVING COUNT(*) > 1
);
```
