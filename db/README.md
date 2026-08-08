# Database Scripts & Assets (`db/`)

This directory contains the production BigQuery DDL, batch ingestion scripts, roster synchronization scripts, and staging table definitions for the `mk_tracking` dataset.

---

## 📜 Active SQL Scripts

### 1. `schema.bq.sql`
- **Purpose**: Definitive BigQuery DDL schema (v4). Creates the `mk_tracking` dataset and all core serving tables (`mk`, `party`, `committee`, `issue`, `issue_anchor`, `social_post`, `post_issue`, `mk_issue_summary`, `bill`, `vote_event`, etc.) along with column descriptions, clustering keys, and primary/foreign key metadata.
- **When to run**:
  - Initializing a new GCP project or environment.
  - Rebuilding the dataset after infrastructure setup or dataset recreation.
- **Execution Command**:
  ```bash
  bq query --use_legacy_sql=false --project_id="$GOOGLE_CLOUD_PROJECT" < db/schema.bq.sql
  ```

---

### 2. `merge_ingest.bq.sql`
- **Purpose**: Main pipeline batch ingestion MERGE script. Idempotently merges staged social posts (`stg_posts`), inline issue classifications (`post_issue`), per-MK issue summaries (`stg_summaries`), and supporting post evidence (`mk_issue_summary_supporting_post`) into serving tables.
- **When to run**:
  - Run automatically as part of the scheduled data ingestion pipeline after loading new JSONL batch files into the staging tables.
- **Execution Command**:
  ```bash
  bq query --use_legacy_sql=false --project_id="$GOOGLE_CLOUD_PROJECT" < db/merge_ingest.bq.sql
  ```

---

### 3. `merge_x_posts.bq.sql`
- **Purpose**: Dedicated MERGE script for parsed X/Twitter posts. Upserts records from `mk_tracking.stg_x_posts` directly into `mk_tracking.social_post`.
- **When to run**:
  - Run during the X post scraping and ingestion pipeline after staging new X post records.
- **Execution Command**:
  ```bash
  bq query --use_legacy_sql=false --project_id="$GOOGLE_CLOUD_PROJECT" < db/merge_x_posts.bq.sql
  ```

---

### 4. `apply_current_mk_roster.bq.sql`
- **Purpose**: Applies a curated 120-member Knesset roster snapshot from `mk_tracking.stg_current_mk_roster` to `mk_tracking.mk` and `mk_tracking.mk_affiliation`. Synchronizes `mk.is_current` flags and updates active party affiliations (`end_date IS NULL`).
- **Prerequisites**:
  1. Base person rows and party records loaded in `mk_tracking.mk` and `mk_tracking.party`.
  2. Curated roster TSV batch-loaded into staging table `mk_tracking.stg_current_mk_roster` (`knesset_member_id INT64, full_name_he STRING, party_name_he STRING`).
- **When to run**:
  - Whenever updating or synchronizing the official 120 sitting MK roster and party affiliations.
- **Execution Command**:
  ```bash
  bq query --use_legacy_sql=false --project_id="$GOOGLE_CLOUD_PROJECT" < db/apply_current_mk_roster.bq.sql
  ```

---

## 📁 Subdirectories & Assets

- **`staging/`**: Contains JSON schema files (`*.schema.json`) defining the explicit BigQuery schemas for loading batch JSONL files into staging tables (`stg_posts`, `stg_summaries`, `stg_issue_anchors`, etc.).
