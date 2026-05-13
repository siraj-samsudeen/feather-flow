"""Workflow stage 12: feather extract.

Scenarios here exercise `feather extract` across:
- basic extraction to bronze
- missing curation.json pre-check
- --table / --source selectors
- --refresh flag (force re-extract)
- grouped output format

Renamed from test_12_cache.py per the feather-transform change
(verb `feather cache` -> `feather extract`).
"""

from __future__ import annotations


def _setup_single_table(project):
    """Minimal project: 1 DuckDB source, 1 curated table."""
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])


def _setup_two_tables(project):
    """Project with two curated tables from the same source."""
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation(
        [
            ("icube", "icube.InventoryGroup", "inv"),
            ("icube", "icube.CUSTOMERMASTER", "cust"),
        ]
    )


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


def test_cold_run_extracts_tables(project, cli):
    _setup_single_table(project)
    result = cli("extract")
    assert result.exit_code == 0
    assert "extracted" in result.output


# ---------------------------------------------------------------------------
# Missing curation.json pre-check
# ---------------------------------------------------------------------------


def test_errors_with_curation_path_when_missing(project, cli):
    # Write config but intentionally skip write_curation() — curation.json
    # must not exist so the pre-check fires.
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )

    result = cli("extract")
    assert result.exit_code == 2
    assert "curation.json" in result.output
    assert "feather discover" in result.output


# ---------------------------------------------------------------------------
# --table / --source selectors
# ---------------------------------------------------------------------------


def test_table_filter_restricts_extraction(project, cli):
    _setup_two_tables(project)
    result = cli("extract", "--table", "icube_inv")
    assert result.exit_code == 0

    rows = project.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'"
    )
    tables = {r[0] for r in rows}
    assert "icube_inv" in tables
    assert "icube_cust" not in tables


def test_source_filter_restricts_extraction(project, cli):
    _setup_two_tables(project)
    # Only one source_db here (icube); this also confirms the filter accepts it.
    result = cli("extract", "--source", "icube")
    assert result.exit_code == 0

    rows = project.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'"
    )
    tables = {r[0] for r in rows}
    assert "icube_inv" in tables
    assert "icube_cust" in tables


def test_table_and_source_intersect(project, cli):
    _setup_two_tables(project)
    result = cli("extract", "--table", "icube_inv", "--source", "icube")
    assert result.exit_code == 0

    rows = project.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze'"
    )
    tables = {r[0] for r in rows}
    assert tables == {"icube_inv"}


def test_unknown_table_errors_with_valid_list(project, cli):
    _setup_two_tables(project)
    result = cli("extract", "--table", "no_such_table")
    assert result.exit_code == 2
    assert "no_such_table" in result.output
    assert "icube_inv" in result.output
    assert "icube_cust" in result.output


def test_unknown_source_errors_with_valid_list(project, cli):
    _setup_two_tables(project)
    result = cli("extract", "--source", "nope")
    assert result.exit_code == 2
    assert "nope" in result.output
    assert "icube" in result.output


# ---------------------------------------------------------------------------
# --refresh flag
# ---------------------------------------------------------------------------


def test_refresh_forces_re_extraction(project, cli):
    _setup_single_table(project)

    # Cold run
    r1 = cli("extract")
    assert r1.exit_code == 0
    assert "1 extracted" in r1.output

    # Warm run — should be cached
    r2 = cli("extract")
    assert r2.exit_code == 0
    assert "1 cached" in r2.output

    # Refresh — should re-extract
    r3 = cli("extract", "--refresh")
    assert r3.exit_code == 0
    assert "1 extracted" in r3.output


# ---------------------------------------------------------------------------
# Grouped output format
# ---------------------------------------------------------------------------


def test_grouped_output_with_failure_expansion(project, cli):
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation(
        [
            ("icube", "icube.InventoryGroup", "good"),
            ("icube", "icube.NOPE", "bad"),
        ]
    )

    result = cli("extract")
    # Non-zero exit because of the failed table
    assert result.exit_code == 1
    out = result.output

    # Grouped: a line starts with "icube" (source_db), has counts
    assert "icube" in out
    # Summary: totals across groups
    assert "2 tables:" in out
    # Failure details expanded
    assert "✗ icube_bad" in out
