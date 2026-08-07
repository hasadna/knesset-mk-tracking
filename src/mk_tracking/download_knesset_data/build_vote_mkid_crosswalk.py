"""Generate the local vote identity crosswalk without writing to BigQuery."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import bigquery

from .upload_over_to_bigquery import fetch_all_over_sql
from .vote_identity import build_exact_crosswalk, validate_crosswalk

DEFAULT_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
DEFAULT_DATASET = "mk_tracking"
DEFAULT_OUTPUT = Path("db/vote_mkid_crosswalk.json")


def fetch_current_mks(project: str, dataset: str) -> list[dict[str, object]]:
    client = bigquery.Client(project=project)
    query = f"""
    SELECT knesset_member_id, full_name_he
    FROM `{project}.{dataset}.mk`
    WHERE is_current
    ORDER BY knesset_member_id
    """
    return [dict(row.items()) for row in client.query(query).result()]


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


def generate(project: str, dataset: str, output: Path) -> dict[str, object]:
    canonical_mks = fetch_current_mks(project, dataset)
    if len(canonical_mks) != 120:
        raise ValueError(f"expected exactly 120 current MKs, got {len(canonical_mks)}")
    rows = build_exact_crosswalk(canonical_mks, fetch_vote_identities())
    validate_crosswalk(rows)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "canonical_source": f"{project}.{dataset}.mk WHERE is_current",
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
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = generate(args.project, args.dataset, args.output)
    print(
        f"Wrote {payload['mapping_count']} mappings covering "
        f"{payload['vote_result_count']} vote results to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
