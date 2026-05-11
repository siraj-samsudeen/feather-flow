"""Tests for MySQL source.

Real-database tests are marked with a skip decorator and are skipped when
the local MySQL instance is not reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.db_bootstrap import MYSQL_CONN_KWARGS, mysql_marker


# ---------------------------------------------------------------------------
# MySQLSource.from_yaml — unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestMySQLFromYaml:
    def test_minimal_entry_builds_connect_kwargs(self):
        from feather_etl.sources.mysql import MySQLSource

        entry = {
            "name": "wh",
            "type": "mysql",
            "host": "db.example.com",
            "user": "u",
            "password": "p",
            "database": "warehouse",
        }
        src = MySQLSource.from_yaml(entry, Path("."))
        assert src.name == "wh"
        assert src.host == "db.example.com"
        assert src.port == 3306
        assert src.database == "warehouse"
        assert src._connect_kwargs["host"] == "db.example.com"
        assert src._connect_kwargs["port"] == 3306
        assert src._connect_kwargs["database"] == "warehouse"
        assert src._connect_kwargs["user"] == "u"
        assert src._connect_kwargs["password"] == "p"

    def test_explicit_port(self):
        from feather_etl.sources.mysql import MySQLSource

        entry = {
            "name": "wh",
            "type": "mysql",
            "host": "h",
            "port": 3307,
            "user": "u",
            "password": "p",
            "database": "X",
        }
        src = MySQLSource.from_yaml(entry, Path("."))
        assert src.port == 3307
        assert src._connect_kwargs["port"] == 3307

    def test_explicit_connection_string(self):
        from feather_etl.sources.mysql import MySQLSource

        entry = {
            "name": "wh",
            "type": "mysql",
            "connection_string": "host=raw;database=verbatim",
        }
        src = MySQLSource.from_yaml(entry, Path("."))
        assert src.connection_string == "host=raw;database=verbatim"

    def test_databases_list_and_xor_rules(self):
        from feather_etl.sources.mysql import MySQLSource

        ok = {
            "name": "wh",
            "type": "mysql",
            "host": "h",
            "user": "u",
            "password": "p",
            "databases": ["A", "B"],
        }
        src = MySQLSource.from_yaml(ok, Path("."))
        assert src.databases == ["A", "B"]

        with pytest.raises(ValueError, match="mutually exclusive"):
            MySQLSource.from_yaml({**ok, "database": "C"}, Path("."))

        with pytest.raises(ValueError, match="non-empty"):
            MySQLSource.from_yaml({**ok, "databases": []}, Path("."))

    def test_missing_host_and_connection_string_raises(self):
        from feather_etl.sources.mysql import MySQLSource

        with pytest.raises(ValueError, match="requires either"):
            MySQLSource.from_yaml({"name": "x", "type": "mysql"}, Path("."))


# ---------------------------------------------------------------------------
# MySQLSource — _connect_kwargs charset/use_unicode (issue #52)
# ---------------------------------------------------------------------------


class TestMySQLConnectKwargs:
    """Without ``charset=utf8mb4`` and ``use_unicode=True``, mysql-connector
    returns VARCHAR/TEXT column values as ``bytes`` rather than ``str``,
    which forces pyarrow to materialise the column as ``binary()`` even
    when the schema declares ``string()`` — landing every text column in
    destination DuckDB as BLOB. See issue #52."""

    def test_host_form_sets_charset_and_use_unicode(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource.from_yaml(
            {
                "name": "wh",
                "type": "mysql",
                "host": "db.example.com",
                "user": "u",
                "password": "p",
                "database": "warehouse",
            },
            Path("."),
        )
        assert src._connect_kwargs["charset"] == "utf8mb4"
        assert src._connect_kwargs["use_unicode"] is True

    def test_databases_list_form_sets_charset_and_use_unicode(self):
        """The multi-DB form must also carry charset settings — when
        ``resolve_source`` binds a child per ``source_db``, the child is
        built via the same ``from_yaml`` host branch."""
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource.from_yaml(
            {
                "name": "Rama",
                "type": "mysql",
                "host": "h",
                "user": "u",
                "password": "p",
                "databases": ["A", "B"],
            },
            Path("."),
        )
        assert src._connect_kwargs["charset"] == "utf8mb4"
        assert src._connect_kwargs["use_unicode"] is True

    def test_connection_string_form_leaves_charset_to_user(self):
        """``connection_string`` is user-owned verbatim. We don't inject
        charset there — operators using an explicit connection string are
        responsible for including ``charset=utf8mb4;use_unicode=True`` in
        the string themselves."""
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource.from_yaml(
            {
                "name": "wh",
                "type": "mysql",
                "connection_string": "host=raw;database=verbatim",
            },
            Path("."),
        )
        assert src._connect_kwargs == {}


# ---------------------------------------------------------------------------
# MySQLSource — _mysql_field_type_to_arrow flag-aware mapping (issue #52)
# ---------------------------------------------------------------------------


class TestMySQLFieldTypeToArrow:
    """mysql-connector overloads type code 252 for both ``BLOB`` and
    ``TEXT``; the family more broadly straddles binary/text variants for
    codes 249/250/251. The ``FieldFlag.BINARY`` bit on
    ``cursor.description[i][7]`` is the only discriminator."""

    def test_blob_with_binary_flag_maps_to_binary(self):
        import pyarrow as pa
        from mysql.connector.constants import FieldFlag

        from feather_etl.sources.mysql import _mysql_field_type_to_arrow

        # type code 252 = BLOB/TEXT, BINARY flag set → real binary
        assert _mysql_field_type_to_arrow(252, FieldFlag.BINARY) == pa.binary()

    def test_blob_without_binary_flag_maps_to_string(self):
        """The TEXT case: same type code 252, but no BINARY flag."""
        import pyarrow as pa

        from feather_etl.sources.mysql import _mysql_field_type_to_arrow

        assert _mysql_field_type_to_arrow(252, 0) == pa.string()

    def test_tiny_blob_family_respects_binary_flag(self):
        import pyarrow as pa
        from mysql.connector.constants import FieldFlag

        from feather_etl.sources.mysql import _mysql_field_type_to_arrow

        # 249 = TINY_BLOB / TINYTEXT
        assert _mysql_field_type_to_arrow(249, FieldFlag.BINARY) == pa.binary()
        assert _mysql_field_type_to_arrow(249, 0) == pa.string()
        # 250 = MEDIUM_BLOB / MEDIUMTEXT
        assert _mysql_field_type_to_arrow(250, FieldFlag.BINARY) == pa.binary()
        assert _mysql_field_type_to_arrow(250, 0) == pa.string()
        # 251 = LONG_BLOB / LONGTEXT
        assert _mysql_field_type_to_arrow(251, FieldFlag.BINARY) == pa.binary()
        assert _mysql_field_type_to_arrow(251, 0) == pa.string()

    def test_var_string_maps_to_string(self):
        """Code 253 = VAR_STRING (the actual VARCHAR wire type). Always string."""
        import pyarrow as pa

        from feather_etl.sources.mysql import _mysql_field_type_to_arrow

        assert _mysql_field_type_to_arrow(253, 0) == pa.string()

    def test_string_maps_to_string(self):
        """Code 254 = STRING (CHAR). Always string."""
        import pyarrow as pa

        from feather_etl.sources.mysql import _mysql_field_type_to_arrow

        assert _mysql_field_type_to_arrow(254, 0) == pa.string()

    def test_numeric_codes_ignore_flags(self):
        """The flag bit is only meaningful for the BLOB family; numeric
        types must keep their declared mapping regardless of flags."""
        import pyarrow as pa
        from mysql.connector.constants import FieldFlag

        from feather_etl.sources.mysql import _mysql_field_type_to_arrow

        # code 3 = LONG → int64 regardless of flags
        assert _mysql_field_type_to_arrow(3, 0) == pa.int64()
        assert _mysql_field_type_to_arrow(3, FieldFlag.BINARY) == pa.int64()


# ---------------------------------------------------------------------------
# MySQLSource — unit tests (no DB needed)
# ---------------------------------------------------------------------------


class TestMySQLSourceUnit:
    def test_source_type_is_mysql(self):
        from feather_etl.sources.mysql import MySQLSource

        assert MySQLSource.type == "mysql"

    def test_watermark_passthrough(self):
        """MySQLSource uses the default _format_watermark (ISO unchanged)."""
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy")
        assert src._format_watermark("2026-01-01T10:00:00") == "2026-01-01T10:00:00"

    def test_build_where_filter_only(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy")
        result = src._build_where_clause(filter="active = 1")
        assert result == " WHERE (active = 1)"

    def test_build_where_watermark_only(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy")
        result = src._build_where_clause(
            watermark_column="modified_at", watermark_value="2026-01-01"
        )
        assert result == " WHERE modified_at > '2026-01-01'"

    def test_build_where_both(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy")
        result = src._build_where_clause(
            filter="active = 1",
            watermark_column="modified_at",
            watermark_value="2026-01-01",
        )
        assert result == " WHERE (active = 1) AND modified_at > '2026-01-01'"


class TestMySQLValidateSourceTable:
    def test_plain_table_ok(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy", name="x")
        assert src.validate_source_table("orders") == []

    def test_qualified_table_ok(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy", name="x")
        assert src.validate_source_table("mydb.orders") == []


# ---------------------------------------------------------------------------
# MySQLSource.check() — unit tests (mocked connector)
# ---------------------------------------------------------------------------


mysql_db = mysql_marker()


class TestMySQLCheckLastError:
    def test_check_failure_populates_last_error(self, monkeypatch):
        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

        def boom(**kwargs):
            raise mysql_mod.mysql.connector.Error("Access denied for user 'root'")

        monkeypatch.setattr(mysql_mod.mysql.connector, "connect", boom)

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "nope"}
        assert src.check() is False
        assert src._last_error is not None
        assert "Access denied" in src._last_error

    def test_check_success_clears_last_error(self, monkeypatch):
        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "localhost"}
        src._last_error = "stale error from a prior call"

        class FakeConn:
            def close(self):
                pass

        monkeypatch.setattr(
            mysql_mod.mysql.connector, "connect", lambda **k: FakeConn()
        )
        assert src.check() is True
        assert src._last_error is None


# ---------------------------------------------------------------------------
# MySQLSource.discover() — integration tests (real MySQL)
# ---------------------------------------------------------------------------


@mysql_db
class TestMySQLDiscoverIntegration:
    @pytest.fixture
    def source(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="")
        src._connect_kwargs = MYSQL_CONN_KWARGS
        src.database = "feather_test"
        return src

    def test_discover_returns_erp_tables(self, source):
        schemas = source.discover()
        names = {s.name for s in schemas}
        assert "erp_customers" in names
        assert "erp_products" in names
        assert "erp_sales" in names

    def test_discover_schema_fields(self, source):
        schemas = source.discover()
        sales = next(s for s in schemas if s.name == "erp_sales")
        assert sales.supports_incremental is True
        assert sales.primary_key is None
        col_names = [c[0] for c in sales.columns]
        assert "id" in col_names
        assert "amount" in col_names

    def test_discover_column_types(self, source):
        schemas = source.discover()
        sales = next(s for s in schemas if s.name == "erp_sales")
        col_types = {c[0]: c[1] for c in sales.columns}
        assert col_types["id"] == "int"
        assert col_types["product"] == "varchar"
        assert col_types["amount"] == "decimal"


# ---------------------------------------------------------------------------
# MySQLSource.get_schema() — integration tests (real MySQL)
# ---------------------------------------------------------------------------


@mysql_db
class TestMySQLGetSchemaIntegration:
    @pytest.fixture
    def source(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="")
        src._connect_kwargs = MYSQL_CONN_KWARGS
        src.database = "feather_test"
        return src

    def test_get_schema_returns_columns(self, source):
        cols = source.get_schema("erp_sales")
        col_names = [c[0] for c in cols]
        assert "id" in col_names
        assert "customer_id" in col_names
        assert "product" in col_names
        assert "amount" in col_names
        assert "modified_at" in col_names

    def test_get_schema_returns_types(self, source):
        cols = source.get_schema("erp_sales")
        col_types = {c[0]: c[1] for c in cols}
        assert col_types["id"] == "int"
        assert col_types["amount"] == "decimal"
        assert col_types["modified_at"] == "timestamp"


# ---------------------------------------------------------------------------
# MySQLSource.extract() — integration tests (real MySQL)
# ---------------------------------------------------------------------------


@mysql_db
class TestMySQLExtractIntegration:
    @pytest.fixture
    def source(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="")
        src._connect_kwargs = MYSQL_CONN_KWARGS
        src.database = "feather_test"
        return src

    def test_extract_full(self, source):
        import pyarrow as pa

        table = source.extract("erp_sales")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 10
        assert "id" in table.column_names
        assert "amount" in table.column_names

    def test_extract_with_columns(self, source):
        table = source.extract("erp_customers", columns=["id", "name"])
        assert table.column_names == ["id", "name"]
        assert table.num_rows == 4

    def test_extract_with_filter(self, source):
        table = source.extract("erp_sales", filter="amount > 150")
        assert table.num_rows > 0
        amounts = table.column("amount").to_pylist()
        assert all(a > 150 for a in amounts)

    def test_extract_incremental_with_watermark(self, source):
        import pyarrow as pa

        table = source.extract(
            "erp_sales",
            watermark_column="modified_at",
            watermark_value="2026-01-03",
        )
        assert isinstance(table, pa.Table)
        assert table.num_rows > 0

    def test_extract_empty_result(self, source):
        import pyarrow as pa

        table = source.extract("erp_sales", filter="id = -999")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 0


# ---------------------------------------------------------------------------
# MySQLSource.detect_changes() — integration tests (real MySQL)
# ---------------------------------------------------------------------------


@mysql_db
class TestMySQLDetectChangesIntegration:
    @pytest.fixture
    def source(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="")
        src._connect_kwargs = MYSQL_CONN_KWARGS
        src.database = "feather_test"
        return src

    def test_detect_changes_first_run(self, source):
        result = source.detect_changes("erp_sales", last_state=None)
        assert result.changed is True
        assert result.reason == "first_run"
        assert "checksum" in result.metadata
        assert "row_count" in result.metadata

    def test_detect_changes_unchanged(self, source):
        first = source.detect_changes("erp_sales", last_state=None)
        last_state = {
            "last_checksum": first.metadata.get("checksum"),
            "last_row_count": first.metadata.get("row_count"),
        }
        second = source.detect_changes("erp_sales", last_state=last_state)
        assert second.changed is False
        assert second.reason == "unchanged"

    def test_detect_changes_incremental_always_changed(self, source):
        last_state = {"strategy": "incremental"}
        result = source.detect_changes("erp_sales", last_state=last_state)
        assert result.changed is True
        assert result.reason == "incremental"


# ---------------------------------------------------------------------------
# MySQLSource.list_databases — unit tests (mocked connector)
# ---------------------------------------------------------------------------


class TestMySQLListDatabases:
    def test_query_filters_system_dbs(self, monkeypatch):
        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

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

        monkeypatch.setattr(
            mysql_mod.mysql.connector, "connect", lambda **k: FakeConn()
        )

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "localhost"}
        result = src.list_databases()

        assert result == ["warehouse", "analytics"]
        sql = captured_sql[0]
        assert "SCHEMA_NAME" in sql
        assert "information_schema" in sql
        assert "mysql" in sql
        assert "performance_schema" in sql
        assert "sys" in sql

    def test_propagates_connector_error(self, monkeypatch):
        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

        def raise_(**k):
            raise mysql_mod.mysql.connector.Error("connection refused")

        monkeypatch.setattr(mysql_mod.mysql.connector, "connect", raise_)
        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "nope"}
        with pytest.raises(mysql_mod.mysql.connector.Error):
            src.list_databases()


# ---------------------------------------------------------------------------
# Registry — unit test
# ---------------------------------------------------------------------------


class TestMySQLRegistry:
    def test_source_in_registry(self):
        from feather_etl.sources.mysql import MySQLSource
        from feather_etl.sources.registry import get_source_class

        assert get_source_class("mysql") is MySQLSource


# ---------------------------------------------------------------------------
# MySQLSource.discover() — guard when database is None
# ---------------------------------------------------------------------------


class TestMySQLDiscoverGuard:
    def test_discover_without_database_raises(self):
        from feather_etl.sources.mysql import MySQLSource

        src = MySQLSource(connection_string="dummy")
        with pytest.raises(ValueError, match="requires database"):
            src.discover()


# ---------------------------------------------------------------------------
# MySQLSource._connect fallback — connection_string branch (mocked)
# ---------------------------------------------------------------------------


class TestMySQLConnectFallback:
    def test_connect_uses_connection_string_when_kwargs_empty(self, monkeypatch):
        """When ``_connect_kwargs`` is empty, _connect falls back to passing
        ``connection_string=...`` to ``mysql.connector.connect``."""
        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

        captured: dict = {}

        class FakeConn:
            def close(self):
                pass

        def fake_connect(**kwargs):
            captured.update(kwargs)
            return FakeConn()

        monkeypatch.setattr(mysql_mod.mysql.connector, "connect", fake_connect)

        src = MySQLSource(connection_string="host=x;user=y;password=z;db=w")
        # Deliberately leave _connect_kwargs empty → falls through the if
        assert src._connect_kwargs == {}
        conn = src._connect()
        assert conn is not None
        assert captured == {"connection_string": "host=x;user=y;password=z;db=w"}


# ---------------------------------------------------------------------------
# MySQLSource.extract — edge cases (mocked)
# ---------------------------------------------------------------------------


class TestMySQLExtractEdgeCases:
    def test_extract_returns_empty_when_description_is_none(self, monkeypatch):
        """If ``cursor.description`` is None (e.g. a statement that returns
        no result set), extract closes the cursor cleanly and returns an
        empty arrow table."""
        import pyarrow as pa

        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

        cursor_closed: list[bool] = []

        class FakeCursor:
            description = None

            def execute(self, *_):
                pass

            def close(self):
                cursor_closed.append(True)

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def close(self):
                pass

        monkeypatch.setattr(
            mysql_mod.mysql.connector, "connect", lambda **k: FakeConn()
        )

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "localhost"}
        out = src.extract("somewhere")

        assert isinstance(out, pa.Table)
        assert out.num_rows == 0
        assert out.num_columns == 0
        assert cursor_closed == [True]


# ---------------------------------------------------------------------------
# MySQLSource.detect_changes — error & edge paths (mocked)
# ---------------------------------------------------------------------------


class TestMySQLDetectChangesMocked:
    def _install_conn(self, monkeypatch, cursor_factory):
        from feather_etl.sources import mysql as mysql_mod

        class FakeConn:
            def cursor(self):
                return cursor_factory()

            def close(self):
                pass

        monkeypatch.setattr(
            mysql_mod.mysql.connector, "connect", lambda **k: FakeConn()
        )

    def test_checksum_error_when_connector_raises(self, monkeypatch):
        """A mysql.connector.Error while running CHECKSUM TABLE is swallowed
        and surfaces as ``ChangeResult(changed=True, reason='checksum_error')``."""
        from feather_etl.sources import mysql as mysql_mod
        from feather_etl.sources.mysql import MySQLSource

        def raise_(**k):
            raise mysql_mod.mysql.connector.Error("server gone")

        monkeypatch.setattr(mysql_mod.mysql.connector, "connect", raise_)

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "nope"}
        src.database = "test"
        result = src.detect_changes("t")

        assert result.changed is True
        assert result.reason == "checksum_error"

    def test_first_run_when_checksum_row_is_none(self, monkeypatch):
        """If CHECKSUM TABLE yields no row (fetchone() is None), detect_changes
        treats it as first_run to be safe."""
        from feather_etl.sources.mysql import MySQLSource

        class FakeCursor:
            _calls = 0

            def execute(self, *_):
                pass

            def fetchone(self):
                type(self)._calls += 1
                # First call = CHECKSUM returns None; second = COUNT
                return None if type(self)._calls == 1 else (0,)

            def close(self):
                pass

        self._install_conn(monkeypatch, FakeCursor)

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "localhost"}
        src.database = "test"
        result = src.detect_changes("t")

        assert result.changed is True
        assert result.reason == "first_run"

    def test_checksum_changed_metadata_populated(self, monkeypatch):
        """When last_checksum differs, detect_changes reports
        ``checksum_changed`` with the fresh checksum + row count."""
        from feather_etl.sources.mysql import MySQLSource

        class FakeCursor:
            _calls = 0

            def execute(self, *_):
                pass

            def fetchone(self):
                type(self)._calls += 1
                # CHECKSUM row is (table_name, checksum); COUNT is (N,)
                if type(self)._calls == 1:
                    return ("test.t", 424242)
                return (100,)

            def close(self):
                pass

        self._install_conn(monkeypatch, FakeCursor)

        src = MySQLSource(connection_string="dummy")
        src._connect_kwargs = {"host": "localhost"}
        src.database = "test"

        last_state = {"last_checksum": 111111, "last_row_count": 50}
        result = src.detect_changes("t", last_state=last_state)

        assert result.changed is True
        assert result.reason == "checksum_changed"
        assert result.metadata == {"checksum": 424242, "row_count": 100}
