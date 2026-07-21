"""Tests for JSON source using real fixtures at tests/fixtures/json_data/."""

from __future__ import annotations

import shutil
from pathlib import Path

import pyarrow as pa
import pytest

from tests.conftest import FIXTURES_DIR

JSON_DIR = FIXTURES_DIR / "json_data"


@pytest.fixture
def json_dir(tmp_path: Path) -> Path:
    """Copy JSON fixture directory to tmp_path."""
    dst = tmp_path / "json_data"
    shutil.copytree(JSON_DIR, dst)
    return dst


class TestJsonSourceRegistry:
    def test_source_in_registry(self):
        from feather_flow.sources.json_source import JsonSource
        from feather_flow.sources.registry import get_source_class

        assert get_source_class("json") is JsonSource

    def test_json_type_attr(self):
        from feather_flow.sources.json_source import JsonSource

        assert JsonSource.type == "json"


class TestJsonSourceCheck:
    def test_check_existing_dir(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        assert src.check() is True

    def test_check_nonexistent(self, tmp_path: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=tmp_path / "nope")
        assert src.check() is False

    def test_check_file_not_dir(self, tmp_path: Path):
        from feather_flow.sources.json_source import JsonSource

        f = tmp_path / "file.json"
        f.write_text("[{}]")
        src = JsonSource(path=f)
        assert src.check() is False


class TestJsonSourceDiscover:
    def test_discover_finds_json_files(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        schemas = src.discover()
        names = {s.name for s in schemas}
        assert "orders.json" in names
        assert "customers.json" in names
        assert "products.json" in names

    def test_discover_schema_has_columns(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        schemas = src.discover()
        orders = next(s for s in schemas if s.name == "orders.json")
        col_names = [c[0] for c in orders.columns]
        assert "order_id" in col_names
        assert "product" in col_names

    def test_discover_supports_incremental_false(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        schemas = src.discover()
        for s in schemas:
            assert s.supports_incremental is False


class TestJsonSourceGetSchema:
    def test_get_schema_orders(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        cols = src.get_schema("orders.json")
        col_names = [c[0] for c in cols]
        assert "order_id" in col_names
        assert "customer_id" in col_names
        assert "product" in col_names

    def test_get_schema_returns_list_of_tuples(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        cols = src.get_schema("customers.json")
        assert isinstance(cols, list)
        assert all(isinstance(c, tuple) and len(c) == 2 for c in cols)


class TestJsonSourceExtract:
    def test_extract_orders_full(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        table = src.extract("orders.json")
        assert isinstance(table, pa.Table)
        assert table.num_rows == 5

    def test_extract_customers_row_count(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        table = src.extract("customers.json")
        assert table.num_rows == 4

    def test_extract_products_row_count(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        table = src.extract("products.json")
        assert table.num_rows == 3

    def test_extract_with_columns(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        table = src.extract("orders.json", columns=["order_id", "product"])
        assert set(table.column_names) == {"order_id", "product"}
        assert table.num_rows == 5

    def test_extract_with_filter(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        table = src.extract("orders.json", filter="quantity > 1")
        assert table.num_rows > 0
        quantities = table.column("quantity").to_pylist()
        assert all(q > 1 for q in quantities)

    def test_extract_returns_pyarrow_table(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        result = src.extract("products.json")
        assert isinstance(result, pa.Table)


class TestJsonSourceDetectChanges:
    def test_detect_changes_first_run(self, json_dir: Path):
        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        result = src.detect_changes("orders.json")
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_detect_changes_unchanged(self, json_dir: Path):
        import os

        from feather_flow.sources.json_source import JsonSource

        src = JsonSource(path=json_dir)
        first = src.detect_changes("orders.json")
        file_path = json_dir / "orders.json"
        last_state = {
            "last_file_mtime": os.path.getmtime(file_path),
            "last_file_hash": first.metadata["file_hash"],
        }
        second = src.detect_changes("orders.json", last_state=last_state)
        assert second.changed is False
        assert second.reason == "unchanged"


class TestJsonSourceFromYaml:
    def test_from_yaml_rejects_non_directory(self, tmp_path: Path):
        """JSON source path must be a directory; a file raises."""
        from feather_flow.sources.json_source import JsonSource

        f = tmp_path / "file.json"
        f.write_text("[]")
        with pytest.raises(ValueError, match="must be a directory"):
            JsonSource.from_yaml({"type": "json", "path": str(f)}, tmp_path)
