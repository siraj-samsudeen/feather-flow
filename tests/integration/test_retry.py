"""Integration: retry policy + StateManager — pipeline.run_table blocks retries
after backoff.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


def _make_broken_config(tmp_path: Path) -> Path:
    """Config with valid source file but non-existent table to force extraction failure."""
    client_db = tmp_path / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    config = {
        "sources": [{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("icube", "icube.nonexistent_table_xyz", "orders")],
    )
    return config_file


def _make_good_config(tmp_path: Path) -> Path:
    """Config pointing at a real DuckDB fixture."""
    client_db = tmp_path / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    config = {
        "sources": [{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("icube", "icube.InventoryGroup", "inventory_group")],
    )
    return config_file


def test_failure_increments_retry_count(tmp_path: Path):
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table
    from feather_flow.state import StateManager

    config_path = _make_broken_config(tmp_path)
    cfg = load_config(config_path)

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "failure"

    sm = StateManager(tmp_path / "feather_state.duckdb")
    wm = sm.read_watermark("icube_orders")
    assert wm is not None
    assert wm["retry_count"] == 1
    assert wm["retry_after"] is not None


def test_table_in_backoff_is_skipped(tmp_path: Path):
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    config_path = _make_broken_config(tmp_path)
    cfg = load_config(config_path)

    run_table(cfg, cfg.tables[0], tmp_path)

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "skipped"
    assert result.error_message is not None


def test_success_resets_retry_count(tmp_path: Path):
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table
    from feather_flow.state import StateManager

    config_path = _make_good_config(tmp_path)
    cfg = load_config(config_path)

    sm = StateManager(tmp_path / "feather_state.duckdb")
    sm.init_state()
    sm.write_watermark("icube_inventory_group", strategy="full")
    sm.increment_retry("icube_inventory_group")
    sm.increment_retry("icube_inventory_group")

    wm = sm.read_watermark("icube_inventory_group")
    assert wm["retry_count"] == 2

    sm.reset_retry("icube_inventory_group")
    sm.increment_retry("icube_inventory_group")
    import duckdb

    con = duckdb.connect(str(sm.path))
    con.execute(
        "UPDATE _watermarks SET retry_count = 2, retry_after = ? "
        "WHERE table_name = 'icube_inventory_group'",
        [datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)],
    )
    con.close()

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"

    wm = sm.read_watermark("icube_inventory_group")
    assert wm["retry_count"] == 0
    assert wm["retry_after"] is None


def test_other_tables_continue_when_one_fails(tmp_path: Path):
    """FR13.5: per-table isolation."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_all

    client_db = tmp_path / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    config = {
        "sources": [{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [
            make_curation_entry("icube", "icube.InventoryGroup", "inventory_group"),
            make_curation_entry("icube", "icube.nonexistent_table", "bad_table"),
        ],
    )

    cfg = load_config(config_file)
    results = run_all(cfg)

    statuses = {r.table_name: r.status for r in results}
    assert statuses["icube_inventory_group"] == "success"
    assert statuses["icube_bad_table"] == "failure"
