"""control_plane — durable failure observability for an extractor fleet.

A WAL for crash durability, a Postgres control plane for run lifecycle / events / budget
telemetry, and a lease for deterministic death detection. The data plane lives elsewhere.

Usage:
    from control_plane import ControlPlane, status
    cp = ControlPlane("gofrugal", dsn=PG_DSN, wal_dir="control_wal")
    with cp.run("nightly", run_id=run_id) as run:
        run.event(entity="sales_item_wise", unit="1001", business_date=d,
                  status=status.EV_OK, http_status=200, rows=42,
                  source_attempts=1, sink_attempts=1, pull_ms=1200, load_ms=800)
        run.budget(status.BUDGET_WALLTIME, limit=1800, used=1800, remaining=0, hit=True)
        run.finish(status.RUN_PARTIAL, stop_reason=status.STOP_BUDGET_TIME,
                   n_ok=47, n_err=1, n_skipped=0)

The facade above is resolved LAZILY (PEP 562), and that is load-bearing rather than tidy.
`control_plane.business_day` and `control_plane.status` are pure stdlib, and consumers want them
on their own — a cron tick asking what the business day is has no database in the picture. A
plain `from .run import ControlPlane` at the top of this file runs on `import
control_plane.business_day` too, because a submodule import executes its package first, so every
such consumer was forced to have psycopg installed. That tax is why the same `business_day.py`
ended up hand-copied into scripts that only needed `ist_today()`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["ControlPlane", "ControlPlaneError", "RunContext", "new_run_id", "status"]

# Name -> the submodule that defines it. `status` is absent deliberately: `from control_plane
# import status` is a submodule import, which Python's import machinery already resolves on its
# own without help from here.
_LAZY = {
    "ControlPlane": ".run",
    "ControlPlaneError": ".run",
    "RunContext": ".run",
    "new_run_id": ".run",
}

if TYPE_CHECKING:  # the lazy names, visible to type checkers and IDEs
    from . import status as status
    from .run import ControlPlane as ControlPlane
    from .run import ControlPlaneError as ControlPlaneError
    from .run import RunContext as RunContext
    from .run import new_run_id as new_run_id


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    try:
        return getattr(importlib.import_module(module, __name__), name)
    except ModuleNotFoundError as exc:
        if exc.name != "psycopg":
            raise
        raise ModuleNotFoundError(
            f"control_plane.{name} talks to Postgres and needs the psycopg driver, which is an "
            "optional dependency: install `feather-control-plane[postgres]`. The stdlib-only half "
            "(control_plane.business_day, control_plane.status) needs no extra.",
            name="psycopg",
        ) from exc


def __dir__() -> list[str]:
    return sorted(__all__)
