# Pre-flight Sizing + Adaptive Bucket Selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Issue:** #63 — pre-flight sizing + adaptive bucket selection (closes epic #60)
**Spec:** [`docs/issues/63-preflight-adaptive-bucket.md`](../issues/63-preflight-adaptive-bucket.md)
**Date:** 2026-05-15
**Status:** DRAFT
**Branch:** `feature/63-preflight-adaptive-bucket`
**Worktree:** auto-created from this branch via `superpowers:using-git-worktrees`

## Scope & Boundaries

**In:** A pre-flight phase that runs inside `feather run` and `feather extract` for **DB sources only** (SQL Server, Postgres, MySQL). It classifies each table into SMALL / MEDIUM / LARGE using row count and column count, auto-derives the window strategy + partition column from discovery metadata, surfaces a banner, refuses to proceed silently on three named blockers, extends `extract_windows.plan_windows` to actually emit multiple windows for date/pk_range strategies, and persists per-table operator overrides in a new `_extract_overrides` state-DB table with a `feather extract override` CLI subcommand group.

**Out:**
- File-source pre-flight (CSV, Excel, DuckDB, JSON) — single-stream reads, no bucket affordance.
- Auto-installation of `connectorx` — error message points at the install command; operator runs it.
- Adaptive partition-count tuning — the formula is fixed: `min(8, max(2, rows // 1_000_000))`.
- Per-environment overrides — one row per table.
- ETA telemetry written back to `_runs` — the planner reads what's already there.
- A blanket `--yes` flag — each blocker has its own named bypass.
- Mutating `curation.json` — no new fields.

## User-Facing Contract

```bash
# Default behavior (TTY): banner + Y/n prompt unless blockers fire
$ feather run

# Pre-flight only, no extract
$ feather run --plan-only

# Bypass specific blockers (one-shot, combinable, cron-safe)
$ feather run --accept-wide
$ feather run --single-window
$ feather run --accept-slow-transport
$ feather run --table zakya_dbo_sales --accept-wide --accept-slow-transport

# Sticky per-table overrides
$ feather extract override set zakya_dbo_sales --window-column "Invoice Date"
$ feather extract override set zakya_dbo_sales --partition-on "Invoice ID" --window-grain 1d
$ feather extract override clear zakya_dbo_sales
$ feather extract override list
```

Exit codes:

- `0` — extract completed (or `--plan-only` succeeded).
- `1` — extract failed mid-run (unchanged from today).
- `3` — pre-flight blocker(s) unaccepted. Banner stays on stdout; error names each blocker and its bypass flag.

Banner format (always printed per table when planner runs):

```
[zakya_dbo_sales] pre-flight:
    rows (est)      : 49,830,000 (sys.dm_db_partition_stats)
    columns         : 97 (32 in transforms/bronze/zakya_dbo_sales.sql)
    bucket          : LARGE
    transport       : connectorx (partition_on=Invoice ID, partitions=8)
    windowing       : date, Invoice Date, 1d windows
    windows planned : 408 (2025-04-01 → 2026-05-13)
    windows done    : 12 (skipping)
    windows to run  : 396
    est wall time   : 6h 45m at 2,100 rows/sec
```

## Integration Surface

| File | Change | Risk |
|---|---|---|
| `src/feather_etl/planner.py` | **New file** — `pick_plan()` + `ExtractPlan` + `Blocker` + bucket logic (~180 LOC) | None (new code) |
| `src/feather_etl/preflight_banner.py` | **New file** — banner rendering + ETA estimator (~80 LOC) | None (new code) |
| `src/feather_etl/extract_windows.py` | Extend `plan_windows()` to emit date / pk_range windows; add `WindowSpec` source-filter field; rev contract docstring | Medium — touches the #62 contract; per-window load path must stay byte-identical |
| `src/feather_etl/config.py` | Add `t_small_rows`, `t_large_rows`, `t_narrow_cols`, `t_wide_cols` to `ExtractDefaultsConfig` (config.py:78) + YAML parser (config.py:329) | Low |
| `src/feather_etl/state.py` | Add `_extract_overrides` table DDL + `get_override(table)`, `set_override(table, **fields)`, `clear_override(table)`, `list_overrides()` | Low (additive) |
| `src/feather_etl/commands/extract.py` | Wire planner into existing extract command; add 4 flags (`--plan-only`, `--accept-wide`, `--single-window`, `--accept-slow-transport`) | Medium — main wiring point |
| `src/feather_etl/commands/override.py` | **New file** — `feather extract override set/clear/list` subcommands (~100 LOC) | None (new code) |
| `src/feather_etl/cli.py` | Register override subcommand group | Minimal |
| `src/feather_etl/sources/sqlserver.py:259` | Populate `primary_key` in `discover()`; add `cheap_rowcount()` + `get_window_range()` methods | Low |
| `src/feather_etl/sources/postgres.py` | Same trio: `discover()` PK, `cheap_rowcount()`, `get_window_range()` | Low |
| `src/feather_etl/sources/mysql.py` | Same trio | Low |
| `src/feather_etl/sources/base.py` | Add `cheap_rowcount()` and `get_window_range()` as optional protocol methods (default raises `NotImplementedError`) | Low |
| `tests/test_planner.py` | **New file** — bucket boundaries, auto-derive rules, blocker triggers | None (new code) |
| `tests/test_extract_windows_date.py` | **New file** — date-strategy windowing | None (new code) |
| `tests/test_extract_windows_pk_range.py` | **New file** — pk_range-strategy windowing | None (new code) |
| `tests/test_extract_overrides.py` | **New file** — state-DB CRUD + CLI override commands | None (new code) |
| `tests/test_preflight_banner.py` | **New file** — banner rendering, ETA estimation | None (new code) |
| `tests/test_preflight_integration.py` | **New file** — end-to-end against SQL Server fixture (stubbed source) | None (new code) |
| `tests/test_discovery_pk.py` | **New file** — PK capture for each DB source against stubbed catalogs | None (new code) |
| `docs/prd.md` | New §FR-Preflight; cross-link from §FR-Extract | Docs only |
| `README.md` | Update command table + add pre-flight banner example | Docs only |

Total: 7 new source files, 4 narrow source edits, 7 new test files, 2 doc updates.

## Architectural Decisions

### Decision 1: Planner is a pure function, no I/O

`pick_plan()` takes already-fetched inputs (table config, discovery row, cheap rowcount, override row, defaults, transport registry contents) and returns a complete `ExtractPlan` with `blockers: list[Blocker]`. All I/O — `cheap_rowcount()`, `get_window_range()`, override lookup — happens in the caller (`commands/extract.py`). Keeps the planner trivially testable with no fixtures.

### Decision 2: Discovery PK capture is opt-in per source

Source protocol gains an optional `discover()` enrichment: file sources still emit `primary_key=None`. DB sources query their catalog once per table at discover time. No retrofit cost for existing fixtures — they get re-discovered the next time discovery runs.

### Decision 3: `_extract_overrides` uses NULL = inherit auto-derive

Sparse storage. An operator who only fixes the date column writes one field via `feather extract override set <table> --window-column X`. The other columns stay NULL and the planner auto-derives them.

### Decision 4: `WindowSpec` gains a source-filter field; per-window load path unchanged

Today every window re-fetches the whole source table and relies on `DELETE-WHERE-predicate` for window isolation ([extract_windows.py:24-29](../../src/feather_etl/extract_windows.py)). #63 must push the predicate into the source fetch so date-windowed extracts don't re-read 50M rows per window. Add `source_filter: str | None` to `WindowSpec` (composed by the planner from `window_column` + the window's date/pk range). The destination load path (`load_batched_append`) is byte-identical; only the source-fetch call gains a `filter=` argument.

### Decision 5: Bucket = LARGE without connectorx is a blocker, not a warning

Per Q4 confirmation. Silently using arrow-odbc on a 50M-row table is the failure mode operators want to be loud about. Bypass with `--accept-slow-transport` documents the decision in the cron line; persistent acceptance means installing `connectorx`.

### Decision 6: ETA falls back to per-transport baseline

`_runs` history may not have prior runs for a never-extracted table (the motivating bootstrap case). Baseline: 2,000 rows/sec for arrow-odbc, 6,000 rows/sec for connectorx-with-partition_on. When `_runs` has data, the last-5 mean wins.

## Task Breakdown

### Task 1: Add bucket-threshold fields to `ExtractDefaultsConfig`

- **What:** Add `t_small_rows: int = 100_000`, `t_large_rows: int = 5_000_000`, `t_narrow_cols: int = 25`, `t_wide_cols: int = 60` to `ExtractDefaultsConfig` at [config.py:78](../../src/feather_etl/config.py). Extend the YAML parser at [config.py:329](../../src/feather_etl/config.py) to read them with the same defaults.
- **Why/Risk:** Foundation for the planner. Risk: silently divergent defaults between dataclass and parser — keep them in one constant.
- **TDD test:** `test_extract_defaults_threshold_defaults`, `test_extract_defaults_threshold_yaml_override`, `test_extract_defaults_threshold_partial_yaml_keeps_other_defaults`.
- **Code:** ~10 LOC dataclass + ~8 LOC parser.

### Task 2: Source protocol — add `cheap_rowcount()` and `get_window_range()` as optional methods

- **What:** In [sources/base.py](../../src/feather_etl/sources/base.py) (or wherever the Source protocol lives), add two methods that default to raising `NotImplementedError`:
  - `cheap_rowcount(self, table: str) -> int`
  - `get_window_range(self, table: str, column: str) -> tuple[Any, Any] | None`
- **Why/Risk:** Lets the planner ask any source and have file sources opt out without isinstance checks. Risk: introducing breaking-change protocol churn — keep defaults so existing tests still pass.
- **TDD test:** `test_base_source_cheap_rowcount_raises_notimplemented`, `test_base_source_get_window_range_raises_notimplemented`.
- **Code:** ~15 LOC.

### Task 3: SQL Server source — populate PK in `discover()`, add `cheap_rowcount`, `get_window_range`

- **What:** In [sources/sqlserver.py](../../src/feather_etl/sources/sqlserver.py):
  - Extend `discover()` (currently `primary_key=None` at line 259) to query `INFORMATION_SCHEMA.KEY_COLUMN_USAGE` per table and populate `StreamSchema.primary_key` (list of column names in `ORDINAL_POSITION` order).
  - Add `cheap_rowcount(table)` using `sys.dm_db_partition_stats` summed across `index_id IN (0, 1)`.
  - Add `get_window_range(table, column)` running `SELECT MIN([col]), MAX([col]) FROM [schema].[table]`.
- **Why/Risk:** Catalog query at discover time is one round-trip per table; negligible. `MIN/MAX` is fast on indexed columns and a table scan otherwise — document in the docstring that the operator should index the window column.
- **TDD test:**
  - `test_sqlserver_discover_populates_pk_single_column`
  - `test_sqlserver_discover_populates_pk_composite`
  - `test_sqlserver_discover_no_pk_returns_none`
  - `test_sqlserver_cheap_rowcount_returns_int`
  - `test_sqlserver_get_window_range_returns_min_max`
  - All against a `tests/fixtures/sample_erp.duckdb`-style stubbed-catalog fixture (no live SQL Server required).
- **Code:** ~60 LOC.

### Task 4: Postgres source — same trio

- **What:** In [sources/postgres.py](../../src/feather_etl/sources/postgres.py):
  - `discover()` PK via `information_schema.key_column_usage` joined to `table_constraints WHERE constraint_type='PRIMARY KEY'`.
  - `cheap_rowcount(table)` via `SELECT reltuples::bigint FROM pg_class WHERE oid = '<schema.table>'::regclass`.
  - `get_window_range(table, column)` via `SELECT MIN("col"), MAX("col") FROM "schema"."table"`.
- **Why/Risk:** Same shape as SQL Server, different dialect. Risk: `reltuples` is an estimate (updated by ANALYZE); document.
- **TDD test:** `test_postgres_discover_populates_pk`, `test_postgres_cheap_rowcount_returns_estimate`, `test_postgres_get_window_range_returns_min_max`.
- **Code:** ~60 LOC.

### Task 5: MySQL source — same trio

- **What:** In [sources/mysql.py](../../src/feather_etl/sources/mysql.py):
  - `discover()` PK via `SHOW KEYS FROM <table> WHERE Key_name = 'PRIMARY'`.
  - `cheap_rowcount(table)` via `SELECT TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?`.
  - `get_window_range(table, column)` via `SELECT MIN(\`col\`), MAX(\`col\`) FROM \`schema\`.\`table\``.
- **Why/Risk:** Same shape; backtick quoting. Risk: `TABLE_ROWS` is approximate for InnoDB; document.
- **TDD test:** `test_mysql_discover_populates_pk`, `test_mysql_cheap_rowcount_returns_estimate`, `test_mysql_get_window_range_returns_min_max`.
- **Code:** ~60 LOC.

### Task 6: `_extract_overrides` table + StateManager CRUD

- **What:** In [state.py](../../src/feather_etl/state.py), add migration DDL for `_extract_overrides`:
  ```sql
  CREATE TABLE IF NOT EXISTS _extract_overrides (
      table_name      VARCHAR PRIMARY KEY,
      window_strategy VARCHAR,
      window_column   VARCHAR,
      window_grain    VARCHAR,
      partition_on    VARCHAR,
      created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```
  Plus methods on `StateManager`:
  - `get_override(table_name: str) -> ExtractOverride | None`
  - `set_override(table_name: str, **fields) -> None` (upsert; only writes fields explicitly passed; preserves others)
  - `clear_override(table_name: str) -> None`
  - `list_overrides() -> list[ExtractOverride]`
- **Why/Risk:** Mirrors `_runs` / `_watermarks` pattern. Risk: DuckDB doesn't have `ON CONFLICT … DO UPDATE` for partial updates as cleanly as Postgres — use `INSERT OR REPLACE` with a SELECT-merge for partial updates.
- **TDD test:**
  - `test_extract_overrides_table_created_on_init`
  - `test_set_override_creates_row`
  - `test_set_override_partial_preserves_other_fields`
  - `test_set_override_idempotent_upsert`
  - `test_clear_override_removes_row`
  - `test_clear_override_missing_table_no_error`
  - `test_list_overrides_returns_all`
  - `test_list_overrides_empty_returns_empty_list`
- **Code:** ~70 LOC.

### Task 7: `feather extract override set/clear/list` CLI

- **What:** New file `src/feather_etl/commands/override.py`. Typer subcommand group registered under `feather extract`:
  ```
  feather extract override set <table> [--window-strategy X] [--window-column Y] [--window-grain Z] [--partition-on W]
  feather extract override clear <table>
  feather extract override list
  ```
  `set` calls `StateManager.set_override` with only the explicitly passed fields. `list` prints a tab-aligned table; empty list prints `"No overrides set."`.
  Register in [cli.py](../../src/feather_etl/cli.py) alongside other command groups.
- **Why/Risk:** Operator-facing surface. Risk: `--window-strategy` is enum-typed (`date|pk_range|single`) — Typer's `Enum` support gives free validation.
- **TDD test:**
  - `test_override_set_single_field_writes_one_column`
  - `test_override_set_multiple_fields_writes_all`
  - `test_override_set_invalid_strategy_rejected_by_typer`
  - `test_override_clear_removes_row`
  - `test_override_clear_unknown_table_exits_zero_with_message`
  - `test_override_list_empty_prints_no_overrides_set`
  - `test_override_list_populated_prints_table`
- **Code:** ~100 LOC.

### Task 8: Extend `extract_windows.plan_windows` for date and pk_range strategies

- **What:** In [extract_windows.py](../../src/feather_etl/extract_windows.py):
  - Add `source_filter: str | None` to `WindowSpec` (rev the contract docstring at lines 24-29).
  - Extend `plan_windows(table, *, strategy, column, grain, range_)` signature. `range_` is the `(min, max)` tuple from `get_window_range` (or None for single).
  - Implement date enumeration: parse `grain` as a duration (`1d`, `1h`, `1w` — start with `1d` only and reject others until follow-up), iterate `range_[0]..range_[1]` by grain, emit one `WindowSpec` per slice with:
    - `window_key`: ISO-date string for date strategy; `<min>-<max>` for pk_range
    - `window_predicate`: `<dest_col> >= '<start>' AND <dest_col> < '<end>'` (composed in destination quoting)
    - `source_filter`: same shape in source-dialect quoting (delegated to a small `_quote_for_dialect` helper)
    - `window_start`, `window_end`: datetime objects
  - Implement pk_range: divide `range_[0]..range_[1]` into N equal chunks where N = `min(64, max(8, total // 100_000))` (separate from connectorx partition count; this is windowing granularity). Emit `WindowSpec`s with integer ranges.
  - The single-window degenerate case (`strategy='single'`) keeps today's `1=1` / `source_filter=None`.
- **Why/Risk:** Most architecturally load-bearing task. Risk: SQL composition correctness — the predicate is interpolated directly into DELETE and SELECT. Use parameter binding where the destination supports it; otherwise use the same quoting helper consistently. **The composer must reject any window_column that doesn't match `^[A-Za-z_][\w\s]*$` to prevent injection.**
- **TDD test:**
  - `test_plan_windows_single_strategy_returns_one_window` (preserves today's behavior)
  - `test_plan_windows_date_strategy_emits_one_per_day`
  - `test_plan_windows_date_strategy_inclusive_start_exclusive_end`
  - `test_plan_windows_date_strategy_partial_last_day_emits_until_max`
  - `test_plan_windows_pk_range_strategy_divides_into_chunks`
  - `test_plan_windows_pk_range_strategy_handles_single_value_range`
  - `test_plan_windows_rejects_window_column_with_sql_injection_chars`
  - `test_window_spec_source_filter_renders_in_destination_quoting`
  - `test_window_spec_source_filter_quotes_for_sqlserver_dialect` (square brackets)
  - `test_window_spec_source_filter_quotes_for_postgres_dialect` (double quotes)
  - `test_window_spec_source_filter_quotes_for_mysql_dialect` (backticks)
- **Code:** ~120 LOC.

### Task 9: Planner module — `pick_plan()` pure function

- **What:** New file `src/feather_etl/planner.py`:
  ```python
  @dataclass(frozen=True)
  class ExtractPlan:
      bucket: Literal["SMALL", "MEDIUM", "LARGE"]
      transport: str
      partition_on: str | None
      partition_count: int | None
      window_strategy: Literal["date", "pk_range", "single"]
      window_column: str | None
      window_grain: str | None
      windows: list[WindowSpec]
      blockers: list[Blocker]
      eta_seconds: int | None
      rows_estimate: int
      column_count: int
      projection_column_count: int | None  # from transforms/bronze/<table>.sql if present

  @dataclass(frozen=True)
  class Blocker:
      kind: Literal["wide_table", "no_window_column", "slow_transport"]
      table: str
      message: str
      bypass_flag: str

  def pick_plan(
      table: TableConfig,
      discovery: StreamSchema,
      cheap_rows: int,
      window_range: tuple[Any, Any] | None,
      override: ExtractOverride | None,
      defaults: ExtractDefaultsConfig,
      registered_transports: list[str],
      transport_supports_partition_on: bool,
      projection_column_count: int | None,
  ) -> ExtractPlan: ...
  ```
  Logic: derive bucket → derive strategy/column/partition (override-first, auto-derive fallback) → assemble blockers → call `extract_windows.plan_windows` → estimate ETA.
- **Why/Risk:** Heart of #63. Risk: the override-vs-auto-derive precedence has to be field-by-field, not record-by-record. Test each combination.
- **TDD test:**
  - `test_pick_plan_small_bucket_picks_single_window` (10K rows × 5 cols)
  - `test_pick_plan_medium_bucket_picks_date_windowing` (1M rows × 30 cols, has timestamp)
  - `test_pick_plan_large_bucket_picks_connectorx_when_supported` (50M × 97 cols, connectorx registered)
  - `test_pick_plan_large_bucket_blocks_when_connectorx_absent` (50M × 97 cols, no connectorx → `Blocker(kind=slow_transport)`)
  - `test_pick_plan_wide_table_no_projection_blocks` (cols > t_wide_cols, no projection → wide_table blocker)
  - `test_pick_plan_wide_table_with_projection_no_block` (cols > t_wide_cols, projection present → no blocker, projection col count in banner)
  - `test_pick_plan_no_window_column_medium_blocks_no_window_column`
  - `test_pick_plan_no_window_column_small_picks_single_no_block`
  - `test_pick_plan_override_window_column_beats_auto_derive`
  - `test_pick_plan_override_partial_inherits_other_fields_from_auto_derive`
  - `test_pick_plan_auto_derive_date_picks_first_timestamp_column_by_ordinal`
  - `test_pick_plan_auto_derive_pk_range_picks_first_pk_column_by_ordinal`
  - `test_pick_plan_partition_count_formula` (parameterized: 100K→2, 1M→2, 5M→5, 10M→8, 50M→8)
  - `test_pick_plan_eta_uses_runs_history_when_available`
  - `test_pick_plan_eta_falls_back_to_arrow_odbc_baseline`
  - `test_pick_plan_eta_falls_back_to_connectorx_baseline`
  - `test_pick_plan_eta_omitted_when_zero_windows_to_run`
- **Code:** ~180 LOC.

### Task 10: Pre-flight banner rendering

- **What:** New file `src/feather_etl/preflight_banner.py`. Single function `render(plan: ExtractPlan, table_name: str) -> str` produces the exact banner shown in the spec. Wall-time formatter: under 60s → `"45s"`, under 60m → `"12m 30s"`, under 24h → `"6h 45m"`, ≥ 24h → `"1d 6h"`.
- **Why/Risk:** Pure formatting. Risk: divergence between the format here and the spec's banner — keep the spec banner as a golden-string test.
- **TDD test:**
  - `test_render_banner_matches_golden_string_for_large_bucket` (uses the exact spec banner as expected output)
  - `test_render_banner_small_bucket_omits_partition_on_line`
  - `test_render_banner_no_projection_shows_default_all_columns`
  - `test_render_banner_zero_committed_windows_omits_skipping_line`
  - `test_render_banner_wall_time_seconds`, `_minutes`, `_hours`, `_days`
- **Code:** ~80 LOC.

### Task 11: Wire planner into `commands/extract.py` and `commands/run.py` + the four new flags

- **What:** Pre-flight applies to any extract path, so both `feather extract` ([commands/extract.py](../../src/feather_etl/commands/extract.py)) and `feather run` ([commands/run.py](../../src/feather_etl/commands/run.py)) gain the same flags. Extract the pre-flight runner into a shared helper (e.g. `commands/_preflight.py`) so both commands call it identically. The helper accepts the config, the four bypass-flag booleans, `plan_only: bool`, and an optional `--table` filter; returns either `(plans: list[ExtractPlan], blockers: list[Blocker])` or exits 3 if any blocker is unaccepted.
  - Add Typer options on **both** commands: `--plan-only: bool`, `--accept-wide: bool`, `--single-window: bool`, `--accept-slow-transport: bool`, `--table: str | None`.
  - Before the existing extract loop, for each DB-source table:
    1. Call `source.cheap_rowcount(table)`.
    2. Look up override via `StateManager.get_override(table)`.
    3. If override picks date strategy or auto-derive lands on date/pk_range, call `source.get_window_range(...)`.
    4. Count projection columns from `transforms/bronze/<table>.sql` if present (helper already in `bronze_projections.py` from #58).
    5. Call `pick_plan(...)`.
    6. Print banner via `preflight_banner.render(plan, table)`.
    7. Filter `plan.blockers` against the four accept-flags. If any remain, accumulate; after all tables planned, exit 3 with the assembled error message.
    8. If `--plan-only`, skip the actual extract for this table.
  - For TTY without blockers, prompt `Proceed? [Y/n]` once (across all tables, not per-table).
- **Why/Risk:** The wiring point. Risk: ordering — banner must print **before** the TTY prompt for that table, but the prompt is a single global gate.
- **TDD test:**
  - `test_extract_command_db_source_runs_planner_and_prints_banner`
  - `test_extract_command_file_source_skips_planner`
  - `test_extract_command_plan_only_exits_zero_without_extract`
  - `test_extract_command_blocker_unaccepted_exits_three`
  - `test_extract_command_blocker_accepted_by_flag_proceeds`
  - `test_extract_command_multiple_blockers_each_listed_separately`
  - `test_extract_command_tty_prompts_y_n_after_banner` (monkeypatch `sys.stdin.isatty` and `input`)
  - `test_extract_command_non_tty_no_prompt`
  - `test_extract_command_table_filter_restricts_planning_and_extract_to_one_table`
- **Code:** ~150 LOC of additions to `commands/extract.py`.

### Task 12: End-to-end integration test against motivating fixture

- **What:** New file `tests/test_preflight_integration.py`. Build a stubbed SQL Server source (no live SQL Server required) returning controlled `cheap_rowcount`, `discover()`, and `get_window_range()`. Wire it through `feather run` end-to-end. Three scenarios:
  1. Small table (5K rows, 10 cols) → SMALL bucket, single window, no banner prompt-blockers, extract completes.
  2. Large table with all conditions met (50M rows, 32 projection cols, connectorx registered) → banner shows partition_on, 408 windows, extract proceeds.
  3. Large table missing connectorx → banner + `slow_transport` blocker → exit 3.
- **Why/Risk:** Catches integration gaps unit tests miss (CLI wiring, state-DB initialization, source-protocol surface). Risk: stubbed source drift from real SqlServerSource — keep the stub thin; verify the real source separately in Task 3.
- **TDD test:** The three scenarios above as separate test functions.
- **Code:** ~120 LOC including the stubbed source.

### Task 13: PRD + README documentation

- **What:** Add §FR-Preflight to [docs/prd.md](../prd.md) documenting the bucket model, three blockers, override surface, and exit codes. Update [README.md](../../README.md) command table with `feather extract override` subcommands and add a "Pre-flight banner" example block.
- **Why/Risk:** Discoverability — operators won't use what they can't find. Risk: docs drift from CLI; the README example should be a real command, ideally a doctest.
- **TDD test:** Visual review; if doctests are wired (per existing pattern), make the README block executable.
- **Code:** ~50 lines of markdown.

### Task 14: `ruff format .` + final test sweep

- **What:** Run `ruff format .` across the entire repo per standing convention. Commit as `style: ruff format sweep before pushing #63`. Run `uv run pytest -q` and confirm green; run `ruff check .` and confirm clean.
- **Why/Risk:** Per `format_all_before_push` memory — format sweep is a separate commit before push. Risk: a stray format-only diff in an unrelated file — review the diff before committing.
- **TDD test:** N/A (format pass).
- **Code:** N/A.

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `WindowSpec` source-filter composition opens a SQL-injection surface | Low | High | Window-column regex check at planner; quoting helpers per dialect; parameter binding where supported; explicit injection test in Task 8 |
| `cheap_rowcount` is wildly wrong for a never-vacuumed Postgres table | Medium | Low | Document the `reltuples` caveat in the banner ("est"); operator can re-ANALYZE if it bothers them |
| MIN/MAX on a non-indexed column triggers a table scan | Medium | Medium | Document the index assumption in `get_window_range` docstring + acceptance criterion; banner reports the column name so the operator knows what to index |
| Override-vs-auto-derive precedence wrong on partial overrides | Medium | Medium | Field-by-field precedence with explicit test for each combination (Task 9) |
| Date-windowing partial-last-day off-by-one | Medium | Low | Explicit "inclusive-start-exclusive-end" test in Task 8; predicate uses `>= start AND < end` consistently |
| Banner format drift from spec | Low | Low | Golden-string test in Task 10 pinned to the spec banner |
| Discovery PK query breaks on a table the operator can't read | Low | Medium | Wrap PK query in `try / except`; fall back to `primary_key=None` with a single-line warning, don't abort discovery |
| `_extract_overrides` upsert race between two `feather extract override set` runs | Very low | Very low | DuckDB single-writer; not a real concern |
| Operator runs `--plan-only` then runs without `--plan-only`; banner appears twice | N/A | N/A | Working as designed; banner-on-every-run is the contract |

## Out-of-Scope (Track Separately)

- Adaptive partition-count tuning (formula fixed at `min(8, max(2, rows // 1M))`).
- Per-environment overrides — open a follow-up if mode-specific extract policy ever becomes needed.
- File-source pre-flight (CSV / Excel / DuckDB / JSON).
- Window grains other than `1d` — Task 8 explicitly rejects `1h` / `1w` for now.
- ETA telemetry written back to `_runs`.
- A blanket `--yes` flag.
- `feather extract plan` as a separate top-level command (today it's `feather run --plan-only`).
- Bronze-projection authoring helpers (#58 already shipped the file; #63 only reads it).

## Acceptance Criteria

- `uv run pytest -q` passes (720 existing + ~85 new ≈ 805 tests).
- `feather run` against a SMALL table behaves identically to today; banner prints; no prompt-blockers.
- `feather run` against a 50M-row × 97-column SQL Server table emits the spec banner exactly; uses `partition_on` + 8 partitions when `connectorx` is registered; exits 3 with `--accept-slow-transport` guidance when not.
- A wide table (cols > 60) without `transforms/bronze/<table>.sql` exits 3 with a message naming `--accept-wide` and the projection file path it could create.
- A MEDIUM table with no auto-derivable window column exits 3 with `--single-window` and `feather extract override set` guidance.
- `feather extract override set zakya_dbo_sales --window-column "Invoice Date"` persists across runs; subsequent `feather run` uses `Invoice Date`.
- `feather extract override list` round-trips set/clear correctly.
- `feather run --plan-only` exits 0 with the banner and never opens an extract connection.
- File sources extract identically to today — no banner, no `cheap_rowcount` call.
- Discovery on SQL Server / Postgres / MySQL populates `StreamSchema.primary_key`; file sources still emit `primary_key=None`.
- `ruff check .` clean; `ruff format .` produces no diff.
