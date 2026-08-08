# Database Design — mk-tracking

**Status:** design proposal, ready to implement. Revised after self-review — see §8 for the change log.
**Date:** 2026-07-30 (v2)
**Concrete DDL:** [`db/schema.sql`](../db/schema.sql) · **Seed:** [`db/seed.sql`](../db/seed.sql)

This document specifies the database that backs the MK-tracking product. It is written so a follow-up turn (with Claude or another agent) can implement it, and so the sentiment-analysis team can flow their output into it with a clear contract.

---

## 1. What the product shows (recap of requirements)

The UI is built around **per-MK profiles**, aggregated into an **all-MKs overview**:

- **MK identity** — party, office/ministry, parliamentary roles (committee chairs, etc.).
- **Filters** — by issue/topic, by social-media platform, by statement language (He/En).
- **Positions per issue** — the core. For each MK × predetermined issue:
  - a time-sortable **list of quotes**, each linked to a specific social-media post and tagged with issue(s);
  - a **`is_concrete_promise` flag** per quote (a specific promise / concrete policy position → UI highlight + filter);
  - a **position summary** (short free text; may say "flip-flopped");
  - the **sentiment/valence** of the MK on that issue.
- **Notable related MKs** per MK.
- **Cross-MK layer** — group by party or by similar positions; filter/query all MKs by issue and stance ("matrix transpose" of who-said-what per issue).
- **Said vs did** — crossing stated positions with actual votes/parliamentary activity.

Producing the sentiment tags, quote buckets, and summaries is **another team's job**. Our job is the schema those land in and the read paths the UI uses.

---

## 2. Architecture decision: relational Postgres on Supabase

**Recommendation: PostgreSQL, hosted on [Supabase](https://supabase.com), frontend on [Vercel](https://vercel.com).** *(Confirmed with the team.)*

Why relational (not a document/graph store):
- The data is **highly relational and queried across dimensions** (MK × issue × platform × language × time, plus vote joins). SQL joins and `GROUP BY` are exactly the right tool.
- Your team already has **SQL/Snowflake fluency** — no new query paradigm.
- The issue taxonomy is a small closed set → clean foreign keys, not free-form documents.

Why **Supabase specifically** (vs. bare Postgres):
- It **is** Postgres — no lock-in, fully open-source, self-hostable if the nonprofit ever wants to leave.
- Ships a **instant auto-generated REST + GraphQL API** (PostgREST) over your tables. **The app needs no hand-written backend** — the frontend queries the DB directly through a typed client, safely, thanks to Row-Level Security.
- Built-in **auth**, **file storage** (MK photos), **Python & JS client libraries** (the sentiment team writes with `supabase-py` or a plain Postgres connection; the app reads with `supabase-js`).
- **Cost fits a nonprofit:** free tier (500 MB DB, 1 GB storage, 50k monthly active users) comfortably covers 120 MKs + tens of thousands of quotes. Paid Pro is ~$25/mo if you outgrow it.

Why **not Snowflake for the app DB:** Snowflake is an analytical warehouse — priced for large scans on a running warehouse, high per-query latency, no row-level security for a public app, and costly for the many small point-reads a profile page makes. It's the wrong shape for a live public UI. (The data team *may* still use Snowflake for heavy offline analysis, then push results into Postgres — but Postgres is the serving layer.)

**Alternatives considered** (if Supabase is ever rejected): **Neon** (serverless Postgres) + a hand-written FastAPI/Next API layer — more control, more work; **Railway/Fly.io** self-hosted Postgres — cheap but you operate it. All keep the same schema below.

```
                 ┌──────────────────────────────┐
  sentiment /    │   Supabase (managed Postgres) │        Next.js app
  ingestion team │   • tables + views (this doc) │        on Vercel
  (Python) ─────▶│   • PostgREST auto REST/GraphQL│◀────── (supabase-js,
  writes via     │   • Row-Level Security         │  reads  public anon key)
  service key    │   • Storage (MK photos)        │
                 └──────────────────────────────┘
```

---

## 3. Data model

### 3.1 Entity map

```
party ──┐
        │ (time-bounded)
        ▼
      mk_affiliation ──▶ mk ◀── mk_role ──▶ committee
                          │  ◀── mk_social_account   (registry of handles per platform)
        ┌─────────────────┼───────────────────────────┐
        ▼                 ▼                             ▼
   social_post ──▶ quote ──▶ quote_issue ──▶ issue ◀── mk_issue_position
                    (M:N quote↔issue,             ▲        (1 row per MK×issue:
                     carries sentiment)           │         stance + summary)
                                                  │
                              bill ──▶ bill_issue ┘
                               │
                               ▼
                            mk_vote ◀── mk

   mk ──▶ mk_relation ──▶ mk   (party / similar-position / "notable" edges)
```

### 3.2 Tables (rationale; full DDL in `db/schema.sql`)

**Identity & structure**

| Table | Purpose | Key points |
|-------|---------|-----------|
| `mk` | One row per Member of Knesset. | `slug` for profile URLs; `knesset_member_id` is the **external hook** to Open Knesset / OData / the votes source. `is_current` flags the sitting Knesset. |
| `party` | Political parties. | `is_current` distinguishes active parties. |
| `mk_affiliation` | **Time-bounded** party membership (`start_date`/`end_date`). | MKs switch parties — history is modeled from day one; seed only current rows now, back-fill later with no schema change. **`end_date is null` is the single definition of "current"** (no separate flag to drift). |
| `mk_role` | **Time-bounded** offices & parliamentary roles (minister, committee chair, coalition/opposition…). | `role_type` enum + free-text `title_he`/`title_en` (e.g. specific ministry) + optional `committee_id`. Current = `end_date is null`. |
| `mk_social_account` | **Registry of MK handles per platform** — seeded from the account list (the Google Sheet), independent of whether posts were collected yet. | Drives the "platforms used" filter (works before ingestion) and tells the pipeline where to look. `verified` marks human-confirmed accounts; `is_active` marks closed/suspended ones. |
| `committee` | Knesset committees. | External `knesset_committee_id`. |
| `issue` | The **predetermined policy/issue areas** — the closed taxonomy everything is tagged against. | Seeded in `db/seed.sql`; `slug` is the stable machine key. |

**The core positions model**

| Table | Purpose | Key points |
|-------|---------|-----------|
| `social_post` | Raw social-media posts (the evidence). | `platform` enum (twitter/facebook/instagram/telegram/tiktok/gov_il), native `platform_post_id`, `posted_at`, `text`, `language`, flexible `engagement` JSONB (likes/shares/…), `is_deleted` for deleted-post tracking. Unique on `(platform, platform_post_id)`. |
| `quote` | **The taggable statement unit** the sentiment team produces. | Links to `source_post_id` (nullable — a quote may come from a speech). Carries `said_at` (**timeline sort key**), `language` (**per-quote He/En/Ar tag + filter**), and **`is_concrete_promise`** (the true/false policy-declaration flag → UI highlight + filter). `model_version` for provenance. **Dedup:** a stored `text_hash` + unique `(mk_id, source_post_id, text_hash)` makes re-ingestion idempotent — pipeline re-runs upsert instead of duplicating. A trigger guards that `quote.mk_id` matches the source post's MK. |
| `quote_issue` | **M:N** quote ↔ issue, with the **sentiment on that issue**. | One post can touch several issues, each with its own `signal` (support/oppose/neutral/mixed) + optional `valence` (-1..1) + `confidence` (0..1), both range-checked. |
| `mk_issue_position` | **The rollup the profile shows: exactly one row per (MK, issue).** | `stance` enum (supports/opposes/mixed/unclear) **plus a separate `is_flip_flop` boolean** — consistency-over-time is an independent axis from current stance (an MK can support something *and* have flip-flopped to get there), and gets its own UI filter. `position_summary_he/_en` (the short summary text), `overall_valence`, `has_concrete_promise`. Unique on `(mk_id, issue_id)`. |

**Parliamentary activity (said vs did)**

| Table | Purpose |
|-------|---------|
| `bill` | Bills/motions, external `knesset_bill_id`. |
| `bill_issue` | M:N bill ↔ issue, so votes roll up to the same issue taxonomy. |
| `mk_vote` | How each MK voted (`for`/`against`/`abstain`/`absent`). A bill is voted multiple times (1st/2nd/3rd reading), so the key is `(mk_id, bill_id, reading)` — `reading` null when the source doesn't distinguish. |

**Connections**

| Table | Purpose |
|-------|---------|
| `mk_relation` | Directed edges between MKs: `same_party`, `similar_positions` (optionally per `issue_id`, with a `score`), `notable`, `ally`, `rival`. Powers "notable related MKs" and similarity grouping. |

### 3.3 Why this shape supports every UI requirement

- **Quote list, sortable by time, per issue** → `quote` filtered by `mk_id` + `quote_issue.issue_id`, ordered by `said_at`.
- **Concrete-promise highlight + filter** → boolean `quote.is_concrete_promise`, indexed.
- **Position summary / "flip-flopped"** → `mk_issue_position.position_summary_*` + the dedicated `is_flip_flop` boolean (filterable independently of stance).
- **Sentiment per issue** → `quote_issue.signal/valence` (per quote) and `mk_issue_position.overall_valence` (rollup).
- **Issue filter** → join through `issue` / `quote_issue`.
- **Platform filter** → `mk_social_account.platform` (which platforms an MK uses) and `social_post.platform` (per-quote origin).
- **Language tag + filter** → `quote.language` (and `social_post.language`).
- **Keyword search** ("show every quote mentioning X") → trigram (`pg_trgm`) GIN indexes on `quote.text` / `social_post.text`; substring matching works for Hebrew, where stock Postgres full-text stemming doesn't.
- **All-MKs overview + card grid** → `v_mk_card` view.
- **Group by party / similar positions; query all MKs by issue & stance** → `v_issue_landscape` + `mk_relation`.
- **Said vs did** → `v_said_vs_did` view.

---

## 4. Views (read models for the UI)

Defined in `schema.sql`; these keep the frontend queries simple:

- **`v_mk_card`** — one row per MK for the main grid: name, photo, current party, current roles, platforms used, issue count.
- **`v_mk_issue_position`** — per (MK, issue) block for the profile: stance, summaries, valence, quote & promise counts.
- **`v_issue_landscape`** — the **matrix transpose**: for each issue, every MK's stance/valence. Powers the cross-MK issue view and "group by position".
- **`v_said_vs_did`** — stated stance per issue next to that MK's vote tallies on bills tagged to the issue.

---

## 5. How the application queries it

With Supabase, the frontend talks to PostgREST via `supabase-js` using the **public anon key** — RLS guarantees read-only access. No backend to build.

**Main page — MK grid:**
```js
const { data } = await supabase
  .from('v_mk_card')
  .select('*')
  .eq('is_current', true)
  .order('full_name_he');
```

**Profile — the issue blocks (stance, summary, quote/promise counts):**
```js
const { data } = await supabase
  .from('v_mk_issue_position')
  .select('*')
  .eq('mk_id', mkId)
  .order('issue_slug');
```

**Profile — the quote timeline for one issue, with all filters.** Quotes relate to issues through `quote_issue` (not through the rollup table), so filtered quote lists go through the `search_quotes` RPC defined in `schema.sql` — one round-trip, all filtering in the DB:
```js
const { data } = await supabase.rpc('search_quotes', {
  p_mk_id: mkId,
  p_issue_slug: 'housing',
  p_platform: 'twitter',      // optional
  p_language: 'he',           // optional
  p_promises_only: true,      // optional
  p_keyword: 'מע"מ',          // optional free-text search (trigram-indexed)
  p_limit: 50, p_offset: 0,
});
```

**Cross-MK — everyone's stance on one issue (matrix transpose):**
```js
const { data } = await supabase
  .from('v_issue_landscape')
  .select('*')
  .eq('issue_slug', 'housing')
  .order('overall_valence', { ascending: false });
```

Add further RPCs in the same style as query patterns firm up; prefer an RPC over client-side assembly whenever a query crosses `quote_issue`.

---

## 6. Write path — ingestion contract for the sentiment team

The sentiment/ingestion pipeline writes with the **service-role key** (bypasses RLS) or a direct Postgres connection. All writes are **idempotent upserts** on natural keys, so re-runs are safe.

**Order & keys:**

1. **Seed once:** `issue` (by `slug`), `party`, `committee`.
2. **`mk`** — upsert on `knesset_member_id`.
3. **`mk_affiliation` / `mk_role`** — current rows for each MK (`end_date` null = current).
4. **`mk_social_account`** — the handle registry, from the account list; upsert on `(mk_id, platform, handle)`.
5. **`social_post`** — upsert on `(platform, platform_post_id)`.
6. **`quote`** — **upsert on the `quote_dedup` constraint** (`mk_id, source_post_id, text_hash` — the hash is computed by the DB from `text`); set `mk_id`, `source_post_id`, `language`, `said_at`, `is_concrete_promise`, `model_version`. Note `quote.mk_id` must match the source post's MK — a trigger rejects mismatches.
7. **`quote_issue`** — one row per (quote, issue) with `signal`/`valence`/`confidence`; upsert on `(quote_id, issue_id)`.
8. **`mk_issue_position`** — upsert on `(mk_id, issue_id)` with `stance`, `is_flip_flop`, `position_summary_*`, `overall_valence`, `has_concrete_promise`.
9. **Votes:** `bill` (upsert on `knesset_bill_id`), `bill_issue`, `mk_vote` (upsert on `(mk_id, bill_id, reading)`).

**Python example (`supabase-py`):**
```python
from supabase import create_client
sb = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

post = sb.table("social_post").upsert({
    "mk_id": mk_id, "platform": "twitter", "platform_post_id": tweet_id,
    "url": url, "posted_at": posted_at, "text": text, "language": "he",
    "engagement": {"likes": 120, "retweets": 8},
}, on_conflict="platform,platform_post_id").execute().data[0]

quote = sb.table("quote").upsert({
    "mk_id": mk_id, "source_post_id": post["id"], "text": quote_text,
    "language": "he", "said_at": posted_at, "is_concrete_promise": True,
    "model_version": "sentiment-v0.3",
}, on_conflict="mk_id,source_post_id,text_hash").execute().data[0]  # idempotent re-runs

sb.table("quote_issue").upsert({
    "quote_id": quote["id"], "issue_id": housing_id,
    "signal": "support", "valence": 0.7, "confidence": 0.9,
}, on_conflict="quote_id,issue_id").execute()

sb.table("mk_issue_position").upsert({
    "mk_id": mk_id, "issue_id": housing_id, "stance": "supports",
    "is_flip_flop": False,
    "position_summary_he": "תומך בהרחבת היצע הדיור...",
    "overall_valence": 0.6, "has_concrete_promise": True,
    "model_version": "sentiment-v0.3",
}, on_conflict="mk_id,issue_id").execute()
```

**Provenance:** `model_version` on `quote` and `mk_issue_position` lets the team trace which pipeline run produced a row and re-tag safely.

---

## 7. Implementation checklist (for the next turn)

1. Create a Supabase project (org's account). Note the project URL, `anon` key, `service_role` key.
2. Apply schema: `psql "$DATABASE_URL" -f db/schema.sql` (or add as a Supabase migration).
3. Seed issues: `psql "$DATABASE_URL" -f db/seed.sql`. Confirm the taxonomy with the team first.
4. Load MK identity data (120 current MKs) from the Knesset OData / `knesset-data-python` — populate `mk`, `party`, `mk_affiliation`, `mk_role`, `committee`.
5. Seed `mk_social_account` from the MK account list (see the external reference sheet noted in [`DEVELOPMENT.md`](DEVELOPMENT.md)); mark rows `verified` once a human confirms them.
6. Hand the sentiment team the **ingestion contract** (§6) + the `service_role` key (kept out of the repo / in env).
7. Scaffold the Next.js app on Vercel with `supabase-js`; wire the queries in §5 against the views in §4 and the `search_quotes` RPC.
8. Add further RPC functions as UI needs firm up.

**Open items to confirm with the team:**
- Final issue taxonomy (edit `db/seed.sql`).
- Whether `overall_valence` should be computed by the pipeline or derived in a view from `quote_issue`.
- Arabic-language quotes: the `content_lang` enum includes `ar` defensively even though the spec said He/En — confirm whether to surface an Arabic filter in the UI.
- Whether keyword search via trigram substring match is sufficient, or whether a real Hebrew search stack (e.g. Elasticsearch/Meilisearch with a Hebrew analyzer) is eventually needed. Trigram is fine at current scale.

---

## 8. Design review log (v2, 2026-07-30)

A self-review pass found and fixed the following in v1:

| # | Problem | Fix |
|---|---------|-----|
| 1 | **Quote ingestion wasn't idempotent** — the contract claimed "idempotent upserts" but `quote` had no natural key; pipeline re-runs would silently duplicate every quote. | Stored `text_hash` (md5 of text, DB-computed) + `unique nulls not distinct (mk_id, source_post_id, text_hash)`; contract now upserts. |
| 2 | **The §5 profile query example was wrong** — it embedded quotes under `mk_issue_position` via `mk`, which returns *all* the MK's quotes regardless of issue (quotes link to issues via `quote_issue`). | Replaced with the `search_quotes` RPC, now defined in `schema.sql` (issue + platform + language + promises + keyword, one round-trip). |
| 3 | **`is_current` + `end_date` on affiliation/role = two sources of truth** that drift. | Dropped the flags; `end_date is null` is the definition of current, with partial indexes. |
| 4 | **`flip_flopped` inside the stance enum conflated two axes** — current position vs. consistency over time (an explicit team measure). | Stance enum is now supports/opposes/mixed/unclear; separate `is_flip_flop` boolean, independently filterable. |
| 5 | **No home for the MK account registry** — platforms were inferred from collected posts, so the platform filter was empty before ingestion and unscraped handles had nowhere to live. | New `mk_social_account` table (handle registry, `verified`, `is_active`); `v_mk_card.platforms` now reads from it. |
| 6 | **`mk_vote` unique on `(mk_id, bill_id)`** breaks on real Knesset bills, which are voted in multiple readings. | Added `reading`; key is `(mk_id, bill_id, reading)` nulls-not-distinct. |
| 7 | **No keyword-search path** (a stated product feature), and stock Postgres FTS can't stem Hebrew. | `pg_trgm` extension + GIN trigram indexes on `quote.text` and `social_post.text`; `search_quotes` exposes `p_keyword`. |
| 8 | Assorted: no range checks on valence/confidence; `updated_at` never updated; denormalized `quote.mk_id` could contradict the source post; views ran as owner; single-column indexes on the timeline path. | `CHECK` constraints; `touch_updated_at` triggers; `quote_mk_guard` trigger; `security_invoker = true` on all views; composite `(mk_id, said_at desc)` + partial promise index. |

Known simplifications accepted at this stage: correlated subqueries in `v_mk_card` (fine at ~120 rows); md5 for dedup (not cryptographic, fine for dedup); trigram substring search instead of a Hebrew analyzer; no soft-delete/audit-history on pipeline overwrites (`model_version` is the provenance breadcrumb).

---

## 9. Infra pivot: GCP / BigQuery (2026-07-30)

**What changed:** the deployed infra is GCP. **Decision (Option B): BigQuery end-to-end** — the sentiment team writes to BQ, and the app reads BQ through a thin API. §2's Supabase/Postgres design remains the reference architecture and the migration target, but it is **not what's deployed**.

**Deployed reality:**
- **Project:** `YOUR_PROJECT_ID` (GCP; service account `your-service-account@your-project.iam.gserviceaccount.com`)
- **Dataset:** `mk_tracking` (location US) — the authoritative tables and serving views are defined in [`db/schema.bq.sql`](../db/schema.bq.sql); issues are seeded from [`data/seed/issues.tsv`](../data/seed/issues.tsv)
- ⚠️ **BigQuery projects are persistent.** The repo is the source of truth; schema is idempotent and rebuilds the whole dataset in under a minute when needed: `bq query --use_legacy_sql=false < db/schema.bq.sql`

**Postgres → BigQuery deltas** (details in the header of `schema.bq.sql`):
- Enums/CHECKs → STRING columns with allowed values in column descriptions; the **pipeline validates**.
- No unique constraints or triggers → **idempotency moves to the pipeline**: `MERGE` on the natural keys (`issue.slug`; `mk.knesset_member_id`; `(platform, platform_post_id)`; quote's `(mk_id, source_post_id, text_hash)` with the hash computed as `TO_HEX(MD5(text))`; `(mk_id, issue_id)`; `(mk_id, bill_id, reading)`). Plain `INSERT` in a re-runnable job **will duplicate rows** — always MERGE.
- ids are `GENERATE_UUID()` STRINGs; PK/FK are declared `NOT ENFORCED` (documentation + optimizer hints only).
- `quote`/`social_post` are day-partitioned and clustered by `mk_id` so per-MK reads stay cheap under BQ's scan-based pricing.
- `search_quotes` RPC → doesn't exist in BQ; the **thin API** runs the equivalent parameterized SQL. Keyword search via `LIKE`/`REGEXP_CONTAINS` (fine at our scale).
- No RLS → **the app must not query BQ directly from the browser.** Serving plan: a small API (Next.js API routes or FastAPI on Cloud Run, authenticated to BQ via ADC/service account) exposes the §5 read patterns; the frontend calls it.

**Auth model (local dev):** `gcloud auth login` + `gcloud auth application-default login` (done). Credentials live in `~/.config/gcloud/` — agents run `bq`/`gcloud` which read them; **agents never read the credential files themselves**.

**Honest trade-offs accepted:** 1–2s query latency (fine for a demo); integrity enforcement moved from DB to pipeline discipline; per-scan pricing (mitigated by partitioning/clustering and tiny data volume — free tier is 1TB scanned/month).

---

## 10. Spec change v3: no position layer; committee votes; complex issue mapping (2026-07-30)

> **Sections §3–§6 above describe v2 and are partially superseded.** The deployed v3 schema is `db/schema.bq.sql`; the working contract is `docs/INGESTION_CONTRACT.md`. The Postgres files (`db/schema.sql`, `db/seed.sql`) remain at v2 as an architecture reference and have NOT been updated.

### 10.1 Position layer removed

Team finding: discerning *which issues* a post relates to is reliable; extracting structured *positions* and placing MKs on them is not. New flow: aggregate posts per MK → segment by issue → an SLM reads each (MK × issue) bucket and writes a **free-text opinion summary**.

Schema consequences:
- **Dropped:** `quote`, `quote_issue` (the quote-extraction layer existed to feed structured position extraction), and `mk_issue_position`'s structured fields (`stance`, `is_flip_flop`, `overall_valence`).
- **Added:** `post_issue` — issue tagging at the **post** level (`confidence`, `model_version`, and `is_concrete_promise` **retained from the v2 spec pending team confirmation** — it was a headline UI feature and costs nothing to keep).
- **Replaced:** `mk_issue_position` → `mk_issue_summary`. Flip-flop
  observations live inside the summary text. The v5 taxonomy later added an
  evidence-grounded `1`–`5` rating whose meanings are defined separately for
  each issue; it is NULL when evidence quality is `none`.
- Views updated: `v_mk_issue_summary` (summary + post/promise counts), `v_issue_landscape` (per-issue matrix, now summary-based).

### 10.2 Committee votes: the `vote_event` generalization

v2 supported only plenum bill votes (`mk_vote` → `bill`). MKs also vote in committees, and that record is equally valuable for rhetoric-vs-record comparison.

- **`vote_event`** — anything MKs vote on: `event_kind` `plenum|committee`, optional `bill_id` (plenum readings), optional `committee_id` (committee motions), `reading`, natural key `external_key` (stable string from the source, e.g. `'odata:vote:12345'`).
- **`mk_vote`** now references `vote_event`, natural key `(mk_id, vote_event_id)`. A bill's separate readings are separate events — this also replaced v2's awkward `reading` column on the vote row.
- **`v_said_vs_did`** now tallies per (MK, issue, **arena**) — plenum and committee records display separately next to the SLM summary.

### 10.3 Complex vote-topic → issue mapping

Requirement: 1:N, N:1, and possibly M:N mappings, with subtleties not apparent from titles. Assessment: junction tables already support all these cardinalities natively — what was missing was mapping at the right granularity and **provenance**:

- **Two mapping levels:** `bill_issue` (bill-level — one mapping covers all that bill's vote events; avoids re-mapping three readings) and `vote_event_issue` (event-level — committee motions, and events whose subject diverges from the parent bill). Effective mapping = union of both, materialized in the `v_vote_event_issues` view.
- **Provenance on every mapping row:** `mapping_method` (`manual|model|rule`), `mapping_note` (the non-obvious rationale — e.g. "committee motion actually about VAT despite generic title"), `confidence`.

### 10.4 Issue curation

Direction at v3 was to prefer concrete, objectively-verifiable issue areas. The
deployed taxonomy is now the eight-row set documented in `docs/ISSUES.md` and
reproduced by `data/seed/issues.tsv`.

### 10.5 Verification

v3 was smoke-tested end-to-end on the live dataset: test MK → tagged post (2 issues, promise flag) → SLM summary → plenum vote FOR + committee vote AGAINST on cost-of-living-mapped events → `v_mk_issue_summary` and `v_said_vs_did` (arena-split) rendered correctly; merge re-runs produced zero duplicates. Test data removed afterward.

## 11. Spec change v4: issue prompts and evidence-grounded summaries (2026-07-30)

- The curated taxonomy is the eight-issue set in `docs/ISSUES.md`, synchronized
  from the live `mk_tracking.issue` table.
- `issue` now uses `name`, `description`, `prompt_for_social_post_similarity`, and `prompt_for_bill_similarity`. Separate prompts let post and bill classifiers use criteria appropriate to each source type.
- `bill.enacted_law_document_uri` points to the enacted-law document when a bill became law.
- `vote_event.draft_document_uri` points to the draft or text presented for that specific vote.
- `mk_issue_summary.summary_en` is removed. Summaries now carry `quality` (`strong`, `partial`, or `none`) and `limitations`.
- Supporting evidence is normalized into `mk_issue_summary_supporting_post`; the serving view exposes its post IDs as `supporting_post_ids`.
- The pipeline enforces evidence integrity: `strong`/`partial` requires supporting posts, `none` requires none, and every supporting post must belong to the summarized MK.
- `social_post.embedding` and `bill.embedding` store vectors as `ARRAY<FLOAT64>`; arrays are empty or exactly 3,072-dimensional, enforced by loaders because BigQuery does not enforce fixed length.
