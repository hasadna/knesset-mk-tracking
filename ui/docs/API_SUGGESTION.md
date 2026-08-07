# API suggestion: JSON now, BigQuery later

## Goal

Add a small server-side API without changing the current evidence or opinion
data. Initially, the API should read the existing JSON files:

- `data/analysis.json`
- `data/tweets.normalized.json`
- `assets/roster.json`
- `assets/topics.json`

The browser should stop downloading the two large files under `data/`
directly. Later, the API's data-access implementation can be replaced with
BigQuery queries while keeping the same HTTP responses and frontend code.

Recommended stack: **FastAPI + Uvicorn**, managed with `uv`.

```text
index.html -> /api/* -> FastAPI -> JSON repository (initially)
                                  -> BigQuery repository (later)
```

## Why this shape

- The frontend is static vanilla HTML/JavaScript, so there is no existing
  server framework to extend.
- The repository already uses Python and `uv`.
- Serving the frontend and API from the same process avoids CORS during local
  development.
- A repository interface separates HTTP response design from the temporary
  JSON storage format.
- Google credentials remain server-side when BigQuery is introduced.

## Initial endpoints

### `GET /api/health`

Return:

```json
{"status": "ok", "backend": "json"}
```

### `GET /api/mks`

Return the visual roster, enriched with the existing account statistics.
This replaces no opinion evidence and may still use party presentation data
from `assets/`.

Useful optional query parameters:

- `q`: name search
- `party`: party identifier

### `GET /api/issues`

Return the topic taxonomy from `assets/topics.json`. Topics contain labels and
filter metadata, not politician opinions.

### `GET /api/mks/{mk_key}/issues`

Return the analysis records for one politician, preferably in this shape:

```json
{
  "mkKey": "stable-roster-key",
  "topics": [
    {
      "id": "judiciary",
      "status": "strong",
      "stance": "Hebrew evidence-grounded summary",
      "sources": ["x:account:tweet_id"],
      "limit": "What cannot be concluded from this snapshot"
    }
  ]
}
```

`mk_key` should be a stable identifier from the roster, not the displayed
Hebrew name. If the current roster has no stable key, add one explicitly and
retain the Hebrew name as presentation data.

### `GET /api/mks/{mk_key}/posts`

Return only tweets belonging to the selected politician. Support:

- `issue`: optional topic ID; when present, return tweets referenced by that
  politician's analysis record for the topic
- `limit`: bounded, default 100
- `offset`: default 0

Example response:

```json
{
  "items": [
    {
      "key": "x:account:tweet_id",
      "publisher": "שם חבר הכנסת",
      "account": "account",
      "createdAt": "2026-01-01T12:00:00Z",
      "text": "...",
      "url": "https://x.com/...",
      "metrics": {}
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

Do not expose an endpoint that accepts arbitrary SQL or arbitrary filesystem
paths.

## Suggested code organization

```text
src/mk_work/
  __init__.py
  app.py                 # FastAPI construction and static-file mounting
  api.py                 # routes and request validation
  models.py              # response models
  repositories/
    base.py              # repository protocol/interface
    json_repository.py   # current implementation
    bigquery_repository.py  # later implementation
```

The API routes should depend only on the repository interface. Choose the
implementation through configuration, for example:

```text
MK_WORK_DATA_BACKEND=json       # default
MK_WORK_DATA_BACKEND=bigquery   # later
```

Do not put secrets, service-account keys, or `.env` files in the repository.

## Serving locally

Preserve the current commands:

```bash
uv run mkwork
uv run mkwork validate
```

`uv run mkwork` should start Uvicorn and serve:

- API routes under `/api`
- `index.html`, `assets/`, and other static frontend files

The JSON repository should load and validate its files at startup. It may keep
them in memory because this snapshot is small and immutable during a server
run.

## Frontend migration

Keep the existing UI and behavior. Change only its data boundary:

1. Replace the startup topic/roster requests with `/api/issues` and `/api/mks`,
   or leave the small `assets/` requests in place for the first iteration.
2. Replace the lazy `data/analysis.json` request with
   `/api/mks/{mk_key}/issues`.
3. Replace the lazy `data/tweets.normalized.json` request with
   `/api/mks/{mk_key}/posts`.
4. Preserve topic filtering, relevant-only mode, the profile drawer, source
   dialog, and tweet links.
5. Show a clear unavailable/error state if an API request fails.

Prefer the incremental option in step 1: keep small non-evidentiary assets
static initially, and move only analysis and tweets behind the API.

## Evidence rules that must remain enforced

The API refactor must not change the project's source-of-truth rules (see
`../README.md`):

- Opinion evidence comes only from collected X posts (served live via the
  API, or, in offline mode, from `data/tweets.normalized.json`).
- Never add, restore, or reference CSV evidence.
- Never infer opinions from party, coalition, biography, office, or outside
  reporting.
- Missing evidence is `none`, not neutral.
- `strong` and `partial` require at least one valid `x:` source.
- `none` requires an empty source list.
- Every returned source must resolve to a normalized tweet.
- Preserve each opinion's `limit` field.

Run the existing validation before starting the API and retain
`uv run mkwork validate` as a separate command.

## Later BigQuery adapter

The BigQuery dataset currently lives at:

```text
$GOOGLE_CLOUD_PROJECT.mk_tracking
```

Relevant deployed tables/views:

- `mk`
- `social_post`
- `post_issue`
- `issue`
- `mk_issue_summary`
- `v_mk_card`
- `v_mk_issue_summary`
- `v_issue_landscape`
- `v_said_vs_did`

Only `bigquery_repository.py` should know BigQuery SQL. Use parameterized
queries and a server-side Google identity. For Cloud Run, use an attached
service account with read-only BigQuery permissions; for local development,
use Application Default Credentials.

Do not switch to BigQuery until these compatibility gaps are resolved:

1. The frontend has 15 topic IDs while the current BigQuery seed contains 14
   differently grouped issue slugs.
2. The frontend's `strong | partial | none`, `stance`, `sources`, and `limit`
   evidence contract is not represented exactly by the current v3 database
   schema.
3. Stable mappings are needed between frontend politicians, X accounts, and
   `mk.knesset_member_id`.

Resolve these with explicit mapping/data-model decisions. Do not infer mappings
from party or display names.

## Acceptance criteria for the JSON-backed iteration

- The visual behavior remains unchanged on desktop and narrow mobile layouts.
- The browser no longer fetches the complete `data/analysis.json` or
  `data/tweets.normalized.json` files.
- Opening a profile fetches only that politician's analysis and tweets.
- Topic filtering, relevant-only mode, profile opening, and tweet links work.
- API inputs are validated and list responses are bounded.
- `uv run mkwork validate` passes and confirms exactly 850 tweets.
- Every non-`none` opinion source resolves to an X tweet.
- Searching the generated/served code and output finds neither `csv:` nor
  `"sourceType": "csv"`.
- No Google credentials or secrets are committed.

