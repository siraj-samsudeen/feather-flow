"""#61 acceptance — all three transports surface identical Arrow schema +
row count via SqlServerSource.extract_batches.

In CI we don't have a SQL Server fixture, so we stub each transport to
yield the same canned batches. Real-DB equivalence lives in
scripts/manual_transport_equivalence.py (Task 7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pytest

from feather_flow.sources.sqlserver import SqlServerSource


CANNED_BATCHES = [
    pa.RecordBatch.from_pylist(
        [{"id": 1, "name": "alice", "price": 9.99}],
    ),
    pa.RecordBatch.from_pylist(
        [{"id": 2, "name": "bob", "price": 19.99}],
    ),
]


def _make_source(tmp_path: Path, transport: str) -> SqlServerSource:
    return SqlServerSource.from_yaml(
        {
            "type": "sqlserver",
            "name": "t",
            "host": "h",
            "port": 1433,
            "user": "u",
            "password": "p",
            "database": "d",
            "transport": transport,
        },
        tmp_path,
    )


def _stub(self, *args, **kwargs) -> Iterator[pa.RecordBatch]:
    yield from CANNED_BATCHES


@pytest.mark.parametrize("transport", ["pyodbc", "arrow-odbc", "connectorx"])
def test_extract_batches_identical_across_transports(
    monkeypatch, tmp_path: Path, transport: str
) -> None:
    monkeypatch.setattr(
        "feather_flow.transports.pyodbc_transport.PyodbcTransport.stream_batches",
        _stub,
    )
    monkeypatch.setattr(
        "feather_flow.transports.arrow_odbc_transport.ArrowOdbcTransport.stream_batches",
        _stub,
    )
    monkeypatch.setattr(
        "feather_flow.transports.connectorx_transport.ConnectorxTransport.stream_batches",
        _stub,
    )

    source = _make_source(tmp_path, transport)
    batches = list(source.extract_batches("dbo.t"))

    rows = sum(b.num_rows for b in batches)
    assert rows == 2
    schema = batches[0].schema
    assert schema.names == ["id", "name", "price"]
    # Pin types so a future change to one transport's coercion path
    # is caught.
    assert schema.field("id").type == pa.int64()
    assert schema.field("name").type == pa.string()
    assert schema.field("price").type == pa.float64()
