"""Tests for CsvSource, including multi-file CSV glob support (V6 extension)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import FIXTURES_DIR


GLOB_FIXTURES = FIXTURES_DIR / "csv_glob"


class TestCsvSource:
    @pytest.fixture
    def csv_dir(self, tmp_path: Path) -> Path:
        """Copy CSV fixture directory to tmp_path."""
        import shutil

        src = FIXTURES_DIR / "csv_data"
        dst = tmp_path / "csv_data"
        shutil.copytree(src, dst)
        return dst

    def test_check_valid_directory(self, csv_dir: Path):
        from feather_flow.sources.csv import CsvSource

        source = CsvSource(path=csv_dir)
        assert source.check() is True

    def test_check_nonexistent_directory(self, tmp_path: Path):
        from feather_flow.sources.csv import CsvSource

        source = CsvSource(path=tmp_path / "nope")
        assert source.check() is False

    def test_check_file_not_directory(self, tmp_path: Path):
        from feather_flow.sources.csv import CsvSource

        file = tmp_path / "not_a_dir.csv"
        file.write_text("a,b\n1,2\n")
        source = CsvSource(path=file)
        assert source.check() is False

    def test_detect_changes_first_run(self, csv_dir: Path):
        from feather_flow.sources.csv import CsvSource

        source = CsvSource(path=csv_dir)
        result = source.detect_changes("orders.csv")
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_source_path_for_table_per_file(self, csv_dir: Path):
        """CSV source resolves per-file: path/orders.csv, not the directory."""
        from feather_flow.sources.csv import CsvSource

        source = CsvSource(path=csv_dir)
        assert source._source_path_for_table("orders.csv") == csv_dir / "orders.csv"


class TestCsvGlobDiscover:
    def test_discover_shows_glob_as_single_table(self, tmp_path: Path):
        """feather discover shows a glob pattern as one logical table."""
        from feather_flow.sources.csv import CsvSource

        data_dir = tmp_path / "data"
        shutil.copytree(GLOB_FIXTURES, data_dir)
        source = CsvSource(data_dir)
        schemas = source.discover()
        names = [s.name for s in schemas]
        assert "sales_jan.csv" in names
        assert "sales_feb.csv" in names


class TestCsvGlobExtractErrors:
    def test_extract_glob_with_no_matches_raises(self, tmp_path: Path):
        """Extract against a glob pattern that matches nothing raises
        FileNotFoundError, surfacing a clear message to the caller."""
        from feather_flow.sources.csv import CsvSource

        (tmp_path / "data").mkdir()
        source = CsvSource(path=tmp_path / "data")
        with pytest.raises(FileNotFoundError, match="No files matching glob"):
            source.extract("sales_*.csv")

    def test_get_schema_glob_with_no_matches_returns_empty_list(self, tmp_path: Path):
        """get_schema on an empty glob returns [] (not raises) — caller can
        decide what to do with an empty schema."""
        from feather_flow.sources.csv import CsvSource

        (tmp_path / "data").mkdir()
        source = CsvSource(path=tmp_path / "data")
        assert source.get_schema("sales_*.csv") == []


class TestCsvGlobStateRecovery:
    def test_detect_changes_treats_corrupt_stored_hash_as_first_run(
        self, tmp_path: Path
    ):
        """If ``last_file_hash`` in the watermark is not valid JSON (or is a
        non-string type), detect_changes treats the run as first-run instead
        of raising — the JSON/Type errors are swallowed and stored_files
        stays empty."""
        from feather_flow.sources.csv import CsvSource

        data_dir = tmp_path / "data"
        shutil.copytree(GLOB_FIXTURES, data_dir)
        source = CsvSource(data_dir)

        # last_file_hash is truthy but not parseable as JSON
        last_state = {"last_file_hash": "not-json-at-all"}
        result = source.detect_changes("sales_*.csv", last_state=last_state)

        assert result.changed is True
        assert result.reason == "first_run"
        # metadata should contain a valid JSON-serialised per-file hash map
        import json

        parsed = json.loads(str(result.metadata["file_hash"]))
        assert "sales_jan.csv" in parsed
