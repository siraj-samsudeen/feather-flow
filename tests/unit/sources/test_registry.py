"""Tests for feather_flow.sources.registry (resolution + lazy loading)."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSourceRegistry:
    def test_registry_resolves_duckdb(self):
        from feather_flow.sources.duckdb_file import DuckDBFileSource
        from feather_flow.sources.registry import get_source_class

        assert get_source_class("duckdb") is DuckDBFileSource

    def test_registry_resolves_csv(self):
        from feather_flow.sources.csv import CsvSource
        from feather_flow.sources.registry import get_source_class

        assert get_source_class("csv") is CsvSource

    def test_registry_resolves_sqlite(self):
        from feather_flow.sources.registry import get_source_class
        from feather_flow.sources.sqlite import SqliteSource

        assert get_source_class("sqlite") is SqliteSource

    def test_get_source_class_and_instantiate(self, client_db: Path):
        from feather_flow.sources.registry import get_source_class

        cls = get_source_class("duckdb")
        source = cls(path=client_db)
        assert source.check() is True

    def test_get_source_class_unknown_type(self):
        from feather_flow.sources.registry import get_source_class

        with pytest.raises(ValueError, match="not implemented"):
            get_source_class("cassandra")


class TestLazyRegistry:
    def test_get_source_class_returns_class_by_name(self):
        from feather_flow.sources.csv import CsvSource
        from feather_flow.sources.registry import get_source_class

        assert get_source_class("csv") is CsvSource

    def test_get_source_class_unknown_raises(self):
        from feather_flow.sources.registry import get_source_class

        with pytest.raises(ValueError, match="not implemented"):
            get_source_class("oracle")

    def test_only_referenced_modules_imported(self):
        """Importing the registry alone must NOT import every source connector.

        This is the closing condition for issue #4 — pyodbc / psycopg2 stay
        unimported until a sqlserver/postgres source is requested.
        """
        import importlib
        import sys

        # Snapshot and restore sys.modules so clearing for this test does not
        # break module-level imports in other test files (e.g. test_sqlserver.py
        # imports SqlServerSource at the top level, and mock.patch targets that
        # same module object).
        snapshot = dict(sys.modules)

        try:
            for mod_name in list(sys.modules):
                if mod_name.startswith("feather_flow.sources"):
                    del sys.modules[mod_name]

            importlib.import_module("feather_flow.sources.registry")
            loaded = {m for m in sys.modules if m.startswith("feather_flow.sources")}

            assert "feather_flow.sources.sqlserver" not in loaded
            assert "feather_flow.sources.postgres" not in loaded
            assert "feather_flow.sources.csv" not in loaded
        finally:
            sys.modules.update(snapshot)
