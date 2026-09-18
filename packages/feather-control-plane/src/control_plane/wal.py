"""Local write-ahead log: the durability boundary (issue #204).

Every control record is appended here as one line of JSON, fsync'd, *before* any network
write to Postgres. On a crash the tail survives on disk (box: persistent; Railway: the
in-flight buffer), and the next run's recovery ships it. This already beats GoFrugal's old
"buffer events in memory, flush at end-of-date" — a mid-date death now loses nothing.

The file is `O_APPEND`, so concurrent appends never interleave a partial line, and a reader
that stops at the last newline never sees a half-written record (the durability invariant
recovery relies on).
"""
from __future__ import annotations

import datetime as _dt
import json
import os


def _default(o):
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    raise TypeError(f"not JSON-serialisable for the WAL: {type(o).__name__}")


class Wal:
    """Append-only newline-JSON log for one run."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def append(self, record: dict) -> None:
        """Serialise + fsync one record. Durable on return."""
        line = (json.dumps(record, default=_default, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)


def read_new(path: str, start_offset: int = 0) -> tuple[list[dict], int]:
    """Parse complete (newline-terminated) records from `start_offset` onward.

    Returns (records, new_offset). A partial trailing line (no newline yet — a crash
    mid-write) is left unconsumed, so new_offset never advances past durable data.
    """
    if not os.path.exists(path):
        return [], start_offset
    with open(path, "rb") as f:
        f.seek(start_offset)
        data = f.read()
    records: list[dict] = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):  # @spec wal_torn_tail_not_consumed
            break  # partial trailing line — not durable, stop here
        consumed += len(line)
        s = line.strip()
        if s:
            records.append(json.loads(s))
    return records, start_offset + consumed
