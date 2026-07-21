"""Shared DB bootstrap for the test suite.

Owns the DSN constants, live-reachability probes, the per-flavor
`CREATE DATABASE IF NOT EXISTS` bootstrap, marker factories used by
test files, and the session-start banner shown when a server is down.

Everything here assumes brew-managed Postgres + MySQL on localhost with
default trust/peer auth. Not designed for credentialed production DBs.
"""

from __future__ import annotations

import mysql.connector
import psycopg2
import pytest

# ---------------------------------------------------------------------------
# Connection constants
# ---------------------------------------------------------------------------

TARGET_DB = "feather_test"

POSTGRES_DSN = f"dbname={TARGET_DB} host=localhost"
POSTGRES_ADMIN_DSN = "dbname=postgres host=localhost"

MYSQL_CONN_KWARGS = {"host": "localhost", "user": "root", "database": TARGET_DB}
MYSQL_ADMIN_KWARGS = {"host": "localhost", "user": "root"}


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def postgres_check() -> tuple[bool, str | None]:
    """Return (True, None) if `feather_test` is reachable on Postgres."""
    try:
        conn = psycopg2.connect(POSTGRES_DSN)
        conn.close()
        return (True, None)
    except Exception as exc:
        return (False, str(exc))


def mysql_check() -> tuple[bool, str | None]:
    """Return (True, None) if `feather_test` is reachable on MySQL."""
    try:
        conn = mysql.connector.connect(**MYSQL_CONN_KWARGS)
        conn.close()
        return (True, None)
    except Exception as exc:
        return (False, str(exc))


# ---------------------------------------------------------------------------
# Per-flavor bootstrap
# ---------------------------------------------------------------------------


def _ensure_postgres_database() -> tuple[bool, str | None]:
    """Create `feather_test` on Postgres if it doesn't exist.

    CREATE DATABASE cannot run inside a transaction, hence autocommit=True.
    Any driver error is captured as the failure reason — never re-raised.
    """
    try:
        admin = psycopg2.connect(POSTGRES_ADMIN_DSN)
        admin.autocommit = True
        cur = admin.cursor()
        try:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TARGET_DB,))
            if cur.fetchone() is None:
                # Identifier cannot be parameterized; TARGET_DB is a fixed const.
                cur.execute(f'CREATE DATABASE "{TARGET_DB}"')
        finally:
            cur.close()
            admin.close()
    except Exception as exc:
        return (False, str(exc))

    return postgres_check()


def _ensure_mysql_database() -> tuple[bool, str | None]:
    """Create `feather_test` on MySQL if it doesn't exist.

    Connects with no `database=` kwarg to avoid the chicken-and-egg of
    asking for a DB that may not yet exist. Any driver error is captured
    as the failure reason — never re-raised.
    """
    try:
        admin = mysql.connector.connect(**MYSQL_ADMIN_KWARGS)
        cur = admin.cursor()
        try:
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = %s",
                (TARGET_DB,),
            )
            if cur.fetchone() is None:
                cur.execute(f"CREATE DATABASE `{TARGET_DB}`")
        finally:
            cur.close()
            admin.close()
    except Exception as exc:
        return (False, str(exc))

    return mysql_check()


# ---------------------------------------------------------------------------
# Combined session-start entry point
# ---------------------------------------------------------------------------


def ensure_bootstrap_databases() -> dict[str, tuple[bool, str | None]]:
    """Run both flavor bootstraps. Returns per-flavor (ok, reason).

    Called once from `pytest_sessionstart`. Per-flavor logic is
    idempotent: exists → no CREATE; missing → one CREATE.
    """
    return {
        "postgres": _ensure_postgres_database(),
        "mysql": _ensure_mysql_database(),
    }


# ---------------------------------------------------------------------------
# Banner shown at session start when a server is down
# ---------------------------------------------------------------------------


_BREW_COMMANDS = {
    "postgres": "brew services start postgresql@17",
    "mysql": "brew services start mysql",
}


def format_banner(results: dict[str, tuple[bool, str | None]]) -> str | None:
    """Return the session-start banner text, or None if both flavors are OK."""
    failed = [(flavor, reason) for flavor, (ok, reason) in results.items() if not ok]
    if not failed:
        return None

    lines = ["", "=" * 72, "feather-flow test suite: local DB unavailable"]
    for flavor, reason in failed:
        lines.append(f"  {flavor}: {reason}")
        lines.append(f"    fix:  {_BREW_COMMANDS[flavor]}")
    lines.append("DB-gated tests will skip. Suite will still exit 0.")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Marker factories
# ---------------------------------------------------------------------------


def postgres_marker():
    """Build a fresh `pytest.mark.skipif` from a live probe.

    Called at test-file import time — which is *after* pytest_sessionstart
    has run bootstrap, so the probe sees the live DB.
    """
    ok, reason = postgres_check()
    return pytest.mark.skipif(not ok, reason=reason or "PostgreSQL not available")


def mysql_marker():
    """Build a fresh `pytest.mark.skipif` from a live probe.

    Called at test-file import time — which is *after* pytest_sessionstart
    has run bootstrap, so the probe sees the live DB.
    """
    ok, reason = mysql_check()
    return pytest.mark.skipif(not ok, reason=reason or "MySQL not available")
