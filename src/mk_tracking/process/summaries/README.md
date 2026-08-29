# MK Summary Creator

Creates the `analysis.json` consumed by the FastAPI application in
`ui/`. Gemini runs on Vertex AI, but all evidence and citations are
restricted to `ui/data/tweets.normalized.json`.

## Safety properties

- One model request per politician; tweets are never mixed between politicians.
- Structured model output is validated locally.
- Unknown, duplicate, and cross-politician citations fail the run.
- Opinions cite at most four distinct tweets; `strong` requires at least two.
- Missing topics become explicit `none` records.
- Checkpoints make interrupted runs resumable.
- Each run records elapsed generation time per politician in a timing sidecar.
- Publishing requires every politician with tweets to be present.
- The existing analysis is backed up before an atomic replacement.

## Setup

Enable the Vertex AI API in a GCP project, authenticate locally with Application
Default Credentials, then install:

```powershell
gcloud auth application-default login
uv sync
```

The SDK uses `GOOGLE_CLOUD_PROJECT` and, optionally,
`GOOGLE_CLOUD_LOCATION` (default: `global`).

## Review without spending tokens

Write prompts locally:

```powershell
uv run process-summaries preview --politician netanyahu
```

The selector accepts an account, Hebrew name, or roster key. Omit it to preview
all prompts.

## Generate

Start with one politician:

```powershell
$env:GOOGLE_CLOUD_PROJECT="your-project-id"
uv run process-summaries generate --politician netanyahu
```

After reviewing the checkpoint, delete `output/checkpoints` and run the complete
dataset:

```powershell
uv run process-summaries generate
uv run process-summaries validate output/analysis.candidate.json
```

Generation reuses existing checkpoints. Delete a politician's checkpoint to
regenerate only that result.

Alongside the candidate, the command writes
`output/analysis.candidate.timings.json`. It reports `elapsedSeconds` for each
politician and marks whether the result came from Vertex AI or a checkpoint.

Each generated opinion contains a short `stance`, optional `extendedStance`,
`limitations`, up to four sources, and an issue-specific `rating` from 1 to 5.
The rating is required for `strong` and `partial` evidence and is `null` for
`none`.

Publish only a complete candidate:

```powershell
uv run process-summaries publish output/analysis.candidate.json
```

This validates the full member set, creates
`ui/data/analysis.json.backup`, and atomically replaces the analysis.

The default model is `gemini-2.5-flash`; override it with `--model`.

## Generate from BigQuery

Read one politician, the current issue taxonomy, and Twitter posts directly
from the live schema while keeping all generated output local:

```powershell
uv run process-summaries generate-db `
  --project $env:GOOGLE_CLOUD_PROJECT `
  --dataset mk_tracking `
  --politician netanyahu `
  --top-posts-per-issue 5 `
  --output output/netanyahu-db-sample.json
```

The selector accepts the internal DB UUID, numeric `knesset_member_id`, MK
slug, exact Hebrew name, or Twitter handle. The creator deduplicates
`post_issue`, selects the highest-confidence posts for each issue, unions those
sets, and sends one combined request per MK. `generate-db` does not write
summaries or run rows to BigQuery.

Add `--write-db` to publish the fully validated local result:

```powershell
uv run process-summaries generate-db `
  --project $env:GOOGLE_CLOUD_PROJECT `
  --dataset mk_tracking `
  --politician netanyahu `
  --top-posts-per-issue 5 `
  --output output/netanyahu-db-top5-sample.json `
  --write-db
```

The command displays a `tqdm` progress bar for input loading, generation or
checkpoint validation, transactional publication, and completion. Publication
upserts all issue summaries for the MK and replaces their supporting-post links
inside one BigQuery transaction. A `summary_generation_run` row records the
model, post count, issue count, status, and generation duration.

### Generate a selected batch safely

Vertex generation for different MKs may run concurrently, but BigQuery
publication must be sequential: concurrent transactions updating
`mk_issue_summary` can abort one another. For a batch, first run `generate-db`
without `--write-db` in parallel with a unique output path per MK. After all
candidates validate, rerun each command one at a time with `--write-db`.
The second pass reuses the per-MK checkpoint, so it does not make another
model call.
