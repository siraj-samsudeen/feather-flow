"""Tests for append load strategy."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa


def _make_table(num_rows: int = 5, id_offset: int = 0) -> pa.Table:
    return pa.table(
        {
            "id": list(range(id_offset, id_offset + num_rows)),
            "name": [f"item_{i}" for i in range(id_offset, id_offset + num_rows)],
        }
    )


class TestLoadAppend:
    def test_load_append_inserts_with_metadata(self, tmp_path: Path):
        from feather_flow.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        data = _make_table(5)
        rows = dest.load_append("bronze.audit_log", data, "run_001")

        assert rows == 5

        con = duckdb.connect(str(db_path), read_only=True)
        count = con.execute("SELECT COUNT(*) FROM bronze.audit_log").fetchone()[0]
        row = con.execute(
            "SELECT _etl_loaded_at, _etl_run_id FROM bronze.audit_log LIMIT 1"
        ).fetchone()
        con.close()

        assert count == 5
        assert row[0] is not None
        assert row[1] == "run_001"

    def test_load_append_accumulates_rows(self, tmp_path: Path):
        from feather_flow.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        data1 = _make_table(5, id_offset=0)
        dest.load_append("bronze.audit_log", data1, "run_001")

        data2 = _make_table(3, id_offset=100)
        dest.load_append("bronze.audit_log", data2, "run_002")

        con = duckdb.connect(str(db_path), read_only=True)
        count = con.execute("SELECT COUNT(*) FROM bronze.audit_log").fetchone()[0]
        run_ids = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT _etl_run_id FROM bronze.audit_log"
            ).fetchall()
        }
        con.close()

        assert count == 8
        assert run_ids == {"run_001", "run_002"}

    def test_load_append_creates_table_if_not_exists(self, tmp_path: Path):
        from feather_flow.destinations.duckdb import DuckDBDestination

        db_path = tmp_path / "data.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()

        data = _make_table(2)
        dest.load_append("bronze.new_table", data, "run_001")

        dest.load_append("bronze.new_table", _make_table(3, id_offset=10), "run_002")

        con = duckdb.connect(str(db_path), read_only=True)
        count = con.execute("SELECT COUNT(*) FROM bronze.new_table").fetchone()[0]
        con.close()
        assert count == 5
