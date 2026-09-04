"""Telemetry keeps the Sweep 2e `_slot` probe contract and adds the D-7 fuse (#3079 D11)."""
from __future__ import annotations

import datetime as _dt

import psycopg

from pg_md_cdc import publication, snapshot, telemetry
from pg_md_cdc.destination import Destination, DestinationSpec

SWEEP_KEYS = {"slot", "active", "wal_status", "restart_lsn", "confirmed_flush_lsn", "lag_bytes"}


class FakeRun:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


def test_slot_probe_is_none_when_the_slot_does_not_exist(fresh_db, names):
    assert telemetry.slot_probe(fresh_db, names["slot"]) is None


def test_slot_probe_carries_every_key_sweep_2e_reads(fresh_db, names):
    snapshot.create_slot_with_snapshot(fresh_db, names["slot"]).close()
    probe = telemetry.slot_probe(fresh_db, names["slot"])
    assert SWEEP_KEYS <= set(probe)
    assert probe["slot"] == names["slot"]
    assert probe["wal_status"] == "reserved"
    assert isinstance(probe["lag_bytes"], int)
    assert "max_slot_wal_keep_size_bytes" in probe


def test_emit_probe_uses_the_entity_and_unit_the_sweep_queries(fresh_db, names):
    snapshot.create_slot_with_snapshot(fresh_db, names["slot"]).close()
    run = FakeRun()
    extra = telemetry.emit_probe(run, telemetry.slot_probe(fresh_db, names["slot"]),
                                 {"selected_tables": 3, "published_tables": 3,
                                  "destination_tables": 3, "checkpoint_lsn": 42,
                                  "checkpoint_age_s": 1.0}, pull_ms=5)
    (event,) = run.events
    assert event["entity"] == "_slot" and event["unit"] == "probe"
    assert SWEEP_KEYS <= set(event["extra"])
    assert extra["selected_tables"] == 3 and extra["checkpoint_lsn"] == 42


def test_fuse_bytes_reads_units_and_the_unlimited_sentinel():
    assert telemetry._fuse_bytes(("2048", "MB")) == 2048 * 1024 ** 2
    assert telemetry._fuse_bytes(("-1", "MB")) == -1
    assert telemetry._fuse_bytes(("16", "8kB")) == 16 * 8192
    assert telemetry._fuse_bytes(None) is None


def test_checkpoint_age_treats_a_naive_timestamp_as_utc():
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    age = telemetry.checkpoint_age_s(now)
    assert 0 <= age < 5
    assert telemetry.checkpoint_age_s(None) is None


def test_reconcile_compares_counts_and_skips_the_big_ones(fresh_db, names, duckdb_path, tmp_path):
    dest = Destination(DestinationSpec(kind="duckdb", database="t", path=duckdb_path,
                                       pipelines_dir=str(tmp_path / "dlt")))
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.small (id int primary key)")
        c.execute("insert into public.small select generate_series(1, 7)")
        c.execute("create table public.big (id int primary key)")
        c.execute("insert into public.big select generate_series(1, 100)")
        c.execute("analyze")
    with snapshot.baseline_resource(fresh_db, ("public", "small"), ["id"]) as res:
        dest.pipeline_for_schema("public", "public").run(res)

    rows = telemetry.reconcile_counts(fresh_db, dest, [("public", "small"), ("public", "big")],
                                      {}, threshold=50)
    by_table = {r["table"]: r for r in rows}
    assert by_table["small"] == {"schema": "public", "table": "small", "skipped": False,
                                 "source_rows": 7, "destination_rows": 7, "delta": 0}
    assert by_table["big"]["skipped"] is True and "threshold" in by_table["big"]["reason"]

    run = FakeRun()
    telemetry.emit_reconcile(run, rows)
    units = {e["unit"] for e in run.events}
    assert units == {"reconcile"}
    assert any(e["extra"].get("skipped") for e in run.events)


def test_a_table_missing_at_the_destination_reports_none_not_zero(fresh_db, duckdb_path, tmp_path):
    dest = Destination(DestinationSpec(kind="duckdb", database="t", path=duckdb_path,
                                       pipelines_dir=str(tmp_path / "dlt")))
    dest.ensure_runtime_tables()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.only_source (id int primary key)")
    (row,) = telemetry.reconcile_counts(fresh_db, dest, [("public", "only_source")], {})
    assert row["destination_rows"] is None and row["delta"] is None
