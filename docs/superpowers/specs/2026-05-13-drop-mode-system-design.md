# Design: Drop the Mode System

**Date:** 2026-05-13
**Issue:** `docs/issues/rethink-mode-system.md` (C1 resolution)
**Status:** Spec — pre-implementation

---

## What is this?

Remove the `mode: dev | prod | test` config key and its three-preset bundle
entirely. Replace the four concerns it was secretly controlling with four
explicit, independent controls.

This resolves all three symptoms in the issue doc. After this change there
is no mode concept anywhere in feather-etl — not in YAML, not in CLI flags,
not in code.

---

## The four replacements

### 1. Target schema (was: `mode == "prod"` → silver, else → bronze)

**After:** `bronze.<name>` is always the default when no explicit
`target_table:` is set. Per-table `target_table:` already works today and
continues to work unchanged.

Change in `pipeline._resolve_target`:

```python
# Before
def _resolve_target(table, mode):
    if table.target_table:
        return table.target_table
    if mode == "prod":
        return f"silver.{table.name}"
    return f"bronze.{table.name}"

# After
def _resolve_target(table):
    return table.target_table or f"bronze.{table.name}"
```

**Migration note:** Any deployment currently running `mode: prod` without
explicit `target_table:` on each table will shift its extraction target from
`silver.*` back to `bronze.*`. Those deployments need `target_table: silver.<name>`
added per table before upgrading.

### 2. Column filtering (was: `mode == "prod" and column_map` → filter)

**After:** `column_map:` present → always extract filtered and renamed
columns. No mode check.

```python
# Before
prod_columns = list(table.column_map.keys()) if config.mode == "prod" and table.column_map else None
# ...
if config.mode == "prod" and table.column_map:
    data = _apply_column_map(data, table.column_map)

# After
prod_columns = list(table.column_map.keys()) if table.column_map else None
# ...
if table.column_map:
    data = _apply_column_map(data, table.column_map)
```

**Migration note:** Any deployment with `column_map:` set and `mode: dev`
or `mode: test` will now have column filtering applied where it was previously
ignored. This is almost always the correct behavior (you wrote a column_map
because you wanted it).

### 3. Row limiting (was: `mode == "test" and defaults.row_limit` → slice)

**After:** `defaults.row_limit` applies whenever set, regardless of any
other context. Additionally, a `--limit N` CLI flag on `feather run` and
`feather extract` allows one-shot limits without touching YAML.

```python
# Before
if config.mode == "test" and config.defaults.row_limit:
    data = data.slice(0, config.defaults.row_limit)

# After
if config.defaults.row_limit:
    data = data.slice(0, config.defaults.row_limit)
```

`--limit N` CLI flag: when present, overrides `defaults.row_limit` for
that invocation. Does not persist to YAML.

**Migration note:** Any deployment with `defaults.row_limit` set and
`mode: dev` or `mode: prod` will now have the limit applied. This is safe —
no one sets `row_limit` without wanting it.

### 4. DDL kind (was: `mode == "prod"` → materialize gold, else → force views)

**After:** Default behavior honors `-- materialized: true` comments in SQL
files (current prod behavior). A `--force-views` flag on `feather run` and
`feather transform` creates all transforms as VIEWs regardless, enabling
iteration without materializing.

```python
# Before (in transforms.run_transforms)
if config.mode == "prod":
    exec_results = execute_transforms(con, ordered)
    rebuild_results = rebuild_materialized_gold(con, ordered)
else:
    exec_results = execute_transforms(con, ordered, force_views=True)

# After
def run_transforms(config, force_views: bool = False) -> list[TransformResult]:
    ...
    if force_views:
        exec_results = execute_transforms(con, ordered, force_views=True)
    else:
        exec_results = execute_transforms(con, ordered)
        rebuild_results = rebuild_materialized_gold(con, ordered)
```

**Migration note:** Deployments running `mode: dev` or `mode: test` will
shift from force-views to materialize-gold behavior. Pass `--force-views`
to recover the old behavior.

---

## Watermark unification (Symptom 2 / B1)

The dual `_watermarks` / `_cache_watermarks` split existed because `extract`
was conceived as a "dev-only verb with its own state." With mode gone, that
distinction is artificial.

**After:** Single `_watermarks` table. `_cache_watermarks` is migrated and
dropped. A `source_db` column is added to `_watermarks` (nullable; populated
by `extract`, NULL for `run`).

### Migration in `init_state`

Run on every connect, idempotent:

```sql
-- 1. Add source_db column if absent
ALTER TABLE _watermarks ADD COLUMN IF NOT EXISTS source_db VARCHAR;

-- 2. Migrate _cache_watermarks rows → _watermarks (insert-only, no overwrite)
INSERT INTO _watermarks (
    table_name, source_db,
    last_file_mtime, last_file_hash,
    last_checksum, last_row_count, last_run_at
)
SELECT
    table_name, source_db,
    last_file_mtime, last_file_hash,
    last_checksum, last_row_count, last_run_at
FROM _cache_watermarks
WHERE table_name NOT IN (SELECT table_name FROM _watermarks)
  AND EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_name = '_cache_watermarks'
  );

-- 3. Drop the old table
DROP TABLE IF EXISTS _cache_watermarks;
```

### State API changes

- `read_cache_watermark(table_name)` → `read_watermark(table_name)` (already exists; callers updated)
- `write_cache_watermark(...)` → `write_watermark(...)` with `source_db` kwarg added
- Both old methods deleted after callers are migrated

The `extract` module currently calls `state.write_cache_watermark` and
`state.read_cache_watermark`. After this change it calls `write_watermark`
and `read_watermark` with `source_db=` set.

---

## Config changes

```python
# Removed from config.py
VALID_MODES = {"dev", "prod", "test"}

# Removed from FeatherConfig
mode: str = "dev"

# Removed from load_config
yaml_mode = raw.get("mode", "dev")
env_mode = os.environ.get("FEATHER_MODE")
if mode_override: ...
elif env_mode: ...
mode = yaml_mode
if mode not in VALID_MODES: raise ...
```

`FEATHER_MODE` env var is also removed. Any existing YAML with `mode:` key
is silently ignored (or we emit a deprecation warning — see Open questions).

---

## CLI surface changes

### `feather run`
- Remove `--mode` option
- Add `--force-views` flag (default False)
- Add `--limit N` option (default None; overrides `defaults.row_limit`)

### `feather transform`
- Remove `--mode` option if present
- Add `--force-views` flag (default False)

### `feather setup`
- Remove `--mode` option
- No additions (setup doesn't extract or transform)

### `feather extract`
- Remove prod-mode guard (the `if cfg.mode == "prod": exit` block)
- Remove "dev-only" label from docstring and CLI help text
- Add `--limit N` option
- Remove `typer.echo("Mode: dev (extract)")` line

### `feather validate`
- Remove `"mode": cfg.mode` from JSON output

---

## Behavior change summary

| Scenario | Before | After |
|---|---|---|
| No `target_table:`, was `mode: prod` | writes to `silver.*` | writes to `bronze.*` — **needs explicit `target_table:`** |
| `column_map` set, was `mode: dev` | ignored | applied — column filtering now active |
| `defaults.row_limit` set, was `mode: prod` | ignored | applied |
| Was `mode: dev`/`test`, transforms | all VIEWs | gold materialized — pass `--force-views` to revert |
| `feather extract` in any config | blocked if `mode: prod` | always allowed |

---

## Files to change

| File | Changes |
|---|---|
| `src/feather_etl/config.py` | Remove `VALID_MODES`, `mode` field, mode resolution in `load_config` |
| `src/feather_etl/pipeline.py` | Simplify `_resolve_target`, remove all `config.mode` checks |
| `src/feather_etl/transforms.py` | Add `force_views` param to `run_transforms`, remove `config.mode` branch |
| `src/feather_etl/state.py` | Add `source_db` col to `_watermarks`, migration from `_cache_watermarks`, remove `read_cache_watermark` / `write_cache_watermark` |
| `src/feather_etl/extract.py` | Replace `read_cache_watermark` → `read_watermark`, `write_cache_watermark` → `write_watermark` (with `source_db=`) |
| `src/feather_etl/commands/run.py` | Drop `--mode`, add `--force-views`, add `--limit` |
| `src/feather_etl/commands/transform.py` | Drop `--mode`, add `--force-views` |
| `src/feather_etl/commands/setup.py` | Drop `--mode` |
| `src/feather_etl/commands/extract.py` | Drop mode guard, drop "dev-only" label, add `--limit` |
| `src/feather_etl/commands/validate.py` | Drop `cfg.mode` from output |
| `tests/` | Update all mode references (~218 lines) |

---

## Open questions

- **Deprecation warning vs silent ignore:** When a YAML file still has `mode:` set, should feather log a deprecation warning (`mode: key is ignored — remove it`) or silently ignore? A warning is friendlier for existing deployments. Recommend: emit a `typer.echo` warning (not an error) when `mode:` key is present in raw YAML.

- **SCHEMA_VERSION bump:** The watermark migration changes the state DB schema. Should `SCHEMA_VERSION` be bumped to 2 and the downgrade protection updated accordingly? Recommend yes — bump to 2 so future code can detect pre-migration state DBs.

---

## Out of scope

- Changing how `target_table:` per-table works (already correct)
- Changing the `_runs` table structure (trigger column already exists)
- Any MotherDuck or remote destination changes
- The `feather init` wizard — it currently writes `mode: dev` into generated YAML; that line gets removed from the template as part of this change
