"""_emit_heartbeats wraps any RecordBatch iterator with the same row/time
thresholds fetch_batches uses today."""

from __future__ import annotations

import logging
from typing import Iterator

import pyarrow as pa
import pytest

from feather_etl.transports.base import _emit_heartbeats


def _batch(n: int) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist([{"id": i} for i in range(n)])


def _drain(it: Iterator[pa.RecordBatch]) -> int:
    return sum(b.num_rows for b in it)


def test_passthrough_yields_all_batches() -> None:
    out = _emit_heartbeats(
        iter([_batch(5), _batch(7)]),
        table_label="t",
        heartbeat_every_rows=1_000,
        heartbeat_every_seconds=1_000,
    )
    assert _drain(out) == 12


def test_row_threshold_emits_heartbeat(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="feather_etl.transports.base")
    out = _emit_heartbeats(
        iter([_batch(50), _batch(50), _batch(50)]),
        table_label="t",
        heartbeat_every_rows=100,
        heartbeat_every_seconds=0,
        clock=lambda: 0.0,
    )
    _drain(out)
    msgs = [r for r in caplog.records if r.message == "extract_heartbeat"]
    assert len(msgs) == 1
    assert msgs[0].__dict__["rows_loaded"] == 100


def test_time_threshold_emits_heartbeat(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="feather_etl.transports.base")
    tick = iter([0.0, 0.5, 1.5, 2.5])
    out = _emit_heartbeats(
        iter([_batch(1), _batch(1), _batch(1)]),
        table_label="t",
        heartbeat_every_rows=0,
        heartbeat_every_seconds=1,
        clock=lambda: next(tick),
    )
    _drain(out)
    msgs = [r for r in caplog.records if r.message == "extract_heartbeat"]
    assert len(msgs) == 2


def test_both_thresholds_zero_disables_heartbeat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="feather_etl.transports.base")
    out = _emit_heartbeats(
        iter([_batch(10_000), _batch(10_000)]),
        table_label="t",
        heartbeat_every_rows=0,
        heartbeat_every_seconds=0,
    )
    _drain(out)
    msgs = [r for r in caplog.records if r.message == "extract_heartbeat"]
    assert msgs == []
