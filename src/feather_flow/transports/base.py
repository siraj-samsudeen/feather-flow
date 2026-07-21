"""Transport protocol + shared heartbeat helper.

Transports own *how* batches are pulled off the wire (pyodbc cursor /
arrow-odbc Rust loop / connectorx parallel partitions). The Source layer
above them is single-pyodbc-free: it only builds the query string,
delegates to `transport.stream_batches`, and forwards the resulting
batches to the destination.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterator, Protocol

import pyarrow as pa


logger = logging.getLogger(__name__)


class Transport(Protocol):
    """Pluggable batch-stream provider for a database source.

    Implementations live in sibling modules; `registry.get_transport_class`
    resolves a name to a class. `partition_on` / `partition_num` are
    accepted by every transport for a uniform signature but only
    `ConnectorxTransport.supports_partition_on()` returns True.
    """

    name: str

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,
        partition_num: int = 4,
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]: ...

    def supports_partition_on(self) -> bool: ...


def _should_emit(
    rows_since_last: int,
    secs_since_last: float,
    every_rows: int,
    every_seconds: int,
) -> bool:
    by_rows = every_rows > 0 and rows_since_last >= every_rows
    by_time = every_seconds > 0 and secs_since_last >= every_seconds
    return by_rows or by_time


def _emit_heartbeats(
    batches: Iterator[pa.RecordBatch],
    *,
    table_label: str,
    heartbeat_every_rows: int,
    heartbeat_every_seconds: int,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[pa.RecordBatch]:
    """Yield each batch unchanged, emitting `extract_heartbeat` log records
    on the same row/time cadence `fetch_batches` uses today.

    Lifted from `sources/fetch_batches.py` so every transport can wrap its
    output uniformly.
    """
    start = clock()
    last_emit_time = start
    last_emit_rows = 0
    rows = 0
    for batch in batches:
        yield batch
        rows += batch.num_rows
        now = clock()
        if _should_emit(
            rows_since_last=rows - last_emit_rows,
            secs_since_last=now - last_emit_time,
            every_rows=heartbeat_every_rows,
            every_seconds=heartbeat_every_seconds,
        ):
            elapsed = now - start
            rps = int(rows / elapsed) if elapsed > 0 else 0
            logger.info(
                "extract_heartbeat",
                extra={
                    "table": table_label,
                    "rows_loaded": rows,
                    "elapsed_s": round(elapsed, 1),
                    "rows_per_sec": rps,
                },
            )
            last_emit_time = now
            last_emit_rows = rows
