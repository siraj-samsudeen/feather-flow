# Per-table project state — design review

Created: 2026-05-13
Status: DRAFT — for review
Triggered by: [#58](https://github.com/siraj-samsudeen/feather-etl/issues/58) (bronze column projection); supersedes the implementation direction in `docs/superpowers/specs/2026-05-12-bronze-projection-design.md`
Reviewers: human reviewers + agent reviewers — please engage with each decision independently and flag what we missed.

---

## 0. How to read this doc

This is an RFC, not a tight implementation spec. The intent is to put each design choice on the table with its alternatives, so reviewers can push back on individual decisions without having to accept the whole proposal as a package.

Each numbered decision in §3 is structured as:

- **Question** — the fork in the road.
- **Alternatives considered** — what we looked at.
- **Chosen** — what we landed on, and why.
- **Why not the next-best alternative** — counterfactual.
- **Reviewer-attention flags** — explicit prompts for what we want pushback on.

The proposed shape is in §4, walked through with concrete files in §5. Open questions we haven't answered yet are in §6 — reviewers especially welcome there.

---

## 1. Origin and motivation

Issue #58 asked for column-level projection at bronze extract: a wide source table (97 columns) where silver only consumes 32 columns, extracted as `SELECT *`, taking 3.5 hours and saturating the wire with bytes that get dropped one layer later. The original proposal added a `column_overrides` array to `discovery/curation.json`.

Working through the design surfaced a deeper friction that #58 alone could not resolve. The friction has three faces:

1. **`discovery/curation.json` fuses three concerns into one file.** Discovered facts (what tables and columns exist), table-level operator intent (include / strategy / PK / timestamp), and column-level operator intent (the #58 ask) all share one JSON manifest. Each concern has a different author and a different edit cadence.

2. **The single-JSON shape is failing for agent edits.** Agents populating curation.json for medium-sized projects are producing incomplete or hanging edits — the file grows linearly with project size and is too large for reliable structured editing. The pain shows up in real curation runs today.

3. **Operators are alienated from the project state.** Because curation.json is predominantly agent-authored, operators accept defaults rather than reading the file. The bronze table name (`<source_db>_<alias>` sanitised) lives there; operators don't engage with it; the original #58 proposal then forces them to learn that name to write a projection file. The friction surfaced as a design smell, not a coding bug.

The third face is the one that made `feather-etl`'s creator say "this issue forces me to rethink the workflow." This RFC is the rethink. The original #58 spec is paused while the structural questions are resolved.

The rethink touches:

- **State shape** — where project state lives on disk (file format, granularity, directory layout).
- **YAML / SQL boundary** — when an operator expresses intent in YAML vs SQL, and what compiles to what.
- **Operator journeys** — two distinct entrances ("I have a specific table I want" and "I don't know what's in the source") that should converge on the same destination shape.
- **Agent edit ergonomics** — file granularity that supports clean atomic edits without big-file failure modes.

---

## 2. Goals

After this lands, an operator with a specific table in mind can opt in to a bronze table in a single small YAML file, with no `feather discover` run required. An operator who runs `feather discover` produces the same kind of file (one per table) automatically, agent-assisted or by hand. The two entrances converge.

For each bronze table, an operator can:

- Subset columns to include or exclude. Identity-level. Stays in YAML.
- Rename columns and cast types at silver. Identity-level. Stays in YAML.
- Dedupe by key. Identity-level. Stays in YAML.
- Filter rows at extract, derive computed columns at silver, join other tables at silver or gold. Expression-level. Lives in SQL files.

A power user can edit any of these in any text editor without needing an agent assist. An agent edit touches one small file per change, with no single-big-JSON failure mode.

The current `discovery/curation.json` is deprecated as the project manifest. It survives only as an advisory output of the curate skill (recommendations, not state).

---

## 3. Decisions log

Each decision is a fork the reviewer can engage with independently.

### Decision 1 — Per-table state, not one global manifest

**Question.** Should project state live in one global file (status quo: `discovery/curation.json`) or split into per-table files?

**Alternatives considered.**

- **Global JSON** (`curation.json`) — current state. One file holds every table's intent.
- **Global YAML** — same shape, different format. Solves human-readability but not the size problem.
- **Per-table files** — one file per bronze table. Each file is small and atomically editable.
- **SQLite metadata DB** (issue #41) — rows in a typed schema, queryable via Datasette.

**Chosen.** Per-table files.

**Why not global JSON / YAML?** Agents fail on large structured edits to single files. Power users scrolling a 5,000-line manifest to find their table is a worse experience than opening `tables/zakya_sales/table.yaml`. Git diffs on per-table files are atomic — a rename of one table touches one file. Code review aligns with the natural unit of change.

**Why not SQLite now?** SQLite is the right end-state for queryability (#41 already proposes it). The migration from per-table files to SQLite rows is mechanical (each file becomes a row). The migration in the other direction is not mechanical. Per-table files first, SQLite later when there is concrete demand for cross-table queries. Doing both at once trebles the surface for this RFC.

**Reviewer attention.** Is there a project size at which per-table files become a problem (e.g., a 500-table warehouse with 500 files)? Should we plan for hierarchical subdirectories under `tables/` before that scale arrives?

### Decision 2 — Folder per table (feature folders) over flat layout

**Question.** Within the per-table model, are tables files or folders?

**Alternatives considered.**

- **Flat with naming convention** — `tables/zakya_sales.yaml`, `tables/zakya_sales.bronze.sql`, etc.
- **Folder per table, layer-named files** — `tables/zakya_sales/table.yaml`, `tables/zakya_sales/bronze.sql`, etc.
- **Folder per table with layer subdirectories** — `tables/zakya_sales/silver/sales.sql`. Overkill.

**Chosen.** Folder per table, layer-named files inside.

**Why not flat with naming convention?** A 100-table project produces a 300-file flat directory. Visual scan finds the table you want, but the cognitive grouping is by prefix sort, not by directory boundary. Atomic operations on a table (delete, copy, rename) become multi-file moves with naming guard rails. The folder boundary is a real signal.

**Why not subdirectories per layer?** Adds depth without value. Most tables have at most one file per layer; the directory adds a click for no information.

**Why this matches developer intuition.** The frontend "feature folder" pattern (`Button/Button.tsx` + `Button/Button.test.tsx`) and the Django app pattern (`posts/models.py` + `posts/views.py`) both flip the same axis. Working on one thing means opening one folder. Working across all silvers becomes a glob (`find tables -name silver.sql`), which is rare-case workflow and acceptable cost.

**Reviewer attention.** Does this map onto operator habits? Anyone reviewing who works in a data tool that does the opposite (e.g., Airflow's flat DAG directory) — is the layer-axis layout something you'd miss?

### Decision 3 — Filename / folder name is the identity, no separate `alias` field

**Question.** Where does the bronze table name live — in YAML as an `alias:` field, or as the folder name?

**Alternatives considered.**

- **Alias in YAML** — `tables/zakya_sales/table.yaml` contains `alias: zakya_sales`. Both could disagree.
- **Folder name is identity** — folder name IS the bronze table name. No alias field. Rename the folder, the table is renamed.

**Chosen.** Folder name is identity.

**Why.** Two sources of truth invite drift. The filesystem already provides a unique identifier; using it removes a class of bug. The convention is unambiguous (`tables/<bronze_table_name>/`) and operators recognise it immediately.

**Reviewer attention.** Are there filesystem characters that can appear in well-formed bronze names but cause issues on Windows or in tools? Today's sanitiser produces `[a-z0-9_]+` only, which is safe everywhere. If the sanitiser ever loosens, this assumption breaks.

### Decision 4 — YAML for identity, SQL for expressions

**Question.** When an operator expresses intent, when is YAML the right tool and when is SQL?

**Alternatives considered.**

- **YAML for everything (with embedded SQL strings)** — `filter: "Quantity > 0"`, `derives: { net: "qty * price" }`. Convenient but smuggles SQL into YAML as string values.
- **SQL for everything (dbt-style)** — even renames are `SELECT col AS new_name`. Loses the elegance of `{old: new}` maps and forces every layer through a SELECT body.
- **Identity / expression split** — YAML holds maps and lists of names and types (renames, includes, excludes, casts, dedupe keys). SQL holds anything that computes a value (filters, derives, joins, aggregations).

**Chosen.** Identity / expression split. Strict.

**Why.** The smell of embedding SQL strings in YAML is real — the operator now has to know YAML syntax AND SQL syntax in one file, and the parser has to special-case which string fields contain SQL. The strict split keeps each tool doing what it does best: YAML for declarative structure, SQL for the parts that need to compute.

**Why not the convenience hybrid?** The hybrid is what we tried first and called out as a rule violation. Once you allow one SQL string in YAML, the boundary rots. Strictness is the only stable line.

**Concrete rule.** Every YAML value is one of:
- a name (string identifier of a column / table / source / type)
- a list of names
- a `{name: name}` map
- a parameterless scalar (boolean, integer, etc.)

Anything that needs operators (`+`, `>`, `||`, function calls) goes to SQL.

**Reviewer attention.** Are there real silver/gold operations that fall into a third category — neither identity nor expression — that this rule fails to accommodate? For example, dynamic column generation based on metadata, or schema-driven type widening?

### Decision 5 — Numeric type defaults; operator types `decimal` not `decimal(18,3)`

**Question.** How should casts be expressed in YAML?

**Alternatives considered.**

- **Full type strings** — `casts: { quantity: "DECIMAL(18,3)" }`. Faithful but ugly.
- **Type names with defaults** — `casts: { quantity: decimal }` → engine expands to `DECIMAL(18,3)`. Operator can override with `decimal(20,4)` if they need it.
- **Reuse source types verbatim** — engine reads source column type and applies. Loses operator intent.

**Chosen.** Type names with defaults; operator overrides explicitly when needed.

**Why.** `DECIMAL(18,3)` for monetary quantities is the right default for the ERP cases this tool targets. Operators who care about precision specify it; everyone else types `decimal` and gets sane behaviour. This is a small ergonomic win that adds up across hundreds of cast declarations.

**Reviewer attention.** Default precision/scale for `decimal` — is `(18,3)` the right default for this project's ERP context, or should it be configurable at the project level? Also: TIMESTAMP defaults (with/without timezone), VARCHAR defaults (length cap), JSON handling — same question.

### Decision 6 — File presence indicates layer activity; no empty stub files

**Question.** How does the engine know whether a layer is active for a given table?

**Alternatives considered.**

- **Required stub files** — every table must have `silver.sql` (even if empty). Engine reads them all.
- **Explicit YAML markers** — `table.yaml` carries `silver: { active: true }` or `silver: { skip: true }`.
- **File presence** — if `silver.sql` or a `silver:` section in `table.yaml` exists, silver runs. If neither, no silver for this table.

**Chosen.** File presence. Layer is active iff at least one signal for that layer exists.

**Why.** Empty stub files are noise. Explicit markers add a third place to drift from. File presence is what an operator naturally reasons about ("does this table have a silver?").

**The operator pattern.** Layer config in `table.yaml` AND/OR layer SQL in `<layer>.sql`. Any of:

- YAML only — `table.yaml` has a non-empty `silver:` section. Compiles to a SQL SELECT.
- SQL only — `silver.sql` exists. Runs as written.
- Both — `silver.sql` runs; YAML identity transforms wrap its output as a CTE.
- Neither — no silver table is produced.

**Reviewer attention.** What is the failure mode when the operator intended a layer but forgot to add the file or YAML section? Should `feather` warn ("you've curated this table but never declared a silver — is that intentional?") or stay silent? This may need its own decision later.

### Decision 7 — SQL is the truth for dependencies; no `from:` field in YAML

**Question.** Where do cross-table dependencies live?

**Alternatives considered.**

- **YAML `from:` field** — `from: [bronze.zakya_orders, bronze.zakya_line_items]`. Explicit, declarative.
- **SQL FROM clauses parsed at load** — continues the issue #54 convention; `silver.sql` already has the FROM clauses, the engine parses them with sqlglot to infer edges.
- **Both** — `from:` mandatory, SQL FROM optional. Two sources of truth.

**Chosen.** SQL is the truth. No `from:` field in YAML.

**Why.** Issue #54 established that SQL is the single source for dependency information (auto-discovered DAG via sqlglot AST). Adding a YAML field that duplicates the SQL's FROM clauses re-opens the wound — operators now have to update both when they refactor a query. Keeping SQL authoritative honours #54.

**Reviewer attention.** For tables with no SQL file (pure-identity silver, or bronze-origin tables), how are dependencies inferred? Today: bronze-origin tables depend on the source declared in YAML, no SQL needed. Pure-identity silver: depends on the bronze of the same name (1:1 default). N:1 silver: requires a SQL file with FROM clauses. Is the "1:1 default" rule clear enough or does it need to be explicit in YAML?

### Decision 8 — `kind:` is required, not inferred

**Question.** Does `table.yaml` need an explicit `kind:` (bronze / silver / gold), or can the engine infer it from which layer files exist?

**Alternatives considered.**

- **Inferred** — if only `silver.sql` exists, kind is silver. If both `bronze.sql` and `silver.sql`, kind is bronze-origin with silver layer. Etc.
- **Required** — every `table.yaml` declares `kind:` at the top. Engine validates against what's present.

**Chosen.** Required.

**Why.** Inference is fragile. A bronze-origin table that hasn't grown a silver yet would be indistinguishable from a silver that depends on an unbuilt bronze. Explicit declaration is one line and removes the ambiguity. Validation can then point clearly at mismatches ("you declared `kind: gold` but there's a `bronze.sql` in this folder — pick one").

**Reviewer attention.** The set of valid `kind:` values — is bronze / silver / gold sufficient, or do we anticipate more (raw, quarantine, audit)? If extensible, what does the registration mechanism look like?

### Decision 9 — Bronze filter lives in SQL, not YAML

**Question.** Where do extract-time `WHERE` predicates live?

**Alternatives considered.**

- **YAML as an exception** — `filter:` is the one expression-shaped key allowed in YAML, justified because it runs in the source's dialect, not DuckDB's.
- **SQL file** — `bronze.sql` contains `SELECT [columns] FROM <source_table> WHERE <predicate>;`. Engine parses, sends WHERE to source.

**Chosen.** SQL file.

**Why.** Keeps the identity/expression rule pure. The cost is one optional file per bronze table that needs filtering. Bronze SQL files have a strictly limited grammar (`SELECT [* | column list] FROM <source_table> [WHERE <predicate>];`) — much tighter than silver/gold SQL — which lets the parser reject anything else clearly.

**Implementation note.** The column list in `bronze.sql` (if present) is redundant with `table.yaml`'s `bronze.columns.include`. The engine picks one source of truth: YAML wins for columns, SQL wins for WHERE. Or: bronze SQL must be `SELECT * FROM <source> WHERE ...` and the column projection is always YAML. The second rule is cleaner — bronze SQL is for predicates only.

**Reviewer attention.** Is this division mentally manageable, or should bronze stay entirely in YAML and accept that `filter:` is an exception? Note that `TableConfig.filter` already exists today as a YAML field; moving it costs migration.

### Decision 10 — `curation.json` becomes advisory output of the curate skill

**Question.** What happens to the existing `discovery/curation.json` file?

**Alternatives considered.**

- **Delete entirely** — replace with per-table YAMLs across the board. No backwards compat.
- **Keep as state, in parallel** — both shapes valid, engine reads either. Long migration window.
- **Deprecate as state, keep as advisory artefact** — the curate skill produces `curation_recommendations.json` as an output. Operator turns it into per-table YAMLs (agent-assisted or by hand). The recommendations file is not read by the engine.

**Chosen.** Deprecate as state, keep as advisory.

**Why.** The curate skill's value is the recommendations themselves (which tables to include, recommended PKs, recommended strategies). Capturing those as a JSON output that humans and agents review is the right thing. But that artefact is a recommendation, not project state. Project state is the per-table YAMLs.

**Reviewer attention.** Migration tool — `feather migrate-state` reads `discovery/curation.json` and emits the equivalent `tables/*/table.yaml` set. Is this enough, or should the migration also preserve the curation file as a frozen audit artefact?

---

## 4. Proposed shape — concrete

```
project-root/
  feather.yaml                        # sources, destination, global defaults (unchanged)
  discovery/
    schema_rama-dw.json               # feather discover output (unchanged)
    curation_recommendations.json     # NEW — advisory curate-skill output
  tables/                             # NEW — per-table feature folders
    zakya_sales/
      table.yaml                      # required: kind + identity config
      bronze.sql                      # optional: WHERE for extract
      silver.sql                      # optional: silver expressions
    zakya_branches/
      table.yaml
    orders_with_lines/                # N:1 silver: joins multiple bronzes
      table.yaml                      # kind: silver
      silver.sql                      # required for N:1 — has the JOIN
    facts_sales/                      # gold: joins silvers
      table.yaml                      # kind: gold
      gold.sql                        # required — gold is always SQL
```

The `tables/` directory is the project's state of truth. Each subfolder is one table. The folder name is the bronze table name (for bronze-origin tables) or the operator-chosen identifier (for derived silver / gold).

`feather.yaml` stays as is — connection configuration, destination path, global defaults. It does not list tables.

`discovery/` keeps producing the schema artefact (unchanged) and now also produces an advisory recommendations file. Nothing in `discovery/` is project state.

---

## 5. Walked-through examples

### 5.1 The rama-dw / Zakya / dbo.Sales case (bronze-origin with projection)

```yaml
# tables/zakya_sales/table.yaml
kind: bronze
source: rama-dw
database: Zakya
source_table: dbo.Sales
strategy: incremental
primary_key: [invoice_id]
timestamp_column: modified_at

bronze:
  columns:
    include: [Quantity, "Invoice Date", "Branch ID"]

silver:
  renames:
    "Invoice Date": invoice_date
    "Branch ID": branch_id
  casts:
    Quantity: decimal
    invoice_date: date
  dedupe: [invoice_id]
```

No `bronze.sql` — extract pulls only the listed columns, no WHERE. No `silver.sql` — silver is pure identity (renames + casts + dedupe), so the engine compiles it directly:

```sql
-- Generated silver SQL (operator never sees this unless they run `feather inspect`)
SELECT
    Quantity::DECIMAL(18,3) AS Quantity,
    "Invoice Date" AS invoice_date,
    "Branch ID" AS branch_id
FROM bronze.zakya_sales
QUALIFY ROW_NUMBER() OVER (PARTITION BY invoice_id ORDER BY 1) = 1;
```

### 5.2 Same lineage, but silver needs a filter and a derived column

```yaml
# tables/zakya_sales/table.yaml
kind: bronze
source: rama-dw
database: Zakya
source_table: dbo.Sales
strategy: incremental
primary_key: [invoice_id]
timestamp_column: modified_at

bronze:
  columns:
    include: [Quantity, "Invoice Date", "Branch ID", "Unit Price"]

silver:
  renames:
    "Invoice Date": invoice_date
    "Branch ID": branch_id
    "Unit Price": unit_price
  casts:
    Quantity: decimal
    unit_price: decimal
    invoice_date: date
```

```sql
-- tables/zakya_sales/silver.sql
SELECT
    *,
    Quantity * unit_price AS line_total
FROM bronze.zakya_sales
WHERE Quantity > 0;
```

Composition: `silver.sql` runs first, producing a result set with `line_total` derived and zero-quantity rows filtered. Then `table.yaml`'s `silver:` renames and casts apply on top as a CTE wrapper. The operator never writes the renames in SQL; the engine handles that.

### 5.3 N:1 silver — joins two bronzes

```yaml
# tables/orders_with_lines/table.yaml
kind: silver
strategy: full
primary_key: [order_id]
```

```sql
-- tables/orders_with_lines/silver.sql
SELECT
    o.id AS order_id,
    o.customer_id,
    SUM(li.amount) AS line_total
FROM bronze.zakya_orders o
LEFT JOIN bronze.zakya_line_items li ON li.order_id = o.id
GROUP BY 1, 2;
```

Dependencies are inferred from the SQL's FROM clauses (issue #54 convention). The folder name `orders_with_lines` is operator-chosen — no source maps to it directly. `kind: silver` is required (Decision 8) so the engine knows this isn't a bronze-origin table.

### 5.4 Gold — joins silvers, aggregates

```yaml
# tables/facts_sales/table.yaml
kind: gold
strategy: full
materialization: table
```

```sql
-- tables/facts_sales/gold.sql
SELECT
    o.invoice_date,
    b.region,
    SUM(o.line_total) AS total
FROM silver.orders_with_lines o
JOIN silver.zakya_branches b USING (branch_id)
GROUP BY 1, 2;
```

Gold rarely needs YAML beyond `kind:`, `strategy:`, and optional `materialization:` / `description:` / test metadata. SQL does the work.

### 5.5 Operator journeys

**Journey A — "I know I want this one table."**

1. Operator edits `feather.yaml` to add the rama-dw source (one-time).
2. Operator creates `tables/zakya_sales/table.yaml` by hand. Six lines.
3. `feather run`. Done.

No `feather discover` run required. No agent involvement required.

**Journey B — "I don't know what's in this source."**

1. Operator edits `feather.yaml`.
2. `feather discover` writes `schema_rama-dw.json`.
3. Curate skill (agent) reads the schema, applies recommendations, and emits `tables/<name>/table.yaml` files — one per recommended table. Optionally also writes `curation_recommendations.json` for audit.
4. Operator reviews the created folders, edits as needed, deletes unwanted ones.
5. `feather run`.

Both journeys converge on the same destination: `tables/<name>/` folders. No matter which entrance the operator used, the same shape lives on disk.

---

## 6. What #58 specifically becomes

The original #58 — "add `column_overrides` to curation.json" — was rejected during brainstorming. The follow-on direction — "add `transforms/bronze/<name>.sql` projection files" — is also superseded by this RFC.

Under this design, #58 collapses to: **add `bronze.columns.include` and `bronze.columns.exclude` to the per-table YAML schema.** That's it. No new file shape, no new directory, no separate parser module. The rest of the design machinery (per-table folders, YAML schema, kind: declarations) is what carries #58.

If this RFC lands, #58's PR is a couple of dozen lines of YAML schema and a compile-time step that resolves include/exclude against the discovered schema to produce a concrete column list passed to `source.extract(columns=...)`.

The source-side dialect quoting that already landed in [`a602e4f`](https://github.com/siraj-samsudeen/feather-etl/commit/a602e4f) is exactly the contract this needs — sources receive bare column names from the YAML compiler and quote them per dialect.

---

## 7. Open questions for reviewers

We do not yet have answers to these. Reviewer input especially welcome.

### 7.1 Composition order when YAML and SQL coexist at a layer

Decision 6 says "SQL runs first, YAML identity transforms wrap as a CTE." Is this the right order, or should YAML renames happen first so the SQL author writes against the renamed columns?

Example tension: silver.sql has `WHERE invoice_date > '2024-01-01'`. If renames-first, the SQL author writes the new name. If SQL-first, the SQL author writes `"Invoice Date" > '2024-01-01'` (the bronze name). The latter is more brittle if the operator later changes the rename.

Two viable answers, each with trade-offs. Pick one and commit.

### 7.2 1:1 silver default — explicit or implicit?

A bronze table `zakya_sales` with a `silver:` block in `table.yaml` (and no `silver.sql`) is silently understood to be "the silver derived from `bronze.zakya_sales`." Is this implicit 1:1 default acceptable, or should the YAML say `silver: { from: bronze.zakya_sales }` explicitly?

Risk of implicit: an operator who renames `zakya_sales` to `sales_facts` would expect silver to follow; if it doesn't, surprise.

Risk of explicit: every silver YAML gets a redundant `from:` line that the engine could have inferred.

### 7.3 Validation surface — where do projection errors surface?

The original #58 spec proposed adding bronze-projection validation to `feather validate`. We dropped that because validate today is connection-and-config-level only. Under this RFC, the YAML schema check (does `bronze.columns.include` reference columns that exist?) could naturally live in validate. Should it?

Related: do we want `feather validate` to grow into a general "is this project state coherent" check, covering bronze + silver + gold? If yes, this is a larger surface decision worth its own RFC.

### 7.4 Migration tool semantics

`feather migrate-state` should read existing `discovery/curation.json` and emit the equivalent `tables/*/table.yaml` set. Open:

- Should it preserve `curation.json` for rollback, or delete after successful migration?
- How does it handle entries with `decision: exclude`? Skip them entirely, or emit a `kind: bronze` YAML with a `disabled: true` marker?
- Are there fields in `curation.json` today that have no clean home in the new YAML schema?

### 7.5 N:1 silver folder naming

For `tables/orders_with_lines/`, the operator chose the name. Are there naming conventions to enforce (lowercase, snake_case, no special chars)? Should the curate skill auto-suggest names for N:1 silvers, or always leave them to the operator?

### 7.6 Cross-source / cross-database silvers

A silver that joins `bronze.zakya_sales` (from `rama-dw`) and `bronze.icube_orders` (from a different source / database) — is this in scope? Today, curation.json's `source_db` field implicitly assumes one source per table. The new YAML has no such restriction (silvers don't have a source — they're derived). Reviewers: any subtlety we should pin?

### 7.7 What about `column_map` in `feather.yaml`?

The current `TableConfig.column_map` mechanism (used in prod mode to rename columns) lives in YAML and overlaps with `silver.renames`. Should we deprecate `column_map` and migrate operators to the new shape? Or do both coexist for a release cycle?

### 7.8 Star-operator support at silver / gold

DuckDB native `* EXCLUDE`, `* RENAME`, `COLUMNS('regex')` are powerful at silver / gold. The YAML schema today maps `include` / `exclude` / `match` to concrete column lists at bronze. Should we mirror these at silver via YAML, or push operators to use raw DuckDB syntax in `silver.sql` when they want star-op behaviour?

---

## 8. Migration and rollout

Sequence we'd propose if this RFC is accepted:

1. **PR 1 — schema and reader for `tables/<name>/table.yaml`.** Engine learns to read the new shape alongside `curation.json`. Both work in parallel. New projects can use either.

2. **PR 2 — `feather migrate-state` command.** Reads `curation.json` → emits `tables/<name>/table.yaml` set. Existing projects upgrade with one command.

3. **PR 3 — #58 lands.** `bronze.columns.include` and `bronze.columns.exclude` in the YAML schema, plus the compile-time resolver. Trivial PR on top of the per-table foundation.

4. **PR 4 — `silver.sql` / `gold.sql` composition.** YAML renames/casts wrap SQL outputs as CTEs. Decision 6 mechanism.

5. **PR 5 — deprecate `curation.json` as state.** Engine stops reading it. Curate skill produces `curation_recommendations.json` as advisory output.

6. **Documentation update** — `docs/architecture/warehouse-layers.md`, `docs/CONTRIBUTING.md`, `README.md` all reflect the new shape. Old PRs and review docs are not rewritten — they stay as a record of how the design evolved.

Each PR is independently reviewable. The order matters for backward compatibility (engine reads new shape before old shape is removed).

---

## 9. Explicitly NOT decided here

To prevent scope creep, the following are flagged but left for separate decisions:

- **SQLite metadata DB** (issue #41). Future direction. Per-table files are the bridge.
- **Schema viewer / Datasette integration** for the new state. The per-table YAMLs are grep-able; richer viewer work is its own feature.
- **Test framework changes.** Existing tests use `make_curation_entry` / `write_curation` helpers. New helpers `make_table_folder` / `write_table_yaml` will be needed but the testing philosophy doesn't change.
- **Performance characteristics.** Reading 500 small YAMLs vs one big JSON — likely faster (parallelisable, no need to parse what you don't need) but unmeasured. Not a blocker.
- **Multi-environment / multi-mode per-table config.** Today's `mode: dev / prod / test` interacts with `column_map`. How modes interact with the new YAML schema is a follow-on question (#58's bronze projection should apply uniformly across modes; renames may differ by mode).

---

## 10. Summary for the busy reviewer

If you only have time for the headline:

- Replace `discovery/curation.json` as project state with `tables/<name>/table.yaml` files — one folder per table.
- Folder name is the bronze table identity. No `alias:` field.
- YAML for identity operations (renames, includes, excludes, casts, dedupe keys). SQL for expressions (filters, derives, joins, aggregations).
- `kind: bronze | silver | gold` required in every YAML.
- Dependencies inferred from SQL FROM clauses (issue #54 convention).
- File presence indicates layer activity; no empty stubs.
- Migration via `feather migrate-state` from existing `curation.json`.
- Issue #58 collapses to "add `bronze.columns` to the YAML schema."

We want pushback on the decisions in §3 and answers (or sharper questions) for the opens in §7.
