"""Startup recovery: replay un-shipped WAL tails of prior runs (issue #204).

ARIES-simplified. Before a run does any work it replays the WAL tails left by *prior* runs
of this extractor on this host — pushing their last-logged events (incl. a dead run's cause)
into Postgres. Reconciling those runs to `died` is done by the caller, *after* it acquires
the lease (holding the lease ⇒ no prior run is alive ⇒ unfinished runs are dead), so death
detection stays deterministic — never a runtime guess.
"""
from __future__ import annotations

import glob
import os

import psycopg

from . import shipper


def replay_prior(conn: psycopg.Connection, extractor: str, wal_dir: str, this_run_id: str) -> int:
    """Ship the un-shipped tail of every prior run's WAL for this extractor. Returns total
    records shipped. Best-effort per file: one unreadable WAL never blocks the others."""
    shipped = 0
    pattern = os.path.join(wal_dir, extractor, "*.jsonl")
    for path in sorted(glob.glob(pattern)):
        run_id = os.path.basename(path)[: -len(".jsonl")]
        if run_id == this_run_id:  # @spec prior_wal_replayed_before_lease
            continue
        try:
            shipped += shipper.ship(conn, path)
        except Exception as exc:  # noqa: BLE001 — one bad WAL must not abort recovery
            print(f"[control_plane] WAL replay failed for {path}: "
                  f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
    return shipped
