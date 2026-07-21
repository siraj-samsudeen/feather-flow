"""Tests for Excel source using real fixtures at tests/fixtures/excel_data/."""

from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa
import pytest

from tests.conftest import FIXTURES_DIR

EXCEL_DIR = FIXTURES_DIR / "excel_data"


@pytest.fixture
def excel_dir(tmp_path: Path) -> Path:
    """Copy Excel fixture directory to tmp_path."""
    dst = tmp_path / "excel_data"
    shutil.copytree(EXCEL_DIR, dst)
    return dst


class TestExcelSourceRegistry:
    def test_source_in_registry(self):
        from feather_flow.sources.excel import ExcelSource
        from feather_flow.sources.registry import get_source_class

        assert get_source_class("excel") is ExcelSource

    def test_excel_type_attr(self):
        from feather_flow.sources.excel import ExcelSource

        assert ExcelSource.type == "excel"


class TestExcelSourceCheck:
    def test_check_existing_dir(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        assert src.check() is True

    def test_check_nonexistent(self, tmp_path: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=tmp_path / "nope")
        assert src.check() is False

    def test_check_file_not_dir(self, tmp_path: Path):
        from feather_flow.sources.excel import ExcelSource

        f = tmp_path / "file.xlsx"
        f.write_bytes(b"x")
        src = ExcelSource(path=f)
        assert src.check() is False


class TestExcelSourceDiscover:
    def test_discover_finds_xlsx_files(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        schemas = src.discover()
        names = {s.name for s in schemas}
        assert "orders.xlsx" in names
        assert "customers.xlsx" in names
        assert "products.xlsx" in names

    def test_discover_schema_has_columns(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        schemas = src.discover()
        orders = next(s for s in schemas if s.name == "orders.xlsx")
        col_names = [c[0] for c in orders.columns]
        assert "order_id" in col_names

    def test_discover_supports_incremental_false(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        schemas = src.discover()
        for s in schemas:
            assert s.supports_incremental is False


class TestExcelSourceGetSchema:
    def test_get_schema_orders(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        cols = src.get_schema("orders.xlsx")
        col_names = [c[0] for c in cols]
        assert "order_id" in col_names
        assert "customer_id" in col_names

    def test_get_schema_returns_list_of_tuples(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        cols = src.get_schema("customers.xlsx")
        assert isinstance(cols, list)
        assert all(isinstance(c, tuple) and len(c) == 2 for c in cols)


class TestExcelSourceExtract:
    def test_extract_orders_full(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        table = src.extract("orders.xlsx")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 5

    def test_extract_customers_row_count(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        table = src.extract("customers.xlsx")
        assert table.num_rows == 4

    def test_extract_products_row_count(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        table = src.extract("products.xlsx")
        assert table.num_rows == 3

    def test_extract_with_columns(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        table = src.extract("orders.xlsx", columns=["order_id", "customer_id"])
        assert set(table.column_names) == {"order_id", "customer_id"}
        assert table.num_rows == 5

    def test_extract_returns_pyarrow_table(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        result = src.extract("products.xlsx")
        assert isinstance(result, pa.Table)
        assert len(result.column_names) > 0


class TestExcelSourceFromYaml:
    def test_from_yaml_rejects_non_directory(self, tmp_path: Path):
        """excel source path must be a directory; passing a file raises."""
        from feather_flow.sources.excel import ExcelSource

        f = tmp_path / "file.xlsx"
        f.write_bytes(b"x")
        with pytest.raises(ValueError, match="must be a directory"):
            ExcelSource.from_yaml({"type": "excel", "path": str(f)}, tmp_path)


class TestExcelValidateSourceTable:
    def test_validate_source_table_always_ok(self, tmp_path: Path):
        """Excel uses filenames, not SQL identifiers — so validate_source_table
        returns an empty list for any input."""
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=tmp_path)
        assert src.validate_source_table("literally anything (with) parens.xlsx") == []


class TestExcelSourceDetectChanges:
    def test_detect_changes_first_run(self, excel_dir: Path):
        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        result = src.detect_changes("orders.xlsx")
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_detect_changes_unchanged(self, excel_dir: Path):
        import os

        from feather_flow.sources.excel import ExcelSource

        src = ExcelSource(path=excel_dir)
        first = src.detect_changes("orders.xlsx")
        file_path = excel_dir / "orders.xlsx"
        last_state = {
            "last_file_mtime": os.path.getmtime(file_path),
            "last_file_hash": first.metadata["file_hash"],
        }
        second = src.detect_changes("orders.xlsx", last_state=last_state)
        assert second.changed is False
        assert second.reason == "unchanged"
