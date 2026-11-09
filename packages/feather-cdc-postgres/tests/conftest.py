"""Test fixtures for feather_cdc_postgres (#3079).

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

import datetime as _dt
import json
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
    slot/publication names — use the `names` fixture). Dropped afterwards.

    A tick is *meant* to leave its slot alive — that is how the next tick resumes — so the
    teardown drops this database's slots itself. What it refuses to tolerate is a slot that
    outlives the database it belongs to: nothing drops it afterwards, and it retains WAL for
    the whole cluster until `max_replication_slots=20` is exhausted. That one fails here,
    loudly, on the test that caused it."""
    import psycopg
    name = "t_" + uuid.uuid4().hex[:10]
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        pre_existing = {r[0] for r in c.execute("select slot_name from pg_replication_slots")}
        c.execute(f'create database "{name}"')
    yield pg_dsn.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        # this database's own slots: expected, and they must go before `drop database` can run
        for (slot,) in c.execute("select slot_name from pg_replication_slots where database = %s", (name,)).fetchall():
            c.execute("select pg_drop_replication_slot(%s)", (slot,))
        c.execute(f'drop database if exists "{name}" with (force)')
        strays = {r[0] for r in c.execute("select slot_name from pg_replication_slots")} - pre_existing
    assert not strays, (f"replication slot(s) {sorted(strays)} outlived the database they were "
                        f"created for — nothing will ever drop them, and the cluster retains "
                        f"WAL for each until max_replication_slots is exhausted")


@pytest.fixture
def names():
    """Unique slot/publication names for one test."""
    tag = uuid.uuid4().hex[:8]
    return {"slot": f"s_{tag}", "publication": f"p_{tag}", "registration": f"r_{tag}"}


@pytest.fixture
def duckdb_path(tmp_path):
    return str(tmp_path / "dest.duckdb")


# --------------------------------------------------------------- reading a WAL-only run log

_EPOCH = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)


def _ran_at(records: list[dict]) -> _dt.datetime:
    """When this run finished (or started, if it never recorded a finish)."""
    for kind, field in (("run_finish", "finished_at"), ("run_start", "started_at")):
        for rec in records:
            if rec.get("kind") == kind and rec.get(field):
                return _dt.datetime.fromisoformat(rec[field])
    return _EPOCH


def wal_runs(wal_dir, extractor) -> list[tuple[str, list[dict]]]:
    """`(run_id, records)` per run in one extractor's WAL directory, oldest run FIRST.

    Ordered by the `finished_at` the run recorded — never by filename. A WAL file is named
    `<run_id>.jsonl` and `new_run_id` is `<UTC second>-<extractor>-<6 random hex>`, so the two
    runs a test makes back-to-back normally share a timestamp and sort by the coin flip in
    their suffix (#89): `sorted(glob(...))[-1]` returned the FIRST run's outcome half the time.
    """
    runs = []
    for path in pathlib.Path(wal_dir, extractor).glob("*.jsonl"):
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        if records:
            runs.append((_ran_at(records), path.stem, records))
    runs.sort(key=lambda run: run[0])
    stamps = [ran_at for ran_at, _, _ in runs]
    assert len(set(stamps)) == len(stamps), (
        "two runs share a timestamp, so which one is 'the last run' is a guess: "
        + ", ".join(f"{run_id}@{ran_at.isoformat()}" for ran_at, run_id, _ in runs))
    return [(run_id, records) for _, run_id, records in runs]


def wal_records(wal_dir, extractor) -> list[dict]:
    """Every record in one extractor's WAL, run by run, oldest run first."""
    return [rec for _, records in wal_runs(wal_dir, extractor) for rec in records]


def finish_record(wal_dir, extractor="test_cdc") -> dict:
    """The `run_finish` the LAST run wrote — what `control.run` would hold for it."""
    finishes = [r for r in wal_records(wal_dir, extractor) if r.get("kind") == "run_finish"]
    assert finishes, f"no run finished in {wal_dir}/{extractor}"
    return finishes[-1]
