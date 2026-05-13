"""Tests for feather.destinations module."""

import os
import stat
from pathlib import Path
from unittest.mock import patch

import duckdb
import pyarrow as pa
import pytest


def _sample_arrow_table(num_rows: int = 10) -> pa.Table:
    return pa.table(
        {
            "id": list(range(num_rows)),
            "name": [f"item_{i}" for i in range(num_rows)],
            "value": [float(i) * 1.5 for i in range(num_rows)],
        }
    )


class TestQualifiedTableGuard:
    """Bare table names (without `schema.`) raise ValueError on every load path."""

    @pytest.mark.parametrize(
        "method,call",
        [
            ("load_full", lambda d: d.load_full("foo", _sample_arrow_table(1), "r")),
            (
                "load_append",
                lambda d: d.load_append("foo", _sample_arrow_table(1), "r"),
            ),
            (
                "load_incremental",
                lambda d: d.load_incremental("foo", _sample_arrow_table(1), "r", "id"),
            ),
        ],
    )
    def test_rejects_bare_table_name(self, tmp_path: Path, method: str, call) -> None:
        from feather_etl.destinations.duckdb import DuckDBDestination

        dest = DuckDBDestination(path=tmp_path / "data.duckdb")
        dest.setup_schemas()
        with pytest.raises(ValueError, match=f"{method} expects schema.table"):
            call(dest)


class TestDuckDBDestination:
    def test_setup_schemas(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        con = duckdb.connect(str(db_path), read_only=True)
        schemas = [
            r[0]
            for r in con.execute(
                "SELECT schema_name FROM information_schema.schemata"
            ).fetchall()
        ]
        con.close()
        for s in ["bronze", "silver", "gold", "_quarantine"]:
            assert s in schemas

    def test_load_full_creates_table(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        data = _sample_arrow_table(10)
        rows = dest.load_full("bronze.test_table", data, "test_run_001")
        assert rows == 10

        con = duckdb.connect(str(db_path), read_only=True)
        result = con.execute("SELECT COUNT(*) FROM bronze.test_table").fetchone()
        assert result[0] == 10
        con.close()

    def test_load_full_has_etl_metadata(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        data = _sample_arrow_table(5)
        dest.load_full("bronze.test_table", data, "run_xyz")

        con = duckdb.connect(str(db_path), read_only=True)
        row = con.execute(
            "SELECT _etl_loaded_at, _etl_run_id FROM bronze.test_table LIMIT 1"
        ).fetchone()
        con.close()
        assert row[0] is not None  # _etl_loaded_at
        assert row[1] == "run_xyz"  # _etl_run_id

    def test_load_full_swap_replaces_data(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        data1 = _sample_arrow_table(10)
        dest.load_full("bronze.test_table", data1, "run_1")

        data2 = _sample_arrow_table(5)
        dest.load_full("bronze.test_table", data2, "run_2")

        con = duckdb.connect(str(db_path), read_only=True)
        count = con.execute("SELECT COUNT(*) FROM bronze.test_table").fetchone()[0]
        run_id = con.execute(
            "SELECT DISTINCT _etl_run_id FROM bronze.test_table"
        ).fetchone()[0]
        con.close()
        assert count == 5  # replaced, not appended
        assert run_id == "run_2"

    def test_new_db_gets_600_permissions(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "new_data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()
        mode = stat.S_IMODE(os.stat(db_path).st_mode)
        assert mode == 0o600

    def test_chmod_oserror_is_swallowed(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        with patch(
            "feather_etl.destinations.duckdb.os.chmod", side_effect=OSError("denied")
        ):
            dest.setup_schemas()  # should not raise
        assert db_path.exists()

    def test_load_full_rollback_on_error(self, tmp_path: Path):
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        # Don't create schemas — CREATE TABLE in nonexistent schema triggers rollback
        data = _sample_arrow_table(5)
        with pytest.raises(Exception):
            dest.load_full("nonexistent_schema.bad_table", data, "run_fail")

    def test_load_append_rollback_on_error(self, tmp_path: Path):
        """load_append must ROLLBACK and re-raise when CREATE/INSERT fails
        (here: the target schema doesn't exist, so CREATE TABLE errors)."""
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        # Skip setup_schemas → bronze/etc schemas don't exist
        data = _sample_arrow_table(3)
        with pytest.raises(Exception):
            dest.load_append("nonexistent_schema.bad_table", data, "run_append_fail")

        # DB file exists (from connect), but the bad table must not
        con = duckdb.connect(str(db_path), read_only=True)
        schemas = [
            r[0]
            for r in con.execute(
                "SELECT schema_name FROM information_schema.schemata"
            ).fetchall()
        ]
        con.close()
        assert "nonexistent_schema" not in schemas

    def test_load_incremental_rollback_on_error(self, tmp_path: Path):
        """load_incremental must ROLLBACK and re-raise when the target table
        doesn't exist (the DELETE FROM raises CatalogException)."""
        from feather_etl.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()  # bronze exists, but bronze.never_existed does NOT

        # Use a timestamp column so the min_ts computation succeeds
        data = pa.table(
            {
                "id": [1, 2, 3],
                "modified_at": pa.array(
                    ["2026-01-01", "2026-01-02", "2026-01-03"],
                    type=pa.string(),
                ),
            }
        )
        with pytest.raises(Exception):
            dest.load_incremental(
                "bronze.never_existed", data, "run_inc_fail", "modified_at"
            )

        # The table must not exist after the failed run
        con = duckdb.connect(str(db_path), read_only=True)
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'bronze'"
            ).fetchall()
        ]
        con.close()
        assert "never_existed" not in tables
