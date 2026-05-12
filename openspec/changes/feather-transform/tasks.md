## 1. Preflight

- [x] 1.1 Run `uv run pytest -q` and confirm fully green before touching code. **Result: 808 passing.**
- [x] 1.2 Locate the existing mode-resolution code path. **Result: already factored.** `mode_override` is a parameter on `load_config()` in `config.py:239-327`; the env > YAML > default chain is implemented inside `load_config()` (lines 321-327). CLI calls `_load_and_validate(config, mode_override=mode)` from `commands/run.py:25`. Task 2 becomes "add a direct unit test if missing"; no lift-out needed.
- [x] 1.3 Confirm `_runs` schema state. **Result: no trigger column at `state.py:61-77`**, consistent with the spec's "this change adds it" framing.
- [x] 1.4 Verify DuckDB version supports `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. **Result: supported and idempotent** (verified at the CLI).
- [x] 1.5 Enumerate `feather_etl/cache.py` and `commands/cache.py`. **Result: both exist.** Test files: `tests/unit/commands/test_cache.py`, `tests/integration/test_cache_pipeline.py`, `tests/e2e/test_12_cache.py`. Internal imports from `cli.py` and `commands/cache.py` (self).

## 2. Mode resolver lift-out (skip if already factored)

- [x] 2.1 If `resolve_mode(cli_flag, env, config)` is not already a standalone callable, lift it into `config.py` (or wherever the existing `feather run` callback lives). Pure refactor — same chain, same precedence, same default. **No-op:** already factored as `mode_override` parameter on `load_config()` in `config.py:319-335`.
- [x] 2.2 Update `feather run`'s Typer callback to import and call the lifted helper. No behaviour change. **No-op:** `commands/run.py:25` already calls `_load_and_validate(config, mode_override=mode)`.
- [x] 2.3 Add a direct unit test for `resolve_mode(...)` covering the four-step precedence (CLI > env > YAML > default `dev`) and rejection of invalid values. Added five new tests in `tests/unit/test_mode.py`: CLI beats env, env beats default, YAML beats default, invalid CLI rejected, invalid env rejected. Together with pre-existing tests, all four precedence steps and invalid-value rejection are covered.
- [x] 2.4 Run `uv run pytest -q`. All existing mode tests must pass unchanged. Commit as its own diff so the lift-out is reviewable in isolation. **813 passing** (was 808; +5 new tests).

## 3. Rename `cache` → `extract` (folds in issue #55)

- [x] 3.1 Confirm the file inventory from 1.5 — `commands/cache.py` exists; `feather_etl/cache.py` may or may not. Adjust the rename scope accordingly. Both files existed; both renamed.
- [x] 3.2 `git mv src/feather_etl/commands/cache.py src/feather_etl/commands/extract.py`. Rename the `cache()` function to `extract()` and update the `app.command(name="cache")` registration to `name="extract"`.
- [x] 3.3 If `src/feather_etl/cache.py` exists at top-level, `git mv` it to `src/feather_etl/extract.py` and rename `run_cache()` to `run_extract()`. Update the import inside the renamed command module. Also renamed `CacheResult` -> `ExtractResult` (return type of `run_extract`) since leaving it as `CacheResult` would have been misleading.
- [x] 3.4 Update `src/feather_etl/cli.py`: remove the `cache` registration; add the `extract` registration. No alias.
- [x] 3.5 Update every internal import referencing the old paths (`from feather_etl.cache import ...` → `from feather_etl.extract import ...`; `from feather_etl.commands.cache import ...` → likewise). Also updated `tests/integration/test_architecture_purity.py` (CORE_MODULES list) and `tests/README.md` (doc example).
- [x] 3.6 Rename test files: `tests/test_cache*.py` → `tests/test_extract*.py`. Update test function names and import paths. Three files moved via `git mv`. Output strings inside e2e test changed from `Mode: dev (cache)` to `Mode: dev (extract)` to match the renamed verb context.
- [x] 3.7 Add a test asserting `feather cache` returns a "no such command" error from Typer (positive proof of the hard rename — no alias slipped in). Added `TestNoCacheAlias` in `tests/unit/commands/test_extract.py` (three tests: cache not registered, extract registered, top-level help lists extract not cache) plus `test_feather_help_does_not_list_cache` in `tests/e2e/test_00_cli_structure.py`.
- [x] 3.8 Run `uv run pytest -q`. All renamed tests must pass; no test refers to `cache` anymore (grep to confirm). **817 passing.** Zero module imports / public-symbol references survive; the only remaining "feather cache" strings are intentional docstrings explaining the rename and the no-alias assertion messages.
- [x] 3.9 Commit the rename as its own diff so the rename is reviewable in isolation from later semantic work.

## 4. Add `_runs.trigger` column and history filter

- [x] 4.1 Update `state.py` `_runs` `CREATE TABLE` to include a `trigger VARCHAR` column. Add an `ALTER TABLE _runs ADD COLUMN IF NOT EXISTS trigger VARCHAR` immediately after the create-table block, so existing destinations gain the column on first connect. Idempotent.
- [x] 4.2 Locate every `INSERT INTO _runs` call (currently `state.py:332`). Add the `trigger` value as a new placeholder, sourced from the verb context:
  - `feather run` path → `trigger='run'`
  - `feather extract` (renamed) path → `trigger='extract'`
  - `feather transform` (new) path → `trigger='transform'`
  - Future scheduled path (placeholder, no current writer) → `trigger='schedule'`
- [x] 4.3 Add an application-side validator that rejects trigger values outside `{'run', 'extract', 'transform', 'schedule'}`. Place it at the single writer chokepoint (likely a helper in `state.py`) so all paths share it.
- [x] 4.4 Update `history.py` `load_history(...)` to accept a `trigger: str | None = None` parameter. When set, the SELECT WHERE clause filters by it.
- [x] 4.5 Update `commands/history.py`: add `--trigger <value>` Typer option; pass through to `load_history`. Update both human and JSON output paths to include the `trigger` column.
- [x] 4.6 Unit tests:
  - Fresh destination creates `_runs` with the column present.
  - Existing destination (simulated by creating `_runs` without the column then connecting) gets the column added; second open is a no-op.
  - Validator rejects unknown trigger; accepts the four valid values.
  - `load_history(trigger='run')` returns only run rows. Unfiltered call returns NULL-trigger rows too.
- [x] 4.7 Run `uv run pytest -q`. Existing history tests must pass; new tests must pass.

## 5. Extract `transforms.run_transforms()` helper

- [x] 5.1 Survey the existing `feather run` test suite for branch coverage of the block being extracted (`pipeline.py:568-597`). Confirm both branches are exercised: (a) prod with at least one `-- materialized: true` gold transform that becomes a TABLE; (b) dev/test where everything is forced to VIEW. If either branch is uncovered, add the missing `feather run` test *before* the refactor — otherwise 5.4's "tests pass unchanged" invariant proves nothing. **Result: both branches covered.** Branch A (prod, materialized TABLE) by `tests/integration/test_mode.py::test_prod_gold_materialized` and `tests/integration/test_transforms.py::test_run_rebuilds_materialized_gold` / `test_run_mode_switch_rematerializes_gold`. Branch B (dev/test, force_views) by `tests/integration/test_mode.py::test_dev_gold_view`. Exception-swallow path covered by `tests/integration/test_transforms.py::test_run_all_transform_rebuild_failure_is_caught_and_logged`. No new tests needed.
- [x] 5.2 In `transforms.py`, add `run_transforms(config) -> list[TransformResult]`. Body is the existing block from `pipeline.py:568-597`, copied verbatim — discover, sort, open destination, branch on `config.mode`, return result list. Returns `execute_transforms` results in both branches plus `rebuild_materialized_gold` results in the prod branch.
- [x] 5.3 In `pipeline.run_all()`, replace the inlined block with a single call to `run_transforms(config)`. No other changes to `run_all`'s shape or call sites. Net delete: ~27 lines; net add: 3 lines.
- [x] 5.4 Run `uv run pytest -q`. Every `feather run` test must pass unchanged. If any test changes, the extraction was not behaviour-preserving — revert and reconcile before continuing. **831 passing, unchanged.**
- [x] 5.5 Add a direct unit test calling `run_transforms(config)` on a fixture config and asserting the returned `TransformResult` list shape, schema membership, and statuses. Added `tests/integration/test_run_transforms.py` with 4 tests (return-type/shape, dev-mode views, prod-mode materialized gold, empty-transforms returns []). **835 passing total.**
- [x] 5.6 Commit the refactor as its own diff.

## 6. Advisory bronze-dependency check

- [x] 6.1 Add `check_bronze_dependencies(con, transforms) -> list[str]` to `transforms.py`. Returns a list of warning strings, one per silver transform whose `-- depends_on: bronze.<table>` is missing from `information_schema.tables`.
- [x] 6.2 If the returned list has more than five entries, collapse into a single summary string: `f"WARNING: {N} bronze dependencies missing — run `feather extract` first"`. Otherwise emit one `WARNING:` line per missing dep. **Collapse logic lives in the transform command (Section 7), not the helper — the helper returns the raw list, the caller renders/collapses.**
- [x] 6.3 Pytest covers: zero missing → empty list; one missing → one warning; six missing → one summary; mixed silver-without-depends_on and silver-with-depends_on → only the latter is checked. **7 unit tests in `tests/unit/test_bronze_check.py`.**

## 7. New `feather transform` command

- [x] 7.1 Create `src/feather_etl/commands/transform.py` with a Typer command, mirroring the shape of `commands/extract.py` (renamed). Accept `--config PATH` (default `feather.yaml`) and `--mode {dev,prod,test}` (optional).
- [x] 7.2 Inside the command: load config, resolve mode via the lifted helper, open destination connection, run `check_bronze_dependencies()` and write its output to stderr, call `run_transforms(config)`, write each result as a `_runs` row with `trigger='transform'`, print summary, close connection, exit with the right code. **De-duplicated `(schema, name)` pairs before writing to `_runs` and before rendering the summary; rationale documented in the command's module docstring.**
- [x] 7.3 Exit-code contract: 0 on success (including zero-transforms), 1 if any returned result has `status == 'error'`, 2 if config load or destination open raises. **`_load_and_validate` raises `Exit(code=1)`; the command traps and re-raises with `code=2` to honor the transform-spec contract.**
- [x] 7.4 Summary output: header line `Mode: <mode>`, one line per transform showing `<schema>.<name>  <view|table>  <success|failure>`, trailing line `<total> transforms: <success_count> succeeded.` Match the line shape used by `feather extract` where it overlaps.

## 8. CLI registration

- [x] 8.1 In `src/feather_etl/cli.py`, register the new `transform` command alongside the existing verbs (now `extract`, `run`, etc.). Two-line addition. Also updated `tests/e2e/test_00_cli_structure.py` (the registered-commands and module-names lists) to expect `transform` as part of the CLI surface.
- [x] 8.2 Confirm `uv run feather --help` lists `transform` and `extract` (not `cache`), and `uv run feather transform --help` renders correctly. **Both verified.**

## 9. CLI integration tests

Real DuckDB fixtures, no mocking, per project convention. Unit tests for new pure helpers (`resolve_mode`, `run_transforms`, the trigger validator, `check_bronze_dependencies`) are bundled with their implementing tasks (2.3, 5.5, 4.6, 6.3). This group covers the CLI seam.

- [x] 9.1 Build shared test fixtures under `tests/fixtures/transform_command/` and helpers under `tests/helpers.py`:
  - A `transforms/` directory containing one silver, one unmarked gold, and one gold with `-- materialized: true`. **Added** `silver_orders.sql` (silver), `gold_summary.sql` (unmarked gold), `gold_facts.sql` (marked gold).
  - One silver transform annotated `-- depends_on: bronze.<table>`. **`silver_orders.sql` declares `-- depends_on: bronze.orders_src_orders`** (the sanitized name produced by curation from source_db=`orders_src` + alias=`orders`).
  - A `RaisingSource` class subclassing the source protocol, whose `_connect()` raises `RuntimeError("should not be called")`. **Added in `tests/helpers.py`.** Class-level `connect_called` + `called_methods` flags let tests assert the invariant after the run.
  - A pytest fixture that runs `feather run` once to populate bronze. **Implemented as `populated_destination`** but uses a direct DuckDB insert (cheaper, decouples from `feather extract`); tests that need the extract path call it themselves.
- [x] 9.2 Add `tests/test_transform_command.py` and import from the shared fixtures. **Lives at `tests/integration/test_transform_command.py`** alongside the project's other integration tests.
- [x] 9.3 **No-source-touch invariant** (load-bearing for the whole change). `test_no_source_touch_invariant` registers `RaisingSource` and asserts `RaisingSource.connect_called is False` after the run.
- [x] 9.4 Happy path. `test_happy_path_creates_transforms_and_records_runs` asserts all three transforms in `information_schema.tables` and three rows in `_runs` with `trigger='transform'`.
- [x] 9.5 Zero-transforms case. `test_zero_transforms_exits_zero_and_prints_summary` removes the transforms tree and checks for `0 transforms` in stdout.
- [x] 9.6 Mode matrix (two-axis DDL rule). Parametrized `test_mode_matrix_two_axis_ddl_rule[dev|prod|test]` — three modes × three transforms = nine assertions.
- [x] 9.7 Mode-chain precedence. Four tests: `test_mode_precedence_cli_beats_env_and_yaml`, `test_mode_precedence_env_beats_yaml`, `test_mode_precedence_yaml_beats_default`, `test_mode_precedence_default_is_dev`. Resolution verified by checking whether `gold_facts` materialized as TABLE (prod) or VIEW (dev/test).
- [x] 9.8 Advisory warning (one missing). `test_advisory_warning_single_missing_bronze` drops the bronze table and asserts a `WARNING` line names it.
- [x] 9.9 Zero-warnings clean run. `test_zero_warnings_when_bronze_present` asserts no `bronze dependency missing` substrings appear.
- [x] 9.10 Collapse rule. `test_collapse_rule_more_than_five_missing` writes six silver transforms each declaring a unique missing bronze dep and asserts exactly one summary WARNING line containing `6`.
- [x] 9.11 Exit-code contract. `test_bad_sql_returns_exit_1` (broken gold SQL → exit 1, failure in breakdown) and `test_missing_config_returns_exit_2` (nonexistent config → exit 2). Plus two coverage-driven additions: `test_unexpected_config_load_error_returns_exit_2` and `test_unreachable_destination_returns_exit_2`.
- [x] 9.12 Idempotency. `test_idempotent_two_consecutive_runs` asserts identical `table_type` map and row counts after a second invocation; no "already exists" surface.
- [x] 9.13 History filter. `test_history_trigger_filter_after_mixed_invocations` runs extract → transform → run, then asserts each `--trigger <verb>` filter returns rows whose `trigger` column matches.
- [x] 9.14 Summary output format. `test_summary_output_format` regex-asserts `Mode: dev`, per-transform lines `<schema>.<name>  <view|table>  <status>`, and trailing `\d+ transforms?: \d+ succeeded\.`.
- [x] 9.15 **Verb-equivalence** (load-bearing for the refactor). `test_verb_equivalence_extract_plus_transform_equals_run` parametrized over `{dev, test}`. Compares (1) `(schema, name, table_type)` set, (2) row counts, (3) row content excluding `_etl_loaded_at` and `_etl_run_id`, (4) the union of `_watermarks ∪ _cache_watermarks` table-name set, (5) `_runs.trigger` distribution: path A all `run`, path B has both `extract` and `transform`. **Prod is excluded by design** — `feather run` in prod loads directly into `silver.*` (skipping bronze via `pipeline._resolve_target`), so the two paths are not substitutable in prod. Test docstring explains the exclusion. Watermark comparison was relaxed from "identical `_watermarks` contents" to "same set of watermarked table names across `_watermarks ∪ _cache_watermarks`" because the run path writes `_watermarks` while the extract path writes `_cache_watermarks` — a separation that's intentional in `state.py` and predates this change.
- [x] 9.16 Coverage on new code is 100% line + branch:
  - `commands/transform.py`: **100% / 100%**.
  - `transforms.py`: **99% / 99%** overall; the one remaining BrPart (line 65) is inside `parse_transform_file` and is pre-existing — the new helpers (`run_transforms`, `check_bronze_dependencies`) are fully covered.
  - `state.py`: **100% / 100%**.
  - `history.py`: **100% / 100%**.
  - `commands/history.py`: **95% / 92%**; the two BrPart partials are around the empty-result and error-truncation display paths, both pre-existing — the `--trigger` filter path itself is covered.
  - Full suite: **865 tests passing** (up from 842).

## 10. Documentation

- [x] 10.1 Add §FR-Transform to `docs/prd.md`. State the verb, the mode interaction (cite design's Mode interaction section by name), the no-source invariant, and the exit-code contract.
- [x] 10.2 Update existing §FR-Cache → §FR-Extract in `docs/prd.md`. Bump PRD version. Note the rename was a correction, not a feature addition.
- [x] 10.3 Update `README.md`: top-level command table now lists `extract` (not `cache`), and `transform`. Add a one-line dev-workflow example showing `uv run feather extract && uv run feather transform`. Add a one-line note about the trigger filter: `uv run feather history --trigger transform`.
- [x] 10.4 Update `docs/README.md` and any relevant sub-hub README that referenced `cache`.
- [x] 10.5 Add a one-line migration note to the README for any script authors: *"`feather cache` was renamed to `feather extract`. The old name no longer works; update your scripts."*

## 11. Final verification

- [ ] 11.1 `uv run pytest -q` — fully green, including new tests.
- [ ] 11.2 `uv run pytest -q --cov=src --cov-fail-under=80` — coverage threshold holds.
- [ ] 11.3 `ruff check .` — clean.
- [ ] 11.4 `ruff format .` across the whole repo and commit as a separate style-only diff before push (per project convention).
- [ ] 11.5 Manual smoke: in a throwaway dir, run `uv run feather init`, `uv run feather extract`, `uv run feather transform`, `uv run feather history --trigger transform`, `uv run feather history --trigger extract`. Confirm output matches the spec's summary-output and filter requirements.
- [ ] 11.6 Confirm `uv run feather cache --help` returns "no such command" (positive proof of the hard rename — no alias slipped in).
- [ ] 11.7 Open PR. Body cites this change name and the spec/design under `openspec/changes/feather-transform/`.
