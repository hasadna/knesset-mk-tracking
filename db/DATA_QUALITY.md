# Data quality — measured from the SQLite export

Source: `mk_tracking.db` (SQLite, 798 MB, 2026-08-21), the export of the live
BigQuery `mk_tracking` dataset. Every figure below was measured, not estimated.
`db/schema.sql` and `db/load_from_sqlite.py` are built around these findings.

`mk_tracking_2.db` (136 MB) is a **strict subset** of the same export — identical
schema, identical row counts, except `social_post` holds 0 rows instead of 9,633,
and it lacks `summary_generation_run`, `tweet_cluster` and the six `v_` tables.
It contains nothing the larger file does not. Safe to delete.

## Why these problems exist

BigQuery marks every constraint `NOT ENFORCED`. The natural keys and foreign keys
in `docs/INGESTION_CONTRACT.md` were conventions the pipeline was asked to honour,
not rules the database applied. Each item below is a convention that drifted; all
of them become enforced at the point of the Postgres migration.

## Blocking

| Finding | Scale | Handling |
|---|---|---|
| `vote_event` and `vote_event_issue` are absent from the export | 907,210 `mk_vote` rows reference 34,159 missing events | Re-export both tables. Until then `v_said_vs_did` and `v_vote_event_issues` return 0 rows and `mk_vote`'s foreign key cannot be created. |

## Repaired automatically by the loader

| Finding | Scale | Handling |
|---|---|---|
| `social_post` violates its natural key `(platform, platform_post_id)` | 1,016 excess rows across 1,987 involved; 971 keys differ only in `fetched_at` (re-fetches); 0 differ in text; 5 keys are attributed to two different MKs | Keep the most recently fetched row per key. Dependent `post_issue` and evidence rows are repointed at the surviving post — 7,191 tags remapped, all of which then collapsed as exact duplicates, and 92 evidence links remapped with none lost. |
| `social_post.mk_id` holds a Twitter handle instead of a UUID | 27 rows (`yairlapid`, `YuliaMalinovsky`) | Resolved through `mk_social_account.handle`. This is the failure `INGESTION_CONTRACT.md` §6 calls "mk_id written as a handle/slug instead of the resolved UUID". |
| `social_post.account_id` holds the platform-native numeric id | 597 rows | Set to NULL — `mk_social_account` has no column holding the numeric id, so they cannot be resolved from this export. See `db/pending_constraints.sql` (2). |

## Left as documented debt

| Finding | Scale | Handling |
|---|---|---|
| `social_post.mk_id` UUIDs with no matching `mk` | 9 rows | FK declared `NOT VALID`: new writes checked, these 9 tolerated. |
| `mk.slug` is not unique | 6 slugs shared by different MKs | `slug` is indexed but not unique; `knesset_member_id` is unique instead. The ambiguous URL key is a product decision. |
| `party.name_he` is not unique | 6 repeated names | Indexed, not unique. |
| `bill_issue` is effectively empty | 10 rows against 23,240 bills | Not a migration problem — the "said vs did" comparison has almost no data behind it either way. |
| `committee`, `mk_role`, `bill_author`, `mk_relation` are empty | 0 rows each | `mk_role` is load-bearing: the read path derives each MK's coalition/opposition bloc from it, so that classification currently yields nothing for every MK. |

## Verified clean

Measured and found to hold, so enforced without qualification:

- `issue.slug`, `mk.knesset_member_id`, `bill.knesset_bill_id`, and
  `mk_social_account (platform, handle)` are unique.
- `post_issue (post_id, issue_id)`, `mk_issue_summary (mk_id, issue_id)`,
  `mk_vote (mk_id, vote_event_id)`, `issue_anchor (issue_id, anchor_index)`,
  `tweet_cluster (model_version, cluster_id)` are unique.
- Every foreign key except the three above resolves — 26 are enforced and
  satisfied by the loaded data.
- The rating invariant holds for all 1,773 summaries: a rating exists exactly
  when `quality <> 'none'`.
- All `post_issue.confidence` values lie in 0..1.
- Embeddings: 9,633 posts, 72 anchors and 90 centroids all carry exactly 3,072
  finite components. `bill.embedding` is `[]` for all 23,240 rows — unpopulated.
- The taxonomy is the v6 nine-issue set, matching `docs/ISSUES.md` and
  `db/seed.bq.sql` exactly. (`bigquery_issue_scoring.EXPECTED_ISSUE_COUNT` is
  still 7 and will raise against this data — fix it independently.)

## Load result

1,011,187 rows in roughly 50 seconds on a laptop, producing a **343 MB**
database against the 798 MB SQLite file. The reduction is almost entirely
embeddings: 635 MB of JSON text becomes 128 MB of `real[]`.

View parity against the export's own flattened views: `v_mk_card` 1,264,
`v_mk_issue_summary` 1,773, `v_issue_landscape` 1,773 — exact matches.

## A pre-existing view bug, faithfully ported

`post_issue` is a **dense score matrix, not a tag list**: every scored post carries
a row for all nine issues (min 9, mean 9.00, max 9). Only above the 0.2 serving
threshold does it behave like tagging — 3,295 posts averaging 1.04 issues each.

`v_mk_issue_summary.post_count` and `v_issue_landscape.post_count` do not filter
on confidence, so they count *all* of an MK's scored posts for *every* issue. In
practice every issue block for a given MK shows the same number — Yair Lapid
reads `posts=62` on all nine. The repository applies the 0.2 threshold itself,
so the UI is unaffected wherever it uses the repository rather than the view.

This is inherited from `db/schema.bq.sql`, where the same subquery has the same
omission; the Postgres views reproduce it deliberately so the migration stays a
faithful port. The fix is one predicate in each of the two views:

```sql
AND pi.confidence >= 0.2
```

Worth doing, but as its own change with its own review — not folded silently into
a migration.
