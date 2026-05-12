# `feather transform` — Design Spec

> **Superseded note (issue #54):** when this plan was written, the
> advisory bronze-existence check consumed `-- depends_on: bronze.<table>`
> headers from each silver transform. Issue #54 has since removed the
> header convention entirely; the check now derives bronze refs from
> the silver SQL body via `extract_bronze_dependencies`. The plan steps
> below are preserved for historical context — the implementation in
> `transforms.py` differs at the points where the body says "read
> `t.depends_on`."

**Issue:** #53 — `feather transform` (run silver/gold transforms only, without re-extracting bronze)
**Date:** 2026-05-11
**Status:** DRAFT
**Branch:** `feat/feather-transform`
**Worktree:** `.claude/worktrees/feather-transform`

## Scope & Boundaries

**In:** A new `feather transform` CLI command that re-executes silver/gold SQL transforms against the *existing* destination DuckDB. Honors the same dev/prod mode semantics as `feather run` (silver = views; gold = views in dev, materialized tables in prod). Records its own history rows tagged `trigger='transform'`. Validates that referenced bronze tables exist (advisory warning, non-blocking). Reuses the existing `transforms.py` orchestration; no new transform-engine logic.

**Out:**
- `--select` / `--exclude` graph-aware filtering (separate follow-up; ship the unfiltered command first).
- AST-based DAG inference (#54 — independent improvement to `transforms.py`).
- Incremental materialization strategies (`-- materialized: incremental`).
- Renaming `feather cache` → `feather extract` (#55 — sequenced *before* this lands so the verb trio ships coherent).

## User-Facing Contract

```bash
$ feather transform                         # run all transforms (mode from feather.yaml)
$ feather transform --mode prod             # rematerialize gold as TABLEs
$ feather transform --mode dev              # everything as VIEWs
$ feather transform --force-views           # override mode='prod' to keep gold as VIEWs
$ feather transform --config custom.yaml    # non-default config path
```

Exit codes:
- `0` — all transforms executed successfully (or no transforms found).
- `1` — one or more transforms raised SQL errors (per-transform breakdown printed).
- `2` — config invalid, destination DuckDB unreachable, or `transforms/` directory malformed.

Output (mirrors `cache` style):

```
Mode: dev
  silver.accommodation        view    success
  silver.storewise_hrcost     view    success
  gold.hrcost_by_store_month  table   success

3 transforms: 3 succeeded.
```

The verb pair (after #55):

| Command | Layer | Hits source? | Hits destination? |
|---|---|---|---|
| `feather extract` | source → bronze | ✅ | ✅ (write) |
| `feather transform` (this) | bronze → silver/gold | ❌ | ✅ (read+write) |
| `feather run` | source → … → gold | ✅ | ✅ |

## Integration Surface

| File | Change | Risk |
|---|---|---|
| `src/feather_etl/commands/transform.py` | **New file** — typer command (~120 LOC, mirrors `cache.py` shape) | None (new code) |
| `src/feather_etl/cli.py` | Two lines: import + `register_transform(app)` | Minimal |
| `src/feather_etl/transforms.py` | Extract one helper `run_transforms(config, *, force_views=None) → list[TransformResult]` from `pipeline.py:568-597`. No semantic change. | Low — call site of the existing block in `run_all` updated to use the helper. Behavior preserved. |
| `src/feather_etl/pipeline.py` | Replace lines 568-597 with single call to `run_transforms(config)` | Low |
| `src/feather_etl/history.py` | Accept `trigger='transform'` as a valid value in the trigger column constraint | Minimal |
| `tests/test_transform_command.py` | **New file** — unit + integration tests | None (new code) |
| `docs/prd.md` | New §FR-Transform; update §499 Dev cache section to mention `transform` | Docs only |
| `README.md` | Add command to top-level command table; one-line example in dev workflow section | Docs only |

Total: 2 new files, 4 narrow edits to existing files.

## Architectural Decisions

### Decision 1: Extract orchestration into `run_transforms()` helper

**What:** Move `pipeline.py:568-597` (the post-extract transform block) into `transforms.run_transforms(config, *, force_views=None)`. Both `pipeline.run_all` and the new `commands/transform.py` call it.

**Why:** Without extraction, the new command would duplicate the same `discover → topo-sort → execute → maybe-rebuild-materialized` sequence. One source of truth keeps dev/prod semantics identical between `run` and `transform`. Cheap refactor (one function moved, no logic change).

**Rejected alternative:** Inline the orchestration in `commands/transform.py`. Faster to write but guarantees drift the next time pipeline.py's transform handling evolves.

### Decision 2: `--mode` resolution order

**What:** CLI `--mode` flag > `FEATHER_MODE` env var > `feather.yaml` `mode:` field > default `"dev"`. (Same chain as `feather run`.)

**Why:** Operators tuning transforms locally need to flip dev↔prod without editing YAML. The chain is already established by other commands; reusing it means zero new docs.

### Decision 3: Bronze-existence validation is advisory, not blocking

**What:** Before executing, walk each silver transform's `-- depends_on: bronze.x` and check whether `bronze.x` exists in the destination DuckDB. If not, emit `WARNING: bronze.x not present — silver.foo may fail` to stderr but **continue**. The SQL layer raises the actual error if it matters.

**Why:** Operators iterate on transform SQL while bronze extraction is still in flight (or pre-cache). A blocking check would force them to either wait or comment out half the curation. The warning gives signal without forcing serial workflow.

**Rejected alternative:** Hard-fail when bronze deps are missing. Cleaner in theory; in practice it makes the dev loop slower, not safer — the SQL error message is just as good and arrives 1 sec later.

### Decision 4: `--force-views` overrides `mode='prod'` re-materialization

**What:** When `--force-views` is set, gold transforms are created as VIEWs even if the mode resolves to `prod`.

**Why:** A common dev workflow is "production config but just iterating gold SQL" — operator wants the same mode resolution but doesn't want to pay the table-rebuild cost on every iteration. `transforms.execute_transforms` already supports `force_views=True`; the flag just exposes it.

### Decision 5: History tag is `trigger='transform'`

**What:** New trigger value alongside existing `'manual'`, `'schedule'`, `'cache'`. Each transform invocation writes one row per executed transform to `_runs` with `trigger='transform'` and `target=f"{schema}.{name}"`.

**Why:** Operators need to know "did silver.foo last get rebuilt by `run`, `cache`, or `transform`?" — the trigger column is exactly the field for that. `feather history --trigger transform` becomes a discoverable filter.

**Migration:** Add `'transform'` to whatever validates the trigger column (likely a Python literal set in `history.py`). Zero schema migration if no DB-level CHECK constraint exists.

## Task Breakdown

### Task 1: Extract `run_transforms()` helper into `transforms.py`

- **What:** New function `run_transforms(config: FeatherConfig, *, force_views: bool | None = None) -> list[TransformResult]`. Encapsulates the discover→order→execute→maybe-rebuild block currently inline in `pipeline.py:568-597`. Returns the per-transform results.
- **Why/Risk:** Pure refactor — no semantic change. Risk: `pipeline.run_all` currently catches `Exception` broadly around the block and logs; preserve that wrapping at the call site (the helper itself raises; caller handles).
- **TDD test:** `test_run_transforms_dev_mode_creates_views`, `test_run_transforms_prod_mode_materializes_gold`, `test_run_transforms_returns_per_transform_results`, `test_run_transforms_no_transforms_returns_empty_list_no_error`.
- **Code:** ~30 LOC. Move existing block; update `pipeline.run_all` to call `run_transforms(config)` inside its existing `try/except`.

### Task 2: Implement `commands/transform.py`

- **What:** Typer command with `--config`, `--mode`, `--force-views` options. Loads + validates config (`_load_and_validate`), resolves mode via standard chain, calls `run_transforms()`, prints grouped summary, exits with appropriate code.
- **Why/Risk:** Thin wrapper. Risk: forgetting the bronze-advisory check. Implement that as a small helper `_warn_missing_bronze(config, transforms)` called before `run_transforms`.
- **TDD test:**
  - `test_transform_command_no_transforms_exits_zero` (empty `transforms/` dir).
  - `test_transform_command_dev_mode_all_views`.
  - `test_transform_command_prod_mode_materializes_gold`.
  - `test_transform_command_force_views_overrides_prod`.
  - `test_transform_command_one_failing_transform_exits_one`.
  - `test_transform_command_missing_bronze_emits_warning_continues`.
  - **Negative test:** `test_transform_command_does_not_open_source_connection` — monkeypatch each source's `_connect()` to raise; assert it's never called.
- **Code:** ~120 LOC mirroring `commands/cache.py` structure.

### Task 3: Wire command into CLI

- **What:** Add `from feather_etl.commands.transform import register as register_transform` and `register_transform(app)` to `cli.py`.
- **Why/Risk:** Trivial.
- **TDD test:** `test_cli_transform_command_registered` — `runner.invoke(app, ["transform", "--help"])` returns exit 0 and prints usage.
- **Code:** 2 lines.

### Task 4: Add `'transform'` to history trigger values

- **What:** Update `history.py` (and any state-write site that records `trigger`) so `'transform'` is an accepted value. Each executed transform writes one row with `trigger='transform'`, `target=qualified_name`, `status='success'|'failure'`.
- **Why/Risk:** Without this, history is silent about transform-only runs. Risk: schema constraint somewhere — verify with `grep "trigger"` across `state.py`, `history.py`, and migrations.
- **TDD test:** `test_transform_run_writes_history_rows`, `test_history_filter_by_trigger_transform_returns_only_transform_rows`.
- **Code:** ~15 LOC + history-emission call inside `run_transforms`.

### Task 5: Bronze-existence advisory check

- **What:** Helper `_warn_missing_bronze(con, transforms) -> None` queries `information_schema.tables WHERE table_schema='bronze'`, walks each transform's `depends_on`, prints `WARNING: ...` for each unmet bronze.* dependency.
- **Why/Risk:** Should not block execution. Risk: noisy if bronze is genuinely empty (initial setup); mitigated by collapsing the warnings into one summary line when more than 5.
- **TDD test:** `test_warn_missing_bronze_emits_per_unmet_dep`, `test_warn_missing_bronze_silent_when_all_present`, `test_warn_missing_bronze_collapses_when_many`.
- **Code:** ~25 LOC.

### Task 6: PRD + README documentation

- **What:** Add `transform` to `feather --help` table in README. Add §FR-Transform to PRD documenting the verb, mode semantics, history trigger, and the canonical `extract && transform` workflow. Update §499 (Dev cache) to mention `transform` as the natural follow-up.
- **Why/Risk:** Discoverability. Operators won't use a command they can't find.
- **TDD test:** Doctest on the README example block (if doctests are wired); otherwise visual review.
- **Code:** ~40 lines of markdown.

### Task 7: Integration test against rama_dw-style fixture

- **What:** End-to-end test: scaffold a fixture with one bronze table, one silver transform (depending on bronze), one gold transform (`-- materialized: true`, depending on silver). Run `feather transform`. Assert silver is a VIEW, gold is a TABLE, gold rowcount > 0, no source connections opened.
- **Why/Risk:** Catches the integration gaps that unit tests miss (fixture loading, FS layout, history wiring).
- **TDD test:** `test_e2e_transform_against_seeded_bronze`.
- **Code:** ~60 LOC including fixture setup.

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `transforms` block in `pipeline.py:568-597` has subtle behavior I missed | Medium | Medium | Task 1 is pure refactor, covered by existing `feather run` tests; run the full suite before moving on |
| History column constraint blocks the new `'transform'` trigger value | Low | Low | Grep before edit; add the value alongside existing literals |
| `--force-views` flag interacts badly with already-materialized gold from a prior `prod` run | Low | Medium | `execute_transforms` already does `DROP TABLE IF EXISTS` before `CREATE OR REPLACE VIEW` — covered |
| Bronze-advisory check slows down the command on large bronze schemas | Low | Low | One `information_schema` query; runs in milliseconds |
| Operator confusion between `transform` and the renamed `extract` (#55) before #55 ships | Medium | Low | Sequence #55 first per work order; if that slips, README clarifies the verb pair |

## Out-of-Scope (Track Separately)

- `--select silver.foo` / `--select +gold.bar` graph-aware filtering — open as a follow-up issue once the unfiltered command is in users' hands.
- AST-based DAG inference (#54).
- `--dry-run` that prints the SQL each transform would execute without running them.
- Per-transform timing in the output table.

## Acceptance Criteria

- [ ] `feather transform` (no args) executes all discovered transforms in topological order against the existing destination DuckDB.
- [ ] Mode resolution chain matches `feather run` exactly: CLI > env > YAML > default.
- [ ] Exit code 0 on success (including no-transforms-found), 1 on per-transform SQL failure, 2 on config/setup error.
- [ ] Re-running unchanged transforms is idempotent (`CREATE OR REPLACE`).
- [ ] `feather history --trigger transform` lists transform-only runs distinct from `run` and `cache`/`extract`.
- [ ] No source-system connection is opened during a transform invocation (regression test asserts this).
- [ ] `--force-views` produces VIEWs for gold even when resolved mode is `prod`.
- [ ] Missing bronze dependencies emit a warning to stderr but do not block execution.
- [ ] All existing `feather run` tests pass without modification (Task 1 refactor is behavior-preserving).
- [ ] PRD §FR-Transform and README updated.

## Effort Estimate

- Task 1 (refactor): 1h
- Task 2 (command): 2h
- Task 3 (wire-up): 15min
- Task 4 (history): 1h
- Task 5 (advisory check): 1h
- Task 6 (docs): 1h
- Task 7 (e2e test): 1.5h

Total: ~7.5h focused. Realistic ship: one focused day or two half-days with buffer for review feedback.
