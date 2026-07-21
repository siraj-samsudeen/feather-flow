"""Integration: NDJSON logging via pipeline.run_all — asserts feather_run.jsonl
structure.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


def test_feather_log_jsonl_created(tmp_path: Path):
    """feather_log.jsonl is created alongside state DB after feather run."""
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
        [make_curation_entry("icube", "icube.InventoryGroup", "inventory_group")],
    )
    cfg = load_config(config_file)

    run_all(cfg)

    log_path = tmp_path / "feather_log.jsonl"
    assert log_path.exists()


def test_each_line_is_valid_json(tmp_path: Path):
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
        [make_curation_entry("icube", "icube.InventoryGroup", "inventory_group")],
    )
    cfg = load_config(config_file)

    run_all(cfg)

    log_path = tmp_path / "feather_log.jsonl"
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) >= 1
    for line in lines:
        entry = json.loads(line)
        assert "timestamp" in entry
        assert "level" in entry
        assert "event" in entry


def test_log_is_append_only(tmp_path: Path):
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
        [make_curation_entry("icube", "icube.InventoryGroup", "inventory_group")],
    )
    cfg = load_config(config_file)

    run_all(cfg)
    log_path = tmp_path / "feather_log.jsonl"
    lines_after_first = len(log_path.read_text().strip().split("\n"))

    run_all(cfg)
    lines_after_second = len(log_path.read_text().strip().split("\n"))

    assert lines_after_second > lines_after_first
