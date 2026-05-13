# Drop Mode System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `dev/prod/test` mode system from feather-etl and replace the four concerns it bundled with explicit, independent controls.

**Architecture:** Four mode-coupled concerns become four honest controls: target schema defaults to `bronze.*` (explicit `target_table:` still overrides), `column_map:` always applies when set, `defaults.row_limit` always applies when set, and a `--force-views` CLI flag controls DDL kind. The `_cache_watermarks` state table is unified into `_watermarks` (schema version bump 1→2 with one-time migration).

**Tech Stack:** Python, DuckDB, Typer, pytest

**Design doc:** `docs/superpowers/specs/2026-05-13-drop-mode-system-design.md`

---

## File Map

| File | Change |
|---|---|
| `src/feather_etl/state.py` | Add `source_db` to `_watermarks`; bump SCHEMA_VERSION to 2; v1→v2 migration; redirect `read/write_cache_watermark` to unified table; then remove those methods |
| `src/feather_etl/config.py` | Remove `VALID_MODES`, `mode` field, mode resolution, `mode_override` param |
| `src/feather_etl/commands/_common.py` | Remove `mode_override` param from `_load_and_validate` |
| `src/feather_etl/pipeline.py` | Simplify `_resolve_target`; remove three mode checks; add `force_views` to `run_all` |
| `src/feather_etl/transforms.py` | Add `force_views: bool = False` to `run_transforms`; remove `config.mode` branch |
| `src/feather_etl/setup.py` | Remove `cfg.mode` branch; call `run_transforms(cfg, force_views=force_views)` |
| `src/feather_etl/extract.py` | Replace `read_cache_watermark`/`write_cache_watermark` with unified calls |
| `src/feather_etl/commands/run.py` | Drop `--mode`; add `--force-views`, `--limit` |
| `src/feather_etl/commands/transform.py` | Drop `--mode`; add `--force-views` |
| `src/feather_etl/commands/setup.py` | Drop `--mode`; add `--force-views` |
| `src/feather_etl/commands/extract.py` | Drop prod-mode guard; remove "dev-only" label; add `--limit` |
| `src/feather_etl/commands/validate.py` | Drop `"mode": cfg.mode` from JSON output |
| `tests/unit/test_mode.py` | **DELETE** — tests the removed feature |
| `tests/integration/test_mode.py` | **REWRITE** — tests new behaviors without mode |
| `tests/integration/test_run_transforms.py` | Update `_write_config` and tests to use `force_views` |
| `tests/integration/test_incremental.py` | Update 4 tests: remove mode param, use `row_limit`/`column_map` directly |
| `tests/integration/test_transform_command.py` | Delete mode-precedence tests; update parametrized DDL test; update `_write_csv_config` |
| `tests/e2e/test_07_transforms.py` | Update 2 setup tests |
| `tests/e2e/test_12_extract.py` | Delete prod-mode guard tests |

---

## Task 1: Unify watermarks in state.py

**Files:**
- Modify: `src/feather_etl/state.py`
- Modify: `tests/unit/test_state.py`

This task is self-contained. After it, the suite must pass.

- [ ] **Step 1.1: Add `source_db` to `_watermarks` DDL and bump schema version**

In `src/feather_etl/state.py`, change the top-level constant and the `_watermarks` CREATE TABLE:

```python
SCHEMA_VERSION = 2  # was 1
```

In `_watermarks` CREATE TABLE, add `source_db VARCHAR` after `boundary_hashes JSON`:

```sql
CREATE TABLE IF NOT EXISTS _watermarks (
    table_name VARCHAR PRIMARY KEY,
    strategy VARCHAR,
    last_value VARCHAR,
    last_checksum VARCHAR,
    last_row_count INTEGER,
    last_file_mtime DOUBLE,
    last_file_hash VARCHAR,
    last_run_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    retry_after TIMESTAMP,
    boundary_hashes JSON,
    source_db VARCHAR
)
```

- [ ] **Step 1.2: Add the v1→v2 migration function**

Add this function just above `init_state` in `state.py`:

```python
def _migrate_v1_to_v2(self, con: duckdb.DuckDBPyConnection) -> None:
    """One-time migration: add source_db to _watermarks, copy _cache_watermarks rows."""
    con.execute("ALTER TABLE _watermarks ADD COLUMN IF NOT EXISTS source_db VARCHAR")
    # Copy rows from _cache_watermarks if that table still exists.
    tables = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'"
        ).fetchall()
    }
    if "_cache_watermarks" in tables:
        con.execute("""
            INSERT INTO _watermarks
                (table_name, source_db, last_file_mtime, last_file_hash,
                 last_checksum, last_row_count, last_run_at)
            SELECT
                table_name, source_db, last_file_mtime, last_file_hash,
                last_checksum, last_row_count, last_run_at
            FROM _cache_watermarks
            WHERE table_name NOT IN (SELECT table_name FROM _watermarks)
        """)
        con.execute("DROP TABLE _cache_watermarks")
```

- [ ] **Step 1.3: Call the migration from `init_state`**

In `init_state`, replace the version check block:

```python
row = con.execute(
    "SELECT schema_version FROM _state_meta LIMIT 1"
).fetchone()
if row is None:
    con.execute(
        "INSERT INTO _state_meta VALUES (?, ?, ?)",
        [
            SCHEMA_VERSION,
            datetime.now(timezone.utc),
            feather_etl.__version__,
        ],
    )
elif row[0] < SCHEMA_VERSION:
    if row[0] == 1:
        self._migrate_v1_to_v2(con)
    con.execute(
        "UPDATE _state_meta SET schema_version = ?", [SCHEMA_VERSION]
    )
elif row[0] > SCHEMA_VERSION:
    raise RuntimeError(
        f"State DB schema version {row[0]} is newer than feather-etl "
        f"version {SCHEMA_VERSION}. Upgrade feather-etl."
    )
```

- [ ] **Step 1.4: Add `source_db` param to `write_watermark`**

In `write_watermark`, add `source_db: str | None = None` to the signature and include it in both the UPDATE and INSERT statements. Find the UPDATE block (around line 270) and add `source_db = ?` and the value. Find the INSERT block and add `source_db` column and `source_db` value.

The full updated signature:
```python
def write_watermark(
    self,
    table_name: str,
    strategy: str | None,
    last_run_at: datetime,
    last_value: str | None = _SENTINEL,
    last_file_mtime: float | None = None,
    last_file_hash: str | None = None,
    last_checksum: str | None = None,
    last_row_count: int | None = None,
    source_db: str | None = None,
) -> None:
```

In the UPDATE statement add `, source_db = ?` and `source_db` to the params list.
In the INSERT statement add `source_db` to the columns list and `source_db` to the values list.

- [ ] **Step 1.5: Redirect `write_cache_watermark` to `write_watermark`**

Replace the body of `write_cache_watermark` (keep the signature unchanged for now):

```python
def write_cache_watermark(
    self,
    table_name: str,
    source_db: str,
    last_run_at: datetime,
    last_file_mtime: float | None = None,
    last_file_hash: str | None = None,
    last_checksum: str | None = None,
    last_row_count: int | None = None,
) -> None:
    """Deprecated — delegates to write_watermark with source_db."""
    self.write_watermark(
        table_name=table_name,
        strategy=None,
        last_run_at=last_run_at,
        last_file_mtime=last_file_mtime,
        last_file_hash=last_file_hash,
        last_checksum=last_checksum,
        last_row_count=last_row_count,
        source_db=source_db,
    )
```

- [ ] **Step 1.6: Redirect `read_cache_watermark` to `read_watermark`**

Replace the body of `read_cache_watermark` (keep the signature):

```python
def read_cache_watermark(self, table_name: str) -> dict[str, object] | None:
    """Deprecated — delegates to read_watermark."""
    return self.read_watermark(table_name)
```

- [ ] **Step 1.7: Update state tests**

In `tests/unit/test_state.py`:

Replace `test_cache_watermarks_table_schema` (which checks `_cache_watermarks` columns) with a test that verifies `_watermarks` has a `source_db` column:

```python
def test_watermarks_has_source_db_column(self, tmp_path: Path):
    """_watermarks includes source_db for extract-written rows."""
    sm = StateManager(tmp_path / "state.duckdb")
    sm.init_state()
    con = sm._connect()
    cols = {
        row[0]
        for row in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = '_watermarks' ORDER BY ordinal_position"
        ).fetchall()
    }
    con.close()
    assert "source_db" in cols
```

Update `test_write_cache_watermark_does_not_touch_watermarks` — this test verified the two tables were separate; after the redirect they're unified. Replace it with:

```python
def test_write_cache_watermark_writes_to_watermarks(self, tmp_path: Path):
    """write_cache_watermark delegates to write_watermark (unified table)."""
    sm = StateManager(tmp_path / "state.duckdb")
    sm.init_state()
    sm.write_cache_watermark(
        "orders", source_db="erp", last_run_at=datetime.now(timezone.utc)
    )
    row = sm.read_watermark("orders")
    assert row is not None
    assert row["source_db"] == "erp"
```

Leave the other `test_write_cache_watermark_*` and `test_read_cache_watermark_*` tests in place — they still call the deprecated API which now delegates through. They should pass.

- [ ] **Step 1.8: Run the suite**

```bash
uv run pytest tests/unit/test_state.py -v
uv run pytest -q
```

Expected: all pass. Fix anything red before continuing.

- [ ] **Step 1.9: Commit**

```bash
git add src/feather_etl/state.py tests/unit/test_state.py
git commit -m "refactor(state): unify _cache_watermarks into _watermarks (schema v2)"
```

---

## Task 2: Remove mode system — source code

**Files:**
- Modify: `src/feather_etl/config.py`
- Modify: `src/feather_etl/commands/_common.py`
- Modify: `src/feather_etl/pipeline.py`
- Modify: `src/feather_etl/transforms.py`
- Modify: `src/feather_etl/setup.py`
- Modify: `src/feather_etl/extract.py`
- Modify: `src/feather_etl/commands/run.py`
- Modify: `src/feather_etl/commands/transform.py`
- Modify: `src/feather_etl/commands/setup.py`
- Modify: `src/feather_etl/commands/extract.py`
- Modify: `src/feather_etl/commands/validate.py`

After this task the suite will be **partially broken** — existing mode-specific tests will fail. Task 3 fixes that. Do NOT commit until after Task 3.

- [ ] **Step 2.1: Remove mode from `config.py`**

Delete the constant:
```python
# DELETE this line:
VALID_MODES = {"dev", "prod", "test"}
```

Remove `mode: str = "dev"` from `FeatherConfig` dataclass.

Remove `mode_override: str | None = None` from `load_config`'s signature.

Remove the entire mode resolution block from `load_config` (the `yaml_mode`/`env_mode`/`mode`/`mode_source` block and the validation check). Replace it with a deprecation warning when `mode:` key is present in raw YAML:

```python
if "mode" in raw:
    import warnings
    warnings.warn(
        "'mode:' key in feather.yaml is ignored and will be removed. "
        "Remove it from your config.",
        UserWarning,
        stacklevel=2,
    )
```

Remove `mode=mode` from the `FeatherConfig(...)` constructor call at the bottom of `load_config`.

- [ ] **Step 2.2: Remove `mode_override` from `_common.py`**

In `src/feather_etl/commands/_common.py`, change `_load_and_validate`:

```python
def _load_and_validate(
    config_path: Path,
    discover_mode: bool = False,
):
    ...
    cfg = load_config(
        config_path,
        validate=not discover_mode,
    )
```

- [ ] **Step 2.3: Simplify `_resolve_target` and remove mode checks in `pipeline.py`**

Replace `_resolve_target`:
```python
def _resolve_target(table: TableConfig) -> str:
    """Return explicit target_table or default to bronze.<name>."""
    return table.target_table or f"bronze.{table.name}"
```

Update every call from `_resolve_target(table, config.mode)` to `_resolve_target(table)`.

Remove `prod_columns` computation and replace with:
```python
prod_columns = list(table.column_map.keys()) if table.column_map else None
```

In all three extraction paths (incremental subsequent, append, full/first-incremental), change:
```python
# DELETE:
if config.mode == "test" and config.defaults.row_limit:
    data = data.slice(0, config.defaults.row_limit)
if config.mode == "prod" and table.column_map:
    data = _apply_column_map(data, table.column_map)

# REPLACE WITH:
if config.defaults.row_limit:
    data = data.slice(0, config.defaults.row_limit)
if table.column_map:
    data = _apply_column_map(data, table.column_map)
```

Add `force_views: bool = False` to `run_all`'s signature. Pass it to `run_transforms`:
```python
run_transforms(config, force_views=force_views)
```

Remove the comment `# Post-extraction transforms (mode-dependent)` → replace with `# Post-extraction transforms`.

- [ ] **Step 2.4: Add `force_views` to `transforms.py`**

Change `run_transforms` signature:
```python
def run_transforms(config, force_views: bool = False) -> list[TransformResult]:
```

Replace the `if config.mode == "prod":` branch:
```python
if force_views:
    exec_results = execute_transforms(con, ordered, force_views=True)
    results.extend(exec_results)
else:
    exec_results = execute_transforms(con, ordered)
    results.extend(exec_results)
    rebuild_results = rebuild_materialized_gold(con, ordered)
    count = sum(1 for r in rebuild_results if r.status == "success")
    if count:
        logger.info("Rebuilt %d materialized gold table(s)", count)
    results.extend(rebuild_results)
```

Update the docstring: remove "prod honors ``-- materialized: true``; dev/test forces all to views" and replace with "When ``force_views=True``, all transforms are created as VIEWs; otherwise gold transforms marked ``-- materialized: true`` are rebuilt as TABLEs."

- [ ] **Step 2.5: Update `setup.py`**

Change `run_setup` signature:
```python
def run_setup(cfg: FeatherConfig, force_views: bool = False) -> SetupResult:
```

Replace the inner mode-dependent transform block:
```python
if transforms:
    from feather_etl.transforms import run_transforms
    transform_results = run_transforms(cfg, force_views=force_views)
```

Remove the imports of `build_execution_order`, `execute_transforms` (they're no longer needed directly in `setup.py`; `run_transforms` handles them). Keep `discover_transforms` only if still used — it's now inside `run_transforms`, so remove it too. The full `if transforms:` block becomes just the `run_transforms` call. Update the `from feather_etl.transforms import (...)` import accordingly.

- [ ] **Step 2.6: Update `extract.py` module**

In `src/feather_etl/extract.py`, replace:
```python
wm = state.read_cache_watermark(table.name)
```
with:
```python
wm = state.read_watermark(table.name)
```

Replace the `state.write_cache_watermark(...)` call with `state.write_watermark(...)`:
```python
state.write_watermark(
    table.name,
    strategy=None,
    last_run_at=last_run_at,
    last_file_mtime=change.metadata.get("file_mtime") if change.metadata else None,
    last_file_hash=change.metadata.get("file_hash") if change.metadata else None,
    last_checksum=change.metadata.get("checksum") if change.metadata else None,
    last_row_count=change.metadata.get("row_count") if change.metadata else None,
    source_db=table.database or table.source_table,
)
```

(Check the existing `write_cache_watermark` call to confirm the `source_db` value being passed — match it exactly.)

- [ ] **Step 2.7: Update `commands/run.py`**

```python
def run(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    force_views: bool = typer.Option(
        False, "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
    limit: int | None = typer.Option(
        None, "--limit",
        help="Override defaults.row_limit for this invocation.",
    ),
    table: str | None = typer.Option(None, "--table", help="Extract only this table."),
) -> None:
    """Extract all configured tables (or a single table with --table)."""
    from feather_etl.pipeline import run_all

    cfg = _load_and_validate(config)
    if limit is not None:
        cfg.defaults.row_limit = limit
    try:
        results = run_all(cfg, config, table_filter=table, force_views=force_views)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    # ... rest of output unchanged, but remove the `typer.echo(f"Mode: {cfg.mode}")` line
```

- [ ] **Step 2.8: Update `commands/transform.py`**

Remove the `mode` option from the `transform` function signature. Add `force_views`:
```python
def transform(
    config: Path = typer.Option("feather.yaml", "--config"),
    force_views: bool = typer.Option(
        False, "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
) -> None:
```

Change `cfg = _load_and_validate(config, mode_override=mode)` to `cfg = _load_and_validate(config)`.

Change `results = run_transforms(cfg)` to `results = run_transforms(cfg, force_views=force_views)`.

Remove both `typer.echo(f"Mode: {cfg.mode}")` lines (there are two — one in the zero-transforms early return and one in the summary block).

Update the module docstring to remove the mode-specific explanation.

- [ ] **Step 2.9: Update `commands/setup.py`**

Remove the `mode` option. Add `force_views`:
```python
def setup(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    force_views: bool = typer.Option(
        False, "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
) -> None:
```

Change `cfg = _load_and_validate(config, mode_override=mode)` to `cfg = _load_and_validate(config)`.

Change `result = run_setup(cfg)` to `result = run_setup(cfg, force_views=force_views)`.

Remove `typer.echo(f"Mode: {cfg.mode}")`.

- [ ] **Step 2.10: Update `commands/extract.py`**

Delete the prod-mode guard block (lines 51–57):
```python
# DELETE:
if cfg.mode == "prod":
    typer.echo(
        "feather extract is a dev-only tool. "
        "Remove 'mode: prod' or unset FEATHER_MODE=prod to use it.",
        err=True,
    )
    raise typer.Exit(code=2)
```

Change `cfg = _load_and_validate(config)` — already no `mode_override`, no change needed.

Add `limit` option to the function signature:
```python
limit: int | None = typer.Option(
    None, "--limit",
    help="Override defaults.row_limit for this invocation.",
),
```

After `cfg = _load_and_validate(config)`, add:
```python
if limit is not None:
    cfg.defaults.row_limit = limit
```

Update the CLI docstring from `"""Pull curated source tables into bronze (dev-only)."""` to `"""Pull curated source tables into bronze."""`.

Delete `typer.echo("Mode: dev (extract)")`.

- [ ] **Step 2.11: Update `commands/validate.py`**

Remove `"mode": cfg.mode,` from the JSON output dict.

- [ ] **Step 2.12: Remove legacy cache watermark API from `state.py`**

Delete the `read_cache_watermark` method (now a one-liner that already delegates — remove it entirely and update `extract.py` callers to use `read_watermark` directly, which was done in Step 2.6).

Delete the `write_cache_watermark` method (same reason).

Remove `CREATE TABLE IF NOT EXISTS _cache_watermarks (...)` from `init_state` — it's now replaced by the migration logic in `_migrate_v1_to_v2`.

---

## Task 3: Rewrite tests

**Files:**
- Delete: `tests/unit/test_mode.py`
- Rewrite: `tests/integration/test_mode.py`
- Modify: `tests/integration/test_run_transforms.py`
- Modify: `tests/integration/test_incremental.py`
- Modify: `tests/integration/test_transform_command.py`
- Modify: `tests/e2e/test_07_transforms.py`
- Modify: `tests/e2e/test_12_extract.py`

- [ ] **Step 3.1: Delete `tests/unit/test_mode.py`**

```bash
rm tests/unit/test_mode.py
```

This file tests mode parsing (YAML mode, env var override, invalid mode, mode_override param). All those behaviors are being removed. No replacement needed.

- [ ] **Step 3.2: Rewrite `tests/integration/test_mode.py`**

Replace the entire file with tests for the four replacement behaviors. Keep the same filename — it now tests "behaviors that mode used to control":

```python
"""Integration: behaviors previously coupled to mode — now independent controls.

Four concerns:
  1. Target schema defaults to bronze.* (explicit target_table: overrides)
  2. column_map always applies when set
  3. defaults.row_limit always applies when set
  4. --force-views controls DDL kind; default materializes gold
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from feather_etl.config import load_config
from feather_etl.pipeline import run_all
from feather_etl.transforms import run_transforms
from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


def _run_pipeline(
    tmp_path: Path,
    column_map: dict[str, str] | None = None,
    target_table: str | None = None,
    row_limit: int | None = None,
) -> tuple:
    """Helper: run pipeline and return (config, results, dest_path)."""
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    dest_path = tmp_path / "output.duckdb"
    config: dict = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
    }
    if row_limit is not None:
        config["defaults"] = {"row_limit": row_limit}
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)

    if column_map is not None:
        cfg.tables[0].column_map = column_map
    if target_table is not None:
        cfg.tables[0].target_table = target_table

    results = run_all(cfg, config_path)
    return cfg, results, dest_path


def _run_with_transforms(
    tmp_path: Path, force_views: bool = False
) -> Path:
    """Helper: run pipeline with a gold transform and return dest_path."""
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    silver_dir = tmp_path / "transforms" / "silver"
    gold_dir = tmp_path / "transforms" / "gold"
    silver_dir.mkdir(parents=True)
    gold_dir.mkdir(parents=True)

    (silver_dir / "customers_clean.sql").write_text(
        "SELECT customer_id, name AS customer_name, city\n"
        "FROM bronze.erp_customers\n"
    )
    (gold_dir / "customer_summary.sql").write_text(
        "-- materialized: true\n"
        "SELECT city, COUNT(*) AS customer_count\n"
        "FROM silver.customers_clean\n"
        "GROUP BY city\n"
    )

    dest_path = tmp_path / "output.duckdb"
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump({
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(dest_path)},
    }, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)
    cfg.tables[0].target_table = "bronze.erp_customers"
    run_all(cfg, config_path, force_views=force_views)
    return dest_path


# --- 1. Target schema ---

def test_default_extracts_to_bronze(tmp_path):
    """No target_table set: data lands in bronze.<name> by default."""
    _, results, dest_path = _run_pipeline(tmp_path)

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


def test_explicit_target_table_overrides_default(tmp_path):
    """Explicit target_table: silver.erp_customers lands in silver."""
    _, results, dest_path = _run_pipeline(
        tmp_path, target_table="silver.erp_customers"
    )

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM silver.erp_customers").fetchone()[0]
    con.close()
    assert count > 0


# --- 2. column_map ---

def test_column_map_always_applied(tmp_path):
    """column_map present → columns filtered and renamed regardless of any other config."""
    _, results, dest_path = _run_pipeline(
        tmp_path,
        column_map={"customer_id": "cust_id", "name": "cust_name"},
        target_table="silver.erp_customers",
    )

    assert results[0].status == "success"
    con = duckdb.connect(str(dest_path))
    cols = [
        row[0]
        for row in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='silver' AND table_name='erp_customers' "
            "ORDER BY ordinal_position"
        ).fetchall()
    ]
    con.close()

    assert "cust_id" in cols
    assert "cust_name" in cols
    assert "customer_id" not in cols
    assert "email" not in cols


# --- 3. row_limit ---

def test_row_limit_always_applied(tmp_path):
    """defaults.row_limit always limits extracted rows when set."""
    _, results, dest_path = _run_pipeline(tmp_path, row_limit=2)

    assert results[0].status == "success"
    assert results[0].rows_loaded <= 2

    con = duckdb.connect(str(dest_path))
    count = con.execute("SELECT COUNT(*) FROM bronze.erp_customers").fetchone()[0]
    con.close()
    assert count <= 2


# --- 4. DDL kind ---

def test_gold_materialized_by_default(tmp_path):
    """Gold transform marked materialized:true becomes a TABLE by default."""
    dest_path = _run_with_transforms(tmp_path, force_views=False)

    con = duckdb.connect(str(dest_path))
    result = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='customer_summary'"
    ).fetchone()
    con.close()

    assert result is not None
    assert result[0] == "BASE TABLE"


def test_force_views_prevents_materialization(tmp_path):
    """force_views=True: gold transform stays a VIEW even when marked materialized."""
    dest_path = _run_with_transforms(tmp_path, force_views=True)

    con = duckdb.connect(str(dest_path))
    result = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='customer_summary'"
    ).fetchone()
    con.close()

    assert result is not None
    assert result[0] == "VIEW"


def test_yaml_mode_key_ignored_with_warning(tmp_path, recwarn):
    """YAML with 'mode:' key still loads — mode is ignored, warning emitted."""
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump({
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(tmp_path / "output.duckdb")},
        "mode": "prod",
    }, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)
    assert not hasattr(cfg, "mode")
    assert any("mode" in str(w.message).lower() for w in recwarn.list)
```

- [ ] **Step 3.3: Update `tests/integration/test_run_transforms.py`**

Update `_write_config` to remove the `mode` parameter:
```python
def _write_config(tmp_path: Path, dest_db: Path) -> Path:
    """Minimal config file with a single source (unused by run_transforms)."""
    source_db = tmp_path / "source.duckdb"
    duckdb.connect(str(source_db)).close()
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    return config_file
```

Update all `_write_config(tmp_path, dest_db, mode="dev")` and `_write_config(tmp_path, dest_db, mode="prod")` calls to `_write_config(tmp_path, dest_db)`.

Rename and update the two mode-based materialization tests:

```python
def test_run_transforms_force_views_creates_views(tmp_path: Path):
    """force_views=True: gold + silver both land as VIEWs."""
    dest_db = tmp_path / "dest.duckdb"
    _prepare_dest_with_bronze(dest_db)
    _write_sql(tmp_path, "silver", "employees_clean",
               "SELECT id, name FROM bronze.src_employees")
    _write_sql(tmp_path, "gold", "employee_summary",
               "-- materialized: true\nSELECT COUNT(*) AS cnt FROM silver.employees_clean")
    config_file = _write_config(tmp_path, dest_db)

    cfg = load_config(config_file)
    run_transforms(cfg, force_views=True)

    con = duckdb.connect(str(dest_db))
    silver_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='silver' AND table_name='employees_clean'"
    ).fetchone()[0]
    gold_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='employee_summary'"
    ).fetchone()[0]
    con.close()
    assert silver_type == "VIEW"
    assert gold_type == "VIEW"


def test_run_transforms_default_materializes_gold(tmp_path: Path):
    """Default (force_views=False): silver stays VIEW; materialized gold becomes TABLE."""
    dest_db = tmp_path / "dest.duckdb"
    _prepare_dest_with_bronze(dest_db)
    _write_sql(tmp_path, "silver", "employees_clean",
               "SELECT id, name FROM bronze.src_employees")
    _write_sql(tmp_path, "gold", "employee_summary",
               "-- materialized: true\nSELECT COUNT(*) AS cnt FROM silver.employees_clean")
    config_file = _write_config(tmp_path, dest_db)

    cfg = load_config(config_file)
    run_transforms(cfg, force_views=False)

    con = duckdb.connect(str(dest_db))
    silver_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='silver' AND table_name='employees_clean'"
    ).fetchone()[0]
    gold_type = con.execute(
        "SELECT table_type FROM information_schema.tables "
        "WHERE table_schema='gold' AND table_name='employee_summary'"
    ).fetchone()[0]
    con.close()
    assert silver_type == "VIEW"
    assert gold_type == "BASE TABLE"
```

Update the test that had `_write_config(tmp_path, dest_db, mode="dev")` and tested no-materialized-gold to use `force_views=True` instead.

- [ ] **Step 3.4: Update `tests/integration/test_incremental.py`**

Four tests to update. For each, remove `mode=` from the config dict and the fixture call, keeping only the behaviors (`row_limit` / `column_map`) that are now unconditional.

`test_incremental_test_mode_applies_row_limit` → rename to `test_incremental_row_limit_applies`:
- Remove `mode="test"` from config dict; keep `defaults: {row_limit: N}`
- The test body is otherwise unchanged (row_limit already comes from `defaults.row_limit`)

`test_incremental_prod_mode_applies_column_map` → rename to `test_incremental_column_map_applies`:
- Remove `mode="prod"` from config dict
- Ensure `target_table: "silver.erp_..."` is set explicitly (was previously implied by prod mode)

`test_append_test_mode_applies_row_limit` → rename to `test_append_row_limit_applies`:
- Remove `mode="test"` from config dict; keep `defaults: {row_limit: N}`

`test_append_prod_mode_applies_column_map` → rename to `test_append_column_map_applies`:
- Remove `mode="prod"` from config dict
- Ensure `target_table` is set explicitly

For the column_map tests: find the fixture setup (the `project` fixture) and check where `mode="prod"` was being set. Remove it. If the config helper had a `mode:` key, drop it. Also add `target_table: silver.<table>` to the table config so data lands in silver (previously implied by prod mode).

- [ ] **Step 3.5: Update `tests/integration/test_transform_command.py`**

Update `_write_csv_config` to remove the `yaml_mode` parameter:
```python
def _write_csv_config(project_dir: Path) -> Path:
    config = {
        "sources": [...],
        "destination": {...},
        # no mode key
    }
    ...
```

Remove all calls that pass `yaml_mode=`.

**Delete these tests** (they test mode precedence, which no longer exists):
- `test_mode_precedence_cli_beats_env_and_yaml`
- `test_mode_precedence_env_beats_yaml`
- `test_mode_precedence_yaml_beats_default`
- `test_mode_precedence_default_is_dev`
- The `_resolved_mode_from_facts` helper function (used only by the above)

**Replace** `test_mode_matrix_two_axis_ddl_rule` (parametrized over `["dev", "prod", "test"]`) with two tests:

```python
def test_transform_default_materializes_gold(populated_destination: Path):
    """Default: silver VIEW, unmarked gold VIEW, marked gold TABLE."""
    result = _invoke("transform", "--config", str(cfg_path))
    assert result.exit_code == 0
    types = _table_types(populated_destination)
    assert types[("silver", "silver_orders")] == "VIEW"
    assert types[("gold", "gold_summary")] == "VIEW"
    assert types[("gold", "gold_facts")] == "BASE TABLE"


def test_transform_force_views_all_views(populated_destination: Path):
    """--force-views: all transforms are VIEWs including materialized gold."""
    result = _invoke("transform", "--config", str(cfg_path), "--force-views")
    assert result.exit_code == 0
    types = _table_types(populated_destination)
    assert types[("silver", "silver_orders")] == "VIEW"
    assert types[("gold", "gold_summary")] == "VIEW"
    assert types[("gold", "gold_facts")] == "VIEW"
```

**Update** `test_verb_equivalence_extract_plus_transform_equals_run`: remove the `mode` parametrize (was `["dev", "test"]`). Run the equivalence check once with no special flags.

Update the `_invoke` helper: remove any `"--mode", mode` argument passing.

- [ ] **Step 3.6: Update `tests/e2e/test_07_transforms.py`**

`test_setup_reports_gold_views_in_dev_mode` → rename to `test_setup_reports_gold_views_with_force_views`:
- Pass `--force-views` to the `feather setup` invocation instead of `--mode dev`
- The assertion (gold is a VIEW) remains the same

`test_setup_reports_gold_tables_in_prod_mode` → rename to `test_setup_reports_gold_tables_by_default`:
- Remove `--mode prod` from the `feather setup` invocation
- The assertion (gold is a TABLE) remains the same

- [ ] **Step 3.7: Update `tests/e2e/test_12_extract.py`**

Delete the prod-mode hard-error guard test block (the section marked `# Prod-mode hard-error guard` around line 56). This tested that `feather extract` exits 2 when `mode: prod` — the guard is now removed.

For any remaining test that passes `--mode` to `feather run` or `feather extract`, remove `"--mode", "prod"` (or whichever mode arg). Check `test_04_extract_full.py` and `test_14_status.py` for mode references and drop them similarly.

- [ ] **Step 3.8: Run the full suite**

```bash
uv run pytest -q
```

Expected: all tests pass. If red, investigate — do NOT proceed until green.

Common failure modes:
- `AttributeError: 'FeatherConfig' object has no attribute 'mode'` → a test still reads `cfg.mode`; find and fix it
- `TypeError: load_config() got unexpected keyword argument 'mode_override'` → a test still passes mode_override; remove it
- `TypeError: run_transforms() got unexpected keyword argument 'force_views'` → signature not updated in transforms.py
- `AssertionError` on silver table existing → a test expected prod behavior (silver target); add explicit `target_table: silver.*`

- [ ] **Step 3.9: Commit**

```bash
git add \
  src/feather_etl/config.py \
  src/feather_etl/commands/_common.py \
  src/feather_etl/pipeline.py \
  src/feather_etl/transforms.py \
  src/feather_etl/setup.py \
  src/feather_etl/extract.py \
  src/feather_etl/commands/run.py \
  src/feather_etl/commands/transform.py \
  src/feather_etl/commands/setup.py \
  src/feather_etl/commands/extract.py \
  src/feather_etl/commands/validate.py \
  src/feather_etl/state.py \
  tests/integration/test_mode.py \
  tests/integration/test_run_transforms.py \
  tests/integration/test_incremental.py \
  tests/integration/test_transform_command.py \
  tests/e2e/test_07_transforms.py \
  tests/e2e/test_12_extract.py \
  tests/e2e/test_04_extract_full.py \
  tests/e2e/test_14_status.py
git rm tests/unit/test_mode.py
git commit -m "feat: remove mode system — replace with explicit per-concern controls

Four mode-coupled concerns now independent:
- Target schema defaults to bronze.* (explicit target_table: still overrides)
- column_map always applies when set (was prod-only)
- defaults.row_limit always applies when set (was test-only)
- --force-views flag controls DDL kind (replaces dev/test behavior)

CLI: feather run/transform/setup gain --force-views; run/extract gain --limit.
feather extract prod-mode guard removed.
State: _cache_watermarks unified into _watermarks (schema v2, auto-migrated).
YAML 'mode:' key now emits a deprecation warning and is ignored."
```

---

## Self-review checklist

- [x] **Spec coverage:** Target schema (Step 2.3), column_map (Step 2.3), row_limit (Step 2.3, 2.7, 2.10), DDL kind (Step 2.4, 2.8, 2.9), watermark unification (Task 1), deprecation warning (Step 2.1). All four concerns covered.
- [x] **No placeholders:** All steps have actual code.
- [x] **Type consistency:** `force_views: bool = False` used consistently in `run_transforms`, `run_all`, `run_setup`, and all three CLI commands. `source_db: str | None = None` added to `write_watermark` in Task 1 Step 1.4 and used in Step 2.6.
- [x] **Migration:** SCHEMA_VERSION bump, v1→v2 migration, DROP TABLE in migration — covered in Steps 1.1–1.3.
- [x] **`feather run` output:** `Mode: {cfg.mode}` echo removed in Step 2.7.
- [x] **`feather transform` output:** Both `Mode: {cfg.mode}` echos removed in Step 2.8.
- [x] **Incremental tests:** Step 3.4 covers all 4 incremental/append mode tests.
