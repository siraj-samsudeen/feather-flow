"""End-to-end integration tests for the ``feather transform`` CLI command.

Covers Section 9 of the feather-transform OpenSpec change — the CLI seam.
Every test uses a real DuckDB destination, a real config on disk, and
invokes the command via Typer's ``CliRunner``. No mocking.

Two load-bearing scenarios anchor the suite:

* **No-source-touch** (`test_no_source_touch_invariant`) — proves the
  whole verb's purpose: ``feather transform`` MUST NOT open any source
  connection. The config uses ``RaisingSource``; every method raises.
  Bronze is pre-populated by writing rows directly into the destination
  DuckDB (bypassing the source). If the command opens any source, the
  test surfaces the exception.

* **Verb equivalence** (`test_verb_equivalence_extract_plus_transform_equals_run`)
  — proves ``feather extract && feather transform`` is substitutable for
  ``feather run``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import duckdb
import pytest
import yaml
from typer.testing import CliRunner

from feather_etl.cli import app
from feather_etl.state import StateManager
from tests.helpers import (
    RaisingSource,
    copy_transform_fixture,
    make_curation_entry,
    register_raising_source,
    write_curation,
)


# ---------------------------------------------------------------------------
# Helpers (local to this file — fixtures-vs-helpers split: pure functions
# go here; pytest fixtures yielding state go through conftest below).
# ---------------------------------------------------------------------------


def _write_csv_config(
    project_dir: Path,
) -> Path:
    """Write feather.yaml pointing at the project's CSV bronze fixture.

    The CSV directory must already exist under ``project_dir/csv_data`` —
    the caller is responsible (use ``copy_transform_fixture``).
    """
    config: dict = {
        "sources": [
            {
                "type": "csv",
                "name": "orders_src",
                "path": str(project_dir / "csv_data"),
            }
        ],
        "destination": {"path": str(project_dir / "feather_data.duckdb")},
    }
    cfg_path = project_dir / "feather.yaml"
    cfg_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        project_dir,
        [
            make_curation_entry(
                "orders_src",
                "orders.csv",
                "orders",
                primary_key=["order_id"],
            )
        ],
    )
    return cfg_path


def _populate_bronze_directly(dest_db: Path) -> None:
    """Create bronze schema + ``bronze.orders`` table directly in dest DuckDB.

    Bypasses any source connection — used by tests that need bronze present
    without invoking ``feather extract``. Schema matches the CSV fixture.
    """
    con = duckdb.connect(str(dest_db))
    for schema in ("bronze", "silver", "gold"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute(
        "CREATE TABLE bronze.orders_src_orders ("
        "order_id INT, customer_id INT, order_date DATE, amount DOUBLE)"
    )
    con.execute(
        "INSERT INTO bronze.orders_src_orders VALUES "
        "(1, 101, DATE '2026-01-01', 100.50),"
        "(2, 101, DATE '2026-01-02', 250.00),"
        "(3, 102, DATE '2026-01-03', 75.25),"
        "(4, 103, DATE '2026-01-04', 1200.00),"
        "(5, 102, DATE '2026-01-05', 420.99)"
    )
    con.close()


def _table_types(dest_db: Path) -> dict[tuple[str, str], str]:
    """Map (schema, name) -> table_type for silver/gold objects in dest."""
    con = duckdb.connect(str(dest_db), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_schema, table_name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema IN ('silver', 'gold')"
        ).fetchall()
    finally:
        con.close()
    return {(r[0], r[1]): r[2] for r in rows}


def _invoke(*args: str) -> "object":
    """Run ``feather <args>`` via CliRunner. Returns the click Result."""
    runner = CliRunner()
    return runner.invoke(app, list(args))



# ---------------------------------------------------------------------------
# Pytest fixtures (lifecycle state only — pure helpers stay in helpers.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def transform_command_fixture_dir() -> Path:
    """Path to the bundled transform-command fixture tree (read-only)."""
    from tests.helpers import TRANSFORM_FIXTURE_DIR

    return TRANSFORM_FIXTURE_DIR


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A tmp project directory pre-loaded with the transform fixture tree.

    Contains ``transforms/`` and ``csv_data/`` but no config or bronze
    yet — tests add what they need.
    """
    copy_transform_fixture(tmp_path)
    return tmp_path


@pytest.fixture
def populated_destination(project_dir: Path) -> Path:
    """A project_dir with a written config AND a populated bronze.orders.

    Bronze is seeded directly (CREATE TABLE + INSERT) to keep this fixture
    decoupled from ``feather extract``. Tests that specifically want the
    extract path call it themselves.

    Returns the project root path (cfg_path = populated_destination / "feather.yaml").
    """
    _write_csv_config(project_dir)
    _populate_bronze_directly(project_dir / "feather_data.duckdb")
    return project_dir


# ---------------------------------------------------------------------------
# 9.3 — No-source-touch invariant (LOAD-BEARING)
# ---------------------------------------------------------------------------


def test_no_source_touch_invariant(project_dir: Path):
    """``feather transform`` MUST NOT open any source connection.

    Config declares ``RaisingSource``; bronze is seeded directly into the
    destination. The command should complete successfully and the source's
    connect/check/discover/extract/detect_changes/get_schema methods must
    all stay un-called.
    """
    register_raising_source()
    RaisingSource.reset()

    # Touch a dummy source path so config-loading doesn't trip on missing files.
    dummy_path = project_dir / "dummy_source"
    dummy_path.mkdir(exist_ok=True)

    config = {
        "sources": [
            {
                "type": "raising",
                "name": "raising_src",
                "path": str(dummy_path),
            }
        ],
        "destination": {"path": str(project_dir / "feather_data.duckdb")},
    }
    cfg_path = project_dir / "feather.yaml"
    cfg_path.write_text(yaml.dump(config))
    write_curation(
        project_dir,
        [make_curation_entry("raising_src", "orders", "orders")],
    )

    _populate_bronze_directly(project_dir / "feather_data.duckdb")

    result = _invoke("transform", "--config", str(cfg_path))

    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.output!r}"
    assert RaisingSource.connect_called is False, (
        f"RaisingSource was touched: {RaisingSource.called_methods}"
    )
    assert RaisingSource.called_methods == []


# ---------------------------------------------------------------------------
# 9.4 — Happy path
# ---------------------------------------------------------------------------


def test_happy_path_creates_transforms_and_records_runs(populated_destination: Path):
    """All three transforms execute; _runs gets one row per transform with
    trigger='transform'."""
    cfg_path = populated_destination / "feather.yaml"
    result = _invoke("transform", "--config", str(cfg_path))

    assert result.exit_code == 0, result.output

    # Destination has all three transforms.
    types = _table_types(populated_destination / "feather_data.duckdb")
    assert ("silver", "silver_orders") in types
    assert ("gold", "gold_summary") in types
    assert ("gold", "gold_facts") in types

    # _runs has trigger='transform' rows.
    state_path = populated_destination / "feather_state.duckdb"
    sm = StateManager(state_path)
    rows = sm.get_history(trigger="transform", limit=100)
    assert len(rows) == 3
    names = sorted(r["table_name"] for r in rows)
    assert names == ["gold.gold_facts", "gold.gold_summary", "silver.silver_orders"]
    for r in rows:
        assert r["trigger"] == "transform"
        assert r["status"] == "success"


# ---------------------------------------------------------------------------
# 9.5 — Zero transforms
# ---------------------------------------------------------------------------


def test_zero_transforms_exits_zero_and_prints_summary(project_dir: Path):
    """Empty transforms directory → exit 0, ``0 transforms`` in stdout."""
    # Remove the transforms tree so discover returns [].
    shutil.rmtree(project_dir / "transforms")
    _write_csv_config(project_dir)
    _populate_bronze_directly(project_dir / "feather_data.duckdb")

    result = _invoke("transform", "--config", str(project_dir / "feather.yaml"))
    assert result.exit_code == 0, result.output
    assert "0 transforms" in result.stdout


# ---------------------------------------------------------------------------
# 9.6 — DDL kind control (force_views vs default)
# ---------------------------------------------------------------------------


def test_transform_default_materializes_gold(populated_destination: Path):
    """Default: silver VIEW, unmarked gold VIEW, materialized gold TABLE."""
    cfg_path = populated_destination / "feather.yaml"
    result = _invoke("transform", "--config", str(cfg_path))
    assert result.exit_code == 0, result.output

    types = _table_types(populated_destination / "feather_data.duckdb")
    assert types[("silver", "silver_orders")] == "VIEW"
    assert types[("gold", "gold_summary")] == "VIEW"
    assert types[("gold", "gold_facts")] == "BASE TABLE"


def test_transform_force_views_all_views(populated_destination: Path):
    """--force-views: all transforms are VIEWs including materialized gold."""
    cfg_path = populated_destination / "feather.yaml"
    result = _invoke("transform", "--config", str(cfg_path), "--force-views")
    assert result.exit_code == 0, result.output

    types = _table_types(populated_destination / "feather_data.duckdb")
    assert types[("silver", "silver_orders")] == "VIEW"
    assert types[("gold", "gold_summary")] == "VIEW"
    assert types[("gold", "gold_facts")] == "VIEW"


# ---------------------------------------------------------------------------
# 9.8 — Advisory warning (single missing)
# ---------------------------------------------------------------------------


def test_advisory_warning_single_missing_bronze(populated_destination: Path):
    """Silver SQL body's ``FROM bronze.orders_src_orders`` clause is what the
    bronze advisory check reads; if the table is dropped, command warns on
    stderr. Per spec: still attempts execution; we pin the warning, not the
    exit code (the transform will fail downstream)."""
    dest_db = populated_destination / "feather_data.duckdb"
    con = duckdb.connect(str(dest_db))
    con.execute("DROP TABLE bronze.orders_src_orders")
    con.close()

    result = _invoke(
        "transform", "--config", str(populated_destination / "feather.yaml")
    )
    combined = result.output  # mix_stderr default
    assert "WARNING" in combined
    assert "bronze.orders_src_orders" in combined


# ---------------------------------------------------------------------------
# 9.9 — Zero warnings when clean
# ---------------------------------------------------------------------------


def test_zero_warnings_when_bronze_present(populated_destination: Path):
    """When every declared bronze dep exists, no WARNING lines appear."""
    result = _invoke(
        "transform", "--config", str(populated_destination / "feather.yaml")
    )
    assert result.exit_code == 0, result.output
    # No bronze-warning lines. (Other unrelated WARNINGs from logging might
    # appear but we only forbid the specific bronze-missing prefix.)
    assert "bronze dependency missing" not in result.output
    assert "bronze dependencies missing" not in result.output


# ---------------------------------------------------------------------------
# 9.10 — Collapse rule (> 5 missing)
# ---------------------------------------------------------------------------


def test_collapse_rule_more_than_five_missing(project_dir: Path):
    """Six+ missing bronze deps → one summary WARNING line stating the count."""
    # Replace the silver dir with six silver transforms each depending on
    # a unique bronze table that does NOT exist.
    silver_dir = project_dir / "transforms" / "silver"
    shutil.rmtree(silver_dir)
    silver_dir.mkdir(parents=True)
    for i in range(6):
        (silver_dir / f"silver_{i}.sql").write_text(
            f"SELECT 1 AS x FROM bronze.missing_{i}\n"
        )
    # Remove gold transforms so only the missing-bronze warnings are in play.
    shutil.rmtree(project_dir / "transforms" / "gold")

    _write_csv_config(project_dir)
    # Create an empty dest with the bronze schema so the connection works
    # but bronze.missing_N tables don't exist.
    dest_db = project_dir / "feather_data.duckdb"
    con = duckdb.connect(str(dest_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.close()

    result = _invoke("transform", "--config", str(project_dir / "feather.yaml"))

    # Count WARNING lines mentioning bronze deps.
    bronze_warning_lines = [
        line
        for line in result.output.splitlines()
        if "WARNING" in line and "bronze" in line
    ]
    assert len(bronze_warning_lines) == 1, (
        f"expected exactly one summary line, got {len(bronze_warning_lines)}: "
        f"{bronze_warning_lines}"
    )
    # And the line states the count (6).
    assert "6" in bronze_warning_lines[0]


# ---------------------------------------------------------------------------
# 9.11 — Exit-code contract
# ---------------------------------------------------------------------------


def test_bad_sql_returns_exit_1(populated_destination: Path):
    """A transform with broken SQL → exit 1 with the failure in the breakdown."""
    # Inject a deliberately-broken gold transform.
    bad = populated_destination / "transforms" / "gold" / "gold_broken.sql"
    bad.write_text("SELECT this_column_does_not_exist FROM bronze.orders_src_orders\n")

    result = _invoke(
        "transform", "--config", str(populated_destination / "feather.yaml")
    )
    assert result.exit_code == 1
    # Per-transform line shows the failure.
    assert "gold_broken" in result.output
    assert "failure" in result.output


def test_missing_config_returns_exit_2(tmp_path: Path):
    """--config nonexistent.yaml → exit 2, no transform executed."""
    missing = tmp_path / "nonexistent.yaml"
    result = _invoke("transform", "--config", str(missing))
    assert result.exit_code == 2


def test_unexpected_config_load_error_returns_exit_2(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """A non-typer.Exit exception from config loading → exit 2 with stderr msg.

    Exercises the broad-except fallback path in the command (lines that
    aren't otherwise reachable through normal misconfiguration, which
    raises typer.Exit via ``_load_and_validate``).
    """
    _write_csv_config(project_dir)

    def _boom(*_a, **_kw):
        raise RuntimeError("synthetic config-load failure")

    monkeypatch.setattr("feather_etl.commands.transform._load_and_validate", _boom)

    result = _invoke("transform", "--config", str(project_dir / "feather.yaml"))
    assert result.exit_code == 2
    assert "Failed to load config" in result.output
    assert "synthetic config-load failure" in result.output


def test_unreachable_destination_returns_exit_2(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the destination DuckDB cannot be opened → exit 2.

    Simulate by patching ``DuckDBDestination._connect`` to raise.
    """
    _write_csv_config(project_dir)
    # Provide bronze so we'd otherwise succeed.
    _populate_bronze_directly(project_dir / "feather_data.duckdb")

    from feather_etl.destinations.duckdb import DuckDBDestination

    def _boom(self):
        raise RuntimeError("synthetic destination-open failure")

    monkeypatch.setattr(DuckDBDestination, "_connect", _boom)

    result = _invoke("transform", "--config", str(project_dir / "feather.yaml"))
    assert result.exit_code == 2
    assert "Failed to open destination" in result.output


# ---------------------------------------------------------------------------
# 9.12 — Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_two_consecutive_runs(populated_destination: Path):
    """Running ``feather transform`` twice yields identical schema + row counts."""
    cfg_path = populated_destination / "feather.yaml"
    dest_db = populated_destination / "feather_data.duckdb"

    r1 = _invoke("transform", "--config", str(cfg_path))
    assert r1.exit_code == 0, r1.output
    types_1 = _table_types(dest_db)
    counts_1 = _row_counts(dest_db, types_1.keys())

    r2 = _invoke("transform", "--config", str(cfg_path))
    assert r2.exit_code == 0, r2.output
    types_2 = _table_types(dest_db)
    counts_2 = _row_counts(dest_db, types_2.keys())

    assert types_1 == types_2
    assert counts_1 == counts_2
    # No "table already exists" error surface.
    assert "already exists" not in r2.output


def _row_counts(dest_db: Path, objs) -> dict[tuple[str, str], int]:
    """Row count per (schema, name) object in the destination."""
    con = duckdb.connect(str(dest_db), read_only=True)
    try:
        return {
            (s, n): con.execute(f'SELECT COUNT(*) FROM "{s}"."{n}"').fetchone()[0]
            for (s, n) in objs
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 9.13 — History filter
# ---------------------------------------------------------------------------


def test_history_trigger_filter_after_mixed_invocations(project_dir: Path):
    """After ``feather run``, ``feather extract``, ``feather transform``,
    each ``feather history --trigger <verb>`` returns only that verb's rows."""
    _write_csv_config(project_dir)
    cfg_path = project_dir / "feather.yaml"

    # extract → writes trigger='extract' rows.
    r_extract = _invoke("extract", "--config", str(cfg_path))
    assert r_extract.exit_code == 0, r_extract.output

    # transform → writes trigger='transform' rows.
    r_transform = _invoke("transform", "--config", str(cfg_path))
    assert r_transform.exit_code == 0, r_transform.output

    # run → writes trigger='run' rows. Bronze re-extract is fine.
    r_run = _invoke("run", "--config", str(cfg_path))
    assert r_run.exit_code == 0, r_run.output

    state_path = project_dir / "feather_state.duckdb"
    sm = StateManager(state_path)

    transform_rows = sm.get_history(trigger="transform", limit=100)
    extract_rows = sm.get_history(trigger="extract", limit=100)
    run_rows = sm.get_history(trigger="run", limit=100)

    assert transform_rows, "expected at least one transform row"
    assert extract_rows, "expected at least one extract row"
    assert run_rows, "expected at least one run row"

    assert all(r["trigger"] == "transform" for r in transform_rows)
    assert all(r["trigger"] == "extract" for r in extract_rows)
    assert all(r["trigger"] == "run" for r in run_rows)


# ---------------------------------------------------------------------------
# 9.14 — Summary output format
# ---------------------------------------------------------------------------


def test_summary_output_format(populated_destination: Path):
    """Stdout shape:
    * one per-transform line ``<schema>.<name>  <view|table>  <status>``
    * trailing summary ``<N> transforms: <K> succeeded.``
    """
    result = _invoke(
        "transform",
        "--config",
        str(populated_destination / "feather.yaml"),
    )
    assert result.exit_code == 0, result.output

    lines = [line for line in result.stdout.splitlines() if line.strip()]

    # Per-transform lines.
    assert any("silver.silver_orders" in line and "success" in line for line in lines)
    assert any("gold.gold_summary" in line and "success" in line for line in lines)
    assert any("gold.gold_facts" in line and "success" in line for line in lines)

    # Trailing summary line.
    last = lines[-1]
    assert re.match(r"^\d+ transforms?: \d+ succeeded\.$", last), (
        f"summary line: {last!r}"
    )


# ---------------------------------------------------------------------------
# 9.15 — Verb-equivalence (LOAD-BEARING)
# ---------------------------------------------------------------------------


def _bronze_silver_gold_objects(dest_db: Path) -> dict[tuple[str, str], str]:
    """(schema, name) -> table_type for bronze/silver/gold (no internal schemas)."""
    con = duckdb.connect(str(dest_db), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_schema, table_name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema IN ('bronze', 'silver', 'gold')"
        ).fetchall()
    finally:
        con.close()
    return {(r[0], r[1]): r[2] for r in rows}


def _content_signature(
    dest_db: Path, objs: list[tuple[str, str]]
) -> dict[tuple[str, str], list[tuple]]:
    """Return sorted rows per object, excluding wall-clock audit columns."""
    audit_cols = {"_etl_loaded_at", "_etl_run_id"}
    out: dict[tuple[str, str], list[tuple]] = {}
    con = duckdb.connect(str(dest_db), read_only=True)
    try:
        for s, n in objs:
            cols = con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                [s, n],
            ).fetchall()
            keep = [c[0] for c in cols if c[0] not in audit_cols]
            if not keep:
                out[(s, n)] = []
                continue
            col_sql = ", ".join(f'"{c}"' for c in keep)
            rows = con.execute(f'SELECT {col_sql} FROM "{s}"."{n}"').fetchall()
            # Sort by repr — works for any value type, deterministic.
            out[(s, n)] = sorted(rows, key=repr)
    finally:
        con.close()
    return out


def _watermarked_tables(state_db: Path) -> set[str]:
    """Return all table names recorded in _watermarks."""
    if not state_db.exists():
        return set()
    con = duckdb.connect(str(state_db), read_only=True)
    try:
        rows = con.execute("SELECT DISTINCT table_name FROM _watermarks").fetchall()
        return {r[0] for r in rows}
    except duckdb.CatalogException:
        return set()
    finally:
        con.close()


def test_verb_equivalence_extract_plus_transform_equals_run(tmp_path: Path):
    """Path A (``feather run``) and Path B (``feather extract`` then
    ``feather transform``) produce equivalent destination state.

    Both paths write to bronze.* by default. feather run then runs transforms;
    feather extract + transform does the same two-step sequence explicitly.
    """
    # Set up two independent project dirs.
    dir_a = tmp_path / "path_a"
    dir_b = tmp_path / "path_b"
    dir_a.mkdir()
    dir_b.mkdir()
    copy_transform_fixture(dir_a)
    copy_transform_fixture(dir_b)
    cfg_a = _write_csv_config(dir_a)
    cfg_b = _write_csv_config(dir_b)

    # Path A: feather run.
    r_a = _invoke("run", "--config", str(cfg_a))
    assert r_a.exit_code == 0, f"feather run failed: {r_a.output}"

    r_b_extract = _invoke("extract", "--config", str(cfg_b))
    assert r_b_extract.exit_code == 0, (
        f"feather extract failed: {r_b_extract.output}"
    )

    r_b_transform = _invoke("transform", "--config", str(cfg_b))
    assert r_b_transform.exit_code == 0, (
        f"feather transform failed: {r_b_transform.output}"
    )

    dest_a = dir_a / "feather_data.duckdb"
    dest_b = dir_b / "feather_data.duckdb"

    # 1. Same (schema, name, table_type) tuples.
    objs_a = _bronze_silver_gold_objects(dest_a)
    objs_b = _bronze_silver_gold_objects(dest_b)
    assert objs_a == objs_b, (
        f"object set diverged.\n"
        f"  only in A: {set(objs_a) - set(objs_b)}\n"
        f"  only in B: {set(objs_b) - set(objs_a)}\n"
        f"  type mismatches: "
        f"{ {k: (objs_a.get(k), objs_b.get(k)) for k in set(objs_a) & set(objs_b) if objs_a[k] != objs_b[k]} }"
    )

    # 2. Same row counts per object.
    counts_a = _row_counts(dest_a, objs_a.keys())
    counts_b = _row_counts(dest_b, objs_b.keys())
    for key in objs_a:
        assert counts_a[key] == counts_b[key], (
            f"row count diverged for {key}: "
            f"A={counts_a[key]}, B={counts_b[key]}"
        )

    # 3. Same content per object (excluding wall-clock audit columns).
    sig_a = _content_signature(dest_a, list(objs_a.keys()))
    sig_b = _content_signature(dest_b, list(objs_b.keys()))
    for key in objs_a:
        assert sig_a[key] == sig_b[key], (
            f"content diverged for {key}.\n"
            f"  A first 3 rows: {sig_a[key][:3]}\n"
            f"  B first 3 rows: {sig_b[key][:3]}"
        )

    # 4. Watermarked-table set matches.
    wms_a = _watermarked_tables(dir_a / "feather_state.duckdb")
    wms_b = _watermarked_tables(dir_b / "feather_state.duckdb")
    assert wms_a == wms_b, (
        f"watermarked-table set diverged.\n  A={wms_a}\n  B={wms_b}"
    )

    # 5. _runs differs only by trigger.
    con_a = duckdb.connect(str(dir_a / "feather_state.duckdb"), read_only=True)
    con_b = duckdb.connect(str(dir_b / "feather_state.duckdb"), read_only=True)
    try:
        triggers_a = {
            row[0]
            for row in con_a.execute(
                "SELECT DISTINCT trigger FROM _runs WHERE trigger IS NOT NULL"
            ).fetchall()
        }
        triggers_b = {
            row[0]
            for row in con_b.execute(
                "SELECT DISTINCT trigger FROM _runs WHERE trigger IS NOT NULL"
            ).fetchall()
        }
    finally:
        con_a.close()
        con_b.close()
    assert triggers_a == {"run"}, f"path A triggers = {triggers_a}"
    assert "extract" in triggers_b and "transform" in triggers_b, (
        f"path B triggers = {triggers_b}"
    )
