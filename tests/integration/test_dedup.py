"""Integration: dedup strategy — pipeline.run_table with dedup enabled against
CSV and JSON sources.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.helpers import make_curation_entry, write_curation


def test_dedup_true_removes_exact_duplicates(tmp_path: Path):
    """dedup: true causes SELECT DISTINCT at extraction."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    csv_dir = tmp_path / "data"
    csv_dir.mkdir()
    (csv_dir / "orders.csv").write_text(
        "order_id,name,amount\n1,A,100\n2,B,200\n1,A,100\n3,C,300\n"
    )
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(csv_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "orders.csv", "orders")],
    )
    cfg = load_config(tmp_path / "feather.yaml")
    cfg.tables[0].dedup = True

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"
    assert result.rows_loaded == 3


def test_dedup_columns_deduplicates_by_key(tmp_path: Path):
    """dedup_columns removes duplicates by specified columns."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    csv_dir = tmp_path / "data"
    csv_dir.mkdir()
    (csv_dir / "orders.csv").write_text(
        "order_id,name,amount\n1,A,100\n2,B,200\n1,X,150\n3,C,300\n"
    )
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(csv_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "orders.csv", "orders")],
    )
    cfg = load_config(tmp_path / "feather.yaml")
    cfg.tables[0].dedup_columns = ["order_id"]

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"
    assert result.rows_loaded == 3


def test_no_dedup_keeps_all_rows(tmp_path: Path):
    """Without dedup config, all rows are kept including duplicates."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    csv_dir = tmp_path / "data"
    csv_dir.mkdir()
    (csv_dir / "orders.csv").write_text(
        "order_id,name,amount\n1,A,100\n2,B,200\n1,A,100\n3,C,300\n"
    )
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(csv_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "orders.csv", "orders")],
    )
    cfg = load_config(tmp_path / "feather.yaml")

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"
    assert result.rows_loaded == 4


def test_dedup_works_for_json_source(tmp_path: Path):
    """Dedup works for JSON sources via pipeline-level _apply_dedup."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    json_dir = tmp_path / "data"
    json_dir.mkdir()
    (json_dir / "orders.json").write_text(
        '[{"id":1,"name":"A"},{"id":2,"name":"B"},{"id":1,"name":"A"}]'
    )
    config = {
        "sources": [{"type": "json", "name": "jsons", "path": str(json_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("jsons", "orders.json", "orders")],
    )
    cfg = load_config(tmp_path / "feather.yaml")
    cfg.tables[0].dedup = True

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"
    assert result.rows_loaded == 2
