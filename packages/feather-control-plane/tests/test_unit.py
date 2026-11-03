"""Pure-logic unit tests — no Postgres, no Docker. Run anywhere."""
from __future__ import annotations

import datetime as dt
import os

from control_plane import status
from control_plane import wal as walmod


def test_wal_roundtrip(tmp_path):
    w = walmod.Wal(str(tmp_path / "sub" / "r1.jsonl"))
    w.append({"kind": "event", "seq": 0, "status": "ok"})
    w.append({"kind": "event", "seq": 1, "status": "source_error"})
    recs, off = walmod.read_new(w.path, 0)
    assert [r["seq"] for r in recs] == [0, 1]
    assert off == os.path.getsize(w.path)


def test_wal_serialises_dates(tmp_path):
    """@spec wal_torn_tail_not_consumed"""
    w = walmod.Wal(str(tmp_path / "r.jsonl"))
    w.append({"ran_at": dt.datetime(2026, 6, 24, 1, 2, 3, tzinfo=dt.timezone.utc),
              "business_date": dt.date(2026, 6, 24)})
    recs, _ = walmod.read_new(w.path)
    assert recs[0]["ran_at"] == "2026-06-24T01:02:03+00:00"
    assert recs[0]["business_date"] == "2026-06-24"


def test_wal_partial_trailing_line_not_consumed(tmp_path):
    """A crash mid-write leaves a newline-less tail; read_new must stop before it so the
    offset never advances past durable data (the recovery invariant).

    @spec wal_torn_tail_not_consumed.crash_mid_write"""
    w = walmod.Wal(str(tmp_path / "r.jsonl"))
    w.append({"seq": 0})
    _, off = walmod.read_new(w.path)
    with open(w.path, "ab") as f:
        f.write(b'{"seq":1,"half":')   # no newline — torn write
    recs2, off2 = walmod.read_new(w.path, off)
    assert recs2 == [] and off2 == off
    # completing the line makes it visible
    with open(w.path, "ab") as f:
        f.write(b'true}\n')
    recs3, _ = walmod.read_new(w.path, off)
    assert recs3 == [{"seq": 1, "half": True}]


def test_wal_incremental_offset(tmp_path):
    w = walmod.Wal(str(tmp_path / "r.jsonl"))
    w.append({"seq": 0})
    _, off1 = walmod.read_new(w.path, 0)
    w.append({"seq": 1})
    recs, off2 = walmod.read_new(w.path, off1)   # only the new tail
    assert recs == [{"seq": 1}] and off2 > off1


def test_status_vocab_partitions():
    """@spec run_vocabulary_unvalidated
    @spec event_vocabulary_unvalidated"""
    assert status.RUN_DIED in status.RUN_STATES
    assert status.STOP_BUDGET_TIME in status.STOP_REASONS
    # API-extractor split vs DB-extractor fused leg are distinct words
    assert status.EV_SOURCE_ERROR != status.EV_SINK_ERROR != status.EV_ERROR
    assert status.ERROR_DETAIL_CAP == 2000
