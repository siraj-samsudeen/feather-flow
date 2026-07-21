"""Unit: feather_flow.sources.expand — expand_db_sources.

Merged from tests/test_discover_expansion.py (4 tests) and
tests/test_expand_db_sources.py (3 tests). Both exercised the same
expand_db_sources function against a mix of real PostgresSource
instances (monkeypatched .list_databases) and MagicMock DatabaseSource
stubs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from feather_flow.sources.expand import expand_db_sources as _expand_db_sources
from feather_flow.sources.postgres import PostgresSource


def _make_postgres_source(
    *,
    explicit_name: bool,
    database: str | None = None,
    databases: list[str] | None = None,
) -> PostgresSource:
    entry: dict[str, object] = {
        "type": "postgres",
        "host": "localhost",
        "user": "tester",
        "password": "secret",
    }
    if explicit_name:
        entry["name"] = "warehouse"
    if database is not None:
        entry["database"] = database
    if databases is not None:
        entry["databases"] = databases
    return PostgresSource.from_yaml(entry, Path("."))


def test_expand_db_sources_children_inherit_explicit_true_from_parent() -> None:
    parent = _make_postgres_source(explicit_name=True, databases=["sales", "erp"])

    expanded = _expand_db_sources([parent])

    assert len(expanded) == 2
    assert all(child._explicit_name is True for child in expanded)


def test_expand_db_sources_children_inherit_explicit_false_from_parent() -> None:
    parent = _make_postgres_source(explicit_name=False)
    parent.list_databases = lambda: ["sales", "erp"]  # type: ignore[method-assign]

    expanded = _expand_db_sources([parent])

    assert len(expanded) == 2
    assert all(child._explicit_name is False for child in expanded)


def test_expand_db_sources_with_concrete_database_is_unchanged_and_preserves_flag() -> (
    None
):
    parent = _make_postgres_source(explicit_name=False, database="sales")

    expanded = _expand_db_sources([parent])

    assert expanded == [parent]
    assert expanded[0]._explicit_name is False


def test_expand_db_sources_defaults_explicit_name_when_parent_lacks_flag() -> None:
    parent = PostgresSource(
        connection_string="host=localhost port=5432 dbname=postgres",
        name="warehouse",
        host="localhost",
        port=5432,
        user="tester",
        password="secret",
        databases=["sales", "erp"],
    )

    expanded = _expand_db_sources([parent])

    assert len(expanded) == 2
    assert all(child._explicit_name is False for child in expanded)


def test_file_sources_pass_through() -> None:
    """File sources are returned unchanged."""
    mock_src = MagicMock()
    mock_src.database = None
    mock_src.databases = None
    # Not a DatabaseSource subclass — simulate a FileSource
    result = _expand_db_sources([mock_src])
    assert result == [mock_src]


def test_db_source_with_single_database_passes_through() -> None:
    """DB source with database set is returned unchanged."""
    from feather_flow.sources.database_source import DatabaseSource

    mock_src = MagicMock(spec=DatabaseSource)
    mock_src.database = "mydb"
    mock_src.databases = None
    result = _expand_db_sources([mock_src])
    assert result == [mock_src]


def test_db_source_list_databases_raises_records_last_error_and_passes_through() -> (
    None
):
    """When ``list_databases()`` raises, the source is kept in the list with
    an informative ``_last_error`` so downstream discover can report it."""
    from feather_flow.sources.database_source import DatabaseSource

    mock_src = MagicMock(spec=DatabaseSource)
    mock_src.database = None
    mock_src.databases = None
    mock_src.host = "db.example.com"
    mock_src.list_databases = MagicMock(
        side_effect=RuntimeError("permission denied for user")
    )

    result = _expand_db_sources([mock_src])

    assert result == [mock_src]
    assert "db.example.com" in mock_src._last_error
    assert "permission denied" in mock_src._last_error


def test_db_source_list_databases_returns_empty_records_last_error_and_passes_through() -> (
    None
):
    """When ``list_databases()`` returns an empty list, the source is kept
    with a friendly _last_error pointing at the likely permission issue."""
    from feather_flow.sources.database_source import DatabaseSource

    mock_src = MagicMock(spec=DatabaseSource)
    mock_src.database = None
    mock_src.databases = None
    mock_src.host = "db.example.com"
    mock_src.list_databases = MagicMock(return_value=[])

    result = _expand_db_sources([mock_src])

    assert result == [mock_src]
    assert "Found 0 databases" in mock_src._last_error
    assert "db.example.com" in mock_src._last_error


def test_db_source_with_databases_list_expands() -> None:
    """DB source with databases: [a, b] produces one child per db."""
    from feather_flow.sources.database_source import DatabaseSource

    mock_src = MagicMock(spec=DatabaseSource)
    mock_src.database = None
    mock_src.databases = ["db_a", "db_b"]
    mock_src.name = "test_server"
    mock_src.type = "sqlserver"
    mock_src.host = "localhost"
    mock_src.port = 1433
    mock_src.user = "sa"
    mock_src.password = "pass"
    mock_src._explicit_name = False

    child_a = MagicMock(spec=DatabaseSource)
    child_b = MagicMock(spec=DatabaseSource)
    type(mock_src).from_yaml = MagicMock(side_effect=[child_a, child_b])

    result = _expand_db_sources([mock_src])
    assert len(result) == 2
    calls = type(mock_src).from_yaml.call_args_list
    assert calls[0][0][0]["database"] == "db_a"
    assert calls[0][0][0]["name"] == "test_server__db_a"
    assert calls[1][0][0]["database"] == "db_b"
    assert calls[1][0][0]["name"] == "test_server__db_b"
