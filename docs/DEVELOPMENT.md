# Development Guide

This document contains architecture notes, BigQuery schema guidance, pipeline design decisions, and operational information for contributors.

## Related projects in the Hasadna ecosystem

Understanding how this project fits into the larger open-data landscape:

### Open Knesset Frontend 2.0
**Repo:** https://github.com/hasadna/open-knesset-frontend-2.0  
Next.js 15 frontend for Open Knesset. Consumes a FastAPI wrapper over the Knesset OData v2 API. Most actively maintained Knesset-related repo. Has comprehensive docs: PRD, architecture, design system, conventions.

### Knesset Data Pipelines
**Repo:** https://github.com/hasadna/knesset-data-pipelines  
ETL pipelines for Knesset data (committee protocols, votes, etc.) going back to 2005. Airflow-based, Docker-first. Includes Jupyter notebooks for exploration.

### knesset-data-python
**Repo:** https://github.com/hasadna/knesset-data-python  
Low-level Python client for the Knesset data service API. Installable: `uv add knesset-data`. Provides access to MK profiles, committees, votes. Note: Knesset blocks some requests — check for reblaze responses.

### Kikar Hamedina
**Site:** http://kikar.org  
**API:** http://kikar.org/api/v1/  
Existing Hasadna project that collects and surfaces MK Facebook posts. The natural sister project. API supports filtering by content, date, MK, party.

---

## Data sources: research and rationale

### ⭐ IsraParlTweet — historical backbone

**Paper:** https://arxiv.org/abs/2405.20269 · **Data:** https://huggingface.co/datasets/guymorlan/IsraParlTweet  
Linked corpus from Hebrew University (Mor-Lan, Levi, Sheafer, Shenhav). Two halves:
- **Knesset floor speeches** 1992–2023 (~4.5M utterances) with speaker IDs, dates, topics — freely downloadable CSV (~22GB)
- **MK tweets** 2008–2023 with full text + engagement metrics — gated (contact guy.mor@mail.huji.ac.il)
- Includes MK tenure/party metadata and linguistic annotations (sentiment predictions directly relevant to our use case)
- License: CC-BY-4.0

**Why we chose it:** This corpus already crosses stated positions (tweets) with parliamentary activity (speeches), providing a strong historical foundation (2008–2023).

**Limitation:** Ends in 2023. We supplement with the official X API for ongoing updates (2024→present).

### ILElectionData — secondary reference

**Repo:** https://github.com/jschler/ILElectionData  
Jonathan Schler. ~5M Facebook posts+comments from Israeli politicians (2019–2020). Openly downloadable with no gating. Narrower (elections-only, ends 2020) but useful as a model-training corpus.

### Official X (Twitter) API — ongoing data

**Reality as of 2026:**
- Full-archive search (back to 2006) is Enterprise-only (contract required, ~$42k/month). Not feasible.
- Pro tier (full-archive) is closed to new signups.
- Pay-per-use model (~$0.005/post, ~2M reads/mo cap) only covers the **last 7 days**.

**Design decision:** The project rejected third-party scraper APIs (twitterapi.io, data365, etc.) as not compliant with X's Developer Agreement. Instead, the pipeline uses IsraParlTweet's historical corpus for baseline data and the official X API for daily incremental updates.

### Knesset OData API

**Docs:** http://oknesset-api.readthedocs.io/en/latest/  
**Main endpoint:** http://main.knesset.gov.il/Activity/Info/Pages/Databases.aspx

Official public-record source for parliamentary activity (votes, bills, committees, MK profiles). Primary source for ground truth.

### MK social media account list (reference)

**Google Sheet (external):** https://docs.google.com/spreadsheets/d/1tUGtlYHUIWl3UUd98KM8QbkrAqYTBBgYI88v0HTXjRE/edit

Public reference document maintained externally. Use for initial discovery; actual MK identities resolved through the `mk` table in BigQuery.

---

## BigQuery Schema & Operational Notes

The pipeline uses Google BigQuery as the canonical data warehouse. All processing (embeddings, clustering, issue classification) happens in BigQuery; results are cached locally for development and debugging.

### Deployed infrastructure

- **Database:** Google BigQuery (deployed v4 schema)
- **Dataset:** `mk_tracking`
- **Python environment:** Python 3.11, uv package manager
- **Backend:** FastAPI
- **Frontend:** React 19, Vite, TypeScript

### BigQuery best practices

**Never use `CREATE OR REPLACE TABLE` on shared tables.** This wipes all metadata unless explicitly re-declared. Use `ALTER TABLE` for additive changes. If a full rebuild is needed, use the canonical `db/schema.bq.sql` (the source of truth for all metadata) and copy data back.

What gets silently lost when metadata is not preserved:

1. **PK/FK `NOT ENFORCED` constraints** — BigQuery refuses to create an FK referencing a table without a declared PK, and the query optimizer uses them for join elimination.
2. **Column descriptions** — BigQuery has no CHECKs or enum types, so **allowed-value lists live in column descriptions** (`platform`, `role_type`, `vote`, ...). Bare recreations erase the value contract.
3. **Partitioning & clustering** — `social_post` is day-partitioned (`posted_at`) and clustered (`mk_id, platform`). Recreate without it and per-MK queries silently become full-table scans.
4. **Column defaults** — `GENERATE_UUID()` ids, `CURRENT_TIMESTAMP()`, `FALSE` flags. Lost defaults cause NULLs to be written where convention expects NOT NULL.
5. **`NOT NULL` modes** and dataset labels/descriptions (minor).

### Schema reference

For detailed documentation, see:

| Resource | Location |
|----------|----------|
| Database design & rationale | `docs/DATABASE_DESIGN.md` |
| Schema reference (tables, columns, keys) | `docs/DB_SCHEMA.md` |
| Pipeline → DB ingestion contract | `docs/INGESTION_CONTRACT.md` |
| Ingestion MERGE script + staging schemas | `db/merge_ingest.bq.sql`, `db/staging/*.schema.json` |
| BigQuery DDL v4 (deployed) | `db/schema.bq.sql` |
| BigQuery seed data (deployed) | `data/seed/issues.tsv` |
| Knesset OData API docs | http://oknesset-api.readthedocs.io/en/latest/ |

### Initialization (first time)

After deploying schema, initialize BigQuery tables:

```bash
bq query --use_legacy_sql=false < db/schema.bq.sql
# Load seed issues from data/seed/issues.tsv
```

Initialize issue anchor embeddings (one-time):

```bash
uv run big_query_to_data.py --initialize-anchors
```

This embeds the seven live issues' hand-curated semantic anchors and inserts them into BigQuery. Subsequent runs omit this flag and assume anchors already exist.

### Ingestion principles

- **Use MERGE on natural keys** — BigQuery has no unique constraints, so MERGE prevents duplicates in re-runnable jobs (versus plain INSERT).
- **Never use streaming inserts** (`insertAll`) — they double-write on retries and rows are frozen for ~90 minutes.
- **Resolve MK IDs through the `mk` table** — never write handles directly as IDs.
- Follow `docs/INGESTION_CONTRACT.md` for the contract between pipeline stages and the database.

### Common BigQuery commands

```bash
# List tables
bq ls mk_tracking

# Show schema with descriptions
bq show --schema --format=prettyjson mk_tracking.social_post

# Run a query
bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM mk_tracking.mk'

# List jobs (useful for debugging)
bq ls -j --all
```

---

## Pipeline design: embeddings & clustering

### Tweet embeddings

All tweets are embedded using `gemini-embedding-2` (3,072-dimensional vectors). The embedding model is fixed globally in the configuration; custom dimensions can be specified per run via CLI flags.

Embeddings are computed incrementally:
- New tweets are embedded via the Gemini API
- Results are checkpointed after every completed row
- Rerunning resumes from a partial output without re-spending API quota
- Complete output files skip the embedding stage entirely

### K-means clustering

If `data/processed/tweet_cluster_centroids.npz` does not exist, the pipeline:

1. Fits K-means with `K=30`, seed `42`, one deterministic initialization
2. Selects the 100 tweets closest to each centroid
3. Calls `gemini-2.5-flash` to generate Hebrew titles and meanings
4. Asks Gemini to classify each cluster as garbage (not political/public messaging, or incoherent)
5. Saves centroids, meanings, cluster sizes, sampled tweet IDs, garbage flags, and configuration

The generated file is the source of truth for garbage cluster IDs. If centroids already exist, clustering and summarization are skipped—but BigQuery read and incremental embedding still run. Delete only the centroid file to force recomputation.

### Issue scoring

For each tweet, the pipeline:

1. Computes max cosine similarity across an issue's eight semantic anchors
2. Applies softmax with temperature `0.07` across all seven issues
3. Sets all probabilities to zero if:
   - Text has fewer than 16 characters after trimming whitespace and URLs
   - K-means assigns the tweet to a garbage cluster

Scores are versioned; rows from the current model are skipped while older versions are refreshed.

### Run history

Every invocation appends a record to `data/processed/big_query_to_data_run_history.jsonl`, including:
- Number of new tweets embedded
- Whether centroids were updated
- Reported Gemini API token usage
- Gemini API cost in USD
- BigQuery bytes processed (but not monetary cost, since classroom credits, free tier, and billing settings cannot be inferred locally)

---

## Development & testing

### Lint & format

```bash
uvx ruff check .          # lint
uvx ruff check . --fix    # lint + autofix
uvx ruff format .         # format
```

### Run tests

```bash
uv run pytest -q
```

### Local snapshot validation

The UI can run against a local JSON snapshot (for development without live BigQuery access):

```bash
# Validate the local evidence snapshot
uv run mkwork validate

# Start UI in offline mode
MK_WORK_DATA_BACKEND=json uv run mkwork
```

Then open http://127.0.0.1:8000. For frontend HMR development:

```bash
cd ui && npm run dev
```

### Package management

```bash
uv add <pkg>         # add a runtime dep
uv add --dev <pkg>   # add a dev dep
uv sync --frozen     # install from lockfile
```

---

## Subprojects

This repo contains three independent uv-managed Python projects sharing a root lock:

- **Main**: embeddings, clustering, FastAPI UI backend
- **x_api_collect/** — X API data collection (collects MK tweets)
- **bill_issues/** — Bill-to-issue classification using Gemini

The `Dockerfile` and `run_daily_pipeline.sh` orchestrate all three.

---

## API client resilience

The Gemini API client retries quota and transient errors (`429`, `5xx`) with exponential backoff. The CLI prints:
- Completed rows
- Percentage progress
- ETA
- Retry delays

No credentials or project IDs are ever written to the repository.
