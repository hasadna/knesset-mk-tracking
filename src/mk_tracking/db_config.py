#!/usr/bin/env python3
"""Resolve the PostgreSQL connection settings for the db/ scripts.

One place decides where the database is, so pointing any script at a different
server — a colleague's laptop today, a hosted instance later — is configuration
rather than a code change. Nothing here has a default host, port, user or
password: an unconfigured environment raises instead of quietly reaching for
localhost.

Precedence, first match wins:

1. an explicit ``--database-url`` passed on the command line
2. ``$DATABASE_URL``
3. ``$DB_URI`` — the name the hasadna deployment platform injects
4. the discrete ``PG*`` variables (``PGHOST``, ``PGPORT``, ``PGUSER``,
   ``PGPASSWORD``, ``PGDATABASE``, ``PGSSLMODE``), assembled into a DSN
5. a `ConfigError` naming them all

(3) exists because a hosted database is usually handed over as separate fields
rather than a URL, and because splitting the password out keeps it off the
command line. libpq would read these variables on its own, but only when *no*
DSN is given at all; assembling them explicitly means the same precedence
applies everywhere and the resolved target can be printed back.

Moving to a hosted instance needs no change here — set ``PGSSLMODE=require``
(or ``?sslmode=require`` on the URL) alongside the new host.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from psycopg.conninfo import conninfo_to_dict, make_conninfo

# A schema name is interpolated into SQL rather than passed as a parameter, so it
# is restricted to characters that cannot end a quoted identifier.
#
# Lowercase only, and that is not cosmetic. Some call sites interpolate the name
# bare (`TRUNCATE {schema}.{table}`) and some quote it (`"{schema}"."{table}"`);
# PostgreSQL folds the unquoted form to lowercase and preserves the quoted one,
# so `--schema Analytics` would create "Analytics", load into analytics, and then
# verify "Analytics" and report every table empty. Refusing mixed case removes
# the whole class of mismatch instead of relying on every call site agreeing.
SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
DEFAULT_SCHEMA = "mk_tracking"

# Whole connection strings, in precedence order. DATABASE_URL is the name this
# project documents; DB_URI is supplied by the deployment platform. Reading both
# means a deployed server needs no extra configuration, while an operator who
# sets DATABASE_URL explicitly still overrides whatever the platform injected.
URL_VARIABLES = ("DATABASE_URL", "DB_URI")

# PG* variable -> libpq keyword. PGPASSFILE and PGSERVICE are deliberately absent:
# libpq handles both itself once a DSN exists, and neither belongs in a DSN string.
PG_ENV_KEYWORDS = {
    "PGHOST": "host",
    "PGPORT": "port",
    "PGUSER": "user",
    "PGPASSWORD": "password",
    "PGDATABASE": "dbname",
    "PGSSLMODE": "sslmode",
}


class ConfigError(RuntimeError):
    """The database target could not be determined from the environment."""


def resolve_dsn(
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return a libpq connection string, or raise `ConfigError`."""
    env = os.environ if env is None else env

    if explicit:
        return explicit

    for variable in URL_VARIABLES:
        url = env.get(variable, "").strip()
        if url:
            return url

    parts = {
        keyword: env[variable].strip()
        for variable, keyword in PG_ENV_KEYWORDS.items()
        if env.get(variable, "").strip()
    }
    # PGDATABASE is the minimum: it is what distinguishes a configured target
    # from a stray PGUSER left over from some other tool. Host and port stay
    # optional on purpose — omitting both is how libpq is told to use the local
    # Unix socket, which is the normal shape of a `brew install postgresql@16`
    # setup, and refusing it would reject a database `psql` connects to fine.
    if "dbname" in parts:
        return make_conninfo(**parts)

    raise ConfigError(
        "no database configured. Set one of:\n"
        "  --database-url postgresql://user:password@host:5432/dbname\n"
        "  DATABASE_URL=postgresql://user:password@host:5432/dbname\n"
        "  DB_URI=postgresql://user:password@host:5432/dbname\n"
        "  PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE (PGDATABASE is the\n"
        "    minimum; omit host and port to use the local Unix socket)\n"
        "See .env.example. Note that .env is not read automatically —\n"
        "export it first:  set -a; . .env; set +a"
    )


def resolve_schema(explicit: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Return the schema holding the tables, validated as a bare identifier."""
    env = os.environ if env is None else env
    schema = explicit or env.get("MK_TRACKING_SCHEMA", "").strip() or DEFAULT_SCHEMA
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ConfigError(
            f"invalid schema name {schema!r}: expected lowercase letters, digits and "
            "underscores, starting with a letter or underscore. PostgreSQL folds "
            "unquoted identifiers to lowercase, so a name with capitals would not "
            "refer to the same schema everywhere."
        )
    return schema


def describe(dsn: str) -> str:
    """A human-readable target for logs. Never includes the password."""
    try:
        parts = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001 - an unparseable DSN must not leak into a log
        return "<unparseable connection string>"
    user = parts.get("user", "")
    # Never invent a value libpq has not been given: an unspecified port is
    # filled from PGPORT at connection time, so printing a literal 5432 here
    # can name a port the connection will not use.
    host = parts.get("host", "<default host>")
    port = parts.get("port", "<default port>")
    dbname = parts.get("dbname", "<default database>")
    sslmode = parts.get("sslmode")
    target = f"{user}@{host}:{port}/{dbname}" if user else f"{host}:{port}/{dbname}"
    return f"{target} (sslmode={sslmode})" if sslmode else target
