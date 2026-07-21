"""Tests for data quality checks (V9)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from feather_flow.dq import run_dq_checks


@pytest.fixture
def dq_db(tmp_path: Path):
    """Create a temp DuckDB with test data for DQ checks."""
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("""
        CREATE TABLE bronze.orders (
            order_id INTEGER,
            customer_code VARCHAR,
            amount DOUBLE
        )
    """)
    con.execute("""
        INSERT INTO bronze.orders VALUES
        (1, 'C001', 100.0),
        (2, 'C002', 200.0),
        (3, NULL, 300.0),
        (4, 'C001', 100.0),
        (4, 'C001', 100.0)
    """)
    con.close()
    return db_path


class TestNotNull:
    def test_not_null_fails_on_nulls(self, dq_db: Path):
        con = duckdb.connect(str(dq_db))
        results = run_dq_checks(
            con,
            "orders",
            "bronze.orders",
            {"not_null": ["customer_code"]},
            "run_1",
        )
        con.close()
        not_null = [r for r in results if r.check_type == "not_null"]
        assert len(not_null) == 1
        assert not_null[0].result == "fail"
        assert "1" in not_null[0].details

    def test_not_null_passes_on_clean_column(self, dq_db: Path):
        con = duckdb.connect(str(dq_db))
        results = run_dq_checks(
            con,
            "orders",
            "bronze.orders",
            {"not_null": ["order_id"]},
            "run_1",
        )
        con.close()
        not_null = [r for r in results if r.check_type == "not_null"]
        assert len(not_null) == 1
        assert not_null[0].result == "pass"


class TestUnique:
    def test_unique_fails_on_duplicates(self, dq_db: Path):
        con = duckdb.connect(str(dq_db))
        results = run_dq_checks(
            con,
            "orders",
            "bronze.orders",
            {"unique": ["order_id"]},
            "run_1",
        )
        con.close()
        unique = [r for r in results if r.check_type == "unique"]
        assert len(unique) == 1
        assert unique[0].result == "fail"

    def test_unique_passes_on_unique_column(self, tmp_path: Path):
        db_path = tmp_path / "unique.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute("CREATE TABLE bronze.items (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO bronze.items VALUES (1, 'A'), (2, 'B'), (3, 'C')")
        results = run_dq_checks(
            con,
            "items",
            "bronze.items",
            {"unique": ["id"]},
            "run_1",
        )
        con.close()
        unique = [r for r in results if r.check_type == "unique"]
        assert len(unique) == 1
        assert unique[0].result == "pass"


class TestRowCount:
    def test_row_count_always_runs(self, dq_db: Path):
        con = duckdb.connect(str(dq_db))
        results = run_dq_checks(con, "orders", "bronze.orders", None, "run_1")
        con.close()
        rc = [r for r in results if r.check_type == "row_count"]
        assert len(rc) == 1
        assert rc[0].result == "pass"
        assert "5" in rc[0].details

    def test_row_count_warns_on_zero(self, tmp_path: Path):
        db_path = tmp_path / "empty.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute("CREATE TABLE bronze.empty_table (id INTEGER)")
        results = run_dq_checks(con, "empty_table", "bronze.empty_table", None, "run_1")
        con.close()
        rc = [r for r in results if r.check_type == "row_count"]
        assert len(rc) == 1
        assert rc[0].result == "warn"


class TestDuplicate:
    def test_config_driven_duplicate_detects_exact_rows(self, dq_db: Path):
        con = duckdb.connect(str(dq_db))
        results = run_dq_checks(
            con,
            "orders",
            "bronze.orders",
            {"duplicate": True},
            "run_1",
        )
        con.close()
        dup = [r for r in results if r.check_type == "duplicate"]
        assert len(dup) == 1
        assert dup[0].result == "fail"
        assert "exact duplicate" in dup[0].details

    def test_pk_based_duplicate_detects_pk_duplicates(self, dq_db: Path):
        con = duckdb.connect(str(dq_db))
        results = run_dq_checks(
            con,
            "orders",
            "bronze.orders",
            {},
            "run_1",
            primary_key=["order_id"],
        )
        con.close()
        dup = [r for r in results if r.check_type == "duplicate"]
        assert len(dup) == 1
        assert dup[0].result == "fail"

    def test_no_duplicate_on_unique_data(self, tmp_path: Path):
        db_path = tmp_path / "unique.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute("CREATE TABLE bronze.clean (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO bronze.clean VALUES (1, 'A'), (2, 'B'), (3, 'C')")
        results = run_dq_checks(
            con,
            "clean",
            "bronze.clean",
            {},
            "run_1",
            primary_key=["id"],
        )
        con.close()
        dup = [r for r in results if r.check_type == "duplicate"]
        assert len(dup) == 1
        assert dup[0].result == "pass"

    def test_config_driven_duplicate_passes_on_unique_rows(self, tmp_path: Path):
        """duplicate: true check reports 'pass' when every row is unique."""
        db_path = tmp_path / "unique_exact.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute("CREATE TABLE bronze.clean (id INTEGER, name VARCHAR)")
        con.execute("INSERT INTO bronze.clean VALUES (1, 'A'), (2, 'B'), (3, 'C')")
        results = run_dq_checks(
            con,
            "clean",
            "bronze.clean",
            {"duplicate": True},
            "run_1",
        )
        con.close()
        dup = [r for r in results if r.check_type == "duplicate"]
        assert len(dup) == 1
        assert dup[0].result == "pass"
        assert "no exact duplicates" in dup[0].details
