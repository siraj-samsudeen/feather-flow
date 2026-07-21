"""Unit tests for discover I/O helpers in feather_flow.config."""


class TestSanitize:
    def test_keeps_safe_chars(self):
        from feather_flow.config import _sanitize

        assert _sanitize("prod-erp.db_01") == "prod-erp.db_01"

    def test_replaces_unsafe_chars(self):
        from feather_flow.config import _sanitize

        assert _sanitize("prod/erp") == "prod_erp"
        assert _sanitize("192.168.2.62:1433") == "192.168.2.62_1433"
        assert _sanitize("a b c") == "a_b_c"

    def test_preserves_dots_and_hyphens(self):
        from feather_flow.config import _sanitize

        assert _sanitize("db.internal-prod") == "db.internal-prod"


class TestResolvedSourceName:
    def test_user_name_wins_over_auto(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(
            connection_string="x", name="prod-erp", host="db.internal"
        )
        assert resolved_source_name(src) == "prod-erp"

    def test_user_name_is_sanitized(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(
            connection_string="x", name="prod/erp", host="db.internal"
        )
        assert resolved_source_name(src) == "prod_erp"

    def test_sqlserver_auto_uses_type_and_host(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(connection_string="x", host="192.168.2.62")
        assert resolved_source_name(src) == "sqlserver-192.168.2.62"

    def test_sqlserver_auto_sanitizes_host(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(connection_string="x", host="192.168.2.62:1433")
        assert resolved_source_name(src) == "sqlserver-192.168.2.62_1433"

    def test_postgres_auto_uses_type_and_host(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.postgres import PostgresSource

        src = PostgresSource(connection_string="x", host="db.internal")
        assert resolved_source_name(src) == "postgres-db.internal"

    def test_csv_auto_uses_directory_basename(self, tmp_path):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.csv import CsvSource

        csv_dir = tmp_path / "csv_data"
        csv_dir.mkdir()
        src = CsvSource(path=csv_dir)
        assert resolved_source_name(src) == "csv-csv_data"

    def test_sqlite_auto_uses_file_basename_without_ext(self, tmp_path):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.sqlite import SqliteSource

        sqlite_file = tmp_path / "source.sqlite"
        sqlite_file.touch()
        src = SqliteSource(path=sqlite_file)
        assert resolved_source_name(src) == "sqlite-source"

    def test_duckdb_auto_uses_file_basename_without_ext(self, tmp_path):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        duck_file = tmp_path / "my_data.duckdb"
        duck_file.touch()
        src = DuckDBFileSource(path=duck_file)
        assert resolved_source_name(src) == "duckdb-my_data"

    def test_excel_auto_uses_file_basename_without_ext(self, tmp_path):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.excel import ExcelSource

        xl = tmp_path / "sheet.xlsx"
        xl.touch()
        src = ExcelSource(path=xl)
        assert resolved_source_name(src) == "excel-sheet"

    def test_json_auto_uses_file_basename_without_ext(self, tmp_path):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.json_source import JsonSource

        js = tmp_path / "events.json"
        js.touch()
        src = JsonSource(path=js)
        assert resolved_source_name(src) == "json-events"

    def test_db_source_without_host_falls_back_to_unknown(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(connection_string="x", host=None)
        assert resolved_source_name(src) == "sqlserver-unknown"

    def test_file_source_without_path_falls_back_to_unknown(self):
        from feather_flow.config import resolved_source_name
        from feather_flow.sources.csv import CsvSource

        src = CsvSource(path=None)
        assert resolved_source_name(src) == "csv-unknown"


class TestSchemaOutputPath:
    def test_explicit_file_source_gets_type_prefix(self, tmp_path):
        from pathlib import Path

        from feather_flow.config import schema_output_path
        from feather_flow.sources.sqlite import SqliteSource

        sqlite_file = tmp_path / "source.sqlite"
        sqlite_file.touch()
        src = SqliteSource(path=sqlite_file, name="prod-erp")
        src._explicit_name = True
        assert schema_output_path(src) == Path("schema_sqlite_prod-erp.json")

    def test_explicit_db_source_gets_type_prefix(self):
        from pathlib import Path

        from feather_flow.config import schema_output_path
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(
            connection_string="x", name="prod-erp", host="db.internal", database="ZAKYA"
        )
        src._explicit_name = True
        assert schema_output_path(src) == Path("schema_sqlserver_prod-erp.json")

    def test_auto_derived_names_remain_unchanged(self):
        from pathlib import Path

        from feather_flow.config import schema_output_path
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(
            connection_string="x", host="192.168.2.62", database="ZAKYA"
        )
        assert schema_output_path(src) == Path("schema_sqlserver-192.168.2.62.json")

    def test_expanded_child_name_with_explicit_true_gets_prefixed(self):
        from pathlib import Path

        from feather_flow.config import schema_output_path
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(
            connection_string="x", name="erp__db1", host="db.internal"
        )
        src._explicit_name = True
        assert schema_output_path(src) == Path("schema_sqlserver_erp__db1.json")

    def test_expanded_child_name_with_explicit_false_has_no_extra_prefix(self):
        from pathlib import Path

        from feather_flow.config import schema_output_path
        from feather_flow.sources.sqlserver import SqlServerSource

        src = SqlServerSource(
            connection_string="x", name="erp__db1", host="db.internal"
        )
        src._explicit_name = False
        assert schema_output_path(src) == Path("schema_erp__db1.json")
