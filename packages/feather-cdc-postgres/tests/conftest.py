"""Test fixtures for pg_md_cdc (#3079).

Integration tests need a real Postgres with wal_level=logical. Resolution order:

  1. PG_MD_CDC_TEST_DSN — an existing Postgres you own for testing. Never a production DB:
     the suite creates/drops slots, publications, schemas and tables.
  2. A local Postgres binary (Homebrew `postgresql@N` on the dev Macs, or PATH): initdb into a
     short /tmp dir, start on a throwaway port with wal_level=logical, stop + delete afterwards.
  3. Otherwise skip — the pure-logic unit tests still run anywhere.

Destination in tests is dlt's `duckdb` destination (a temp file). It is the same load path
MotherDuck takes minus credentials, so merge/hard-delete/schema-evolution behaviour is
exercised for real. Pattern borrowed from control_plane/tests/conftest.py (PR #3093).
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_PORT = int(os.environ.get("PG_MD_CDC_TEST_PORT", "54331"))


def _pg_bin() -> pathlib.Path | None:
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
    """DSN of a Postgres with wal_level=logical, superuser, for the whole session."""
    env_dsn = os.environ.get("PG_MD_CDC_TEST_DSN")
    if env_dsn:
        yield env_dsn
        return
    pg = _pg_bin()
    if pg is None:
        pytest.skip("no PG_MD_CDC_TEST_DSN and no local Postgres binary — skipping integration tests")
    # Short /tmp dir: macOS caps a unix-socket path at 104 bytes. LC_ALL=C: an odd locale makes
    # the postmaster load CoreFoundation and die "became multithreaded during startup".
    data = pathlib.Path(tempfile.mkdtemp(prefix="cdc-pg-", dir="/tmp"))
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    subprocess.run([str(pg / "initdb"), "-D", str(data), "-U", "cdc", "--auth=trust",
                    "-E", "UTF8", "--locale=C"], check=True, capture_output=True, env=env)
    log = data / "pg.log"

    def _stop():
        subprocess.run([str(pg / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(data, ignore_errors=True)

    opts = (f"-p {_PORT} -c unix_socket_directories={data} -c listen_addresses=127.0.0.1 "
            "-c wal_level=logical -c max_replication_slots=20 -c max_wal_senders=20")
    started = subprocess.run([str(pg / "pg_ctl"), "-D", str(data), "-l", str(log), "-w",
                              "-o", opts, "start"], capture_output=True, text=True, env=env)
    if started.returncode != 0:
        tail = log.read_text(encoding="utf-8")[-800:] if log.exists() else started.stderr[-800:]
        _stop()
        pytest.fail(f"local Postgres failed to start on :{_PORT}:\n{tail}")
    dsn = f"postgresql://cdc@127.0.0.1:{_PORT}/postgres"
    _wait_ready(dsn, _stop)
    yield dsn
    _stop()


@pytest.fixture
def fresh_db(pg_dsn):
    """A brand-new database per test (slots are cluster-wide, so tests must also use unique
    slot/publication names — use the `names` fixture). Dropped afterwards."""
    import psycopg
    name = "t_" + uuid.uuid4().hex[:10]
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    yield pg_dsn.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        # drop any slot the test left behind (a leaked slot retains WAL for the whole cluster)
        for (slot,) in c.execute("select slot_name from pg_replication_slots where database = %s", (name,)).fetchall():
            c.execute("select pg_drop_replication_slot(%s)", (slot,))
        c.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def names():
    """Unique slot/publication names for one test."""
    tag = uuid.uuid4().hex[:8]
    return {"slot": f"s_{tag}", "publication": f"p_{tag}", "registration": f"r_{tag}"}


@pytest.fixture
def duckdb_path(tmp_path):
    return str(tmp_path / "dest.duckdb")
