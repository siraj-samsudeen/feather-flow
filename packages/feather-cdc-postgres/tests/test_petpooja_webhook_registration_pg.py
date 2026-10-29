"""The Petpooja webhook registration, end to end against the receiver's own DDL (#4272 D27).

Applies `petpooja_webhook/store.py`'s SCHEMA_DDL to a throwaway Postgres, compiles the shipped
registration exactly as `run` does, and drains it twice into duckdb: the table must land as
`webhook.bills` with what silver reads, the Postgres-only columns (the URL secret, the body token,
the guest's details) must never reach the destination, and `_dlt_loads` must exist for the
runner's completion proof.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import pathlib
import sys
import types

import duckdb
import psycopg

from pg_md_cdc import discovery, manifest as mf, runner
from pg_md_cdc.destination import Destination, DestinationSpec
from pg_md_cdc.ownership import OwnerSession
from pg_md_cdc.selectors import compile as compile_selection

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = mf.load(str(ROOT / "pg_md_cdc/registrations/petpooja_webhook.yml"))


def _store():
    """The receiver's store module, for its DDL and redaction only: its connection pool library
    is not a pg_md_cdc dependency, so a stand-in is registered when it is absent."""
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        sys.modules["psycopg_pool"] = types.SimpleNamespace(ConnectionPool=object)
    spec =importlib.util.spec_from_file_location("ppw_store", ROOT / "petpooja_webhook/store.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Run:
    def __init__(self):
        self.events, self.finished = [], None

    def event(self, **kw):
        self.events.append(kw)

    def finish(self, status, **kw):
        self.finished = (status, kw)


def _tick(cfg, relations):
    run = _Run()
    with OwnerSession(cfg.source_dsn, cfg.slot) as owner:
        assert owner.held
        return runner.run_relations(cfg, relations, run=run, owner=owner)


def _insert(dsn, store, n, *, wrapped=False):
    """One delivery as the receiver stores it. `wrapped` puts the bill inside a JSON array, a
    shape nobody has seen: its token and guest block must still never leave Postgres."""
    body = {"token": "body-token", "properties": {"Customer": {"phone": "9999"},
                                                   "Order": {"orderID": str(n)}}}
    if wrapped:
        body = [body]
    payload = store.warehouse_payload(body)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO petpooja.bills (raw_body, payload, body_sha256, source_ip, headers, "
                  "request_path) VALUES (%s, %s, %s, '1.2.3.4', '{}', '/pp/the-url-secret')",
                  (json.dumps(body), None if payload is None else json.dumps(payload), f"{n:064d}"))


def test_registration_static_shape():
    assert mf.errors(mf.validate_static(MANIFEST)) == []
    assert MANIFEST.destination.database == "bronze_petpooja"
    assert MANIFEST.destination.owner == "petpooja_extract"
    assert MANIFEST.destination.target_schema("petpooja", "bills") == "webhook"
    assert MANIFEST.control.extractor == "petpooja_webhook_cdc"   # the runner listens for it
    assert set(MANIFEST.source.deny_columns["petpooja.bills"]) == set(_store().POSTGRES_ONLY_COLUMNS)


def test_bills_land_as_webhook_bills_without_the_postgres_only_columns(fresh_db, names, tmp_path):
    store = _store()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute(store.SCHEMA_DDL)
    _insert(fresh_db, store, 1)
    _insert(fresh_db, store, 3, wrapped=True)

    path = str(tmp_path / "dest.duckdb")
    dest = Destination(DestinationSpec(kind="duckdb", database="bronze_petpooja", path=path,
                                       pipelines_dir=str(tmp_path / "dlt")))
    cfg = runner.config_from_manifest(MANIFEST, dsn=fresh_db, destination=dest)
    cfg = dataclasses.replace(cfg, slot=names["slot"], publication=names["publication"],
                              registration=names["registration"])
    plan = compile_selection(MANIFEST, discovery.discover(fresh_db), [])
    assert mf.errors(list(plan.problems)) == []
    assert [(r.schema, r.table, r.target_schema) for r in plan.relations] == [("petpooja", "bills", "webhook")]

    first = _tick(cfg, plan.relations)       # the snapshot
    _insert(fresh_db, store, 2)
    _insert(fresh_db, store, 4, wrapped=True)
    second = _tick(cfg, plan.relations)      # the stream
    assert first["outcome"]["status"] == "ok" and second["outcome"]["status"] == "ok", (first, second)

    con = duckdb.connect(path)
    try:
        cols = {r[0] for r in con.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'webhook' and table_name = 'bills'").fetchall()}
        assert {"event_id", "received_at", "_loaded_at", "payload", "body_sha256"} <= cols
        assert not cols & set(store.POSTGRES_ONLY_COLUMNS), cols
        rows = con.execute("select event_id, payload, received_at = _loaded_at from webhook.bills "
                           "order by event_id").fetchall()
        assert len(rows) == 4 and all(r[2] for r in rows)
        assert sum(r[1] is None for r in rows) == 2   # the array-wrapped pair: no warehouse payload
        assert all(r[0] >= store.FIRST_NEW_EVENT_ID for r in rows)
        dump = json.dumps([str(v) for r in con.execute("select * from webhook.bills").fetchall() for v in r])
        assert "body-token" not in dump and "9999" not in dump and "the-url-secret" not in dump
        assert con.execute("select count(*) from webhook._dlt_loads").fetchone()[0] >= 2
    finally:
        con.close()
