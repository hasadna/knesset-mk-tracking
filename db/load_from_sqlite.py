#!/usr/bin/env python3
"""Load the SQLite export of the BigQuery dataset into PostgreSQL.

    DATABASE_URL=... uv run python db/load_from_sqlite.py mk_tracking.db

Apply db/schema.sql first; this script only moves data. It is idempotent at the
table level (each table is truncated before load) and safe to re-run.

The export loses all type fidelity — SQLite stores everything as TEXT, INTEGER
or REAL, and `embedding` is declared REAL but actually holds a JSON string. Every
column is therefore converted explicitly rather than copied. See db/DATA_QUALITY.md
for the measured state of the source data.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from typing import Any, Callable

import psycopg

from mk_tracking.db_config import ConfigError, describe, resolve_dsn, resolve_schema

EMBEDDING_DIMS = 3072

# Load order respects foreign keys. The six `v_*` objects in the export are
# flattened views and are deliberately not loaded — schema.sql recreates them.
LOAD_ORDER = [
    "party", "committee", "issue", "issue_anchor",
    "mk", "mk_affiliation", "mk_role", "mk_social_account",
    "bill", "vote_event", "bill_issue", "vote_event_issue", "bill_author",
    "summary_generation_run", "social_post", "post_issue", "tweet_cluster",
    "mk_issue_summary", "mk_issue_summary_supporting_post",
    "mk_vote", "mk_relation",
]

BOOL_COLUMNS = {"is_current", "is_active", "is_deleted", "is_garbage",
                "is_concrete_promise", "verified"}
JSON_COLUMNS = {"engagement", "rating_scale"}
VECTOR_COLUMNS = {"embedding", "centroid"}
DATE_COLUMNS = {"birth_date", "start_date", "end_date"}
UUID_COLUMNS = {"id", "mk_id", "account_id", "issue_id", "post_id", "bill_id",
                "party_id", "committee_id", "related_mk_id", "vote_event_id",
                "generation_run_id", "summary_id"}
UUID_PATTERN = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                          r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
TIMESTAMP_COLUMNS = {"created_at", "updated_at", "posted_at", "fetched_at",
                     "started_at", "completed_at", "occurred_at"}


def to_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def to_vector(value: Any) -> list[float] | None:
    """Parse a JSON array string into a float list. '[]' becomes NULL."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if not text or text == "[]":
        return None
    parsed = json.loads(text)
    if len(parsed) != EMBEDDING_DIMS:
        raise ValueError(f"expected {EMBEDDING_DIMS} dimensions, got {len(parsed)}")
    return [float(component) for component in parsed]


def to_json(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else json.dumps(value)


def passthrough_blank_as_null(value: Any) -> Any:
    return None if value == "" else value


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and UUID_PATTERN.match(value) is not None


def converter_for(column: str) -> Callable[[Any], Any]:
    if column in VECTOR_COLUMNS:
        return to_vector
    if column in BOOL_COLUMNS:
        return to_bool
    if column in JSON_COLUMNS:
        return to_json
    if column in UUID_COLUMNS:
        # BigQuery stored these as STRING, so platform-native ids leaked in where
        # a resolved UUID belongs (the failure docs/INGESTION_CONTRACT.md calls
        # Rule 1). Anything not UUID-shaped becomes NULL; social_post.mk_id is
        # repaired by handle lookup first, in repair_social_post_rows().
        return lambda value: value if is_uuid(value) else None
    return passthrough_blank_as_null


def sqlite_columns(source: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]


def sqlite_has_table(source: sqlite3.Connection, table: str) -> bool:
    found = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return found is not None


def select_sql(table: str, columns: list[str]) -> str:
    """Rows to load, with per-table deduplication where the source violates a
    natural key that the Postgres schema enforces."""
    projection = ", ".join(f'"{c}"' for c in columns)
    if table == "social_post":
        # 1,016 excess rows share (platform, platform_post_id); 971 of those
        # differ only in fetched_at, i.e. they are re-fetches of the same tweet.
        # Keep the most recently fetched row per natural key.
        return (
            f"SELECT {projection} FROM ("
            f"  SELECT {projection}, ROW_NUMBER() OVER ("
            f"    PARTITION BY platform, platform_post_id"
            f"    ORDER BY COALESCE(fetched_at,'') DESC, COALESCE(created_at,'') DESC, id"
            f"  ) AS pick FROM social_post"
            f") WHERE pick = 1"
        )
    return f'SELECT {projection} FROM "{table}"'


def surviving_post_ids(source: sqlite3.Connection) -> dict[str, str]:
    """Map dropped duplicate post id -> the id kept for that natural key.

    social_post is deduplicated on (platform, platform_post_id); without this
    remap, every post_issue tag and evidence link pointing at a dropped
    duplicate would be orphaned.
    """
    remap: dict[str, str] = {}
    rows = source.execute(
        "SELECT id, platform, platform_post_id, "
        "  ROW_NUMBER() OVER ("
        "    PARTITION BY platform, platform_post_id"
        "    ORDER BY COALESCE(fetched_at,'') DESC, COALESCE(created_at,'') DESC, id"
        "  ) AS pick "
        "FROM social_post"
    )
    keeper: dict[tuple[str, str], str] = {}
    others: list[tuple[str, tuple[str, str]]] = []
    for post_id, platform, platform_post_id, pick in rows:
        key = (platform, platform_post_id)
        if pick == 1:
            keeper[key] = post_id
        else:
            others.append((post_id, key))
    for post_id, key in others:
        if key in keeper:
            remap[post_id] = keeper[key]
    return remap


def handle_to_mk_id(source: sqlite3.Connection) -> dict[str, str]:
    """Twitter handle -> mk uuid, used to repair posts whose mk_id is a handle."""
    return {
        handle: mk_id
        for handle, mk_id in source.execute(
            "SELECT handle, mk_id FROM mk_social_account WHERE handle IS NOT NULL"
        )
    }


def load_table(
    source: sqlite3.Connection,
    target: psycopg.Connection,
    table: str,
    batch_size: int,
    repairs: dict[str, str] | None = None,
    post_remap: dict[str, str] | None = None,
    schema: str = "mk_tracking",
) -> tuple[int, int, dict[str, int]]:
    stats: dict[str, int] = {"repaired": 0, "dropped": 0, "nulled_account": 0,
                             "remapped": 0, "collapsed": 0}
    if not sqlite_has_table(source, table):
        return 0, 0, stats
    columns = sqlite_columns(source, table)
    converters = [converter_for(column) for column in columns]
    available = int(source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    target.execute(f"TRUNCATE {schema}.{table} CASCADE")
    quoted = ", ".join(f'"{c}"' for c in columns)
    copy_sql = f"COPY {schema}.{table} ({quoted}) FROM STDIN"

    mk_index = columns.index("mk_id") if table == "social_post" else -1
    account_index = columns.index("account_id") if table == "social_post" else -1

    # Tables whose post_id must follow social_post deduplication.
    post_index = -1
    dedupe_on: tuple[int, ...] = ()
    if post_remap and table in {"post_issue", "mk_issue_summary_supporting_post"}:
        post_index = columns.index("post_id")
        other = "issue_id" if table == "post_issue" else "summary_id"
        dedupe_on = (post_index, columns.index(other))
    seen: set[tuple[Any, ...]] = set()

    loaded = 0
    cursor = source.execute(select_sql(table, columns))
    with target.cursor().copy(copy_sql) as copy:
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                values = list(row)
                if mk_index >= 0 and not is_uuid(values[mk_index]):
                    # mk_id holding a Twitter handle: resolve it via the account
                    # table rather than discarding the post.
                    resolved = (repairs or {}).get(values[mk_index])
                    if resolved is None:
                        stats["dropped"] += 1
                        continue
                    values[mk_index] = resolved
                    stats["repaired"] += 1
                if account_index >= 0 and values[account_index] is not None \
                        and not is_uuid(values[account_index]):
                    stats["nulled_account"] += 1
                if post_index >= 0:
                    replacement = post_remap.get(values[post_index])
                    if replacement is not None:
                        values[post_index] = replacement
                        stats["remapped"] += 1
                    key = tuple(values[i] for i in dedupe_on)
                    if key in seen:
                        stats["collapsed"] += 1
                        continue
                    seen.add(key)
                copy.write_row([convert(value) for convert, value in zip(converters, values)])
                loaded += 1
    return loaded, available, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path")
    parser.add_argument(
        "database_url",
        nargs="?",
        help="PostgreSQL connection URI. Falls back to $DATABASE_URL, then the PG* "
             "variables — see src/mk_tracking/db_config.py. Prefer the environment: an argument "
             "here puts the password in `ps` output.",
    )
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument(
        "--schema", help="schema holding the tables (default: mk_tracking)"
    )
    args = parser.parse_args()

    try:
        database_url = resolve_dsn(args.database_url)
        schema = resolve_schema(args.schema)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"target: {describe(database_url)}")

    source = sqlite3.connect(f"file:{args.sqlite_path}?mode=ro", uri=True)
    repairs = handle_to_mk_id(source) if sqlite_has_table(source, "mk_social_account") else {}
    post_remap = surviving_post_ids(source) if sqlite_has_table(source, "social_post") else {}
    print(f"{'table':<36} {'loaded':>10} {'in export':>10}  note")
    print("-" * 78)

    total_loaded = 0
    repair_log: dict[str, int] = {}
    with psycopg.connect(database_url) as target:
        target.execute("SET session_replication_role = replica")  # defer FK checks
        for table in LOAD_ORDER:
            loaded, available, stats = load_table(
                source, target, table, args.batch_size, repairs, post_remap, schema
            )
            total_loaded += loaded
            if any(stats.values()):
                repair_log[table] = stats
            note = ""
            if not sqlite_has_table(source, table):
                note = "not in export"
            elif loaded < available:
                note = f"{available - loaded:,} dropped (see notes)"
            print(f"{table:<36} {loaded:>10,} {available:>10,}  {note}")
        target.execute("SET session_replication_role = DEFAULT")
        target.commit()

        print("-" * 78)
        print(f"{'total':<36} {total_loaded:>10,}")

        if repair_log:
            print("\nnotes")
            for table, stats in repair_log.items():
                if stats["repaired"]:
                    print(f"  {table}: repaired {stats['repaired']:,} mk_id values "
                          f"that held a Twitter handle instead of a UUID")
                if stats["nulled_account"]:
                    print(f"  {table}: nulled {stats['nulled_account']:,} account_id values "
                          f"that held a platform-native id instead of a UUID")
                if stats["remapped"]:
                    print(f"  {table}: repointed {stats['remapped']:,} rows at the surviving "
                          f"post after social_post deduplication "
                          f"({stats['collapsed']:,} then collapsed as duplicates)")
                if stats["dropped"]:
                    print(f"  {table}: dropped {stats['dropped']:,} rows whose mk_id "
                          f"could not be resolved")

        with target.cursor() as check:
            check.execute(
                f"SELECT count(*) FROM {schema}.social_post WHERE embedding IS NOT NULL"
            )
            print(f"\nposts carrying an embedding: {check.fetchone()[0]:,}")
    source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
