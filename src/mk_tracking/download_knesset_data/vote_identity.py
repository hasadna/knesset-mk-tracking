"""Build and validate the Over-vote-ID to canonical-MK-ID crosswalk."""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

FINAL_TO_REGULAR = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})


def normalize_hebrew_name(value: str | None) -> str:
    """Return letters-only Hebrew text with final letters normalized."""
    normalized = unicodedata.normalize("NFKD", value or "").translate(
        FINAL_TO_REGULAR
    )
    return "".join(character for character in normalized if "א" <= character <= "ת")


def build_exact_crosswalk(
    canonical_mks: Iterable[dict[str, Any]],
    vote_identities: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve vote identities by exact normalized name and reject ambiguity."""
    canonical_by_name: dict[str, dict[str, Any]] = {}
    for member in canonical_mks:
        normalized_name = normalize_hebrew_name(member["full_name_he"])
        if not normalized_name:
            raise ValueError("canonical MK has an empty normalized name")
        if normalized_name in canonical_by_name:
            raise ValueError(f"duplicate canonical MK name: {member['full_name_he']}")
        canonical_by_name[normalized_name] = member

    matches: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for identity in vote_identities:
        source_name = " ".join(
            part
            for part in (identity.get("firstname"), identity.get("lastname"))
            if part
        ).strip()
        member = canonical_by_name.get(normalize_hebrew_name(source_name))
        if member is None:
            continue
        matches[int(member["knesset_member_id"])].append(
            {
                "vote_mkid": int(identity["mkid"]),
                "knesset_member_id": int(member["knesset_member_id"]),
                "full_name_he": member["full_name_he"],
                "source_name_he": source_name,
                "vote_result_count": int(identity["vote_result_count"]),
                "first_vote_at": identity.get("first_vote_at"),
                "last_vote_at": identity.get("last_vote_at"),
                "match_method": "exact_normalized_name",
            }
        )

    missing = [
        member["full_name_he"]
        for member in canonical_mks
        if int(member["knesset_member_id"]) not in matches
    ]
    ambiguous = {
        member_id: rows for member_id, rows in matches.items() if len(rows) != 1
    }
    if missing or ambiguous:
        raise ValueError(
            f"crosswalk is incomplete or ambiguous: missing={missing}, "
            f"ambiguous_ids={sorted(ambiguous)}"
        )

    return sorted(
        (rows[0] for rows in matches.values()),
        key=lambda row: row["knesset_member_id"],
    )


def validate_crosswalk(rows: Iterable[dict[str, Any]], expected_count: int = 120) -> None:
    """Validate one-to-one source and canonical identifiers."""
    materialized = list(rows)
    vote_ids = {int(row["vote_mkid"]) for row in materialized}
    canonical_ids = {int(row["knesset_member_id"]) for row in materialized}
    if len(materialized) != expected_count:
        raise ValueError(
            f"expected {expected_count} crosswalk rows, got {len(materialized)}"
        )
    if len(vote_ids) != expected_count:
        raise ValueError("vote_mkid values are not one-to-one")
    if len(canonical_ids) != expected_count:
        raise ValueError("knesset_member_id values are not one-to-one")
    if any(row.get("match_method") != "exact_normalized_name" for row in materialized):
        raise ValueError("crosswalk contains a non-exact or unreviewed match")


def load_crosswalk(path: Path, expected_count: int = 120) -> dict[int, int]:
    """Load a reviewed crosswalk indexed by the vote source's ``mkid``."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported vote crosswalk schema in {path}")
    rows = payload.get("mappings")
    if not isinstance(rows, list):
        raise TypeError(f"vote crosswalk mappings must be a list in {path}")
    validate_crosswalk(rows, expected_count=expected_count)
    return {
        int(row["vote_mkid"]): int(row["knesset_member_id"]) for row in rows
    }


def resolve_vote_results(
    vote_results: Iterable[dict[str, Any]], crosswalk: dict[int, int]
) -> list[dict[str, Any]]:
    """Replace source vote IDs with canonical IDs, failing on unknown voters."""
    resolved = []
    unresolved: dict[int, int] = defaultdict(int)
    for result in vote_results:
        source_mkid = int(result["mkid"])
        canonical_id = crosswalk.get(source_mkid)
        if canonical_id is None:
            unresolved[source_mkid] += 1
            continue
        resolved.append({**result, "knesset_member_id": canonical_id})
    if unresolved:
        sample = sorted(unresolved.items())[:10]
        raise ValueError(
            f"{sum(unresolved.values())} vote results have unresolved source mkids; "
            f"sample={sample}"
        )
    return resolved
