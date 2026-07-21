"""Tests for SqliteSource."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FIXTURES_DIR


class TestSqliteSource:
    @pytest.fixture
    def sqlite_db(self, tmp_path: Path) -> Path:
        """Copy SQLite fixture to tmp_path."""
        import shutil

        src = FIXTURES_DIR / "sample_erp.sqlite"
        dst = tmp_path / "sample_erp.sqlite"
        shutil.copy2(src, dst)
        return dst

    def test_check_valid_file(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        assert source.check() is True

    def test_check_nonexistent_file(self, tmp_path: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=tmp_path / "nope.sqlite")
        assert source.check() is False

    def test_check_corrupt_file(self, tmp_path: Path):
        from feather_flow.sources.sqlite import SqliteSource

        bad = tmp_path / "bad.sqlite"
        bad.write_bytes(b"not a sqlite file")
        source = SqliteSource(path=bad)
        assert source.check() is False

    def test_discover_lists_tables(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        schemas = source.discover()
        names = {s.name for s in schemas}
        assert names == {"orders", "customers", "products"}

    def test_discover_has_columns(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        schemas = source.discover()
        orders = next(s for s in schemas if s.name == "orders")
        col_names = [c[0] for c in orders.columns]
        assert "order_id" in col_names
        assert "status" in col_names

    def test_discover_supports_incremental(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        schemas = source.discover()
        assert all(s.supports_incremental for s in schemas)

    def test_extract_returns_arrow(self, sqlite_db: Path):
        import pyarrow as pa

        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        table = source.extract("orders")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 5

    def test_extract_customers_row_count(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        table = source.extract("customers")
        assert table.num_rows == 4

    def test_extract_null_preserved(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        table = source.extract("products")
        assert table.num_rows == 3
        stock_qty = table.column("stock_qty")
        assert stock_qty[2].as_py() is None

    def test_get_schema(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        schema = source.get_schema("orders")
        col_names = [c[0] for c in schema]
        assert "order_id" in col_names
        assert len(col_names) > 0

    def test_detect_changes_first_run(self, sqlite_db: Path):
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        result = source.detect_changes("orders")
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_source_path_for_table_returns_file_path(self, sqlite_db: Path):
        """SQLite source uses the single .sqlite file for all tables."""
        from feather_flow.sources.sqlite import SqliteSource

        source = SqliteSource(path=sqlite_db)
        assert source._source_path_for_table("orders") == sqlite_db
