# First BigQuery summary publication

## Published result

The validated Netanyahu top-five result was published to BigQuery.

- Run ID: `230e139c-7c80-4cbf-ae4d-85db771b2e12`
- Status: `completed`
- MK: בנימין נתניהו
- Model: `gemini-2.5-flash`
- Issues: 8
- Retrieved unique posts: 36
- Stored summaries: 8
- Stored supporting-post links: 3
- Recorded generation duration: 28,247 ms
- Cross-MK supporting sources: 0

The stored status distribution is:

- `partial`: 3
- `none`: 5
- `strong`: 0

## Database write behavior

The command now:

1. Inserts a `summary_generation_run` row with `running`.
2. Validates the complete local artifact and source ownership.
3. Starts a BigQuery transaction.
4. Upserts the MK's eight `mk_issue_summary` rows.
5. Removes the prior supporting-post links for those summaries.
6. Inserts the currently cited supporting posts.
7. Commits the transaction.
8. Marks the generation run `completed`.

If the transaction fails, existing summaries remain intact and the run is
marked `failed` with its error.

## Progress display

`generate-db` now uses `tqdm` for:

- Loading BigQuery input and top-five retrieval.
- Gemini generation or checkpoint validation.
- Transactional BigQuery publication.
- Completion.

This publication reused the already validated checkpoint, so the complete
command took 23.276 seconds, primarily BigQuery read/write job latency. The run
record preserves the original successful Gemini generation duration of 28.247
seconds rather than incorrectly recording the checkpoint-load time.

## Verified stored rows

| Issue | Quality | Supporting posts | Extended summary |
|---|---:|---:|---:|
| `oct7-accountability` | none | 0 | no |
| `haredi-conscription` | none | 0 | no |
| `policing-crime` | partial | 1 | yes |
| `settler-violence` | none | 0 | no |
| `settlements-funding` | partial | 1 | no |
| `economic-reforms` | partial | 1 | yes |
| `cost-of-living` | none | 0 | no |
| `infrastructure` | none | 0 | no |

The updated `v_mk_issue_summary` exposes `extended_summary_he` and
`generation_run_id`.

## Known review concern

The stored `economic-reforms` result is the previously documented
low-confidence false positive. Its highest retrieval confidence was `0.134`,
and the cited post concerns care for wounded security personnel rather than the
issue's structural-economic definition.

It was intentionally stored because this request asked to publish the reviewed
top-five result. Before processing the full database, add or calibrate a
retrieval-confidence gate to prevent similarly weak matches from creating
summaries.
