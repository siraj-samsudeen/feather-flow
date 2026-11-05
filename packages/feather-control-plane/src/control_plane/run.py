"""The consumer-facing facade (issue #204).

An extractor wraps its work in `ControlPlane(...).run(mode) as run:` and calls `run.event(...)`
per unit, `run.budget(...)` for quota telemetry, and `run.finish(...)` with final counts. The
facade handles WAL durability, ship-as-you-go to Postgres, startup recovery, the lease, and a
background renewer thread.

Postgres is a **hard dependency** (ADR 0028): if a DSN is configured and unreachable at startup,
the run **aborts** — no blind runs (a silent load with no provenance is worse than no load). The
WAL is a local **crash redo-log** *under* the Postgres writes, not an outage buffer: every event
hits the WAL first (fsync), then ships to PG; if the process dies, the next run replays the tail so
the death's breadcrumbs survive ("died after table X / chunk Y"), and the lease supplies the fact
of death. `dsn=None` is the explicit no-control-plane mode for dev/test (WAL-only).
"""
from __future__ import annotations

import datetime as _dt
import os
import secrets
import socket
import threading
from contextlib import contextmanager

import psycopg

from . import recovery, shipper, status, store
from .status import ERROR_DETAIL_CAP
from .wal import Wal


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def new_run_id(extractor: str) -> str:  # @spec run_id_unique_across_extractors
    """`<UTC second>-<extractor>-<6 hex>`. `control.run` is keyed on run_id alone, and every Railway
    container is pid 1, so the old `<second>-<pid>` gave two services starting in the same second
    one id (#4614). The timestamp stays first: SAP names backups `run_id[:8]`."""
    return f"{_now().strftime('%Y%m%dT%H%M%SZ')}-{extractor}-{secrets.token_hex(3)}"


class ControlPlaneError(RuntimeError):
    pass


class _Renewer:
    """Renews the lease on its OWN connection, decoupled from the work loop — so a 14h job or
    a 90-min SAP chunk keeps proving liveness and is never falsely declared dead. Swallows
    transient PG errors and retries on the next tick (reconnecting if needed)."""

    def __init__(self, dsn: str, extractor: str, run_id: str, ttl_s: int, interval_s: int):
        self._dsn = dsn
        self._extractor = extractor
        self._run_id = run_id
        self._ttl = ttl_s
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="cp-lease-renewer", daemon=True)
        self._conn: psycopg.Connection | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 5)
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):  # @spec lease_renewed_in_background
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = psycopg.connect(self._dsn, connect_timeout=10)
                if not store.lease_renew(self._conn, self._extractor, self._run_id, self._ttl):
                    # @spec lease_loss_does_not_stop_run
                    print(f"[control_plane] lease lost for {self._extractor}/{self._run_id} "
                          "(another run reclaimed it?)", flush=True)
            except Exception as exc:  # noqa: BLE001 — renew must never crash the run  # @spec false_death_of_live_run
                print(f"[control_plane] lease renew failed (will retry): "
                      f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                self._conn = None


class RunContext:
    def __init__(self, cp: "ControlPlane", run_id: str, wal: Wal, conn: psycopg.Connection | None):
        self.cp = cp
        self.run_id = run_id
        self.wal = wal
        self.conn = conn            # None ⇒ PG was down at start; WAL-only degraded mode
        self._seq = 0
        self._finished = False

    # --- internal ---
    def _ship_now(self) -> None:
        if self.conn is None:
            return
        try:
            shipper.ship(self.conn, self.wal.path)
        except Exception as exc:  # noqa: BLE001 — WAL already holds it; recovery ships later  # @spec ship_as_you_go
            print(f"[control_plane] ship failed (buffered in WAL): "
                  f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)

    def _emit(self, record: dict) -> None:
        self.wal.append(record)   # durable first  # @spec wal_before_network
        self._ship_now()          # then best-effort to PG

    # --- public API ---
    def event(self, *, entity: str, status: str, unit: str | None = None,
              business_date=None, http_status: int | None = None, rows: int | None = None,
              source_attempts: int | None = None, sink_attempts: int | None = None,
              pull_ms: int | None = None, load_ms: int | None = None,
              error_detail: str | None = None, extra: dict | None = None) -> None:
        """Record one unit/cell outcome (one row in control.event). `status` here is the
        event-status string (status.EV_*), not the module. `extra` is a small JSONB bag for
        source-specific metrics that don't warrant a shared column (e.g. Zakya's n_requests /
        gen_ms / rate_remaining); leave it None when there's nothing source-specific to keep."""
        self._emit({
            "kind": "event", "extractor": self.cp.extractor, "run_id": self.run_id,
            "seq": self._seq, "ran_at": _now(), "entity": entity, "unit": unit,
            "business_date": business_date, "status": status, "http_status": http_status,
            "rows": rows, "source_attempts": source_attempts, "sink_attempts": sink_attempts,
            "pull_ms": pull_ms, "load_ms": load_ms,
            "error_detail": (error_detail[:ERROR_DETAIL_CAP] if error_detail else None),  # @spec error_detail_capped
            "extra": extra,
        })
        self._seq += 1  # @spec event_one_row_per_unit

    def budget(self, name: str, *, limit: int | None, used: int | None,
               remaining: int | None, hit: bool) -> None:
        """Record quota utilisation (every run, not only on a hit) → headroom is queryable."""
        self._emit({
            "kind": "budget", "extractor": self.cp.extractor, "run_id": self.run_id,
            "budget_name": name, "limit_val": limit, "used": used, "remaining": remaining,
            "hit": hit, "ran_at": _now(),
        })

    def finish(self, run_status: str, *, stop_reason: str, n_ok: int | None = None,
               n_err: int | None = None, n_skipped: int | None = None) -> None:
        """Close the run with its final disposition. Idempotent (first call wins)."""
        if self._finished:  # @spec run_finish_first_call_wins
            return
        self._emit({
            "kind": "run_finish", "extractor": self.cp.extractor, "run_id": self.run_id,
            "started_at": self.cp._started_at, "finished_at": _now(),
            "status": run_status, "stop_reason": stop_reason,
            "n_ok": n_ok, "n_err": n_err, "n_skipped": n_skipped,
        })
        self._finished = True


class ControlPlane:
    def __init__(self, extractor: str, dsn: str | None, wal_dir: str, *,
                 lease_ttl_s: int = 300, renew_interval_s: int = 60, host: str | None = None):
        self.extractor = extractor
        self.dsn = dsn
        self.wal_dir = wal_dir
        self.lease_ttl_s = lease_ttl_s
        self.renew_interval_s = renew_interval_s
        self.host = host or socket.gethostname()
        self._started_at: str | None = None

    @contextmanager
    def run(self, mode: str, *, run_id: str | None = None):
        run_id = run_id or new_run_id(self.extractor)
        self._started_at = _now().isoformat()
        wal = Wal(os.path.join(self.wal_dir, self.extractor, run_id + ".jsonl"))
        conn: psycopg.Connection | None = None
        renewer: _Renewer | None = None

        # Postgres is a HARD dependency (ADR 0028): if a DSN is configured, the control plane
        # MUST be reachable or the run aborts — never run blind. A failure here propagates out of
        # the `with` before any work begins. (dsn=None is the explicit no-control-plane mode for
        # dev/test → WAL-only.)
        if self.dsn:  # @spec hard_startup_dependency
            conn = psycopg.connect(self.dsn, connect_timeout=10)
            try:
                store.ensure_schema(conn)
                recovery.replay_prior(conn, self.extractor, self.wal_dir, run_id)
                # @spec lease_refuses_second_writer
                if not store.lease_acquire(conn, self.extractor, run_id, self.lease_ttl_s):
                    raise ControlPlaneError(
                        f"another live run holds the lease for {self.extractor!r} "
                        "(fresh lease present) — refusing to start a second writer")
                store.reconcile_dead(conn, self.extractor, run_id)  # @spec death_visible_only_at_next_start
                renewer = _Renewer(self.dsn, self.extractor, run_id,
                                   self.lease_ttl_s, self.renew_interval_s)
                renewer.start()
            except BaseException:
                # setup failed — clean up and abort; never degrade to a blind run
                if renewer is not None:
                    renewer.stop()
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                raise

        rc = RunContext(self, run_id, wal, conn)
        rc._emit({  # @spec run_start_recorded_before_work
            "kind": "run_start", "run_id": run_id, "extractor": self.extractor,
            "mode": mode, "host": self.host, "pid": os.getpid(),
            "started_at": self._started_at, "status": status.RUN_RUNNING,
            "wal_path": wal.path,
        })

        try:
            yield rc
        except BaseException:
            if not rc._finished:
                rc.finish(status.RUN_FAILED, stop_reason=status.STOP_ERROR)  # @spec implicit_finish_on_exit.sys_exit_inside_block
            raise
        finally:
            if not rc._finished:
                rc.finish(status.RUN_OK, stop_reason=status.STOP_CLEAN)  # @spec implicit_finish_on_exit
            if renewer is not None:
                renewer.stop()
            if conn is not None:
                try:
                    store.lease_release(conn, self.extractor, run_id)  # @spec lease_released_on_exit
                    shipper.ship(conn, wal.path)  # final flush  # @spec final_flush_on_exit
                except Exception as exc:  # noqa: BLE001
                    print(f"[control_plane] final flush/release failed (WAL retained): "
                          f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
                finally:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
