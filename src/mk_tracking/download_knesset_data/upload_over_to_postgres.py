#!/usr/bin/env python3
"""
Upload Over Knesset Vote Data to PostgreSQL
===========================================
Ingests the public Knesset vote record into PostgreSQL. It queries vote events and
MK voting records from the Over Knesset API (https://www.over.org.il/api/knesset-db/sql)
and upserts them into the PostgreSQL `mk_tracking` schema defined by `db/schema.sql`.

`fetch_over_vote_events`, `fetch_over_vote_results` and `map_vote_result` come from
`over_api`, and the reviewed source-id crosswalk from `vote_identity`. Upserts run as
`INSERT ... ON CONFLICT DO UPDATE` against the real UNIQUE constraints
(`vote_event.external_key`, `mk_vote (mk_id, vote_event_id)`).

Scope:
  Tables populated: vote_event, mk_vote.
  `party`, `mk`, `bill` and `mk_affiliation` are already loaded in PostgreSQL from a
  separate export and are deliberately NOT re-synced here. Adding one later means a
  fetch function in `over_api` plus one more `load_and_upsert_*` method in
  PostgresUploader — every method follows the same stage-then-upsert shape.

No Google Cloud:
  There is no GCS document upload. `draft_document_uri` keeps
  whatever HTTP URL the Over API returned (backslashes normalized to forward slashes).
  Object storage is out of scope for the PostgreSQL migration.

Idempotency & Internal Foreign Key Resolution:
  - Re-run Safety: fully idempotent and safe to execute repeatedly (e.g. via cron).
    Rows are matched on their natural keys — `external_key` for vote_event,
    `(knesset_member_id, external_key)` for mk_vote — and updated in place.
  - Foreign Key Resolution: internal UUIDs (`bill.id`, `mk.id`, `vote_event.id`) are
    resolved inside PostgreSQL by joining staging tables on natural keys during the
    upsert. The script never invents or hardcodes internal UUID foreign keys in Python.
  - Whole Run Atomicity: everything happens in one transaction. `--dry-run` performs the
    identical work — staging, joins, diagnostics — and then rolls it back, so a dry run
    reports real numbers while writing nothing.

Dead Vote Rows:
  The pre-migration export lost `vote_event` entirely, leaving `mk_vote` rows pointing at
  event UUIDs that exist nowhere. Once the vote events are loaded, this script deletes
  exactly those orphans — `WHERE NOT EXISTS (... vote_event ...)`, never a blanket
  TRUNCATE — and reports the count. See `db/DATA_QUALITY.md` and
  `db/pending_constraints.sql` (3), the foreign key this unblocks.

Usage Options:
  --database-url     PostgreSQL connection URI (default: $DATABASE_URL)
  --dry-run          Fetch, stage and diagnose without committing anything
  --start-date       Filter vote events and votes from this timestamp onward (e.g. 2026-01-01)
  --limit-votes      Limit rows fetched from the Over API (applies to events and results)
  --vote-crosswalk   Reviewed vote-mkid crosswalk JSON

Examples:
  python upload_over_to_postgres.py
  python upload_over_to_postgres.py --dry-run --limit-votes 300
  python upload_over_to_postgres.py --start-date 2026-01-01
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import psycopg

from mk_tracking.db_config import ConfigError, resolve_dsn, resolve_schema
from mk_tracking.download_knesset_data.over_api import (
    fetch_over_vote_events,
    fetch_over_vote_results,
    map_vote_result,
)
from mk_tracking.download_knesset_data.vote_identity import (
    load_crosswalk,
    resolve_vote_results,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VOTE_CROSSWALK = PROJECT_ROOT / "db" / "vote_mkid_crosswalk.json"
DEFAULT_SCHEMA = "mk_tracking"
STAGING_PROGRESS_EVERY = 100_000

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("over_pg_uploader")


class PostgresUploader:
    """Handles staged loading and upsert operations into PostgreSQL via psycopg 3.

    Every entity follows the same two steps:
      1. COPY the source rows, carrying their natural keys, into an UNLOGGED staging
         table (fast, crash-unsafe, dropped at the end — exactly what staging wants).
      2. `INSERT ... SELECT` out of staging, JOINing the real tables to resolve UUID
         foreign keys, with `ON CONFLICT (<natural key>) DO UPDATE` doing the merge.
    """

    def __init__(
        self,
        connection: psycopg.Connection,
        schema: str = DEFAULT_SCHEMA,
        dry_run: bool = False,
    ):
        self.conn = connection
        self.schema = schema
        self.dry_run = dry_run
        self.conn.execute(f"SET search_path TO {schema}, public")

    # -- staging helpers ----------------------------------------------------

    def _create_staging_table(self, stg_name: str, columns_ddl: str) -> None:
        """(Re)create an UNLOGGED staging table; UNLOGGED skips WAL for throughput."""
        with self.conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.schema}.{stg_name}")
            cur.execute(
                f"CREATE UNLOGGED TABLE {self.schema}.{stg_name} ({columns_ddl})"
            )

    def _load_staging_data(
        self, stg_name: str, columns: list[str], records: list[dict[str, Any]]
    ) -> int:
        """COPY records into the staging table and ANALYZE it for the upsert planner."""
        column_list = ", ".join(columns)
        copy_sql = f"COPY {self.schema}.{stg_name} ({column_list}) FROM STDIN"
        staged = 0
        with self.conn.cursor() as cur:
            with cur.copy(copy_sql) as copy:
                for record in records:
                    copy.write_row([record[column] for column in columns])
                    staged += 1
                    if staged % STAGING_PROGRESS_EVERY == 0:
                        logger.info(
                            "%s: staged %s rows", stg_name, f"{staged:,}"
                        )
            cur.execute(f"ANALYZE {self.schema}.{stg_name}")
        return staged

    def _cleanup_staging_table(self, stg_name: str) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {self.schema}.{stg_name}")

    def finish(self) -> None:
        """Commit the whole run, or roll it back entirely on a dry run."""
        if self.dry_run:
            self.conn.rollback()
            logger.info("🔍 Dry run complete. Transaction rolled back, zero rows written.")
        else:
            self.conn.commit()
            logger.info("🎉 PostgreSQL ingestion committed successfully.")

    # -- vote_event ---------------------------------------------------------

    def load_and_upsert_vote_events(self, vote_events: list[dict[str, Any]]) -> int:
        """Upsert vote_event rows, the prerequisite for every mk_vote foreign key.

        `bill_id` is resolved by LEFT JOINing `bill` on the fetched `knesset_bill_id`, so
        a vote whose bill is not in the table still loads with a NULL `bill_id`; the
        `COALESCE(EXCLUDED.bill_id, vote_event.bill_id)` guard keeps a previously matched
        bill when a later run fails to match. That linkage is what feeds
        `v_vote_event_issues` through `bill_issue`.
        """
        if not vote_events:
            logger.info("No vote_event records to upload.")
            return 0

        stg_name = "stg_vote_event_temp"
        records = []
        for ve in vote_events:
            vid = ve.get("id")
            if not vid:
                continue
            title = ve.get("votetitle") or ve.get("votesubject") or "הצבעת מליאה"
            occurred_at = str(ve.get("votedatetime") or "").strip() or None
            draft_uri = ve.get("draft_document_uri")
            knesset_bill_id = ve.get("knesset_bill_id")

            records.append({
                "external_key": f"odata:vote:{vid}",
                "event_kind": "plenum",
                "title_he": title.strip(),
                "occurred_at": occurred_at,
                # Keep the HTTP URL the API returned; only fix its Windows separators.
                "draft_document_uri": draft_uri.replace("\\", "/").strip() if draft_uri else None,
                "knesset_bill_id": int(knesset_bill_id) if knesset_bill_id else None,
            })

        columns = [
            "external_key", "event_kind", "title_he", "occurred_at",
            "draft_document_uri", "knesset_bill_id",
        ]
        self._create_staging_table(
            stg_name,
            """
            external_key       text,
            event_kind         text,
            title_he           text,
            occurred_at        timestamptz,
            draft_document_uri text,
            knesset_bill_id    integer
            """,
        )
        logger.info(f"Staging {len(records)} vote_event records...")
        self._load_staging_data(stg_name, columns, records)

        # DISTINCT ON collapses duplicate external_keys inside the batch; without it a
        # repeated key makes ON CONFLICT DO UPDATE hit the same row twice and abort.
        # `xmax = 0` on the returned row distinguishes a fresh insert from an update;
        # the counting stays in SQL so a 900k-row batch never lands in Python memory.
        upsert_sql = f"""
        WITH upserted AS (
            INSERT INTO {self.schema}.vote_event
                (external_key, event_kind, title_he, occurred_at, draft_document_uri, bill_id)
            SELECT DISTINCT ON (stg.external_key)
                stg.external_key,
                stg.event_kind::event_kind,
                stg.title_he,
                stg.occurred_at,
                stg.draft_document_uri,
                b.id AS bill_id
            FROM {self.schema}.{stg_name} stg
            LEFT JOIN {self.schema}.bill b ON b.knesset_bill_id = stg.knesset_bill_id
            ORDER BY stg.external_key, b.id
            ON CONFLICT (external_key) DO UPDATE SET
                title_he           = EXCLUDED.title_he,
                occurred_at        = COALESCE(EXCLUDED.occurred_at, vote_event.occurred_at),
                draft_document_uri = COALESCE(EXCLUDED.draft_document_uri, vote_event.draft_document_uri),
                bill_id            = COALESCE(EXCLUDED.bill_id, vote_event.bill_id)
            RETURNING (xmax = 0) AS inserted, bill_id
        )
        SELECT count(*), count(*) FILTER (WHERE inserted),
               count(*) FILTER (WHERE bill_id IS NOT NULL)
        FROM upserted
        """
        with self.conn.cursor() as cur:
            cur.execute(upsert_sql)
            affected, inserted, linked = cur.fetchone()

        self._cleanup_staging_table(stg_name)
        logger.info(
            f"Successfully upserted vote_event records: {affected:,} affected "
            f"({inserted:,} inserted, {affected - inserted:,} updated), "
            f"{linked:,} carry a resolved bill_id."
        )
        return affected

    # -- mk_vote ------------------------------------------------------------

    def delete_orphan_mk_votes(self) -> int:
        """Delete mk_vote rows whose vote_event_id matches no vote_event.

        The pre-migration export lost vote_event, so every legacy vote row points at a
        UUID that no longer exists anywhere. Only unmatched rows are removed — rows that
        resolve against the freshly loaded events are left untouched.
        """
        with self.conn.cursor() as cur:
            cur.execute(f"""
                DELETE FROM {self.schema}.mk_vote v
                WHERE NOT EXISTS (
                    SELECT 1 FROM {self.schema}.vote_event e WHERE e.id = v.vote_event_id
                )
            """)
            removed = cur.rowcount
        if removed:
            logger.info(
                f"🧹 Removed {removed:,} orphaned mk_vote rows whose vote_event no longer exists."
            )
        else:
            logger.info("No orphaned mk_vote rows to remove.")
        return removed

    def load_and_upsert_mk_votes(self, vote_results: list[dict[str, Any]]) -> int:
        """Upsert mk_vote rows, resolving both foreign keys by natural key in SQL."""
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

            records.append({
                "knesset_member_id": int(mk_id),
                "external_key": f"odata:vote:{vote_id}",
                "vote": map_vote_result(vr.get("resultcode"), vr.get("resultdesc")),
            })

        columns = ["knesset_member_id", "external_key", "vote"]
        self._create_staging_table(
            stg_name,
            """
            knesset_member_id integer,
            external_key      text,
            vote              text
            """,
        )
        logger.info(f"Staging {len(records)} mk_vote records...")
        self._load_staging_data(stg_name, columns, records)

        # Report what cannot resolve before dropping it. Unresolved events are expected
        # under --limit-votes (the two fetches are limited independently) but signal a
        # partial vote_event load on a full run.
        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    count(*) FILTER (WHERE m.id IS NULL)  AS unknown_mk,
                    count(*) FILTER (WHERE ve.id IS NULL) AS unknown_vote_event
                FROM {self.schema}.{stg_name} stg
                LEFT JOIN {self.schema}.mk m ON m.knesset_member_id = stg.knesset_member_id
                LEFT JOIN {self.schema}.vote_event ve ON ve.external_key = stg.external_key
            """)
            unknown_mk, unknown_event = cur.fetchone()
        if unknown_mk or unknown_event:
            logger.warning(
                f"⚠️ Skipping unresolvable staged votes: {unknown_mk:,} with no matching mk, "
                f"{unknown_event:,} with no matching vote_event."
            )

        upsert_sql = f"""
        WITH upserted AS (
            INSERT INTO {self.schema}.mk_vote (mk_id, vote_event_id, vote)
            SELECT DISTINCT ON (m.id, ve.id)
                m.id AS mk_id,
                ve.id AS vote_event_id,
                stg.vote::vote_value
            FROM {self.schema}.{stg_name} stg
            JOIN {self.schema}.mk m ON m.knesset_member_id = stg.knesset_member_id
            JOIN {self.schema}.vote_event ve ON ve.external_key = stg.external_key
            ORDER BY m.id, ve.id, stg.vote
            ON CONFLICT (mk_id, vote_event_id) DO UPDATE SET vote = EXCLUDED.vote
            RETURNING (xmax = 0) AS inserted
        )
        SELECT count(*), count(*) FILTER (WHERE inserted) FROM upserted
        """
        with self.conn.cursor() as cur:
            cur.execute(upsert_sql)
            affected, inserted = cur.fetchone()

        self._cleanup_staging_table(stg_name)
        logger.info(
            f"Successfully upserted mk_vote records: {affected:,} affected "
            f"({inserted:,} inserted, {affected - inserted:,} updated)."
        )
        return affected


def report_vote_coverage(connection: psycopg.Connection, schema: str, label: str) -> None:
    """Log the row counts the vote chips in the UI actually depend on."""
    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT
                (SELECT count(*) FROM {schema}.vote_event),
                (SELECT count(*) FROM {schema}.vote_event WHERE bill_id IS NOT NULL),
                (SELECT count(*) FROM {schema}.mk_vote),
                (SELECT count(*) FROM {schema}.v_vote_event_issues)
        """)
        events, linked, votes, issues = cur.fetchone()
    logger.info(
        f"[{label}] vote_event={events:,} (bill_id set on {linked:,}), "
        f"mk_vote={votes:,}, v_vote_event_issues={issues:,}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload Vote Events and MK Votes from the Over API into PostgreSQL."
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="PostgreSQL connection URI. Falls back to $DATABASE_URL, then the PG* "
             "variables. Prefer the environment: an argument here puts the password "
             "in `ps` output.",
    )
    parser.add_argument(
        "--schema",
        type=str,
        default=None,
        help=f"PostgreSQL schema holding the tables. Falls back to "
             f"$MK_TRACKING_SCHEMA, then {DEFAULT_SCHEMA}.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform a dry run: stage and diagnose, then roll back without writing",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Filter vote events and votes from this date onward (e.g. 2024-01-01)",
    )
    parser.add_argument(
        "--limit-votes",
        type=int,
        default=None,
        help="Limit number of vote events and vote results fetched from Over API",
    )
    parser.add_argument(
        "--vote-crosswalk",
        type=Path,
        default=DEFAULT_VOTE_CROSSWALK,
        help="Reviewed vote-mkid crosswalk JSON",
    )

    args = parser.parse_args()

    try:
        database_url = resolve_dsn(args.database_url)
        schema = resolve_schema(args.schema)
    except ConfigError as error:
        parser.error(str(error))

    logger.info(
        f"Starting upload to PostgreSQL schema `{schema}` "
        f"(dry_run={args.dry_run}, start_date={args.start_date}, limit_votes={args.limit_votes})"
    )

    # 1. Fetch first: no connection is held open across ~900 paginated API requests.
    vote_events = fetch_over_vote_events(
        limit=args.limit_votes, start_date=args.start_date
    )
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

    # delete_orphan_mk_votes() below removes every mk_vote row whose event was not
    # in this fetch. That is correct when the fetch succeeded and destructive when
    # it did not: an empty result would delete the entire table and commit. The API
    # layer now raises rather than returning empty, so reaching here with nothing is
    # not expected — but the cost of being wrong is the whole vote record, so refuse
    # explicitly rather than relying on that.
    if not vote_events:
        logger.error(
            "the Over API returned no vote events. Refusing to continue: the "
            "orphan cleanup would delete every existing mk_vote row. Nothing was "
            "written. Re-run when the API is reachable."
        )
        return 1
    if not resolved_vote_results:
        logger.error(
            "the Over API returned no vote results for the %d events fetched. "
            "Refusing to continue for the same reason. Nothing was written.",
            len(vote_events),
        )
        return 1

    with psycopg.connect(database_url) as connection:
        uploader = PostgresUploader(
            connection, schema=schema, dry_run=args.dry_run
        )
        report_vote_coverage(connection, schema, "before")

        # 2. Vote events, the prerequisite for every mk_vote foreign key.
        uploader.load_and_upsert_vote_events(vote_events)

        # 3. Drop the legacy vote rows the lost vote_event export left dangling, then
        #    load the current ones. Order matters: the events must exist first, or every
        #    row that should survive would look orphaned.
        uploader.delete_orphan_mk_votes()
        uploader.load_and_upsert_mk_votes(resolved_vote_results)

        report_vote_coverage(connection, schema, "after")
        uploader.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
