"""Unit tests for mk_tracking.db_config — the connection-target resolution.

Pure logic: no server is involved, so these run everywhere. The behaviour worth
pinning down is the precedence between the three sources, the refusal to invent
a localhost default, and that a password never reaches a log line.
"""

from __future__ import annotations

import pytest

from mk_tracking.db_config import (
    ConfigError,
    describe,
    resolve_dsn,
    resolve_schema,
)

DISCRETE = {
    "PGHOST": "db.example.com",
    "PGPORT": "5432",
    "PGUSER": "mk",
    "PGPASSWORD": "s3cret",
    "PGDATABASE": "mk_tracking",
}


def test_explicit_url_wins_over_everything() -> None:
    env = {"DATABASE_URL": "postgresql://env/db", **DISCRETE}
    assert resolve_dsn("postgresql://flag/db", env) == "postgresql://flag/db"


def test_database_url_wins_over_discrete_variables() -> None:
    assert resolve_dsn(None, {"DATABASE_URL": "postgresql://env/db", **DISCRETE}) == (
        "postgresql://env/db"
    )


def test_db_uri_is_read_for_the_deployment_platform() -> None:
    """The hasadna platform injects DB_URI, not DATABASE_URL.

    Reading it means a deployed server connects with no extra configuration
    rather than refusing to start.
    """
    assert resolve_dsn(None, {"DB_URI": "postgresql://app:pw@host:5432/app"}) == (
        "postgresql://app:pw@host:5432/app"
    )


def test_database_url_wins_over_db_uri() -> None:
    """An explicitly set DATABASE_URL overrides whatever the platform injected."""
    env = {"DATABASE_URL": "postgresql://chosen/db", "DB_URI": "postgresql://injected/db"}
    assert resolve_dsn(None, env) == "postgresql://chosen/db"


def test_db_uri_wins_over_discrete_variables() -> None:
    assert resolve_dsn(None, {"DB_URI": "postgresql://url/db", **DISCRETE}) == (
        "postgresql://url/db"
    )


def test_discrete_variables_are_assembled() -> None:
    dsn = resolve_dsn(None, DISCRETE)
    for fragment in ("host=db.example.com", "port=5432", "user=mk", "dbname=mk_tracking"):
        assert fragment in dsn


def test_sslmode_is_carried_through() -> None:
    assert "sslmode=require" in resolve_dsn(None, {**DISCRETE, "PGSSLMODE": "require"})


def test_empty_values_are_ignored() -> None:
    """A variable set to the empty string is not a configured target."""
    with pytest.raises(ConfigError):
        resolve_dsn(None, {"DATABASE_URL": "   "})


def test_no_configuration_raises_rather_than_defaulting_to_localhost() -> None:
    with pytest.raises(ConfigError) as caught:
        resolve_dsn(None, {})
    message = str(caught.value)
    assert "localhost" not in message
    for source in ("--database-url", "DATABASE_URL", "DB_URI", "PGHOST"):
        assert source in message


def test_partial_discrete_variables_are_not_enough() -> None:
    """A stray PGUSER or PGHOST from another tool must not become the target."""
    with pytest.raises(ConfigError):
        resolve_dsn(None, {"PGUSER": "mk"})
    with pytest.raises(ConfigError):
        resolve_dsn(None, {"PGHOST": "db.example.com"})


def test_pgdatabase_alone_is_a_unix_socket_target() -> None:
    """Omitting host and port is how libpq is told to use the local socket.

    A `brew install postgresql@16` setup with peer auth needs nothing but
    PGDATABASE, and psql connects to it; refusing that would reject a database
    that plainly works.
    """
    dsn = resolve_dsn(None, {"PGDATABASE": "mk_tracking"})
    assert "dbname=mk_tracking" in dsn
    assert "host=" not in dsn
    assert "port=" not in dsn


def test_describe_never_reveals_the_password() -> None:
    rendered = describe("postgresql://mk:hunter2@db.example.com:5432/mk_tracking")
    assert "hunter2" not in rendered
    assert "mk@db.example.com:5432/mk_tracking" == rendered


def test_describe_never_reveals_the_password_from_discrete_variables() -> None:
    assert "s3cret" not in describe(resolve_dsn(None, DISCRETE))


def test_describe_does_not_invent_a_port() -> None:
    """An unspecified port is filled from PGPORT at connection time.

    Printing a literal 5432 would name a port the connection may not use — the
    pooler case (PGPORT=6432) makes that actively misleading.
    """
    rendered = describe("postgresql://mk@db.example.com/mk_tracking")
    assert "5432" not in rendered
    assert "<default port>" in rendered


def test_describe_survives_an_unparseable_dsn() -> None:
    """A bad DSN must produce a message, not an exception mid-log."""
    assert describe("=") == "<unparseable connection string>"


def test_schema_defaults_and_overrides() -> None:
    assert resolve_schema(None, {}) == "mk_tracking"
    assert resolve_schema(None, {"MK_TRACKING_SCHEMA": "other"}) == "other"
    assert resolve_schema("flag", {"MK_TRACKING_SCHEMA": "env"}) == "flag"


@pytest.mark.parametrize(
    "hostile", ['a"b', "a;b", "a b", "a-b", "public.x", "x--", "9leading"]
)
def test_schema_rejects_anything_that_is_not_a_bare_identifier(hostile: str) -> None:
    """The schema is interpolated into SQL, so the guard is load-bearing."""
    with pytest.raises(ConfigError):
        resolve_schema(hostile, {})


@pytest.mark.parametrize("mixed_case", ["Analytics", "mk_Tracking", "MK_TRACKING"])
def test_schema_rejects_mixed_case(mixed_case: str) -> None:
    """Bare and quoted interpolation disagree on case, so allowing it is a trap.

    `--schema Analytics` would CREATE SCHEMA "Analytics", load into analytics
    (PostgreSQL folds the unquoted form), then verify "Analytics" and report
    every table empty.
    """
    with pytest.raises(ConfigError, match="lowercase"):
        resolve_schema(mixed_case, {})


def test_empty_schema_means_unset_rather_than_invalid() -> None:
    """An unset flag arrives as "" or None; both mean "use the default"."""
    assert resolve_schema("", {}) == "mk_tracking"
    assert resolve_schema(None, {"MK_TRACKING_SCHEMA": ""}) == "mk_tracking"
