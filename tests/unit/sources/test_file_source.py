"""Tests for the FileSource base and file-source YAML / validation behaviour.

Merged from the original tests/test_sources.py:
- TestFileSource
- TestFileSourceFromYaml
- TestFileSourceValidateSourceTable
- TestFileSourcesRejectDbFields
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestFileSource:
    def test_check_existing_path(self, tmp_path: Path):
        from feather_flow.sources.file_source import FileSource

        existing = tmp_path / "somefile"
        existing.write_text("data")
        source = FileSource(path=existing)
        assert source.check() is True

    def test_check_nonexistent_path(self, tmp_path: Path):
        from feather_flow.sources.file_source import FileSource

        source = FileSource(path=tmp_path / "nope")
        assert source.check() is False

    def test_detect_changes_first_run(self, tmp_path: Path):
        from feather_flow.sources.file_source import FileSource

        existing = tmp_path / "somefile"
        existing.write_text("data")
        source = FileSource(path=existing)
        result = source.detect_changes("any_table")
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_detect_changes_unchanged(self, tmp_path: Path):
        """File untouched between runs → changed=False, empty metadata."""
        import os

        from feather_flow.sources.file_source import FileSource

        f = tmp_path / "somefile"
        f.write_text("data")
        source = FileSource(path=f)

        # Simulate a prior successful run by building last_state
        mtime = os.path.getmtime(f)
        first = source.detect_changes("any_table")
        last_state = {
            "last_file_mtime": mtime,
            "last_file_hash": first.metadata["file_hash"],
        }

        result = source.detect_changes("any_table", last_state=last_state)
        assert result.changed is False
        assert result.reason == "unchanged"
        assert result.metadata == {}

    def test_detect_changes_touch_scenario(self, tmp_path: Path):
        """Mtime changes but content identical → changed=False, metadata populated."""
        import os
        import time

        from feather_flow.sources.file_source import FileSource

        f = tmp_path / "somefile"
        f.write_text("data")
        source = FileSource(path=f)

        first = source.detect_changes("any_table")
        last_state = {
            "last_file_mtime": first.metadata["file_mtime"],
            "last_file_hash": first.metadata["file_hash"],
        }

        # Touch: update mtime without changing content
        time.sleep(0.05)
        os.utime(f, None)

        result = source.detect_changes("any_table", last_state=last_state)
        assert result.changed is False
        assert result.reason == "unchanged"
        # Metadata populated so pipeline can update watermark mtime
        assert "file_mtime" in result.metadata
        assert result.metadata["file_mtime"] != last_state["last_file_mtime"]
        assert result.metadata["file_hash"] == last_state["last_file_hash"]

    def test_detect_changes_content_changed(self, tmp_path: Path):
        """File content changes → changed=True, reason=hash_changed."""
        from feather_flow.sources.file_source import FileSource

        f = tmp_path / "somefile"
        f.write_text("original")
        source = FileSource(path=f)

        first = source.detect_changes("any_table")
        last_state = {
            "last_file_mtime": first.metadata["file_mtime"],
            "last_file_hash": first.metadata["file_hash"],
        }

        f.write_text("modified")

        result = source.detect_changes("any_table", last_state=last_state)
        assert result.changed is True
        assert result.reason == "hash_changed"
        assert "file_mtime" in result.metadata
        assert result.metadata["file_hash"] != last_state["last_file_hash"]

    def test_detect_changes_partial_state_null_mtime(self, tmp_path: Path):
        """Watermark exists but mtime is NULL (Slice 1 legacy) → treat as first run."""
        from feather_flow.sources.file_source import FileSource

        f = tmp_path / "somefile"
        f.write_text("data")
        source = FileSource(path=f)

        last_state = {"last_file_mtime": None, "last_file_hash": None}
        result = source.detect_changes("any_table", last_state=last_state)
        assert result.changed is True
        assert result.reason == "first_run"
        assert "file_mtime" in result.metadata
        assert "file_hash" in result.metadata

    def test_source_path_for_table_returns_self_path(self, tmp_path: Path):
        """Base FileSource returns self.path (whole file)."""
        from feather_flow.sources.file_source import FileSource

        f = tmp_path / "db.duckdb"
        f.write_text("data")
        source = FileSource(path=f)
        assert source._source_path_for_table("any_table") == f

    def test_path_stored(self, tmp_path: Path):
        from feather_flow.sources.file_source import FileSource

        p = tmp_path / "test"
        source = FileSource(path=p)
        assert source.path == p

    def test_duckdb_file_source_extends_file_source(self, client_db: Path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource
        from feather_flow.sources.file_source import FileSource

        source = DuckDBFileSource(path=client_db)
        assert isinstance(source, FileSource)


class TestFileSourceFromYaml:
    @pytest.fixture
    def csv_dir(self, tmp_path):
        d = tmp_path / "data"
        d.mkdir()
        (d / "orders.csv").write_text("id,amt\n1,10\n")
        return d

    @pytest.fixture
    def sqlite_file(self, tmp_path):
        f = tmp_path / "src.sqlite"
        f.write_bytes(b"")
        return f

    def test_csv_from_yaml(self, csv_dir):
        from feather_flow.sources.csv import CsvSource

        entry = {"name": "sheets", "type": "csv", "path": str(csv_dir)}
        src = CsvSource.from_yaml(entry, csv_dir.parent)
        assert src.name == "sheets"
        assert src.path == csv_dir

    def test_csv_relative_path_resolves_against_config_dir(self, tmp_path):
        from feather_flow.sources.csv import CsvSource

        d = tmp_path / "rel" / "csvs"
        d.mkdir(parents=True)
        entry = {"name": "x", "type": "csv", "path": "rel/csvs"}
        src = CsvSource.from_yaml(entry, tmp_path)
        assert src.path == d.resolve()

    def test_csv_path_required(self, tmp_path):
        from feather_flow.sources.csv import CsvSource

        with pytest.raises(ValueError, match="path"):
            CsvSource.from_yaml({"name": "x", "type": "csv"}, tmp_path)

    def test_csv_rejects_database_field(self, csv_dir):
        from feather_flow.sources.csv import CsvSource

        entry = {"name": "x", "type": "csv", "path": str(csv_dir), "database": "BAD"}
        with pytest.raises(ValueError, match="not supported for source type csv"):
            CsvSource.from_yaml(entry, csv_dir.parent)

    def test_sqlite_from_yaml(self, sqlite_file):
        from feather_flow.sources.sqlite import SqliteSource

        entry = {"name": "db", "type": "sqlite", "path": str(sqlite_file)}
        src = SqliteSource.from_yaml(entry, sqlite_file.parent)
        assert src.path == sqlite_file

    def test_duckdb_from_yaml(self, tmp_path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        f = tmp_path / "src.duckdb"
        f.write_bytes(b"")
        entry = {"name": "d", "type": "duckdb", "path": str(f)}
        src = DuckDBFileSource.from_yaml(entry, tmp_path)
        assert src.path == f

    def test_excel_from_yaml(self, tmp_path):
        from feather_flow.sources.excel import ExcelSource

        d = tmp_path / "xlsx"
        d.mkdir()
        src = ExcelSource.from_yaml(
            {"name": "e", "type": "excel", "path": str(d)}, tmp_path
        )
        assert src.path == d

    def test_json_from_yaml(self, tmp_path):
        from feather_flow.sources.json_source import JsonSource

        d = tmp_path / "json"
        d.mkdir()
        src = JsonSource.from_yaml(
            {"name": "j", "type": "json", "path": str(d)}, tmp_path
        )
        assert src.path == d


class TestFileSourceValidateSourceTable:
    def test_csv_accepts_filename(self, tmp_path):
        from feather_flow.sources.csv import CsvSource

        src = CsvSource(path=tmp_path, name="x")
        assert src.validate_source_table("orders.csv") == []

    def test_csv_accepts_glob(self, tmp_path):
        from feather_flow.sources.csv import CsvSource

        src = CsvSource(path=tmp_path, name="x")
        assert src.validate_source_table("sales_*.csv") == []

    def test_sqlite_rejects_dotted(self, tmp_path):
        from feather_flow.sources.sqlite import SqliteSource

        src = SqliteSource(path=tmp_path / "x.sqlite", name="x")
        errs = src.validate_source_table("schema.table")
        assert errs and "unqualified" in errs[0]
        assert "'table'" in errs[0]  # suggests correct form

    def test_sqlite_rejects_invalid_identifier(self, tmp_path):
        from feather_flow.sources.sqlite import SqliteSource

        src = SqliteSource(path=tmp_path / "x.sqlite", name="x")
        errs = src.validate_source_table("bad-name")  # hyphen not allowed
        assert errs and "identifier" in errs[0].lower()

    def test_duckdb_requires_dotted(self, tmp_path):
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        src = DuckDBFileSource(path=tmp_path / "x.duckdb", name="x")
        errs = src.validate_source_table("plain")
        assert errs and "schema.table" in errs[0]


class TestFileSourcesRejectDbFields:
    """Every file source must reject DB fields in its YAML entry with the
    correct type name in the error message."""

    @pytest.mark.parametrize(
        "cls_path,type_name,path_factory",
        [
            ("feather_flow.sources.csv.CsvSource", "csv", "dir"),
            ("feather_flow.sources.sqlite.SqliteSource", "sqlite", "file"),
            ("feather_flow.sources.duckdb_file.DuckDBFileSource", "duckdb", "file"),
            ("feather_flow.sources.excel.ExcelSource", "excel", "dir"),
            ("feather_flow.sources.json_source.JsonSource", "json", "dir"),
        ],
    )
    def test_rejects_database_field(self, cls_path, type_name, path_factory, tmp_path):
        import importlib

        module_path, cls_name = cls_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), cls_name)

        if path_factory == "dir":
            target = tmp_path / "d"
            target.mkdir()
        else:
            target = tmp_path / "f"
            target.write_bytes(b"")

        entry = {"name": "x", "type": type_name, "path": str(target), "database": "BAD"}
        with pytest.raises(
            ValueError, match=f"not supported for source type {type_name}"
        ):
            cls.from_yaml(entry, tmp_path)
