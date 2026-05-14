"""ArrowOdbcTransport — wraps arrow_odbc.read_arrow_batches_from_odbc.

Mocks at the module level so the test doesn't need an actual ODBC driver
or SQL Server. Equivalence with PyodbcTransport on real data is covered
by the operator script (Task 7).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa

from feather_etl.transports.arrow_odbc_transport import ArrowOdbcTransport


def test_name_and_does_not_support_partition_on() -> None:
    t = ArrowOdbcTransport()
    assert t.name == "arrow-odbc"
    assert t.supports_partition_on() is False


@patch("feather_etl.transports.arrow_odbc_transport.read_arrow_batches_from_odbc")
def test_stream_batches_passes_through_arrow_batches(mock_reader: MagicMock) -> None:
    canned = [
        pa.RecordBatch.from_pylist([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]),
        pa.RecordBatch.from_pylist([{"id": 3, "name": "c"}]),
    ]
    mock_reader.return_value = iter(canned)

    out = list(
        ArrowOdbcTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=x",
            "SELECT id, name FROM t",
            batch_size=100,
            table_label="t",
        )
    )
    assert out == canned

    kwargs = mock_reader.call_args.kwargs
    assert kwargs["query"] == "SELECT id, name FROM t"
    assert kwargs["connection_string"] == "DRIVER={ODBC Driver 18};SERVER=x"
    # arrow-odbc 10.4.0 uses the keyword `batch_size`.
    assert kwargs["batch_size"] == 100


@patch("feather_etl.transports.arrow_odbc_transport.read_arrow_batches_from_odbc")
def test_empty_result_yields_no_batches(mock_reader: MagicMock) -> None:
    mock_reader.return_value = iter([])
    assert (
        list(
            ArrowOdbcTransport().stream_batches(
                "X", "SELECT 1 WHERE 0=1", batch_size=10, table_label="empty"
            )
        )
        == []
    )
