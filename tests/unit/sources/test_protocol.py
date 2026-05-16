"""Tests for the Source protocol / contract."""

from __future__ import annotations

import pytest


class _MinimalSource:
    """Concrete class that satisfies the Source Protocol without overriding
    cheap_rowcount or get_window_range, so we can test the Protocol's own defaults."""

    name = "test"
    type = "test"

    @classmethod
    def from_yaml(cls, entry, config_dir):
        return cls()

    def validate_source_table(self, source_table):
        return []

    def check(self):
        return True

    def discover(self):
        return []

    def extract(
        self,
        table,
        columns=None,
        filter=None,
        watermark_column=None,
        watermark_value=None,
    ):
        import pyarrow as pa

        return pa.table({})

    def detect_changes(self, table, last_state=None):
        from feather_etl.sources import ChangeResult

        return ChangeResult(changed=False, reason="unchanged")

    def get_schema(self, table):
        return []


class TestSourceProtocol:
    def test_base_source_cheap_rowcount_raises_notimplemented(self):
        from feather_etl.sources import Source

        src = _MinimalSource()
        with pytest.raises(NotImplementedError):
            Source.cheap_rowcount(src, "some_table")

    def test_base_source_get_window_range_raises_notimplemented(self):
        from feather_etl.sources import Source

        src = _MinimalSource()
        with pytest.raises(NotImplementedError):
            Source.get_window_range(src, "some_table", "created_at")

    def test_protocol_has_from_yaml_and_validate_source_table(self):
        from feather_etl.sources import Source

        assert hasattr(Source, "from_yaml")
        assert hasattr(Source, "validate_source_table")

    def test_each_source_class_declares_type_attr(self):
        from feather_etl.sources.csv import CsvSource
        from feather_etl.sources.duckdb_file import DuckDBFileSource
        from feather_etl.sources.excel import ExcelSource
        from feather_etl.sources.json_source import JsonSource
        from feather_etl.sources.postgres import PostgresSource
        from feather_etl.sources.sqlite import SqliteSource
        from feather_etl.sources.sqlserver import SqlServerSource

        assert CsvSource.type == "csv"
        assert DuckDBFileSource.type == "duckdb"
        assert ExcelSource.type == "excel"
        assert JsonSource.type == "json"
        assert PostgresSource.type == "postgres"
        assert SqliteSource.type == "sqlite"
        assert SqlServerSource.type == "sqlserver"
