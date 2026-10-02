"""The dbt-ready trigger on control.event (#3884), against a real Postgres.

dbt-runner's worker LISTENs on `dbt_ready`; these prove what it will and will not hear, and WHEN.
"""
from __future__ import annotations

import json

import psycopg

from control_plane import shipper, status, store
from control_plane.wal import Wal


def _listener(dsn, channels=(store.DBT_READY_CHANNEL, store.DBT_RUN_FINISHED_CHANNEL)):
    """The worker's connection: both channels since #4024. A pre-#4024 worker is
    `_listener(dsn, (store.DBT_READY_CHANNEL,))`."""
    conn = psycopg.connect(dsn, autocommit=True)
    for channel in channels:
        conn.execute(f"LISTEN {channel}")
    return conn


def _heard(conn, timeout=0.5):
    return [json.loads(n.payload) for n in conn.notifies(timeout=timeout)]


def _heard_on(conn, timeout=0.5):
    return [(n.channel, json.loads(n.payload)) for n in conn.notifies(timeout=timeout)]


def _event(seq, *, extractor="zakya", entity="transform_ready", st=status.EV_OK, run_id="Z1"):
    return {"kind": "event", "extractor": extractor, "run_id": run_id, "seq": seq,
            "ran_at": "2026-09-21T05:10:00+00:00", "entity": entity, "unit": "*",
            "business_date": "2026-09-21", "status": st, "rows": 1}


def test_ready_event_notifies_with_its_payload(fresh_schema):
    """@spec ready_event_notify"""
    listener = _listener(fresh_schema)
    with psycopg.connect(fresh_schema) as c, c.cursor() as cur:
        store.apply(cur, _event(0))
        c.commit()
    assert _heard(listener) == [
        {"extractor": "zakya", "run_id": "Z1", "business_date": "2026-09-21"}]


def test_only_zakya_ok_transform_ready_notifies(fresh_schema):
    """@spec ready_event_notify — other extractors, other entities, non-ok statuses stay silent."""
    listener = _listener(fresh_schema)
    with psycopg.connect(fresh_schema) as c, c.cursor() as cur:
        store.apply(cur, _event(0, extractor="gofrugal"))
        store.apply(cur, _event(1, entity="export_invoices"))
        store.apply(cur, _event(2, st=status.EV_SINK_ERROR))
        store.apply(cur, _event(3, st=status.EV_OK_PARTIAL))
        c.commit()
    assert _heard(listener) == []


def test_replayed_event_does_not_notify_again(fresh_schema):
    """@spec ready_event_notify — ON CONFLICT DO NOTHING inserts no row, so the row trigger is
    not fired; the WAL's at-least-once replay therefore wakes the worker once, not per replay."""
    listener = _listener(fresh_schema)
    for _ in range(3):
        with psycopg.connect(fresh_schema) as c, c.cursor() as cur:
            store.apply(cur, _event(0))
            c.commit()
    assert len(_heard(listener)) == 1


def test_notify_is_delivered_at_commit_not_at_insert(fresh_schema):
    """@spec ready_event_notify — the operator's question (a): a batched ship is ONE transaction;
    the listener must hear nothing until it commits, and nothing at all if it rolls back."""
    listener = _listener(fresh_schema)
    c = psycopg.connect(fresh_schema)
    with c.cursor() as cur:
        store.apply_events_batch(cur, [_event(0, entity="export_invoices"), _event(1),
                                       _event(2, entity="list_invoices")])
        assert _heard(listener, timeout=0.3) == []          # inserted, not committed
        c.rollback()
    assert _heard(listener, timeout=0.3) == []              # rolled back: never delivered
    with c.cursor() as cur:
        store.apply_events_batch(cur, [_event(0), _event(1, run_id="Z2")])
        assert _heard(listener, timeout=0.3) == []
        c.commit()
    assert sorted(p["run_id"] for p in _heard(listener)) == ["Z1", "Z2"]
    c.close()


def test_shipper_path_notifies(fresh_schema, tmp_path):
    """@spec ready_event_notify — the real path Zakya takes: WAL append then ship-as-you-go."""
    listener = _listener(fresh_schema)
    wal = Wal(str(tmp_path / "zakya_Z9.wal"))
    wal.append({**_event(0, entity="export_invoices", run_id="Z9")})
    wal.append({**_event(1, run_id="Z9")})
    with psycopg.connect(fresh_schema) as c:
        assert shipper.ship(c, wal.path) == 2
        assert shipper.ship(c, wal.path) == 0               # nothing new; nothing heard twice
    assert [p["run_id"] for p in _heard(listener)] == ["Z9"]


def test_ensure_schema_adds_the_trigger_to_a_pre_3884_control_plane(fresh_schema):
    """@spec ready_event_notify — a live plane created before #3884 has every table and column,
    so only the trigger check can send it down the migration path; after that it is current."""
    with psycopg.connect(fresh_schema) as c:
        c.execute(f"DROP TRIGGER {store.DBT_READY_TRIGGER} ON control.event")
        c.commit()
        assert not store._schema_is_current(c)
        c.rollback()
        store.ensure_schema(c)
        assert store._schema_is_current(c)
        c.rollback()
        store.ensure_schema(c)                              # idempotent second pass
        n = c.execute("SELECT count(*) FROM pg_trigger WHERE tgname = %s",
                      (store.DBT_READY_TRIGGER,)).fetchone()[0]
    assert n == 1


# --- the run-finish trigger on control.run (#4024) ---------------------------------------------
# GoFrugal and SAP have no marker event; their proof of a complete load is their own control.run
# row finishing ok. The second trigger notifies the same channel with `kind: run`, once per row,
# on the transition into (ok, finished) only.

def _run_start(run_id="G1", extractor="gofrugal"):
    return {"kind": "run_start", "run_id": run_id, "extractor": extractor, "mode": "scheduled",
            "host": "test", "pid": 1, "started_at": "2026-09-21T05:00:00+00:00",
            "status": status.RUN_RUNNING, "wal_path": None}


def _run_finish(run_id="G1", extractor="gofrugal", st=status.RUN_OK):
    return {"kind": "run_finish", "run_id": run_id, "extractor": extractor,
            "started_at": "2026-09-21T05:00:00+00:00", "finished_at": "2026-09-21T05:10:00+00:00",
            "status": st, "stop_reason": None, "n_ok": 3, "n_err": 0, "n_skipped": 0}


def _apply_all(dsn, *recs):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        for rec in recs:
            store.apply(cur, rec)
        c.commit()


def test_a_source_run_finishing_ok_notifies_once_with_a_run_payload(fresh_schema):
    """@spec run_finish_notify — the real shape: run_start lands, the finish UPDATEs it to ok. On
    its OWN channel: a worker from before #4024 (marker channel only) hears nothing, so it can
    never mistake the finish for a Zakya marker (review of #4025)."""
    from datetime import datetime, timezone
    listener = _listener(fresh_schema)
    old_worker = _listener(fresh_schema, (store.DBT_READY_CHANNEL,))
    _apply_all(fresh_schema, _run_start())
    assert _heard(listener) == []                              # a start is not a finish
    _apply_all(fresh_schema, _run_finish())
    heard = _heard_on(listener)
    assert len(heard) == 1
    channel, payload = heard[0]
    assert channel == store.DBT_RUN_FINISHED_CHANNEL != store.DBT_READY_CHANNEL
    assert payload["kind"] == "run"
    assert payload["extractor"] == "gofrugal" and payload["run_id"] == "G1"
    assert datetime.fromisoformat(payload["finished_at"]) == datetime(2026, 9, 21, 5, 10, tzinfo=timezone.utc)
    assert _heard(old_worker) == []


def test_a_finish_with_no_start_row_notifies_once(fresh_schema):
    """@spec run_finish_notify — Postgres was down at start: the finish is an INSERT, and it is
    still the transition into (ok, finished)."""
    listener = _listener(fresh_schema)
    _apply_all(fresh_schema, _run_finish(run_id="G2"))
    assert [p["run_id"] for p in _heard(listener)] == ["G2"]


def test_a_replayed_finish_does_not_notify_again(fresh_schema):
    """@spec run_finish_notify.transition_only — the WAL's at-least-once replay is ON CONFLICT DO
    UPDATE on a row already ok: the row trigger fires, the body drops it."""
    listener = _listener(fresh_schema)
    _apply_all(fresh_schema, _run_start())
    for _ in range(3):
        _apply_all(fresh_schema, _run_finish())
    assert len(_heard(listener)) == 1


def test_only_a_source_finish_that_is_ok_notifies(fresh_schema):
    """@spec run_finish_notify — partial and failed finishes, the `died` reconcile, a later
    reconciled_by UPDATE of an ok row, and dbt's own rows (the listener's own builds) stay silent;
    a `died` row later overwritten by a late ok finish is a transition and notifies."""
    listener = _listener(fresh_schema)
    _apply_all(fresh_schema, _run_start("G3"), _run_finish("G3", st=status.RUN_PARTIAL),
               _run_start("G4"), _run_finish("G4", st=status.RUN_FAILED),
               _run_start("D1", extractor="dbt"), _run_finish("D1", extractor="dbt"),
               _run_start("G5"))
    with psycopg.connect(fresh_schema) as c:
        c.execute("UPDATE control.run SET status = 'died', stop_reason = 'died', finished_at = now() "
                  "WHERE run_id = 'G5'")
        c.commit()
    assert _heard(listener) == []
    _apply_all(fresh_schema, _run_finish("G5"))                # died -> ok: the load did complete
    assert [p["run_id"] for p in _heard(listener)] == ["G5"]
    with psycopg.connect(fresh_schema) as c:
        c.execute("UPDATE control.run SET reconciled_by = 'X' WHERE run_id = 'G5'")
        c.commit()
    assert _heard(listener) == []


def test_a_rolled_back_finish_delivers_nothing(fresh_schema):
    """@spec run_finish_notify — NOTIFY rides the transaction, as the marker's does."""
    listener = _listener(fresh_schema)
    c = psycopg.connect(fresh_schema)
    with c.cursor() as cur:
        store.apply(cur, _run_finish("G6"))
        assert _heard(listener, timeout=0.3) == []
        c.rollback()
    assert _heard(listener, timeout=0.3) == []
    c.close()


def test_ensure_schema_adds_the_run_trigger_to_a_pre_4024_control_plane(fresh_schema):
    """@spec run_finish_notify.installed_by_ensure_schema — a live plane has every table, column
    and #3884's trigger; only the run trigger's absence sends it down `trigger_missing`, which
    installs that one and leaves the event trigger untouched."""
    with psycopg.connect(fresh_schema) as c:
        c.execute(f"DROP TRIGGER {store.RUN_FINISH_TRIGGER} ON control.run")
        c.commit()
        assert store._schema_state(c) == "trigger_missing"
        c.rollback()
        store.ensure_schema(c)
        assert store._schema_is_current(c)
        c.rollback()
        store.ensure_schema(c)                              # idempotent second pass
        counts = {name: c.execute("SELECT count(*) FROM pg_trigger WHERE tgname = %s",
                                  (name,)).fetchone()[0]
                  for name in (store.DBT_READY_TRIGGER, store.RUN_FINISH_TRIGGER)}
    assert counts == {store.DBT_READY_TRIGGER: 1, store.RUN_FINISH_TRIGGER: 1}
    listener = _listener(fresh_schema)
    _apply_all(fresh_schema, _run_finish("G7"), _event(0))
    assert sorted(p["run_id"] for p in _heard(listener)) == ["G7", "Z1"]


def test_ensure_schema_installs_both_triggers_when_both_are_missing(fresh_schema):
    """@spec run_finish_notify.installed_by_ensure_schema — a plane whose #3884 install timed out,
    or a restored one: both absent, both installed, each in its own transaction (the review of
    #4025 reproduced a deadlock with a replaying shipper when the two shared one)."""
    with psycopg.connect(fresh_schema) as c:
        c.execute(f"DROP TRIGGER {store.DBT_READY_TRIGGER} ON control.event")
        c.execute(f"DROP TRIGGER {store.RUN_FINISH_TRIGGER} ON control.run")
        c.commit()
        assert store._schema_state(c) == "trigger_missing"
        c.rollback()
        store.ensure_schema(c)
        assert store._schema_is_current(c)
        c.rollback()
        counts = {name: c.execute("SELECT count(*) FROM pg_trigger WHERE tgname = %s",
                                  (name,)).fetchone()[0]
                  for name in (store.DBT_READY_TRIGGER, store.RUN_FINISH_TRIGGER)}
    assert counts == {store.DBT_READY_TRIGGER: 1, store.RUN_FINISH_TRIGGER: 1}
