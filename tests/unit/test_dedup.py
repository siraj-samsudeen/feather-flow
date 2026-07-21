"""Tests for dedup config validation (unit-level)."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.helpers import make_curation_entry, write_curation


class TestDedupConfig:
    def test_dedup_and_dedup_columns_mutually_exclusive(self, tmp_path: Path):
        """Validation rejects both dedup and dedup_columns set."""
        from feather_flow.config import load_config, _validate

        (tmp_path / "orders.csv").write_text("order_id,name\n1,A\n")

        config = {
            "sources": [{"type": "csv", "name": "csvs", "path": str(tmp_path)}],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        (tmp_path / "feather.yaml").write_text(yaml.dump(config))
        write_curation(
            tmp_path,
            [make_curation_entry("csvs", "orders.csv", "orders")],
        )
        cfg = load_config(tmp_path / "feather.yaml", validate=False)
        cfg.tables[0].dedup = True
        cfg.tables[0].dedup_columns = ["order_id"]
        errors = _validate(cfg)
        assert any("dedup" in e and "dedup_columns" in e for e in errors)
