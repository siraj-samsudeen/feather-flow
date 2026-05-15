"""ConnectorxTransport — parallel reads via connectorx.

Mocks connectorx.read_sql; real-DB equivalence lives in
scripts/manual_transport_equivalence.py (Task 7).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from feather_etl.transports.connectorx_transport import (
    ConnectorxTransport,
    _odbc_to_connectorx_uri,
)


def test_name_and_supports_partition_on() -> None:
    t = ConnectorxTransport()
    assert t.name == "connectorx"
    assert t.supports_partition_on() is True


@patch("feather_etl.transports.connectorx_transport.cx")
def test_stream_batches_forwards_partition_args(mock_cx: MagicMock) -> None:
    canned = pa.Table.from_pylist([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    mock_cx.read_sql.return_value = canned

    out = list(
        ConnectorxTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=host;DATABASE=db;UID=u;PWD=p",
            "SELECT id, name FROM t",
            batch_size=100,
            table_label="t",
            partition_on="id",
            partition_num=8,
        )
    )

    total_rows = sum(b.num_rows for b in out)
    assert total_rows == 2

    kwargs = mock_cx.read_sql.call_args.kwargs
    assert kwargs["partition_on"] == "id"
    assert kwargs["partition_num"] == 8
    assert kwargs["return_type"] == "arrow"


@patch("feather_etl.transports.connectorx_transport.cx")
def test_stream_batches_without_partition_on(mock_cx: MagicMock) -> None:
    """Non-parallel single-connection mode still works — partition_on is optional."""
    canned = pa.Table.from_pylist([{"id": 1}])
    mock_cx.read_sql.return_value = canned

    out = list(
        ConnectorxTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=host;DATABASE=db;UID=u;PWD=p",
            "SELECT id FROM t",
            batch_size=100,
            table_label="t",
        )
    )
    assert sum(b.num_rows for b in out) == 1
    kwargs = mock_cx.read_sql.call_args.kwargs
    assert "partition_on" not in kwargs or kwargs["partition_on"] is None


def test_odbc_to_connectorx_uri_translates_known_fields() -> None:
    uri = _odbc_to_connectorx_uri(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=host.example.com,1433;DATABASE=mydb;"
        "UID=myuser;PWD=p@ss;TrustServerCertificate=yes"
    )
    assert uri.startswith("mssql://")
    assert "myuser" in uri
    assert "mydb" in uri
    assert "host.example.com" in uri


def test_missing_connectorx_dep_raises_clear_message(monkeypatch) -> None:
    """If the extra isn't installed, instantiating the transport must raise
    a message that names the right install command — no AttributeError /
    'NoneType' confusion."""
    import sys

    monkeypatch.setitem(sys.modules, "connectorx", None)

    import importlib
    import feather_etl.transports.connectorx_transport as mod

    with pytest.raises(ImportError) as exc:
        importlib.reload(mod)
    assert "feather-etl[connectorx]" in str(exc.value)
