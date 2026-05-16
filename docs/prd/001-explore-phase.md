# 001 — The Explore phase

Created: 2026-05-15
Revised: 2026-05-16 — after manual rehearsal with real client (rama_dw); see `/Users/siraj/Desktop/NonDropBoxProjects/rama_dw/tally/store_sales_2026_05_11/agent_log.md`
Status: DRAFT — revised against actual session evidence
Sequence: First phase of the operator's journey. Precedes curate / extract / transform / deploy.

---

## 1. Purpose & scope

This phase covers the work an operator does **before any pipeline runs** — from holding a reference report (Power BI screenshot, Tableau export, CSV from the client) to having a **validated hypothesis** about which tables, columns, and aggregation logic match that reference.

**The load-bearing commitment:** all of this work happens inside feather. No DBeaver, no SSMS, no TablePlus, no psql. Feather is the workbench, not just the pipeline.

**The dominant pattern in this phase is hypothesis iteration.** v1 of an operator's query produces actuals; the diff vs baseline reveals what's wrong; v2 corrects; sometimes v3 confirms via an independent measurement. The PRD treats hypothesis iteration as the canonical shape; drift detection (re-running a fixed query over time) is its degenerate case (one version, many runs).

**The output of this phase** is a small set of artefacts that feed everything downstream:

- One or more reference baselines (`tally/<event>/<base>.csv`) — what the numbers *should* be.
- Versioned probe quadruples (probe + actuals + diff + analysis, per version per baseline) — the iteration trail.
- An exploration log (`explore/`) — every ad-hoc query tried outside a saved tally.
- A `README.md` and `agent_log.md` per tally folder — durable documentation of what was learned.

Once these exist, the operator can move to the **curate** phase knowing their assumptions are grounded in real numbers.

---

## 2. Goals of the explore phase

1. **Everything in feather.** Zero context-switching to external SQL tools during exploration.
2. **Nothing is lost.** Every SQL run, every output, every comparison is auto-saved and retrievable.
3. **Hypothesis iteration is first-class.** The tool fits the v1 → v2 → v3 cadence naturally — versioned artefacts, no overwrites of prior thinking.
4. **The validated hypothesis is re-runnable.** Saved as a tally quadruple that can run again at any later phase (against source, bronze, silver, or gold).
5. **Humans and agents use the same verbs.** Every command has a non-interactive flag set and machine-readable output mode.
6. **Skills (orchestration / narrative / judgment) sit on top of verbs (mechanical).** The CLI is verb-only; skills live separately and call verbs.

---

## 3. The Rama story — pure narrative

*(No tool mention. Just what the people do. Real session, summarised. Detailed transcript in `rama_dw/tally/store_sales_2026_05_11/agent_log.md`.)*

- **Report.** An analyst receives a sales report from a Power BI dashboard — branch-wise revenue for May 11, 2026 (sixteen branches plus a Total). The goal: prove the numbers can be reproduced from the source data, so the dashboard can eventually point at the warehouse instead.

- **Hypothesis.** The analyst points the operator at the **Zakya** database on the client's SQL Server, table likely called `Sales`. Working hypothesis, not a guarantee.

- **First attempt (v1).** Operator aggregates `dbo.Sales` by branch, filtered to 2026-05-11. Every branch over-reports by 0.4-5% relative to the dashboard. None are under. The bias is symmetric — Sales is always strictly greater than PBI.

- **Pattern recognition.** Universal positive bias is not noise. PBI must be subtracting something the operator isn't accounting for. Likely candidate: returns.

- **Second attempt (v2).** Operator finds `dbo.SalesCredit` (credit notes for returns). Re-aggregates as `Sales − SalesCredit` per branch. All 16 deltas zero. The credit total — to the rupee — equals the v1 over-report.

- **Independent confirmation (v3).** The client's BI dev maintains a view, `dbo.AllSales_Zakya_Grouped`, that internally does the UNION ALL of Sales (positive) + SalesCredit (sign-flipped). Operator queries the view directly. Same numbers, third measurement. Triangulated.

- **What emerged.** The Power BI baseline, two reproductions of it (from raw tables and from the dev's view), and per-version written narrative capturing what each iteration learned. All in a single tally folder, durable for the rest of the project's life.

---

## 4. The same story, with feather overlaid

*(High-level. What the operator uses, what feather does, where the human still has to think.)*

- **Reference arrives.** Analyst exports the Power BI numbers to CSV, drops at `tally/store_sales_2026_05_11/storewise_summary.csv`. The folder is named for the event (date + report). Multiple baselines can sit alongside — later in the session, an underlying-grain baseline joined the same folder.

- **Source registration.** Operator runs `feather source add` for the SQL Server. Connection tested, databases listed (four: Zakya, Gofrugal, SAP, Portal), Zakya picked as the current context.

- **First attempt (v1).** Operator writes `storewise_summary_probe_v1.sql` aggregating `dbo.Sales` by branch. `feather tally store_sales_2026_05_11 storewise_summary` runs it, saves `storewise_summary_actuals_v1.csv` and `storewise_summary_diff_v1.csv`. Every branch over-reports.

- **Reflection.** Operator writes `storewise_summary_analysis_v1.md` capturing the hypothesis for v2 — "PBI is net of returns; try `Sales − SalesCredit`." Feather doesn't author this; the operator (or a skill) does.

- **Second attempt (v2).** Operator writes `storewise_summary_probe_v2.sql` doing `Sales − SalesCredit`. Another `feather tally` invocation. All deltas zero. Writes `storewise_summary_analysis_v2.md` recording what worked.

- **Third attempt (v3) — independent confirmation.** Operator writes `storewise_summary_probe_v3.sql` querying the dev's view directly. Same baseline, different angle. `feather tally` again. Same numbers. Writes `storewise_summary_analysis_v3.md`.

- **What feather did.** Eliminated auth + connection + run + save + diff boilerplate across three iterations. Auto-numbered every artefact (`_v1`, `_v2`, `_v3`) for permanent history. Made any version re-runnable with one command.

- **What feather didn't do.** Form the "subtraction" hypothesis. Find SalesCredit. Decide v3 was worth doing. Write the three analysis MDs. All bucket-2 work — operator or skill territory.

---

## 5. Feature surface

The verbs feather provides for the explore phase. Detail in §6.

| Verb | Purpose |
|---|---|
| `feather init` | Scaffold a project: feather.yaml, .env, .gitignore, project directories. |
| `feather source add` | Register a new source interactively; validate connection; set current context. |
| `feather source test` | Re-test a registered source's connection. |
| `feather use` | Set, show, or clear the project's current source/database context. |
| `feather sample` | Row peek from a source table; auto-LIMIT or auto-sample by size. |
| `feather sql` | Run arbitrary read-only SQL against a source; auto-save. |
| `feather compare` | Diff two CSVs by key, with tolerance. |
| `feather explore log` | List ad-hoc exploration entries from a date range. |
| `feather explore replay` | Re-run a previously saved exploration. |
| `feather tally` | Run a baseline's probe SQL (per version), save actuals + diff. |

**Note: `feather profile` is deferred.** The real session never reached for it — operators came with context from the PBI inspection step, and catalog SQL (information_schema, sys.columns) handled the dim-discovery work via plain `feather sql`. Reserved as a future skill (`profile-unfamiliar-table`) that composes `feather sample` + a fixed `feather sql` block. Promote to verb only if the skill ceiling is hit. See §6.6 and §8.

## 5.1 The skills layer

Verbs are **mechanical** — deterministic transformations of data or state. Skills sit on top and handle **narrative, judgment, and orchestration** — work where two operators on two days could legitimately produce different outputs. The CLI is verb-only; skills are separate artefacts that **call verbs** via their `--json` mode.

The manual rehearsal produced four skills (already built and registered, kebab-case names):

| Skill | Bucket | Output |
|---|---|---|
| **`inspect-powerbi-report`** | Source-system mapping | `powerbi_inspection_report.md` |
| **`reconcile-bi-baseline`** | Orchestrator + iteration loop | Closed `tally/<event>/` folder |
| **`write-tally-analysis`** | Per-iteration narrative | `<base>_analysis_v<N>.md` |
| **`resolve-dim-mapping`** | Key-translation table | `<dim-name>_mapping.csv` |

**The pipeline.**

```
1. Operator gives a PBI URL  →  inspect-powerbi-report
                                writes powerbi_inspection_report.md
                                prompts: "Run reconcile-bi-baseline?"

2. Operator confirms         →  reconcile-bi-baseline
                                scaffolds tally/<event>/
                                generates probe_v1.sql + diff_v1 sidecar
                                shows the SQL + expected result
                                PAUSES: "Run v1?"

3. Operator confirms         →  runs v1
                                calls write-tally-analysis → analysis_v1.md
                                reads the diff, pattern-matches
                                PAUSES: "v2 hypothesis is X. Run v2?"

   ... loops on iteration approval ...

4. Diff closes               →  reconcile-bi-baseline marks complete
                                generates source.sql for feather tally consumption
                                (or hits a key-mismatch pattern → calls resolve-dim-mapping)
```

Each gate is explicit. Operator can stop at any point. Skill internals are out of scope for this PRD; they get their own design docs.

**Out of scope for the skills layer (deferred or won't-fix).**

- The would-be commands. Today the skills generate Python sidecar scripts wrapping connectorx + connection boilerplate. Once `feather sql` / `feather compare` / `feather tally` ship, those templates collapse to CLI invocations. The PRD is the source of truth for what becomes a command.
- **DAX text extraction.** `inspect-powerbi-report` reads the dashboard structurally but cannot extract DAX measure text without `Dataset.Read.All` against XMLA. The skill is honest about this gap. The other three skills don't need it (they reproduce numbers empirically, not by parsing DAX).
- **Cross-DB-server joins** (e.g. SQL Server ↔ MySQL). Skills assume one connection at a time; for federation, operator does load-then-join in DuckDB (§7.5).

**Verb-side requirement imposed by the skill layer:** machine-readable `--json` output on every verb. That's the only constraint skills place on the CLI design.

---

## 6. Per-feature requirements

Each feature has three subsections:

- **Requirements** — what it must do (capability level).
- **Acceptance criteria** — verifiable conditions; how we know it's done.
- **Contract** — CLI signature, inputs/outputs, examples, edge cases.

### 6.1 `feather init`

#### Requirements
- Scaffold a new feather project in the current directory.
- Create `feather.yaml` with a commented-out `sources:` block and project defaults.
- Create `.env` for secrets (empty, gitignored).
- Create `.gitignore` covering `.env`, `explore/`, `.feather/`, `*.duckdb`.
- Create empty directories: `explore/`, `tally/`, `tables/`.
- Refuse to scaffold over an existing project unless `--force`.

#### Acceptance criteria
- After `feather init` in an empty directory, every file/dir listed above exists.
- After `feather init`, `feather use` exits 0 and prints `(none)`.
- After `feather init`, `feather sample` (with no source registered) exits 1 with a message pointing at `feather source add`.
- `feather init` in a non-empty directory exits 1 unless `--force`.

#### Contract
```bash
feather init [--name PROJECT_NAME] [--force] [--json]
```
Exit codes: `0` success; `1` non-empty directory without `--force`.

### 6.2 `feather source add`

#### Requirements
- Interactively register a new source.
- Prompts are driven by a **type registry** — each source type (sqlserver, postgres, sqlite, csv, duckdb, etc.) declares its required fields and supplies sensible defaults (e.g. port 1433 for sqlserver, 5432 for postgres).
- Validate the connection as the final step of data entry.
- For types with a database concept: discover databases on the server. If exactly one, register it silently. If more than one, prompt to pick which to include.
- Write the resulting block to `feather.yaml`. Never write raw passwords — prompt, store in `.env` under a generated key (`<TYPE>_PASS_<NAME>`), reference via `${VAR}` in `feather.yaml`.
- On success: set the new source as the current source (and the current database, if exactly one was registered or the operator picked one). Skippable with `--no-set-current`.
- On failure at any step: still write the partial block with empty placeholders for unverified fields, with inline comments naming what to fix and which command to re-run.
- Every prompt has an equivalent CLI flag, so the same workflow runs headless for agents.
- Refuse to overwrite an existing source name unless `--force`.

#### Acceptance criteria
- After a successful `feather source add` flow, `feather.yaml` contains a complete, validated source block.
- The source's password reference (`${VAR}`) resolves to a value present in `.env`.
- `feather use` reports the new source as the current context (unless `--no-set-current`).
- A subsequent `feather sample <source>.<database>.<table>` succeeds without re-prompting for credentials.
- When the connection test fails, `feather.yaml` still contains the host/port/user the operator entered, plus an inline comment explaining what to fix.
- A second `feather source add --name <existing>` without `--force` exits 1 and leaves `feather.yaml` unchanged.

#### Contract
```bash
feather source add [--name N] [--type T] [--host H] [--port P] \
                   [--user U] [--password-env VAR] \
                   [--databases D1,D2] [--no-set-current] [--force] [--json]
```

Interactive walkthrough (typical happy path):
```
Source name: rama-prod
Source type (sqlserver, postgres, sqlite, csv, duckdb): sqlserver
Host: sql-prod.rama.local
Port [1433]:
User: rama_reader
Password: ********    (saved to .env as SQLSERVER_PASS_RAMA-PROD)

Testing connection... OK
Found 4 databases on the server: Zakya, Gofrugal, SAP, Portal
Include all? [Y/n/select]: select
Choose databases: Zakya, SAP

Wrote rama-prod to feather.yaml.
Current context: rama-prod.Zakya
```

Failure-path yaml (connection test failed):
```yaml
sources:
  rama-prod:
    type: sqlserver
    host: sql-prod.rama.local
    port: 1433
    auth:
      user: rama_reader
      password: ${SQLSERVER_PASS_RAMA-PROD}
    databases: []        # connection test failed: could not list databases.
                         # Fill in manually, then run `feather source test rama-prod`.
```

Exit codes: `0` success; `1` hard error; `3` partial write (connection or discovery failed; yaml has placeholders).

### 6.3 `feather source test`

#### Requirements
- Re-run connection check for an already-registered source.
- Report whether the connection succeeds and whether the configured databases are still listable.
- Pure read on `feather.yaml`. **Drift-handling and `--update-databases` are deferred to the operate phase** (we don't yet have the use case in explore).
- Idempotent.

#### Acceptance criteria
- For a healthy source, exits 0.
- For a source with bad credentials or unreachable host, exits 1 with an actionable error.
- For a source missing from `feather.yaml`, exits 1 with closest-match suggestion.

#### Contract
```bash
feather source test <name> [--json]
```

In-sync output:
```
Testing rama-prod...
Connection: OK
Databases configured: [Zakya, SAP] (all reachable)
```

Exit codes: `0` healthy; `1` connection failed or source not registered.

### 6.4 `feather use`

#### Requirements
- Set, show, or clear the project's current source/database context (stored at `.feather/state.yaml` — see §7.6).
- Accept `<source>` (source only) or `<source>.<database>` (both).
- Validate that the source exists in `feather.yaml`; validate the database exists if given.
- No-argument form prints the current context (or `(none)`).
- `--clear` removes the current context.
- Atomic write.

#### Acceptance criteria
- `feather use rama-prod.Zakya` then `feather use` prints `rama-prod.Zakya` and exits 0.
- `feather use bad-source` exits 1 with a closest-match suggestion.
- `feather use rama-prod` when the source has multiple databases sets the source but leaves the database slot empty; commands then require an explicit database in their argument.
- `feather use --clear` removes the current context.

#### Contract
```bash
feather use <source>[.<database>]
feather use                          # show current
feather use --clear
feather use --json
```

Exit codes: `0` success; `1` source or database not registered.

### 6.5 `feather sample`

`feather sample` is load-bearing for shape-inspection in this MVP (since `feather profile` is deferred). Its auto-sizing behaviour matters.

#### Requirements
- Connect to a source via the current context (or via an explicit source-prefixed argument).
- Return up to N rows from a table reference. Default N = 100.
- **Auto-detect table size and pick the right strategy:**
  - **Small tables** (under a configurable row threshold, default 1M): use `LIMIT N` — returns the first N rows. Fast, deterministic.
  - **Huge tables** (above the threshold): use driver-appropriate **representative sampling** (e.g. `TABLESAMPLE` on Postgres / SQL Server, `ROWS RANDOM()` on SQLite + LIMIT). Returns up to N rows drawn from across the table, not just the first page.
  - The strategy used is printed to stderr and recorded in the log entry. Operator can override with `--method limit` or `--method sample`.
- Auto-save the full result to a CSV under `explore/<date>/<time>-<label>.csv`.
- Auto-save the generated SQL alongside the CSV.
- Append an entry to the daily exploration log (`explore/<date>/log.csv`).
- Render a terminal-friendly preview when interactive.
- Render machine-readable JSON when `--json` is set.
- Print a `Using: <source>.<database>` banner on stderr.

#### Acceptance criteria
- `feather sample sales` (with current context set) returns up to 100 rows and saves to `explore/<date>/<time>-sample-sales.csv`.
- For a small table, the CSV contains the first 100 rows (or all rows if fewer than 100); the stderr banner says `method: limit`.
- For a 100M-row table, the CSV contains up to 100 rows sampled across the table; stderr banner says `method: sample` and reports the sampling fraction used.
- `--method sample` on a small table is honoured (no auto-downgrade); operator gets what they asked for.
- With no current context and an unqualified table name, exits 1 with a message naming the missing qualifier.
- Table size check is cheap (catalog lookup or fast COUNT — no full scan) and fails open: on error, defaults to `limit`.

#### Contract
```bash
feather sample [<source>.<database>.]<table> [--limit N] [--method limit|sample] [--threshold ROWS] [--label NAME] [--json]
```

Defaults: `--limit 100`. `--threshold 1000000` (tunable per source via `sources.<name>.sample_threshold` in `feather.yaml`). `--method auto` (decides limit-vs-sample by threshold). `--label` defaults to `sample-<table>`.

Edge cases:
- Table doesn't exist → exit 1 with Levenshtein suggestion.
- Source connection fails → exit 1; partial log entry written.
- Driver doesn't support efficient sampling → falls back to `LIMIT N` with a warning on stderr.
- Table is empty → CSV has header only; exit 0.

### 6.6 `feather profile` — DEFERRED from MVP

The real session never used profile. Operators came with context from `inspect-powerbi-report` (or analyst conversation), and dim-discovery work was done via catalog SQL (`information_schema.COLUMNS`, `sys.columns`, etc.) through plain `feather sql`. `feather sample` covered row-shape inspection.

**Decision:** drop `feather profile` from MVP. Reserve as a future **skill** (`profile-unfamiliar-table`) that composes `feather sample` + a fixed `feather sql` block against the source's catalogue. Promote to a first-class verb only if the skill ceiling is hit and the pattern proves recurring across operators.

**Reasoning:**

1. **The fresh-discovery case is narrow.** Most explore-phase work comes from a reference dashboard with known column semantics. The "I'm landing on a 97-column table blind" case is real but uncommon — `inspect-powerbi-report` typically removes it.
2. **A skill can compose the primitives.** `feather sample` (LIMIT/sample), `feather sql` against `information_schema.COLUMNS`, then a small narrative ranking — that's the whole profile feature, as a skill.
3. **Asymmetric cost.** Adding a verb later if a skill proves clunky is cheap. Removing one after operators wire pipelines to its CSV output is expensive.
4. **Verb-count discipline.** 10 verbs in MVP (down from 11) — every one earned by the real session's evidence.

**What was the `--hint sales,amount,...` keyword-and-shape scoring vocabulary** (Money / Time / Identifier / Text boost table from earlier drafts) — kept here as a design note for the future skill, not committed as a verb feature:

| Family | Triggers | Boost type |
|---|---|---|
| Money | sales, amount, total, price, value, sum, qty, count, quantity | numeric +2 |
| Time | date, time, modified, created, updated | timestamp +2 |
| Identifier | id, code, key | int / short varchar +1 |
| Text | name, desc, description, address | varchar +1 |

If/when the future skill is built, this is its starting vocabulary.

### 6.7 `feather sql`

#### Requirements
- Execute arbitrary **read-only** SQL against a named source.
- Accept SQL from a file (`--file`) or inline (`--inline`).
- Auto-save both SQL and result to `explore/`.
- Reject mutating statements (INSERT / UPDATE / DELETE / DROP / TRUNCATE / etc.) at parse time.
- Honour `--timeout SECONDS` (default 60s; configurable per source).
- Resolve credentials and env vars from the source.
- Support DDL-retrieval queries (`OBJECT_DEFINITION()`, `pg_views`, `sys.columns`, etc.) — these are read-only and useful for capturing view definitions into the tally folder.

#### Acceptance criteria
- `feather sql rama-prod --file q.sql` writes both `<timestamp>-<label>.sql` and `.csv` to today's explore directory.
- `feather sql rama-prod --inline "UPDATE ..."` exits 1 at parse time, before connecting.
- A query exceeding `--timeout` is cancelled; no partial CSV.
- `feather sql rama-prod --inline "SELECT OBJECT_DEFINITION(OBJECT_ID('dbo.MyView'))"` returns the view DDL as a CSV (one cell). Operator can move that CSV into the tally folder as `view_MyView.sql`.

#### Contract
```bash
feather sql <source> --file PATH [--label NAME] [--timeout SECONDS] [--max-rows N] [--json]
feather sql <source> --inline "SELECT ..." [--label NAME] [...]
```

Edge cases:
- Long-running query → timeout enforced; no partial save.
- 10M-row result → warning + optional `--max-rows N` cap.
- Empty result → CSV has header only.

### 6.8 `feather compare`

#### Requirements
- Diff two CSVs row-by-row, matching on a specified key column (or set).
- Report per-row: key, left value, right value, abs diff, rel diff, within-tolerance flag.
- Tolerance: absolute (`--tolerance-abs`) or relative (`--tolerance`).
- Handle column-order differences (match by name, not position).
- Handle missing rows on either side (null on the missing side).
- Auto-save the diff CSV; terminal shows summary.
- **Tolerance default by reference data type:** if the baseline CSV is integer-typed (e.g. PBI underlying-data export uses `#,0` integer rounding), default is `--tolerance-abs 1`. If fractional/decimal, default is exact (`0.0`). Operator can override either way. (This was an empirical finding from the manual rehearsal — see `agent_log.md` §12.)
- **Type detection.** If a `<base>.meta.yaml` sidecar exists (see §6.10), the operator-declared tolerance wins. Otherwise: scan the first N rows of each numeric column in the baseline CSV; if every value parses as an integer, treat as integer-rounded (default `--tolerance-abs 1`); else fractional (default `0.0`). The chosen mode is printed on stderr in the run banner.

#### Acceptance criteria
- Identical CSVs → all rows pass, exit 0.
- One row's value differs by 2% with `--tolerance 0.01` → that row marked `within_tolerance=false`, exit 1.
- One CSV missing a key the other has → that key reported with the missing side as null, exit 1.
- CSVs with different columns → exit 3 with structural mismatch.
- Integer-typed baseline → default tolerance is ±1 per row; documented in command output.

#### Contract
```bash
feather compare <left.csv> <right.csv> --key COL[,COL2] [--tolerance PCT] [--tolerance-abs N] [--label NAME] [--json]
```

Diff CSV:
```csv
branch,left,right,diff_abs,diff_pct,within_tolerance
Bangalore,1156800.00,1156800.00,0.00,0.00,true
Mumbai,1450200.00,1432500.00,17700.00,0.01236,false
```

Exit codes: `0` all match; `1` drift detected; `3` structural mismatch.

### 6.9 `feather explore log` / `replay`

#### Requirements

**`log`** lists ad-hoc exploration entries from a date range. Default: today. Reads from `explore/<date>/log.csv`. Filterable by source, kind, label.

**`replay`** re-runs a previously saved exploration. Takes a label or timestamp; locates the matching SQL; re-executes against the same source; saves a NEW result CSV (does not overwrite history).

Note: `explore/` holds **ad-hoc** queries — quick samples, one-off SQLs, scratch work. Anything that belongs to an investigative session sits in a `tally/<event>/` folder (versioned, durable). The two stay separate.

#### Acceptance criteria
- `feather explore log` prints today's entries chronologically.
- `feather explore log --since 2026-05-10 --kind sql` filters correctly.
- `feather explore replay attempt-1` produces a new CSV with `-replay` suffix; original untouched.
- `feather explore replay bad-label` exits 1 with closest-match suggestion.

#### Contract
```bash
feather explore log [--since DATE] [--source NAME] [--kind sample|profile|sql|compare] [--label SUBSTR] [--json]
feather explore replay <label|timestamp> [--date DATE]
```

### 6.10 `feather tally`

The phase's headline verb. **Reshaped as hypothesis-iteration first; drift detection is the single-version degenerate case.**

#### Concept

A **tally folder** is one investigative session pointed at a real event (a date's sales report, a month's revenue dashboard, etc.).

**Who creates what.** The folder, the baseline CSV, the probe SQL files, and the analysis MDs are all **operator-authored** (or skill-generated — `reconcile-bi-baseline` typically scaffolds these). `feather tally` does not create folders, baselines, or probes. It **consumes** those inputs and **produces** the run outputs (`_actuals_vN.csv`, `_diff_vN.csv`, log entries). This separation is deliberate: authorship is judgment; running is mechanical.

**One tally per date** (convention, not enforced). A tally folder is scoped to a single point-in-time reference — e.g. `store_sales_2026_05_11` validates that specific day's PBI export. To validate another day, the operator copies the folder, renames the date, and changes the literal in the probe SQL. Date-parameterised probes (`--param date=2026-05-12`) are **out of scope for MVP** — feature creep that doesn't pay for itself when folder-copy is one shell command. Conventional folder name: `<event>_<YYYY_MM_DD>` (e.g. `store_sales_2026_05_11`). Not validated; soft convention.

**Optional `<base>.meta.yaml` sidecar** declares per-baseline metadata (key columns, tolerance). When present, it wins over flags and auto-detection:

```yaml
# tally/<event>/<base>.meta.yaml
key: [Short Name, Director Head, MainCategory, Category, SubCategory]
tolerance:
  TotSalQty: { abs: 1 }
  TotSalval: { abs: 1 }
notes: "Underlying-data export — integer rounded per row."
```

Absent → tolerance auto-detected from baseline column types (§6.8), key inferred from first column. Present → operator's choice wins. Small surface; eliminates re-passing `--key` and `--tolerance` on every invocation.

The folder contains:

- One or more **baselines** — reference CSVs from Power BI / Tableau / the client (e.g. `storewise_summary.csv`, `storewise_underlying.csv`).
- For each baseline, a series of **versioned probe quadruples** — the iteration trail. Each quadruple is four files sharing a base + version:
  - `<base>_probe_vN.sql` — the SQL for version N
  - `<base>_actuals_vN.csv` — output of running the probe (auto-overwritten on re-run of the same version)
  - `<base>_diff_vN.csv` — diff vs baseline (auto-overwritten on re-run)
  - `<base>_analysis_vN.md` — operator/skill-written narrative (manual, never overwritten)
- **Supplementary artefacts** — captured DDL (`view_<name>.sql`), dim-discovery dumps (`dim_discovery.json`), notes.
- A **`README.md`** documenting what's in the folder and the conventions used.
- An **`agent_log.md`** capturing the chronological record of the session — what was tried, what was found, what's still open.
- A **`log.csv`** tracking every `feather tally` run (timestamp, baseline, version, status, max drift).

The folder is named for the event (e.g. `store_sales_2026_05_11`), not for any one baseline.

#### Requirements

- `feather tally <event> <base>` runs the **latest version** of `<base>`'s probe SQL against the source resolved from the current context (or `--source`), compares the result against `<base>.csv` using the key+tolerance rules, and writes (or overwrites) `<base>_actuals_v<N>.csv` and `<base>_diff_v<N>.csv` where N is the latest version.
- **"Latest version" rule.** Highest N for which a `<base>_probe_vN.sql` file exists, regardless of gaps. If v1 and v3 exist but v2 doesn't, latest = v3. The gap is the operator's choice; feather doesn't enforce contiguity.
- `feather tally <event> <base> --version N` runs version N specifically.
- `feather tally <event> <base> --new-version` creates the slot for the next version. If a prior `_probe_vN.sql` exists, copies it to `_probe_v(N+1).sql` as a starting template (operator edits). If no prior version exists, creates a blank `_probe_v1.sql` — operator types from scratch. No run.
- `feather tally <event> <base> --all-versions` runs every version sequentially (regression check across the iteration trail). Versions run independently — a v2 failure does NOT stop v3. Overall exit code is 0 only if every version passes; 1 if any version drifts; 3 if any has a structural mismatch. Per-version status table printed at the end.
- `feather tally <event> --all` runs every baseline in the folder (latest version of each).
- `feather tally <event>` (no baseline arg) is an **error**: exit 1 with message "Specify a baseline (`feather tally <event> <base>`) or use `--all`/`--list`." Explicit beats magic.
- `feather tally --all` runs every tally folder in the project.
- `feather tally <event> --list` enumerates baselines and versions with last-run status.
- Append a row to `tally/<event>/log.csv` for every run.
- Re-runs of the same version overwrite the actuals and diff CSVs but never the analysis MD or the probe SQL.
- **Key and tolerance resolution order** (first match wins):
  1. CLI flag (`--key`, `--tolerance`, `--tolerance-abs`).
  2. `<base>.meta.yaml` sidecar in the tally folder.
  3. Auto-detect from baseline (key = first column; tolerance per §6.8).

#### Acceptance criteria

- A folder with `<base>.csv` + `<base>_probe_v1.sql`, run via `feather tally <event> <base>`, produces `<base>_actuals_v1.csv`, `<base>_diff_v1.csv`, and a row in `log.csv`.
- A second invocation with the same v1 probe overwrites the actuals and diff CSVs (re-run for drift); log.csv grows.
- After operator adds `<base>_probe_v2.sql`, `feather tally <event> <base>` (no version flag) detects v2 as latest and writes `_actuals_v2.csv` + `_diff_v2.csv` without touching v1's artefacts.
- With v1 and v3 present but v2 missing, `feather tally <event> <base>` runs v3 (max-N, gaps allowed).
- `feather tally <event>` (no baseline) exits 1 with an explicit error pointing at `--all`, `--list`, or a baseline name.
- `feather tally <event> --list` shows each baseline with its highest version and last-run status (see example below).
- `feather tally <event> <base> --all-versions` runs every version; exits 0 only if all pass.
- `feather tally <event> <base> --new-version` on a folder with no prior probes creates a blank `_probe_v1.sql`; on a folder with prior versions, copies the latest as `_probe_v(N+1).sql`.
- Numbers match within tolerance → exit 0, status `pass`. Drift → exit 1. Structural mismatch → exit 3.
- The analysis MD (`<base>_analysis_vN.md`) is **never written by feather** — only by the operator or a skill.

#### Verb-composability contract

`feather tally <event> <base>` is equivalent to:

```bash
feather sql <source> --file tally/<event>/<base>_probe_vN.sql --label tally-<event>-<base>-v<N>
# explore/<date>/<time>-tally-<event>-<base>-v<N>.csv is produced

feather compare tally/<event>/<base>.csv <that-csv> --key <from-meta-or-first-col> [tolerance flags from meta]
# explore/<date>/<time>-compare-... is produced
```

**…with one structural difference:** `tally` writes its run outputs into `tally/<event>/` (as `<base>_actuals_vN.csv` and `<base>_diff_vN.csv`), not into `explore/`. The actuals and diff content are byte-identical to what the manual two-command sequence would produce in `explore/`, modulo destination path and filename.

This composability is a design check. An integration test (`tests/integration/test_tally_composability.py`) should assert it: run the manual two-command sequence, run `feather tally`, diff the resulting CSVs ignoring path — they must match.

#### `feather tally <event> --list` output

```
$ feather tally store_sales_2026_05_11 --list

baseline                       latest_v  last_status  rows                   last_run
storewise_summary              v3        pass         17/17 within ±0.01     2026-05-15T12:30
storewise_underlying           v4        pass         6994/7029 within ±1    2026-05-16T09:01
```

`--json` produces the same data as an array of objects.

#### Contract

```bash
feather tally <event> <base> [--version N] [--source NAME] [--tolerance PCT] [--key COL] [--json]
feather tally <event> <base> --new-version             # create slot for next version
feather tally <event> <base> --all-versions            # run every version of one baseline
feather tally <event> --all                            # run every baseline in this folder
feather tally <event> --list
feather tally --all                                    # run every tally folder
```

Folder layout (real session):
```
tally/store_sales_2026_05_11/
├── README.md                                # docs what's here, conventions
├── agent_log.md                             # chronological session record
├── storewise_summary.csv                    # baseline #1 (16-row branch totals)
├── storewise_summary_probe_v1.sql           # v1 hypothesis: raw dbo.Sales
├── storewise_summary_actuals_v1.csv         # v1 actuals
├── storewise_summary_diff_v1.csv            # v1 diff (showed +0.4-5% per branch)
├── storewise_summary_analysis_v1.md         # v1 narrative
├── storewise_summary_probe_v2.sql           # v2 hypothesis: Sales − SalesCredit
├── storewise_summary_actuals_v2.csv
├── storewise_summary_diff_v2.csv            # v2 diff (all zeros — match!)
├── storewise_summary_analysis_v2.md
├── storewise_summary_probe_v3.sql           # v3 confirmation: query the dev's view
├── storewise_summary_actuals_v3.csv
├── storewise_summary_diff_v3.csv
├── storewise_summary_analysis_v3.md
├── storewise_underlying.csv                 # baseline #2 (7,029-row category grain)
├── storewise_underlying_probe_v1.sql
├── ... (4 versions of the underlying track)
├── view_AllSales_Zakya_Grouped.sql          # captured DDL (from feather sql + move)
├── dim_discovery.json                       # supplementary: dim-search output
└── log.csv                                  # every run across every baseline+version
```

`log.csv`:
```csv
timestamp,baseline,version,status,rows_matched,rows_total,max_drift_pct,notes
2026-05-15T10:02:11,storewise_summary,1,fail,0,17,0.0540,systematic +bias
2026-05-15T11:14:03,storewise_summary,2,pass,17,17,0.0000,Sales-SalesCredit
2026-05-15T12:30:55,storewise_summary,3,pass,17,17,0.0000,via AllSales view
2026-05-16T07:42:18,storewise_underlying,1,fail,4046,7029,N/A,42% key mismatch
2026-05-16T09:01:24,storewise_underlying,4,pass,6994,7029,N/A,99.5%; 35 DAX exceptions
```

Edge cases:
- Folder doesn't exist → exit 1 with closest-match.
- Folder exists but no `<base>.csv` → exit 1.
- `<base>.csv` exists but no `<base>_probe_v*.sql` → exit 1 with hint to create one.
- Operator wants to refresh a baseline (PBI numbers changed) → hand-edit `<base>.csv`. Feather never auto-updates baselines.
- Hypothesis iteration that crosses sources (e.g. join Zakya + MySQL) — see §7.5 cross-server pattern.

#### Relationship to drift detection

Drift = single version, run repeatedly. The same machinery handles it: `feather tally <event> <base>` re-runs v1 (no v2 exists), overwriting `_actuals_v1.csv` / `_diff_v1.csv` each time, and `log.csv` accumulates the history. A nightly cron is one `feather tally --all` invocation. No second verb needed.

---

## 7. Cross-cutting requirements

### 7.1 The exploration log

**Auto-save.** Ad-hoc `feather sample` / `feather profile` / `feather sql` / `feather compare` writes to `explore/<date>/` without any flag. The save IS the behaviour.

**Layout:**
```
explore/
  2026-05-15/
    09:14:12-sample-sales.sql
    09:14:12-sample-sales.csv
    09:17:42-profile-sales.csv
    09:32:01-attempt-1.sql
    09:32:01-attempt-1.csv
    09:33:15-compare-attempt-1.csv
    log.csv
```

**Gitignore.** `explore/` is gitignored. `tally/` is **always** committed.

**File naming.** `<HH:MM:SS>-<label>.{sql,csv}`. Labels are sanitised (lowercase, underscores). Same-second collisions: append `-2`, `-3`.

**`tally/` vs `explore/` — what goes where.** Ad-hoc queries → `explore/`. Anything tied to a saved baseline + hypothesis iteration → moved into a `tally/<event>/` folder. The operator (or a skill) chooses when to graduate from explore to tally; feather doesn't force it.

### 7.2 Agent compatibility

Every command in §6 must:

1. **Have a `--json` output mode.** Default is human-readable; `--json` is machine-readable and complete. This is the **interface skills depend on**.
2. **Have no required interactive prompt.** Every prompt has a flag equivalent.
3. **Return distinct exit codes:** `0` success, `1` error, `3` needs decision (drift, structural mismatch, ambiguity).
4. **Never modify source data.** Explore is read-only on sources, write-only to `explore/` / `tally/`.
5. **Be idempotent in harmless cases.** Re-running `feather tally <event> <base>` with the same version overwrites actuals/diff (deliberate); never destroys probe SQLs, baselines, or analysis MDs.

### 7.3 File formats

| Use | Format |
|---|---|
| Reference baselines | CSV |
| Probe outputs (actuals) | CSV |
| Diff outputs | CSV |
| Tally log (`tally/<event>/log.csv`) | CSV |
| Daily exploration log | CSV |
| Analysis narratives | Markdown |
| Tally README + agent_log | Markdown |
| Feather project config | YAML |
| Per-source secrets reference | `.env` |
| Current context | YAML (`.feather/state.yaml`) |
| Captured DDL | `.sql` (raw text) |
| Supplementary discovery dumps | JSON (machine output of `feather sql` against system catalogues) |

**No JSON in project state.** JSON is a transient `--json` output for machine consumers, plus a permissible format for supplementary discovery dumps (sys.objects / sys.columns / metadata).

### 7.4 Performance

- `feather sample`: auto-decides LIMIT vs TABLESAMPLE by table size (default threshold 1M rows). Default N = 100. Strategy reported on stderr + in log. (See §6.5.)
- `feather sql`: `--timeout SECONDS` (default 60). Configurable per source.
- `feather compare` / tally diff: in-memory for under ~1M rows; streaming via DuckDB above.

### 7.5 Connection management, source addressing, and cross-server joins

- All commands read source connection from `feather.yaml`. Env var interpolation (`${VAR}`) resolved at command time.
- No `--host` / `--user` / `--password` flags on explore commands.
- Addressing forms:
  - Fully qualified: `<source>.<database>.<table>`.
  - Partially qualified: `<database>.<table>` (source from current context).
  - Unqualified: `<table>` (source + database from current context).
- For sources without a database concept (CSV, JSON, single-file DuckDB): `<source>.<table>`.

**Cross-server joins.** `feather sql <source>` is single-source; cross-database joins **within** a source are fine (the rama_dw session joined Zakya + SAP databases via one SQL Server source). For joins **across** sources (e.g. MySQL ↔ SQL Server), the pattern is **load-then-join in DuckDB**:

1. Run a probe SQL against each source via `feather sql --source A` and `feather sql --source B`, saving each result.
2. Load both CSVs into the local DuckDB working file and join there in a follow-up probe.

Source-level federation (cross-server SQL JOIN at engine level) is **not supported** in MVP and not planned. DuckDB-as-junction is the canonical pattern.

### 7.6 Current source context

**Storage.** `.feather/state.yaml` (project-local, gitignored):
```yaml
current:
  source: rama-prod
  database: Zakya
```

**Who sets it.** `feather source add` (unless `--no-set-current`); `feather use <source>[.<database>]`; `feather use --clear`.

**Display.** Every source-touching command prints `Using: <source>.<database>` on stderr.

**Resolution order:**
1. Fully-qualified argument → use as-is.
2. Partial → fill from current.
3. Unqualified → fill from current.
4. `--source` flag → overrides for this invocation only.

If nothing resolves and no qualifier given: exit 1 with `No source set. Run 'feather use <source>.<database>' or pass a fully-qualified reference.`

### 7.7 Project layout

Full tree of a feather project after `feather init` and a working explore session:

```
<project-root>/
  feather.yaml                                # source connections, project defaults
  .env                                        # secrets (gitignored)
  .gitignore                                  # includes .env, explore/, .feather/, *.duckdb
  .feather/
    state.yaml                                # current source/database context (gitignored)
  explore/                                    # ad-hoc queries (gitignored)
    2026-05-15/
      09:14:12-sample-sales.{sql,csv}
      09:32:01-attempt-1.{sql,csv}
      log.csv
  tally/                                      # investigative sessions (committed)
    store_sales_2026_05_11/
      README.md
      agent_log.md
      storewise_summary.csv                   # baseline #1
      storewise_summary.meta.yaml             # optional key/tolerance sidecar
      storewise_summary_probe_v1.sql          # version 1
      storewise_summary_actuals_v1.csv
      storewise_summary_diff_v1.csv
      storewise_summary_analysis_v1.md
      storewise_summary_probe_v2.sql          # version 2
      ...
      view_AllSales_Zakya_Grouped.sql         # captured DDL
      log.csv                                 # run history across baselines/versions
  tables/                                     # curated tables for the pipeline (committed; out of scope this phase)
```

### 7.8 Error messages

- Every error names the file or command that caused it.
- Levenshtein suggestions on typos (table names, source names, label references, baseline names, event names).
- Multi-error batch reporting where applicable.
- Error codes documented in `feather <command> --help`.

---

## 8. What's NOT in this phase (boundaries)

Deferred to later phase docs:

- **Curation.** Deciding what to extract long-term (lives in `tables/...`). Explore informs curation; doesn't write it.
- **Bronze extraction.** No data moves to the warehouse during explore.
- **Silver / gold transforms.** No SQL files at `tables/<name>/silver.sql`. The probe SQL produced in explore is for **validation**, not for production transforms.
- **Watermarks / incremental.** Not relevant — explore is one-shot reads.
- **DAG / dependency analysis.** Not relevant.
- **Tally against bronze / silver / gold.** The tally folder grows additional `_probe_vN.sql` files in later phases; the verb stays the same.
- **Skills themselves.** Four named in §5.1; their design lives in separate docs.

Deferred from MVP, reserved as future work:

- **`feather profile` (entire verb).** Real-session evidence says compose-as-skill first (§6.6). Adds back if skill ceiling is hit.
- **`feather profile --hint` keyword/shape vocabulary.** Kept as a design note inside §6.6 for the future skill.
- **`feather source test --update-databases` drift handling.** Drift is operate-phase concern (§6.3).
- **Cross-server SQL federation.** Use load-then-join in DuckDB instead (§7.5).
- **DAX text extraction in `inspect-powerbi-report`.** Requires `Dataset.Read.All` against XMLA. Skill is honest about this gap; the reconcile chain reproduces numbers empirically without needing DAX text.

---

## 9. Open questions

Genuinely unresolved. Decisions previously listed as open but answered in the PRD body have been moved to "Decisions captured" at the bottom of this section.

1. **What counts as "read-only" SQL?** Is `SELECT INTO #temp` allowed? CTEs and window functions: yes. Stored procedures: probably no. Driver-specific.

2. **Per-tally `log.csv` schema.** Real session captured timestamp + baseline + version + status + match counts + drift + notes. Lock the schema in the design phase or leave operator-extensible? Lean: lock, with a future `feather tally log --format ...` if extensibility ever needed.

3. **Captured DDL — naming convention.** Real session used `view_<viewname>.sql`. Codify in the PRD? Or leave operator-chosen?

4. **Skill ↔ verb JSON schema.** Skills call verbs with `--json`. Should each verb's output have a published JSON schema (versioned)? Probably yes; defer specifics until skill re-implementation begins (post-step-8).

5. **Threshold default of 1M for `feather sample`.** Driver semantics for `COUNT(*)` and `TABLESAMPLE` vary widely. Design phase picks the actual value; PRD just says "tunable per source."

### Decisions captured (previously open)

- **Tolerance metadata sidecar.** Committed in MVP. `<base>.meta.yaml` is optional; when present it wins. Absent → auto-detect by column type. See §6.10 and §6.8.
- **Tolerance default by reference data type.** Integer baseline → `--tolerance-abs 1`. Fractional → `0.0`. See §6.8.
- **Cross-track diff inside a tally folder.** No first-class support. Tracks are independent; cross-track narrative lives in operator/skill-written MDs.
- **Tally event-name convention.** Soft convention `<event>_<YYYY_MM_DD>`. Not validated; operator-chosen. See §6.10.
- **Probe SQL evolution on `--new-version`.** Copies prior version as a template. If no prior exists, creates a blank `_probe_v1.sql`. See §6.10.
- **Analysis MD template.** Enforced by the `write-tally-analysis` skill (status → highlights → conclusion → open items → next-version plan). Operator may override post-write. Feather never writes the MD.
- **Replay safety.** `feather explore replay` re-runs against live source and saves with `-replay` suffix on the label. Never snapshots. See §6.9.
- **Latest version with gaps.** Highest N where `_probe_vN.sql` exists. Gaps allowed. See §6.10.
- **`feather tally <event>` (no baseline).** Error, exit 1. Explicit beats magic.
- **One tally per date.** Folder is scoped to one point-in-time reference. Multi-date validation = copy folder. Date-parameter substitution out of MVP.
- **Verb composability.** `feather tally` is equivalent to `feather sql` + `feather compare` modulo destination path. Tested in integration suite.

---

## 10. What we do next

The manual rehearsal proved the verb design is fundamentally correct and the shape (hypothesis iteration, multi-baseline tally folders, skills layer) was the real surprise. The plan:

### Build order (sequential workflow)

The order an operator (or agent) executes verbs when walking through the explore phase end-to-end. Each verb consumes the prior's output; the tool is meaningfully usable at every milestone.

1. **`feather init`** — scaffold a project. Required first in a fresh repo.
2. **`feather source add`** — register the source, test connection, list databases. Sets the current context as a side effect.
3. **`feather source test`** — connection re-check. Built alongside source add; they share helpers (connection probe, database listing). Pragmatic deviation from strict workflow order to amortise shared work.
4. **`feather use`** — explicit context switching (set / show / clear).
5. **`feather sample`** — first read against a registered source. Row-shape inspection. Auto-LIMIT vs TABLESAMPLE per §6.5.
6. **`feather sql`** — run arbitrary read-only SQL. Powers every probe in the hypothesis loop.
7. **`feather compare`** — diff two CSVs by key with tolerance. Powers the diff step.
8. **`feather tally`** — wraps `feather sql` + `feather compare` into the versioned hypothesis-iteration shape. The headline verb; full explore-phase workflow lands here.
9. **`feather explore log`** — observability over the daily exploration corpus.
10. **`feather explore replay`** — re-run a previously saved exploration. Smallest verb; ships last.

10 verbs total.

**Milestones along the way:**

- **After step 5 (sample):** project is connectable. Operator can register sources, switch context, peek at tables. First usable shipment.
- **After step 7 (compare):** the hypothesis-iteration loop is **manually executable** — operator (or skill) creates the `tally/<event>/` folder by hand, drops the baseline, authors `<base>_probe_v1.sql`, runs it via `feather sql`, runs `feather compare` against the baseline, and moves the outputs into the folder. The real session worked this way for the duration of the rehearsal. Clunky but everything-in-feather; most of the rehearsal's value is unlocked here.
- **After step 8 (tally):** the move-files + invoke loop collapses to a single `feather tally <event> <base>`. Folder creation, baseline placement, and probe SQL authorship remain operator/skill responsibility (§6.10 "Who creates what"). The PRD's headline shipment.
- **Steps 9-10:** polish + return-visit ergonomics.

**Who does what across the build sequence:**

| Activity | Operator / skill | Feather |
|---|---|---|
| Create `tally/<event>/` folder | Always | Never |
| Drop `<base>.csv` baseline | Always | Never |
| Author `<base>_probe_vN.sql` | Always | Never |
| Author `<base>_analysis_vN.md` | Always | Never |
| Run probe SQL against source | Pre-step-8: manual via `feather sql` | Post-step-8: via `feather tally` |
| Diff actuals vs baseline | Pre-step-8: manual via `feather compare` | Post-step-8: via `feather tally` |
| Auto-number / version run outputs | Pre-step-8: manual file moves | Post-step-8: via `feather tally` |
| Maintain `log.csv` in tally folder | Pre-step-8: manual / skill | Post-step-8: via `feather tally` |

The skills layer (§5.1) generates Python sidecar scripts today to cover the "manual" column. Those templates collapse to CLI invocations as soon as the corresponding verb ships — `feather sql` and `feather compare` after steps 6-7, then full collapse after step 8.

**Follow-up milestone (not a blocker for shipping verbs):** after step 8 lands, the four committed skills (`inspect-powerbi-report`, `reconcile-bi-baseline`, `write-tally-analysis`, `resolve-dim-mapping`) are re-implemented to call CLI invocations and the Python sidecar templates are deleted. Treat this as one cleanup pass post-step-8 — schedule it before step 9 ships, to prevent the drift where verbs exist but skills still write Python files.

### Deferred

- **`feather profile` (entire verb).** Compose as skill first; promote later if needed (§6.6).
- **`feather source test --update-databases` drift handling.** Operate-phase concern.
- **Cross-server SQL federation.** Load-then-join in DuckDB.
- **Skill implementations** — four skills already exist (`inspect-powerbi-report`, `reconcile-bi-baseline`, `write-tally-analysis`, `resolve-dim-mapping`) as Python sidecars that generate connectorx-wrapper scripts. They collapse to clean CLI invocations once verbs 1-4 ship. Skill internals get their own design docs.

### What this PRD doesn't decide

- How any verb is implemented (CLI parser, code structure, test surface) — that's the **design doc**, separate file.
- Skill internals — separate docs per skill.

The manual rehearsal was the cheapest possible validation; it gave us a real test against the requirements. The PRD's bones survived; the assumptions and shape needed updating. We're now ready for the design phase.
