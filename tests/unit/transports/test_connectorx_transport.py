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


@patch("feather_etl.transports.connectorx_transport.cx")
def test_empty_result_yields_one_schema_only_batch(mock_cx: MagicMock) -> None:
    """Match PyodbcTransport / ArrowOdbcTransport: even an empty result set
    yields one zero-row batch carrying the schema, so destinations can
    CREATE TABLE from the first batch."""
    empty_table = pa.table(
        {"id": pa.array([], type=pa.int64()), "name": pa.array([], type=pa.string())}
    )
    mock_cx.read_sql.return_value = empty_table

    out = list(
        ConnectorxTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=h;DATABASE=db;UID=u;PWD=p",
            "SELECT id, name FROM t WHERE 1=0",
            batch_size=100,
            table_label="empty",
        )
    )
    assert len(out) == 1
    assert out[0].num_rows == 0
    assert out[0].schema.names == ["id", "name"]


def test_odbc_to_connectorx_uri_translates_known_fields() -> None:
    uri = _odbc_to_connectorx_uri(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=host.example.com,1433;DATABASE=mydb;"
        "UID=myuser;PWD=plainpw;TrustServerCertificate=yes"
    )
    # Pin the exact output so a regression in any field (port split,
    # bracket-stripping, key lowercasing, ordering) is caught.
    assert uri == "mssql://myuser:plainpw@host.example.com:1433/mydb"


def test_odbc_to_connectorx_uri_percent_encodes_user_and_password() -> None:
    """Passwords containing @, /, #, : would silently misparse as URI
    delimiters without percent-encoding. ERP-generated credentials
    frequently contain these characters, so this isn't hypothetical."""
    uri = _odbc_to_connectorx_uri(
        "DRIVER={ODBC Driver 18};SERVER=h;DATABASE=db;UID=u@corp;PWD=p/w#:@x"
    )
    # u@corp -> u%40corp
    # p/w#:@x -> p%2Fw%23%3A%40x
    assert uri == "mssql://u%40corp:p%2Fw%23%3A%40x@h/db"


def test_missing_connectorx_dep_raises_clear_message(monkeypatch) -> None:
    """If the extra isn't installed, instantiating the transport must raise
    a message that names the right install command — no AttributeError /
    'NoneType' confusion."""
    import sys
    import importlib
    import feather_etl.transports.connectorx_transport as mod

    # Snapshot the real, working module before reload so later tests don't
    # inherit a half-initialised module object cached in sys.modules.
    real_mod = sys.modules["feather_etl.transports.connectorx_transport"]
    monkeypatch.setitem(sys.modules, "connectorx", None)

    with pytest.raises(ImportError) as exc:
        importlib.reload(mod)
    assert "feather-etl[connectorx]" in str(exc.value)

    # Restore the working module so other tests don't inherit our wreckage.
    sys.modules["feather_etl.transports.connectorx_transport"] = real_mod
