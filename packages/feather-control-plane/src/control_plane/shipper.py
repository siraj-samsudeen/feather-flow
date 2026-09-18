"""WAL → Postgres projection (issue #204).

Tails a WAL file from a byte offset (sidecar `<wal>.off`) and applies each complete record
to Postgres in one transaction, then advances the offset. At-least-once + idempotent upserts
(store.apply) = effectively-once, with no distributed transaction. Re-shipping after a lost
offset is harmless (ON CONFLICT), so the offset is an optimisation, not a correctness anchor.
"""
from __future__ import annotations

import os

import psycopg

from . import store, wal


def _off_path(wal_path: str) -> str:
    return wal_path + ".off"


def _read_off(wal_path: str) -> int:
    p = _off_path(wal_path)
    try:
        with open(p, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def _write_off(wal_path: str, offset: int) -> None:
    p = _off_path(wal_path)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(offset))
    os.replace(tmp, p)  # atomic


def ship(conn: psycopg.Connection, wal_path: str) -> int:
    """Ship the un-shipped tail of `wal_path` to Postgres. Returns records shipped.

    One transaction for the whole tail: if any apply fails we roll back and leave the offset
    untouched, so the next attempt re-ships the same tail cleanly (idempotent)."""
    start = _read_off(wal_path)
    records, new_off = wal.read_new(wal_path, start)
    if not records:
        return 0
    try:
        with conn.cursor() as cur:
            for rec in records:
                store.apply(cur, rec)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _write_off(wal_path, new_off)  # @spec whole_tail_one_transaction
    return len(records)
