from pathlib import Path

from feather_flow.config import load_config
from feather_flow.sources.duckdb_file import DuckDBFileSource
from feather_flow.sources.postgres import PostgresSource
from tests.helpers import write_config


def test_file_source_sets_explicit_name_true_when_name_present(tmp_path: Path) -> None:
    source = DuckDBFileSource.from_yaml(
        {"type": "duckdb", "path": "source.duckdb", "name": "warehouse"},
        tmp_path,
    )
    assert source._explicit_name is True


def test_file_source_sets_explicit_name_false_when_name_omitted(tmp_path: Path) -> None:
    source = DuckDBFileSource.from_yaml(
        {"type": "duckdb", "path": "source.duckdb"},
        tmp_path,
    )
    assert source._explicit_name is False


def test_db_source_sets_explicit_name_true_when_name_present(tmp_path: Path) -> None:
    source = PostgresSource.from_yaml(
        {
            "type": "postgres",
            "connection_string": "host=localhost port=5432 dbname=postgres",
            "name": "pg_primary",
        },
        tmp_path,
    )
    assert source._explicit_name is True


def test_db_source_sets_explicit_name_false_when_name_omitted(tmp_path: Path) -> None:
    source = PostgresSource.from_yaml(
        {"type": "postgres", "connection_string": "host=localhost dbname=postgres"},
        tmp_path,
    )
    assert source._explicit_name is False


def test_single_source_name_backfill_keeps_explicit_name_false(tmp_path: Path) -> None:
    source_path = tmp_path / "source.duckdb"
    source_path.touch()
    config = {
        "sources": [{"type": "duckdb", "path": str(source_path)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
        "tables": [
            {
                "name": "test_table",
                "source_table": "main.test",
                "target_table": "bronze.test_table",
                "strategy": "full",
            }
        ],
    }

    config_file = write_config(tmp_path, config)
    loaded = load_config(config_file, validate=False)

    source = loaded.sources[0]
    assert source.name != ""
    assert source._explicit_name is False
