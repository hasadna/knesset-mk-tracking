from __future__ import annotations

import json
from pathlib import Path

import pytest

from mk_tracking.collect.knesset.vote_identity import (
    build_exact_crosswalk,
    load_crosswalk,
    normalize_hebrew_name,
    resolve_vote_results,
    validate_crosswalk,
)


def test_normalize_hebrew_name_ignores_spacing_punctuation_and_final_letters() -> None:
    assert normalize_hebrew_name("  יואב סגלוביץ' ") == normalize_hebrew_name(
        "יואב-סגלוביצ"
    )


def test_build_exact_crosswalk_uses_source_mkid_not_canonical_id() -> None:
    rows = build_exact_crosswalk(
        [{"knesset_member_id": 30860, "full_name_he": "טלי גוטליב"}],
        [
            {
                "mkid": 34362,
                "firstname": "טלי",
                "lastname": "גוטליב",
                "vote_result_count": 123,
                "first_vote_at": "2023-01-01",
                "last_vote_at": "2026-01-01",
            }
        ],
    )
    assert rows[0]["vote_mkid"] == 34362
    assert rows[0]["knesset_member_id"] == 30860


def test_build_exact_crosswalk_rejects_missing_identity() -> None:
    with pytest.raises(ValueError, match="missing"):
        build_exact_crosswalk(
            [{"knesset_member_id": 1, "full_name_he": "אדם אחד"}], []
        )


def test_validate_crosswalk_rejects_duplicate_source_id() -> None:
    rows = [
        {
            "vote_mkid": 10,
            "knesset_member_id": 1,
            "match_method": "exact_normalized_name",
        },
        {
            "vote_mkid": 10,
            "knesset_member_id": 2,
            "match_method": "exact_normalized_name",
        },
    ]
    with pytest.raises(ValueError, match="vote_mkid"):
        validate_crosswalk(rows, expected_count=2)


def test_committed_crosswalk_covers_the_authoritative_roster() -> None:
    path = Path(__file__).parents[1] / "db" / "vote_mkid_crosswalk.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["mappings"]
    validate_crosswalk(rows)
    assert payload["mapping_count"] == 120
    assert payload["vote_result_count"] == sum(
        row["vote_result_count"] for row in rows
    )
    tali = next(row for row in rows if row["knesset_member_id"] == 30860)
    assert tali["vote_mkid"] == 34379
    assert load_crosswalk(path)[34379] == 30860


def test_resolve_vote_results_preserves_count_and_adds_canonical_id() -> None:
    source = [{"mkid": 34379, "voteid": 99, "resultcode": 7}]
    assert resolve_vote_results(source, {34379: 30860}) == [
        {
            "mkid": 34379,
            "knesset_member_id": 30860,
            "voteid": 99,
            "resultcode": 7,
        }
    ]


def test_resolve_vote_results_rejects_unknown_source_mkid() -> None:
    with pytest.raises(ValueError, match="unresolved source mkids"):
        resolve_vote_results([{"mkid": 999, "voteid": 1}], {34379: 30860})
