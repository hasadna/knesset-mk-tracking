"""Unit tests for db/bootstrap.py's schema retargeting.

The orchestrator advertises --schema, which is only true if db/schema.sql can be
pointed at another schema. These tests pin that down without needing a server:
the retargeting is pure text, and getting it silently wrong would build every
table in the wrong place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "db"))

import bootstrap


def test_default_schema_is_the_file_verbatim() -> None:
    """The common path must not depend on the substitution working at all."""
    assert bootstrap.schema_sql_for("mk_tracking") == bootstrap.SCHEMA_SQL.read_text()


def test_retargeting_moves_both_naming_statements() -> None:
    sql = bootstrap.schema_sql_for("analytics")
    assert 'CREATE SCHEMA IF NOT EXISTS "analytics";' in sql
    assert 'SET search_path TO "analytics", public;' in sql


def test_retargeting_leaves_no_reference_to_the_old_schema() -> None:
    """Everything after those two lines is unqualified, so nothing may remain.

    A leftover `mk_tracking` here would mean part of the schema is built in one
    place and part in another.
    """
    sql = bootstrap.schema_sql_for("analytics")
    executable = [
        line for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert not [line for line in executable if "mk_tracking" in line]


def test_retargeting_raises_rather_than_guessing(monkeypatch, tmp_path: Path) -> None:
    """If schema.sql is reorganised, fail loudly instead of building elsewhere."""
    reorganised = tmp_path / "schema.sql"
    reorganised.write_text("CREATE SCHEMA IF NOT EXISTS something_else;\n")
    monkeypatch.setattr(bootstrap, "SCHEMA_SQL", reorganised)
    with pytest.raises(SystemExit) as caught:
        bootstrap.schema_sql_for("analytics")
    assert "cannot retarget" in str(caught.value)


def test_load_order_comes_from_the_loader() -> None:
    """Verification must count what the loader actually loads."""
    from load_from_sqlite import LOAD_ORDER

    assert bootstrap._load_order() == list(LOAD_ORDER)


# ---------------------------------------------------------------------------
# Row-count classification
#
# verify()'s exit code is the only automated signal that a load finished. The
# empty-table rule below was once folded into the EXPECTED_SHORTFALL exemption,
# which silently turned a wiped mk_vote table into "all checks passed".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(bootstrap.EXPECTED_SHORTFALL))
def test_an_empty_table_is_a_problem_even_when_shortfalls_are_expected(
    table: str,
) -> None:
    """These three legitimately load fewer rows. None legitimately loads none.

    This is the detector for a wiped table — including the one the vote
    ingestion's own guards exist to prevent.
    """
    note, is_problem = bootstrap.classify_count(table, loaded=0, source_count=907_210)
    assert is_problem
    assert "EMPTY" in note


def test_expected_shortfall_is_not_a_problem() -> None:
    """social_post is deduplicated on its natural key; that loss is measured."""
    note, is_problem = bootstrap.classify_count(
        "social_post", loaded=8_617, source_count=9_633
    )
    assert not is_problem
    assert "expected" in note


def test_undocumented_shortfall_is_a_problem() -> None:
    """No documented reason to lose rows means the load did not finish."""
    note, is_problem = bootstrap.classify_count("party", loaded=319, source_count=320)
    assert is_problem
    assert "MISSING" in note


def test_empty_table_that_is_empty_in_the_export_is_fine() -> None:
    """committee, mk_role, bill_author and mk_relation are empty upstream."""
    assert bootstrap.classify_count("committee", loaded=0, source_count=0) == ("", False)


def test_extra_rows_are_reported_but_not_a_problem() -> None:
    """The vote ingestion legitimately adds rows the export never had."""
    note, is_problem = bootstrap.classify_count("mk_vote", loaded=100, source_count=90)
    assert not is_problem
    assert "more" in note
