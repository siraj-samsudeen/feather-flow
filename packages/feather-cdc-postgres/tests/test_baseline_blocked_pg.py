"""A long-running source transaction must not hang a baseline forever (#3079 hardening item 2).

CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT blocks until every in-flight transaction ends;
statement_timeout cannot cancel it (see snapshot.create_slot_with_snapshot's docstring). This
proves the client-side deadline: BaselineBlocked fires within ~baseline_wait_s and names the
blocking session's pid; once the blocker commits, the same call succeeds.
"""
from __future__ import annotations

import threading
import time

import psycopg
import pytest

from pg_md_cdc import snapshot


@pytest.fixture
def seeded(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute('create table public.t1 (id int primary key, v text)')
        c.execute("insert into public.t1 values (1, 'a')")
    return fresh_db


def test_a_held_open_transaction_trips_baseline_blocked_naming_its_pid(seeded, names):
    blocker_ready = threading.Event()
    release_blocker = threading.Event()
    blocker_pid = {}

    def hold_open():
        conn = psycopg.connect(seeded)
        conn.execute("begin")
        conn.execute("update public.t1 set v = 'b' where id = 1")
        blocker_pid["pid"] = conn.execute("select pg_backend_pid()").fetchone()[0]
        blocker_ready.set()
        release_blocker.wait(timeout=30)
        conn.commit()
        conn.close()

    th = threading.Thread(target=hold_open, daemon=True)
    th.start()
    blocker_ready.wait(timeout=10)
    time.sleep(0.2)  # let the UPDATE's row lock settle before we race it

    t0 = time.monotonic()
    try:
        with pytest.raises(snapshot.BaselineBlocked, match=str(blocker_pid["pid"])):
            with snapshot.consistent_baseline(seeded, names["slot"], ("public", "t1"), ["id"],
                                              baseline_wait_s=2):
                pass
        elapsed = time.monotonic() - t0
        assert elapsed < 6, f"BaselineBlocked took {elapsed:.1f}s, expected close to 2s"
    finally:
        release_blocker.set()
        th.join(timeout=10)


def test_the_baseline_succeeds_once_the_blocker_commits(seeded, names):
    blocker_ready = threading.Event()
    release_blocker = threading.Event()

    def hold_open():
        conn = psycopg.connect(seeded)
        conn.execute("begin")
        conn.execute("update public.t1 set v = 'b' where id = 1")
        blocker_ready.set()
        release_blocker.wait(timeout=30)
        conn.commit()
        conn.close()

    th = threading.Thread(target=hold_open, daemon=True)
    th.start()
    blocker_ready.wait(timeout=10)
    time.sleep(0.2)

    result = {}

    def take_baseline():
        with snapshot.consistent_baseline(seeded, names["slot"], ("public", "t1"), ["id"],
                                          baseline_wait_s=15) as (resource, boundary):
            result["rows"] = list(resource)
            result["boundary"] = boundary

    worker = threading.Thread(target=take_baseline, daemon=True)
    worker.start()
    time.sleep(1.0)
    assert worker.is_alive(), "the baseline should still be waiting on the open transaction"
    release_blocker.set()
    th.join(timeout=10)
    worker.join(timeout=15)
    assert not worker.is_alive()
    assert result["boundary"] > 0
    assert sorted(r["id"] for r in result["rows"]) == [1]


def test_the_blocker_diagnostic_never_carries_the_blockers_sql(seeded, names):
    """#4346 (Codex round 9): the blocking session's last statement may be an
    `UPDATE person SET password_hash = '…'` — BaselineBlocked (which reaches control.event and
    the log) names the session, never its SQL."""
    blocker_ready = threading.Event()
    release_blocker = threading.Event()

    def hold_open():
        conn = psycopg.connect(seeded)
        conn.execute("begin")
        conn.execute("update public.t1 set v = 'SYNTHETIC_HASH_4346' where id = 1")
        blocker_ready.set()
        release_blocker.wait(timeout=30)
        conn.commit()
        conn.close()

    th = threading.Thread(target=hold_open, daemon=True)
    th.start()
    blocker_ready.wait(timeout=10)
    time.sleep(0.2)
    try:
        with pytest.raises(snapshot.BaselineBlocked) as info:
            with snapshot.consistent_baseline(seeded, names["slot"], ("public", "t1"), ["id"],
                                              baseline_wait_s=2):
                pass
        assert "SYNTHETIC_HASH_4346" not in str(info.value)
        assert "oldest blocker pid=" in str(info.value)
    finally:
        release_blocker.set()
        th.join(timeout=10)
