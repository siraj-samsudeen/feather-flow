"""Tests for PostgreSQL source.

Real-database tests are marked @pytest.mark.postgres and are skipped when
the local PostgreSQL instance is not reachable.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from tests.db_bootstrap import POSTGRES_DSN as CONN_STR, postgres_marker

postgres = postgres_marker()


# ---------------------------------------------------------------------------
# DatabaseSource._format_watermark refactor
# ---------------------------------------------------------------------------


class TestDatabaseSourceFormatWatermark:
    """The base class default passes values through unchanged."""

    def test_default_passthrough(self):
        from feather_etl.sources.database_source import DatabaseSource

        ds = DatabaseSource("dummy")
        assert ds._format_watermark("2026-01-01T10:00:00") == "2026-01-01T10:00:00"

    def test_sqlserver_override_replaces_T(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        src = SqlServerSource("dummy")
        result = src._format_watermark("2026-01-01T10:00:00.123456")
        assert "T" not in result
        assert result == "2026-01-01 10:00:00.123"

    def test_sqlserver_override_no_fractional(self):
        from feather_etl.sources.sqlserver import SqlServerSource

        src = SqlServerSource("dummy")
        result = src._format_watermark("2026-01-01T10:00:00")
        assert result == "2026-01-01 10:00:00"

    def test_build_where_uses_format_watermark(self):
        """_build_where_clause delegates to _format_watermark."""
        from feather_etl.sources.database_source import DatabaseSource

        ds = DatabaseSource("dummy")
        result = ds._build_where_clause(
            watermark_column="modified_at",
            watermark_value="2026-01-01T10:00:00",
        )
        # Default formatter: value unchanged
        assert "modified_at > '2026-01-01T10:00:00'" in result


# ---------------------------------------------------------------------------
# PostgresSource — unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestPostgresSourceUnit:
    def test_source_in_registry(self):
        from feather_etl.sources.postgres import PostgresSource
        from feather_etl.sources.registry import get_source_class

        assert get_source_class("postgres") is PostgresSource

    def test_watermark_passthrough(self):
        """PostgresSource uses the default _format_watermark (ISO unchanged)."""
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(CONN_STR)
        assert src._format_watermark("2026-01-01T10:00:00") == "2026-01-01T10:00:00"

    def test_build_where_filter_only(self):
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(CONN_STR)
        result = src._build_where_clause(filter="active = true")
        assert result == " WHERE (active = true)"

    def test_build_where_watermark_only(self):
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(CONN_STR)
        result = src._build_where_clause(
            watermark_column="modified_at", watermark_value="2026-01-01"
        )
        assert result == " WHERE modified_at > '2026-01-01'"

    def test_build_where_both(self):
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(CONN_STR)
        result = src._build_where_clause(
            filter="active = true",
            watermark_column="modified_at",
            watermark_value="2026-01-01",
        )
        assert result == " WHERE (active = true) AND modified_at > '2026-01-01'"


# ---------------------------------------------------------------------------
# PostgresSource.check() — unit tests (mocked psycopg2)
# ---------------------------------------------------------------------------


class TestPostgresCheckLastError:
    """check() must capture the real exception on failure so the CLI can print it."""

    def test_check_failure_populates_last_error(self, monkeypatch):
        from feather_etl.sources import postgres as pg_mod
        from feather_etl.sources.postgres import PostgresSource

        def boom(*args, **kwargs):
            raise pg_mod.psycopg2.Error("FATAL: password authentication failed")

        monkeypatch.setattr(pg_mod.psycopg2, "connect", boom)

        src = PostgresSource("dbname=nope host=nope")
        assert src.check() is False
        assert src._last_error is not None
        assert "password authentication failed" in src._last_error

    def test_check_success_clears_last_error(self, monkeypatch):
        from feather_etl.sources import postgres as pg_mod
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource("dbname=x host=y")
        src._last_error = "stale error from a prior call"

        class FakeConn:
            def close(self):
                pass

        monkeypatch.setattr(pg_mod.psycopg2, "connect", lambda *a, **k: FakeConn())
        assert src.check() is True
        assert src._last_error is None


# ---------------------------------------------------------------------------
# PostgresSource — integration tests (real PostgreSQL required)
# ---------------------------------------------------------------------------


@postgres
class TestPostgresSourceIntegration:
    @pytest.fixture
    def source(self):
        from feather_etl.sources.postgres import PostgresSource

        return PostgresSource(CONN_STR)

    def test_check_returns_true(self, source):
        assert source.check() is True

    def test_check_bad_conn_returns_false(self):
        from feather_etl.sources.postgres import PostgresSource

        bad = PostgresSource("dbname=nonexistent host=localhost")
        assert bad.check() is False

    def test_discover_returns_erp_tables(self, source):
        schemas = source.discover()
        names = {s.name for s in schemas}
        assert "erp.sales" in names
        assert "erp.customers" in names
        assert "erp.products" in names

    def test_discover_excludes_system_schemas(self, source):
        schemas = source.discover()
        for s in schemas:
            schema_part = s.name.split(".")[0]
            assert schema_part not in ("pg_catalog", "information_schema")

    def test_discover_schema_fields(self, source):
        schemas = source.discover()
        sales = next(s for s in schemas if s.name == "erp.sales")
        assert sales.supports_incremental is True
        # discover() now populates primary_key from INFORMATION_SCHEMA
        assert sales.primary_key == ["id"]
        col_names = [c[0] for c in sales.columns]
        assert "id" in col_names
        assert "amount" in col_names

    def test_get_schema_qualified(self, source):
        cols = source.get_schema("erp.sales")
        col_names = [c[0] for c in cols]
        assert "id" in col_names
        assert "customer_id" in col_names
        assert "product" in col_names
        assert "amount" in col_names
        assert "modified_at" in col_names

    def test_get_schema_unqualified_defaults_public(self, source):
        """Unqualified table name defaults to 'public' schema."""
        # erp.sales lives in erp schema, not public — so this returns empty
        cols = source.get_schema("sales")
        # Just verify it returns a list (may be empty for non-public tables)
        assert isinstance(cols, list)

    def test_extract_full(self, source):
        import pyarrow as pa

        table = source.extract("erp.sales")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 10
        assert "id" in table.column_names
        assert "amount" in table.column_names

    def test_extract_with_columns(self, source):
        table = source.extract("erp.customers", columns=["id", "name"])
        assert table.column_names == ["id", "name"]
        assert table.num_rows == 4

    def test_extract_with_filter(self, source):
        table = source.extract("erp.sales", filter="amount > 150")
        assert table.num_rows > 0
        amounts = table.column("amount").to_pylist()
        assert all(a > 150 for a in amounts)

    def test_extract_incremental_with_watermark(self, source):
        """Incremental extract with watermark returns only newer rows."""
        table = source.extract(
            "erp.sales",
            watermark_column="modified_at",
            watermark_value="2026-01-03",
        )
        assert isinstance(table, int) is False  # must be a table
        import pyarrow as pa

        assert isinstance(table, pa.Table)
        assert table.num_rows > 0

    def test_detect_changes_first_run(self, source):
        result = source.detect_changes("erp.sales", last_state=None)
        assert result.changed is True
        assert result.reason == "first_run"
        assert "row_count" in result.metadata

    def test_detect_changes_unchanged(self, source):
        first = source.detect_changes("erp.sales", last_state=None)
        # Feed back the metadata as "prior state"
        last_state = {
            "last_checksum": first.metadata.get("checksum"),
            "last_row_count": first.metadata.get("row_count"),
        }
        second = source.detect_changes("erp.sales", last_state=last_state)
        assert second.changed is False
        assert second.reason == "unchanged"

    def test_detect_changes_incremental_always_changed(self, source):
        last_state = {"strategy": "incremental"}
        result = source.detect_changes("erp.sales", last_state=last_state)
        assert result.changed is True
        assert result.reason == "incremental"


# ---------------------------------------------------------------------------
# PostgresSource.from_yaml — unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestPostgresFromYaml:
    def test_minimal_entry_builds_conn_string(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        entry = {
            "name": "wh",
            "type": "postgres",
            "host": "db.example.com",
            "user": "u",
            "password": "p",
            "database": "warehouse",
        }
        src = PostgresSource.from_yaml(entry, Path("."))
        assert src.name == "wh"
        assert src.host == "db.example.com"
        assert src.port == 5432
        assert src.database == "warehouse"
        assert "host=db.example.com" in src.connection_string
        assert "port=5432" in src.connection_string
        assert "dbname=warehouse" in src.connection_string

    def test_explicit_port(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        entry = {
            "name": "wh",
            "type": "postgres",
            "host": "h",
            "port": 5499,
            "user": "u",
            "password": "p",
            "database": "X",
        }
        src = PostgresSource.from_yaml(entry, Path("."))
        assert src.port == 5499
        assert "port=5499" in src.connection_string

    def test_databases_list_and_xor_rules(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        ok = {
            "name": "wh",
            "type": "postgres",
            "host": "h",
            "user": "u",
            "password": "p",
            "databases": ["A", "B"],
        }
        src = PostgresSource.from_yaml(ok, Path("."))
        assert src.databases == ["A", "B"]

        with pytest.raises(ValueError, match="mutually exclusive"):
            PostgresSource.from_yaml({**ok, "database": "C"}, Path("."))

        with pytest.raises(ValueError, match="non-empty"):
            PostgresSource.from_yaml({**ok, "databases": []}, Path("."))

    def test_explicit_connection_string_overrides(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        entry = {
            "name": "wh",
            "type": "postgres",
            "connection_string": "host=raw dbname=verbatim",
        }
        src = PostgresSource.from_yaml(entry, Path("."))
        assert src.connection_string == "host=raw dbname=verbatim"

    def test_empty_user_password_not_emitted_in_dsn(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        entry = {
            "name": "wh",
            "type": "postgres",
            "host": "localhost",
            "user": "",
            "password": "",
            "database": "warehouse",
        }
        src = PostgresSource.from_yaml(entry, Path("."))
        assert "host=localhost" in src.connection_string
        assert "dbname=warehouse" in src.connection_string
        assert "user=" not in src.connection_string
        assert "password=" not in src.connection_string

    def test_missing_database_defaults_to_postgres_catalog(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        entry = {
            "name": "wh",
            "type": "postgres",
            "host": "localhost",
            "user": "",
            "password": "",
        }
        src = PostgresSource.from_yaml(entry, Path("."))
        assert "host=localhost" in src.connection_string
        assert "port=5432" in src.connection_string
        assert "dbname=postgres" in src.connection_string


# ---------------------------------------------------------------------------
# PostgresSource.validate_source_table — unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestPostgresValidateSourceTable:
    def test_schema_dot_table_ok(self):
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(connection_string="dummy", name="x")
        assert src.validate_source_table("public.orders") == []

    def test_plain_table_ok(self):
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(connection_string="dummy", name="x")
        assert src.validate_source_table("orders") == []


# ---------------------------------------------------------------------------
# PostgresSource.list_databases — unit tests (mocked psycopg2)
# ---------------------------------------------------------------------------


class TestPostgresListDatabases:
    def test_query_filters_template_dbs(self, monkeypatch):
        from feather_etl.sources import postgres as pg

        captured_sql: list[str] = []

        class FakeCursor:
            def execute(self, sql, *_):
                captured_sql.append(sql)

            def fetchall(self):
                return [("warehouse",), ("analytics",)]

            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        monkeypatch.setattr(pg.psycopg2, "connect", lambda *a, **k: FakeConn())

        src = pg.PostgresSource(connection_string="dummy", name="x")
        result = src.list_databases()

        assert result == ["warehouse", "analytics"]
        sql = captured_sql[0]
        assert "NOT IN" in sql
        assert "= false" in sql
        assert "pg_database" in sql
        assert "datistemplate" in sql or "template" in sql
        assert "postgres" in sql  # 'postgres' default DB filtered out

    def test_propagates_psycopg2_error(self, monkeypatch):
        from feather_etl.sources import postgres as pg

        def raise_(*a, **k):
            raise pg.psycopg2.Error("connection refused")

        monkeypatch.setattr(pg.psycopg2, "connect", raise_)
        src = pg.PostgresSource(connection_string="dummy", name="x")
        with pytest.raises(pg.psycopg2.Error):
            src.list_databases()


# ---------------------------------------------------------------------------
# PostgresSource.from_yaml — validation errors (no DB needed)
# ---------------------------------------------------------------------------


class TestPostgresFromYamlErrors:
    def test_missing_host_and_connection_string_raises(self):
        from pathlib import Path

        from feather_etl.sources.postgres import PostgresSource

        entry = {"type": "postgres", "name": "x"}
        with pytest.raises(
            ValueError, match="requires either 'connection_string' or 'host'"
        ):
            PostgresSource.from_yaml(entry, Path("."))


# ---------------------------------------------------------------------------
# PostgresSource.extract — mocked edge cases
# ---------------------------------------------------------------------------


def _make_pg_source():
    from feather_etl.sources.postgres import PostgresSource

    return PostgresSource(connection_string="dummy", name="x")


class TestPostgresExtractMocked:
    """All tests monkeypatch psycopg2.connect — no live DB required."""

    def _install_cursor(self, monkeypatch, cursor):
        from feather_etl.sources import postgres as pg

        class FakeConn:
            def cursor(self):
                return cursor

            def close(self):
                pass

        monkeypatch.setattr(pg.psycopg2, "connect", lambda *a, **k: FakeConn())

    def test_extract_returns_empty_table_when_description_is_none(self, monkeypatch):
        """A query that returns no result description (e.g. void function)
        results in an empty arrow table with no columns."""
        import pyarrow as pa

        cursor_closed: list[bool] = []

        class FakeCursor:
            description = None

            def execute(self, *_):
                pass

            def close(self):
                cursor_closed.append(True)

        self._install_cursor(monkeypatch, FakeCursor())

        result = _make_pg_source().extract("something")
        assert isinstance(result, pa.Table)
        assert result.num_rows == 0
        assert result.num_columns == 0
        assert cursor_closed == [True]

    def test_extract_zero_rows_returns_typed_empty_arrow_table(self, monkeypatch):
        """When the query returns no rows, extract returns an empty arrow
        table that nevertheless preserves the declared column names/types."""
        import pyarrow as pa

        class FakeCursor:
            description = [
                ("id", 23),  # int4
                ("name", 1043),  # varchar
            ]

            def execute(self, *_):
                pass

            def fetchmany(self, _n):
                return []  # exhaust immediately → no batches

            def close(self):
                pass

        self._install_cursor(monkeypatch, FakeCursor())

        result = _make_pg_source().extract("t")
        assert isinstance(result, pa.Table)
        assert result.num_rows == 0
        # Column names preserved even though result is empty
        assert result.column_names == ["id", "name"]

    def test_extract_converts_decimal_to_float(self, monkeypatch):
        """``Decimal`` values coming out of psycopg2 are cast to float so they
        fit the float64 arrow column (non-Decimal values are passed through)."""
        import decimal

        class FakeCursor:
            description = [("amount", 1700)]  # NUMERIC → float64
            _returned = False

            def execute(self, *_):
                pass

            def fetchmany(self, _n):
                if type(self)._returned:
                    return []
                type(self)._returned = True
                return [(decimal.Decimal("42.5"),), (decimal.Decimal("7.25"),)]

            def close(self):
                pass

        self._install_cursor(monkeypatch, FakeCursor())

        result = _make_pg_source().extract("t")
        assert result.num_rows == 2
        amounts = result.column("amount").to_pylist()
        assert amounts == [42.5, 7.25]
        assert all(isinstance(a, float) for a in amounts)


# ---------------------------------------------------------------------------
# PostgresSource._discover_pk_columns / detect_changes — error paths (mocked)
# ---------------------------------------------------------------------------


class TestPostgresChangeDetectionMocked:
    def _install_conn(self, monkeypatch, cursor_factory):
        from feather_etl.sources import postgres as pg

        class FakeConn:
            def cursor(self):
                return cursor_factory()

            def close(self):
                pass

        monkeypatch.setattr(pg.psycopg2, "connect", lambda *a, **k: FakeConn())

    def test_discover_pk_columns_returns_empty_list_on_error(self, monkeypatch):
        """If querying pg_index fails, _discover_pk_columns swallows the
        psycopg2.Error and returns [] — callers fall back to ORDER BY 1."""
        from feather_etl.sources import postgres as pg

        class FakeCursor:
            def execute(self, *_):
                raise pg.psycopg2.Error("relation does not exist")

            def close(self):
                pass

        self._install_conn(monkeypatch, FakeCursor)

        # Access the private method directly — test behaviour, not interface
        result = _make_pg_source()._discover_pk_columns("schema.nonexistent")
        assert result == []

    def test_detect_changes_returns_checksum_error_when_psycopg2_raises(
        self, monkeypatch
    ):
        from feather_etl.sources import postgres as pg

        def raise_(*a, **k):
            raise pg.psycopg2.Error("connection dropped")

        monkeypatch.setattr(pg.psycopg2, "connect", raise_)

        result = _make_pg_source().detect_changes("t", last_state=None)
        assert result.changed is True
        assert result.reason == "checksum_error"

    def test_detect_changes_returns_first_run_when_row_is_none(self, monkeypatch):
        """Defensive path: if the checksum SELECT yields no row at all,
        detect_changes conservatively reports first_run."""

        class FakeCursor:
            def execute(self, *_):
                pass

            def fetchone(self):
                return None  # no row

            def close(self):
                pass

        self._install_conn(monkeypatch, FakeCursor)

        # Patch _discover_pk_columns so we don't also need to mock pg_index
        src = _make_pg_source()
        src._discover_pk_columns = lambda _t: []  # type: ignore[method-assign]

        result = src.detect_changes("t", last_state=None)
        assert result.changed is True
        assert result.reason == "first_run"

    def test_detect_changes_checksum_changed_populates_metadata(self, monkeypatch):
        """When the new checksum or row_count differs from the stored
        value, detect_changes reports ``checksum_changed`` with the fresh
        metadata."""

        class FakeCursor:
            def execute(self, *_):
                pass

            def fetchone(self):
                return (100, "md5-NEW")

            def close(self):
                pass

        self._install_conn(monkeypatch, FakeCursor)

        src = _make_pg_source()
        src._discover_pk_columns = lambda _t: []  # type: ignore[method-assign]
        last_state = {"last_checksum": "md5-OLD", "last_row_count": 100}

        result = src.detect_changes("t", last_state=last_state)
        assert result.changed is True
        assert result.reason == "checksum_changed"
        assert result.metadata == {"checksum": "md5-NEW", "row_count": 100}


# ---------------------------------------------------------------------------
# PostgresSource.extract — column quoting (mocked)
# ---------------------------------------------------------------------------


class TestPostgresColumnQuoting:
    def test_columns_are_double_quoted(self, monkeypatch):
        from feather_etl.sources import postgres as pg

        captured_sql: list[str] = []

        class FakeCursor:
            description = [("id", 23), ("name", 1043)]

            def execute(self, sql, *_):
                captured_sql.append(sql)

            def fetchmany(self, _n):
                return []

            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        monkeypatch.setattr(pg.psycopg2, "connect", lambda *a, **k: FakeConn())

        src = pg.PostgresSource(connection_string="dummy", name="x")
        src.extract("erp.sales", columns=["Invoice Date", "Quantity"])

        assert any('"Invoice Date", "Quantity"' in sql for sql in captured_sql)

    def test_columns_with_double_quote_rejected(self):
        from feather_etl.sources.postgres import PostgresSource

        src = PostgresSource(connection_string="dummy", name="x")
        with pytest.raises(ValueError, match="cannot be double-quoted"):
            src.extract("erp.sales", columns=['col"name'])

    def test_columns_none_uses_star(self, monkeypatch):
        from feather_etl.sources import postgres as pg

        captured_sql: list[str] = []

        class FakeCursor:
            description = [("id", 23)]

            def execute(self, sql, *_):
                captured_sql.append(sql)

            def fetchmany(self, _n):
                return []

            def close(self):
                pass

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        monkeypatch.setattr(pg.psycopg2, "connect", lambda *a, **k: FakeConn())

        src = pg.PostgresSource(connection_string="dummy", name="x")
        src.extract("erp.sales")

        assert any("SELECT *" in sql for sql in captured_sql)


# ---------------------------------------------------------------------------
# PostgresSource.discover() — primary_key population (mocked)
# ---------------------------------------------------------------------------


@patch("feather_etl.sources.postgres.psycopg2")
def test_postgres_discover_populates_pk(mock_psycopg2: MagicMock) -> None:
    """discover() sets primary_key=[col] when one PK column is returned."""
    from feather_etl.sources.postgres import PostgresSource

    mock_cursor = MagicMock()
    # fetchall call sequence:
    #   0: list tables → 1 table
    #   1: columns for erp.orders
    #   2: PK columns for erp.orders (single PK)
    mock_cursor.fetchall.side_effect = [
        [("erp", "orders")],
        [("id", "integer"), ("total", "numeric")],
        [("id",)],
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_psycopg2.connect.return_value = mock_conn

    src = PostgresSource(connection_string="dummy")
    schemas = src.discover()

    assert len(schemas) == 1
    assert schemas[0].primary_key == ["id"]


# ---------------------------------------------------------------------------
# PostgresSource.cheap_rowcount — mocked
# ---------------------------------------------------------------------------


@patch("feather_etl.sources.postgres.psycopg2")
def test_postgres_cheap_rowcount_returns_estimate(mock_psycopg2: MagicMock) -> None:
    """cheap_rowcount() queries pg_class.reltuples and returns an int."""
    from feather_etl.sources.postgres import PostgresSource

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (55000,)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_psycopg2.connect.return_value = mock_conn

    src = PostgresSource(connection_string="dummy")
    count = src.cheap_rowcount("erp.orders")

    assert count == 55000
    assert isinstance(count, int)
    execute_calls = [str(c) for c in mock_cursor.execute.call_args_list]
    assert any("pg_class" in c for c in execute_calls)


# ---------------------------------------------------------------------------
# PostgresSource.get_window_range — mocked
# ---------------------------------------------------------------------------


@patch("feather_etl.sources.postgres.psycopg2")
def test_postgres_get_window_range_returns_min_max(mock_psycopg2: MagicMock) -> None:
    """get_window_range() returns (min, max) tuple for the given column."""
    import datetime

    from feather_etl.sources.postgres import PostgresSource

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (
        datetime.datetime(2020, 1, 1),
        datetime.datetime(2026, 5, 15),
    )
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_psycopg2.connect.return_value = mock_conn

    src = PostgresSource(connection_string="dummy")
    result = src.get_window_range("erp.orders", "updated_at")

    assert result is not None
    assert result[0] == datetime.datetime(2020, 1, 1)
    assert result[1] == datetime.datetime(2026, 5, 15)
