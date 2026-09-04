"""Test fixtures. The integration suite needs a real Postgres. Resolution order:

  1. CONTROL_PG_DSN — an existing Postgres (e.g. the live Railway instance for a deliberate
     live-verification step). Note `fresh_schema` DROPs the control schema, so never point this
     at production casually.
  2. Docker `postgres:16` — same wire protocol as Railway PG; the only deploy difference is the DSN.
  3. A local Postgres binary (Homebrew `postgresql@N` on the dev Macs): initdb into a temp dir,
     start on a throwaway port, stop and delete afterwards. No daemon, nothing left behind.
  4. Otherwise skip — the pure-logic unit tests still run anywhere.

Added (3) for #3091: the concurrency regression there can only be proven against a real server,
and a test that silently skips on every dev machine is not a guard.
"""
from __future__ import annotations

import os
import pathlib
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


def _pg_bin() -> pathlib.Path | None:
    """A local Postgres install: PATH first, then Homebrew's keg-only postgresql@N."""
    found = shutil.which("pg_ctl")
    if found:
        return pathlib.Path(found).parent
    for cand in sorted(pathlib.Path("/opt/homebrew/opt").glob("postgresql@*/bin"), reverse=True):
        if (cand / "pg_ctl").exists():
            return cand
    return None


def _wait_ready(dsn: str, on_fail) -> None:
    import psycopg
    deadline = time.time() + 60
    while True:
        try:
            psycopg.connect(dsn, connect_timeout=3).close()
            return
        except Exception:  # noqa: BLE001
            if time.time() > deadline:
                on_fail()
                pytest.fail("Postgres did not become ready in 60s")
            time.sleep(1)


@pytest.fixture(scope="session")
def pg_dsn():
    """A DSN to a clean Postgres for the whole session."""
    env_dsn = os.environ.get("CONTROL_PG_DSN")
    if env_dsn:
        yield env_dsn
        return

    if _docker_ok():
        subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
        subprocess.run(
            ["docker", "run", "-d", "--name", _CONTAINER,
             "-e", "POSTGRES_PASSWORD=cp", "-e", "POSTGRES_USER=cp", "-e", "POSTGRES_DB=control",
             "-p", f"{_PORT}:5432", "postgres:16"],
            check=True, capture_output=True,
        )
        dsn = f"postgresql://cp:cp@127.0.0.1:{_PORT}/control"
        _wait_ready(dsn, lambda: subprocess.run(["docker", "rm", "-f", _CONTAINER],
                                                capture_output=True))
        yield dsn
        subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
        return

    # No Docker (the dev Macs): a throwaway local cluster. initdb into a short /tmp dir -- macOS
    # caps a unix-socket path at 104 bytes and $TMPDIR eats ~60 -- and LC_ALL=C, because an odd
    # locale makes the postmaster load CoreFoundation and die "became multithreaded during startup".
    pg = _pg_bin()
    if pg is None:
        pytest.skip("no CONTROL_PG_DSN, no Docker, no local Postgres — skipping PG integration tests")
    import tempfile
    data = pathlib.Path(tempfile.mkdtemp(prefix="cp-pg-", dir="/tmp"))
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    subprocess.run([str(pg / "initdb"), "-D", str(data), "-U", "cp", "--auth=trust",
                    "-E", "UTF8", "--locale=C"], check=True, capture_output=True, env=env)
    log = data / "pg.log"

    def _stop():
        subprocess.run([str(pg / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(data, ignore_errors=True)

    started = subprocess.run(
        [str(pg / "pg_ctl"), "-D", str(data), "-l", str(log), "-w",
         "-o", f"-p {_PORT} -c unix_socket_directories={data} -c listen_addresses=127.0.0.1",
         "start"], capture_output=True, text=True, env=env)
    if started.returncode != 0:
        tail = log.read_text(encoding="utf-8")[-800:] if log.exists() else started.stderr[-800:]
        _stop()
        pytest.fail(f"local Postgres failed to start on :{_PORT}:\n{tail}")
    dsn = f"postgresql://cp@127.0.0.1:{_PORT}/postgres"
    _wait_ready(dsn, _stop)
    yield dsn
    _stop()


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
