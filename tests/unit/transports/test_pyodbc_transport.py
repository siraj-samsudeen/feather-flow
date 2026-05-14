"""PyodbcTransport — wraps the existing fetchmany loop. Mocks pyodbc."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from feather_etl.transports.pyodbc_transport import PyodbcTransport


FAKE_CONN = "DRIVER={ODBC Driver 18};SERVER=fake;DATABASE=db"


def _cursor_with(rows: list[tuple], description: list[tuple]) -> MagicMock:
    cur = MagicMock()
    cur.description = description
    cur.fetchmany.side_effect = [rows, []]
    return cur


def test_name_and_does_not_support_partition_on() -> None:
    t = PyodbcTransport()
    assert t.name == "pyodbc"
    assert t.supports_partition_on() is False


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_stream_batches_yields_record_batches(mock_pyodbc: MagicMock) -> None:
    cursor = _cursor_with(
        rows=[(1, "a"), (2, "b")],
        description=[
            ("id", int, None, None, None, None, None),
            ("name", str, None, None, None, None, None),
        ],
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    out = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN,
            "SELECT id, name FROM x",
            batch_size=100,
            table_label="x",
        )
    )

    assert len(out) == 1
    batch = out[0]
    assert isinstance(batch, pa.RecordBatch)
    assert batch.column_names == ["id", "name"]
    assert batch.column("id").to_pylist() == [1, 2]
    assert batch.column("name").to_pylist() == ["a", "b"]

    calls = [c.args[0] for c in cursor.execute.call_args_list]
    assert calls[0] == "SET NOCOUNT ON"
    assert calls[1] == "SELECT id, name FROM x"


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_multi_batch_fetch_yields_one_record_batch_per_chunk(
    mock_pyodbc: MagicMock,
) -> None:
    """The normal production workload is N>1 populated fetchmany returns;
    confirm the while-loop yields one RecordBatch per non-empty chunk."""
    cursor = MagicMock()
    cursor.description = [("id", int, None, None, None, None, None)]
    cursor.fetchmany.side_effect = [[(1,)], [(2,)], [(3,), (4,)], []]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "SELECT id FROM t", batch_size=2, table_label="t"
        )
    )

    assert [b.column("id").to_pylist() for b in batches] == [[1], [2], [3, 4]]


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_decimal_and_uuid_are_coerced(mock_pyodbc: MagicMock) -> None:
    import decimal
    import uuid

    val_uuid = uuid.uuid4()
    cursor = _cursor_with(
        rows=[(decimal.Decimal("3.14"), val_uuid)],
        description=[
            ("price", decimal.Decimal, None, None, None, None, None),
            ("id", uuid.UUID, None, None, None, None, None),
        ],
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "SELECT price, id FROM t", batch_size=100, table_label="t"
        )
    )

    assert batches[0].column("price").to_pylist() == [3.14]
    assert batches[0].column("id").to_pylist() == [str(val_uuid)]


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_zero_row_query_yields_schema_only_batch(mock_pyodbc: MagicMock) -> None:
    """Preserves the fetch_batches contract: every call yields >=1 batch
    so the destination's schema-from-first-batch path keeps working."""
    cursor = MagicMock()
    cursor.description = [
        ("id", int, None, None, None, None, None),
    ]
    cursor.fetchmany.side_effect = [[]]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "SELECT id FROM empty", batch_size=100, table_label="empty"
        )
    )
    assert len(batches) == 1
    assert batches[0].num_rows == 0
    assert batches[0].column_names == ["id"]


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_no_description_query_yields_nothing(mock_pyodbc: MagicMock) -> None:
    """e.g. an UPDATE in a query slot — cursor.description is None."""
    cursor = MagicMock()
    cursor.description = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "UPDATE t SET x=1", batch_size=100, table_label="t"
        )
    )
    assert batches == []
