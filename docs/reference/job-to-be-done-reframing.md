# Feather-etl requirements — Jobs to be Done framing

Created: 2026-05-14
Status: DRAFT — for review before design
Author: collaborative draft, feather-etl creator + agent reviewer

---

## 0. Purpose of this document

This document re-derives feather-etl's requirements from the operator's lived experience, using the Jobs-to-Be-Done (JTBD) lens.

It deliberately **does not propose design**. No folder structures, no YAML schemas, no command implementations. Those come after we agree on the scenarios and what the tool must enable.

Why redo requirements now: the per-table-state RFC (`2026-05-13-per-table-project-state-design.md`) and the discussion that followed exposed that we were designing without a shared, complete picture of who uses feather and what they're trying to do. This doc fills that gap.

**Read top-down.** §1-4 set the frame. §5 walks through concrete scenarios — skim or deep-dive. §6-8 are cross-cutting concerns. §9 is open questions for discussion.

---

## 1. JTBD framing

A "job" is what the operator is trying to accomplish, framed as a verb-led outcome:

> When [situation], I want to [motivation], so that [outcome].

Jobs are stable across tools and time. Implementations vary. By writing jobs first, we keep the design honest to what operators actually need to do — not what's easy for us to build.

---

## 2. Personas

### The Builder
Sets up and maintains feather-etl projects. Knows SQL. Comfortable in YAML and the CLI. Cares about: setting it up once and not babysitting it; clear errors; reproducibility; minimal repetition; not paying for engineer-hours on plumbing.

### The Analyst
Consumes the output (gold tables, marts) in Metabase / Superset / a notebook. Rarely touches feather directly. Cares about: data freshness; consistency; clear definitions of what each table means; trust that yesterday's number won't silently change.

### The Agent
Long-running LLM agents (Claude Code, Codex, etc.) acting on the Builder's behalf. Reads source schemas; proposes curation; edits config; runs commands. Cares about: machine-readable output; deterministic file shapes; exit codes that distinguish success / decision-needed / failure; non-interactive flags for every prompt.

### The On-Call
Comes back to a project after months away — or inherits someone else's project. Needs to understand what's running, what failed, how to fix it. Cares about: discoverability; readable state; useful history; orientation in 10 minutes, not 10 hours.

---

## 3. Top-level jobs

Six jobs cover everything feather-etl does today + redesign aspirations:

| # | Job | One-liner |
|---|---|---|
| J1 | **Connect** | Point feather at a data source (or several). |
| J2 | **Understand** | Learn what's in a source — tables, columns, types, row counts. |
| J3 | **Curate** | Decide what to extract, with what shape, on what cadence. |
| J4 | **Transform** | Express how raw bronze becomes clean silver, gold, and marts. |
| J5 | **Operate** | Run the pipeline; re-run safely after changes; debug; observe history. |
| J6 | **Evolve** | Adapt to source changes; add tables/transforms; refactor without breakage. |

The current CLI maps imperfectly onto these. Some jobs are well-served (J1 Connect, J5 partial Operate); others are underserved (J3 Curate is ceremony-heavy; J6 Evolve is fragile).

---

## 4. Feature inventory (capabilities the tool must provide)

| Capability | Jobs served | Status today |
|---|---|---|
| `feather init` | J1 | Exists; scaffolds stale shape. |
| `feather discover` | J2 | Exists; writes JSON-per-source. |
| Curation editing (file authoring) | J3 | Hand or agent edits `curation.json`. Ceremony-heavy. |
| `feather validate` | J3 / J5 | Exists; connection + config only. Does not check coherence. |
| `feather extract` | J5 | Exists; bronze only. |
| `feather transform` | J4 / J5 | Recent addition; silver / gold via SQL files. |
| `feather run` | J5 | Exists; full pipeline. |
| `feather rerun <thing>` | J5 / J6 | Does not exist. |
| `feather backfill <thing>` | J6 | Implicit today (first run = backfill). No explicit verb. |
| `feather status` | J5 | Exists; minimal. |
| `feather history` | J5 | Does not exist; `_runs` table is queried manually. |
| `feather inspect <table>` | J5 / J6 | Does not exist. |
| `feather list-tables` | J2 / J5 | Does not exist. |
| `feather rename-table` | J6 | Does not exist. |
| `feather schema-diff` | J6 | Does not exist; discover overwrites. |

Open question: which subset is MVP for the redesign, which is deferred. Resolve once §5 scenarios are agreed.

---

## 5. Scenarios

Each scenario is a concrete situation a real operator faces. The shape:

- **Situation** — when does this happen.
- **Goal** — what the operator wants.
- **Walkthrough** — what they DO, step by step.
- **Variants** — what could differ.
- **Implied capability** — what the tool must enable.

### S1 — "I just want this one table from this DB" (greenfield, single-table)

**Situation.** Operator has SQL Server credentials. Wants to extract `Zakya.dbo.Sales` to the local warehouse. Knows the table. Doesn't care what else is in the DB.

**Goal.** From zero to a working pipeline in under 10 minutes.

**Walkthrough.**
1. Operator runs `feather init`. Project scaffolds.
2. Operator edits `feather.yaml` to add the SQL Server source (host, auth).
3. Operator declares the single table to extract — minimal config, hand-written.
4. Operator runs `feather run`.
5. Bronze table appears in the warehouse.

**Variants.**
- Operator wants a column subset, not all.
- Operator wants to skip rows where `Quantity = 0`.
- Operator wants silver renames (snake_case) on day one.

**Implied capability.** A path to a working pipeline that does not require `feather discover` first. Curation can be authored from the operator's prior knowledge of the source. No ceremony for "I just want one table."

---

### S2 — "Discover this source so I can see what's there" (exploratory curation)

**Situation.** Operator inherits a SQL Server with an unfamiliar database. 50+ tables, mostly junk (backups, dev clones, audit tables, `Sales_2023_Migration_Backup`) and 3-5 real ones. Operator doesn't know which is which without inspection.

**Goal.** See what's in the source; pick the few real ones; ignore the rest.

**Walkthrough.**
1. Operator points feather at the source via `feather.yaml`.
2. Runs `feather discover`. Produces a machine artefact listing every table + columns + row counts.
3. Operator (or agent) reads the artefact, identifies real tables (often by row count, freshness, naming pattern).
4. Operator marks 3-5 tables as "include" with appropriate keys.
5. The other 45 are simply NOT mentioned — implicit exclude.
6. `feather run`.

**Pain today.** Each excluded table needs an entry with `decision: exclude` in `curation.json`. For 50 junk tables this is 50 entries of ceremony. Operator engagement collapses; they let the agent fill defaults.

**Variants.**
- Some "junk" tables turn out to be needed later — easy to add back.
- A few tables look like they might be needed but operator's not sure — defer decision.

**Implied capability.** **Implicit exclude.** Operator only lists what they want. Discover artefact is read-only intermediate data, not state. Curation file lists includes only.

---

### S3 — "Multiple databases on one server, several wanted" (multi-DB source)

**Situation.** Single SQL Server hosts `Zakya` (sales ERP), `Gofrugal` (POS), and `HR`. Operator wants Zakya + Gofrugal, not HR.

**Goal.** One source connection, two databases curated independently, clear separation in the warehouse.

**Walkthrough.**
1. Operator declares the SQL Server source once in `feather.yaml`.
2. Operator lists the databases of interest (`Zakya`, `Gofrugal`). `HR` is not listed — implicit exclude at the database level.
3. `feather discover` walks the listed databases. Schema artefacts produced — one per database.
4. Curation specified per database.
5. Each database extracts to its own bronze namespace; no name collisions.

**Variant.** The same database name appears in different sources (`Zakya` in both SQL Server and MySQL — a real case for this project). Must disambiguate without operator gymnastics.

**Implied capability.** Source is **connection-scoped**, not database-scoped. Database is a per-table or per-group attribute. Bronze namespaces disambiguate cross-source automatically.

---

### S4 — "Cross-source silver / gold" (joining bronzes from different sources)

**Situation.** Sales facts come from Zakya (SQL Server). Customer master comes from Gofrugal. Operator wants one gold `facts_sales` joining both.

**Goal.** Write a silver/gold that joins bronzes from different sources, no ceremony.

**Walkthrough.**
1. Bronze for both sources curated and extracted normally (S3).
2. Operator writes a silver/gold SQL that joins both bronzes.
3. Pipeline knows the dependencies (DAG inferred from SQL FROM clauses).
4. Cross-source refresh cadences may differ — operator may want a warning if stale bronze is being joined to fresh bronze.

**Implied capability.** Silvers / golds are first-class. Can span sources. Dependencies inferred from SQL. Cross-source freshness asymmetry is observable.

---

### S5 — "Monthly CSVs from a client" (directory of files)

**Situation.** Client emails a folder of CSVs each month (`orders_2024_01.csv`, `customers.csv`, `products.csv`). Operator drops them in `client_files/`. Wants ingest.

**Goal.** Treat the directory as a source. Each CSV = one table. Re-extract automatically when new/changed files arrive.

**Walkthrough.**
1. Operator declares the source: type=csv, path=`client_files/`.
2. `feather discover` lists each CSV as a candidate table with detected columns.
3. Operator curates the ones they want.
4. `feather run`.
5. Next month: drop new files into the directory. `feather run` detects changes (mtime / hash) and re-extracts only the changed files.

**Variants.**
- CSV columns drift month-to-month (new column added by client) — needs detection.
- Some CSVs are full snapshots; others are append-only.
- Filenames change predictably (`orders_2024_01.csv`, `orders_2024_02.csv`) — operator wants to pattern-match these into one table partitioned by month.

**Implied capability.** File sources. mtime + hash change detection (exists today). Per-file extraction. Glob / pattern matching for filename-as-period.

---

### S6 — "Excel workbook with monthly sheets" (multi-sheet file)

**Situation.** Client maintains one master `.xlsx` workbook with sheets `Jan_2024`, `Feb_2024`, `Mar_2024`. Each sheet is a monthly fact dump with the same column shape.

**Goal.** Each sheet becomes either a row partition of one table (if shape is stable) or a separate table (if shape varies).

**Walkthrough.**
1. Source: type=excel, path=workbook.xlsx.
2. `feather discover` lists each sheet as a candidate table.
3. Operator decides: union all sheets into one table (shape is stable), or treat each as separate.
4. `feather run`.

**Variants.**
- New sheet added each month — pipeline should pick it up.
- Sheet names have spaces / special chars — operator wants a sane handle.
- One sheet doesn't match the schema — pipeline should warn, not silently merge.

**Implied capability.** Excel-as-source with per-sheet enumeration. Optional union-of-sheets pattern with shape validation.

---

### S7 — "Local DuckDB / SQLite as source" (file-DB source)

**Situation.** Client's extract tool dumps to `client_erp.duckdb` weekly. Operator wants to pull from it.

**Goal.** Treat the file as a database — schemas inside, tables inside schemas.

**Walkthrough.**
1. Source: type=duckdb (or sqlite), path=`client_erp.duckdb`.
2. `feather discover` walks the file's schemas + tables (e.g. `icube.orders`, `icube.line_items`).
3. Curate as for any DB source.
4. `feather run`.

**Implied capability.** File-DB sources expose internal schemas + tables. Same curation surface as server-DB sources. Schema inside the file is a real hierarchy level.

---

### S8 — "Iterative buildout over weeks" (bronze → silver → gold)

**Situation.** Operator builds bronze tables in week 1. Adds silver renames/casts in week 2. Adds gold facts in week 3. Each week's work should not break prior weeks.

**Goal.** Layered additions without ceremony. Today's bronze still works after tomorrow's silver lands.

**Walkthrough.**
1. **Week 1.** Bronze tables curated. `feather run` — bronze appears.
2. **Week 2.** Silver SQL / config added. `feather run` — silver appears on top of unchanged bronze.
3. **Week 3.** Gold SQL added. `feather run` — gold appears on top of silver.
4. Bronze is **not** re-extracted unless source data changed.

**Variants.**
- Operator iterates on silver SQL — bronze must not re-run on every silver edit.
- Operator decides one bronze table is wrong; needs to rebuild from scratch.

**Implied capability.** Layers compose. Each layer is independently re-runnable. Bronze re-extract is gated by **source** change detection, not by silver/gold edits.

---

### S9 — "Bug in silver SQL — fix and re-run only what's affected"

**Situation.** Operator notices `silver.zakya_sales.invoice_date` is wrong (cast bug — string parsed as US format instead of UK). Fixes the SQL. Wants to re-run only this silver, not the whole pipeline.

**Goal.** Targeted re-run. Don't re-extract bronze. Don't re-run unaffected silvers. Re-run gold tables that depend on this silver.

**Walkthrough.**
1. Operator edits the silver expression.
2. Runs `feather rerun silver zakya_sales` (or `feather run --from silver.zakya_sales`).
3. Only that silver is rebuilt. Downstream gold rebuilds (DAG).
4. Bronze and unrelated silvers are untouched.

**Implied capability.** Layer-scoped, table-scoped re-run. DAG knows what's downstream. Operator can pick scope.

---

### S10 — "Source schema changed — new column appeared" (schema drift)

**Situation.** Source `Sales` gained a `tax_amount` column. Operator wants to know — silently extracting it would inflate bronze; silently dropping it would lose data.

**Goal.** Be told. Decide whether to ingest the new column.

**Walkthrough.**
1. Operator runs `feather discover` (or `feather schema-diff`).
2. Tool reports: `Zakya.Sales` has a new column `tax_amount` not in curation.
3. Operator either adds it to curation (include) or explicitly acknowledges.

**Variants.**
- A column was removed from the source — pipeline must not silently break.
- A column was renamed (semantic same, different name) — needs more than mechanical diff.
- Type widened (INT → BIGINT) — automatically tolerated or surfaced?

**Implied capability.** Schema-drift detection. Source-side snapshot vs. curation reconciliation. Surfaced through CLI. Never silent. Auto-discover output must not overwrite the curated state.

---

### S11 — "Backfill a newly curated table"

**Situation.** Operator adds a new table to curation. Source has 3 years of history. Wants to backfill once, then incremental.

**Goal.** Pull historical data in one go; thereafter incremental on the operator's chosen watermark.

**Walkthrough.**
1. New table added to curation.
2. `feather backfill <table>` — explicit verb. (Or first `feather run` does full backfill if no watermark exists — implicit.)
3. Subsequent runs are incremental.

**Variants.**
- Backfill a date range only (`--from 2023-01-01 --to 2023-12-31`).
- Backfill in chunks to avoid timeout / memory pressure.
- Re-backfill after a corruption — destructive, needs confirmation.

**Implied capability.** First-run semantics (implicit backfill). Explicit `feather backfill` for re-backfill / range. Watermark management. Destructive operations require explicit confirmation or flag.

---

### S12 — "Inherited a project — what's running, what's broken?"

**Situation.** New operator inherits a feather project. Doesn't know what tables exist, what ran last, what failed. Or: returning operator after 3 months away.

**Goal.** Orientation in 10 minutes.

**Walkthrough.**
1. `feather list-tables` — what's curated, grouped by source/database.
2. `feather status` — what ran last, what's stale (no run in > X days), what failed.
3. `feather history` — recent runs, errors, durations.
4. `feather inspect <table>` — full effective config for one table; generated SQL; lineage.

**Implied capability.** Observability commands as first-class. State of the project is **queryable**, not just file-readable. The `_runs` table is exposed through the CLI, not just SQL.

---

### S13 — "Multiple environments" (dev vs prod warehouse — replacing the old mode system)

**Situation.** Operator develops locally; production runs on a server (or MotherDuck). Both should be possible without a separate codebase or branched config.

**Goal.** One project, switchable environments. No mode-specific column maps, no mode-coupled rename rules. The four-axis mode system is being removed; this scenario is what replaces it.

**Walkthrough.**
1. Locally: `feather run --env dev` writes to `data/feather_dev.duckdb`.
2. Prod: `feather run --env prod` writes to MotherDuck.
3. Same curation, same transforms — only destination differs.

**Variants.**
- Per-env source connections (dev hits a sample DB; prod hits the real one).
- Per-env subset of tables (don't run gigantic facts table in dev).

**Implied capability.** Environment is a runtime parameter that affects **destination** only (and optionally source selection). Transforms and curation are environment-independent. The pre-existing mode system's column-map / schema-rename / gold-materialisation coupling is gone.

---

### S14 — "Scheduled, unattended runs" (cron / scheduler)

**Situation.** Production pipeline runs nightly. No human present.

**Goal.** Run on schedule; succeed silently; fail loudly.

**Walkthrough.**
1. Cron (or APScheduler) invokes `feather run --env prod --quiet`.
2. On success: log to history, no notification.
3. On failure: alerting hook fires (email / Slack / webhook).
4. Operator reads logs the next morning if needed.

**Implied capability.** Non-interactive execution. Alerting on failure (smtplib / webhooks — already in pyproject deps). Structured logs for SRE tooling.

---

### S15 — "Reset / wipe a table and start over"

**Situation.** Silver SQL was wrong for 2 weeks. The data is corrupted past the point where re-running fixes it (because bronze has new data layered over the bug). Operator wants to wipe and rebuild.

**Goal.** Drop bronze + silver + gold for a specific table, re-extract from source, re-transform.

**Walkthrough.**
1. `feather reset <table>` — drops the table at every layer where it exists. Confirmation required.
2. Watermark removed.
3. Next `feather run` rebuilds from scratch.

**Implied capability.** Destructive operations with explicit confirmation. Reset semantics that are layer-aware (drop bronze AND silver AND gold of the same table).

---

## 6. Human / Agent / Mixed collaboration

Three modes of work; each must be a first-class path.

### Pure-human flow
Small projects, one operator. Single-table extracts. Hand-written YAML. Agent not invoked.

**Common cases:** S1, S5 (the monthly cadence after initial setup), S7.

### Pure-agent flow
Bulk curation. Schema-diff triage. Backfill scripts. Anything mechanical at scale.

**Common cases:** S2 (curating a 50-table discover output), S10 (schema-drift triage proposal), bulk renames.

### Mixed flow (most common in practice)
Agent proposes, human reviews, agent applies the approved changes.

**Common cases:** S2 (agent reads discover output, proposes curation, human approves, agent writes); S10 (agent diffs and proposes which new columns to include); S9 (agent fixes a small bug, human reviews diff and merges).

### Requirements for agent compatibility (cross-cutting)
Every command must:

1. **Have a non-interactive mode.** Every interactive prompt has a flag equivalent.
2. **Return distinct exit codes.** `0` success, `1` error, `3` needs human decision (the "agent should pause and ask" code).
3. **Produce machine-readable output on `--json` (or `--yaml`).** When invoked by an agent, output is structured, not formatted-for-humans.
4. **Never silently default to a destructive choice.** Destructive operations require either confirmation or explicit `--yes`.
5. **Be idempotent.** Re-running a command that already completed produces the same state — no surprise side effects.

---

## 7. Cross-cutting concerns

### Idempotency
Every `feather run` invocation produces the same result given the same inputs and unchanged source data. Re-running is safe.

### Determinism
Bronze table names, schemas, and column types are deterministic functions of curation + source state. No "machine-generated suffix" surprises. No "this hash changed because we re-ran discover" surprises.

### Observability
Every action is logged. `feather history` is queryable. `feather status` answers "what's stale" in one glance. `feather inspect` answers "what SQL will actually run."

### Error messages
Errors point at the **file** + **line** + the **rule violated**. Typos suggest corrections (Levenshtein). Batch validation reports all errors at once, not just the first.

### Resumability
A run that fails midway can be resumed without re-extracting completed tables. Watermarks survive partial failure.

### Schema drift
Source changes are surfaced, never silent. Operator (or agent) decides what to do.

### Data quality / quarantine
Reserved as future scope. Should not be designed in conflict with this requirements doc — should compose cleanly into the per-table model when it lands.

### Secrets management
Source credentials never appear in `feather.yaml` literally; always via env var interpolation (`${SQLSERVER_PASS}`).

---

## 8. What feather is NOT (scope boundaries)

Useful to declare, to keep the design tight.

- **Not a full data-quality framework.** DQ checks are reserved scope; the tool should not preclude them but is not them.
- **Not a BI tool.** Output is queryable tables; visualisation lives elsewhere (Metabase, Superset).
- **Not a scheduler.** APScheduler integration is optional; the primary execution model is "run when invoked." Scheduling is composable.
- **Not a data catalog.** Lineage is a useful by-product (DAG from SQL) but the tool is not Atlas / DataHub.
- **Not a transformation framework like dbt.** Overlaps in transform surface but feather is **opinionated end-to-end** (extract + transform + warehouse-shape), where dbt is **transform-only** with BYO extract.

---

## 9. Open questions for discussion

Items where the right answer isn't obvious to me yet. I'd like your view before we move to design.

1. **Curation file granularity.** Per-database, per-source, or per-project? Trade-off: file size vs at-a-glance overview vs agent edit footprint.
2. **Explicit "I considered this and skipped" memory.** With implicit exclude, an operator who looked at a junk table and chose to skip has no record. Useful for audit / handoff? Or noise?
3. **Materialisation defaults.** What does silver default to — view (cheap, recomputes on read) or table (expensive once, fast to read)? Same question for gold.
4. **First-run vs backfill semantics.** Is the first run of a new table always a backfill, or does the operator have to declare? Today: implicit.
5. **Environment switching mechanism.** Replacing mode — is it `--env` flag, separate `feather.yaml` per env, env vars, or all three?
6. **Schema drift cadence.** When does feather check — every `feather run`, only on explicit `feather schema-diff`, both?
7. **Operational columns.** PK / timestamp_column — auto-included when a column subset is set, or operator must list explicitly? (Bug in §5.1 of the prior RFC suggested implicit-include.)
8. **Persona weight.** When the Builder and the Agent want different things from the same command's UX (e.g. progress bar vs structured logs), what's the priority?
9. **Scope of the redesign.** Does the redesign cover everything in §4, or do we sequence — e.g. ship curate + run first, ship `feather inspect` / `rerun` later?
10. **Four-layer reality.** Bronze / Silver / Gold / Departmental Marts (per `docs/architecture/warehouse-layers.md`). The RFC used 3 layers, the architecture doc has 4. Resolve before design.
11. **Test fixtures and test rewriting strategy.** ~290 call sites depend on `make_curation_entry` / `write_curation`. New test helpers must be pinned before code lands.

---

## 10. Next step

If we agree on the scenarios in §5 and the implied capabilities, the next document is the **design** — folder structures, YAML schemas, command signatures — derived from these requirements.

**Acceptance criterion for the design:** every scenario in §5 satisfied without ceremony. Any scenario that requires the operator to write significantly more than they're writing today, or that requires significant agent gymnastics, is a regression — back to the drawing board.

---

## Appendix A — Scenario / capability matrix

Quick cross-reference: which scenarios exercise which capabilities.

| Scenario | init | discover | curate-edit | validate | extract | transform | run | rerun | backfill | status | history | inspect | list | rename | schema-diff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 single-table | ✓ | – | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – | – | – | – | – |
| S2 junk discover | – | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – | – | – | – | – |
| S3 multi-DB | – | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – | – | – | – | – |
| S4 cross-source | – | – | ✓ | ✓ | ✓ | ✓ | ✓ | – | – | – | – | – | – | – | – |
| S5 monthly CSVs | – | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – | – | – | – | – |
| S6 Excel sheets | – | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – | – | – | – | – |
| S7 file-DB | – | ✓ | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – | – | – | – | – |
| S8 iterative buildout | – | – | ✓ | ✓ | – | ✓ | ✓ | – | – | – | – | – | – | – | – |
| S9 silver bug fix | – | – | – | ✓ | – | – | – | ✓ | – | – | – | ✓ | – | – | – |
| S10 schema drift | – | ✓ | ✓ | – | – | – | – | – | – | – | – | – | – | – | ✓ |
| S11 backfill | – | – | ✓ | ✓ | – | – | – | – | ✓ | – | – | – | – | – | – |
| S12 inherited project | – | – | – | – | – | – | – | – | – | ✓ | ✓ | ✓ | ✓ | – | – |
| S13 multi-env | – | – | – | – | – | – | ✓ | – | – | – | – | – | – | – | – |
| S14 scheduled | – | – | – | – | – | – | ✓ | – | – | – | ✓ | – | – | – | – |
| S15 reset/wipe | – | – | – | – | – | – | – | – | ✓ | – | – | – | – | – | – |

Capabilities that show up in ≥3 scenarios are MVP candidates. Capabilities in 1-2 are deferrable.

**MVP candidates** (≥3 scenario coverage): `init`, `discover`, curate-edit, `validate`, `extract`, `run`, `inspect`.

**Deferrable** (1-2 scenarios): `rerun`, `backfill`, `status`, `history`, `list-tables`, `rename-table`, `schema-diff`, `transform` (separate from `run`).

This matrix is for discussion — not yet a sequencing decision.
