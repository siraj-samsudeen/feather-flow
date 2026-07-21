"""Tests for incremental extraction (Slice 3)."""

from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from feather_flow.destinations.duckdb import DuckDBDestination
from feather_flow.sources.file_source import FileSource
from feather_flow.state import StateManager


class TestBuildWhereClause:
    """Unit tests for FileSource._build_where_clause()."""

    def setup_method(self):
        self.fs = FileSource(Path("/dummy"))

    def test_watermark_only(self):
        result = self.fs._build_where_clause("col", "2025-01-01", None)
        assert result == " WHERE col >= '2025-01-01' ORDER BY col"

    def test_watermark_and_filter(self):
        result = self.fs._build_where_clause("col", "2025-01-01", "status <> 1")
        assert result == " WHERE col >= '2025-01-01' AND (status <> 1) ORDER BY col"

    def test_filter_only(self):
        result = self.fs._build_where_clause(None, None, "status <> 1")
        assert result == " WHERE (status <> 1)"

    def test_no_params(self):
        result = self.fs._build_where_clause(None, None, None)
        assert result == ""


def _ts_arrow_table(timestamps: list[str], ids: list[int] | None = None) -> pa.Table:
    """Build a PyArrow table with a modified_at TIMESTAMP column."""
    ts_values = [datetime.fromisoformat(t) for t in timestamps]
    if ids is None:
        ids = list(range(1, len(timestamps) + 1))
    return pa.table(
        {
            "id": ids,
            "amount": [100.0 * i for i in ids],
            "modified_at": ts_values,
        }
    )


class TestLoadIncremental:
    """Unit tests for DuckDBDestination.load_incremental()."""

    def _setup_dest(self, tmp_path: Path) -> DuckDBDestination:
        db_path = tmp_path / "dest.duckdb"
        dest = DuckDBDestination(path=db_path)
        dest.setup_schemas()
        return dest

    def test_incremental_inserts_new_rows(self, tmp_path: Path):
        dest = self._setup_dest(tmp_path)
        data1 = _ts_arrow_table(["2025-01-01T00:00:00", "2025-01-02T00:00:00"], [1, 2])
        dest.load_full("bronze.sales", data1, "run_1")

        data2 = _ts_arrow_table(["2025-01-02T00:00:00", "2025-01-03T00:00:00"], [2, 3])
        rows = dest.load_incremental("bronze.sales", data2, "run_2", "modified_at")
        assert rows == 2

        con = duckdb.connect(str(tmp_path / "dest.duckdb"), read_only=True)
        total = con.execute("SELECT COUNT(*) FROM bronze.sales").fetchone()[0]
        assert total == 3
        con.close()

    def test_incremental_zero_rows_is_noop(self, tmp_path: Path):
        dest = self._setup_dest(tmp_path)
        data1 = _ts_arrow_table(["2025-01-01T00:00:00"], [1])
        dest.load_full("bronze.sales", data1, "run_1")

        empty = pa.table(
            {
                "id": pa.array([], type=pa.int64()),
                "amount": pa.array([], type=pa.float64()),
                "modified_at": pa.array([], type=pa.timestamp("us")),
            }
        )
        rows = dest.load_incremental("bronze.sales", empty, "run_2", "modified_at")
        assert rows == 0

        con = duckdb.connect(str(tmp_path / "dest.duckdb"), read_only=True)
        total = con.execute("SELECT COUNT(*) FROM bronze.sales").fetchone()[0]
        assert total == 1
        con.close()

    def test_incremental_deletes_by_timestamp(self, tmp_path: Path):
        dest = self._setup_dest(tmp_path)
        data1 = _ts_arrow_table(
            ["2025-01-01T00:00:00", "2025-01-02T00:00:00", "2025-01-03T00:00:00"],
            [1, 2, 3],
        )
        dest.load_full("bronze.sales", data1, "run_1")

        data2 = _ts_arrow_table(
            ["2025-01-02T00:00:00", "2025-01-03T00:00:00"], [20, 30]
        )
        dest.load_incremental("bronze.sales", data2, "run_2", "modified_at")

        con = duckdb.connect(str(tmp_path / "dest.duckdb"), read_only=True)
        ids = sorted(
            r[0] for r in con.execute("SELECT id FROM bronze.sales").fetchall()
        )
        con.close()
        assert ids == [1, 20, 30]


class TestWatermarkLastValue:
    """Unit tests for StateManager.write_watermark() with last_value."""

    def test_write_watermark_with_last_value(self, tmp_path: Path):
        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark(
            "orders",
            strategy="incremental",
            last_value="2025-01-10T00:00:00",
        )
        wm = sm.read_watermark("orders")
        assert wm["last_value"] == "2025-01-10T00:00:00"

    def test_update_watermark_last_value(self, tmp_path: Path):
        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="incremental", last_value="2025-01-05")
        sm.write_watermark("orders", strategy="incremental", last_value="2025-01-10")
        wm = sm.read_watermark("orders")
        assert wm["last_value"] == "2025-01-10"

    def test_full_strategy_watermark_has_no_last_value(self, tmp_path: Path):
        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")
        wm = sm.read_watermark("orders")
        assert wm["last_value"] is None
