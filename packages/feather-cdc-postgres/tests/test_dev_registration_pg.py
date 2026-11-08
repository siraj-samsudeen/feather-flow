"""A `dev` registration end to end, run exactly as a dev deployment would.

The first tick is a full baseline — `person` with its built-ins, `identifier_system` — into the
dev mirror, logged under the dev extractor; the next tick drains a change. The deny list is
production's, so the password hash and the identity tables never reach the dev copy either.
RAILWAY_ENVIRONMENT_NAME=dev throughout: a production registration here would refuse.
"""
from __future__ import annotations

import pathlib

import duckdb
import psycopg

from feather_cdc_postgres import cli

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "featherbase_namespaced_schema.sql"
DEV_MANIFEST = str(pathlib.Path(__file__).parent / "fixtures" / "registrations" / "demo_app_dev.yml")


def _rows(path, sql):
    con = duckdb.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_the_first_dev_tick_baselines_person_and_the_next_drains(fresh_db, duckdb_path, tmp_path,
                                                                  monkeypatch):
    from conftest import finish_record

    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute(FIXTURE.read_text(encoding="utf-8"))
        c.execute("insert into featherbase.person values ('Administrator', 'h1'), ('Guest', 'h2'), ('psn_a', 'h3')")
        c.execute("insert into featherbase.identifier_system values ('employee_code')")
        c.execute("insert into featherbase.service_account values ('sa_ci')")
    wal = tmp_path / "wal"
    monkeypatch.setenv("DEMO_APP_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.setenv("CONTROL_WAL_DIR", str(wal))
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "dev")
    for var in ("CDC_LOG_PG_DSN", "CONTROL_PG_DSN", "MOTHERDUCK_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    assert cli.main(["run", DEV_MANIFEST, "--dev"]) == 0
    assert finish_record(str(wal), extractor="demo_app_dev")["status"] == "ok"
    assert sorted(r[0] for r in _rows(duckdb_path, "select row_id from access.person")) == [
        "Administrator", "Guest", "psn_a"]
    assert _rows(duckdb_path, "select row_id from access.identifier_system") == [("employee_code",)]
    # the password hash never reaches the dev mirror (#4346: same deny as production)
    assert _rows(duckdb_path, "select count(*) from information_schema.columns "
                              "where table_name = 'person' and column_name = 'password_hash'") == [(0,)]
    # a denied identity table never reaches the dev mirror
    assert _rows(duckdb_path, "select count(*) from information_schema.tables "
                              "where table_name = 'service_account'") == [(0,)]
    assert _rows(duckdb_path, "select registration, slot from _cdc_runtime.checkpoint") == [
        ("demo_app_dev", "demo_app_dev_cdc_slot")]

    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into featherbase.person values ('psn_b', 'h4')")
    assert cli.main(["run", DEV_MANIFEST, "--dev"]) == 0
    assert finish_record(str(wal), extractor="demo_app_dev")["status"] == "ok"
    assert _rows(duckdb_path, "select count(*) from access.person") == [(4,)]
