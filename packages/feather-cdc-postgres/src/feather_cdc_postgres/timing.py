"""How long each stage of a run takes, and how long MotherDuck connections stay open (#4708).

Telemetry only: nothing here may change or fail a run. One process is one run (a Railway cron
tick), so the connection totals are process-wide and each stage records the change in them.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass

UNIT = "stage_timing"
ENTITY = "_timing"


@dataclass
class ConnectionTotals:
    """Destination connections opened by this process. dlt opens its own during a load; those
    are not counted here, and show up as the time of the stage that loads."""
    opened: int = 0
    opening_s: float = 0.0   # inside duckdb.connect
    held_s: float = 0.0      # from the start of duckdb.connect to close

    def snapshot(self) -> tuple[int, float, float]:
        return self.opened, self.opening_s, self.held_s


CONNECTIONS = ConnectionTotals()


class TimedConnection:
    """Wraps one duckdb connection so its close is timed. `with` yields the real connection."""

    def __init__(self, connect) -> None:
        self._t0 = time.monotonic()
        self._con = connect()
        CONNECTIONS.opened += 1
        CONNECTIONS.opening_s += time.monotonic() - self._t0
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            CONNECTIONS.held_s += time.monotonic() - self._t0
        self._con.close()

    def __enter__(self):
        return self._con

    def __exit__(self, *exc) -> None:
        self.close()

    def __getattr__(self, name):
        return getattr(self._con, name)


def _log(msg: str) -> None:
    print(f"[feather_cdc_postgres] {msg}", flush=True)


class StageClock:
    """Times named stages. Stages finished before the control.run row exists are held and
    written as soon as `bind` gives the clock a run to write to."""

    def __init__(self, run=None) -> None:
        self._run = run
        self._t0 = time.monotonic()
        self._held: list[dict] = []
        self._notes: dict[str, float] = {}

    def bind(self, run) -> None:
        self._run = run
        held, self._held = self._held, []
        for extra in held:
            self._emit(extra)

    def note(self, name: str, seconds: float) -> None:
        """Add time spent on `name` inside the current stage."""
        self._notes[name] = self._notes.get(name, 0.0) + seconds

    @contextmanager
    def stage(self, name: str):
        started = time.monotonic()
        before = CONNECTIONS.snapshot()
        self._notes = {}
        try:
            yield self
        finally:
            self._finish(name, started, before)

    def total(self) -> None:
        """One last entry with the whole run's connection totals."""
        opened, opening, held = CONNECTIONS.snapshot()
        self._emit({"stage": "total", "seconds": round(time.monotonic() - self._t0, 2),
                    "md_connections": opened, "md_opening_s": round(opening, 2),
                    "md_held_s": round(held, 2)})

    def _finish(self, name: str, started: float, before: tuple) -> None:
        now = CONNECTIONS.snapshot()
        extra = {"stage": name, "seconds": round(time.monotonic() - started, 2),
                 "since_start_s": round(started - self._t0, 2),
                 "md_connections": now[0] - before[0],
                 "md_opening_s": round(now[1] - before[1], 2),
                 "md_held_s": round(now[2] - before[2], 2)}
        extra.update({k: round(v, 2) for k, v in self._notes.items()})
        self._emit(extra)

    def _emit(self, extra: dict) -> None:
        try:
            _log(f"timing {extra}")
            if self._run is None:
                self._held.append(extra)
                return
            from control_plane import status as cp_status
            self._run.event(entity=ENTITY, status=cp_status.EV_OK, unit=UNIT, extra=extra)
        except Exception as exc:  # noqa: BLE001 — telemetry must never fail a run
            _log(f"could not record timing: {type(exc).__name__}")


class _NoClock(StageClock):
    """For callers that do not time anything (tests, `resnapshot`)."""

    @contextmanager
    def stage(self, name: str):
        yield self

    def note(self, name: str, seconds: float) -> None:
        pass

    def _emit(self, extra: dict) -> None:
        pass


NO_CLOCK = _NoClock()
