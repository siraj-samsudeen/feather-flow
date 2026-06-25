"""Postgres integration tests (Docker postgres:16 via conftest). Cover the properties the
whole design hinges on: idempotent shipping, deterministic lease (steal-only-if-expired,
long-run-never-killed), and death reconciliation end-to-end through ControlPlane."""
from __future__ import annotations

import psycopg
import pytest

from control_plane import ControlPlane, ControlPlaneError, status, store
from control_plane import shipper
from control_plane.wal import Wal

EXT = "gofrugal"


def _q(dsn, sql, args=()):
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


# --- shipping + idempotency ---

def test_event_upsert_idempotent(fresh_schema):
    dsn = fresh_schema
    rec = {"kind": "event", "extractor": EXT, "run_id": "R1", "seq": 0,
           "ran_at": "2026-06-24T01:00:00+00:00", "entity": "sales_item_wise", "unit": "1001",
           "business_date": "2026-06-24", "status": status.EV_SOURCE_ERROR,
           "http_status": 500, "rows": 0, "source_attempts": 4, "sink_attempts": 0,
           "pull_ms": 1200, "load_ms": None, "error_detail": "HTTP 500"}
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            store.apply(cur, rec)
            store.apply(cur, rec)   # replay — must not duplicate
        c.commit()
    rows = _q(dsn, "SELECT status, http_status, source_attempts FROM control.event "
                   "WHERE run_id='R1' AND seq=0")
    assert rows == [(status.EV_SOURCE_ERROR, 500, 4)]


def test_event_extra_jsonb_round_trip(fresh_schema):
    """Source-specific metrics (Zakya's n_requests/gen_ms/rate_remaining) ride in control.event.extra
    losslessly; a None extra stays SQL NULL (#222, D1)."""
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            store.apply(cur, {"kind": "event", "extractor": EXT, "run_id": "RX", "seq": 0,
                              "ran_at": "2026-06-24T01:00:00+00:00", "entity": "export", "unit": "*",
                              "business_date": "2026-06-24", "status": status.EV_OK, "rows": 12,
                              "extra": {"n_requests": 3, "gen_ms": 9000, "rate_remaining": 24988}})
            store.apply(cur, {"kind": "event", "extractor": EXT, "run_id": "RX", "seq": 1,
                              "ran_at": "2026-06-24T01:00:01+00:00", "entity": "list", "unit": "*",
                              "status": status.EV_OK, "rows": 1})   # no extra → NULL
        c.commit()
    rows = _q(dsn, "SELECT extra->>'gen_ms', extra->>'rate_remaining' FROM control.event "
                   "WHERE run_id='RX' AND seq=0")
    assert rows == [("9000", "24988")]
    assert _q(dsn, "SELECT extra FROM control.event WHERE run_id='RX' AND seq=1") == [(None,)]


def test_shipper_tails_and_resumes(fresh_schema, tmp_path):
    dsn = fresh_schema
    w = Wal(str(tmp_path / EXT / "R2.jsonl"))
    w.append({"kind": "run_start", "run_id": "R2", "extractor": EXT, "mode": "nightly",
              "host": "h", "pid": 1, "started_at": "2026-06-24T01:00:00+00:00",
              "status": status.RUN_RUNNING, "wal_path": w.path})
    w.append({"kind": "event", "extractor": EXT, "run_id": "R2", "seq": 0,
              "ran_at": "2026-06-24T01:00:01+00:00", "entity": "f", "status": status.EV_OK,
              "rows": 5})
    with psycopg.connect(dsn) as c:
        assert shipper.ship(c, w.path) == 2
        assert shipper.ship(c, w.path) == 0          # nothing new
        w.append({"kind": "run_finish", "run_id": "R2", "extractor": EXT,
                  "started_at": "2026-06-24T01:00:00+00:00",
                  "finished_at": "2026-06-24T01:05:00+00:00", "status": status.RUN_OK,
                  "stop_reason": status.STOP_CLEAN, "n_ok": 1, "n_err": 0, "n_skipped": 0})
        assert shipper.ship(c, w.path) == 1          # only the new tail
    run = _q(dsn, "SELECT status, stop_reason, n_ok FROM control.run WHERE run_id='R2'")
    assert run == [(status.RUN_OK, status.STOP_CLEAN, 1)]


# --- the lease: deterministic death detection ---

def test_lease_steal_only_if_expired(fresh_schema):
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        assert store.lease_acquire(c, EXT, "A", ttl_s=300) is True    # first holder
        assert store.lease_acquire(c, EXT, "B", ttl_s=300) is False   # A is fresh → B refused


def test_lease_reclaim_after_expiry(fresh_schema):
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        assert store.lease_acquire(c, EXT, "A", ttl_s=-1) is True      # already-expired lease
        assert store.lease_acquire(c, EXT, "B", ttl_s=300) is True     # B reclaims the lapsed lease
        holder = _q(dsn, "SELECT run_id FROM control.lease WHERE extractor=%s", (EXT,))
        assert holder == [("B",)]


def test_lease_renew_extends(fresh_schema):
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        store.lease_acquire(c, EXT, "A", ttl_s=5)
        before = _q(dsn, "SELECT lease_expires_at FROM control.lease WHERE extractor=%s", (EXT,))[0][0]
        assert store.lease_renew(c, EXT, "A", ttl_s=600) is True
        after = _q(dsn, "SELECT lease_expires_at FROM control.lease WHERE extractor=%s", (EXT,))[0][0]
        assert after > before
        assert store.lease_renew(c, EXT, "OTHER", ttl_s=600) is False  # not the owner


# --- reconcile_dead ---

def test_reconcile_marks_unfinished_died(fresh_schema):
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            store.apply(cur, {"kind": "run_start", "run_id": "DEAD", "extractor": EXT,
                              "started_at": "2026-06-24T01:00:00+00:00",
                              "status": status.RUN_RUNNING})
        c.commit()
        n = store.reconcile_dead(c, EXT, "LIVE")
    assert n == 1
    row = _q(dsn, "SELECT status, stop_reason, reconciled_by, finished_at IS NOT NULL "
                  "FROM control.run WHERE run_id='DEAD'")
    assert row == [(status.RUN_DIED, status.STOP_DIED, "LIVE", True)]


# --- end-to-end through ControlPlane ---

def test_controlplane_full_lifecycle(fresh_schema, tmp_path):
    dsn = fresh_schema
    cp = ControlPlane(EXT, dsn=dsn, wal_dir=str(tmp_path), lease_ttl_s=300, renew_interval_s=60)
    with cp.run("nightly", run_id="LIVE1") as run:
        run.event(entity="sales_item_wise", unit="1001", business_date="2026-06-24",
                  status=status.EV_OK, http_status=200, rows=42,
                  source_attempts=1, sink_attempts=1, pull_ms=900, load_ms=700)
        run.event(entity="sales_item_wise", unit="1002", business_date="2026-06-24",
                  status=status.EV_SOURCE_ERROR, http_status=500, rows=0,
                  source_attempts=4, sink_attempts=0, pull_ms=1200, error_detail="HTTP 500")
        run.budget(status.BUDGET_WALLTIME, limit=1800, used=1800, remaining=0, hit=True)
        run.finish(status.RUN_PARTIAL, stop_reason=status.STOP_BUDGET_TIME,
                   n_ok=1, n_err=1, n_skipped=0)
    assert _q(dsn, "SELECT status, stop_reason, n_ok, n_err FROM control.run WHERE run_id='LIVE1'") \
        == [(status.RUN_PARTIAL, status.STOP_BUDGET_TIME, 1, 1)]
    assert _q(dsn, "SELECT count(*) FROM control.event WHERE run_id='LIVE1'")[0][0] == 2
    assert _q(dsn, "SELECT status FROM control.event WHERE run_id='LIVE1' AND unit='1002'") \
        == [(status.EV_SOURCE_ERROR,)]
    assert _q(dsn, "SELECT hit, remaining FROM control.budget_usage WHERE run_id='LIVE1'") \
        == [(True, 0)]


def test_controlplane_reconciles_dead_predecessor(fresh_schema, tmp_path):
    """A crashed run (started, never finished, lease lapsed) is reconciled to `died` by the
    next run — the core death-detection guarantee."""
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            store.apply(cur, {"kind": "run_start", "run_id": "CRASHED", "extractor": EXT,
                              "started_at": "2026-06-24T01:00:00+00:00",
                              "status": status.RUN_RUNNING})
        c.commit()
        store.lease_acquire(c, EXT, "CRASHED", ttl_s=-1)   # its lease already lapsed
    cp = ControlPlane(EXT, dsn=dsn, wal_dir=str(tmp_path))
    with cp.run("nightly", run_id="NEXT") as run:
        run.finish(status.RUN_OK, stop_reason=status.STOP_CLEAN, n_ok=0, n_err=0, n_skipped=0)
    assert _q(dsn, "SELECT status, reconciled_by FROM control.run WHERE run_id='CRASHED'") \
        == [(status.RUN_DIED, "NEXT")]


def test_controlplane_refuses_second_writer(fresh_schema, tmp_path):
    """A genuinely-live run (fresh lease) is never displaced — the 14h-job-not-killed property:
    the second starter refuses rather than stealing the lease."""
    dsn = fresh_schema
    with psycopg.connect(dsn) as c:
        store.lease_acquire(c, EXT, "LONGRUN", ttl_s=300)   # alive, fresh lease
    cp = ControlPlane(EXT, dsn=dsn, wal_dir=str(tmp_path))
    with pytest.raises(ControlPlaneError):
        with cp.run("nightly", run_id="INTRUDER"):
            pass
    # LONGRUN is untouched — not reconciled to died
    assert _q(dsn, "SELECT count(*) FROM control.run WHERE status='died'")[0][0] == 0


def test_controlplane_hard_dependency_raises_when_pg_down(tmp_path):
    """Postgres is a HARD dependency (ADR 0028): a configured-but-unreachable DSN ABORTS the run —
    no blind runs. The failure propagates out of the `with` before any work begins."""
    import pytest
    cp = ControlPlane(EXT, dsn="postgresql://nobody@127.0.0.1:1/none", wal_dir=str(tmp_path))
    with pytest.raises(Exception):
        with cp.run("nightly", run_id="OFFLINE") as run:
            run.event(entity="f", status=status.EV_OK, rows=1)


def test_controlplane_dsn_none_is_wal_only_dev_mode(tmp_path):
    """dsn=None is the explicit no-control-plane mode (dev/test): events go to the WAL only, no PG.
    This is what the hermetic extractor-wiring tests rely on."""
    cp = ControlPlane(EXT, dsn=None, wal_dir=str(tmp_path))
    with cp.run("nightly", run_id="DEVRUN") as run:
        run.event(entity="f", status=status.EV_OK, rows=1)
        run.finish(status.RUN_OK, stop_reason=status.STOP_CLEAN, n_ok=1, n_err=0, n_skipped=0)
    from control_plane import wal as walmod
    recs, _ = walmod.read_new(str(tmp_path / EXT / "DEVRUN.jsonl"))
    assert [r["kind"] for r in recs] == ["run_start", "event", "run_finish"]
