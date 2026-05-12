## Why

Operators iterating on silver/gold SQL today have no way to re-run transforms without paying the full `feather run` cost of re-extracting bronze from source systems. The dev loop is "edit SQL → wait for source extraction → see result," which is slow and pointlessly hits source ERPs while the bronze data hasn't changed. A dedicated `feather transform` verb closes the gap and completes the `extract` / `transform` / `run` trio that operators already expect from the CLI grammar.

To ship the trio coherently this change also folds in **issue #55**: rename the existing `feather cache` verb to `feather extract`, since "cache" describes a side effect (caching bronze locally) rather than the verb's purpose. And to give the verb trio actual operator observability, this change adds a `_runs.trigger` column (currently absent from the schema) plus a `feather history --trigger <value>` filter, so operators can answer "what did `transform` do last time?" without poking around the destination.

## What Changes

**The new verb:**

- Add a new `feather transform` CLI command that executes all discovered silver/gold transforms against the existing destination DuckDB, without opening any source connection.
- Honor the same dev/prod mode-resolution chain as `feather run` (CLI flag > `FEATHER_MODE` env > `feather.yaml` > default `dev`). Silver always materializes as views; gold materializes per the two-axis rule (only `-- materialized: true` gold becomes a table, and only in prod).
- Extract the existing post-extract transform block in `pipeline.py:568-597` into a shared `transforms.run_transforms(config)` helper used by both `feather run` and `feather transform`. Pure refactor — no semantic change to `feather run`.
- Emit advisory (non-blocking) `WARNING:` to stderr for each silver transform that references a `bronze.*` table (via `FROM`/`JOIN` in its SQL body) which is missing from the destination. Many warnings collapse to a single summary line. Bronze references are derived from the operator's silver SQL `FROM`/`JOIN` clauses referencing `bronze.X` — issue #54 removed the `-- depends_on: bronze.<table>` header convention; the SQL body is now the sole source of truth.

**The verb rename (folds in #55):**

- Rename `feather cache` → `feather extract`. Hard rename, **no alias**. Existing scripts that call `feather cache` will break loudly with a "no such command" error — chosen over a silent deprecation alias because the project is pre-1.0 and aliases are tech debt.
- Rename `src/feather_etl/commands/cache.py` → `commands/extract.py` and any underlying module (`feather_etl/cache.py` → `feather_etl/extract.py`).
- Existing test files / fixtures named after `cache` rename accordingly.

**Run history infrastructure (currently missing):**

- Add a `trigger` column to the `_runs` table via schema migration. Currently the schema has no such column; the spec previously assumed it did, in error. Fresh destinations get the column in the `CREATE TABLE`; existing destinations get an `ALTER TABLE ADD COLUMN`. Legacy rows have `trigger=NULL`, which is honest.
- Every `_runs` writer sets the trigger value going forward:
  - `feather run` writes `trigger='run'`
  - `feather extract` (renamed from cache) writes `trigger='extract'`
  - `feather transform` (new) writes `trigger='transform'`
  - Future scheduled-run invocations write `trigger='schedule'`
- Extend `feather history` with `--trigger <value>` filter so operators can scope output to one verb's writes.

**Docs:**

- Update PRD (new §FR-Transform, updated §FR-Cache → §FR-Extract) and README to document the verb trio, the rename, the trigger filter, and the canonical `extract && transform` workflow.

Explicitly out of scope (tracked separately):

- `--select` / `--exclude` graph-aware filtering.
- AST-based DAG inference (#54).
- `--dry-run` preview.
- Incremental materialization strategies.
- Per-invocation materialization overrides (e.g. "prod mode but views"). Operators wanting views-only iterate with `--mode dev`.
- Deprecation alias for `feather cache`. The rename is hard.
- Backfill of `trigger` value for legacy `_runs` rows. NULL is the answer for pre-change rows.

## Capabilities

### New Capabilities

- `transform-command`: The `feather transform` CLI verb — discovery, mode resolution, advisory bronze-existence check, execution against the existing destination DuckDB, history emission, exit-code contract, and human-readable summary output. Also adds the `_runs.trigger` column and the `feather history --trigger` filter, since both are needed by this verb and by the rename below.
- `extract-verb-rename`: The `feather cache` → `feather extract` rename. Hard rename, no alias. Covers the CLI surface, the underlying module, and the trigger value written to `_runs`.

### Modified Capabilities

<!-- None. openspec/specs/ is empty; the existing transforms.py / state.py / history.py changes are refactor/extension and do not modify any previously-specified capability. -->

## Impact

**Code — new files:**

- `src/feather_etl/commands/transform.py` — new Typer command (~120 LOC), mirrors the existing cache/extract command shape.
- `tests/test_transform_command.py` — new integration tests including the load-bearing no-source-touch invariant.
- `tests/test_extract_rename.py` (or extension of existing cache tests, renamed) — covers the rename.
- `openspec/changes/feather-transform/specs/extract-verb-rename/spec.md` — rename spec.

**Code — modified files:**

- `src/feather_etl/cli.py` — register `transform`; replace `cache` registration with `extract`.
- `src/feather_etl/commands/cache.py` → renamed to `commands/extract.py`. Function and `app.command(name=...)` updated.
- `src/feather_etl/cache.py` → renamed to `feather_etl/extract.py` (if module exists at that name today).
- `src/feather_etl/transforms.py` — new `run_transforms()` helper extracted from `pipeline.py`. Behaviour-preserving. New `check_bronze_dependencies()` helper for the advisory check.
- `src/feather_etl/pipeline.py` — `run_all()` calls the new helper in place of the inlined block. Writes `trigger='run'` to `_runs`.
- `src/feather_etl/state.py` — `_runs` `CREATE TABLE` gains a `trigger VARCHAR` column; existing-destination path runs an `ALTER TABLE ADD COLUMN IF NOT EXISTS trigger VARCHAR` for backward compatibility. All `INSERT INTO _runs` calls add the trigger value.
- `src/feather_etl/history.py` — `load_history()` accepts a `trigger` filter argument.
- `src/feather_etl/commands/history.py` — adds `--trigger <value>` Typer option.

**Docs:**

- `docs/prd.md` — new §FR-Transform; existing §FR-Cache renamed and updated to §FR-Extract.
- `README.md` — top-level command table updated for the trio; dev-workflow example showing `extract && transform`.
- `docs/README.md` and any sub-hub README that listed `cache` updated.

**APIs / external surface:** 

- New `transform` subcommand.
- `cache` subcommand replaced by `extract` — **breaking change** for any external script calling `feather cache`. Acceptable pre-1.0.
- New `trigger` column in `_runs` table — additive, NULL for legacy rows.
- New `--trigger` filter on `feather history` — additive.

**Dependencies:** None added.

**Sequencing:** None. This change subsumes #55. Land as one atomic verb-trio shipment.
