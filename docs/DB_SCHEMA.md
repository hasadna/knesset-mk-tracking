# DB Schema Reference — `mk_tracking` (BigQuery, v4)

Column-level reference for the deployed dataset. Source of truth: [`db/schema.bq.sql`](../db/schema.bq.sql) — if this doc and the DDL disagree, the DDL wins; then fix this doc.

Companion docs: [`DATABASE_DESIGN.md`](DATABASE_DESIGN.md) (rationale, §10 = v3 spec), [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md) (how the pipeline writes).

**Deployed at:** project `$GOOGLE_CLOUD_PROJECT`, dataset `mk_tracking`, location US. ⚠️ Rebuild the dataset with `bq query --use_legacy_sql=false < db/schema.bq.sql` then seed from `data/seed/issues.tsv`.

**v3 → v4:** rebuild the ephemeral dataset before applying these files. `CREATE TABLE IF NOT EXISTS` cannot reshape existing BigQuery tables, and the v4 seed intentionally replaces the old taxonomy.

---

## Conventions (apply to every table)

- **`id`** — `STRING`, `DEFAULT GENERATE_UUID()`, the PK. **Never reference ids across environments**: they change on every rebuild. Join/merge by the natural key listed per table.
- **PK/FK are `NOT ENFORCED`** — BigQuery never enforces them; they document intent and help the optimizer. Uniqueness is the pipeline's job (MERGE on natural keys).
- **Enum-like columns** are plain `STRING`s — allowed values are listed here and in column descriptions; **the writer validates** (the DB accepts anything).
- **"Current"** for time-bounded rows means `end_date IS NULL`.
- All timestamps are `TIMESTAMP` (UTC).

### Entity overview

```
party ◀── mk_affiliation ──▶ mk ◀── mk_role ──▶ committee
                              ▲◀── mk_social_account
                              │◀── mk_relation ──▶ mk
              ┌───────────────┼──────────────────────┐
              ▼               ▼                      ▼
        social_post ──▶ post_issue ──▶ issue ◀── mk_issue_summary
              │
              └──────▶ tweet_cluster (semantic gating model)
                                       │ ▲
                                       ▼ │
                                  issue_anchor
                                         ▲
                     bill ──▶ bill_issue ─┤
                       ▲                  │
        vote_event ────┴─▶ vote_event_issue
             ▲
          mk_vote ◀── mk
```

---

## Identity & structure

### `mk` — Members of Knesset
Natural key: **`knesset_member_id`** (external, from Knesset OData / Open Knesset). One row per MK.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `knesset_member_id` | INT64 | yes | **natural key**; external hook to OData, votes, Open Knesset |
| `slug` | STRING | no | URL key, e.g. `yair-lapid` |
| `full_name_he` | STRING | no | |
| `full_name_en` | STRING | yes | |
| `photo_url` | STRING | yes | |
| `birth_date` | DATE | yes | |
| `gender` | STRING | yes | |
| `home_city` | STRING | yes | |
| `bio_he` / `bio_en` | STRING | yes | |
| `is_current` | BOOL | no | default TRUE — sitting in the current Knesset; authoritative roster snapshot: `data/seed/current_mk_roster_2026-07-31.tsv` |
| `created_at` / `updated_at` | TIMESTAMP | no | default now |

### `party`
| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `name_he` | STRING | no | |
| `name_en` / `short_name` | STRING | yes | |
| `is_current` | BOOL | no | default TRUE |
| `created_at` / `updated_at` | TIMESTAMP | no | |

### `committee`
Natural key: **`knesset_committee_id`**.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `knesset_committee_id` | INT64 | yes | **natural key** (external) |
| `name_he` | STRING | no | |
| `name_en` | STRING | yes | |

### `issue` — the curated policy taxonomy
Natural key: **`slug`**. Seeded only from [`data/seed/issues.tsv`](../data/seed/issues.tsv); the closed list everything is tagged against. Curation direction: concrete, verifiable areas (economic etc.). **Never invent slugs in the pipeline.**

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `slug` | STRING | no | **natural key**, stable machine key, e.g. `cost-of-living` |
| `name` | STRING | no | Hebrew display name |
| `description` | STRING | yes | Scope and examples for this issue |
| `prompt_for_social_post_similarity` | STRING | no | Matching prompt for social posts |
| `prompt_for_bill_similarity` | STRING | no | Matching prompt for bills |
| `rating_scale` | JSON | no | Hebrew meanings for ratings `1`–`5`; first axis pole is 1 and second is 5 |
| `sort_order` | INT64 | no | default 0, controls display order |

### `issue_anchor` — multilingual semantic anchors
Natural key: **`(issue_id, anchor_index)`**. Each of the seven issues has exactly
eight hand-curated examples: four Hebrew, two English, and two Arabic. These
sentences and their embeddings are created once; normal pipeline runs only read
them.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `issue_id` | STRING | no | FK → `issue.id` |
| `anchor_index` | INT64 | no | 1–8 within the issue |
| `language` | STRING | no | `he`, `en`, or `ar` |
| `text` | STRING | no | Curated representative sentence |
| `embedding` | ARRAY<FLOAT64> | yes | Exactly 3,072 values when populated |
| `embedding_model` | STRING | no | Model used to create the stored vector |
| `created_at` | TIMESTAMP | no | default now |

### `mk_affiliation` — party membership over time
FK: `mk_id` → mk, `party_id` → party. Current membership = `end_date IS NULL`. Seeded with current rows; history back-fills later without schema change.

The current roster and its 120 open affiliations are applied by
`db/apply_current_mk_roster.bq.sql`. Generic person ingestion may
enrich MK identity fields, but must not derive `mk.is_current` from
`kns_person.iscurrent`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` |
| `party_id` | STRING | no | FK → `party.id` |
| `start_date` / `end_date` | DATE | yes | `end_date NULL` == current |

### `mk_role` — offices & parliamentary roles over time
| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` |
| `role_type` | STRING | no | one of: `prime_minister` `minister` `deputy_minister` `knesset_speaker` `deputy_speaker` `committee_chair` `committee_member` `faction_chair` `coalition` `opposition` `other` |
| `title_he` / `title_en` | STRING | yes | free text, e.g. `שר האוצר` |
| `committee_id` | STRING | yes | FK → `committee.id`, when committee-scoped |
| `start_date` / `end_date` | DATE | yes | `end_date NULL` == current |

### `mk_social_account` — handle registry
Natural key: **`(mk_id, platform, handle)`**. Seeded from the MK account list independent of collected posts; drives the "platforms used" filter and tells the pipeline where to look.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` |
| `platform` | STRING | no | one of: `twitter` `facebook` `instagram` `telegram` `tiktok` `gov_il` `other` |
| `handle` | STRING | yes | e.g. `yairlapid` |
| `url` | STRING | yes | |
| `is_active` | BOOL | no | default TRUE; FALSE = account closed/suspended |
| `verified` | BOOL | no | default FALSE; TRUE = human confirmed it's really the MK |

### `mk_relation` — connections between MKs
Natural key: `(mk_id, related_mk_id, relation_type, issue_id)`. Powers "notable other MKs" and similarity grouping.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` |
| `related_mk_id` | STRING | no | FK → `mk.id`; must differ from `mk_id` (pipeline-enforced) |
| `relation_type` | STRING | no | one of: `same_party` `similar_positions` `notable` `ally` `rival` |
| `issue_id` | STRING | yes | FK → `issue.id`, set when relation is issue-specific |
| `score` | NUMERIC | yes | similarity/strength |

---

## Social content (pipeline-written — see INGESTION_CONTRACT.md)

### `social_post` — raw posts (the evidence)
Natural key: **`(platform, platform_post_id)`**. Partitioned by `DATE(posted_at)`, clustered by `(mk_id, platform)` — always filter by `mk_id` and/or date range to keep scans cheap.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` — **the resolved UUID, never a handle** |
| `account_id` | STRING | yes | FK → `mk_social_account.id` |
| `platform` | STRING | no | same values as `mk_social_account.platform` |
| `platform_post_id` | STRING | yes | **natural key** with `platform` — the platform's native id |
| `url` | STRING | yes | |
| `posted_at` | TIMESTAMP | yes | partition column |
| `text` | STRING | yes | |
| `embedding` | ARRAY<FLOAT64> | yes | empty or exactly 3,072 dimensions; pipeline-validated |
| `language` | STRING | yes | one of: `he` `en` `ar` `other` |
| `engagement` | JSON | yes | `{likes, shares, comments, views, ...}` — platform-flexible |
| `is_deleted` | BOOL | no | default FALSE — deleted-post tracking |
| `fetched_at` | TIMESTAMP | yes | last scrape/refresh time |
| `created_at` | TIMESTAMP | no | |

### `tweet_cluster` — automatically classified semantic gating clusters

Natural key: **`(model_version, cluster_id)`**. The production issue scorer
assigns each normalized tweet embedding to its nearest raw K-means centroid.
Tweets in a Gemini-classified garbage cluster receive zero confidence for every
issue.

| Column | Type | Null | Notes |
|---|---|---|---|
| `model_version` | STRING | no | versioned cluster/classifier run identifier |
| `cluster_id` | INT64 | no | zero-based ID within the model |
| `centroid` | ARRAY<FLOAT64> | yes | exactly 3,072 values; pipeline-required |
| `title_he` | STRING | no | Gemini-generated short title |
| `description_he` | STRING | no | Gemini-generated cluster description |
| `cluster_size` | INT64 | no | assigned tweets when the model was fitted |
| `is_garbage` | BOOL | no | Gemini-generated negative-scoring gate |
| `embedding_model` / `summary_model` | STRING | no | model provenance |
| `k` / `random_state` / `n_init` | INT64 | no | clustering parameters |
| `updated_at` | TIMESTAMP | no | refreshed by MERGE |

### `post_issue` — issue tags per post
Natural key: **`(post_id, issue_id)`**. A post can touch several issues. Clustered by `issue_id`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `post_id` | STRING | no | FK → `social_post.id` |
| `issue_id` | STRING | no | FK → `issue.id` |
| `confidence` | NUMERIC | yes | 0–1, tagging confidence (pipeline-validated) |
| `is_concrete_promise` | BOOL | no | default FALSE — post contains a concrete policy promise w.r.t. this issue. *Retained from v2 spec, pending team confirmation.* |
| `model_version` | STRING | yes | provenance — which tagger produced this |

### `mk_issue_summary` — the SLM's per-(MK, issue) opinion summary
Natural key: **`(mk_id, issue_id)`** — exactly one row; **overwrite semantics**
(re-summarizing replaces the row). The evidence-grounded Hebrew text is paired
with a `1`–`5` rating defined by the issue-specific scale; flip-flop
observations remain in the text. Clustered by `mk_id`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` |
| `issue_id` | STRING | no | FK → `issue.id` |
| `summary_he` | STRING | yes | Hebrew evidence-grounded summary |
| `quality` | STRING | no | one of: `strong` `partial` `none` |
| `rating` | INT64 | yes | `1`–`5` using `issue.rating_scale`; NULL when quality is `none` |
| `limitations` | STRING | yes | what cannot be concluded from the available posts |
| `model_version` | STRING | yes | provenance |
| `updated_at` | TIMESTAMP | no | refreshed on every overwrite |

### `mk_issue_summary_supporting_post` — summary evidence
Natural key: **`(summary_id, post_id)`**. Many-to-many link to the exact posts supporting a summary. `strong` and `partial` require at least one supporting post; `none` requires none. The pipeline validates those rules.

| Column | Type | Null | Notes |
|---|---|---|---|
| `summary_id` | STRING | no | FK → `mk_issue_summary.id` |
| `post_id` | STRING | no | FK → `social_post.id` |

---

## Parliamentary activity (infra-side loaders — not the sentiment pipeline)

### `bill`
Natural key: **`knesset_bill_id`**.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `knesset_bill_id` | INT64 | yes | **natural key** (external) |
| `title_he` | STRING | no | |
| `title_en` / `summary` / `status` | STRING | yes | |
| `embedding` | ARRAY<FLOAT64> | yes | empty or exactly 3,072 dimensions; parliamentary loader validates the length |
| `enacted_law_document_uri` | STRING | yes | URI for the enacted-law document, when the bill became law |
| `created_at` | TIMESTAMP | no | |

### `vote_event` — anything MKs vote on
Natural key: **`external_key`** — a stable string from the source, e.g. `odata:vote:12345` or `committee:450:2026-03-02:item4`. Covers plenum readings **and** committee motions (v3 generalization).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `external_key` | STRING | no | **natural key** for MERGE |
| `event_kind` | STRING | no | one of: `plenum` `committee` |
| `bill_id` | STRING | yes | FK → `bill.id` — set for plenum readings (and committee votes on bills) |
| `committee_id` | STRING | yes | FK → `committee.id` — set for committee motions |
| `reading` | INT64 | yes | 1/2/3 for plenum bill readings |
| `title_he` | STRING | no | |
| `title_en` | STRING | yes | |
| `draft_document_uri` | STRING | yes | URI for the draft/text presented for this vote event |
| `voted_at` | TIMESTAMP | yes | |

### `bill_issue` / `vote_event_issue` — the two-level issue mapping
Both are M:N junctions (support 1:N, N:1, M:N). **Effective mapping for an event = union of both levels** (materialized in `v_vote_event_issues`):
- `bill_issue` — bill-level; one mapping covers all of that bill's vote events.
- `vote_event_issue` — event-level; committee motions, and events whose subject diverges from the parent bill.

Shared columns (both tables):

| Column | Type | Null | Notes |
|---|---|---|---|
| `bill_id` \| `vote_event_id` | STRING | no | FK to the mapped entity |
| `issue_id` | STRING | no | FK → `issue.id` |
| `mapping_method` | STRING | yes | one of: `manual` `model` `rule` |
| `mapping_note` | STRING | yes | the non-obvious rationale — record subtleties not apparent from the title |
| `confidence` | NUMERIC | yes | 0–1 |

### `bill_author` — bill initiators/co-sponsors
Natural key: **`(bill_id, mk_id)`**. Authorship is independent of votes — a bill has authors even if never voted on. Maps 1:1 to Knesset OData `KNS_BillInitiator`. Deliberately NOT on `vote_event`/`mk_vote`: authorship is an MK↔bill relation (many co-initiators per bill), not a vote property.

| Column | Type | Null | Notes |
|---|---|---|---|
| `bill_id` | STRING | no | FK → `bill.id` |
| `mk_id` | STRING | no | FK → `mk.id` |
| `role` | STRING | yes | one of: `initiator` `co_initiator` |

### `mk_vote`
Natural key: **`(mk_id, vote_event_id)`**. Clustered by `mk_id`. A bill's separate readings are separate vote events.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | STRING | no | UUID PK |
| `mk_id` | STRING | no | FK → `mk.id` |
| `vote_event_id` | STRING | no | FK → `vote_event.id` |
| `vote` | STRING | no | one of: `for` `against` `abstain` `absent` |

---

## Views (read models — never written)

### `v_mk_card` — main-page MK grid
One row per MK: `id`, `slug`, `full_name_he/_en`, `photo_url`, `is_current`, `party_he/_en` (current affiliation), `platforms` (ARRAY, from the account registry — works before any posts exist), `current_roles` (ARRAY), `issue_count` (summaries present).

### `v_mk_issue_summary` — profile issue blocks
One row per (MK, issue) with a summary: issue identifiers (`issue_slug`, `issue_name`, `issue_description`), `summary_he`, `quality`, `limitations`, `supporting_post_ids`, `model_version`, `updated_at`, plus `post_count` and `promise_count`.

### `v_issue_landscape` — the "matrix transpose"
For one issue, every MK's row: `issue_slug`, `issue_name`, `mk_id`, `full_name_he`, `mk_slug`, `summary_he`, `quality`, `limitations`, `updated_at`, `post_count`.

### `v_vote_event_issues` — effective event→issue mapping
`(vote_event_id, issue_id)` — the union of `vote_event_issue` and the parent bill's `bill_issue` rows. Use this, not the raw junctions, when joining votes to issues.

### `v_mk_issue_authorship` — legislative initiative per (MK, issue)
Per `(mk_id, issue_id)`: `bills_initiated`, `bills_co_initiated`, `bills_authored_total`, rolled up via `bill_issue`. A strong "did" signal alongside the vote tallies.

### `v_said_vs_did` — rhetoric vs record
Per `(mk_id, issue_id, event_kind)`: `votes_for`, `votes_against`, `votes_abstain`, `votes_absent`. The `event_kind` split shows plenum and committee records separately, next to the SLM summary in the UI.

---

## Operational notes

- **Writes**: everything social-content goes through the staged MERGE flow in `INGESTION_CONTRACT.md`. No streaming inserts (`insertAll`) — retry double-writes + rows frozen in the streaming buffer ~90 min.
- **Embedding dimensions**: `social_post.embedding`, `issue_anchor.embedding`,
  and `bill.embedding` contain 3,072 `FLOAT64` values (post/bill arrays may be
  empty before their backfill). BigQuery cannot enforce fixed array length, so
  writers validate it.
- **Scan hygiene**: `social_post` and `post_issue` are the only tables expected to grow large. Filter `social_post` by `mk_id` (cluster) and `posted_at` (partition) wherever possible.
- **Rebuild after environment recreation or a schema migration (e.g. v3→v4)**: `schema.bq.sql` + `data/seed/issues.tsv` are idempotent once v4 exists; serving data replays from the pipeline's kept JSONL batches.
- **Empty prerequisite**: `mk` must be loaded (from Knesset OData) before any pipeline writes resolve.
