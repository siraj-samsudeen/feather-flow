"""Integration: config.load_config + curation.load_curation_tables reading
discovery/curation.json from real project directories.

Builds real DuckDB source databases to exercise cross-source curation
routing. The local `_write_curation` / `_make_include` helpers from the
source file are replaced with the shared `tests.helpers.write_curation`
and `tests.helpers.make_curation_entry` per Rule I2.
"""

from __future__ import annotations

import duckdb
import pytest

from feather_flow.config import load_config
from tests.helpers import make_curation_entry, write_curation


def test_loads_tables_from_curation_json(project):
    src_db = project.root / "source.duckdb"
    con = duckdb.connect(str(src_db))
    con.execute("CREATE SCHEMA erp")
    con.execute("CREATE TABLE erp.orders (id INT, amount DOUBLE)")
    con.execute("INSERT INTO erp.orders VALUES (1, 100.0)")
    con.close()

    project.write_config(
        sources=[{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [make_curation_entry("erp", "erp.orders", "orders")],
    )

    cfg = load_config(project.config_path, validate=False)
    assert len(cfg.tables) == 1
    assert cfg.tables[0].name == "erp_orders"
    assert cfg.tables[0].target_table == ""  # mode-derived at runtime
    assert cfg.tables[0].source_table == "erp.orders"
    assert cfg.tables[0].database == "erp"


def test_multi_source_loads_from_curation(project):
    src_a = project.root / "source_a.duckdb"
    con = duckdb.connect(str(src_a))
    con.execute("CREATE TABLE main.items (id INT)")
    con.close()

    src_b = project.root / "source_b.duckdb"
    con = duckdb.connect(str(src_b))
    con.execute("CREATE TABLE main.users (id INT)")
    con.close()

    project.write_config(
        sources=[
            {"type": "duckdb", "name": "inventory", "path": str(src_a)},
            {"type": "duckdb", "name": "crm", "path": str(src_b)},
        ],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry("inventory", "main.items", "items"),
            make_curation_entry("crm", "main.users", "users"),
        ],
    )

    cfg = load_config(project.config_path, validate=False)
    assert len(cfg.tables) == 2
    names = {t.name for t in cfg.tables}
    assert names == {"inventory_items", "crm_users"}


def test_no_curation_no_tables_raises(project):
    src_db = project.root / "source.duckdb"
    src_db.touch()
    project.write_config(
        sources=[{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        destination={"path": str(project.data_db_path)},
    )

    with pytest.raises(FileNotFoundError, match="curation.json"):
        load_config(project.config_path)
