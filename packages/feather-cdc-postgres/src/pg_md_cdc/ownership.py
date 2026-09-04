"""Source-side advisory ownership for one registration (#3079 D8).

Session-scoped `pg_try_advisory_lock` on the SOURCE database, keyed on the slot: Postgres
releases it when the owning backend dies, so a killed drainer cannot split-brain. Held across
EVERY protected phase (publication reconcile, snapshot, backfill, each drain batch) — call
`assert_held()` before each; a dead connection or a lost lock raises `OwnershipLost`.
"""
from __future__ import annotations

import psycopg

KEY_PREFIX = "pg_md_cdc:"


class OwnershipLost(RuntimeError):
    """The owner session is gone (connection dead or advisory lock no longer held)."""


class OwnerSession:
    def __init__(self, dsn: str, slot: str, *, connect_timeout: int = 10) -> None:
        self.dsn = dsn
        self.slot = slot
        self.key = KEY_PREFIX + slot
        self._conn: psycopg.Connection | None = None
        self._held = False

    # --- lifecycle ---
    def acquire(self) -> bool:
        if self._held:
            return True
        self._conn = psycopg.connect(self.dsn, connect_timeout=10, autocommit=True)
        got = self._conn.execute(
            "select pg_try_advisory_lock(hashtext(%s)::bigint)", (self.key,)
        ).fetchone()[0]
        self._held = bool(got)
        if not self._held:
            self._close()
        return self._held

    def assert_held(self) -> None:
        """Cheap liveness + ownership re-check. Doubles as the keepalive that stops a managed
        Postgres (Railway) reaping the owner connection as idle during a long destination load."""
        if not self._held or self._conn is None or self._conn.closed:
            raise OwnershipLost(f"owner session for slot {self.slot!r} is not held")
        try:
            self._conn.execute("select 1").fetchone()
            granted = self._conn.execute(
                "select count(*) from pg_locks where locktype = 'advisory' "
                "and pid = pg_backend_pid() and granted"
            ).fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — a dead connection IS a lost lock
            self._held = False
            raise OwnershipLost(
                f"owner session for slot {self.slot!r} died: {type(exc).__name__}: {exc}"
            ) from exc
        if not granted:
            self._held = False
            raise OwnershipLost(
                f"advisory lock for slot {self.slot!r} is no longer held by this session"
            )

    def release(self) -> None:
        if self._conn is not None and not self._conn.closed and self._held:
            try:
                self._conn.execute("select pg_advisory_unlock(hashtext(%s)::bigint)", (self.key,))
            except Exception:  # noqa: BLE001 — closing the session releases it anyway
                pass
        self._held = False
        self._close()

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    # --- context manager ---
    def __enter__(self) -> "OwnerSession":
        self.acquired = self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    @property
    def held(self) -> bool:
        return self._held
