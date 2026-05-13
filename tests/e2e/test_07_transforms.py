"""Workflow stage 07: transforms — silver views, gold materialization via CLI.

Scenarios here exercise `feather setup` with transforms configured: the
setup command reads `transforms/*.sql`, discovers the DAG, and persists
silver views + gold materialized tables.

This file currently contains only the CLI-invoking transform tests;
integration-level transform tests (DAG execution without CLI) live in
tests/integration/test_transforms.py after Wave C.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


def _write_sql(base: Path, schema: str, name: str, content: str) -> Path:
    """Write a .sql file under base/transforms/{schema}/{name}.sql."""
    d = base / "transforms" / schema
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.sql"
    p.write_text(content)
    return p


def test_setup_creates_transforms(project, cli):
    source_db = project.root / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    con = duckdb.connect(str(project.data_db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE TABLE bronze.src_employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO bronze.src_employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(source_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation([("src", "erp.employees", "employees")])

    _write_sql(
        project.root,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )

    result = cli("setup")

    assert result.exit_code == 0
    assert "Transforms applied" in result.output
    assert "1 silver view(s)" in result.output


def test_setup_reports_gold_views_with_force_views(project, cli):
    """With --force-views, all transforms are views — including gold. The command
    reports gold view count separately from silver view count. (commands/setup.py:58)"""
    source_db = project.root / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'A'), (2, 'B')")
    con.close()

    # Seed bronze so silver/gold can build against it
    con = duckdb.connect(str(project.data_db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE TABLE bronze.src_employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO bronze.src_employees VALUES (1, 'A'), (2, 'B')")
    con.close()

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(source_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation([("src", "erp.employees", "employees")])

    _write_sql(
        project.root,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )
    _write_sql(
        project.root,
        "gold",
        "emp_summary",
        "SELECT COUNT(*) AS n FROM silver.emp_clean",
    )

    result = cli("setup", "--force-views")
    assert result.exit_code == 0
    assert "1 silver view(s)" in result.output
    assert "1 gold view(s)" in result.output


def test_setup_reports_gold_tables_by_default(project, cli):
    """By default, gold transforms marked ``materialized: true`` are
    rebuilt as tables. The output reports them separately as 'gold table(s)'.
    (commands/setup.py:63)"""
    source_db = project.root / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'A'), (2, 'B')")
    con.close()

    con = duckdb.connect(str(project.data_db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE TABLE bronze.src_employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO bronze.src_employees VALUES (1, 'A'), (2, 'B')")
    con.close()

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(source_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation([("src", "erp.employees", "employees")])

    _write_sql(
        project.root,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )
    _write_sql(
        project.root,
        "gold",
        "emp_summary",
        "-- materialized: true\nSELECT COUNT(*) AS n FROM silver.emp_clean",
    )

    result = cli("setup")
    assert result.exit_code == 0
    assert "gold table(s)" in result.output


def test_setup_reports_transform_errors_on_stderr(project, cli):
    """A transform whose SQL is broken produces an entry with status='error';
    the setup command prints it as 'Transform error: ...'. (commands/setup.py:68)"""
    source_db = project.root / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT)")
    con.close()

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(source_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation([("src", "erp.employees", "employees")])

    # Syntactically valid SQL that references a nonexistent table
    _write_sql(
        project.root,
        "silver",
        "broken",
        "SELECT * FROM bronze.does_not_exist_anywhere",
    )

    result = cli("setup")
    # Broken transforms don't fail setup (they're logged), so exit 0
    assert result.exit_code == 0
    combined = result.output + (result.stderr or "")
    assert "Transform error" in combined
    assert "broken" in combined


def test_setup_no_transforms_dir_ok(project, cli):
    source_db = project.root / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.close()

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(source_db)}],
        destination={"path": str(project.data_db_path)},
    )
    project.write_curation([("src", "erp.employees", "employees")])

    result = cli("setup")

    assert result.exit_code == 0
    assert "Transforms applied" not in result.output
