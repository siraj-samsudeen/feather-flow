"""Tests for SQL Server source — all pyodbc calls mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from feather_etl.sources.sqlserver import SqlServerSource
from tests.helpers import write_config

FAKE_CONN_STR = "DRIVER={ODBC Driver 18};SERVER=fake;DATABASE=testdb"


@pytest.fixture
def source() -> SqlServerSource:
    return SqlServerSource(connection_string=FAKE_CONN_STR, batch_size=100)


# --- extract ---


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_full_extract_loads_rows(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Full extract returns a PyArrow table with correct columns and rows."""
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("id", int, None, None, None, None, None),
        ("name", str, None, None, None, None, None),
        ("amount", float, None, None, None, None, None),
    ]
    mock_cursor.fetchmany.side_effect = [
        [(1, "Alice", 100.0), (2, "Bob", 200.0)],
        [],  # end of results
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.extract("dbo.orders")

    assert isinstance(result, pa.Table)
    assert result.num_rows == 2
    assert result.column_names == ["id", "name", "amount"]
    assert result.column("id").to_pylist() == [1, 2]
    assert result.column("name").to_pylist() == ["Alice", "Bob"]
    # Verify SET NOCOUNT ON was called
    mock_cursor.execute.assert_any_call("SET NOCOUNT ON")


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_incremental_extract_with_watermark(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Incremental extract builds WHERE clause with watermark."""
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("id", int, None, None, None, None, None),
        ("updated_at", str, None, None, None, None, None),
    ]
    mock_cursor.fetchmany.side_effect = [
        [(3, "2026-03-27")],
        [],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.extract(
        "dbo.orders",
        watermark_column="updated_at",
        watermark_value="2026-03-26",
    )

    assert result.num_rows == 1
    # Verify the query included watermark WHERE clause
    calls = [str(c) for c in mock_cursor.execute.call_args_list]
    query_call = [c for c in calls if "updated_at" in c]
    assert len(query_call) == 1
    assert "updated_at > '2026-03-26'" in query_call[0]


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_extract_with_filter_and_columns(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Extract respects column list and filter clause."""
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("id", int, None, None, None, None, None),
        ("name", str, None, None, None, None, None),
    ]
    mock_cursor.fetchmany.side_effect = [
        [(1, "Alice")],
        [],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.extract(
        "dbo.orders",
        columns=["id", "name"],
        filter="status = 'active'",
    )

    assert result.num_rows == 1
    calls = [str(c) for c in mock_cursor.execute.call_args_list]
    query_call = [c for c in calls if "[id], [name]" in c]
    assert len(query_call) == 1
    assert "status = 'active'" in query_call[0]


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_empty_table(mock_pyodbc: MagicMock, source: SqlServerSource) -> None:
    """Extract on empty table returns empty table with correct schema."""
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("id", int, None, None, None, None, None),
        ("name", str, None, None, None, None, None),
    ]
    mock_cursor.fetchmany.return_value = []  # no rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.extract("dbo.empty_table")

    assert isinstance(result, pa.Table)
    assert result.num_rows == 0
    assert result.column_names == ["id", "name"]


# --- change detection ---


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_change_detection_first_run(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """First run (no last_state) returns changed=True with reason=first_run."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (12345, 100)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.detect_changes("dbo.orders", last_state=None)

    assert result.changed is True
    assert result.reason == "first_run"
    assert result.metadata["checksum"] == 12345
    assert result.metadata["row_count"] == 100


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_change_detection_skip(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Unchanged checksum+row_count returns changed=False."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (12345, 100)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    last_state = {"last_checksum": 12345, "last_row_count": 100}
    result = source.detect_changes("dbo.orders", last_state=last_state)

    assert result.changed is False
    assert result.reason == "unchanged"


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_change_detection_changed(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Changed checksum returns changed=True with new metadata."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (99999, 150)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    last_state = {"last_checksum": 12345, "last_row_count": 100}
    result = source.detect_changes("dbo.orders", last_state=last_state)

    assert result.changed is True
    assert result.reason == "checksum_changed"
    assert result.metadata["checksum"] == 99999
    assert result.metadata["row_count"] == 150


def test_sqlserver_change_detection_incremental_always_changed(
    source: SqlServerSource,
) -> None:
    """Incremental strategy always returns changed=True (watermark handles skip)."""
    last_state = {
        "strategy": "incremental",
        "last_checksum": 12345,
        "last_row_count": 100,
    }
    result = source.detect_changes("dbo.orders", last_state=last_state)

    assert result.changed is True
    assert result.reason == "incremental"


# --- discover ---


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_discover_returns_schemas(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Discover returns StreamSchema list from INFORMATION_SCHEMA."""
    mock_cursor = MagicMock()
    # First call: list tables
    # Second call: columns for first table
    # Third call: columns for second table
    mock_cursor.fetchall.side_effect = [
        [("dbo", "orders"), ("dbo", "customers")],
        [("id", "int"), ("total", "decimal")],
        [("id", "int"), ("name", "nvarchar")],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    schemas = source.discover()

    assert len(schemas) == 2
    assert schemas[0].name == "dbo.orders"
    assert schemas[0].columns == [("id", "int"), ("total", "decimal")]
    assert schemas[0].supports_incremental is True
    assert schemas[1].name == "dbo.customers"
    assert schemas[1].columns == [("id", "int"), ("name", "nvarchar")]


# --- get_schema ---


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_get_schema(mock_pyodbc: MagicMock, source: SqlServerSource) -> None:
    """get_schema returns column list for a single table."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [("id", "int"), ("name", "nvarchar")]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    cols = source.get_schema("dbo.orders")

    assert cols == [("id", "int"), ("name", "nvarchar")]


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_get_schema_unqualified(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """get_schema with unqualified name defaults to dbo schema."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [("id", "int")]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    cols = source.get_schema("orders")

    assert cols == [("id", "int")]
    # Verify it used 'dbo' as schema
    execute_calls = mock_cursor.execute.call_args_list
    assert execute_calls[0][0][1] == ["dbo", "orders"]


# --- connection ---


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_check_success(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """check() returns True on successful connection."""
    mock_conn = MagicMock()
    mock_pyodbc.connect.return_value = mock_conn

    assert source.check() is True
    mock_conn.close.assert_called_once()


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_connection_failure(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """check() returns False when connection fails and populates _last_error."""
    mock_pyodbc.Error = Exception
    mock_pyodbc.connect.side_effect = Exception("Connection refused")

    assert source.check() is False
    assert source._last_error is not None
    assert "Connection refused" in source._last_error
    assert (
        "Hint: ODBC Driver 18 for SQL Server is not installed."
        not in source._last_error
    )


@patch("feather_etl.sources.sqlserver.platform.system", return_value="Darwin")
@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_missing_driver18_open_lib_adds_hint(
    mock_pyodbc: MagicMock, _mock_platform_system: MagicMock, source: SqlServerSource
) -> None:
    """Missing Driver 18 should append install guidance."""
    mock_pyodbc.Error = Exception
    mock_pyodbc.connect.side_effect = Exception(
        "[01000] [unixODBC][Driver Manager]Can't open lib 'ODBC Driver 18 for SQL Server' : file not found"
    )

    assert source.check() is False
    assert source._last_error is not None
    assert "Hint: ODBC Driver 18 for SQL Server is not installed." in source._last_error
    assert "brew install msodbcsql18" in source._last_error


@patch("feather_etl.sources.sqlserver.platform.system", return_value="Windows")
@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_im002_with_driver18_adds_hint(
    mock_pyodbc: MagicMock, _mock_platform_system: MagicMock, source: SqlServerSource
) -> None:
    """IM002 + explicit Driver 18 should append install guidance."""
    mock_pyodbc.Error = Exception
    mock_pyodbc.connect.side_effect = Exception(
        "('IM002', '[IM002] [Microsoft][ODBC Driver Manager] Data source name not found and no default driver specified (0) (SQLDriverConnect)')"
    )

    assert source.check() is False
    assert source._last_error is not None
    assert "Hint: ODBC Driver 18 for SQL Server is not installed." in source._last_error
    assert "download-odbc-driver-for-sql-server" in source._last_error


@patch("feather_etl.sources.sqlserver.platform.system", return_value="Windows")
@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_im002_for_dsn_does_not_add_driver18_hint(
    mock_pyodbc: MagicMock, _mock_platform_system: MagicMock
) -> None:
    """IM002 with DSN-only config should not be mislabeled as missing Driver 18."""
    source = SqlServerSource(connection_string="DSN=MissingERP")
    mock_pyodbc.Error = Exception
    mock_pyodbc.connect.side_effect = Exception(
        "('IM002', '[IM002] [Microsoft][ODBC Driver Manager] Data source name not found and no default driver specified (0) (SQLDriverConnect)')"
    )

    assert source.check() is False
    assert source._last_error is not None
    assert (
        "Hint: ODBC Driver 18 for SQL Server is not installed."
        not in source._last_error
    )


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_check_success_clears_last_error(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """A successful check() resets _last_error left over from a prior failure."""
    mock_pyodbc.Error = Exception
    # First: fail
    mock_pyodbc.connect.side_effect = Exception("boom")
    assert source.check() is False
    assert source._last_error is not None

    # Then: succeed — _last_error should be cleared
    mock_pyodbc.connect.side_effect = None
    mock_pyodbc.connect.return_value = MagicMock()
    assert source.check() is True
    assert source._last_error is None


# --- registry + config integration ---


def test_registry_creates_sqlserver_source() -> None:
    """Registry resolves SqlServerSource when type is 'sqlserver'."""
    from feather_etl.sources.registry import get_source_class
    from feather_etl.sources.sqlserver import SqlServerSource as _Cls

    assert get_source_class("sqlserver") is _Cls


def test_sqlserver_connection_string_builder_trusts_self_signed_cert(tmp_path) -> None:
    """Regression for siraj-samsudeen/feather-etl#3.

    Driver 18 enforces strict TLS validation by default, so connecting to a
    SQL Server with a self-signed cert fails with "certificate verify failed".
    The default connection-string builder must include TrustServerCertificate=yes
    so that on-prem ERP servers work out of the box. Users who want strict TLS
    can override by providing a raw `connection_string:` in feather.yaml.
    """
    from feather_etl.config import load_config

    from tests.helpers import make_curation_entry, write_curation

    cfg = {
        "sources": [
            {
                "type": "sqlserver",
                "name": "erp",
                "host": "10.0.0.1",
                "port": 1433,
                "database": "mydb",
                "user": "sa",
                "password": "secret",
            }
        ],
        "destination": {"path": str(tmp_path / "dest.duckdb")},
    }
    config_file = write_config(tmp_path, cfg)
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "dbo.orders", "orders")],
    )

    result = load_config(config_file, validate=False)
    assert result.sources[0].connection_string is not None
    assert "TrustServerCertificate=yes" in result.sources[0].connection_string
    assert "ODBC Driver 18 for SQL Server" in result.sources[0].connection_string


def test_config_validation_sqlserver_requires_connection_string(tmp_path) -> None:
    """Config validation rejects sqlserver without connection_string."""
    from feather_etl.config import load_config

    cfg = {
        "sources": [{"type": "sqlserver"}],
        "destination": {"path": str(tmp_path / "dest.duckdb")},
        "tables": [
            {
                "name": "orders",
                "source_table": "dbo.orders",
                "strategy": "full",
                "target_table": "bronze.orders",
            }
        ],
    }
    config_file = write_config(tmp_path, cfg)

    with pytest.raises(ValueError, match="connection_string"):
        load_config(config_file)


# --- DatabaseSource._build_where_clause ---


def test_build_where_clause_empty(source: SqlServerSource) -> None:
    """No filter or watermark returns empty string."""
    assert source._build_where_clause() == ""


def test_build_where_clause_filter_only(source: SqlServerSource) -> None:
    """Filter alone produces WHERE with filter."""
    result = source._build_where_clause(filter="status = 'active'")
    assert result == " WHERE (status = 'active')"


def test_build_where_clause_watermark_only(source: SqlServerSource) -> None:
    """Watermark alone produces WHERE with watermark condition."""
    result = source._build_where_clause(
        watermark_column="updated_at", watermark_value="2026-01-01"
    )
    assert result == " WHERE updated_at > '2026-01-01'"


def test_build_where_clause_both(source: SqlServerSource) -> None:
    """Filter + watermark combines with AND."""
    result = source._build_where_clause(
        filter="status = 'active'",
        watermark_column="updated_at",
        watermark_value="2026-01-01",
    )
    assert result == " WHERE (status = 'active') AND updated_at > '2026-01-01'"


class TestSqlServerFromYaml:
    def test_minimal_db_entry_builds_connection_string(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        entry = {
            "name": "erp",
            "type": "sqlserver",
            "host": "db.example.com",
            "user": "u",
            "password": "p",
            "database": "SALES",
        }
        src = SqlServerSource.from_yaml(entry, Path("."))
        assert src.name == "erp"
        assert src.host == "db.example.com"
        assert src.port == 1433
        assert src.database == "SALES"
        assert src.databases is None
        assert "SERVER=db.example.com,1433" in src.connection_string
        assert "DATABASE=SALES" in src.connection_string

    def test_explicit_port(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        entry = {
            "name": "erp",
            "type": "sqlserver",
            "host": "h",
            "port": 1444,
            "user": "u",
            "password": "p",
            "database": "X",
        }
        src = SqlServerSource.from_yaml(entry, Path("."))
        assert src.port == 1444
        assert "SERVER=h,1444" in src.connection_string

    def test_databases_list(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        entry = {
            "name": "erp",
            "type": "sqlserver",
            "host": "h",
            "user": "u",
            "password": "p",
            "databases": ["A", "B"],
        }
        src = SqlServerSource.from_yaml(entry, Path("."))
        assert src.database is None
        assert src.databases == ["A", "B"]

    def test_database_xor_databases_both(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        entry = {
            "name": "erp",
            "type": "sqlserver",
            "host": "h",
            "user": "u",
            "password": "p",
            "database": "A",
            "databases": ["B"],
        }
        with pytest.raises(ValueError, match="mutually exclusive"):
            SqlServerSource.from_yaml(entry, Path("."))

    def test_databases_empty_list(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        entry = {
            "name": "erp",
            "type": "sqlserver",
            "host": "h",
            "user": "u",
            "password": "p",
            "databases": [],
        }
        with pytest.raises(ValueError, match="non-empty"):
            SqlServerSource.from_yaml(entry, Path("."))

    def test_explicit_connection_string_overrides(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        entry = {
            "name": "erp",
            "type": "sqlserver",
            "connection_string": "DRIVER={X};SERVER=raw",
        }
        src = SqlServerSource.from_yaml(entry, Path("."))
        assert src.connection_string == "DRIVER={X};SERVER=raw"


class TestSqlServerValidateSourceTable:
    def test_schema_dot_table_ok(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        src = SqlServerSource(connection_string="dummy", name="x")
        assert src.validate_source_table("dbo.MyTable") == []

    def test_plain_table_ok(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        src = SqlServerSource(connection_string="dummy", name="x")
        assert src.validate_source_table("MyTable") == []


class TestSqlServerListDatabases:
    def test_query_filters_system_dbs(self, monkeypatch):
        """Should run a query against sys.databases excluding master/tempdb/model/msdb."""
        from feather_etl.sources import sqlserver as ss

        captured_sql: list[str] = []

        class FakeCursor:
            def execute(self, sql, *_):
                captured_sql.append(sql)

            def fetchall(self):
                return [("SALES",), ("INVENTORY",), ("HR",)]

            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        monkeypatch.setattr(ss.pyodbc, "connect", lambda *a, **k: FakeConn())

        src = ss.SqlServerSource(connection_string="dummy", name="x")
        result = src.list_databases()

        assert result == ["SALES", "INVENTORY", "HR"]
        assert "sys.databases" in captured_sql[0]
        assert "NOT IN" in captured_sql[0]
        for sysdb in ("master", "tempdb", "model", "msdb"):
            assert f"'{sysdb}'" in captured_sql[0]

    def test_propagates_pyodbc_error(self, monkeypatch):
        from feather_etl.sources import sqlserver as ss

        def raise_(*a, **k):
            raise ss.pyodbc.Error("Login failed")

        monkeypatch.setattr(ss.pyodbc, "connect", raise_)
        src = ss.SqlServerSource(connection_string="dummy", name="x")
        with pytest.raises(ss.pyodbc.Error):
            src.list_databases()


# ---------------------------------------------------------------------------
# _is_missing_odbc_driver_18_error — branch coverage
# ---------------------------------------------------------------------------


class TestIsMissingOdbcDriver18Error:
    def test_file_not_found_with_explicit_driver18(self):
        """The ``file not found`` branch returns True when the error + conn
        string both mention Driver 18."""
        from feather_etl.sources.sqlserver import _is_missing_odbc_driver_18_error

        assert (
            _is_missing_odbc_driver_18_error(
                "Driver Manager: file not found ODBC Driver 18",
                "DRIVER={ODBC Driver 18};SERVER=x",
            )
            is True
        )

    def test_unrelated_error_returns_false(self):
        """A generic network error that doesn't mention any Driver-18 hints
        should NOT be mis-attributed to a missing driver."""
        from feather_etl.sources.sqlserver import _is_missing_odbc_driver_18_error

        assert (
            _is_missing_odbc_driver_18_error(
                "TCP connection timed out",
                "DRIVER={ODBC Driver 17};SERVER=x",
            )
            is False
        )


# ---------------------------------------------------------------------------
# check() hint — Linux branch (else clause after Darwin / Windows)
# ---------------------------------------------------------------------------


@patch("feather_etl.sources.sqlserver.platform.system", return_value="Linux")
@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_missing_driver18_on_linux_adds_linux_hint(
    mock_pyodbc: MagicMock, _mock_platform: MagicMock, source: SqlServerSource
) -> None:
    """On Linux, the install-hint points at the Microsoft Linux/Mac docs."""
    mock_pyodbc.Error = Exception
    mock_pyodbc.connect.side_effect = Exception(
        "[01000] [unixODBC][Driver Manager]Can't open lib 'ODBC Driver 18 for SQL Server' : file not found"
    )

    assert source.check() is False
    assert source._last_error is not None
    assert "Hint: ODBC Driver 18 for SQL Server is not installed." in source._last_error
    assert "linux-mac/installing-the-microsoft-odbc-driver" in source._last_error


# ---------------------------------------------------------------------------
# extract() — description None + type coercion (Decimal/UUID)
# ---------------------------------------------------------------------------


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_extract_description_none_returns_empty_table(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """If ``cursor.description`` is None after executing, extract closes
    both cursor and connection and returns an empty PyArrow table."""
    mock_cursor = MagicMock()
    mock_cursor.description = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.extract("dbo.void_proc")

    assert isinstance(result, pa.Table)
    assert result.num_rows == 0
    assert result.num_columns == 0
    mock_cursor.close.assert_called_once()
    mock_conn.close.assert_called_once()


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_extract_coerces_decimal_and_uuid(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Decimal values → float, UUID values → str; otherwise pyarrow can't
    build a typed column from mixed-Python-type cells."""
    import decimal
    import uuid

    test_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    mock_cursor = MagicMock()
    mock_cursor.description = [
        ("amount", float, None, None, None, None, None),
        ("external_id", str, None, None, None, None, None),
    ]
    mock_cursor.fetchmany.side_effect = [
        [(decimal.Decimal("19.95"), test_uuid)],
        [],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.extract("dbo.t")

    assert result.num_rows == 1
    assert result.column("amount").to_pylist() == [19.95]
    assert result.column("external_id").to_pylist() == [str(test_uuid)]


# ---------------------------------------------------------------------------
# detect_changes — error & defensive branches
# ---------------------------------------------------------------------------


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_detect_changes_checksum_error_on_pyodbc_error(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """When pyodbc raises while computing CHECKSUM_AGG, detect_changes
    reports ``checksum_error`` so the caller can decide to extract anyway."""
    mock_pyodbc.Error = Exception
    mock_pyodbc.connect.side_effect = Exception("login failed")

    result = source.detect_changes("dbo.t", last_state=None)
    assert result.changed is True
    assert result.reason == "checksum_error"


@patch("feather_etl.sources.sqlserver.pyodbc")
def test_sqlserver_detect_changes_first_run_when_row_is_none(
    mock_pyodbc: MagicMock, source: SqlServerSource
) -> None:
    """Defensive branch: if the CHECKSUM SELECT yields no row, fall back
    to treating the call as first_run."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn

    result = source.detect_changes("dbo.t", last_state=None)
    assert result.changed is True
    assert result.reason == "first_run"


# ---------------------------------------------------------------------------
# extract() — column quoting
# ---------------------------------------------------------------------------


class TestSqlServerColumnQuoting:
    @patch("feather_etl.sources.sqlserver.pyodbc")
    def test_columns_are_bracket_quoted(self, mock_pyodbc, source):
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("Invoice Date", str, None, None, None, None, None),
        ]
        mock_cursor.fetchmany.side_effect = [[("2026-01-01",)], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc.connect.return_value = mock_conn

        source.extract("dbo.Sales", columns=["Invoice Date", "Quantity"])

        executed = mock_cursor.execute.call_args_list
        query = executed[1][0][0]
        assert "[Invoice Date], [Quantity]" in query

    @patch("feather_etl.sources.sqlserver.pyodbc")
    def test_columns_with_bracket_rejected(self, mock_pyodbc, source):
        with pytest.raises(ValueError, match="cannot be bracket-quoted"):
            source.extract("dbo.Sales", columns=["col]name"])

    @patch("feather_etl.sources.sqlserver.pyodbc")
    def test_columns_none_uses_star(self, mock_pyodbc, source):
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("id", int, None, None, None, None, None),
        ]
        mock_cursor.fetchmany.side_effect = [[(1,)], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc.connect.return_value = mock_conn

        source.extract("dbo.Sales")
        executed = mock_cursor.execute.call_args_list
        query = executed[1][0][0]
        assert "SELECT *" in query
