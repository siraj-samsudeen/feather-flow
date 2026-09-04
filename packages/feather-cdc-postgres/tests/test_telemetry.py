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
    # unlimited reads as None, NOT -1: cdc_alarms compares lag against a fraction of the
    # fuse, and -1 makes every lag "over" it (a phantom orange on every sweep).
    assert telemetry._fuse_bytes(("-1", "MB")) is None
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
                                 "source_rows": 7, "destination_rows": 7, "delta": 0,
                                 "match": True}
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


def test_a_forced_count_mismatch_is_an_error_event_not_an_ok_with_a_delta(
        fresh_db, duckdb_path, tmp_path):
    """Reconciliation only earns its keep if a disagreement is visible where the sweep looks:
    the event STATUS, not a number buried in `extra`."""
    import duckdb

    dest = Destination(DestinationSpec(kind="duckdb", database="t", path=duckdb_path,
                                       pipelines_dir=str(tmp_path / "dlt")))
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.drifted (id int primary key)")
        c.execute("insert into public.drifted select generate_series(1, 5)")
    with snapshot.baseline_resource(fresh_db, ("public", "drifted"), ["id"]) as res:
        dest.pipeline_for_schema("public", "public").run(res)

    con = duckdb.connect(duckdb_path)
    # a row the source never had (_dlt_load_id/_dlt_id are NOT NULL at the destination)
    con.execute("insert into public.drifted (id, _dlt_load_id, _dlt_id) "
                "values (999, 'hand-inserted', 'hand-inserted')")
    con.close()

    (row,) = telemetry.reconcile_counts(fresh_db, dest, [("public", "drifted")], {})
    assert (row["source_rows"], row["destination_rows"], row["delta"]) == (5, 6, -1)
    assert row["match"] is False

    run = FakeRun()
    telemetry.emit_reconcile(run, [row])
    (event,) = run.events
    assert event["status"] == "error" and event["unit"] == "reconcile"
    assert "source 5 vs destination 6" in event["error_detail"]
    assert event["extra"]["match"] is False


def test_an_empty_source_table_with_no_destination_table_yet_ties(fresh_db, duckdb_path,
                                                                  tmp_path):
    """dlt creates a table on its FIRST row. Calling "0 rows at the source, no table at the
    destination" a mismatch fired an EV_ERROR every tick for every still-empty selected table
    (9 of 11 on a fresh control plane) — the positive control below is the non-empty case,
    which must still be an error."""
    dest = Destination(DestinationSpec(kind="duckdb", database="t", path=duckdb_path,
                                       pipelines_dir=str(tmp_path / "dlt")))
    dest.ensure_runtime_tables()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.still_empty (id int primary key)")
        c.execute("create table public.has_rows (id int primary key)")
        c.execute("insert into public.has_rows values (1)")

    rows = telemetry.reconcile_counts(fresh_db, dest,
                                      [("public", "still_empty"), ("public", "has_rows")], {})
    by_table = {r["table"]: r for r in rows}
    assert by_table["still_empty"]["destination_rows"] is None, "the gap stays visible"
    assert by_table["still_empty"]["match"] is True
    assert by_table["has_rows"]["match"] is False

    run = FakeRun()
    telemetry.emit_reconcile(run, rows)
    assert {e["entity"]: e["status"] for e in run.events} == {
        "public.still_empty": "ok", "public.has_rows": "error"}
