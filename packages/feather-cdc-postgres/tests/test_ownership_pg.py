"""Advisory ownership on the SOURCE database (#3079 D8)."""
from __future__ import annotations

import psycopg
import pytest

from pg_md_cdc.ownership import OwnerSession, OwnershipLost


def _backend_pids(dsn: str, key: str) -> list[int]:
    with psycopg.connect(dsn, autocommit=True) as c:
        return [r[0] for r in c.execute(
            "select l.pid from pg_locks l where l.locktype = 'advisory' and l.granted "
            "and l.objid = (select (hashtext(%s)::bigint) & 4294967295)", (key,)).fetchall()]


def test_second_session_cannot_acquire_while_the_first_holds(fresh_db, names):
    first = OwnerSession(fresh_db, names["slot"])
    assert first.acquire() is True
    second = OwnerSession(fresh_db, names["slot"])
    try:
        assert second.acquire() is False
        first.assert_held()
    finally:
        second.release()
        first.release()


def test_a_different_slot_is_a_different_lock(fresh_db, names):
    a = OwnerSession(fresh_db, names["slot"])
    b = OwnerSession(fresh_db, names["slot"] + "_other")
    try:
        assert a.acquire() and b.acquire()
    finally:
        a.release()
        b.release()


def test_killing_the_owner_connection_loses_the_lock_and_frees_it(fresh_db, names):
    first = OwnerSession(fresh_db, names["slot"])
    assert first.acquire()
    pids = _backend_pids(fresh_db, first.key)
    assert pids, "the advisory lock is not visible in pg_locks"
    with psycopg.connect(fresh_db, autocommit=True) as c:
        for pid in pids:
            c.execute("select pg_terminate_backend(%s)", (pid,))
    with pytest.raises(OwnershipLost):
        first.assert_held()
    second = OwnerSession(fresh_db, names["slot"])
    try:
        assert second.acquire() is True, "Postgres did not release the lock with the dead session"
    finally:
        second.release()
        first.release()


def test_release_frees_the_lock_for_the_next_run(fresh_db, names):
    with OwnerSession(fresh_db, names["slot"]) as owner:
        assert owner.held
        owner.assert_held()
    again = OwnerSession(fresh_db, names["slot"])
    try:
        assert again.acquire() is True
    finally:
        again.release()


def test_assert_held_raises_before_acquire(fresh_db, names):
    with pytest.raises(OwnershipLost):
        OwnerSession(fresh_db, names["slot"]).assert_held()
