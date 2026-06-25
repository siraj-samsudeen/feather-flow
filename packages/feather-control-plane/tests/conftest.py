"""Test fixtures. The integration suite needs a real Postgres; we stand one up in Docker
(postgres:16 — same wire protocol as Railway PG, the only deploy difference is the DSN).
Skips cleanly if Docker is unavailable, so the pure-logic unit tests still run anywhere.

Override with CONTROL_PG_DSN to point the integration suite at an existing Postgres (e.g. the
live Railway instance for the deferred live-verification step).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

_PORT = 54329
_CONTAINER = "cp-test-pg"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0


@pytest.fixture(scope="session")
def pg_dsn():
    """A DSN to a clean Postgres for the whole session."""
    env_dsn = os.environ.get("CONTROL_PG_DSN")
    if env_dsn:
        yield env_dsn
        return

    if not _docker_ok():
        pytest.skip("Docker unavailable and CONTROL_PG_DSN unset — skipping PG integration tests")

    subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", _CONTAINER,
         "-e", "POSTGRES_PASSWORD=cp", "-e", "POSTGRES_USER=cp", "-e", "POSTGRES_DB=control",
         "-p", f"{_PORT}:5432", "postgres:16"],
        check=True, capture_output=True,
    )
    dsn = f"postgresql://cp:cp@127.0.0.1:{_PORT}/control"
    import psycopg
    deadline = time.time() + 60
    while True:
        try:
            psycopg.connect(dsn, connect_timeout=3).close()
            break
        except Exception:
            if time.time() > deadline:
                subprocess.run(["docker", "logs", _CONTAINER], capture_output=False)
                subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
                pytest.fail("Postgres container did not become ready in 60s")
            time.sleep(1)
    yield dsn
    subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)


@pytest.fixture
def fresh_schema(pg_dsn):
    """Drop + recreate the control schema before each integration test (clean slate)."""
    import psycopg

    from control_plane import store
    with psycopg.connect(pg_dsn) as c:
        with c.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS control CASCADE")
        c.commit()
        store.ensure_schema(c)
    return pg_dsn
