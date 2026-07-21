"""ConnectorxTransport — parallel reads via connectorx (`partition_on`).

Optional extra: `pip install 'feather-flow[connectorx]'`. The top-level
import of `connectorx` is behind try/except so users who don't opt in
never need it installed — but if they DO set transport: connectorx in
feather.yaml without the extra, importing this module raises a clean
ImportError naming the install command.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator
from urllib.parse import quote as _quote

import pyarrow as pa

try:
    import connectorx as cx
except ImportError as e:
    raise ImportError(
        "ConnectorxTransport requires the 'connectorx' extra. "
        "Install it with: pip install 'feather-flow[connectorx]'"
    ) from e

from feather_flow.transports.base import _emit_heartbeats


logger = logging.getLogger(__name__)


_ODBC_KV = re.compile(r"([A-Za-z]+)=({[^}]*}|[^;]*)")


def _odbc_to_connectorx_uri(odbc_conn: str) -> str:
    """Translate a SQL Server ODBC connection string into a connectorx URI.

    connectorx accepts SQLAlchemy-style URLs (`mssql://user:pass@host:port/db`).
    The user already configures the ODBC string in feather.yaml; we don't
    want to force a second URL just for connectorx. So we extract the
    standard fields (SERVER/DATABASE/UID/PWD) and rebuild.
    """
    fields: dict[str, str] = {}
    for m in _ODBC_KV.finditer(odbc_conn):
        key = m.group(1).lower()
        val = m.group(2).strip("{}")
        fields[key] = val

    server = fields.get("server", "")
    database = fields.get("database", "")
    user = fields.get("uid", "")
    password = fields.get("pwd", "")

    if "," in server:
        host, port = server.split(",", 1)
        host_part = f"{host}:{port.strip()}"
    else:
        host_part = server

    # Percent-encode user and password so passwords containing @, /, #, : —
    # common in auto-generated ERP credentials — don't silently misparse as
    # URI delimiters (database path / fragment / port / user-info splitter).
    return f"mssql://{_quote(user, safe='')}:{_quote(password, safe='')}@{host_part}/{database}"


class ConnectorxTransport:
    name = "connectorx"

    def supports_partition_on(self) -> bool:
        return True

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
    ) -> Iterator[pa.RecordBatch]:
        uri = _odbc_to_connectorx_uri(connection_string)
        # TODO(#63): connectorx 0.4.5 also supports return_type="arrow_stream"
        # which yields a pa.RecordBatchReader directly. Switching would
        # avoid assembling each partition fully in RAM before yielding —
        # important for large partition_num. Defer until MSSQL streaming
        # behaviour is verified stable on the operator's connectorx version.
        kwargs: dict[str, object] = {"return_type": "arrow"}
        if partition_on:
            kwargs["partition_on"] = partition_on
            kwargs["partition_num"] = partition_num

        table = cx.read_sql(uri, query, **kwargs)
        # `cx.read_sql(..., return_type="arrow")` returns a pa.Table — split
        # into batches so destinations see the same streaming shape as the
        # other transports.
        schema = table.schema  # available even when the table is empty
        yielded_any = False
        for batch in _emit_heartbeats(
            iter(table.to_batches(max_chunksize=batch_size)),
            table_label=table_label,
            heartbeat_every_rows=heartbeat_every_rows,
            heartbeat_every_seconds=heartbeat_every_seconds,
        ):
            yielded_any = True
            yield batch
        if not yielded_any:
            # Match PyodbcTransport / ArrowOdbcTransport: every call yields
            # >=1 batch so the destination can derive the schema from the
            # first one even on zero-row queries.
            yield pa.RecordBatch.from_pylist([], schema=schema)
