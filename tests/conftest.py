from __future__ import annotations

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    import psycopg

# Matches compose.test.yaml. Point TEST_DATABASE_URL somewhere else to run the
# DB tests against another server — but see the guard in `_assert_disposable`.
DEFAULT_TEST_DSN = "postgresql://mk:mk@127.0.0.1:55432/mk_tracking"

SCHEMA = "mk_tracking"

_START_HINT = (
    "PostgreSQL test server is not reachable at {dsn}. Start it with:\n"
    "  docker compose -f compose.test.yaml up -d --wait"
)

# Truncated before and after each seeded test, children first.
FIXTURE_TABLES = (
    "post_issue",
    "mk_issue_summary",
    "social_post",
    "mk_social_account",
    "mk_affiliation",
    "mk",
    "issue",
    "party",
)

# Above this many MKs the target is a real import, not a test database.
REAL_DATA_THRESHOLD = 50


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """DSN of a live test server, or skip.

    The suite stays runnable on a fresh clone with no Docker: DB tests skip
    rather than fail when the server (or psycopg) is absent.
    """
    psycopg = pytest.importorskip(
        "psycopg", reason="psycopg is not installed — run `uv sync`"
    )
    dsn = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DSN)
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            if conn.execute(f"select to_regclass('{SCHEMA}.mk')").fetchone()[0] is None:
                pytest.skip(
                    f"{dsn} has no '{SCHEMA}' schema — apply db/schema.sql, or let "
                    "compose.test.yaml do it: docker compose -f compose.test.yaml up -d --wait"
                )
    except psycopg.OperationalError as exc:
        pytest.skip(f"{_START_HINT.format(dsn=dsn)}\n({exc})")
    return dsn


def _assert_disposable(conn: psycopg.Connection) -> None:
    """Refuse to truncate a database that holds a real import.

    The seeded fixtures wipe their tables. Nothing must be able to point them
    at the development database and destroy the SQLite import, which takes
    hours to rebuild and is not in the repository.
    """
    count = conn.execute(f"select count(*) from {SCHEMA}.mk").fetchone()[0]
    if count > REAL_DATA_THRESHOLD:
        pytest.skip(
            f"refusing to truncate: target has {count} MKs and looks like a real "
            "import, not a test database. Point TEST_DATABASE_URL at the server "
            "from compose.test.yaml (port 55432)."
        )


@pytest.fixture
def pg_conn(postgres_dsn: str) -> Iterator[psycopg.Connection]:
    """Autocommit connection for arranging fixture data.

    Autocommit, not a rolled-back transaction: the repository under test opens
    its own connections, so anything held in an uncommitted transaction here
    would be invisible to it. Cleanup is by truncation instead — see `seeded_db`.
    """
    import psycopg

    conn = psycopg.connect(postgres_dsn, autocommit=True)
    try:
        _assert_disposable(conn)
        yield conn
    finally:
        conn.close()


def _truncate(conn: psycopg.Connection) -> None:
    tables = ", ".join(f"{SCHEMA}.{name}" for name in FIXTURE_TABLES)
    conn.execute(f"truncate {tables} restart identity cascade")


@pytest.fixture
def seeded_db(pg_conn: psycopg.Connection) -> Iterator[dict[str, Any]]:
    """One MK with a party, a Twitter account, a post and an issue summary.

    Enough of the graph for every read path the explorer uses. Returns the
    identifiers the tests assert on. The tables are emptied on the way in and
    on the way out, so tests neither inherit nor leave state.
    """
    _truncate(pg_conn)
    try:
        ids = _seed(pg_conn)
        yield ids
    finally:
        _truncate(pg_conn)


def _seed(conn: psycopg.Connection) -> dict[str, Any]:
    def scalar(sql: str, params: tuple[Any, ...] | None = None) -> Any:
        return conn.execute(sql, params).fetchone()[0]

    party_id = scalar(
        f"insert into {SCHEMA}.party (name_he, name_en, is_current) "
        "values ('מפלגת הבדיקה', 'Test Party', true) returning id"
    )
    mk_id = scalar(
        f"insert into {SCHEMA}.mk (knesset_member_id, slug, full_name_he, bio_he, is_current) "
        "values (99001, 'test-mk', 'חבר כנסת לבדיקה', 'ביוגרפיה', true) returning id"
    )
    conn.execute(
        f"insert into {SCHEMA}.mk_affiliation (mk_id, party_id, start_date, end_date) "
        "values (%s, %s, date '2022-11-01', null)",
        (mk_id, party_id),
    )
    account_id = scalar(
        f"insert into {SCHEMA}.mk_social_account (mk_id, platform, handle, url, is_active, verified) "
        "values (%s, 'twitter', 'test_mk', 'https://x.com/test_mk', true, true) returning id",
        (mk_id,),
    )
    post_id = scalar(
        f"""insert into {SCHEMA}.social_post
              (mk_id, account_id, platform, platform_post_id, url, posted_at, text,
               language, engagement, is_deleted)
            values (%s, %s, 'twitter', '1234567890', 'https://x.com/test_mk/status/1234567890',
                    timestamptz '2026-01-15 09:00:00+00', 'טקסט הפוסט לבדיקה',
                    'he', '{{"likes": 12, "replies": 3}}'::jsonb, false)
            returning id""",
        (mk_id, account_id),
    )
    issue_id = scalar(
        f"insert into {SCHEMA}.issue (slug, name, description, sort_order) "
        "values ('test-issue', 'נושא בדיקה', 'תיאור', 10) returning id"
    )
    conn.execute(
        f"insert into {SCHEMA}.post_issue (post_id, issue_id, confidence, is_concrete_promise) "
        "values (%s, %s, 0.91, true)",
        (post_id, issue_id),
    )
    conn.execute(
        f"""insert into {SCHEMA}.mk_issue_summary
              (mk_id, issue_id, summary_he, extended_summary_he, quality, rating, model_version)
            values (%s, %s, 'סיכום קצר', 'סיכום מורחב', 'strong', 4, 'test-model')""",
        (mk_id, issue_id),
    )
    return {
        "party_id": party_id,
        "mk_id": mk_id,
        "account_id": account_id,
        "post_id": post_id,
        "issue_id": issue_id,
        "mk_key": "mk-99001",
        "post_key": "x:test_mk:1234567890",
    }


@pytest.fixture
def pg_repository(postgres_dsn: str):
    """A PostgresRepository against the test server.

    Function-scoped on purpose: the repository memoises reads in 30-minute TTL
    caches, so a shared instance would serve one test's rows to the next.
    """
    from mk_tracking.ui_app.repositories import PostgresRepository

    return PostgresRepository(postgres_dsn, schema=SCHEMA)
