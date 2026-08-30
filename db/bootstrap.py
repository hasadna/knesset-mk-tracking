#!/usr/bin/env python3
"""Build a complete mk_tracking database from scratch, on any PostgreSQL server.

    uv run python db/bootstrap.py --sqlite path/to/mk_tracking.db

Runs the whole sequence and verifies the result:

  1. apply `db/schema.sql`                     (skip: --skip-schema)
  2. load the SQLite export                    (skip: --skip-load)
  3. ingest vote events from over.org.il       (skip: --skip-votes)
  4. count every table and compare with the export

Each step is idempotent, so a re-run repairs a partial one. Steps 1-2 are the
migration; step 3 fills the one gap the export has — `vote_event` and
`vote_event_issue` were lost before it was taken, leaving all 907,210 `mk_vote`
rows pointing at events that exist nowhere. It needs no credentials; the Over
Knesset API is public.

The target comes from --database-url, $DATABASE_URL or the PG* variables — see
src/mk_tracking/db_config.py. Nothing here assumes localhost, so the same
command builds a hosted database; add sslmode for one.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import psycopg

DB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DB_DIR.parent
SCHEMA_SQL = DB_DIR / "schema.sql"
LOADER = DB_DIR / "load_from_sqlite.py"

from mk_tracking.db_config import ConfigError, describe, resolve_dsn, resolve_schema

# db/ is a directory of scripts, not a package, so its siblings are imported by
# path. (mk_tracking itself is installed in the venv, hence the import above.)
sys.path.insert(0, str(DB_DIR))


def _load_order() -> list[str]:
    """The tables the loader moves, in foreign-key order.

    Imported rather than copied, so the verification step cannot drift out of
    step with what the loader actually loads.
    """
    from load_from_sqlite import LOAD_ORDER

    return list(LOAD_ORDER)


class SchemaMissing(RuntimeError):
    """The target schema has not been applied to the database."""


class SchemaAlreadyApplied(RuntimeError):
    """db/schema.sql has already been applied; it cannot be applied twice."""


def banner(step: str, title: str) -> None:
    print(f"\n{'=' * 78}\n{step}  {title}\n{'=' * 78}", flush=True)


def run(argv: list[str], label: str, dsn: str) -> None:
    """Run a child step, streaming its output. Raises on failure.

    The DSN travels in the child's environment, never in argv: command lines are
    world-readable through `ps`, and this one carries a password.
    """
    print(f"$ {' '.join(argv)}\n", flush=True)
    environment = {**os.environ, "DATABASE_URL": dsn}
    result = subprocess.run(argv, cwd=PROJECT_ROOT, env=environment, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with exit code {result.returncode}")


# The two statements in schema.sql that name the schema. Everything after them
# is unqualified and resolves through search_path, so rewriting these two is
# enough to build the whole schema somewhere else.
SCHEMA_STATEMENTS = (
    (re.compile(r"^CREATE SCHEMA IF NOT EXISTS mk_tracking;$", re.MULTILINE),
     'CREATE SCHEMA IF NOT EXISTS "{schema}";'),
    (re.compile(r"^SET search_path TO mk_tracking, public;$", re.MULTILINE),
     'SET search_path TO "{schema}", public;'),
)


def schema_sql_for(schema: str) -> str:
    """db/schema.sql, retargeted at `schema`.

    Only the CREATE SCHEMA and SET search_path lines name it; the rest of the
    file is unqualified. Each substitution must match exactly once — if
    schema.sql is ever reorganised so one does not, this raises rather than
    silently building the tables in the wrong place.
    """
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    if schema == "mk_tracking":
        return sql
    for pattern, replacement in SCHEMA_STATEMENTS:
        sql, count = pattern.subn(replacement.format(schema=schema), sql)
        if count != 1:
            raise SystemExit(
                f"cannot retarget db/schema.sql at schema {schema!r}: expected exactly "
                f"one line matching {pattern.pattern!r}, found {count}. Update "
                "SCHEMA_STATEMENTS in db/bootstrap.py to match schema.sql."
            )
    return sql


def check_extension_reachable(connection: psycopg.Connection, schema: str) -> None:
    """Fail early if pg_trgm sits outside the target's search_path.

    schema.sql indexes with `gin_trgm_ops`, which resolves through search_path
    (`schema`, then public). Older databases were built before pg_trgm was
    pinned to public, so the extension may live inside whichever schema was
    current at the time. Building a *second* schema in such a database then
    fails deep inside CREATE INDEX with `operator class "gin_trgm_ops" does not
    exist`, which says nothing about the real cause or the one-line fix.
    """
    row = connection.execute(
        "SELECT n.nspname FROM pg_extension e "
        "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
    ).fetchone()
    if row is None:
        return  # not installed yet; schema.sql creates it in public
    installed_in = row[0]
    if installed_in in {"public", schema}:
        return
    raise SystemExit(
        f"pg_trgm is installed in schema {installed_in!r}, which is not on the "
        f"search_path for {schema!r}, so the trigram indexes cannot be created.\n"
        "It is an extension, so it belongs in public. Move it once:\n"
        '  psql "$DATABASE_URL" -c \'ALTER EXTENSION pg_trgm SET SCHEMA public\''
    )


def apply_schema(dsn: str, schema: str) -> None:
    """Execute db/schema.sql, retargeted at `schema`.

    Done through psycopg rather than `psql -f` so the only requirement is the
    Python dependency the project already has; the file contains no psql
    meta-commands.

    schema.sql is *not* re-runnable: its CREATE TYPE and CREATE TABLE statements
    carry no IF NOT EXISTS, so applying it twice fails. That is caught here and
    reported as the ordinary situation it is — the schema already exists — rather
    than as a DuplicateObject traceback.
    """
    with psycopg.connect(dsn, autocommit=False) as connection:
        check_extension_reachable(connection, schema)
        try:
            connection.execute(schema_sql_for(schema))
        except psycopg.errors.InsufficientPrivilege as error:
            # The managed-PostgreSQL case: pg_trgm is not installed and this role
            # may not create it. check_extension_reachable cannot catch this — it
            # returns early precisely because no pg_trgm row exists yet.
            raise SystemExit(
                f"not permitted to create the pg_trgm extension ({error}).\n"
                "Managed PostgreSQL usually restricts CREATE EXTENSION. Ask the "
                "provider to install pg_trgm (most have it available), then re-run:\n"
                '  CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;'
            ) from error
        except psycopg.errors.DuplicateObject as error:
            raise SchemaAlreadyApplied(
                f"schema {schema!r} already exists ({error}).\n"
                "db/schema.sql cannot be applied twice. To load into the existing "
                "schema, re-run with --skip-schema. To rebuild from nothing:\n"
                f'  psql "$DATABASE_URL" -c \'DROP SCHEMA {schema} CASCADE\''
            ) from error
        connection.commit()
    print(f"applied {SCHEMA_SQL.relative_to(PROJECT_ROOT)} to schema {schema!r}")


def sqlite_counts(sqlite_path: Path, tables: list[str]) -> dict[str, int | None]:
    """Row count per table in the export. None means the table is not present."""
    counts: dict[str, int | None] = {}
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        present = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        for table in tables:
            if table not in present:
                counts[table] = None
                continue
            counts[table] = source.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    finally:
        source.close()
    return counts


def postgres_counts(dsn: str, schema: str, tables: list[str]) -> dict[str, int]:
    """Row count per table. Raises `SchemaMissing` if the schema is not applied."""
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        for table in tables:
            # `schema` is validated as a bare identifier by resolve_schema; the
            # table names come from LOAD_ORDER, not from user input.
            try:
                cursor.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            except psycopg.errors.UndefinedTable as error:
                # Reaching here means the schema was never applied, or --schema
                # names one that does not exist. Both are ordinary mistakes and
                # deserve the same treatment as every other failure in main().
                raise SchemaMissing(
                    f"{schema}.{table} does not exist. Apply the schema first: run "
                    "without --skip-schema, or `uv run python db/bootstrap.py "
                    f"--skip-load --skip-votes --schema {schema}`."
                ) from error
            counts[table] = cursor.fetchone()[0]
    return counts


# Tables whose loaded count is legitimately below the export's, each for a reason
# db/DATA_QUALITY.md measures and explains: social_post is deduplicated on its
# natural key, post_issue loses the tags that collapse with it, and mk_vote is
# replaced wholesale by the over.org.il fetch. A shortfall anywhere else has no
# explanation and means the load did not finish.
EXPECTED_SHORTFALL = {"social_post", "post_issue", "mk_vote"}


def classify_count(table: str, loaded: int, source_count: int) -> tuple[str, bool]:
    """Describe one table's row count, and say whether it is a problem.

    Pure so the rules can be tested without a database — the empty case below is
    the detector for a wiped table, and it is easy to lose while adjusting the
    others.
    """
    if loaded == 0 and source_count > 0:
        # Checked before the EXPECTED_SHORTFALL exemption, never inside it. Those
        # three tables legitimately load *fewer* rows than the export; none of
        # them legitimately loads none. This is what catches a table that the
        # vote ingestion, or anything else, has emptied.
        return "EMPTY but the export has rows", True
    if loaded < source_count:
        missing = source_count - loaded
        if table in EXPECTED_SHORTFALL:
            return f"{missing:,} fewer (expected — see db/DATA_QUALITY.md)", False
        # No documented reason for this table to lose rows, so the load is
        # incomplete however plausible the number looks.
        return f"{missing:,} MISSING with no documented cause", True
    if loaded > source_count:
        return f"{loaded - source_count:,} more (added by ingestion)", False
    return "", False


def verify(dsn: str, schema: str, sqlite_path: Path | None) -> int:
    """Compare loaded rows with the export. Returns the number of problems."""
    tables = _load_order()
    loaded = postgres_counts(dsn, schema, tables)
    expected = sqlite_counts(sqlite_path, tables) if sqlite_path else {}

    print(f"{'table':<36} {'in postgres':>12} {'in export':>12}  note")
    print("-" * 78)
    problems = 0
    for table in tables:
        source_count = expected.get(table)
        note = ""
        if source_count is None:
            # Absent from the export. Anything present came from the vote
            # ingestion, which is the point of that step.
            note = "not in export" if sqlite_path else ""
            source_text = "-"
        else:
            source_text = f"{source_count:,}"
            note, is_problem = classify_count(table, loaded[table], source_count)
            problems += is_problem
        print(f"{table:<36} {loaded[table]:>12,} {source_text:>12}  {note}")
    print("-" * 78)
    print(f"{'total':<36} {sum(loaded.values()):>12,}")

    # The orphans the vote ingestion exists to resolve.
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            f'SELECT count(*) FROM "{schema}"."mk_vote" v '
            f'WHERE NOT EXISTS (SELECT 1 FROM "{schema}"."vote_event" e WHERE e.id = v.vote_event_id)'
        )
        orphans = cursor.fetchone()[0]
        cursor.execute(f'SELECT count(*) FROM "{schema}"."social_post" WHERE embedding IS NOT NULL')
        embedded = cursor.fetchone()[0]
        cursor.execute(f'SELECT count(*) FROM "{schema}"."mk" WHERE is_current')
        current_mks = cursor.fetchone()[0]

    print(f"\nmk_vote rows with no vote_event: {orphans:,}")
    print(f"posts carrying an embedding:     {embedded:,}")
    print(f"current MKs:                     {current_mks:,}")
    if orphans:
        problems += 1
        print("  -> the vote ingestion has not resolved these; mk_vote's foreign key")
        print("     in db/pending_constraints.sql (3) cannot be added yet.")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database-url",
        help="PostgreSQL connection URI. Falls back to $DATABASE_URL, then the PG* variables.",
    )
    parser.add_argument("--schema", help="schema holding the tables (default: mk_tracking)")
    parser.add_argument(
        "--sqlite",
        type=Path,
        help="path to the mk_tracking.db export. Required unless --skip-load.",
    )
    parser.add_argument("--batch-size", type=int, default=2000, help="loader batch size")
    parser.add_argument("--skip-schema", action="store_true", help="assume schema.sql is applied")
    parser.add_argument("--skip-load", action="store_true", help="do not load the SQLite export")
    parser.add_argument("--skip-votes", action="store_true", help="do not ingest over.org.il votes")
    parser.add_argument(
        "--dry-run-votes",
        action="store_true",
        help="fetch and diagnose the vote ingestion without committing it",
    )
    args = parser.parse_args()

    try:
        dsn = resolve_dsn(args.database_url)
        schema = resolve_schema(args.schema)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not args.skip_load and args.sqlite is None:
        print("error: --sqlite is required unless --skip-load is given", file=sys.stderr)
        return 2
    if args.sqlite is not None and not args.sqlite.exists():
        print(f"error: no such file: {args.sqlite}", file=sys.stderr)
        return 2

    print(f"target: {describe(dsn)}")
    print(f"schema: {schema}")
    started = time.monotonic()

    # A wrong password or a missing database is the most likely thing to go wrong
    # here, and the least deserving of a stack trace. `describe` keeps the target
    # in the message; libpq's own text never carries the password.
    try:
        with psycopg.connect(dsn, connect_timeout=10):
            pass
    except psycopg.OperationalError as error:
        print(f"error: cannot connect to {describe(dsn)}\n  {error}", file=sys.stderr)
        return 2

    if not args.skip_schema:
        banner("[1/4]", "apply db/schema.sql")
        try:
            apply_schema(dsn, schema)
        except SchemaAlreadyApplied as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
    else:
        print("\n[1/4] apply db/schema.sql — skipped")

    if not args.skip_load:
        banner("[2/4]", f"load {args.sqlite}")
        run(
            [
                sys.executable, str(LOADER), str(args.sqlite),
                "--batch-size", str(args.batch_size),
                "--schema", schema,
            ],
            "SQLite load",
            dsn,
        )
    else:
        print("\n[2/4] load the SQLite export — skipped")

    if not args.skip_votes:
        banner("[3/4]", "ingest vote events from over.org.il")
        vote_argv = [
            sys.executable, "-m", "mk_tracking.collect.knesset.upload_over_to_postgres",
            "--schema", schema,
        ]
        if args.dry_run_votes:
            vote_argv.append("--dry-run")
        run(vote_argv, "vote ingestion", dsn)
    else:
        print("\n[3/4] ingest vote events — skipped")

    banner("[4/4]", "verify")
    # Compare against the export whenever one was named, so
    # `--skip-schema --skip-load --skip-votes --sqlite ...` is a standalone check
    # of a database someone else built.
    try:
        problems = verify(dsn, schema, args.sqlite)
    except (SchemaMissing, psycopg.OperationalError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    elapsed = time.monotonic() - started
    print(f"\nfinished in {elapsed / 60:.1f} min")
    if problems:
        print(f"{problems} check(s) need attention — see the notes above.")
        return 1
    print("all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
