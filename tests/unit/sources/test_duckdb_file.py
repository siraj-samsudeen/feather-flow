"""Tests for DuckDBFileSource."""

from __future__ import annotations

from pathlib import Path


class TestDuckDBFileSource:
    def test_check_valid_file(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        assert source.check() is True

    def test_check_nonexistent_file(self, tmp_path: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=tmp_path / "nope.duckdb")
        assert source.check() is False

    def test_discover_lists_tables(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        schemas = source.discover()
        names = {s.name for s in schemas}
        assert "icube.SALESINVOICE" in names
        assert "icube.CUSTOMERMASTER" in names
        assert "icube.InventoryGroup" in names
        assert len(schemas) == 6

    def test_discover_has_columns(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        schemas = source.discover()
        si = next(s for s in schemas if s.name == "icube.SALESINVOICE")
        col_names = [c[0] for c in si.columns]
        assert "ID" in col_names
        assert "SI_NO" in col_names

    def test_extract_returns_arrow(self, client_db: Path):
        import pyarrow as pa

        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        table = source.extract("icube.InventoryGroup")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 66

    def test_extract_salesinvoice_row_count(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        table = source.extract("icube.SALESINVOICE")
        assert table.num_rows == 11676

    def test_get_schema(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        schema = source.get_schema("icube.InventoryGroup")
        col_names = [c[0] for c in schema]
        assert len(col_names) > 0

    def test_detect_changes_first_run(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        result = source.detect_changes("icube.SALESINVOICE")
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_source_path_for_table_returns_file_path(self, client_db: Path):
        """DuckDB source uses the single .duckdb file for all tables."""
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        source = DuckDBFileSource(path=client_db)
        assert source._source_path_for_table("icube.SALESINVOICE") == client_db

    def test_check_corrupt_file_returns_false(self, tmp_path: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        bad_file = tmp_path / "corrupt.duckdb"
        bad_file.write_bytes(b"this is not a valid duckdb file")
        source = DuckDBFileSource(path=bad_file)
        assert source.check() is False
