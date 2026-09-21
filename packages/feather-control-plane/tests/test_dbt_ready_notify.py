"""The dbt-ready trigger on control.event (#3884), against a real Postgres.

dbt-runner's worker LISTENs on `dbt_ready`; these prove what it will and will not hear, and WHEN.
"""
from __future__ import annotations

import json

import psycopg

from control_plane import shipper, status, store
from control_plane.wal import Wal


def _listener(dsn):
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute(f"LISTEN {store.DBT_READY_CHANNEL}")
    return conn


def _heard(conn, timeout=0.5):
    return [json.loads(n.payload) for n in conn.notifies(timeout=timeout)]


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
