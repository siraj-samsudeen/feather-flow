"""Shared fetchmany → RecordBatch generator with progress heartbeat.

Used by SQL Server (control-plane only post #61), MySQL, and Postgres
sources. Owns the per-batch fetch loop, per-DB row coercion (via a
callback), and heartbeat emission so the three concrete sources stay free
of duplication.

Heartbeats fire on whichever cadence trips first: row count or wall time.
They are emitted as `logger.info` records with structured `extra` fields
so `pipeline._JsonlFormatter` picks them up into `feather_log.jsonl`.

# TODO(#61): dedupe with transports.base._emit_heartbeats — the cadence
# math is identical. Deferred because tests/unit/sources/test_fetch_batches.py
# imports `_should_emit_heartbeat` directly and asserts on the
# `feather_etl.sources.fetch_batches` logger, so a clean swap requires
# touching that test file too.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator

import pyarrow as pa


logger = logging.getLogger(__name__)


def _should_emit_heartbeat(
    rows_since_last_emit: int,
    seconds_since_last_emit: float,
    every_rows: int,
    every_seconds: int,
) -> bool:
    """Fire when the row-count or wall-time threshold is met. Zero = trigger off."""
    by_rows = every_rows > 0 and rows_since_last_emit >= every_rows
    by_time = every_seconds > 0 and seconds_since_last_emit >= every_seconds
    return by_rows or by_time


def fetch_batches(
    cursor: Any,
    arrow_schema: pa.Schema,
    col_names: list[str],
    batch_size: int,
    table_label: str,
    coerce_row: Callable[[Any, str], Any] | None = None,
    heartbeat_every_rows: int = 100_000,
    heartbeat_every_seconds: int = 30,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[pa.RecordBatch]:
    """Yield `pa.RecordBatch` chunks from a DB-API cursor, emitting heartbeats.

    Always yields at least one batch (a zero-row, schema-only batch when
    the query result is empty) so callers can rely on the first yielded
    batch to carry the schema.

    Args:
        cursor: an executed DB-API cursor positioned before the first row.
        arrow_schema: target PyArrow schema built from cursor.description.
        col_names: column names in order, used to build the per-batch
            column-oriented dict before `RecordBatch.from_pydict`.
        batch_size: max rows per `fetchmany` call.
        table_label: human-readable table name for heartbeat log lines.
        coerce_row: optional per-cell coercion `(value, col_name) -> value`.
            Each source plugs in its own (Decimal → float etc.). Skipped
            entirely when None.
        heartbeat_every_rows: emit one heartbeat per N rows fetched. 0 = off.
        heartbeat_every_seconds: emit one heartbeat per M seconds elapsed
            since the last emission. 0 = off.
        clock: injectable monotonic clock for tests.
    """
    start_time = clock()
    last_emit_time = start_time
    last_emit_rows = 0
    rows_fetched = 0
    yielded_any = False

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break

        col_data: dict[str, list] = {name: [] for name in col_names}
        for row in rows:
            for i, name in enumerate(col_names):
                val = row[i]
                if coerce_row is not None:
                    val = coerce_row(val, name)
                col_data[name].append(val)

        batch = pa.RecordBatch.from_pydict(col_data, schema=arrow_schema)
        rows_fetched += batch.num_rows
        yield batch
        yielded_any = True

        now = clock()
        if _should_emit_heartbeat(
            rows_since_last_emit=rows_fetched - last_emit_rows,
            seconds_since_last_emit=now - last_emit_time,
            every_rows=heartbeat_every_rows,
            every_seconds=heartbeat_every_seconds,
        ):
            _emit_heartbeat(table_label, rows_fetched, now - start_time)
            last_emit_time = now
            last_emit_rows = rows_fetched

    if not yielded_any:
        # Always yield at least one (empty) batch so callers receive the
        # schema even on zero-row queries. The destination session relies
        # on the first batch to CREATE the staging table.
        yield pa.RecordBatch.from_pydict(
            {name: [] for name in col_names}, schema=arrow_schema
        )


def _emit_heartbeat(table: str, rows_fetched: int, elapsed_s: float) -> None:
    """Emit one heartbeat log record."""
    rows_per_sec = int(rows_fetched / elapsed_s) if elapsed_s > 0 else 0
    logger.info(
        "extract_heartbeat",
        extra={
            "table": table,
            "rows_loaded": rows_fetched,
            "elapsed_s": round(elapsed_s, 1),
            "rows_per_sec": rows_per_sec,
        },
    )
