"""Integration: incremental extraction — watermarks, overlap, pipeline.run_table
with strategy=incremental.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from feather_etl.config import load_config
from feather_etl.pipeline import run_table
from feather_etl.state import StateManager
from tests.helpers import make_curation_entry, write_curation


def _make_source_db(root: Path) -> Path:
    """Create a small DuckDB source (erp.orders) with timestamped rows."""
    src_path = root / "source.duckdb"
    con = duckdb.connect(str(src_path))
    con.execute("CREATE SCHEMA erp")
    con.execute("""
        CREATE TABLE erp.orders (
            order_id INTEGER,
            amount DECIMAL(10,2),
            modified_at TIMESTAMP
        )
    """)
    con.execute("""
        INSERT INTO erp.orders VALUES
        (1, 100.00, '2025-01-01 00:00:00'),
        (2, 200.00, '2025-01-02 00:00:00'),
        (3, 300.00, '2025-01-03 00:00:00'),
        (4, 400.00, '2025-01-04 00:00:00'),
        (5, 500.00, '2025-01-05 00:00:00')
    """)
    con.close()
    return src_path


def _write_incremental_config(
    project,
    src_path: Path,
    *,
    source_table: str,
    alias: str,
    primary_key: list[str],
    filter: str | None = None,
):
    """Write config + curation for an incremental run, return loaded cfg."""
    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(src_path)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                source_table,
                alias,
                strategy="incremental",
                timestamp_column="modified_at",
                primary_key=primary_key,
            ),
        ],
    )
    cfg = load_config(project.config_path)
    if filter:
        cfg.tables[0].filter = filter
    return cfg


# ---------------------------------------------------------------------------
# pipeline.run_table with incremental strategy (synthetic erp.orders source)
# ---------------------------------------------------------------------------


def test_first_incremental_run_extracts_all(project):
    src_path = _make_source_db(project.root)
    cfg = _write_incremental_config(
        project,
        src_path,
        source_table="erp.orders",
        alias="orders",
        primary_key=["order_id"],
    )

    result = run_table(cfg, cfg.tables[0], project.root)

    assert result.status == "success"
    assert result.rows_loaded == 5

    sm = StateManager(project.state_db_path)
    wm = sm.read_watermark("src_orders")
    assert wm is not None
    assert wm["last_value"] is not None
    assert "2025-01-05" in str(wm["last_value"])


def test_second_run_no_new_rows_extracts_zero(project):
    src_path = _make_source_db(project.root)
    cfg = _write_incremental_config(
        project,
        src_path,
        source_table="erp.orders",
        alias="orders",
        primary_key=["order_id"],
    )

    run_table(cfg, cfg.tables[0], project.root)
    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "skipped"


def test_incremental_after_new_rows(project):
    src_path = _make_source_db(project.root)
    cfg = _write_incremental_config(
        project,
        src_path,
        source_table="erp.orders",
        alias="orders",
        primary_key=["order_id"],
    )

    run_table(cfg, cfg.tables[0], project.root)

    con = duckdb.connect(str(src_path))
    con.execute("""
        INSERT INTO erp.orders VALUES
        (6, 600.00, '2025-01-06 00:00:00'),
        (7, 700.00, '2025-01-07 00:00:00')
    """)
    con.close()

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"
    assert result.rows_loaded == 3

    sm = StateManager(project.state_db_path)
    wm = sm.read_watermark("src_orders")
    assert "2025-01-07" in str(wm["last_value"])


def test_run_after_incremental_no_new_rows_watermark_unchanged(project):
    src_path = _make_source_db(project.root)
    cfg = _write_incremental_config(
        project,
        src_path,
        source_table="erp.orders",
        alias="orders",
        primary_key=["order_id"],
    )

    run_table(cfg, cfg.tables[0], project.root)

    con = duckdb.connect(str(src_path))
    con.execute("INSERT INTO erp.orders VALUES (6, 600.00, '2025-01-06 00:00:00')")
    con.close()

    run_table(cfg, cfg.tables[0], project.root)
    sm = StateManager(project.state_db_path)
    wm_after_second = sm.read_watermark("src_orders")
    watermark_after_second = wm_after_second["last_value"]

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "skipped"
    wm_after_third = sm.read_watermark("src_orders")
    assert wm_after_third["last_value"] == watermark_after_second


def test_filter_excludes_matching_rows(project):
    src_path = project.root / "source2.duckdb"
    con = duckdb.connect(str(src_path))
    con.execute("CREATE SCHEMA erp")
    con.execute("""
        CREATE TABLE erp.orders (
            order_id INTEGER, amount DECIMAL(10,2),
            status VARCHAR, modified_at TIMESTAMP
        )
    """)
    con.execute("""
        INSERT INTO erp.orders VALUES
        (1, 100.00, 'active',    '2025-01-01 00:00:00'),
        (2, 200.00, 'active',    '2025-01-02 00:00:00'),
        (3, 300.00, 'cancelled', '2025-01-03 00:00:00'),
        (4, 400.00, 'active',    '2025-01-04 00:00:00'),
        (5, 500.00, 'cancelled', '2025-01-05 00:00:00')
    """)
    con.close()

    cfg = _write_incremental_config(
        project,
        src_path,
        source_table="erp.orders",
        alias="orders",
        primary_key=["order_id"],
        filter="status <> 'cancelled'",
    )
    result = run_table(cfg, cfg.tables[0], project.root)

    assert result.status == "success"
    assert result.rows_loaded == 3

    cancelled = project.query(
        "SELECT COUNT(*) FROM bronze.src_orders WHERE status = 'cancelled'"
    )[0][0]
    assert cancelled == 0


# ---------------------------------------------------------------------------
# Integration with sample_erp.duckdb fixture (erp.sales)
# ---------------------------------------------------------------------------


def test_full_incremental_cycle_with_fixture(sample_erp_db, project):
    cfg = _write_incremental_config(
        project,
        sample_erp_db,
        source_table="erp.sales",
        alias="sales",
        primary_key=["sale_id"],
    )

    r1 = run_table(cfg, cfg.tables[0], project.root)
    assert r1.status == "success"
    assert r1.rows_loaded == 10

    sm = StateManager(project.state_db_path)
    wm1 = sm.read_watermark("src_sales")
    assert "2025-01-10" in str(wm1["last_value"])

    r2 = run_table(cfg, cfg.tables[0], project.root)
    assert r2.status == "skipped"

    con = duckdb.connect(str(sample_erp_db))
    con.execute("""
        INSERT INTO erp.sales VALUES
        (11, 105, 1100.00, 'completed', '2025-01-11 00:00:00'),
        (12, 106, 1200.00, 'completed', '2025-01-12 00:00:00')
    """)
    con.close()

    r3 = run_table(cfg, cfg.tables[0], project.root)
    assert r3.status == "success"
    assert r3.rows_loaded == 3

    wm3 = sm.read_watermark("src_sales")
    assert "2025-01-12" in str(wm3["last_value"])

    r4 = run_table(cfg, cfg.tables[0], project.root)
    assert r4.status == "skipped"

    total = project.query("SELECT COUNT(*) FROM bronze.src_sales")[0][0]
    assert total == 12


def test_filter_with_fixture(sample_erp_db, project):
    cfg = _write_incremental_config(
        project,
        sample_erp_db,
        source_table="erp.sales",
        alias="sales",
        primary_key=["sale_id"],
        filter="status <> 'cancelled'",
    )
    result = run_table(cfg, cfg.tables[0], project.root)

    assert result.status == "success"
    assert result.rows_loaded == 8

    cancelled = project.query(
        "SELECT COUNT(*) FROM bronze.src_sales WHERE status = 'cancelled'"
    )[0][0]
    assert cancelled == 0


def test_incremental_second_run_zero_new_rows_success_path(project):
    """Subsequent incremental run where change-detection flags the source
    but the watermark-filtered extract yields zero new rows.

    Scenario: run once → watermark = 2025-01-05. Then edit an existing row
    so that (a) the file hash changes (change-detection fires) and (b) no
    row with ts >= stored watermark remains. Second run:
      - incremental extract returns num_rows == 0
      - pipeline hits the ``if data.num_rows == 0`` short-circuit and
        returns status='success', rows_loaded=0, watermark unchanged.
    This exercises pipeline.py:338-360.
    """
    src_path = _make_source_db(project.root)

    # overlap = 0 so only the exact-boundary row comes back next time
    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(src_path)}],
        destination={"path": str(project.data_db_path)},
        defaults={"overlap_window_minutes": 0},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.orders",
                "orders",
                strategy="incremental",
                timestamp_column="modified_at",
                primary_key=["order_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)

    # First run: loads all 5 rows, watermark=2025-01-05.
    first = run_table(cfg, cfg.tables[0], project.root)
    assert first.status == "success"
    assert first.rows_loaded == 5

    sm = StateManager(project.state_db_path)
    wm_before = sm.read_watermark("src_orders")
    first_last_value = wm_before["last_value"]

    # Shift the max-ts row *earlier* (still touching the file hash) so that
    # no rows with modified_at >= stored watermark (2025-01-05) remain. With
    # overlap=0, the incremental extract then returns zero rows directly.
    con = duckdb.connect(str(src_path))
    con.execute(
        "UPDATE erp.orders SET modified_at = '2025-01-04 12:00:00' WHERE order_id = 5"
    )
    con.close()

    # Second run: change-detection says changed, but the watermark-filtered
    # extract returns num_rows == 0 directly.
    second = run_table(cfg, cfg.tables[0], project.root)

    assert second.status == "success"
    assert second.rows_loaded == 0

    wm_after = sm.read_watermark("src_orders")
    # Watermark last_value is preserved across the no-new-rows run.
    assert str(wm_after["last_value"]) == str(first_last_value)

    # Retry state is cleared as part of the success path (see reset_retry).
    # The bronze data shouldn't have grown.
    total = project.query("SELECT COUNT(*) FROM bronze.src_orders")[0][0]
    assert total == 5


def test_incremental_row_limit_applies(project):
    """On the subsequent-incremental path, defaults.row_limit slices the
    extracted data unconditionally. (pipeline.py)"""
    src_path = _make_source_db(project.root)

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(src_path)}],
        destination={"path": str(project.data_db_path)},
        defaults={"overlap_window_minutes": 0, "row_limit": 1},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.orders",
                "orders",
                strategy="incremental",
                timestamp_column="modified_at",
                primary_key=["order_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)

    # First run primes the watermark (test mode also slices here, but via
    # the else branch — not what we're testing).
    run_table(cfg, cfg.tables[0], project.root)

    # Add 3 new rows above the current watermark
    con = duckdb.connect(str(src_path))
    con.execute("""
        INSERT INTO erp.orders VALUES
        (6, 600.00, '2025-01-06 00:00:00'),
        (7, 700.00, '2025-01-07 00:00:00'),
        (8, 800.00, '2025-01-08 00:00:00')
    """)
    con.close()

    # Subsequent incremental run with row_limit=1 → slice to 1 row
    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"
    assert result.rows_loaded == 1


def test_incremental_column_map_applies(project):
    """On the subsequent-incremental path, column_map renames the extracted
    columns unconditionally when set. (pipeline.py)"""
    src_path = _make_source_db(project.root)

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(src_path)}],
        destination={"path": str(project.data_db_path)},
        defaults={"overlap_window_minutes": 0},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.orders",
                "orders",
                strategy="incremental",
                timestamp_column="modified_at",
                primary_key=["order_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)
    # column_map isn't serialized by make_curation_entry — attach it here
    cfg.tables[0].column_map = {
        "order_id": "order_id",
        "amount": "order_amount",
        "modified_at": "modified_at",
    }
    # explicit target_table so data lands in silver with renamed columns
    cfg.tables[0].target_table = "silver.src_orders"

    run_table(cfg, cfg.tables[0], project.root)

    # Second run with a new row → hits the is_incremental+wm branch
    con = duckdb.connect(str(src_path))
    con.execute("INSERT INTO erp.orders VALUES (6, 600.00, '2025-01-06 00:00:00')")
    con.close()

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"
    assert result.rows_loaded >= 1

    # data lands in silver with renamed columns per column_map
    cols = [
        r[0]
        for r in project.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'silver' AND table_name = 'src_orders' "
            "ORDER BY ordinal_position"
        )
    ]
    assert "order_amount" in cols
    assert "amount" not in cols  # renamed


def test_append_row_limit_applies(project):
    """Append strategy + row_limit slices the loaded batch unconditionally.
    (pipeline.py)"""
    src_path = _make_source_db(project.root)

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(src_path)}],
        destination={"path": str(project.data_db_path)},
        defaults={"row_limit": 2},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.orders",
                "orders",
                strategy="append",
                primary_key=["order_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"
    assert result.rows_loaded == 2  # row_limit applied


def test_append_column_map_applies(project):
    """Append strategy + column_map renames columns unconditionally when set.
    (pipeline.py)"""
    src_path = _make_source_db(project.root)

    project.write_config(
        sources=[{"type": "duckdb", "name": "src", "path": str(src_path)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(
        project.root,
        [
            make_curation_entry(
                "src",
                "erp.orders",
                "orders",
                strategy="append",
                primary_key=["order_id"],
            ),
        ],
    )
    cfg = load_config(project.config_path)
    cfg.tables[0].column_map = {
        "order_id": "order_id",
        "amount": "order_amount",
    }
    # explicit target_table so data lands in silver with renamed columns
    cfg.tables[0].target_table = "silver.src_orders"

    result = run_table(cfg, cfg.tables[0], project.root)
    assert result.status == "success"
    assert result.rows_loaded == 5

    cols = [
        r[0]
        for r in project.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'silver' AND table_name = 'src_orders' "
            "ORDER BY ordinal_position"
        )
    ]
    assert "order_amount" in cols
    assert "amount" not in cols


def test_overlap_window_arithmetic(sample_erp_db, project):
    cfg = _write_incremental_config(
        project,
        sample_erp_db,
        source_table="erp.sales",
        alias="sales",
        primary_key=["sale_id"],
    )

    run_table(cfg, cfg.tables[0], project.root)

    sm = StateManager(project.state_db_path)
    wm = sm.read_watermark("src_sales")
    stored_wm = str(wm["last_value"])
    assert "2025-01-10" in stored_wm

    con = duckdb.connect(str(sample_erp_db))
    con.execute("""
        INSERT INTO erp.sales VALUES
        (11, 105, 1100.00, 'completed', '2025-01-10 00:05:00')
    """)
    con.close()

    r2 = run_table(cfg, cfg.tables[0], project.root)
    assert r2.status == "success"
    assert r2.rows_loaded == 2
