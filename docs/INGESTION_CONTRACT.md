# Ingestion Contract — writing pipeline output into the DB (v4)

**Audience:** the sentiment/analysis pipeline team and any agent implementing ingestion.
**Deployed target:** BigQuery, dataset `mk_tracking` (see `docs/DATABASE_DESIGN.md` §9–§10; DDL in `db/schema.bq.sql`).
**Status:** authoritative definition of the pipeline↔DB contact point. If schema and doc disagree, the repo's `.bq.sql` files win — then fix this doc.
**v4 change (2026-07-30):** summaries carry evidence quality, limitations, and explicit supporting-post links. The issue taxonomy and matching prompts are aligned with `mk_work`.
Existing v3 BigQuery datasets must be rebuilt before using this contract.

---

## 1. The mental model

The pipeline produces **five kinds of records**:

```
 (1) social_post      "MK X posted this text on platform Y at time T"   — raw evidence
        │
        ▼  the pipeline tags each post
 (2) issue_anchor     "example wording for issue Z"                     — 8 curated rows PER issue
                        • 4 Hebrew, 2 English, 2 Arabic
                        • sentence + 3,072-dimensional embedding

 (3) tweet_cluster    "reviewed semantic cluster metadata"              — 30 rows PER model version
                        • centroid + Gemini title/description
                        • manually reviewed is_garbage flag

 (4) post_issue       "how similar that post is to issue Z"             — one row PER post × issue
                        • confidence = softmax over all seven issues
                        • each issue logit = max cosine over its 8 anchors
                        • all zero for text shorter than 16 characters
                          after URL removal, or for a garbage cluster
                        • is_concrete_promise is FALSE in this pipeline

 ...and periodically, per (MK, issue), from all that MK's posts on that issue:

 (5) mk_issue_summary "the SLM's opinion summary for MK X on issue Z"   — exactly ONE row per (MK, issue)
                        • summary_he — evidence-grounded free text
                        • quality — strong / partial / none
                        • limitations — what the evidence cannot establish
                        • supporting_posts — exact source posts
```

(1)–(4) are **append-shaped**. (5) is a **rollup with overwrite semantics**:
re-running the SLM for an (MK, issue) pair replaces the previous row.

There is no categorical stance/valence. A summary may carry an evidence-grounded
integer `rating` from 1 to 5 using the issue's own `rating_scale`; `none` always
uses a NULL rating.

Parliamentary data (`bill`, `vote_event`, `vote_event_issue`, `mk_vote`) is loaded by the DB/infra side, *not* the sentiment pipeline — see §5.

## 2. The three rules that prevent 90% of the mess

**Rule 1 — Never invent or hardcode row ids. Resolve by natural key.**
Row `id`s are `GENERATE_UUID()` strings; they change on every environment rebuild. The stable identifiers:

| Entity | Natural key | Where it comes from |
|---|---|---|
| `mk` | `knesset_member_id` (or `slug`) | Knesset OData / Open Knesset |
| `issue` | `slug` (e.g. `'housing'`) | `db/seed.bq.sql` — the closed, curated taxonomy |
| `social_post` | `(platform, platform_post_id)` | the platform's own post id |
| `tweet_cluster` | `(model_version, cluster_id)` | reviewed K-means artifact |
| `post_issue` | `(post_id, issue_id)` | resolved via the two above |
| `mk_issue_summary` | `(mk_id, issue_id)` | — |
| `vote_event` | `external_key` (e.g. `'odata:vote:12345'`) | source system |
| `mk_vote` | `(mk_id, vote_event_id)` | — |

⚠️ **Do not put a handle or slug in `mk_id`** (e.g. `mk_id='yairlapid'`). `mk_id` is the UUID from the `mk` table — resolve it by joining on `knesset_member_id`, as the MERGE templates below do. (This mistake has already happened once in testing.)

**Rule 2 — Batch-load + MERGE. Never plain INSERT, never streaming inserts.**
BigQuery enforces no unique constraints; INSERT in a re-runnable job = duplicates. Streaming inserts (`insertAll` / `insert_rows_json`) are worse: retries silently double-write, and rows in the streaming buffer **cannot be deleted or updated for up to ~90 minutes** (we hit both problems in testing). Follow §3: JSONL → `bq load` into staging → MERGE.

**Rule 3 — You are the validator. The DB accepts garbage silently.**
Validate before writing:
- `platform` ∈ `twitter|facebook|instagram|telegram|tiktok|gov_il|other`
- `language` ∈ `he|en|ar|other`
- `confidence` ∈ [0, 1]
- post `embedding` is either omitted/empty or contains exactly 3,072 floats
- every issue has exactly eight `issue_anchor` rows: 4 `he`, 2 `en`, 2 `ar`;
  each anchor embedding contains exactly 3,072 floats
- every reviewed cluster model contains exactly 30 centroids with 3,072
  dimensions and a manually reviewed `is_garbage` flag
- issue `slug` exists in the taxonomy — **fail loudly on unknown slugs**; the taxonomy changes only via `db/seed.bq.sql` + team agreement
- stamp `model_version` on every tag and summary — the only provenance breadcrumb
- `quality` ∈ `strong|partial|none`
- `strong` or `partial` requires at least one supporting post; `none` requires none
- every supporting post resolves and belongs to the summarized MK

## 3. The flow: stage as JSONL, then MERGE

### 3.1 JSONL shapes the pipeline emits

`posts.jsonl` — one line per post, **issue tags inline**:
```json
{"knesset_member_id": 1034, "platform": "twitter", "platform_post_id": "1801234567890", "url": "https://x.com/...", "posted_at": "2026-07-12T18:03:00Z", "text": "אם אהיה ראש ממשלה אבטל את המע\"מ על מוצרי יסוד", "language": "he", "engagement": {"likes": 320, "retweets": 41}, "model_version": "gemini-embedding-2:max8:softmax-t0.07:short16:gemini-embedding-2:kmeans-k30-rs42-n1:v1:v2", "issues": [{"slug": "cost-of-living", "confidence": 0.95, "is_concrete_promise": false}]}
```

`embedding` is omitted above for readability; when supplied it is a JSON array of exactly 3,072 numbers.

`summaries.jsonl` — one line per (MK, issue) SLM summary:
```json
{"knesset_member_id": 1034, "issue_slug": "economy-welfare", "summary_he": "תומך בהרחבת שירותים ציבוריים; ...", "quality": "strong", "rating": 2, "limitations": "הפוסטים אינם מפרטים מקור תקציבי.", "supporting_posts": [{"platform": "twitter", "platform_post_id": "1801234567890"}], "model_version": "slm-v3.0"}
```

The pipeline references MKs by `knesset_member_id`, posts by `(platform, platform_post_id)`, issues by slug — never by UUID.

### 3.2 Load + MERGE

```bash
# 1. load staging with the explicit schemas from the repo.
#    (NOT --autodetect: it creates columns only for keys present in the batch,
#    so a batch omitting an optional field breaks the MERGE. Explicit schemas
#    load missing keys as NULL.)
bq load --replace --source_format=NEWLINE_DELIMITED_JSON \
  --schema=db/staging/stg_posts.schema.json     mk_tracking.stg_posts     posts.jsonl
bq load --replace --source_format=NEWLINE_DELIMITED_JSON \
  --schema=db/staging/stg_summaries.schema.json mk_tracking.stg_summaries summaries.jsonl

# 2. merge into serving tables (posts -> post_issue -> summaries -> evidence)
bq query --use_legacy_sql=false < db/merge_ingest.bq.sql
```

The staging schemas (`db/staging/*.schema.json`) are the **machine-readable half of this contract** — if the pipeline emits a new field, add it there first.

> ✅ Smoke-tested end-to-end on the live dataset (post with 2 issue tags → summary → correct render in `v_mk_issue_summary` and `v_said_vs_did`; re-running produced zero duplicates).

For the X-specific acquisition procedure—per-MK forward/backfill boundaries,
exact-count pagination, account selection, and post-run verification—see
[`x_api_collect/README.md`](../x_api_collect/README.md#acquiring-fresh-posts-without-duplicates).

The MERGE keys, as implemented in `db/merge_ingest.bq.sql`:
1. `social_post` — `ON (platform, platform_post_id)`, MK resolved by joining `mk.knesset_member_id`.
2. `post_issue` — `ON (post_id, issue_id)`, unnested from the inline `issues[]`, post resolved by `(platform, platform_post_id)`, issue by `slug`.
3. `mk_issue_summary` — `ON (mk_id, issue_id)`, overwrite semantics (`summary_he`, `quality`, `limitations`, `model_version`, `updated_at` refreshed).
4. `mk_issue_summary_supporting_post` — `ON (summary_id, post_id)`, resolving posts by `(platform, platform_post_id)`.

A staged row whose `knesset_member_id` or issue `slug` doesn't resolve is **silently dropped by the JOIN** — count staged vs merged rows and alert on mismatch.

## 4. Ordering & prerequisites

1. **`mk` rows must exist first** — everything resolves through `knesset_member_id`. Loading the ~120 current MKs is a DB/infra-side prerequisite (not the sentiment team's job).
2. `issue` rows come only from `db/seed.bq.sql`. The taxonomy is being curated toward concrete, verifiable areas (economic etc.) — expect edits to the seed, not to the schema.
3. Curated `issue_anchor` rows must exist before similarity scoring. Normal
   pipeline runs validate and read them but do not regenerate them.
4. Within a batch: posts → post_issue → summaries → supporting posts (already
   ordered inside `merge_ingest.bq.sql`).
5. Summaries can be recomputed and re-merged anytime, independent of new posts.

## 5. What the pipeline does NOT write

- `mk`, `party`, `mk_affiliation`, `mk_role`, `mk_social_account`, `committee` — identity data (DB/infra side).
- `bill`, `vote_event`, `bill_issue`, `vote_event_issue`, `mk_vote`, `bill_author` — parliamentary activity (DB/infra side). The bill loader may write `bill.embedding`, but must validate exactly 3,072 floats. `bill_author` (natural key `(bill_id, mk_id)`, `role` ∈ `initiator|co_initiator`) loads from Knesset OData `KNS_BillInitiator`. Note for whoever builds that loader: issue mapping lives at **two levels** — `bill_issue` (covers all of a bill's vote events) and `vote_event_issue` (committee motions / event-specific subtleties); both carry `mapping_method`/`mapping_note`/`confidence` provenance. `v_vote_event_issues` unions them.
- The `v_*` views — read models, never written.

## 6. Failure modes to guard against

| Symptom | Cause | Guard |
|---|---|---|
| Duplicate posts in UI | INSERT instead of MERGE; or streaming inserts with retries | batch load + MERGE only (§2 Rule 2) |
| Rows that can't be deleted/updated | rows sitting in the streaming buffer (~90 min) | don't stream; use `bq load` |
| Posts/tags silently missing | `knesset_member_id` or issue slug failed to resolve (dropped by JOIN) | compare staged vs merged counts; log unresolved keys |
| Joins return nothing for an MK | `mk_id` written as a handle/slug instead of the resolved UUID | Rule 1; always resolve via the `mk` table |
| Issue block empty despite posts | summary step (3) never ran for that (MK, issue) | after post merges, recompute summaries for every (MK, issue) touched |
| Garbage platform/language values | no DB validation | validate against §2 Rule 3 lists before load |
| Everything gone | Environment recreated / dataset deleted | expected; rebuild schema+seed from repo; **keep the JSONL batch files** (locally or GCS) — they're the re-playable log and effectively the backup |
