"""Tests for boundary deduplication (V16)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa


def _pk_hash(*values) -> str:
    """Compute PK hash the same way the pipeline should."""
    key = "|".join(str(v) for v in values)
    return hashlib.sha256(key.encode()).hexdigest()


class TestBoundaryHashStateManager:
    def test_write_and_read_boundary_hashes(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="incremental")

        hashes = [_pk_hash(100), _pk_hash(101)]
        sm.write_boundary_hashes("orders", hashes)

        result = sm.read_boundary_hashes("orders")
        assert result == hashes

    def test_read_returns_empty_when_no_hashes(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="incremental")

        result = sm.read_boundary_hashes("orders")
        assert result == []

    def test_read_returns_empty_for_nonexistent_table(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        result = sm.read_boundary_hashes("nonexistent")
        assert result == []

    def test_overwrite_replaces_previous(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="incremental")

        sm.write_boundary_hashes("orders", [_pk_hash(1)])
        sm.write_boundary_hashes("orders", [_pk_hash(2), _pk_hash(3)])

        result = sm.read_boundary_hashes("orders")
        assert result == [_pk_hash(2), _pk_hash(3)]


class TestBoundaryHashComputation:
    def test_compute_pk_hashes(self):
        from feather_flow.pipeline import _compute_pk_hashes

        data = pa.table(
            {
                "id": [1, 2, 3],
                "ts": ["2026-01-01", "2026-01-01", "2026-01-02"],
                "value": [10, 20, 30],
            }
        )
        hashes = _compute_pk_hashes(data, ["id"], "ts", "2026-01-01")
        assert len(hashes) == 2
        assert _pk_hash(1) in hashes
        assert _pk_hash(2) in hashes

    def test_multi_column_pk(self):
        from feather_flow.pipeline import _compute_pk_hashes

        data = pa.table(
            {
                "id1": [1, 1],
                "id2": ["a", "b"],
                "ts": ["2026-01-01", "2026-01-01"],
            }
        )
        hashes = _compute_pk_hashes(data, ["id1", "id2"], "ts", "2026-01-01")
        assert len(hashes) == 2
        assert _pk_hash(1, "a") in hashes
        assert _pk_hash(1, "b") in hashes

    def test_no_rows_at_boundary(self):
        from feather_flow.pipeline import _compute_pk_hashes

        data = pa.table(
            {
                "id": [1, 2],
                "ts": ["2026-01-01", "2026-01-02"],
            }
        )
        hashes = _compute_pk_hashes(data, ["id"], "ts", "2026-01-03")
        assert hashes == []


class TestBoundaryFiltering:
    def test_filter_boundary_rows(self):
        from feather_flow.pipeline import _filter_boundary_rows

        data = pa.table(
            {
                "id": [1, 2, 3, 4],
                "ts": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
                "value": [10, 20, 30, 40],
            }
        )
        # Previous boundary hashes: row with id=1 at old watermark "2026-01-01"
        prev_hashes = [_pk_hash(1)]
        old_watermark = "2026-01-01"

        filtered, skipped = _filter_boundary_rows(
            data, ["id"], "ts", old_watermark, prev_hashes
        )
        assert filtered.num_rows == 3  # id=1 filtered out
        assert skipped == 1
        assert filtered.column("id").to_pylist() == [2, 3, 4]

    def test_no_filtering_when_no_prev_hashes(self):
        from feather_flow.pipeline import _filter_boundary_rows

        data = pa.table(
            {
                "id": [1, 2],
                "ts": ["2026-01-01", "2026-01-02"],
            }
        )
        filtered, skipped = _filter_boundary_rows(data, ["id"], "ts", "2026-01-01", [])
        assert filtered.num_rows == 2
        assert skipped == 0

    def test_no_filtering_when_no_pk(self):
        from feather_flow.pipeline import _filter_boundary_rows

        data = pa.table(
            {
                "id": [1, 2],
                "ts": ["2026-01-01", "2026-01-01"],
            }
        )
        filtered, skipped = _filter_boundary_rows(
            data, None, "ts", "2026-01-01", [_pk_hash(1)]
        )
        assert filtered.num_rows == 2
        assert skipped == 0
