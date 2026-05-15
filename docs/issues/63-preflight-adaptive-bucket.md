# Issue #63 — pre-flight sizing + adaptive bucket selection

Pre-design notes pinning the requirements that won't change. Plan: [`docs/plans/2026-05-15-issue-63-preflight-adaptive-bucket.md`](../plans/2026-05-15-issue-63-preflight-adaptive-bucket.md) (to be written).

## Problem

Today every DB-source extract takes the same shape: one window covering the whole table, single-stream read through whatever transport the source is configured with. That's fine for the 12-row test fixtures and the 14K-row `icube.*` client tables. It's painful for the motivating case — `zakya_dbo_sales`, 50M rows × 97 columns over ODBC — where:

- The operator has no way to know in advance whether the extract will take 10 minutes or 8 hours.
- `connectorx` is installed but the planner doesn't use `partition_on` because there's no machinery to decide *when* parallel reads pay off.
- A column-projection file from #58 may or may not exist; the operator only finds out a wide extract was wasteful after it's already running.
- Even with #62's window resumability, the planner emits a single window — so a partial failure restarts from zero.

#63 closes the loop. Before the extract runs, the planner classifies each table by row count and column count, picks an extraction shape (windowing strategy + transport hint + partition count), surfaces the plan to the operator, and refuses to silently do the slow thing.

## Design choice — auto-derive everything, override only when wrong

A previous shape stored per-table windowing config in `curation.json` (the original issue draft) or in one YAML per table (a follow-up idea). Both were rejected:

1. **JSON is the wrong surface for extract-shape intent.** #58 already established that curation is a faithful inventory of source tables, not a config bag.
2. **One YAML per table is bloated** — a 100-table project gets 100 files where most are identical to the default.
3. **A sparse single YAML file** is workable but adds a config artifact operators must remember to commit.

The chosen mechanism: **auto-derive from discovery metadata, with sticky per-table overrides stored in the state DB.** Discovery already records column names and types; extending it to also capture primary keys gives the planner everything it needs. The 95% case becomes zero-config. The 5% case becomes one CLI command.

## Auto-derivation rules

The planner decides three things per table at plan time:

| What | How |
|---|---|
| Window strategy | `date` if any timestamp column exists; else `pk_range` if any integer PK exists; else `single` |
| Window column | First column **by ordinal position** whose source type matches a timestamp pattern (`datetime`, `datetime2`, `date`, `timestamp`, `timestamptz`) |
| Partition_on | First column **by ordinal position** from the table's primary key list |

If auto-derivation can't pick a window column for a non-SMALL table, that's a blocker (see below) — the planner refuses to plan rather than silently fall back to single-window.

## Bucket boundaries

Defaults live in `feather.yaml` under `defaults.extract.*` and are read into the existing `ExtractDefaultsConfig` (today only holds heartbeat fields):

| Field | Default | Purpose |
|---|---|---|
| `t_small_rows` | `100_000` | Below this, the table is SMALL regardless of columns |
| `t_large_rows` | `5_000_000` | At or above this AND `cols >= t_narrow_cols`, the table is LARGE |
| `t_narrow_cols` | `25` | Below this, the table is treated as narrow |
| `t_wide_cols` | `60` | Above this AND no `transforms/bronze/<table>.sql`, the wide-table blocker fires |

Bucket assignment:

- **SMALL** — `rows < t_small_rows` AND `cols < t_narrow_cols`
- **LARGE** — `rows >= t_large_rows` AND `cols >= t_narrow_cols`
- **MEDIUM** — neither

## Bucket → action mapping

| Bucket | Windowing | Transport behavior |
|---|---|---|
| SMALL | single window (today's behavior) | use configured transport, no `partition_on` |
| MEDIUM | date-windowed if window column auto-derivable; else exit 3 unless `--single-window` accepted | use configured transport, no `partition_on` |
| LARGE | date-windowed required; exit 3 if no window column unless `--single-window` accepted | propose `connectorx` + `partition_on` if registered and `supports_partition_on()` returns True; **exit 3 if `connectorx` not registered** unless `--accept-slow-transport` accepted |

`connectorx` partition count when applicable: `min(8, max(2, rows // 1_000_000))`. Bounded 2–8. Fixed for now; tuning is out of scope.

## Cheap rowcount per source

A new `cheap_rowcount(table: str) -> int` method on the source protocol, **DB sources only**:

- SQL Server — `sys.dm_db_partition_stats` summed across `index_id IN (0, 1)`.
- Postgres — `SELECT reltuples::bigint FROM pg_class WHERE oid = '<table>'::regclass`.
- MySQL — `SELECT TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES WHERE ...`.

File sources do not implement `cheap_rowcount` and are not subject to pre-flight (see "Out of scope"). The planner runs only when `cheap_rowcount` is available on the source.

## Discovery extension — primary keys

Discovery today hardcodes `primary_key=None` ([sources/sqlserver.py:259](../../src/feather_etl/sources/sqlserver.py)). #63 extends `discover()` on SQL Server, Postgres, and MySQL to populate `StreamSchema.primary_key` from the source's catalog (`INFORMATION_SCHEMA.KEY_COLUMN_USAGE`, `pg_constraint`, `SHOW KEYS`). One extra cheap query per table at discover time; no recurring cost. File sources are not touched.

## Sticky overrides — `_extract_overrides` table

A new table in the state DuckDB, sibling to `_runs` and `_watermarks`:

```
table_name       TEXT PRIMARY KEY
window_strategy  TEXT       -- 'date' | 'pk_range' | 'single' | NULL
window_column    TEXT       -- NULL = inherit auto-derive
window_grain     TEXT       -- e.g. '1d', NULL = default '1d'
partition_on     TEXT       -- NULL = inherit auto-derive
created_at       TIMESTAMP
```

NULL columns inherit auto-derivation. An operator who only needs to correct the date column writes one field, not the whole record.

CLI surface (new `feather extract override` subcommand group):

```
feather extract override set <table> [--window-strategy X] [--window-column Y] [--window-grain Z] [--partition-on W]
feather extract override clear <table>
feather extract override list
```

`set` is upsert. `clear` removes the row. `list` prints the current overrides as a table.

## Three blockers, three explicit flags

The planner refuses to extract — exits with code 3 and a clear error pointing at the bypass flag or the override command — when:

| Blocker | Trigger | Bypass flag |
|---|---|---|
| Wide table, no projection | `cols > t_wide_cols` AND no `transforms/bronze/<table>.sql` | `--accept-wide` |
| No window column | bucket ≥ MEDIUM AND no auto-derivable column AND no override | `--single-window` |
| LARGE without `connectorx` | bucket = LARGE AND `connectorx` not in the transport registry (#61) | `--accept-slow-transport` |

There is **no blanket `--yes`**. Cron jobs must list each accepted blocker by name — the cron line documents which trade-offs the pipeline owner has accepted.

If multiple blockers fire on the same table, all are shown; each must be accepted by its own flag.

## Pre-flight banner

Always printed when the planner runs (whether or not blockers fire). Per-table:

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

ETA estimation:

- Rows/sec for this table from the last 5 successful runs in `_runs` (use whatever's there if fewer than 5; skip the row entirely if none).
- Otherwise a baseline: `2,000 rows/sec` for arrow-odbc, `6,000 rows/sec` for connectorx-with-partition_on.

TTY behavior: after the banner, if no blockers, prompt `Proceed? [Y/n]` (default Y). Blockers always exit 3 with no prompt — the bypass flag is the only acceptance path.

Non-TTY behavior: no prompt. Proceed if no blockers; exit 3 if any blocker is unaccepted.

## New CLI flags on `feather run`

- `--plan-only` — run pre-flight, print banner, exit 0 without extracting. Works identically in TTY and non-TTY.
- `--accept-wide` — bypass the wide-table blocker (one-shot).
- `--single-window` — bypass the no-window-column blocker (one-shot).
- `--accept-slow-transport` — bypass the LARGE-without-connectorx blocker (one-shot).
- `--table <name>` — restrict planner output and extract to one table (useful for debugging plans).

Bypass flags are one-shot by design. Persistent acceptance is the operator deliberately fixing the underlying condition: add a `transforms/bronze/<table>.sql` projection, set an override, or install + register `connectorx`.

## Acceptance

- `feather run` against a DB source with a SMALL table behaves identically to today: single window, configured transport, no banner prompts (banner still prints, no blockers).
- `feather run` against a 50M-row × 97-column SQL Server table emits the banner shown above, refuses to extract without `--accept-slow-transport` if `connectorx` isn't registered, and uses `partition_on` + 8 partitions if it is.
- A wide table (cols > 60) without `transforms/bronze/<table>.sql` exits 3 with a message naming `--accept-wide` and the projection file path it could create.
- A MEDIUM table whose discovery shows no timestamp column and no integer PK exits 3 with a message naming `--single-window` and `feather extract override set`.
- `feather extract override set zakya_dbo_sales --window-column "Invoice Date"` makes a subsequent `feather run` use `Invoice Date` instead of the auto-derived choice.
- `feather extract override list` and `feather extract override clear <table>` round-trip the state.
- `feather run --plan-only` exits 0 with the banner and never opens an extract connection.
- File sources are unaffected — no banner, no `cheap_rowcount` call, no behavior change.
- Discovery on SQL Server / Postgres / MySQL now populates `StreamSchema.primary_key`; file sources still emit `primary_key=None`.

## Out of scope

- File-source pre-flight (CSV / Excel / DuckDB / JSON). Bucket abstraction has no meaningful effect on single-stream file reads.
- Auto-installation of `connectorx`. The error message points to the install command; the operator runs it.
- Adaptive partition count tuning beyond the fixed `min(8, max(2, rows // 1M))` formula.
- Per-environment overrides (one row per table in `_extract_overrides`; mode-specific overrides can come later if needed).
- Wide-table soft-warn on file sources. The mechanism applies in principle but the file-source extract path is left untouched in this issue.
- A blanket `--yes` flag. Each blocker has its own named bypass; this is deliberate.
- Writing ETA telemetry back to `_runs`. The planner reads what's already there; no new columns.
- Tunable `partition_count` via CLI or override.
- Mutating `curation.json` — no new fields.

## Related

- Issue #58 (bronze projection via `transforms/bronze/*.sql`) is the mechanism the wide-table blocker points at. #63 reads the projection file's column count but does not author one.
- Issue #62 (windowed batched-append + resumability) established the `WindowSpec` contract and `_extract_windows` skip-committed-windows path. #63 extends `extract_windows.plan_windows` to emit multiple windows; the per-window load path is unchanged.
- Issue #61 (transport registry: arrow-odbc default, connectorx opt-in) is why the LARGE-without-connectorx blocker exists at all. #63 honors #61's opt-in invariant — the planner proposes connectorx, never silently registers it.
- Issue #57 (extract heartbeat) is the in-flight observability layer. #63 is the pre-flight observability layer. Together they answer "is this extract going to finish today?" before and during the run.
