# Vote-result identity crosswalk

Snapshot generated on 2026-07-31 from read-only queries against:

- the authoritative 120 current MKs in BigQuery;
- Over Knesset's `kns_plenumvoteresult` table.

The machine-readable artifact is `data/seed/vote_mkid_crosswalk.json`. It maps the
vote source's `mkid` to the canonical `mk.knesset_member_id` used by this
project. It has not been applied to BigQuery.

## Findings

| Measure | Count |
|---|---:|
| Authoritative current MKs | 120 |
| Exact one-to-one identity matches | 120 |
| MKs whose two IDs happen to match | 31 |
| MKs whose two IDs differ | 89 |
| Upstream vote results covered | 831,558 |
| Results belonging to same-ID MKs | 345,834 |
| Results belonging to different-ID MKs | 485,724 |

No fuzzy match was needed. Names were normalized only by removing whitespace
and punctuation and converting Hebrew final letters to their regular forms.
Generation fails if a canonical MK is missing, resolves more than once, or if
either identifier is not one-to-one.

The current production import contains 345,620 rows for the authoritative
roster. That is close to the same-ID result count, with the small difference
explained by source updates and the timing of the existing import. The identity
bug therefore accounts for approximately 486,000 missing historical results.

Concrete example:

| MK | Vote-source `mkid` | Canonical ID | Upstream results |
|---|---:|---:|---:|
| טלי גוטליב | 34379 | 30860 | 5,401 |

## Intended integration (not yet implemented)

The local loader integration is now implemented but has not been used for a
database write. It:

1. Loads and validates all 120 committed mappings at startup.
2. Fetches only the 120 reviewed vote-source IDs.
3. No longer joins `kns_plenumvoteresult.mkid` to `kns_person.id`.
4. Translates each result's `mkid` to canonical `knesset_member_id` before
   preparing the staging batch.
5. Rejects every unresolved source ID instead of silently dropping it.
6. Verifies that resolution preserves the fetched row count.

Still required before a production backfill:

1. Add staged/resolved/merged count checks around the existing idempotent
   BigQuery `MERGE`.
2. Produce and review per-MK before/after totals for the intended backfill
   time range.
3. Backfill BigQuery only after that review.
4. Separately measure issue mapping
   and UI coverage. The UI still deduplicates by title and shows at most ten
   votes per issue, so imported and displayed counts will remain different.

## Verified dry run

The integrated loader was run with `--dry-run --tables mk_vote
--start-date 2026-07-01`. It performed no BigQuery writes and produced:

| Stage | Count |
|---|---:|
| July 2026 vote events prepared | 447 |
| Vote results fetched for the reviewed roster | 36,342 |
| Vote results resolved | 36,342 |
| Vote staging records prepared | 36,342 |
| Unresolved identities | 0 |

For טלי גוטליב specifically, the source contains 304 July 2026 vote results,
from July 6 through July 28. These resolve from source ID `34379` to canonical
ID `30860` in the dry-run path.

The dedicated historical repair command (`backfill_mk_votes`) was removed with
the PostgreSQL migration — see git history. Its work now happens inline in the
main ingestion, which resolves every vote through this crosswalk and reconciles
all 120 reviewed identities on each run:

```bash
uv run python -m mk_tracking.collect.knesset.upload_over_to_postgres
```

The one-time production merge is captured in
`db/backfill_vote_mkids_2026-07-31.bq.sql`. It asserts the complete 485,724-row
staging batch and full MK/event resolution both before and after the MERGE.

## Applied result

The guarded backfill was applied on 2026-07-31. Four prerequisite vote events
were absent from BigQuery: two were restored from normal Over event metadata;
two orphan result sets (`25719`, `36529`) had consistent timestamps but no
event metadata and were preserved with provenance-explicit placeholder titles.
They will not appear under an issue unless classified separately.

| Verification | Before | After |
|---|---:|---:|
| Current MKs with raw votes | 31 | 120 |
| Current-roster vote rows | 345,620 | 831,344 |
| Current MKs with UI-visible issue votes | 31 | 119 |
| UI vote slots after deduplication/caps | 249 | 965 |
| טלי גוטליב raw votes | 0 | 5,401 |
| טלי גוטליב UI vote slots | 0 | 8 |

The staging batch contained 485,724 rows. Pre-merge and post-merge assertions
both passed with zero unresolved MKs and zero unresolved vote events.

The sole current MK without an issue-linked UI vote is מוחמד אבו אל היג'א.
This is an issue-classification coverage result, not an identity or raw-vote
ingestion failure.

## Reproduction

The generator performs read-only external queries and writes only the local
JSON file:

```powershell
uv run python -m mk_tracking.collect.knesset.build_vote_mkid_crosswalk
```

It deliberately reads the current roster from BigQuery. Regeneration must fail
unless exactly 120 MKs are current, preventing an accidental artifact from a
drifted roster.
