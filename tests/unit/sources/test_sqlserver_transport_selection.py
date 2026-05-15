"""SqlServerSource resolves transport from feather.yaml — closes #61."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from feather_etl.sources.sqlserver import SqlServerSource


_CONN_KWARGS = dict(
    host="example.com",
    port=1433,
    user="u",
    password="p",
    database="db",
)


def _yaml_entry(**overrides) -> dict:
    return {"type": "sqlserver", "name": "src", **_CONN_KWARGS, **overrides}


def test_default_transport_is_arrow_odbc(tmp_path: Path) -> None:
    src = SqlServerSource.from_yaml(_yaml_entry(), tmp_path)
    assert src.transport_name == "arrow-odbc"


def test_explicit_pyodbc_transport(tmp_path: Path) -> None:
    src = SqlServerSource.from_yaml(_yaml_entry(transport="pyodbc"), tmp_path)
    assert src.transport_name == "pyodbc"


def test_explicit_connectorx_transport(tmp_path: Path) -> None:
    src = SqlServerSource.from_yaml(_yaml_entry(transport="connectorx"), tmp_path)
    assert src.transport_name == "connectorx"


def test_supports_partition_on_reflects_transport_choice(tmp_path: Path) -> None:
    """#63's planner reads source.supports_partition_on to gate the
    large-bucket parallel-reads recommendation. Only connectorx
    supports it."""
    pyodbc_src = SqlServerSource.from_yaml(_yaml_entry(transport="pyodbc"), tmp_path)
    arrow_src = SqlServerSource.from_yaml(_yaml_entry(transport="arrow-odbc"), tmp_path)
    cx_src = SqlServerSource.from_yaml(_yaml_entry(transport="connectorx"), tmp_path)

    assert pyodbc_src.supports_partition_on is False
    assert arrow_src.supports_partition_on is False
    assert cx_src.supports_partition_on is True


def test_unknown_transport_raises_at_yaml_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        SqlServerSource.from_yaml(_yaml_entry(transport="bogus"), tmp_path)
    msg = str(exc.value)
    assert "bogus" in msg
    for name in ("pyodbc", "arrow-odbc", "connectorx"):
        assert name in msg


def test_extract_batches_delegates_to_transport(monkeypatch, tmp_path: Path) -> None:
    """When extract_batches is called, the resolved transport's
    stream_batches is invoked with the built SELECT — no pyodbc.connect
    happens directly on the source."""
    src = SqlServerSource.from_yaml(_yaml_entry(transport="pyodbc"), tmp_path)

    captured: dict = {}

    def fake_stream(self, conn, query, *, batch_size, table_label, **kwargs):
        captured["conn"] = conn
        captured["query"] = query
        captured["batch_size"] = batch_size
        captured["table_label"] = table_label
        yield pa.RecordBatch.from_pylist([{"id": 1}])

    monkeypatch.setattr(
        "feather_etl.transports.pyodbc_transport.PyodbcTransport.stream_batches",
        fake_stream,
    )

    batches = list(
        src.extract_batches(
            "dbo.t",
            columns=["id"],
            filter="region = 'EU'",
            watermark_column="updated_at",
            watermark_value="2026-05-12",
        )
    )

    assert len(batches) == 1
    assert "SELECT [id] FROM dbo.t" in captured["query"]
    assert "region = 'EU'" in captured["query"]
    assert "updated_at > '2026-05-12'" in captured["query"]
    assert captured["batch_size"] == src.batch_size
    assert captured["table_label"] == "dbo.t"
