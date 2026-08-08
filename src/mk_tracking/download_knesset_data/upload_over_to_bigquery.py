#!/usr/bin/env python3
"""
Upload Over Knesset Data to BigQuery
====================================
A standalone script that queries Members of Knesset (MKs), political parties (factions),
MK affiliations, proposed/enacted bills, vote events, and MK voting records from the Over Knesset API (https://www.over.org.il/api/knesset-db/sql)
and upserts them into a Google BigQuery database (`mk_tracking`) according to `docs/DB_SCHEMA.md`.

Target Database:
  Project: $GOOGLE_CLOUD_PROJECT (set via environment or --project-id)
  Dataset: mk_tracking
  Tables populated: party, mk, mk_affiliation, bill, vote_event (prerequisite for votes), mk_vote

Idempotency & Internal Foreign Key Resolution:
  - Re-run Safety: This script is fully idempotent and safe to execute repeatedly (e.g. via cron).
  - Natural Key Matching: Records are staged with source natural keys (`knesset_member_id`, `knesset_bill_id`, `name_he`,
    and `external_key`) and merged using SQL `MERGE` statements. Existing rows are updated in-place;
    new rows are inserted. No duplicate rows or collisions are created.
  - Foreign Key Resolution: Internal database UUIDs (`mk.id`, `party.id`, `bill.id`, `vote_event.id`) are resolved
    dynamically inside BigQuery by JOINing staging tables on natural keys during the `MERGE` step.
    The script never invents or hardcodes internal UUID foreign keys in Python.

Default Behavior:
  By default, only active (current) MKs, active parties, active affiliations, and current bills are synced.
  Use `--include-inactive` to include historical (inactive) MKs, parties, and bills.

Usage Options:
  --dry-run           Simulate execution without modifying BigQuery tables
  --include-inactive  Include historical/inactive MKs, parties, bills, and affiliations (default: active only)
  --start-date        Filter bills, vote events, and votes from this timestamp onward (e.g. 2026-01-01)
  --max-workers       Number of parallel threads for document batch uploading (default: 16)
  --gcp-bucket        Target GCP Cloud Storage bucket for PDF/Word documents
  --tables            Specify tables to sync: mk, party, mk_affiliation, bill, mk_vote, or all

Examples:
  python upload_over_to_bigquery.py
  python upload_over_to_bigquery.py --dry-run
  python upload_over_to_bigquery.py --start-date 2026-01-01 --max-workers 16
  python upload_over_to_bigquery.py --tables bill mk party
"""

import argparse
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from mk_tracking.download_knesset_data.vote_identity import (
    load_crosswalk,
    resolve_vote_results,
)

# API & SSL Configuration
OVER_API_BASE = "https://www.over.org.il/api/knesset-db/sql"
SSL_CTX = ssl._create_unverified_context()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VOTE_CROSSWALK = PROJECT_ROOT / "data" / "automatic" / "vote_mkid_crosswalk.json"

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("over_bq_uploader")


# OVER API Pagination & Rate Limit Recovery:
# - The OVER API caps single SQL query responses at 1,000 rows.
# - fetch_all_over_sql() paginates automatically using `LIMIT 1000 OFFSET X` loops to fetch 100% of records.
# - HTTP 429 Rate Limiting is caught automatically and retried with exponential backoff (attempt * 4 seconds).

def run_over_sql(sql: str, retries: int = 10) -> dict[str, Any]:
    """Execute SQL query against Over Knesset API with retry handling."""
    data = json.dumps({"sql": sql}).encode("utf-8")
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(OVER_API_BASE, data=data, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                offset_match = re.search(r"OFFSET\s+(\d+)", sql, re.IGNORECASE)
                offset_str = f"OFFSET {offset_match.group(1)}" if offset_match else "OFFSET 0"
                if attempt < retries:
                    sleep_time = attempt * 4
                    logger.warning(f"Rate limited (429) at {offset_str}. Retrying in {sleep_time}s (attempt {attempt}/{retries})...")
                    time.sleep(sleep_time)
                    continue
                else:
                    logger.error(f"❌ Reached maximum retry count ({retries}/{retries}) for query at {offset_str}. Rate limit / HTTP 429 persisted.")
                    break
            logger.error(f"HTTP Error {e.code} for query: {sql[:100].strip()}")
            break
        except Exception as e:
            if attempt < retries:
                time.sleep(attempt)
                continue
            logger.error(f"Error querying Over API: {e} for SQL: {sql[:100].strip()}")
            break
    return {"columns": [], "rows": []}


def fetch_all_over_sql(
    sql: str,
    user_limit: int | None = None,
    page_size: int = 1000,
    progress_label: str | None = None,
) -> list[dict[str, Any]]:
    """Execute SQL query against Over API with automatic offset pagination if rows hit page cap."""
    clean_sql = sql.strip().rstrip(";").strip()
    if user_limit and user_limit <= page_size:
        res = run_over_sql(f"{clean_sql} LIMIT {user_limit};")
        return res.get("rows", [])

    all_rows = []
    offset = 0

    while True:
        current_limit = page_size
        if user_limit and (len(all_rows) + current_limit) > user_limit:
            current_limit = user_limit - len(all_rows)

        page_sql = f"{clean_sql} LIMIT {current_limit} OFFSET {offset};"
        res = run_over_sql(page_sql)
        rows = res.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)
        if progress_label:
            logger.info(
                "%s: fetched %s rows", progress_label, f"{len(all_rows):,}"
            )
        if len(rows) < current_limit or (user_limit and len(all_rows) >= user_limit):
            break
        offset += len(rows)
        time.sleep(0.3)  # Brief delay to prevent rate-limiting during page iteration

    return all_rows


def slugify_hebrew_name(name: str, knesset_member_id: int) -> str:
    """Generate a clean URL-friendly slug for an MK."""
    if not name:
        return f"mk-{knesset_member_id}"
    cleaned = re.sub(r"[^\w\s-]", "", name).strip()
    slug = re.sub(r"\s+", "-", cleaned)
    return slug if slug else f"mk-{knesset_member_id}"


def map_vote_result(result_code: int | None, result_desc: str | None) -> str:
    """Map Over API vote result codes/descriptions to DB schema enum ('for'|'against'|'abstain'|'absent')."""
    if result_code == 7 or result_desc == "בעד":
        return "for"
    elif result_code == 8 or result_desc == "נגד":
        return "against"
    elif result_code in (6, 9) or result_desc in ("נמנע", "נוכח"):
        return "abstain"
    elif result_code == 10 or result_desc in ("לא נכח/ אינו נוכח", "לא נכח"):
        return "absent"
    return "absent"


def format_date_str(val: str | None) -> str | None:
    """Format timestamp/date string to YYYY-MM-DD for BigQuery DATE columns."""
    if not val:
        return None
    val_str = str(val).strip()
    if len(val_str) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", val_str):
        return val_str[:10]
    return None


class BigQueryUploader:
    """Handles dataset loading and MERGE operations into BigQuery via Google Cloud Python SDK."""

    def __init__(
        self,
        project_id: str,
        dataset_id: str,
        credentials_file: str | None = None,
        dry_run: bool = False,
    ):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.dataset_ref = f"{project_id}.{dataset_id}"
        self.credentials_file = credentials_file
        self.dry_run = dry_run
        self.client = None

        if self.dry_run:
            logger.info("🔍 DRY-RUN MODE ACTIVE: Data will be fetched and prepared, but BigQuery will NOT be modified.")
            return

        # If credentials file provided explicitly
        if credentials_file:
            if not os.path.exists(credentials_file):
                logger.error(f"Credentials file '{credentials_file}' not found!")
                sys.exit(1)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(credentials_file)
            logger.info(f"Using Service Account credentials from: {credentials_file}")

        # Initialize Python BigQuery SDK client
        try:
            from google.cloud import bigquery
            if credentials_file:
                self.client = bigquery.Client.from_service_account_json(
                    credentials_file, project=project_id
                )
            else:
                self.client = bigquery.Client(project=project_id)

            self.client.get_dataset(self.dataset_ref)
            logger.info("Successfully connected to BigQuery using Python SDK.")
        except Exception as e:
            logger.error(f"Failed to initialize BigQuery Python SDK client: {e}")
            sys.exit(1)

    def _execute_merge_sql(self, merge_sql: str, entity_name: str = "records"):
        """Execute a MERGE query using Google Cloud BigQuery Python SDK."""
        if self.dry_run:
            return

        try:
            merge_job = self.client.query(merge_sql)
            merge_job.result()
        except Exception as e:
            err_msg = str(e)
            if "UPDATE/MERGE must match at most one source row" in err_msg:
                logger.error(
                    f"\n❌ [BIGQUERY MERGE ERROR 400] Multiple source rows matched a single target row when updating '{entity_name}'.\n"
                    f"   Reason: Duplicate natural keys exist in the staged data or in joined reference tables.\n"
                    f"   Diagnostic Details: {err_msg}\n"
                )
            else:
                logger.error(f"❌ Execution error during MERGE for '{entity_name}': {e}")
            raise

    def _load_staging_data(self, stg_table_name: str, records: list[dict[str, Any]]):
        """Load records into a staging table using BigQuery Python SDK."""
        if self.dry_run:
            return

        stg_table_id = f"{self.dataset_ref}.{stg_table_name}"
        from google.cloud import bigquery
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            autodetect=True
        )
        job = self.client.load_table_from_json(records, stg_table_id, job_config=job_config)
        job.result()

    def _cleanup_staging_table(self, stg_table_name: str):
        """Drop staging table after MERGE operation using BigQuery Python SDK."""
        if self.dry_run:
            return

        stg_table_id = f"{self.dataset_ref}.{stg_table_name}"
        self.client.delete_table(stg_table_id, not_found_ok=True)

    # BigQuery MERGE Error 400 Prevention & Deduplication:
    # - BigQuery MERGE requires that at most ONE source staging row matches each target row.
    # - OVER DB contains duplicate historical records (e.g., duplicate party names in kns_faction across terms).
    # - Multi-Sponsors & Multi-Formats: Bills with multiple co-sponsors (5-15 MKs) and vote events with multiple document
    #   formats (.pdf, .docx, .tif) generate duplicate candidate rows.
    # - Resolution: Python pre-ingestion deduplication (_deduplicate_records) + PostgreSQL DISTINCT ON (id) +
    #   SQL QUALIFY ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY <field>) = 1 windowing in MERGE queries.
    def _deduplicate_records(
        self, records: list[dict[str, Any]], key_fields: list[str], entity_name: str
    ) -> list[dict[str, Any]]:
        """Identify duplicate records on natural key_fields, log detailed warnings, and return deduplicated list."""
        seen = set()
        deduped = []
        duplicates = []

        for rec in records:
            key = tuple(rec.get(k) for k in key_fields)
            if key in seen:
                duplicates.append(key)
            else:
                seen.add(key)
                deduped.append(rec)

        if duplicates:
            sample = duplicates[:5]
            logger.warning(
                f"⚠️ [DUPLICATE DETECTED] Found {len(duplicates)} duplicate record(s) for '{entity_name}' on keys {key_fields}.\n"
                f"   Automatically deduplicated ({len(records)} → {len(deduped)} rows) to prevent BigQuery MERGE 400 error.\n"
                f"   Sample duplicate keys: {sample}"
            )

        return deduped

    def load_and_merge_parties(self, party_rows: list[dict[str, Any]]) -> int:
        """Upload and MERGE parties into party table."""
        if not party_rows:
            logger.info("No party records to upload.")
            return 0

        stg_name = "stg_party_temp"
        records = [
            {
                "name_he": row["name"].strip(),
                "is_current": bool(row.get("iscurrent", True)),
            }
            for row in party_rows
            if row.get("name") and row["name"].strip()
        ]

        records = self._deduplicate_records(records, ["name_he"], "party")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Prepared {len(records)} party records for `{self.dataset_ref}.party`.")
            if records:
                logger.info(f"[DRY-RUN] Sample party record: {records[0]}")
            return len(records)

        logger.info(f"Staging {len(records)} party records...")
        self._load_staging_data(stg_name, records)

        merge_sql = f"""
        MERGE `{self.dataset_ref}.party` T
        USING (
          SELECT * FROM `{self.dataset_ref}.{stg_name}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY name_he ORDER BY is_current DESC) = 1
        ) S
        ON T.name_he = S.name_he
        WHEN MATCHED THEN
          UPDATE SET T.is_current = S.is_current, T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (id, name_he, name_en, short_name, is_current, created_at, updated_at)
          VALUES (GENERATE_UUID(), S.name_he, NULL, NULL, S.is_current, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP());
        """

        self._execute_merge_sql(merge_sql, entity_name="party")
        self._cleanup_staging_table(stg_name)
        logger.info("Successfully merged party records.")
        return len(records)

    def load_and_merge_mks(self, mk_rows: list[dict[str, Any]]) -> int:
        """Upload and MERGE MKs into mk table."""
        if not mk_rows:
            logger.info("No MK records to upload.")
            return 0

        stg_name = "stg_mk_temp"
        records = []
        for row in mk_rows:
            mk_id = row.get("id")
            if not mk_id:
                continue
            first_name = row.get("firstname", "") or ""
            last_name = row.get("lastname", "") or ""
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                continue

            records.append({
                "knesset_member_id": int(mk_id),
                "slug": slugify_hebrew_name(full_name, int(mk_id)),
                "full_name_he": full_name,
                "gender": row.get("genderdesc"),
                "is_current": bool(row.get("iscurrent", True)),
            })

        records = self._deduplicate_records(records, ["knesset_member_id"], "mk")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Prepared {len(records)} MK records for `{self.dataset_ref}.mk`.")
            if records:
                logger.info(f"[DRY-RUN] Sample MK record: {records[0]}")
            return len(records)

        logger.info(f"Staging {len(records)} MK records...")
        self._load_staging_data(stg_name, records)

        merge_sql = f"""
        MERGE `{self.dataset_ref}.mk` T
        USING (
          SELECT * FROM `{self.dataset_ref}.{stg_name}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY knesset_member_id ORDER BY is_current DESC) = 1
        ) S
        ON T.knesset_member_id = CAST(S.knesset_member_id AS INT64)
        WHEN MATCHED THEN
          UPDATE SET
            T.full_name_he = S.full_name_he,
            T.gender = S.gender,
            T.updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (id, knesset_member_id, slug, full_name_he, gender, is_current, created_at, updated_at)
          VALUES (GENERATE_UUID(), CAST(S.knesset_member_id AS INT64), S.slug, S.full_name_he, S.gender, FALSE, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP());
        """

        self._execute_merge_sql(merge_sql, entity_name="mk")
        self._cleanup_staging_table(stg_name)
        logger.info("Successfully merged MK records.")
        return len(records)

    def load_and_merge_mk_affiliations(self, affiliation_rows: list[dict[str, Any]]) -> int:
        """Upload and MERGE MK party affiliations into mk_affiliation table."""
        if not affiliation_rows:
            logger.info("No mk_affiliation records to upload.")
            return 0

        stg_name = "stg_mk_affiliation_temp"
        records = []
        for row in affiliation_rows:
            mk_id = row.get("knesset_member_id")
            party_name = row.get("party_name_he")
            if not mk_id or not party_name:
                continue

            is_current = bool(row.get("iscurrent", True))
            start_date = format_date_str(row.get("startdate"))
            end_date = None if is_current else format_date_str(row.get("finishdate"))

            records.append({
                "knesset_member_id": int(mk_id),
                "party_name_he": party_name.strip(),
                "start_date": start_date,
                "end_date": end_date,
            })

        records = self._deduplicate_records(records, ["knesset_member_id", "party_name_he"], "mk_affiliation")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Prepared {len(records)} mk_affiliation records for `{self.dataset_ref}.mk_affiliation`.")
            if records:
                logger.info(f"[DRY-RUN] Sample mk_affiliation record: {records[0]}")
            return len(records)

        logger.info(f"Staging {len(records)} mk_affiliation records...")
        self._load_staging_data(stg_name, records)

        merge_sql = f"""
        MERGE `{self.dataset_ref}.mk_affiliation` T
        USING (
          SELECT 
            m.id AS mk_id,
            p.id AS party_id,
            CAST(stg.start_date AS DATE) AS start_date,
            CAST(stg.end_date AS DATE) AS end_date
          FROM `{self.dataset_ref}.{stg_name}` stg
          JOIN `{self.dataset_ref}.mk` m ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
          JOIN (
            SELECT id, name_he
            FROM `{self.dataset_ref}.party`
            QUALIFY ROW_NUMBER() OVER (PARTITION BY name_he ORDER BY id) = 1
          ) p ON p.name_he = stg.party_name_he
          QUALIFY ROW_NUMBER() OVER (PARTITION BY m.id, p.id ORDER BY stg.start_date DESC) = 1
        ) S
        ON T.mk_id = S.mk_id AND T.party_id = S.party_id
        WHEN MATCHED THEN
          UPDATE SET T.start_date = S.start_date, T.end_date = S.end_date
        WHEN NOT MATCHED THEN
          INSERT (id, mk_id, party_id, start_date, end_date)
          VALUES (GENERATE_UUID(), S.mk_id, S.party_id, S.start_date, S.end_date);
        """

        self._execute_merge_sql(merge_sql, entity_name="mk_affiliation")
        self._cleanup_staging_table(stg_name)
        logger.info("Successfully merged mk_affiliation records.")
        return len(records)

    def load_and_merge_bills(
        self, bill_rows: list[dict[str, Any]], upload_docs: bool = True, gcp_bucket: str | None = None, max_workers: int = 16
    ) -> int:
        """Upload and MERGE proposed/enacted bills into bill table."""
        if not bill_rows:
            logger.info("No bill records to upload.")
            return 0

        bucket_name = gcp_bucket or f"{self.project_id}-knesset-documents"
        if upload_docs and not self.dry_run:
            ensure_gcp_bucket_exists(self.project_id, bucket_name, credentials_file=self.credentials_file)

        stg_name = "stg_bill_temp"
        raw_records = []
        for row in bill_rows:
            bill_id = row.get("knesset_bill_id")
            title_he = row.get("title_he")
            if not bill_id or not title_he:
                continue

            enacted_uri = row.get("enacted_law_document_uri")
            raw_records.append({
                "knesset_bill_id": int(bill_id),
                "title_he": title_he.strip(),
                "summary": row.get("summary"),
                "status": row.get("status"),
                "enacted_law_document_uri": enacted_uri.replace("\\", "/").strip() if enacted_uri else None,
            })

        # Deduplicate records by knesset_bill_id FIRST to avoid duplicate document tasks
        records = self._deduplicate_records(raw_records, ["knesset_bill_id"], "bill")

        doc_tasks = []
        for rec in records:
            if rec.get("enacted_law_document_uri") and upload_docs and not self.dry_run:
                doc_tasks.append((rec, f"bill_{rec['knesset_bill_id']}"))

        # Batch upload documents concurrently using ThreadPoolExecutor
        if doc_tasks:
            total_docs = len(doc_tasks)
            logger.info(f"⚡ Batch uploading {total_docs} bill documents using {max_workers} parallel threads...")
            completed_counter = 0
            successful_count = 0
            skipped_count = 0

            indexed_tasks = [(t[0], t[1], idx + 1, total_docs) for idx, t in enumerate(doc_tasks)]

            def _worker(task_info):
                rec_ref, prefix, doc_idx, total = task_info
                uri = rec_ref["enacted_law_document_uri"]
                new_uri, method, size = download_and_upload_to_gcp(
                    http_url=uri,
                    subfolder="enacted_laws",
                    file_prefix=prefix,
                    project_id=self.project_id,
                    bucket_name=bucket_name,
                    credentials_file=self.credentials_file,
                    doc_index=doc_idx,
                    total_docs=total,
                )
                return rec_ref, new_uri, method, size, doc_idx, total

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_worker, t) for t in indexed_tasks]
                for future in as_completed(futures):
                    rec_ref, new_uri, method, size, doc_idx, total = future.result()
                    rec_ref["enacted_law_document_uri"] = new_uri
                    completed_counter += 1
                    if new_uri and new_uri.startswith("gs://"):
                        successful_count += 1
                        logger.info(f"  • [{doc_idx}/{total}] Uploaded document (method={method}, size={size}B) -> {new_uri}")
                    else:
                        skipped_count += 1
                        logger.info(f"  • [{doc_idx}/{total}] Upload skipped (retained HTTP URL) -> {new_uri}")

            logger.info(f"✅ Finished uploading bill documents: {successful_count}/{total_docs} successful, {skipped_count}/{total_docs} retained HTTP URLs.")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Prepared {len(records)} bill records for `{self.dataset_ref}.bill`.")
            if records:
                logger.info(f"[DRY-RUN] Sample bill record: {records[0]}")
            return len(records)

        logger.info(f"Staging {len(records)} bill records...")
        self._load_staging_data(stg_name, records)

        merge_sql = f"""
        MERGE `{self.dataset_ref}.bill` T
        USING (
          SELECT * FROM `{self.dataset_ref}.{stg_name}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY knesset_bill_id ORDER BY knesset_bill_id DESC) = 1
        ) S
        ON T.knesset_bill_id = CAST(S.knesset_bill_id AS INT64)
        WHEN MATCHED THEN
          UPDATE SET
            T.title_he = S.title_he,
            T.summary = S.summary,
            T.status = S.status,
            T.enacted_law_document_uri = COALESCE(S.enacted_law_document_uri, T.enacted_law_document_uri)
        WHEN NOT MATCHED THEN
          INSERT (id, knesset_bill_id, title_he, title_en, summary, status, enacted_law_document_uri)
          VALUES (GENERATE_UUID(), CAST(S.knesset_bill_id AS INT64), S.title_he, NULL, S.summary, S.status, S.enacted_law_document_uri);
        """

        self._execute_merge_sql(merge_sql, entity_name="bill")
        self._cleanup_staging_table(stg_name)
        logger.info("Successfully merged bill records.")
        return len(records)

    def load_and_merge_vote_events(
        self, vote_events: list[dict[str, Any]], upload_docs: bool = True, gcp_bucket: str | None = None, max_workers: int = 16
    ) -> int:
        """Upload prerequisite vote_event rows required for FK references in mk_vote."""
        if not vote_events:
            return 0

        bucket_name = gcp_bucket or f"{self.project_id}-knesset-documents"
        if upload_docs and not self.dry_run:
            ensure_gcp_bucket_exists(self.project_id, bucket_name, credentials_file=self.credentials_file)

        stg_name = "stg_vote_event_temp"
        raw_records = []

        for ve in vote_events:
            vid = ve.get("id")
            if not vid:
                continue
            title = ve.get("votetitle") or ve.get("votesubject") or "הצבעת מליאה"
            voted_at = ve.get("votedatetime")
            draft_uri = ve.get("draft_document_uri")

            raw_records.append({
                "external_key": f"odata:vote:{vid}",
                "event_kind": "plenum",
                "title_he": title.strip(),
                "voted_at": voted_at,
                "draft_document_uri": draft_uri.replace("\\", "/").strip() if draft_uri else None,
            })

        # Deduplicate records by external_key FIRST to avoid duplicate document tasks
        records = self._deduplicate_records(raw_records, ["external_key"], "vote_event")

        doc_tasks = []
        for rec in records:
            if rec.get("draft_document_uri") and upload_docs and not self.dry_run:
                vid = rec["external_key"].replace("odata:vote:", "")
                doc_tasks.append((rec, f"vote_{vid}"))

        # Batch upload vote draft documents concurrently using ThreadPoolExecutor
        if doc_tasks:
            total_docs = len(doc_tasks)
            logger.info(f"⚡ Batch uploading {total_docs} vote draft documents using {max_workers} parallel threads...")
            completed_counter = 0
            successful_count = 0
            skipped_count = 0

            indexed_tasks = [(t[0], t[1], idx + 1, total_docs) for idx, t in enumerate(doc_tasks)]

            def _worker(task_info):
                rec_ref, prefix, doc_idx, total = task_info
                uri = rec_ref["draft_document_uri"]
                new_uri, method, size = download_and_upload_to_gcp(
                    http_url=uri,
                    subfolder="vote_drafts",
                    file_prefix=prefix,
                    project_id=self.project_id,
                    bucket_name=bucket_name,
                    credentials_file=self.credentials_file,
                    doc_index=doc_idx,
                    total_docs=total,
                )
                return rec_ref, new_uri, method, size, doc_idx, total

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_worker, t) for t in indexed_tasks]
                for future in as_completed(futures):
                    rec_ref, new_uri, method, size, doc_idx, total = future.result()
                    rec_ref["draft_document_uri"] = new_uri
                    completed_counter += 1
                    if new_uri and new_uri.startswith("gs://"):
                        successful_count += 1
                        logger.info(f"  • [{doc_idx}/{total}] Uploaded draft document (method={method}, size={size}B) -> {new_uri}")
                    else:
                        skipped_count += 1
                        logger.info(f"  • [{doc_idx}/{total}] Upload skipped (retained HTTP URL) -> {new_uri}")

            logger.info(f"✅ Finished uploading vote draft documents: {successful_count}/{total_docs} successful, {skipped_count}/{total_docs} retained HTTP URLs.")

        records = self._deduplicate_records(records, ["external_key"], "vote_event")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Prepared {len(records)} vote_event records for `{self.dataset_ref}.vote_event`.")
            if records:
                logger.info(f"[DRY-RUN] Sample vote_event record: {records[0]}")
            return len(records)

        logger.info(f"Staging {len(records)} vote_event records...")
        self._load_staging_data(stg_name, records)

        merge_sql = f"""
        MERGE `{self.dataset_ref}.vote_event` T
        USING (
          SELECT * FROM `{self.dataset_ref}.{stg_name}`
          QUALIFY ROW_NUMBER() OVER (PARTITION BY external_key ORDER BY external_key) = 1
        ) S
        ON T.external_key = S.external_key
        WHEN MATCHED THEN
          UPDATE SET 
            T.title_he = S.title_he,
            T.draft_document_uri = COALESCE(S.draft_document_uri, T.draft_document_uri)
        WHEN NOT MATCHED THEN
          INSERT (id, external_key, event_kind, title_he, draft_document_uri, voted_at)
          VALUES (GENERATE_UUID(), S.external_key, S.event_kind, S.title_he, S.draft_document_uri, CAST(S.voted_at AS TIMESTAMP));
        """

        self._execute_merge_sql(merge_sql, entity_name="vote_event")
        self._cleanup_staging_table(stg_name)
        logger.info("Successfully merged vote_event records.")
        return len(records)

    def load_and_merge_mk_votes(self, vote_results: list[dict[str, Any]]) -> int:
        """Upload and MERGE mk_vote records."""
        if not vote_results:
            logger.info("No mk_vote records to upload.")
            return 0

        stg_name = "stg_mk_vote_temp"
        records = []
        for vr in vote_results:
            mk_id = vr.get("knesset_member_id")
            vote_id = vr.get("voteid")
            if not mk_id or not vote_id:
                continue

            vote_val = map_vote_result(vr.get("resultcode"), vr.get("resultdesc"))
            records.append({
                "knesset_member_id": int(mk_id),
                "external_key": f"odata:vote:{vote_id}",
                "vote": vote_val,
            })

        records = self._deduplicate_records(records, ["knesset_member_id", "external_key"], "mk_vote")

        if self.dry_run:
            logger.info(f"[DRY-RUN] Prepared {len(records)} mk_vote records for `{self.dataset_ref}.mk_vote`.")
            if records:
                logger.info(f"[DRY-RUN] Sample mk_vote record: {records[0]}")
            return len(records)

        logger.info(f"Staging {len(records)} mk_vote records...")
        self._load_staging_data(stg_name, records)

        merge_sql = f"""
        BEGIN TRANSACTION;

        ASSERT (
          SELECT COUNT(*) = {len(records)}
          FROM `{self.dataset_ref}.{stg_name}`
        ) AS 'mk_vote staging row count changed before merge';

        ASSERT (
          SELECT COUNT(*) = {len(records)}
          FROM `{self.dataset_ref}.{stg_name}` stg
          JOIN `{self.dataset_ref}.mk` m
            ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
          JOIN `{self.dataset_ref}.vote_event` ve
            ON ve.external_key = stg.external_key
        ) AS 'Every staged vote must resolve to one MK and one vote event';

        MERGE `{self.dataset_ref}.mk_vote` T
        USING (
          SELECT 
            m.id AS mk_id,
            ve.id AS vote_event_id,
            stg.vote
          FROM `{self.dataset_ref}.{stg_name}` stg
          JOIN `{self.dataset_ref}.mk` m ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
          JOIN `{self.dataset_ref}.vote_event` ve ON ve.external_key = stg.external_key
          QUALIFY ROW_NUMBER() OVER (PARTITION BY m.id, ve.id ORDER BY stg.vote) = 1
        ) S
        ON T.mk_id = S.mk_id AND T.vote_event_id = S.vote_event_id
        WHEN MATCHED THEN
          UPDATE SET T.vote = S.vote
        WHEN NOT MATCHED THEN
          INSERT (id, mk_id, vote_event_id, vote)
          VALUES (GENERATE_UUID(), S.mk_id, S.vote_event_id, S.vote);

        ASSERT (
          SELECT COUNT(*) = {len(records)}
          FROM `{self.dataset_ref}.{stg_name}` stg
          JOIN `{self.dataset_ref}.mk` m
            ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
          JOIN `{self.dataset_ref}.vote_event` ve
            ON ve.external_key = stg.external_key
          JOIN `{self.dataset_ref}.mk_vote` target
            ON target.mk_id = m.id AND target.vote_event_id = ve.id
        ) AS 'Every staged vote must exist after merge';

        COMMIT TRANSACTION;
        """

        self._execute_merge_sql(merge_sql, entity_name="mk_vote")
        self._cleanup_staging_table(stg_name)
        logger.info("Successfully merged mk_vote records.")
        return len(records)


def ensure_gcp_bucket_exists(project_id: str, bucket_name: str, credentials_file: str | None = None):
    """Ensure GCP Storage bucket exists using pure Python google.cloud.storage SDK."""
    try:
        from google.cloud import storage
        if credentials_file:
            client = storage.Client.from_service_account_json(credentials_file, project=project_id)
        else:
            client = storage.Client(project=project_id)

        bucket = client.bucket(bucket_name)
        if bucket.exists():
            return
        
        logger.info(f"Creating GCP Storage bucket `gs://{bucket_name}` via Python Storage SDK...")
        client.create_bucket(bucket_name, location="US")
        logger.info(f"✅ Created GCP Storage bucket `gs://{bucket_name}`.")
    except Exception as e:
        logger.warning(f"Could not verify/create GCP Storage bucket `gs://{bucket_name}` via Python SDK: {e}")


# GCP Cloud Storage Document Layout & Database Mapping:
# - Enacted Law Documents:
#     Source: kns_documentbill.filepath where grouptypeid = 9 (חוק - פרסום ברשומות)
#     Storage Path: gs://<bucket_name>/enacted_laws/bill_<knesset_bill_id>.<ext>
#     Database Column: bill.enacted_law_document_uri
# - Plenum Vote Event Draft Documents:
#     Source: kns_documentbill.filepath where grouptypeid IN (1, 2, 4) (הצעת חוק)
#     Storage Path: gs://<bucket_name>/vote_drafts/vote_<vote_id>.<ext>
#     Database Column: vote_event.draft_document_uri
#
# Reblaze WAF Anti-Bot Bypass Architecture:
# - Knesset document servers (fs.knesset.gov.il) enforce Reblaze WAF bot protection. Standard Python urllib
#   TLS ClientHello fingerprints are flagged, returning 200 OK JS challenge pages (rbzns / kramericaindustries).
# - Primary Downloader: Uses system `curl` with Chrome browser headers and TLS emulation to bypass Reblaze WAF.
def download_and_upload_to_gcp(
    http_url: str,
    subfolder: str,
    file_prefix: str,
    project_id: str,
    bucket_name: str,
    credentials_file: str | None = None,
    doc_index: int | None = None,
    total_docs: int | None = None,
) -> tuple[str, str, int]:
    """
    Download document file from HTTP URL and upload to GCP Cloud Storage using Python SDK.
    Returns Tuple of (result_uri, download_method, file_size_bytes).
    """
    if not http_url:
        return (http_url, "none", 0)

    idx_prefix = f"[{doc_index}/{total_docs}] " if doc_index is not None and total_docs is not None else ""
    clean_url = http_url.replace("\\", "/").strip()
    ext = ".pdf" if ".pdf" in clean_url.lower() else ".docx" if ".docx" in clean_url.lower() else ".doc" if ".doc" in clean_url.lower() else ".file"
    file_name = f"{file_prefix}{ext}"
    gcs_blob_name = f"{subfolder}/{file_name}"
    gcs_path = f"gs://{bucket_name}/{gcs_blob_name}"

    tmp_path = None
    download_method = "none"
    download_success = False
    max_retries = 3

    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name

        curl_bin = shutil.which("curl")

        # 1. Primary Downloader: Try curl with retries (bypasses Reblaze WAF TLS fingerprinting)
        if curl_bin:
            curl_cmd = [
                curl_bin, "-s", "-L", "-k",
                "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "-e", "https://main.knesset.gov.il/",
                clean_url, "-o", tmp_path
            ]
            attempt = 1
            while True:
                res = subprocess.run(curl_cmd, capture_output=True, text=True)
                is_valid = res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0
                sample = b""
                if is_valid:
                    with open(tmp_path, "rb") as f:
                        sample = f.read(150)

                is_waf = b"rbzns" in sample or b"kramericaindustries" in sample or (sample.strip().startswith(b"<!DOCTYPE html") and ext != ".html")

                if is_valid and not is_waf:
                    download_success = True
                    download_method = "curl"
                    break

                # Single location retry check per downloader
                if attempt < max_retries:
                    logger.info(f"  • {idx_prefix}[Reblaze WAF Challenge] Anti-bot response triggered for {clean_url} (attempt {attempt}/{max_retries}, curl). Retrying in {attempt * 3}s...")
                    time.sleep(attempt * 3)
                else:
                    logger.info(f"  • {idx_prefix}Exceeded maximum retries ({max_retries}/{max_retries}) for {clean_url} (curl).")
                    break
                
                attempt += 1

        else:
            logger.warning(f"  • {idx_prefix}`curl` CLI tool not found. Falling back to Python `urllib` (may trigger Reblaze WAF challenges).")

        # 2. Fallback Downloader: urllib with Reblaze WAF challenge detection & retries
        if not download_success:
            ssl_ctx = ssl._create_unverified_context()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://main.knesset.gov.il/"
            }
            attempt = 1
            while True:
                content = b""
                download_err = None
                try:
                    req = urllib.request.Request(clean_url, headers=headers)
                    with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
                        content = resp.read()
                except Exception as e:
                    download_err = e

                is_waf = b"rbzns" in content or b"kramericaindustries" in content or (content.strip().startswith(b"<!DOCTYPE html") and ext != ".html")

                if not download_err and content and not is_waf:
                    with open(tmp_path, "wb") as f:
                        f.write(content)
                    download_success = True
                    download_method = "python_urllib"
                    break

                # Single location retry check per downloader
                if attempt < max_retries:
                    logger.info(f"  • {idx_prefix}[Reblaze WAF Challenge] Anti-bot response triggered for {clean_url} (attempt {attempt}/{max_retries}, urllib). Retrying in {attempt * 3}s...")
                    time.sleep(attempt * 3)
                else:
                    logger.error(f"  • {idx_prefix}❌ Exceeded maximum retries ({max_retries}/{max_retries}) for {clean_url} (urllib).")
                    break
                
                attempt += 1

        if not download_success or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            # Give up and keep the original url
            return (clean_url, download_method, 0)

        file_size = os.path.getsize(tmp_path)
        from google.cloud import storage
        if credentials_file:
            storage_client = storage.Client.from_service_account_json(credentials_file, project=project_id)
        else:
            storage_client = storage.Client(project=project_id)

        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_blob_name)
        blob.upload_from_filename(tmp_path)
        return (gcs_path, download_method, file_size)

    except Exception as e:
        logger.warning(f"  • Could not download/upload document {clean_url}: {e}")
        return (clean_url, download_method, 0)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def fetch_over_parties(active_only: bool = True) -> list[dict[str, Any]]:
    """Fetch political factions/parties from Over API."""
    logger.info(f"Fetching parties (factions) from Over API (active_only={active_only})...")
    where_active = " AND iscurrent = true" if active_only else ""
    sql = f"SELECT DISTINCT name, iscurrent FROM kns_faction WHERE name IS NOT NULL AND TRIM(name) != ''{where_active};"
    res = run_over_sql(sql)
    return res.get("rows", [])


def fetch_over_mks(limit: int | None = None, active_only: bool = True) -> list[dict[str, Any]]:
    """Fetch person identities from Over API.

    ``kns_person.iscurrent`` describes a current person record, not reliably a
    currently serving MK. The loader therefore never uses it as the authority
    for ``mk.is_current``; the curated 120-member roster owns that field.
    """
    logger.info(f"Fetching MKs from Over API (active_only={active_only}, limit={limit})...")
    where_active = " WHERE iscurrent = true" if active_only else ""
    sql = f"SELECT id, firstname, lastname, genderdesc, email, iscurrent FROM kns_person{where_active} ORDER BY lastname, firstname;"
    return fetch_all_over_sql(sql, user_limit=limit)


def fetch_over_affiliations(active_only: bool = True) -> list[dict[str, Any]]:
    """Fetch MK party memberships from Over API kns_persontoposition table."""
    logger.info(f"Fetching MK party affiliations from Over API (active_only={active_only})...")
    if active_only:
        sql = """
        SELECT DISTINCT
            pos.personid AS knesset_member_id,
            pos.factionname AS party_name_he,
            pos.startdate,
            pos.finishdate,
            pos.iscurrent
        FROM kns_persontoposition pos
        JOIN kns_person p ON pos.personid = p.id
        WHERE pos.personid IS NOT NULL 
          AND pos.factionname IS NOT NULL 
          AND TRIM(pos.factionname) != ''
          AND p.iscurrent = true
          AND pos.iscurrent = true
        ORDER BY pos.startdate DESC;
        """
    else:
        sql = """
        SELECT 
            pos.personid AS knesset_member_id,
            pos.factionname AS party_name_he,
            pos.startdate,
            pos.finishdate,
            pos.iscurrent
        FROM kns_persontoposition pos
        WHERE pos.personid IS NOT NULL 
          AND pos.factionname IS NOT NULL 
          AND TRIM(pos.factionname) != ''
        ORDER BY pos.startdate DESC;
        """
    return fetch_all_over_sql(sql)


def fetch_over_bills(
    limit: int | None = None, active_only: bool = True, start_date: str | None = None
) -> list[dict[str, Any]]:
    """
    Fetch bills from Over API kns_bill joined with kns_documentbill filepath for enacted laws (grouptypeid=9).
    
    NOTE ON DISTINCT ON (b.id):
    Joining kns_bill with kns_billinitiator (to filter by active MKs) and kns_documentbill
    produces duplicate rows per bill because:
    1) A single bill often has multiple co-sponsoring MK initiators (5–15 per bill).
    2) A bill may have multiple document formats attached in kns_documentbill.
    Using 'DISTINCT ON (b.id)' ensures PostgreSQL returns exactly 1 unique bill row,
    selecting the newest primary document (ORDER BY b.id DESC, d.lastupdateddate DESC).
    """
    logger.info(f"Fetching bills from Over API (active_only={active_only}, limit={limit}, start_date={start_date})...")
    date_clause = f" AND (b.lastupdateddate >= '{start_date}' OR b.publicationdate >= '{start_date}')" if start_date else ""

    if active_only:
        sql = f"""
        SELECT DISTINCT ON (b.id)
            b.id AS knesset_bill_id,
            b.name AS title_he,
            b.summarylaw AS summary,
            b.typedesc AS status,
            d.filepath AS enacted_law_document_uri
        FROM kns_bill b
        JOIN kns_billinitiator bi ON b.id = bi.billid
        JOIN kns_person p ON bi.personid = p.id
        LEFT JOIN kns_documentbill d ON b.id = d.billid AND d.grouptypeid = 9
        WHERE b.id IS NOT NULL 
          AND b.name IS NOT NULL 
          AND TRIM(b.name) != ''
          AND p.iscurrent = true{date_clause}
        ORDER BY b.id DESC, d.lastupdateddate DESC;
        """
    else:
        sql = f"""
        SELECT DISTINCT ON (b.id)
            b.id AS knesset_bill_id,
            b.name AS title_he,
            b.summarylaw AS summary,
            b.typedesc AS status,
            d.filepath AS enacted_law_document_uri
        FROM kns_bill b
        LEFT JOIN kns_documentbill d ON b.id = d.billid AND d.grouptypeid = 9
        WHERE b.id IS NOT NULL 
          AND b.name IS NOT NULL 
          AND TRIM(b.name) != ''{date_clause}
        ORDER BY b.id DESC, d.lastupdateddate DESC;
        """
    return fetch_all_over_sql(sql, user_limit=limit)


def fetch_over_vote_events(
    limit: int | None = None, start_date: str | None = None
) -> list[dict[str, Any]]:
    """
    Fetch vote events from Over API kns_plenumvote joined with kns_documentbill filepath for draft documents.
    
    NOTE ON DISTINCT ON (v.id):
    Joining kns_plenumvote (via v.itemid = d.billid) with kns_documentbill produces duplicate rows
    per vote event because a single bill often has multiple draft document files attached
    (e.g., .pdf, .docx, and .tif versions of the same draft).
    Using 'DISTINCT ON (v.id)' ensures PostgreSQL returns exactly 1 unique vote event row,
    selecting the newest primary draft document (ORDER BY v.id DESC, d.lastupdateddate DESC).
    """
    logger.info(f"Fetching vote events from Over API (limit={limit}, start_date={start_date})...")
    date_clause = f" AND v.votedatetime >= '{start_date}'" if start_date else ""
    sql = f"""
    SELECT DISTINCT ON (v.id)
        v.id, 
        v.votedatetime, 
        v.votetitle, 
        v.votesubject,
        d.filepath AS draft_document_uri
    FROM kns_plenumvote v
    LEFT JOIN kns_documentbill d ON v.itemid = d.billid AND d.grouptypeid IN (1, 2, 4)
    WHERE v.id IS NOT NULL{date_clause}
    ORDER BY v.id DESC, d.lastupdateddate DESC;
    """
    return fetch_all_over_sql(sql, user_limit=limit)


def fetch_over_vote_results(
    vote_mkids: list[int],
    limit: int | None = None,
    start_date: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch vote results for reviewed source IDs without joining kns_person."""
    if not vote_mkids:
        raise ValueError("vote_mkids cannot be empty")
    logger.info(
        "Fetching MK vote results from Over API "
        f"(reviewed_mkids={len(vote_mkids)}, limit={limit}, start_date={start_date})..."
    )
    date_join = " JOIN kns_plenumvote v ON vr.voteid = v.id" if start_date else ""
    date_clause = f" AND v.votedatetime >= '{start_date}'" if start_date else ""
    source_ids = ",".join(str(int(mkid)) for mkid in sorted(set(vote_mkids)))
    sql = f"""
    SELECT vr.mkid, vr.voteid, vr.resultcode, vr.resultdesc
    FROM kns_plenumvoteresult vr{date_join}
    WHERE vr.mkid IN ({source_ids})
      AND vr.voteid IS NOT NULL{date_clause}
    ORDER BY vr.id DESC;
    """
    return fetch_all_over_sql(
        sql,
        user_limit=limit,
        progress_label="Vote-result download",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Upload MKs, Parties, Affiliations, Bills, and MK Votes from Over API to BigQuery."
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP Project ID (default: $GOOGLE_CLOUD_PROJECT env var)",
    )
    parser.add_argument(
        "--dataset-id",
        type=str,
        default="mk_tracking",
        help="BigQuery Dataset ID (default: mk_tracking)",
    )
    parser.add_argument(
        "--credentials-file",
        "-c",
        type=str,
        default=None,
        help="Path to GCP Service Account JSON key file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a dry run: fetch and prepare records without modifying BigQuery",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include historical/inactive Members of Knesset, parties, and bills (default: active only)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Filter bills, vote events, and votes from this date onward (e.g. 2024-01-01)",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=["mk", "party", "mk_affiliation", "bill", "mk_vote", "all"],
        default=["all"],
        help="Tables to sync (default: all)",
    )
    parser.add_argument(
        "--gcp-bucket",
        type=str,
        default=None,
        help="Target GCP Cloud Storage bucket name for document files (default: <project-id>-knesset-documents)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Number of parallel worker threads for batch document uploading (default: 16)",
    )
    parser.add_argument(
        "--limit-mks",
        type=int,
        default=None,
        help="Limit number of MKs fetched from Over API",
    )
    parser.add_argument(
        "--limit-bills",
        type=int,
        default=None,
        help="Limit number of bills fetched from Over API",
    )
    parser.add_argument(
        "--limit-votes",
        type=int,
        default=None,
        help="Limit number of vote results fetched from Over API",
    )
    parser.add_argument(
        "--vote-crosswalk",
        type=Path,
        default=DEFAULT_VOTE_CROSSWALK,
        help="Reviewed vote-mkid crosswalk JSON",
    )

    args = parser.parse_args()

    # Active only is default (True unless --include-inactive is specified)
    active_only = not args.include_inactive

    sync_all = "all" in args.tables
    sync_party = sync_all or "party" in args.tables
    sync_mk = sync_all or "mk" in args.tables
    sync_affiliation = sync_all or "mk_affiliation" in args.tables
    sync_bill = sync_all or "bill" in args.tables
    sync_mk_vote = sync_all or "mk_vote" in args.tables

    logger.info(
        f"Starting upload to BigQuery target `{args.project_id}.{args.dataset_id}` "
        f"(active_only={active_only}, include_inactive={args.include_inactive}, dry_run={args.dry_run}, start_date={args.start_date}, max_workers={args.max_workers})"
    )
    uploader = BigQueryUploader(
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        credentials_file=args.credentials_file,
        dry_run=args.dry_run,
    )

    # 1. Party sync
    if sync_party:
        parties = fetch_over_parties(active_only=active_only)
        uploader.load_and_merge_parties(parties)

    # 2. MK sync
    if sync_mk:
        mks = fetch_over_mks(limit=args.limit_mks, active_only=active_only)
        uploader.load_and_merge_mks(mks)

    # 3. MK Affiliation sync (requires party and mk prerequisites)
    if sync_affiliation:
        if not sync_party:
            parties = fetch_over_parties(active_only=active_only)
            uploader.load_and_merge_parties(parties)
        if not sync_mk:
            mks = fetch_over_mks(limit=args.limit_mks, active_only=active_only)
            uploader.load_and_merge_mks(mks)

        affiliations = fetch_over_affiliations(active_only=active_only)
        uploader.load_and_merge_mk_affiliations(affiliations)

    # 4. Bill sync
    if sync_bill:
        bills = fetch_over_bills(limit=args.limit_bills, active_only=active_only, start_date=args.start_date)
        uploader.load_and_merge_bills(bills, upload_docs=True, gcp_bucket=args.gcp_bucket, max_workers=args.max_workers)

    # 5. MK Vote sync (requires vote_event prerequisite)
    if sync_mk_vote:
        if not sync_mk:
            mks = fetch_over_mks(limit=args.limit_mks, active_only=active_only)
            uploader.load_and_merge_mks(mks)

        vote_events = fetch_over_vote_events(limit=args.limit_votes, start_date=args.start_date)
        uploader.load_and_merge_vote_events(vote_events, upload_docs=True, gcp_bucket=args.gcp_bucket, max_workers=args.max_workers)

        vote_crosswalk = load_crosswalk(args.vote_crosswalk)
        vote_results = fetch_over_vote_results(
            list(vote_crosswalk),
            limit=args.limit_votes,
            start_date=args.start_date,
        )
        resolved_vote_results = resolve_vote_results(vote_results, vote_crosswalk)
        if len(resolved_vote_results) != len(vote_results):
            raise RuntimeError("vote identity resolution changed the result count")
        logger.info(
            "Resolved all %s vote results through the reviewed crosswalk.",
            len(resolved_vote_results),
        )
        uploader.load_and_merge_mk_votes(resolved_vote_results)

    if args.dry_run:
        logger.info("🔍 Dry run complete. Zero rows written to BigQuery.")
    else:
        logger.info("🎉 BigQuery ingestion completed successfully.")


if __name__ == "__main__":
    main()
