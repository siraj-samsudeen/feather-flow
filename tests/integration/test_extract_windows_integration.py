"""run_extract now drives load_batched_append per window. Even with the
single-window stub, the _extract_windows commit log must be populated
and second runs must skip the same window (or be skipped by upstream
change detection).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from feather_flow.config import load_config
from feather_flow.extract import run_extract
from feather_flow.state import StateManager
from tests.helpers import make_curation_entry, write_curation


@pytest.fixture
def csv_cfg_and_tables(tmp_path: Path):
    csv_dir = tmp_path / "src"
    csv_dir.mkdir()
    (csv_dir / "items.csv").write_text("id,name\n1,a\n2,b\n")

    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(csv_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config, default_flow_style=False))
    write_curation(tmp_path, [make_curation_entry("csvs", "items.csv", "items")])

    cfg = load_config(tmp_path / "feather.yaml")
    return cfg, cfg.tables, tmp_path


BRONZE_TABLE = "bronze.csvs_items"  # _sanitize_bronze_name("csvs", "items")


def test_first_extract_records_committed_window(csv_cfg_and_tables) -> None:
    cfg, tables, tmp_path = csv_cfg_and_tables
    results = run_extract(cfg, tables, tmp_path)
    assert all(r.status == "success" for r in results)

    state = StateManager(tmp_path / "feather_state.duckdb")
    assert state.get_committed_windows(BRONZE_TABLE) == {"all"}


def test_second_extract_with_unchanged_source_is_cached(csv_cfg_and_tables) -> None:
    """Upstream change-detection still wins for unchanged files — we don't
    even reach the window-skip path. Pin this so #63 doesn't accidentally
    re-extract unchanged files."""
    cfg, tables, tmp_path = csv_cfg_and_tables
    run_extract(cfg, tables, tmp_path)
    results = run_extract(cfg, tables, tmp_path)
    assert [r.status for r in results] == ["cached"]


def test_refresh_clears_window_and_reextracts(csv_cfg_and_tables) -> None:
    cfg, tables, tmp_path = csv_cfg_and_tables
    run_extract(cfg, tables, tmp_path)

    state = StateManager(tmp_path / "feather_state.duckdb")
    assert state.get_committed_windows(BRONZE_TABLE) == {"all"}

    results = run_extract(cfg, tables, tmp_path, refresh=True)
    assert all(r.status == "success" for r in results)
    # Still exactly one committed window — refresh truncated then re-recorded.
    assert state.get_committed_windows(BRONZE_TABLE) == {"all"}
