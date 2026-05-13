# Bronze projection via `transforms/bronze/*.sql`

Created: 2026-05-12
Status: DRAFT
Issue: [#58](https://github.com/siraj-samsudeen/feather-etl/issues/58)

---

# Part I — Requirements

## 1. Problem

`feather` extracts every source column for every bronze table, always. The orchestrator builds `SELECT *` whether silver consumes 5 columns or 95, and whether the source row is 1 KB or 12 KB on the wire. For narrow tables this is invisible; for wide ERP fact tables it dominates the wall clock and the disk.

The concrete trigger was rama-dw on 2026-05-12: a 67M-row × 97-column `ZAKYA.dbo.Sales` extract over pyodbc against SQL Server, where silver and gold combined consume 32 of the 97 columns. The remaining 65 columns are largely wide `nvarchar` strings that the operator does not need at any layer. The first bootstrap took ~3.5 hours, two-thirds of the network traffic and on-disk bronze footprint was waste, and the operator had no way to short-circuit it short of editing source code.

The asymmetry that makes this hurt: bronze is meant to be a faithful, type-preserving copy of (a meaningful slice of) the source. The default of "copy everything" is correct for the median 10-30 column table. For the long-tail wide tables, the default becomes a tax that scales with both row count and column width — and these tables tend to be the most operationally important ones (sales, transactions, line items).

The original draft of this issue placed the column list in `discovery/curation.json` as a `column_overrides` array. That direction was reversed in brainstorming for three reasons that bear repeating in the spec, because they constrain the shape of the solution:

1. **JSON is the wrong surface for column lists.** Issue #54 established that SQL is the single source of truth for dependency information. Re-introducing column metadata into JSON re-opens the dual-source-of-truth wound just closed.
2. **Bronze must not depend on silver.** An alternative considered was auto-inferring projections from silver SQL (walking the DAG, unioning column references per bronze table). This inverts the layer ordering — editing a silver SELECT would silently reshape the next bronze extract. The class of failure is spooky-action-at-a-distance, and it would defeat the architectural rule that bronze is the layer closest to the source and answerable on its own terms.
3. **Bronze-for-browsing must keep working.** Operators query bronze directly through Datasette and ad-hoc DuckDB. The default has to remain "all source columns are preserved at bronze." Inference would silently strip columns no silver consumed yet, breaking exploration workflows.

## 2. Goal

An operator who needs to narrow a bronze extract drops a single SQL file at `transforms/bronze/<bronze_table>.sql` listing the columns they want. The next `feather run` extracts only those columns from the source; absent the file, behaviour is unchanged. The mechanism is opt-in per table, lives on disk in SQL (the same surface silver and gold already use), and produces a loud failure rather than a silent reshape when the file is malformed.

After this ships, the rama-dw `ZAKYA.dbo.Sales` extract can be narrowed to 32 columns by writing one file, and the wide-table extract cost collapses by ~2-3x without touching any Python code, any YAML, or any JSON.

## 3. Acceptance criteria

- A `transforms/bronze/<bronze_table_name>.sql` file listing N columns causes `feather run` and `feather extract` to extract exactly those N columns from the source into bronze, with column order preserved. The file's basename matches the existing bronze table name (the sanitized `<source_db>_<alias>` produced by `curation._sanitize_bronze_name`).
- The mechanism works uniformly across all current sources (SQL Server, Postgres, MySQL, SQLite, DuckDB-file, CSV, JSON, Excel) — identifier quoting is delegated to each source's existing dialect-aware extract code (already landed in [`a602e4f`](https://github.com/siraj-samsudeen/feather-etl/commit/a602e4f)).
- A bronze table without a projection file extracts every source column. Zero regression on any existing test.
- A `SELECT *` inside the projection file is treated as a no-op — same effect as no file. Operators may keep the file as an explicit "I considered narrowing this and chose not to" record.
- A projection file violating any strict rule (expressions, aliases, `WHERE`, `JOIN`, `DISTINCT`, `GROUP BY`, `ORDER BY`, `LIMIT`, `HAVING`, `WITH`, set ops, subqueries, multiple statements, `*` mixed with named columns) aborts the run for that bronze table with an error message naming the offending construct and the file path. Other tables in the same run continue.
- `feather run` and `feather extract` share one resolver — both commands honour the projection file identically.

### End-to-end verification

A concrete exemplar of the acceptance criteria, run by hand once the implementation lands. Uses the rama-dw motivating case: source = `rama-dw` (SQL Server), database = `Zakya`, schema = `dbo`, table = `Sales`, alias = `sales` → bronze table = `zakya_sales`.

```bash
# Project root with curation including the entry above
cat > transforms/bronze/zakya_sales.sql <<'SQL'
SELECT
    "Quantity",
    "Invoice Date",
    "Branch ID"
FROM bronze.zakya_sales;
SQL

uv run feather run

duckdb data/feather.duckdb -c "DESCRIBE bronze.zakya_sales;"
# Expected: exactly 3 columns — Quantity, Invoice Date, Branch ID.

# Negative path:
echo "SELECT * FROM bronze.zakya_sales WHERE id > 10;" > transforms/bronze/zakya_sales.sql
uv run feather run
# zakya_sales aborts with an error naming WHERE and transforms/bronze/zakya_sales.sql.
# Other tables in the run still complete.
```

---

# Part II — Design

## 4. Scope

The mechanism is a per-bronze-table SQL file. The new module that parses it is small and self-contained. The pipeline and validate paths gain a single resolver that maps a `TableConfig` to a column list (or `None`). Sources are unchanged — their dialect-aware identifier quoting from [`a602e4f`](https://github.com/siraj-samsudeen/feather-etl/commit/a602e4f) absorbs whatever names the resolver hands them.

**In**

- A new disk surface: `<config_dir>/transforms/bronze/<bronze_table_name>.sql`, one file per bronze table that opts into projection. Absent file = `SELECT *` (today's default). The filename matches the bronze table name produced by `curation._sanitize_bronze_name` — flat, no subdirectories — mirroring the existing `transforms/silver/` and `transforms/gold/` conventions.
- A parser that accepts `SELECT col1, col2, ... FROM bronze.<name>;` shape and rejects anything that smells like silver work (filters, joins, expressions, renames).
- Both `feather run` and `feather extract` resolve the projection file the same way and thread the resulting column list through to `source.extract(columns=...)`. Sources handle their own dialect quoting.
- A malformed projection file fails the affected table loudly at run time with a message naming the offending construct and the file path. The error surfaces from `parse_projection_file`; no separate pre-flight scan is added.
- Documentation update in `docs/architecture/warehouse-layers.md` describing the new opt-in.

**Out**

- `cast_to` enforcement. Casts continue to live in silver SQL via `CAST(...)`. The original issue draft's `cast_to` field is dropped from scope.
- Mutating `discovery/curation.json` shape. The `column_overrides` key proposed in the original issue is not added.
- Silver-derived inference. Bronze is not allowed to read silver SQL.
- A YAML alternative to the SQL file.
- Row-level filtering at extract. `WHERE` in the projection file is explicitly rejected — that is a separate orthogonal feature (table-level extract filter already exists via `TableConfig.filter` and stays untouched).
- Per-environment / per-mode projection files. One file per bronze table is enough; mode-specific projection can be revisited if a clear need surfaces.
- Auto-generation of projection files from silver column references.
- Extensions to `feather validate`. The command stays connection-and-config-level only. Projection-file errors surface at run time. Adding SQL-file validation to `feather validate` is a separate decision worth its own issue (it would also cover silver and gold transforms).
- Hierarchical projection-file layout (`transforms/bronze/<source_db>/<table>.sql`). Flat layout matches the flat bronze namespace — see §5 *Naming*.

**Outside this scope**

- `src/feather_etl/sources/**` — already carries the dialect quoting from `a602e4f` that this feature relies on. Not touched again.
- The `column_map` YAML mechanism — preserved as a prod-mode rename fallback. When a projection file is present, the file wins; `column_map` is still applied as a rename after extract.

## 5. Key decisions

### Surface for the column list

**Chose:** A SQL file at `transforms/bronze/<bronze_table>.sql`, one per opted-in table.

**Why not in `discovery/curation.json`?** Curation JSON already lost the "single source of truth" race to SQL in issue #54. Re-adding column metadata to the JSON would mean every change to silver's column needs to be reflected in two places, and the system has no way to enforce that they match. SQL is the surface where every other column-level decision lives; bronze projection joins it.

**Why not derive from silver SQL?** Bronze would then depend on silver — an architecturally inverted relationship. The operator workflow is `discover → curate → extract → write silver`, and inference would force `discover → curate → write silver → extract`. Inference also breaks bronze-for-browsing (Datasette / ad-hoc DuckDB queries on bronze want columns silver doesn't yet use).

### Dialect of the projection file

**Chose:** DuckDB SQL dialect. Identifiers may be bare or `"double-quoted"`. Bracketed `[col]` (SQL Server) and backticked `` `col` `` (MySQL) styles are rejected at parse time.

**Why not match the source's dialect?** The source dialect is a property of the connection, not of the operator's declared intent. An operator writing a projection for a SQL Server source should not have to mentally swap to T-SQL identifier style — the silver and gold SQL they write next is DuckDB-dialect anyway. One dialect for all operator-authored SQL, regardless of source.

### Strict validation versus permissive parsing

**Chose:** Reject every SQL construct that isn't a bare column reference or a `*`. Loud failure at run time, before the source connection is opened for the affected table.

**Why not allow `WHERE` / `CAST(...)` for convenience?** Each construct admits ambiguity about where the work happens. `WHERE` at the projection level looks like an extract-time filter, but `TableConfig.filter` already exists for that and threading the same job through two surfaces is confusing. `CAST(...)` looks like a cast at bronze, but bronze is meant to be type-preserving; casts belong in silver. Permissive parsing would force this design to answer "what does it mean when …?" for every SQL construct. Strict parsing answers all those questions with "use the existing surface for that."

### Default behaviour when the file is missing

**Chose:** `SELECT *` — today's behaviour, no regression.

**Why not default to silver-derived narrowing or fail-closed?** Defaults change the meaning of inaction. Operators who have never heard of this feature would be surprised by narrowed bronze tables; operators with no silver yet would have nothing to narrow against. The current default is the only one that respects the "bronze = faithful copy" rule while leaving the door open for opt-in narrowing.

### Naming

**Chose:** Flat directory. The projection file is `transforms/bronze/<bronze_table_name>.sql`, where `<bronze_table_name>` is the existing sanitized `<source_db>_<alias>` (e.g. `zakya_sales` for the rama-dw `Zakya.dbo.Sales` motivating case).

**Why not a hierarchical layout** (e.g. `transforms/bronze/<source_db>/<schema>/<table>.sql`)? The bronze namespace itself is flat — one DuckDB schema (`bronze`) with sanitized table names. The file layout should mirror the table layout it controls, so the file ↔ table mapping is `basename(file) == bronze_table_name` with no transformation. A hierarchical layout would force the parser to reconstruct the bronze table name from the path, introduce a second naming convention to learn, and diverge from `transforms/silver/` and `transforms/gold/` which are already flat. Server-level grouping is also unnecessary because a project's YAML typically configures one source, and database-level disambiguation is already encoded in the bronze name's `<source_db>_` prefix.

### Validation surface

**Chose:** Parse-failure surfaces at run time, from the call inside the orchestrator. No new `feather validate` behaviour.

**Why not pre-flight the projection files inside `feather validate`?** `feather validate` today is connection-and-config-level: it checks the YAML parses, the source connects, and the table list resolves. It does not validate silver or gold SQL files, and extending it to validate bronze projection files alone would be lopsided — operators would reasonably expect parallel coverage for the other layers. That is a separate feature worth its own issue. The current design surfaces the same error one step later (at run time, when the specific table is processed) with no loss of information: the operator sees the file path and the offending construct, and other tables in the run still complete.

## 6. How it works

The mechanism slots into the existing extract flow at one point — between `TableConfig` resolution and `source.extract(...)`. `feather run` and `feather extract` share one resolver.

```
feather run | feather extract
  │
  ├─ resolve TableConfig from curation.json                    (unchanged)
  │
  ├─ resolve bronze projection from file                       (new)
  │     │
  │     ├─ <config_dir>/transforms/bronze/<table.name>.sql exists?
  │     │     ├─ yes ─▶ bronze_projections.parse_projection_file(path)
  │     │     │           ├─ sqlglot.parse_one(sql, dialect="duckdb")
  │     │     │           ├─ validate strict rules
  │     │     │           ├─ on failure: raise; the table run aborts with
  │     │     │           │   a clear error naming the construct and path
  │     │     │           └─ on success: return [bare names] | None (if SELECT *)
  │     │     │
  │     │     └─ no  ─▶ fall back to today's prod-mode column_map.keys() | None
  │     │
  │     └─ column list (or None)
  │
  ├─ source.extract(table.source_table, columns=column_list)   (unchanged signature)
  │     └─ each source quotes identifiers in its own dialect    (already landed in a602e4f)
  │
  └─ load to bronze                                            (unchanged)
```

Failure handling: a `BronzeProjectionParseError` or `BronzeProjectionInvalid` is caught by the per-table run wrapper that already handles extraction errors in `pipeline.run_table` and `extract.run_extract`. The affected table is reported as failed with the error message; sibling tables continue to run.

Two implementation details worth pinning so future maintainers do not relitigate them:

- The projection file's `FROM bronze.<name>` clause is present only so the file is lintable and runnable in a SQL IDE. The parser ignores it completely — it does not validate the FROM clause names the expected bronze table, and it does not care whether the FROM names a table that exists. The SELECT list is the entire contract.
- `parse_projection_file` returns **bare** column names. Quoting is the source's responsibility. This is the inversion of the original plan (which had the pipeline quote) and it is the cleaner inversion: each source already encodes its dialect via the quoting code from `a602e4f`, so the pipeline doesn't need to know anything about T-SQL versus PostgreSQL versus MySQL versus DuckDB.

## 7. Integration surface

**New**

- `src/feather_etl/bronze_projections.py` — the parser. Public surface: `parse_projection_file(path) -> list[str] | None`, `BronzeProjectionParseError`, `BronzeProjectionInvalid`. sqlglot import is contained in this module — no other code imports sqlglot for this feature.
- `tests/unit/test_bronze_projections.py` — exhaustive parser tests, one per strict-validation rule.
- `tests/integration/test_pipeline_bronze_projection.py` — end-to-end with a CSV fixture exercising the operator workflow.

**Modified**

- `src/feather_etl/pipeline.py` — `run_table` currently computes `prod_columns` for column projection. Becomes the home of `extract_columns`, which is resolved through a small helper that reads the projection file first and falls back to today's `column_map`-in-prod behaviour.
- `src/feather_etl/extract.py` — `run_extract` currently calls `source.extract(table.source_table)` with no projection. Becomes the second consumer of the same resolver, so `feather run` and `feather extract` share one source of truth.
- `tests/unit/test_pipeline.py`, `tests/unit/test_extract.py` — gain resolver tests for the projection-file × column_map × mode matrix.
- `docs/architecture/warehouse-layers.md` — the bronze section currently describes "faithful copy of every source column". Gains a paragraph on opt-in narrowing via `transforms/bronze/<bronze_table_name>.sql`.
- `docs/issues/README.md` — Contents list gains the `58-column-projection.md` entry.
- `README.md` — directory-tree comment gains one line mentioning `transforms/bronze/`.

**Outside this scope**

- `src/feather_etl/sources/**` — dialect-aware identifier quoting already landed in [`a602e4f`](https://github.com/siraj-samsudeen/feather-etl/commit/a602e4f) and is exactly the contract this feature needs.
- `src/feather_etl/curation.py`, `src/feather_etl/config.py` — `TableConfig` does not gain `column_overrides` or `column_casts` fields. The original issue draft's JSON-shape change is rejected.
- `src/feather_etl/commands/validate.py`, `src/feather_etl/validate.py` — not touched. Projection-file errors do not flow through `feather validate`; they surface at `feather run` / `feather extract` time. See §5 *Validation surface*.

## 8. Test design

Three tiers, mirroring §6. Detailed task-level test breakdowns live in the plan; this section pins what each layer proves.

**Unit — `tests/unit/test_bronze_projections.py` (the parser):**

- Acceptance: single column, multiple columns (order preserved), `"double-quoted"` identifiers, bare `SELECT *` returns `None`, leading/inline comments and trailing semicolons tolerated.
- Rejection (one test per rule, error message names the offending construct): expressions, aliases, `WHERE`, `JOIN`, `GROUP BY`, `ORDER BY`, `HAVING`, `LIMIT`, `DISTINCT`, `WINDOW`, `WITH` / CTE, subquery anywhere in tree, set ops (`UNION` / `INTERSECT` / `EXCEPT`), multiple statements, `*` mixed with named columns, bracketed `[col]` identifiers, backticked `` `col` `` identifiers.
- File handling: missing file → `None`; empty file / comments-only → `BronzeProjectionParseError`; malformed SQL → `BronzeProjectionParseError` carrying the file path.

**Unit — `tests/unit/test_pipeline.py` + `tests/unit/test_extract.py` (the resolver):**

- Projection file present + named columns → those names threaded to `extract(columns=...)`.
- Projection file present + `SELECT *` → `None` (same as no file).
- Projection file absent + `mode=prod` + `column_map` → `column_map.keys()` (today's behaviour).
- Projection file absent + `mode=dev` + `column_map` → `None`.
- Projection file present + `column_map` set → projection wins at extract; `column_map` still applied as rename after.
- Projection file absent + no `column_map` → `None`.
- `transforms/bronze/` directory does not exist → treated as missing file, no error.
- Malformed projection file → the table run reports failure with the parser's error; sibling tables in the same run are unaffected.

**Integration — `tests/integration/test_pipeline_bronze_projection.py` (real DuckDB, CSV fixture):**

- Golden path: 5-column CSV + projection naming 3 of them → bronze table has exactly those 3 columns in declaration order, correct row count.
- No-projection baseline: same fixture, no file → bronze has all 5 columns.
- `SELECT *` no-op: file contains only `SELECT * FROM bronze.orders;` → identical to baseline.
- Strict-validation gate: projection contains `WHERE` → `feather run` reports the affected table as failed with `WHERE` in the error, AND the destination has no `bronze.orders` table for that table.
- Sibling isolation: two tables in the same run, one with a malformed projection, one without — the malformed table fails, the other completes successfully.
- Co-existence with `column_map`: projection lists `[id, name]`, `column_map: {id: order_id, name: order_name}` → bronze has columns `order_id, order_name`.

**Not added:**

- No `feather validate` tests. The command's surface does not change.
- No live-DB integration tier. The 138 source-level quoting tests in `a602e4f` already prove `[col]` / `"col"` / `` `col` `` through mocked cursors.
- No e2e CLI tier. The integration tests already drive `feather run` and `feather extract` through Typer.
- No fuzz / property tests on sqlglot. Strict rules + named rejection tests are sufficient; sqlglot itself is upstream-tested.

Total new test surface: ~47 tests (~25 parser, ~16 resolver, ~6 integration).
