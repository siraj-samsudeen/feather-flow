"""control_plane — durable failure observability for the extractor fleet (issue #204).

A shared library, vendored in-repo: WAL for crash durability, a Postgres control plane for
run lifecycle / events / budget telemetry, and a lease for deterministic death detection.
The data plane stays in MotherDuck. Supersedes ADR 0005 + #65.

Usage:
    from control_plane import ControlPlane, status
    cp = ControlPlane("gofrugal", dsn=PG_DSN, wal_dir="control_wal")
    with cp.run("nightly", run_id=new_run_id("gofrugal")) as run:
        run.event(entity="sales_item_wise", unit="1001", business_date=d,
                  status=status.EV_OK, http_status=200, rows=42,
                  source_attempts=1, sink_attempts=1, pull_ms=1200, load_ms=800)
        run.budget(status.BUDGET_WALLTIME, limit=1800, used=1800, remaining=0, hit=True)
        run.finish(status.RUN_PARTIAL, stop_reason=status.STOP_BUDGET_TIME,
                   n_ok=47, n_err=1, n_skipped=0)
"""
from __future__ import annotations

from . import status
from .run import ControlPlane, ControlPlaneError, RunContext, new_run_id

__all__ = ["ControlPlane", "ControlPlaneError", "RunContext", "new_run_id", "status"]
