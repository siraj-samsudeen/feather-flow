"""PyodbcTransport — pyodbc-backed batch streamer.

Owns the cursor lifecycle + per-row Python->Arrow coercion + Arrow
schema construction from `cursor.description`. The behaviour is the
verbatim pyodbc loop that previously lived inside
`sources/sqlserver.py:extract_batches`. Lifted here so the source layer
can become transport-agnostic.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from typing import Any, Iterator

import pyarrow as pa
import pyodbc

from feather_etl.transports.base import _emit_heartbeats


logger = logging.getLogger(__name__)


_PYODBC_TYPE_MAP: dict[type, pa.DataType] = {
    int: pa.int64(),
    float: pa.float64(),
    str: pa.string(),
    bool: pa.bool_(),
    bytes: pa.binary(),
    bytearray: pa.binary(),
    decimal.Decimal: pa.float64(),
    datetime.datetime: pa.timestamp("us"),
    datetime.date: pa.date32(),
    datetime.time: pa.time64("us"),
    uuid.UUID: pa.string(),
}


def _pyodbc_type_to_arrow(type_code: type) -> pa.DataType:
    return _PYODBC_TYPE_MAP.get(type_code, pa.string())


def _coerce(val: Any, _col: str) -> Any:
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    return val


class PyodbcTransport:
    name = "pyodbc"

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
        con = pyodbc.connect(connection_string)
        try:
            cursor = con.cursor()
            try:
                cursor.execute("SET NOCOUNT ON")
                cursor.execute(query)

                if cursor.description is None:
                    return

                col_names = [d[0] for d in cursor.description]
                col_types = [_pyodbc_type_to_arrow(d[1]) for d in cursor.description]
                schema = pa.schema(
                    [pa.field(n, t) for n, t in zip(col_names, col_types)]
                )

                yield from _emit_heartbeats(
                    self._iter_cursor(cursor, schema, col_names, batch_size),
                    table_label=table_label,
                    heartbeat_every_rows=heartbeat_every_rows,
                    heartbeat_every_seconds=heartbeat_every_seconds,
                )
            finally:
                cursor.close()
        finally:
            con.close()

    @staticmethod
    def _iter_cursor(
        cursor: Any,
        schema: pa.Schema,
        col_names: list[str],
        batch_size: int,
    ) -> Iterator[pa.RecordBatch]:
        yielded_any = False
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            cols: dict[str, list] = {n: [] for n in col_names}
            for row in rows:
                for i, n in enumerate(col_names):
                    cols[n].append(_coerce(row[i], n))
            yield pa.RecordBatch.from_pydict(cols, schema=schema)
            yielded_any = True
        if not yielded_any:
            yield pa.RecordBatch.from_pydict(
                {n: [] for n in col_names}, schema=schema
            )
