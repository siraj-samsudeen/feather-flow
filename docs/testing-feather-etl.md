# feather-etl — Independent Test Plan

You are testing **feather-etl**, a config-driven Python ETL tool that extracts data from source databases and files into a local DuckDB warehouse.

**Before writing any test:** Read this plan top to bottom. Each section builds on the previous one.

---

## How feather-etl Works (Read This First)

feather-etl reads a `feather.yaml` config file that declares source databases/tables and where to load them. It extracts data from the source, loads it into a local DuckDB file, and tracks what it has done in a separate state database.

**Key files created at runtime:**
- `feather_data.duckdb` — the warehouse (holds your data in bronze/silver/gold schemas)
- `feather_state.duckdb` — the state tracker (watermarks, run history, DQ results, schema snapshots)
- `feather_log.jsonl` — append-only structured log

**CLI entry point:** `feather` (run via `uv run feather`)

**Test fixtures:** `tests/fixtures/`
- `sample_erp.duckdb` — synthetic ERP with `erp` schema: customers(4), orders(5), products(3), sales(10)
- `sample_erp.sqlite` — same data in SQLite format
- `client.duckdb` — real ERP data with `icube` schema: 6 tables, largest is SALESINVOICE(11,676 rows)
- `csv_data/` — 3 CSV files: customers.csv(4), orders.csv(5), products.csv(3)
- `csv_glob/` — 2 CSV files: sales_jan.csv(3), sales_feb.csv(2) for glob testing
- `excel_data/` — 3 .xlsx files: same data as csv_data
- `json_data/` — 3 JSON files: same data as csv_data

**Before you write a single line of code, run both suites and confirm green:**
```bash
uv run pytest -q               # currently: 440 tests
bash scripts/hands_on_test.sh  # currently: 72 checks
```
If anything is red before you touch anything, report immediately.

---

## Things You Need to Know Before Testing

These are design behaviors, not bugs. If you don't know them, you will waste time.

### 1. State DB is keyed by directory, not by config file
`feather_state.duckdb` lives next to `feather.yaml`. If you run two different configs from the same directory, they share the same state database. Change detection watermarks are keyed by `table_name`, so two configs that both define a table called `customers` will interfere with each other. **Always use a fresh directory per test scenario.**

### 2. Change detection uses mtime then MD5
File-based sources (CSV, DuckDB, SQLite, Excel, JSON) detect changes via a two-step process:
1. Compare file `mtime` (modification time). If unchanged &rarr; skip immediately.
2. If mtime changed &rarr; compute MD5 hash. If hash matches stored hash &rarr; skip (the "touch" scenario). If hash differs &rarr; re-extract.

**Implication for testing:** `touch` alone does NOT trigger re-extraction. You must actually change file contents (e.g., `INSERT INTO` a DuckDB source, add a row to a CSV).

### 3. Incremental overlap window
When using `strategy: incremental`, the tool re-extracts rows from a window before the stored watermark. The default `overlap_window_minutes` is 2. This means if your watermark is `2025-01-10`, the tool extracts rows where `timestamp >= 2025-01-10 minus 2 minutes`. The overlap rows from the previous batch are deduped using boundary hashes (SHA-256 of primary key values at the watermark timestamp).

**Implication for testing:** After an incremental run that adds new rows, expect more rows in the "new" batch than were actually new &mdash; some overlap rows are re-extracted and deduped at load time. To avoid this complexity in tests where you want exact row counts, set `overlap_window_minutes: 0` in the config defaults.

### 4. Mode affects column selection and target schema
- `dev` mode (default): All columns extracted. Data lands in `bronze.{name}`. `column_map` is ignored.
- `prod` mode: Only columns listed in `column_map` are extracted (if `column_map` is set). Data lands in `silver.{name}`.
- `test` mode: Same as `dev` but `row_limit` caps the number of rows extracted.
- `target_table` in config always overrides the mode-derived schema.

---

## Area 1: The CLI — Learn the Tool

Run these commands first. They show you what feather-etl does and how it works. Every other test goes through these commands.

### 1.1 Scaffold a new project

```bash
uv run feather init /tmp/test_project
```

**Verify:**
- `feather.yaml` created with template config
- `pyproject.toml`, `.gitignore`, `.env.example` created
- `transforms/silver/`, `transforms/gold/`, `tables/`, `extracts/` directories created

**Also verify:** Re-running init on a non-empty directory is rejected with a clear error and exit code 1.

### 1.2 Validate a config

Create a minimal config pointing to `sample_erp.duckdb`:
```yaml
source:
  type: duckdb
  path: ./source.duckdb
destination:
  path: ./feather_data.duckdb
tables:
  - name: customers
    source_table: erp.customers
    strategy: full
  - name: orders
    source_table: erp.orders
    strategy: full
```

```bash
uv run feather validate --config feather.yaml
```

**Verify:**
- Output shows "Config valid: 2 table(s)"
- Shows source type and resolved path
- Shows destination path and state DB path
- `feather_validation.json` is written alongside the config

**Also verify these rejection cases (each with its own config):**
- Source file doesn't exist &rarr; clear error, exit 1
- Invalid strategy (e.g., `upsert`) &rarr; clear error listing valid strategies
- Missing `source:` section &rarr; clear error
- Missing `destination:` section &rarr; clear error
- Unresolved env var (`${UNSET_VAR}`) &rarr; clear error naming the variable
- `target_table` without schema prefix (e.g., `justname`) &rarr; clear error

### 1.3 Discover source tables

```bash
uv run feather discover --config feather.yaml
```

**Verify:** Lists all tables in the source with column names and data types. For `sample_erp.duckdb`, expect 4 tables: erp.customers, erp.orders, erp.products, erp.sales.

### 1.4 Extract data (the core operation)

```bash
uv run feather run --config feather.yaml
```

**Verify:**
- Output shows each table with status (success/failure) and row count
- Summary line: "N/N tables extracted."
- `feather_data.duckdb` created with data in the `bronze` schema
- `feather_state.duckdb` created with run records

### 1.5 Check status and history

```bash
uv run feather status --config feather.yaml
uv run feather history --config feather.yaml
```

**Verify:**
- `status` shows the last run for each table: name, status, row count, timestamp
- `history` shows a formatted table with run_id, table name, status, rows, timestamps
- `status` before any run shows "No state DB found"
- `status` after setup but before run shows "No runs recorded yet"

### 1.6 Single-table extraction

```bash
uv run feather run --config feather.yaml --table customers
```

**Verify:**
- Only the `customers` table is extracted. Other tables are not touched.
- Running with `--table nonexistent` shows a clear error listing available table names, exit code 1.

### 1.7 History filtering

```bash
uv run feather history --config feather.yaml --table customers --limit 5
```

**Verify:** Shows only runs for `customers`, at most 5 entries.

---

## Area 2: Data Loading — Does Data Arrive Correctly?

This is the most critical area. If data doesn't land correctly, nothing else matters.

### Test Matrix

| # | Test | Config | Verify |
|---|------|--------|--------|
| 2.1 | DuckDB source extracts all tables | `sample_erp.duckdb`, 4 tables, `strategy: full` | All 4 tables in bronze with correct row counts (4, 5, 3, 10) |
| 2.2 | CSV source extracts all files | `csv_data/`, 3 tables, `strategy: full` | All 3 tables in bronze with correct row counts (4, 5, 3) |
| 2.3 | SQLite source extracts tables | `sample_erp.sqlite`, 2+ tables, `strategy: full` | Correct row counts |
| 2.4 | Large dataset extraction | `client.duckdb`, largest table (SALESINVOICE, 11,676 rows) | Full row count, no truncation |
| 2.5 | Excel source extracts files | `excel_data/`, 3 files, `strategy: full` | Row counts (5, 4, 3) |
| 2.6 | JSON source extracts files | `json_data/`, 3 files, `strategy: full` | Row counts (5, 4, 3) |
| 2.7 | ETL metadata columns present | Any source | Every loaded table has `_etl_loaded_at` (TIMESTAMP) and `_etl_run_id` (VARCHAR) columns |
| 2.8 | NULL values pass through | `sample_erp.duckdb`, products table (stock_qty is NULL for Service Pack) | NULL preserved in destination |
| 2.9 | Column names preserved | Any source | All source columns appear in destination with original names |
| 2.10 | Column names with spaces preserved | `client.duckdb`, SALESINVOICEMASTER (has "Round Off" column) | Space in column name preserved |
| 2.11 | BLOB columns load without error | `client.duckdb`, INVITEM (~200 columns, some BLOB) | Extraction succeeds |

**How to verify row counts:**
```python
import duckdb
con = duckdb.connect('feather_data.duckdb', read_only=True)
cnt = con.execute('SELECT COUNT(*) FROM bronze.tablename').fetchone()[0]
```

**How to verify metadata columns:**
```python
cols = [r[0] for r in con.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema='bronze' AND table_name='tablename' "
    "ORDER BY ordinal_position"
).fetchall()]
assert '_etl_loaded_at' in cols
assert '_etl_run_id' in cols
```

---

## Area 3: Change Detection — When Does Re-Extraction Happen?

Change detection determines whether feather-etl re-extracts a table or skips it. This directly affects performance and correctness.

### How it works

For file-based sources (DuckDB, CSV, SQLite, Excel, JSON):
1. First run: always extracts (stores file mtime + MD5 hash in state DB)
2. Subsequent run: if file mtime unchanged &rarr; skip (fast path, no hashing)
3. If mtime changed: compute MD5 hash
   - Hash matches stored hash &rarr; skip ("touch" scenario &mdash; file was touched but content unchanged)
   - Hash differs from stored hash &rarr; re-extract

For multi-table file sources (DuckDB, SQLite): change detection is **file-level**. Modifying ANY table in the file triggers re-extraction of ALL tables from that file.

### Test Matrix

| # | Test | Steps | Verify |
|---|------|-------|--------|
| 3.1 | First run always extracts | Run extraction | Status: success, data loaded |
| 3.2 | Unchanged source skips | Run twice without modifying source | Second run: "skipped (unchanged)", exit 0 |
| 3.3 | Modified source re-extracts | Run, add a row to source (`INSERT INTO` for DuckDB/SQLite, append line for CSV), run again | Re-extraction with updated row count |
| 3.4 | Touched file skips | Run, `touch` the source file, run again | Skipped &mdash; mtime changed but hash identical |
| 3.5 | Touch updates stored mtime | After 3.4, run again without touching | Skipped &mdash; stored mtime was updated to prevent re-hashing |
| 3.6 | Multi-table file: change in one triggers all | Run with 2 tables from same .duckdb, modify one table in the source, run again | Both tables re-extracted |
| 3.7 | Skipped run recorded in state | Run, then run again (unchanged) | `_runs` table has a "skipped" entry for the second run |

**Important:** To modify a DuckDB source, use `duckdb.connect()` + `INSERT INTO`. To modify a CSV, append a line. Do NOT just `touch` &mdash; that tests the touch scenario (3.4), not the change scenario (3.3).

**Example: modifying a DuckDB source to trigger re-extraction:**
```python
import duckdb
con = duckdb.connect('source.duckdb')
con.execute("INSERT INTO erp.customers VALUES (999, 'Test', 'test@test.com', 'Mumbai', 1000, CURRENT_TIMESTAMP)")
con.close()
```

---

## Area 4: Load Strategies — How Data Gets Replaced or Accumulated

feather-etl has three load strategies. Each handles re-extraction differently.

### How they work

| Strategy | First run | Subsequent runs (with changes) |
|----------|-----------|-------------------------------|
| `full` | Create table with all rows | Drop + recreate table (atomic swap) |
| `incremental` | Full load, store watermark (MAX of timestamp column) | Delete rows &ge; watermark, insert new batch (partition overwrite) |
| `append` | Insert all rows | Insert all rows (never deletes) |

### Test Matrix

| # | Test | Config | Verify |
|---|------|--------|--------|
| 4.1 | Full replaces on re-extraction | `strategy: full`, run twice with source modified between runs | Destination has source's row count (not doubled). Only 1 distinct `_etl_run_id`. |
| 4.2 | Incremental first run loads all | `strategy: incremental`, `timestamp_column: modified_at` on `erp.sales` | All 10 rows loaded. Watermark set to MAX(modified_at). |
| 4.3 | Incremental unchanged skips | Run incremental twice without modifying source | Second run: skipped |
| 4.4 | Incremental captures new rows | After 4.2, insert row with timestamp &gt; watermark, re-run | New rows extracted. Total in destination = original + new. |
| 4.5 | Incremental watermark updated | After 4.4, check `_watermarks` table | `last_value` updated to new MAX(modified_at). `boundary_hashes` populated. |
| 4.6 | Incremental with filter | Add `filter: "status != 'cancelled'"` to config | Cancelled rows excluded from extraction. Verify none in destination. |
| 4.7 | Append first run | `strategy: append` | Data lands with ETL metadata columns |
| 4.8 | Append accumulates | Modify source (add rows), run again | BOTH original AND new rows exist. Total = sum of all batches. Distinct `_etl_run_id` count = number of successful batches. |
| 4.9 | Append unchanged skips | Run append twice without modifying source | Second run: skipped (change detection prevents duplicate append) |
| 4.10 | Incremental watermark preserved on skip | Run incremental, skip (unchanged), check watermark | `last_value` unchanged after skip |

**About the overlap window:** `defaults.overlap_window_minutes` (default: 2) means the incremental WHERE clause is `timestamp &gt;= (last_value - 2 minutes)` instead of `timestamp &gt;= last_value`. This re-extracts some rows from the previous batch to handle late-arriving data. The re-extracted overlap rows are deduped at load time using boundary hashes. If you need to test exact row counts, set `overlap_window_minutes: 0` in the config to eliminate the overlap.

---

## Area 5: Configuration and Modes — How Config Controls Behavior

### 5.1 Mode resolution

The mode is resolved in this priority order:
1. `--mode` CLI flag (highest priority)
2. `FEATHER_MODE` environment variable
3. `mode:` in YAML config
4. `dev` (default, if nothing is set)

| # | Test | Steps | Verify |
|---|------|-------|--------|
| 5.1.1 | CLI flag overrides YAML | YAML: `mode: dev`, run with `--mode prod` | Data lands in `silver.{name}` (prod target), column_map applied if present |
| 5.1.2 | Env var overrides YAML | YAML: `mode: dev`, run with `FEATHER_MODE=prod uv run feather run` | Data lands in `silver.{name}` |
| 5.1.3 | CLI overrides env var | `FEATHER_MODE=test`, run with `--mode prod` | `--mode prod` wins |
| 5.1.4 | Invalid mode rejected | Set mode to `staging` in YAML | Clear error: "Invalid mode 'staging' (from YAML config). Valid: ['dev', 'prod', 'test']" |
| 5.1.5 | Invalid mode via CLI | Run with `--mode staging` | Clear error: "Invalid mode 'staging' (from --mode flag)" |

### 5.2 Mode affects data landing and column selection

| # | Test | Config | Verify |
|---|------|--------|--------|
| 5.2.1 | Dev mode: all columns, bronze schema | `mode: dev`, table with `column_map` set | Data in `bronze.{name}`. ALL source columns present (column_map ignored). `_etl_loaded_at` and `_etl_run_id` appended. |
| 5.2.2 | Prod mode: mapped columns, silver schema | `mode: prod`, table with `column_map: {customer_id: id, name: customer_name}` | Data in `silver.{name}`. Only `id`, `customer_name` + metadata columns. Unmapped source columns NOT present. |
| 5.2.3 | Prod mode without column_map | `mode: prod`, table with NO column_map | Data in `silver.{name}`. All source columns present (nothing to filter). |
| 5.2.4 | Test mode: row_limit caps rows | `mode: test`, `defaults: {row_limit: 3}` | Each table has at most 3 rows |
| 5.2.5 | Dev mode ignores row_limit | `mode: dev`, `defaults: {row_limit: 3}` | All rows extracted (limit ignored) |
| 5.2.6 | Explicit target_table overrides mode | `mode: prod`, table with `target_table: bronze.audit` | Data lands in `bronze.audit`, not `silver.{name}`. Works in any mode. |

---

## Area 6: Data Quality Checks — Post-Load Validation

DQ checks run automatically after each table loads. Results are stored in `_dq_results` in the state DB. DQ failures do NOT block the pipeline &mdash; data is still loaded and the run status is `success`.

### How it works

| Check | Trigger | Pass | Fail |
|-------|---------|------|------|
| `row_count` | Always (even without config) | Table has &gt;0 rows | Table has 0 rows &rarr; `warn` |
| `not_null` | `quality_checks: {not_null: [col1, col2]}` | No NULLs in specified columns | Any NULLs &rarr; `fail` with count |
| `unique` | `quality_checks: {unique: [col1]}` | No duplicates in specified column | Any duplicates &rarr; `fail` with count |
| `duplicate` | `quality_checks: {duplicate: true}` | No exact duplicate rows | Exact duplicates &rarr; `fail` with count |
| `duplicate` (PK) | Table has `primary_key` configured | No duplicate PK combinations | Duplicate PKs &rarr; `fail` with count |

### Test Matrix

| # | Test | Config | Verify |
|---|------|--------|--------|
| 6.1 | not_null detects NULLs | Create CSV with a NULL in one row. Configure `not_null: [column]`. | `_dq_results`: `not_null` &rarr; `fail`, details: "N NULL values" |
| 6.2 | not_null passes on clean data | Configure `not_null` on column with no NULLs | `_dq_results`: `not_null` &rarr; `pass` |
| 6.3 | unique detects duplicates | Create data with duplicate values in a column. Configure `unique: [column]`. | `_dq_results`: `unique` &rarr; `fail`, details: "N duplicate values" |
| 6.4 | row_count always runs | Extract table with NO `quality_checks` configured | `_dq_results` has `row_count` &rarr; `pass` with correct count |
| 6.5 | row_count warns on zero | Configure a table that extracts 0 rows (e.g., filter matching nothing) | `_dq_results`: `row_count` &rarr; `warn` |
| 6.6 | DQ failure doesn't block pipeline | Configure a failing DQ check. Run extraction. | Extraction status: `success`. Data loaded. DQ result: `fail`. |
| 6.7 | Multiple checks per table | `quality_checks: {not_null: [col1], unique: [col2]}` | Both checks appear in `_dq_results` |

**How to verify DQ results:**
```python
import duckdb
con = duckdb.connect('feather_state.duckdb', read_only=True)
results = con.execute(
    "SELECT check_type, column_name, result, details "
    "FROM _dq_results ORDER BY check_type"
).fetchall()
```

---

## Area 7: Error Handling and Retry — What Happens When Things Go Wrong

### How it works

When a table extraction fails:
1. The error is recorded in `_runs` with status `failure`
2. A retry backoff is set: `retry_count * 15 minutes`, capped at 120 minutes
3. On the next run, if the table is within its backoff window, it is skipped with status `skipped`
4. Other tables in the same config continue processing unaffected
5. On success, retry count resets to 0

### Test Matrix

| # | Test | Steps | Verify |
|---|------|-------|--------|
| 7.1 | Good table succeeds despite bad table | Config: one valid table + one referencing nonexistent source table. Run. | Valid table: `success`. Bad table: `failure` with clear error. Exit code: 1. |
| 7.2 | Bad table enters backoff | After 7.1, run again immediately. | Bad table: `skipped` with "In backoff (previous failure: ...)". Exit code: 1. |
| 7.3 | Backoff expires | Wait for backoff period or use a table with past `retry_after`. Run. | Table attempts extraction again (not skipped). |
| 7.4 | Success resets retry | Run a table that previously failed, now with a valid config. | `_watermarks.retry_count` = 0. |
| 7.5 | Error message stored in state | After 7.1, query `_runs` for the failed table. | `error_message` contains the actual error text. |
| 7.6 | Other tables unaffected by one failure | Config: 2 valid tables + 1 invalid. Run. | Both valid tables: `success`. Invalid: `failure`. |
| 7.7 | Missing config file | Run `feather run --config nonexistent.yaml` | Clear "Config file not found" error (not a Python traceback). Exit code: 1. |

---

## Area 8: Dedup — Removing Duplicate Rows at Extraction Time

### How it works

| Config | Behavior |
|--------|----------|
| `dedup: true` | `SELECT DISTINCT *` &mdash; removes exact duplicate rows |
| `dedup_columns: [col1, col2]` | `ROW_NUMBER() OVER (PARTITION BY cols ORDER BY 1) WHERE rn = 1` &mdash; keeps first occurrence per key |
| Neither set (default) | No dedup &mdash; all rows loaded including duplicates |
| Both set | Validation error &mdash; mutually exclusive |

### Test Matrix

| # | Test | Config | Verify |
|---|------|--------|--------|
| 8.1 | dedup removes exact duplicates | CSV with 3 rows where 2 are identical. `dedup: true`. | 2 rows in destination (3 - 1 duplicate) |
| 8.2 | dedup_columns keeps first per key | CSV with same ID but different values. `dedup_columns: [id]`. | One row per ID in destination |
| 8.3 | No dedup loads all rows | Same duplicate CSV. No dedup config. | All 3 rows loaded |
| 8.4 | Both rejected at validate | `dedup: true` AND `dedup_columns: [id]` | `feather validate` fails: "mutually exclusive" |

---

## Area 9: Schema Drift Detection — Handling Source Schema Changes

### How it works

On each extraction, feather-etl compares the source table's current schema against the stored snapshot in `_schema_snapshots`. On the first run, it saves a baseline. On subsequent runs:
- Added columns: detected, logged, `ALTER TABLE ADD COLUMN` applied to destination
- Removed columns: detected, logged, destination column becomes NULL
- Type changes: detected, logged with CRITICAL severity

### Test Matrix

| # | Test | Steps | Verify |
|---|------|-------|--------|
| 9.1 | First run saves baseline | Extract a table. Query `_schema_snapshots`. | One row per column with column name, data type, snapshot timestamp. |
| 9.2 | No drift on re-run | Run again without changing source schema. | `_runs.schema_changes` is NULL for this run. |
| 9.3 | Added column detected | After 9.1, `ALTER TABLE source ADD COLUMN phone VARCHAR`. Modify file to trigger re-extraction. | `_runs.schema_changes` contains `{"added": [["phone", "VARCHAR"]]}`. `_schema_snapshots` updated. Destination table has new column. |
| 9.4 | Schema snapshot persists | After 9.3, query `_schema_snapshots` | New column present in snapshot |

---

## Area 10: SQL Transforms — Post-Extraction Views and Tables

### How it works

SQL files in `transforms/silver/` and `transforms/gold/` define post-extraction transforms. Each file's name becomes the view/table name. Header comments declare dependencies and materialization.

```sql
-- materialized: true
SELECT COUNT(*) AS n FROM silver.customers
```

- Silver transforms &rarr; always VIEWs
- Gold transforms &rarr; VIEWs by default, TABLEs if `-- materialized: true` and mode is `prod`
- In `dev`/`test` mode: all transforms (including materialized gold) become VIEWs
- Dependencies on `bronze.*` are silently ignored (bronze is extraction layer)
- Dependencies are inferred from the SQL body (`silver.*`/`gold.*` references in `FROM`/`JOIN`). The `-- depends_on:` header is not part of the contract — only the SQL body declares edges.
- CTE names, string literals, comments, and table-valued functions like `read_csv(...)` are not treated as dependency edges.
- Dependencies on `silver.*`/`gold.*` that don't exist among discovered transforms &rarr; error

### Test Matrix

| # | Test | Config | Verify |
|---|------|--------|--------|
| 10.1 | Silver transform creates view | `transforms/silver/cust.sql` selecting from `bronze.customers`. Run + setup in dev mode. | `silver.cust` exists as VIEW. Queryable. |
| 10.2 | Dev mode: gold materialized &rarr; view | `transforms/gold/summary.sql` with `-- materialized: true`. Run + setup in dev mode. | `gold.summary` exists as VIEW (not table). |
| 10.3 | Dependency ordering | Silver transform + gold transform depending on it. | Silver executed before gold. Both queryable. |
| 10.4 | Missing dependency error | Gold transform depending on nonexistent silver transform. | `feather setup` raises ValueError naming the missing dependency. |

**Known limitation:** In prod mode, `feather setup` creates only gold transforms as materialized tables. Silver is populated by extraction (via `column_map`), not by transforms. However, if a gold transform depends on `silver.{table}` where that table is created by extraction (not by a silver transform), `build_execution_order()` will fail because it only resolves dependencies among discovered transforms. To work around this, either create a pass-through silver transform or test transforms only in dev mode.

---

## Area 11: Multi-File CSV Glob — Extracting Multiple Files as One Table

### How it works

When `source_table` contains a glob pattern (e.g., `sales_*.csv`), feather-etl combines all matching CSV files into a single table. All files must have the same schema (column names and types). Change detection tracks per-file mtime + hash.

### Test Matrix

| # | Test | Config | Verify |
|---|------|--------|--------|
| 11.1 | Glob extracts all matching files | `source_table: "sales_*.csv"`, 2 files (3+2 rows) | Destination has 5 rows |
| 11.2 | No-change skip | Run twice without modifying files | Second run: skipped |
| 11.3 | New matching file triggers re-extraction | After 11.2, add `sales_mar.csv` with matching schema to directory | Re-extraction includes all 3 files |
| 11.4 | Modified file triggers re-extraction | After 11.2, modify one file's content | Re-extraction picks up the change |
| 11.5 | Schema mismatch rejected | Add file with different column names | Extraction fails with clear error about schema mismatch |
| 11.6 | Discover shows individual files | Run `feather discover` | Lists each CSV file separately with its schema |

---

## Area 12: Init Wizard — Project Scaffolding

### Test Matrix

| # | Test | Steps | Verify |
|---|------|-------|--------|
| 12.1 | Non-interactive with DuckDB | `feather init proj --non-interactive --source-type duckdb --source-path sample_erp.duckdb` | `feather.yaml` with all discovered tables. `transforms/silver/` has stub SQL per table. Config passes `feather validate`. |
| 12.2 | Table filter | Add `--tables "erp.customers"` | Only `customers` in generated config |
| 12.3 | Re-init on non-empty dir (interactive) | Run `feather init` on existing non-empty dir without flags | Rejected with clear error, exit 1 |

---

## Area 13: JSON Output — Machine-Readable CLI

### How it works

The global `--json` flag outputs NDJSON (one JSON object per line) instead of human-readable text. Works with `run`, `status`, `history`, `validate`.

### Test Matrix

| # | Test | Command | Verify |
|---|------|---------|--------|
| 13.1 | Run with --json | `uv run feather run --json --config feather.yaml` | Each line is valid JSON. Contains `table`, `status`, `rows_loaded`. |
| 13.2 | Status with --json | `uv run feather status --json --config feather.yaml` | Valid JSON with table status info |
| 13.3 | Log file created | After any run | `feather_log.jsonl` exists. Each line is valid JSON with `timestamp`, `level`, `event`. |

---

## Test Execution Order

Run the areas in this order. Each area assumes the previous ones work:

1. **Area 1 (CLI)** &mdash; learn the tool, verify basic commands work
2. **Area 2 (Data Loading)** &mdash; verify data arrives correctly from each source type
3. **Area 3 (Change Detection)** &mdash; verify re-extraction logic
4. **Area 4 (Load Strategies)** &mdash; verify full/incremental/append behavior
5. **Area 5 (Config and Modes)** &mdash; verify mode resolution and column selection
6. **Area 6 (Data Quality)** &mdash; verify post-load checks
7. **Area 7 (Error Handling)** &mdash; verify failure isolation and retry
8. **Area 8 (Dedup)** &mdash; verify duplicate removal
9. **Area 9 (Schema Drift)** &mdash; verify schema change detection
10. **Area 10 (Transforms)** &mdash; verify SQL views and materialized tables
11. **Area 11 (CSV Glob)** &mdash; verify multi-file extraction
12. **Area 12 (Init Wizard)** &mdash; verify project scaffolding
13. **Area 13 (JSON Output)** &mdash; verify machine-readable output

---

## How to Report Results

For each area, report:
- **PASS** &mdash; what you tested and what you observed
- **FAIL** &mdash; what you expected vs what actually happened, with the exact error or wrong output. Include the config you used and the command you ran.
- **SKIP** &mdash; if a test couldn't be run, explain why (e.g., PostgreSQL not installed)

Report bugs as: "Area X.Y: [test name] &mdash; FAIL: [description]. Config: [yaml]. Command: [cmd]. Expected: [X]. Got: [Y]."
