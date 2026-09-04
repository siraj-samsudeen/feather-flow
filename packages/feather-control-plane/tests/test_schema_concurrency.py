"""ensure_schema() must be safe to call from many processes at once (#3091).

Every writer calls it at the start of every run, so it is the one code path in the library that
is *guaranteed* to run concurrently across the fleet. Before #3091 it issued
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` unconditionally on every startup, and that statement
takes an AccessExclusiveLock whether or not the column is missing. Two writers starting together
each held the preceding CREATE's weaker lock while requesting the exclusive one, and Postgres
killed them both. Under ADR 0028's hard dependency that does not degrade telemetry — it aborts the
run *before* it reads its source: on 04-Sep-2026 two of PTP's three instances did not load at all.

`test_the_legacy_unconditional_ddl_deadlocks` reproduces the cause deterministically, so the guard
below is pinned to a demonstrated failure and not to a story about one.
"""
from __future__ import annotations

import threading

import psycopg
import pytest

from control_plane import store


def _sessions_racing_for(dsn: str, body, n: int = 2):
    """Run `body(conn)` in n threads that are released simultaneously; collect exceptions."""
    ready, go = threading.Barrier(n), threading.Event()
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker():
        try:
            with psycopg.connect(dsn, connect_timeout=10) as conn:
                ready.wait(timeout=30)
                go.wait(timeout=30)
                body(conn)
        except BaseException as exc:  # noqa: BLE001 — the point is to collect them
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    go.set()
    for t in threads:
        t.join(timeout=120)
    return errors


def test_the_legacy_unconditional_ddl_deadlocks(fresh_schema):
    """THE CAUSE, deterministically: hold a shared lock, then both ask for the exclusive one.

    This is what the pre-#3091 body did implicitly — `CREATE TABLE IF NOT EXISTS control.event`
    takes a shared lock on the existing table, then `ALTER TABLE ... ADD COLUMN` asks to escalate.
    Modelled explicitly here so the failure is timing-independent; a test that merely raced the old
    function would pass or fail by luck and would not survive as a regression guard.
    """
    def legacy(conn: psycopg.Connection) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM control.event LIMIT 1")          # AccessShareLock, held
            cur.execute("SELECT pg_sleep(0.5)")                          # overlap the other session
            cur.execute("ALTER TABLE control.event ADD COLUMN IF NOT EXISTS extra JSONB")
        conn.commit()

    errors = _sessions_racing_for(fresh_schema, legacy)
    assert any(isinstance(e, psycopg.errors.DeadlockDetected) for e in errors), (
        "expected the unconditional-DDL pattern to deadlock; got: "
        f"{[type(e).__name__ for e in errors]}"
    )


def test_ensure_schema_survives_concurrent_startups(fresh_schema):
    """The guard: eight writers starting at once, as the fleet does. No exception, no deadlock."""
    errors = _sessions_racing_for(fresh_schema, store.ensure_schema, n=8)
    assert errors == [], f"ensure_schema raised under concurrency: {errors}"


def test_startup_is_not_blocked_by_someone_holding_control_event_exclusively(fresh_schema):
    """The property, not the symptom: on an up-to-date schema, startup must touch control.event
    with NO lock at all — so it cannot be blocked by, or block, another session.

    Proven by holding an ACCESS EXCLUSIVE lock on control.event in a second session and requiring
    ensure_schema to complete anyway, under a short lock_timeout. The pre-#3091 body blocks here
    and raises LockNotAvailable, because its unconditional ALTER has to queue behind the holder.
    (Checking pg_locks after the call instead would prove nothing — the commit has already
    released everything by then, so that version of this test passed on the broken build too.)
    """
    holder = psycopg.connect(fresh_schema)
    try:
        with holder.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("LOCK TABLE control.event IN ACCESS EXCLUSIVE MODE")

        with psycopg.connect(fresh_schema, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '5s'")
            conn.commit()
            store.ensure_schema(conn)   # must not need the lock the other session is holding
    finally:
        holder.rollback()
        holder.close()


def test_ensure_schema_still_builds_a_schema_from_scratch(pg_dsn):
    """The slow path must still work — this is the function's actual job."""
    with psycopg.connect(pg_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS control CASCADE")
        conn.commit()
        store.ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'control'")
            tables = {r[0] for r in cur.fetchall()}
    assert {"run", "lease", "event", "budget_usage"} <= tables


def test_ensure_schema_still_adds_a_missing_migrated_column(fresh_schema):
    """The migration path (#2640's `extra`) must survive the fast-path short-circuit: a plane
    missing the column has to get it, or the skip would silently strand an old control plane."""
    with psycopg.connect(fresh_schema) as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE control.event DROP COLUMN extra")
        conn.commit()
        store.ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='control' AND table_name='event' AND column_name='extra'")
            assert cur.fetchone() is not None, "the migration path no longer restores control.event.extra"


def test_concurrent_first_runs_all_succeed(pg_dsn):
    """Cold start, eight writers at once — the case the advisory lock exists for."""
    with psycopg.connect(pg_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS control CASCADE")
        conn.commit()
    errors = _sessions_racing_for(pg_dsn, store.ensure_schema, n=8)
    assert errors == [], f"concurrent first-runs failed: {errors}"
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='control'")
        assert cur.fetchone()[0] >= 4
