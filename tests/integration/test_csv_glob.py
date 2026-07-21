"""Integration: CSV glob extraction + change detection — pipeline runs across
multiple CSV files with mtime tracking.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


GLOB_FIXTURES = FIXTURES_DIR / "csv_glob"


def test_glob_extracts_all_matching_files(tmp_path: Path):
    """source_table: 'sales_*.csv' extracts all matching files as one table."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    data_dir = tmp_path / "data"
    shutil.copytree(GLOB_FIXTURES, data_dir)
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(data_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "sales_*.csv", "sales")],
    )
    cfg = load_config(tmp_path / "feather.yaml")

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "success"
    assert result.rows_loaded == 5


def test_glob_no_match_fails(tmp_path: Path):
    """Glob that matches no files should fail gracefully."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "other.csv").write_text("id\n1\n")
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(data_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "sales_*.csv", "sales")],
    )
    cfg = load_config(tmp_path / "feather.yaml")

    result = run_table(cfg, cfg.tables[0], tmp_path)
    assert result.status == "skipped"


def test_no_change_skips(tmp_path: Path):
    """Second run with no file changes should skip extraction."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    data_dir = tmp_path / "data"
    shutil.copytree(GLOB_FIXTURES, data_dir)
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(data_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "sales_*.csv", "sales")],
    )
    cfg = load_config(tmp_path / "feather.yaml")

    run_table(cfg, cfg.tables[0], tmp_path)
    result2 = run_table(cfg, cfg.tables[0], tmp_path)
    assert result2.status == "skipped"


def test_new_file_triggers_reextract(tmp_path: Path):
    """Adding a new matching file should trigger re-extraction."""
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    data_dir = tmp_path / "data"
    shutil.copytree(GLOB_FIXTURES, data_dir)
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(data_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "sales_*.csv", "sales")],
    )
    cfg = load_config(tmp_path / "feather.yaml")

    run_table(cfg, cfg.tables[0], tmp_path)

    (data_dir / "sales_mar.csv").write_text(
        "order_id,customer,amount,month\n6,Frank,400,mar\n"
    )

    result2 = run_table(cfg, cfg.tables[0], tmp_path)
    assert result2.status == "success"
    assert result2.rows_loaded == 6


def test_modified_file_triggers_reextract(tmp_path: Path):
    """Modifying a matching file should trigger re-extraction."""
    import time
    from feather_flow.config import load_config
    from feather_flow.pipeline import run_table

    data_dir = tmp_path / "data"
    shutil.copytree(GLOB_FIXTURES, data_dir)
    config = {
        "sources": [{"type": "csv", "name": "csvs", "path": str(data_dir)}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }
    (tmp_path / "feather.yaml").write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("csvs", "sales_*.csv", "sales")],
    )
    cfg = load_config(tmp_path / "feather.yaml")

    run_table(cfg, cfg.tables[0], tmp_path)

    time.sleep(0.1)
    (data_dir / "sales_jan.csv").write_text(
        "order_id,customer,amount,month\n1,Alice,100,jan\n2,Bob,200,jan\n"
    )

    result2 = run_table(cfg, cfg.tables[0], tmp_path)
    assert result2.status == "success"
    assert result2.rows_loaded == 4
