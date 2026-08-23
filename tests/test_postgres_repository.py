"""PostgresRepository against a real PostgreSQL server.

Every other test in this suite uses fakes. These do not: they seed the v6
schema from db/schema.sql and read it back through the repository the web app
actually serves from, which is the only way the hand-written SQL — DISTINCT ON,
jsonb_agg, the jsonpath casts — gets exercised at all.

Skips when the server is down. See tests/conftest.py and compose.test.yaml.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.pg


def test_list_issues_reads_the_taxonomy(pg_repository, seeded_db) -> None:
    issues = pg_repository.list_issues()
    assert [issue["id"] for issue in issues] == ["test-issue"]
    assert issues[0]["title"] == "נושא בדיקה"
    assert issues[0]["description"] == "תיאור"


def test_list_mks_joins_party_account_and_counts(pg_repository, seeded_db) -> None:
    members = pg_repository.list_mks()
    assert len(members) == 1

    member = members[0]
    assert member["key"] == seeded_db["mk_key"]
    assert member["name"] == "חבר כנסת לבדיקה"
    assert member["party"] == "מפלגת הבדיקה"  # via mk_affiliation, end_date IS NULL
    assert member["account"] == "test_mk"  # via mk_social_account
    assert member["postCount"] == 1  # via social_post
    assert member["current"] is True
    assert member["hasData"] is True
    assert member["coverage"] == {"test-issue": "strong"}  # jsonb_agg round-trip


def test_get_mk_finds_the_seeded_member(pg_repository, seeded_db) -> None:
    member = pg_repository.get_mk(seeded_db["mk_key"])
    assert member is not None
    assert member["name"] == "חבר כנסת לבדיקה"


def test_get_mk_returns_none_for_an_unknown_key(pg_repository, seeded_db) -> None:
    assert pg_repository.get_mk("mk-424242") is None


def test_get_mk_issues_returns_the_summary(pg_repository, seeded_db) -> None:
    payload = pg_repository.get_mk_issues(seeded_db["mk_key"])
    assert payload is not None

    blob = repr(payload)
    assert "סיכום קצר" in blob or "סיכום מורחב" in blob


def test_get_mk_posts_returns_the_seeded_post(pg_repository, seeded_db) -> None:
    page = pg_repository.get_mk_posts(seeded_db["mk_key"], None, 20, 0)
    assert page["total"] == 1
    assert page["limit"] == 20
    assert [post["key"] for post in page["items"]] == [seeded_db["post_key"]]


def test_get_mk_posts_filters_by_issue(pg_repository, seeded_db) -> None:
    tagged = pg_repository.get_mk_posts(seeded_db["mk_key"], "test-issue", 20, 0)
    assert tagged["total"] == 1

    untagged = pg_repository.get_mk_posts(seeded_db["mk_key"], "no-such-issue", 20, 0)
    assert untagged["total"] == 0


def test_get_mk_posts_rejects_a_runaway_offset(pg_repository, seeded_db) -> None:
    with pytest.raises(ValueError, match="offset exceeds maximum"):
        pg_repository.get_mk_posts(seeded_db["mk_key"], None, 20, 1001)


def test_get_post_reads_one_post_by_key(pg_repository, seeded_db) -> None:
    post = pg_repository.get_post(seeded_db["post_key"])
    assert post is not None
    assert post["key"] == seeded_db["post_key"]


def test_get_post_rejects_a_malformed_key(pg_repository, seeded_db) -> None:
    # POST_KEY_PATTERN guards the interpolated handle; a key that does not
    # match must never reach the database.
    assert pg_repository.get_post("x:bad handle:not-a-number") is None


def test_preload_summaries_warms_without_error(pg_repository, seeded_db) -> None:
    pg_repository.preload_summaries()
    assert pg_repository.get_mk_issues(seeded_db["mk_key"]) is not None


def test_deleted_posts_are_not_counted(pg_repository, seeded_db, pg_conn) -> None:
    pg_conn.execute(
        "update mk_tracking.social_post set is_deleted = true where id = %s",
        (seeded_db["post_id"],),
    )
    assert pg_repository.list_mks()[0]["postCount"] == 0


def test_an_invalid_schema_name_is_refused(postgres_dsn) -> None:
    from mk_tracking.ui_app.repositories import PostgresRepository

    with pytest.raises(ValueError, match="invalid Postgres schema"):
        PostgresRepository(postgres_dsn, schema='public"; drop table mk; --')
