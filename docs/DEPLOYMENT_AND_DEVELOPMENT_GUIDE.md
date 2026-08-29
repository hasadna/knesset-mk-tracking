# Development & GCP Deployment Guide

This document serves as the operational guide for developers and AI coding assistants working on the **MK-Tracking** project. It covers local development, GCP deployment procedures, architecture, pipeline execution, and common troubleshooting steps.

---

## 1. Architecture & Technology Stack

| Layer | Technologies / Service | Location in Repository |
|---|---|---|
| **Frontend** | React 19, Vite, TypeScript, Vanilla CSS | `ui/` |
| **Backend API** | FastAPI, Uvicorn, Python 3.11 | `src/mk_tracking/ui_app/` |
| **Database** | GCP BigQuery (`mk_tracking` dataset) | `db/` & `docs/DB_SCHEMA.md` |
| **AI / Pipeline** | Gemini 2 (`gemini-embedding-2`, `gemini-2.5-flash`), `uv` | `src/mk_tracking/process/summaries/`, `src/mk_tracking/process/embeddings/big_query_to_data.py` |
| **Hosting & Container** | GCP Cloud Run, Artifact Registry, Cloud Build | `Dockerfile`, `run_daily_pipeline.sh` |

---

## 2. Repository & Branch Structure

* **`main`**: Primary branch containing application code, BigQuery integration, summary pipelines, and React UI updates.
* **`feature/production-hosted-app`**: Contains GCP hosting configurations (`Dockerfile`, `run_daily_pipeline.sh`, GitHub Actions workflows).

> [!IMPORTANT]
> **Branch Synchronization**: When preparing for production deployment, ensure changes from `main` and `feature/production-hosted-app` are merged so that `Dockerfile` and the compiled UI assets in `ui/dist/` are both present.

---

## 3. Local Development Workflow

### Prerequisites
* Python 3.11+ managed via [`uv`](https://github.com/astral-sh/uv)
* Node.js 20+ & `npm`
* Google Cloud SDK (`gcloud` CLI) authenticated via Application Default Credentials (`gcloud auth application-default login`)

### Running the Full Local Stack
1. **Build the Frontend Assets:**
   ```bash
   cd ui
   npm ci
   npm run build
   cd ..
   ```
2. **Start the FastAPI Server & UI:**
   ```bash
   uv run mkwork
   ```
   Access the app at: `http://127.0.0.1:8000`

### Frontend HMR Development
For interactive frontend development with Hot Module Replacement:
```bash
cd ui
npm run dev
```

### Environment Variables
* `MK_WORK_DATA_BACKEND`: `postgres` (default) or `json` (offline mock mode).
  There is no longer a `bigquery` option — setting it raises at startup.
* `DATABASE_URL`: PostgreSQL connection string. **Required** for the default
  backend; the service fails to start without it. The discrete `PGHOST` /
  `PGPORT` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` / `PGSSLMODE` variables work
  instead — see `src/mk_tracking/db_config.py` for the resolution order.
* `MK_TRACKING_SCHEMA`: schema holding the tables (default `mk_tracking`).

`GOOGLE_CLOUD_PROJECT` and `MK_WORK_BIGQUERY_DATASET` no longer affect serving.
They are still read by the unported pipeline steps described in the README.

---

## 4. GCP Deployment Guide (Cloud Run)

### Deployment Architecture
* **Cloud Run Service:** `mk-tracking-web` (Region: `us-central1`)
* **Artifact Registry Repository:** `us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/mk-tracking-repo/mk-tracking`

### Step-by-Step Production Deployment

#### Step 1: Ensure Frontend Assets are Built
FastAPI serves compiled static files from `ui/dist/`. Always build the frontend prior to container image creation:
```bash
cd ui
npm ci
npm run build
cd ..
```

#### Step 2: Verify `Dockerfile` Presence
Ensure `Dockerfile` is present in the repository root. If on `main` branch, ensure `Dockerfile` from `feature/production-hosted-app` is included.

#### Step 3: Build & Push Container Image
Submit the container build to GCP Cloud Build (set `$GOOGLE_CLOUD_PROJECT` first):
```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/mk-tracking-repo/mk-tracking:latest \
  --project=$GOOGLE_CLOUD_PROJECT
```

#### Step 4: Deploy to Cloud Run
> [!WARNING]
> **Crucial Cloud Run Behavior**: Pushing a new container image tag (e.g. `:latest`) to Artifact Registry **does NOT automatically update Cloud Run**. Cloud Run pins image digests per revision. You MUST execute `gcloud run deploy` to create a new revision.

```bash
gcloud run deploy mk-tracking-web \
  --image us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/mk-tracking-repo/mk-tracking:latest \
  --region us-central1 \
  --project=$GOOGLE_CLOUD_PROJECT \
  --set-env-vars MK_WORK_DATA_BACKEND=postgres \
  --set-secrets DATABASE_URL=mk-tracking-database-url:latest
```

`DATABASE_URL` carries a password, so it belongs in Secret Manager rather than
`--set-env-vars`, where it would be readable from the revision description.
Create it once with:

```bash
printf 'postgresql://USER:PASSWORD@HOST:5432/mk_tracking?sslmode=require' \
  | gcloud secrets create mk-tracking-database-url --data-file=- \
      --project=$GOOGLE_CLOUD_PROJECT
```

Grant the Cloud Run service account `roles/secretmanager.secretAccessor` on it.
A managed PostgreSQL instance normally requires `sslmode=require`. Deploying
without `DATABASE_URL` produces a revision that fails to start, with
`no database configured` in the logs.

#### Step 5: Verify Deployment
Inspect the deployed service URL and active revision:
```bash
gcloud run services describe mk-tracking-web --region=us-central1 --project=$GOOGLE_CLOUD_PROJECT
```
Verify the live response:
```bash
curl -I https://mk-tracking-web-941817562832.us-central1.run.app
```

### Safe Isolated Worktree Deployment Workflow

To avoid uncommitted file clutter on `main` or merge conflicts when deploying:

1. **Create an isolated worktree:**
   ```bash
   git worktree add -b prepare-gcp-deploy ../mk-tracking-deploy main
   cd ../mk-tracking-deploy
   ```

2. **Check out deployment infrastructure files:**
   ```bash
   git checkout origin/feature/production-hosted-app -- Dockerfile run_daily_pipeline.sh
   ```

3. **Build UI assets & deploy (set `$GOOGLE_CLOUD_PROJECT` first):**
   ```bash
   cd ui && npm ci && npm run build && cd ..
   gcloud builds submit --tag us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/mk-tracking-repo/mk-tracking:latest --project=$GOOGLE_CLOUD_PROJECT
   gcloud run deploy mk-tracking-web --image us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/mk-tracking-repo/mk-tracking:latest --region us-central1 --project=$GOOGLE_CLOUD_PROJECT
   ```

---

## 5. BigQuery & Data Pipeline Guidelines

* **Schema Reference:** [`DB_SCHEMA.md`](DB_SCHEMA.md)
* **Ingestion Contract:** [`INGESTION_CONTRACT.md`](INGESTION_CONTRACT.md)

### BigQuery Ingestion Rules
1. **Never use `CREATE OR REPLACE TABLE` on shared tables:** It wipes table metadata, PK/FK non-enforced constraints, column descriptions, partitioning, and default generators. Use `ALTER TABLE` or apply `db/schema.sql`.
2. **Use MERGE on Natural Keys:** BigQuery does not enforce unique constraints. All pipeline writes must use `MERGE` queries as described in `docs/INGESTION_CONTRACT.md`.
3. **No Streaming Inserts:** Avoid `insertAll` due to duplicate risks and ~90 minute row freezing in streaming buffers.

### Running Embedding & Summary Generation
* **Embedding pipeline (requires `$GOOGLE_CLOUD_PROJECT` set):**
  ```bash
  uv run process-embeddings
  ```
* **Summary generation (requires `$GOOGLE_CLOUD_PROJECT` set):**
  ```bash
  uv run process-summaries run-db \
    --project $GOOGLE_CLOUD_PROJECT \
    --dataset mk_tracking \
    --top-posts-per-issue 5
  ```

---

## 6. Guidelines for AI Coding Agents

When interacting with this repository, AI agents must adhere to the following rules:

1. **Security & Credentials:**
   * **NEVER** inspect or open files inside `~/.config/gcloud/` or read service account private keys directly. `gcloud` and `bq` tools access credentials automatically.
2. **Package Management:**
   * Use `uv add <package>` for Python runtime dependencies. **Do NOT use `pip install`**.
3. **Verification:**
   * Never claim a task or deployment is complete without running verification commands (`npm run build`, `pytest`, `gcloud run services describe`, or `curl`).
4. **Code Quality:**
   * Format Python code using `uvx ruff format .` and check linting via `uvx ruff check .`.
