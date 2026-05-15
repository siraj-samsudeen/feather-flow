"""ArrowOdbcTransport — bounded-memory streaming via arrow-odbc.

`arrow_odbc.read_arrow_batches_from_odbc` returns a BatchReader; iterating
it yields native pa.RecordBatch instances. No Python-row coercion needed
because the underlying Rust crate maps SQL->Arrow directly.

Cleanup note: arrow-odbc's BatchReader releases its ODBC handle via
``__del__``, not a context manager. If the generator is interrupted
mid-fetch, the handle stays open until the local reference is dropped by
the GC. Under CPython that is prompt (reference-counted), but anything
holding a reference to a partly-consumed iterator delays cleanup.
"""

from __future__ import annotations

import logging
from typing import Iterator

import pyarrow as pa
from arrow_odbc import read_arrow_batches_from_odbc

from feather_etl.transports.base import _emit_heartbeats


logger = logging.getLogger(__name__)


class ArrowOdbcTransport:
    name = "arrow-odbc"

    def supports_partition_on(self) -> bool:
        return False

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,  # ignored
        partition_num: int = 4,  # ignored
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]:
        reader = read_arrow_batches_from_odbc(
            query=query,
            connection_string=connection_string,
            batch_size=batch_size,
            # Bounded RSS is the reason this transport is the default.
            # `fetch_concurrently=True` halves wall time at the cost of
            # roughly 2x memory; flip back if throughput dominates RSS.
            fetch_concurrently=False,
        )
        schema = reader.schema  # available before iteration
        yielded_any = False
        for batch in _emit_heartbeats(
            reader,
            table_label=table_label,
            heartbeat_every_rows=heartbeat_every_rows,
            heartbeat_every_seconds=heartbeat_every_seconds,
        ):
            yielded_any = True
            yield batch
        if not yielded_any:
            # Match PyodbcTransport's contract: every call yields >=1 batch
            # so the destination can derive the schema from the first one.
            yield pa.RecordBatch.from_pylist([], schema=schema)
