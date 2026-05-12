## Context

`feather run` today does two distinct things in one verb: it extracts bronze from source systems, then runs the post-extract SQL transform block at `pipeline.py:568-597` to rebuild silver views and gold views/tables. Operators iterating on transform SQL pay the full extraction cost on every change — slow locally, and a real cost when the source is a remote ERP.

The destination DuckDB already contains last-extract bronze. Re-running silver/gold against that existing destination is fast (seconds), and it does not require any source connection. We expose that path as `feather transform`.

This change is small in surface and shape but touches three structural things at once:
1. A behaviour-preserving refactor inside `pipeline.py` (extract the transform block into a reusable helper).
2. A new CLI command that calls that helper directly.
3. A new value in the history `_runs.trigger` enum.

The design choices below exist to keep each of those three pieces minimal, sharable with `feather run`, and free of new architectural patterns.

## Mode interaction (silver, gold, and materialization)

`feather transform` inherits the mode system already in use by `feather run`. This section pins how that system actually behaves today, because the older `2026-03-28-mode-dev-prod-test.md` plan describes a different model that no longer matches the code.

**Mode resolves once** via the chain `--mode` flag → `FEATHER_MODE` env → `feather.yaml` `mode:` → default `"dev"`. Validated against `{dev, prod, test}`. That resolved value drives four independent concerns at extraction time and one concern at transform time.

**At extraction time (does not apply to `feather transform`):**

1. *Target schema* — dev/test default to `bronze.{name}`, prod defaults to `silver.{name}`. Per-table override via explicit `target_table:`.
2. *Column filtering* — prod with `column_map` extracts only mapped columns and renames via PyArrow post-extract.
3. *Row limiting* — test mode applies `defaults.row_limit`.

These three are extract-only behaviours. `feather transform` does not extract, so they don't apply.

**At transform time (the part `feather transform` cares about):**

4. *DDL kind* — view vs table per transform. This is the **only** mode-driven behaviour `feather transform` exercises.

DDL kind is a **two-axis decision**, not a one-axis decision:

| Layer | Author-side opt-in | dev | test | prod |
|---|---|---|---|---|
| silver | (n/a — always view) | VIEW | VIEW | VIEW |
| gold, no `-- materialized:` header | none | VIEW | VIEW | VIEW |
| gold, with `-- materialized: true` | author opted-in | VIEW | VIEW | **TABLE** |

The reading: **silver is always views.** **Gold is views by default in every mode.** A gold transform only becomes a table when *both* (a) the SQL file's author declared `-- materialized: true` *and* (b) the operator is running prod mode. Dev and test forcibly downgrade marked gold to views (the `force_views=True` path in `execute_transforms`).

The author marker is the per-transform opt-in; the mode is the operator's opt-in to honour the markers. Two layered switches, one resolution.

This is also why `--force-views` is unnecessary (see Non-Goals): the only mode that ever produces a table is prod, and the only escape is `--mode dev`, which already exists.

**What changed since the older mode plan.** The 2026-03-28 plan stated that prod mode would "skip bronze entirely" and "skip silver SQL transforms in prod." That framing was superseded by `docs/architecture/warehouse-layers.md` v1.0 (2026-04-21) and is not what the code does today. Current code: bronze-vs-silver as extract target is a per-table decision, silver SQL transforms always run (always as views), and the only mode-driven behaviour at transform time is whether `-- materialized: true` markers are honoured. When reading the older plan, treat its layer-skipping language as stale.

## Goals / Non-Goals

**Goals:**

- Single execution path for silver/gold across both `feather run` and `feather transform` — one helper, one mode-resolution chain, one mode-aware DDL strategy.
- Operator can iterate on transform SQL in seconds without touching source systems.
- Transform-only runs are filterable in `_runs` history.
- Verb composability — `feather extract && feather transform` is interchangeable with `feather run` in **dev/test** modes. Prod is a documented exception (see Known limitations below).

**Non-Goals:**

- Graph-aware `--select` / `--exclude` filtering. Tracked separately.
- AST-based DAG inference (issue #54). The advisory bronze check uses the existing `-- depends_on:` comment convention.
- `--dry-run` preview. Out of scope for this slice.
- Incremental materialization strategies (e.g. delta tables). Gold-as-table remains a full rebuild.
- Per-invocation materialization overrides. No `--force-views` or similar flag. Operators wanting views-only iterate with `--mode dev`; mode is the single knob.
- Restructuring `pipeline.py` beyond the minimal helper extraction. The orchestration shape stays.

## Decisions

### D1 — Extract `transforms.run_transforms(config)`; both verbs call it

The post-extract block in `pipeline.py:568-597` becomes a free function in `transforms.py`. `pipeline.run_all()` calls it in place; `commands/transform.py` calls it directly without entering the extract path.

**Why a free function, not a class:** the block is already procedural — discover, sort, materialize, record. Wrapping it in a class adds ceremony with no shared state worth holding. The function takes the loaded `Config` and returns a per-transform result list the caller renders.

**Alternative considered:** parametrize `pipeline.run_all(skip_extract=True)`. Rejected because it conflates two verbs into one entry point, makes `run_all`'s contract harder to read, and forces every future flag (`--select`, …) to live behind a boolean wedged into the orchestrator. A named helper keeps each command's surface explicit.

**Refactor risk:** zero net behaviour change for `feather run`. Verified by re-running the existing `feather run` test suite after the extract; no test should need to change.

### D2 — Reuse the existing mode-resolution chain and DDL strategy from `feather run`

Mode is resolved exactly once, in the same order as `feather run`: CLI `--mode` flag → `FEATHER_MODE` env → `feather.yaml` `mode:` field → default `"dev"`. The resolver lives wherever `feather run` resolves it today (likely `config.py` or `cli.py`); both commands import it.

The resolved mode then flows into the same `execute_transforms(..., force_views=...)` call path `feather run` already uses: `force_views=True` in dev/test, default `False` in prod (which honours `-- materialized: true` markers and rebuilds those gold transforms as tables). See the **Mode interaction** section above for the full two-axis rule.

**Why share, not parallel:** two resolvers will drift. The mode chain and the materialization rule are load-bearing user contracts — any divergence between "what `run` does" and "what `transform` does" turns into a debugging trap where the same SQL produces different DDL depending on which verb wrote it. One function, one truth.

**Open implementation question:** if the existing resolver is inlined inside `run`'s Typer callback, lift it into a small `resolve_mode(cli_flag, config)` helper before calling it from `transform`. Flagged in tasks.

### D3 — Bronze-dependency check is advisory, not blocking

Each silver transform may declare `-- depends_on: bronze.<table>` comments. Before execution, the command checks each declared bronze table against the destination's `information_schema.tables`. Missing dependencies produce a `WARNING:` line on stderr but never abort.

**Why advisory:**
- The `depends_on` convention is honour-system today — many existing transforms don't declare it. Blocking on it would break valid workflows the first time an operator forgot a comment.
- The actual dependency truth lives in the SQL itself. If a bronze table is genuinely missing, the transform will fail with a clear SQL error at execution time anyway — the warning just gives an earlier, friendlier signal.
- Iteration verbs should be permissive about preconditions and strict about results.

**Collapse rule:** if more than five distinct unmet bronze dependencies are detected, the command emits a single summary `WARNING:` line stating the count instead of one line per dependency. Threshold of five comes from "still scannable as a list" — past that it's noise.

**Alternative considered:** AST-parse the SQL to build a real DAG and validate. Out of scope (issue #54). The comment-based check is a five-line check; the AST path is a multi-week change.

### D4 — Add `_runs.trigger` column now; schema migration on first connect

The proposal originally claimed `_runs.trigger` already existed with values `{manual, schedule, cache}`. It does not. Inspection of `state.py:61-77` confirms the `_runs` schema has no trigger column, and `feather history` has no `--trigger` filter. This change adds both.

**Column shape.** `trigger VARCHAR`, nullable. Fresh destinations include it in the `CREATE TABLE`. Existing destinations get an `ALTER TABLE _runs ADD COLUMN IF NOT EXISTS trigger VARCHAR` on first connect after upgrade. Idempotent — running upgraded code against an already-upgraded destination is a no-op.

**Accepted values for new writes.** `{'run', 'extract', 'transform', 'schedule'}`. Each names the *verb* that wrote the row, not the invocation source. The earlier `'manual'` framing in the proposal was conceptually wrong — every CLI invocation is "manual" in the sense of being typed by a human, so the value carried no information.

**Legacy rows.** `trigger=NULL` for any `_runs` row written before this change. No backfill. NULL is the honest answer to "what verb wrote this?" when the verb wasn't recorded. The `feather history` command surfaces NULL rows in unfiltered output and excludes them from `--trigger=<x>` output.

**Why a real column, not a derived signal.** The cheap alternative is to infer trigger from `table_name`'s schema prefix (`bronze.*` = extract; `silver.*`/`gold.*` = transform). It's almost right and exactly the kind of fragile heuristic that decays — `feather run` writes both kinds of rows, sources can be configured to land directly in silver, future verbs may break the pattern. A real column is one ALTER TABLE and removes the temptation to add another heuristic later.

**Why now and not as a follow-up.** The new transform verb is observability-blind without it — operators cannot answer "what did `transform` do last time?" except by inspecting the destination directly. The verb's value is partly the audit trail it leaves. Doing the migration now beats two future changes (one for the column, one for the filter).

### D5 — Hard rename `feather cache` → `feather extract`; no alias

The proposal folds in issue #55: rename the verb so the trio `extract` / `transform` / `run` is consistent from the first commit. The rename is **hard** — `feather cache` is removed, not aliased.

**Why hard, not aliased.**
- Project is pre-1.0; there is no installed-base stability contract on CLI surface yet.
- A hidden alias is one more thing that has to be documented, tested, and eventually removed. The removal becomes its own change with its own breakage.
- A clear "no such command" error is a better signal to script authors than a silent deprecation warning that may be missed and later bites in CI.
- `cache` was always a confusing verb name — it described a side effect (caching bronze locally for dev), not the purpose. The rename is a correction, not a stylistic preference.

**Scope of the rename.**
- CLI registration in `cli.py`.
- Command file: `commands/cache.py` → `commands/extract.py`.
- Underlying module if it exists: `feather_etl/cache.py` → `feather_etl/extract.py`.
- Public symbols: `cache()` → `extract()`, `run_cache()` → `run_extract()`.
- Test files / fixtures named after `cache` rename accordingly.
- Trigger value written to `_runs`: `'extract'` (not `'cache'`).

**Alternative considered: hidden alias for one release.** Rejected. The project has no release cadence that gives users time to notice a deprecation; the "one release" never ends. Better to break loudly once.

**What this does not do.** No migration of `_runs` rows already written with `trigger='cache'`. There aren't any — the column is new in this change. Future-proofing for that case is unnecessary.

## Risks / Trade-offs

**Refactor regression in `feather run`** → mitigated by leaving `run_all()` orchestration shape unchanged and running the existing `feather run` test suite unchanged after the extract. The extracted helper is called from the same point with the same arguments.

**Advisory warning fatigue** → mitigated by the >5-dependencies collapse rule. Operators with many gaps see one summary line, not a wall.

**Mode-resolver lift-out churn** → if the existing resolver is buried in `feather run`'s Typer callback, lifting it is a small refactor with its own regression surface. Mitigated by doing the lift as a separate commit before adding `transform`, so the diff is reviewable in isolation.

**Schema migration on existing destinations** → first connect after upgrade runs `ALTER TABLE _runs ADD COLUMN IF NOT EXISTS trigger VARCHAR`. `IF NOT EXISTS` makes it idempotent; running upgraded code twice is safe. The only failure mode is DuckDB versions that don't support `IF NOT EXISTS` on `ADD COLUMN` — verify against the project's pinned version (DuckDB ≥0.10 supports this). Risk: low, but explicitly tested in task 4.

**Hard rename breaks scripts** → any external script invoking `feather cache` will get a "no such command" error. This is intentional (see D5). Risk acknowledged; mitigation is the README note and the loud failure mode itself.

**Trigger value drift between writers** → four writers (`run`, `extract`, `transform`, scheduled-future) each need to set trigger correctly. A single writer forgetting to set it leaves NULL rows that look like legacy data. Mitigation: validator rejects unknown values, and every `_runs` write site is updated in one task (task 4.2) with tests asserting the value per verb.

## Known limitations (deferred to follow-up)

Discovered during implementation of this change. Captured in [`docs/issues/rethink-mode-system.md`](../../../docs/issues/rethink-mode-system.md). Not blockers; this change ships honest about them.

- **Verb equivalence holds in dev/test only, not prod.** `feather run` in prod skips bronze (writes column-mapped rows directly to `silver.*`). `feather extract` always writes to `bronze.*`. Therefore `feather extract && feather transform` is not interchangeable with `feather run` in prod. The 9.15 verb-equivalence test runs in dev/test only, with the prod case excluded by docstring pointing at the issue doc. README/PRD document the verb trio as the dev iteration loop, not a universal substitute.
- **Watermarks split across two tables.** `_watermarks` (written by `feather run`) and `_cache_watermarks` (written by `feather extract`) are separate today. The equivalence test's watermark assertion is relaxed to "same set of watermarked table names across the union." Predates this change; unified table is a candidate cleanup.
- **Root cause is the mode system itself.** Both findings are symptoms of `dev`/`prod`/`test` bundling four orthogonal concerns (target schema, column filtering, row limiting, DDL kind) into three presets that operators routinely need to recombine. The issue doc recommends removing modes entirely in a follow-up.

## Open Questions

- **Resolver location:** is `resolve_mode()` already factored out, or does it need lifting? Confirm during task 1.2 of implementation. (Affects task ordering only, not design.)
- **Summary output format:** the spec mandates the *shape* (`Mode:` header, per-transform line, trailing total). Should the lines match the existing extract/cache command exactly character-for-character, or is "same shape, our choice of wording" enough? Default to character-match unless the existing format reads poorly for transform context.
- **`feather_etl/cache.py` module-rename necessity:** task 3.2 renames `commands/cache.py` unconditionally. Whether there is *also* a top-level `feather_etl/cache.py` module to rename depends on the current tree; verify during task 3.1 and adjust scope.
