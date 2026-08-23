"""Generate the local vote identity crosswalk from PostgreSQL + the Over API.

Reads the current MK roster from PostgreSQL and the vote identities from the
Over API, then writes db/vote_mkid_crosswalk.json. It never writes to a database.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from ..db_config import SCHEMA_PATTERN, ConfigError, resolve_dsn, resolve_schema
from .over_api import fetch_all_over_sql
from .vote_identity import build_exact_crosswalk, validate_crosswalk

DEFAULT_SCHEMA = "mk_tracking"
DEFAULT_OUTPUT = Path("db/vote_mkid_crosswalk.json")


def fetch_current_mks(conninfo: str, schema: str = DEFAULT_SCHEMA) -> list[dict[str, object]]:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("invalid Postgres schema")
    query = f"""
    SELECT knesset_member_id, full_name_he
    FROM {schema}.mk
    WHERE is_current
    ORDER BY knesset_member_id
    """
    with (
        psycopg.connect(conninfo) as connection,
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        cursor.execute(query)
        return cursor.fetchall()


def fetch_vote_identities() -> list[dict[str, object]]:
    rows = fetch_all_over_sql(
        """
        SELECT
          mkid,
          firstname,
          lastname,
          COUNT(*) AS vote_result_count,
          MIN(votedate) AS first_vote_at,
          MAX(votedate) AS last_vote_at
        FROM kns_plenumvoteresult
        GROUP BY mkid, firstname, lastname
        ORDER BY mkid
        """
    )
    return rows


def generate(conninfo: str, schema: str, output: Path) -> dict[str, object]:
    canonical_mks = fetch_current_mks(conninfo, schema)
    if len(canonical_mks) != 120:
        raise ValueError(f"expected exactly 120 current MKs, got {len(canonical_mks)}")
    rows = build_exact_crosswalk(canonical_mks, fetch_vote_identities())
    validate_crosswalk(rows)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "canonical_source": f"postgres:{schema}.mk WHERE is_current",
        "vote_source": "over.org.il:kns_plenumvoteresult",
        "matching_policy": "exact normalized Hebrew full name only; no fuzzy matching",
        "mapping_count": len(rows),
        "vote_result_count": sum(row["vote_result_count"] for row in rows),
        "mappings": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a read-only Over vote-mkid crosswalk for the 120 current MKs."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL connection string. Falls back to $DATABASE_URL, then the "
             "PG* variables.",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help=f"PostgreSQL schema holding the tables. Falls back to "
             f"$MK_TRACKING_SCHEMA, then {DEFAULT_SCHEMA}.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        database_url = resolve_dsn(args.database_url)
        schema = resolve_schema(args.schema)
    except ConfigError as error:
        parser.error(str(error))
    payload = generate(database_url, schema, args.output)
    print(
        f"Wrote {payload['mapping_count']} mappings covering "
        f"{payload['vote_result_count']} vote results to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
