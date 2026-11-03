# feather-control-plane

Durable failure observability for an extractor fleet.

A long-running extractor that dies mid-load leaves nothing behind: no row, no error, no
"it got as far as table X". This library gives it three things that survive the death —

- a **write-ahead log** on local disk, fsynced before anything is shipped, so a crash leaves
  the breadcrumbs of how far the run got;
- a **Postgres control plane** holding run lifecycle, per-unit events and budget telemetry,
  shipped as the run goes rather than written at the end;
- a **liveness lease**, renewed by a background thread, so a process that stops renewing is
  known to be dead instead of being assumed still running.

The data plane is somewhere else entirely — this records what happened to the load, not the
load's data.

## Install

```bash
pip install "feather-control-plane[postgres]"    # the full facade
pip install feather-control-plane                # pure-stdlib helpers only
```

**`psycopg` is an optional extra, and that is deliberate.** Two of the modules here —
`control_plane.business_day` and `control_plane.status` — are pure stdlib, and consumers import
them on their own, with no database anywhere in the picture. Making a database driver mandatory
for a date helper is what drove nine services to copy `business_day.py` around by hand instead
of depending on the library. Anything that actually opens a connection needs the `postgres`
extra; `__init__.py` resolves those names lazily (PEP 562), so importing the stdlib half never
pulls the driver in.

## Import name

The distribution is `feather-control-plane`; the **import name is `control_plane`**:

```python
from control_plane import ControlPlane, status
from control_plane.business_day import ist_today
```

They differ on purpose. The nine services adopting this already have ~40 call sites spelled
`from control_plane import ...`, so keeping the import name turns adoption into a dependency
substitution with no source edits.

## Usage

```python
from control_plane import ControlPlane, status

cp = ControlPlane("gofrugal", dsn=PG_DSN, wal_dir="control_wal")
with cp.run("nightly", run_id=run_id) as run:
    run.event(entity="sales_item_wise", unit="1001", business_date=d,
              status=status.EV_OK, http_status=200, rows=42,
              source_attempts=1, sink_attempts=1, pull_ms=1200, load_ms=800)
    run.budget(status.BUDGET_WALLTIME, limit=1800, used=1800, remaining=0, hit=True)
    run.finish(status.RUN_PARTIAL, stop_reason=status.STOP_BUDGET_TIME,
               n_ok=47, n_err=1, n_skipped=0)
```

`dsn=None` is the explicit no-control-plane mode for dev and test: the WAL still records
everything, nothing ships. With a DSN configured, Postgres is a **hard dependency of the run** —
if it is unreachable at startup the run aborts rather than loading blind, because a load with no
provenance is worse than no load.

## Tests

```bash
cd packages/feather-control-plane && uv run --package feather-control-plane pytest
# or, from the repo root, naming the path:
uv run --package feather-control-plane pytest packages/feather-control-plane/tests
```

`--package` picks the *environment*; it does not pick the tests. pytest takes its config from the
nearest `pyproject.toml` above the working directory, so a bare `pytest` run from the repo root
collects feather-flow's suite no matter what `--package` says — green, 1050 tests, and not one of
them this package's. Either `cd` in or name the path.

The pure-logic tests (WAL framing, status vocabulary, business day) run anywhere. The rest need a
real Postgres and find one in this order: `CONTROL_PG_DSN` if set → a `postgres:16` Docker
container → a local Postgres binary (`pg_ctl` on `PATH`, else Homebrew's keg-only
`postgresql@N`), `initdb`'d into a temp directory on a throwaway port and torn down afterwards.
With none of the three available they skip.
