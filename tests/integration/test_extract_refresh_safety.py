"""#62 acceptance — refresh-all + missing-destination safety.

If feather_data.duckdb is deleted out-of-band while _extract_windows still
has rows, the next `feather extract` must NOT silently re-extract. It must
fail per-table with a message pointing at --refresh-all. With --refresh-all,
the commit log is wiped and extraction proceeds normally.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from feather_etl.config import load_config
from feather_etl.extract import run_extract
from feather_etl.state import StateManager
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


def test_missing_destination_with_committed_windows_returns_failure(
    csv_cfg_and_tables,
) -> None:
    """Deleting feather_data.duckdb while _extract_windows has rows must
    return per-table failure results pointing the operator at --refresh-all."""
    cfg, tables, tmp_path = csv_cfg_and_tables

    # Prime: first extract populates _extract_windows.
    results = run_extract(cfg, tables, tmp_path)
    assert all(r.status == "success" for r in results)

    state = StateManager(tmp_path / "feather_state.duckdb")
    assert state.get_committed_windows(BRONZE_TABLE) == {"all"}

    # Simulate operator deleting feather_data.duckdb out-of-band.
    cfg.destination.path.unlink()
    assert not cfg.destination.path.exists()

    # Re-run WITHOUT --refresh-all — must fail, not silently re-extract.
    results2 = run_extract(cfg, tables, tmp_path)
    assert all(r.status == "failure" for r in results2), (
        f"Expected all failure, got: {[r.status for r in results2]}"
    )
    # Error message must mention destination and --refresh-all.
    for r in results2:
        assert r.error_message is not None
        msg = r.error_message
        assert "feather_data.duckdb" in msg or "destination" in msg.lower(), (
            f"Expected destination mention in: {msg!r}"
        )
        assert "--refresh-all" in msg, f"Expected --refresh-all mention in: {msg!r}"


def test_refresh_all_clears_windows_and_reextracts(
    csv_cfg_and_tables,
) -> None:
    """With --refresh-all, the commit log is wiped and extraction proceeds
    normally even when feather_data.duckdb has been deleted."""
    cfg, tables, tmp_path = csv_cfg_and_tables

    # Prime: first extract populates _extract_windows.
    results = run_extract(cfg, tables, tmp_path)
    assert all(r.status == "success" for r in results)

    state = StateManager(tmp_path / "feather_state.duckdb")
    assert state.get_committed_windows(BRONZE_TABLE) == {"all"}

    # Simulate operator deleting feather_data.duckdb out-of-band.
    cfg.destination.path.unlink()
    assert not cfg.destination.path.exists()

    # Re-run WITH --refresh-all (and --refresh to bypass change-detection
    # on the unchanged source file) — should succeed and re-record windows.
    results2 = run_extract(cfg, tables, tmp_path, refresh=True, refresh_all=True)
    assert all(r.status == "success" for r in results2), (
        f"Expected all success, got: {[r.status for r in results2]}"
    )
    # _extract_windows should have exactly one entry per table (re-recorded).
    assert state.get_committed_windows(BRONZE_TABLE) == {"all"}
