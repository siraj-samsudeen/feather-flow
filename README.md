# feather-flow

> **Renamed** from `feather-etl` on 2026-07-17 — repo, PyPI package (now `feather-flow`), and Python module (now `feather_flow`). Old GitHub URLs redirect. The `feather-etl` PyPI package is frozen at 0.1.3; existing installs upgrade with `uv tool uninstall feather-etl && uv tool install feather-flow`, and imports change from `feather_etl` to `feather_flow`.

A config-driven Python ETL platform for deploying data pipelines across multiple clients with heterogeneous ERP source systems — SQL Server, SAP B1, SAP S4 HANA, and custom Indian ERPs.

feather-flow is the entire data platform for clients who have none. For clients who already have a data warehouse, it is a lightweight extraction and local transform layer that feeds only clean, curated gold tables to the cloud.

## Why feather-flow?

Enterprise ETL stacks (dlt + dbt/SQLMesh + Dagster) introduce a complexity tax — hundreds of transitive dependencies, proprietary abstractions, steep learning curves — that makes them unsuitable for Indian SMBs who need a working data pipeline, not a data engineering career.

feather-flow replaces the full stack with a single Python package (~1,200 LOC, 7 dependencies), deployable by a small team, configurable in YAML, and understandable by anyone who can read SQL.

| Heavy Stack | feather-flow equivalent |
|------------|----------------------|
| dlt (extraction) | `sources/` — Protocol-based connectors |
| dbt / SQLMesh (transforms) | Plain `.sql` files executed in local DuckDB |
| Dagster / Airflow (orchestration) | APScheduler + CLI |
| 3 tools, hundreds of deps | 1 package, 7 deps |

## Design Principles

- **Multi-client by design** — Each client is an independent `feather.yaml`. Same package, different config. One team deploys across dozens of clients.
- **Config-driven** — YAML defines sources, tables, schedules, transforms. Adding a table means editing YAML, not writing Python.
- **Local-first** — Extract and transform in local DuckDB (free compute). Push only gold tables to MotherDuck (minimal cloud cost).
- **Layers are optional** — Skip bronze for SMB clients who don't need it. Use bronze as a dev cache or compliance audit trail when you do.
- **Testable** — Full pipeline testable end-to-end with file-based sources. No mocking needed for core pipeline tests.
- **Package-first** — Reusable library. Client projects import and configure it. The v2 connector library (`feather-connectors`) will provide canonical silver mappings per ERP system — reusable across all clients on the same source.

## Layer Model

```
Source ERP (SQL Server / SAP B1 / SAP S4 HANA / custom)
    |
    v  column_map applied (zero-copy rename + select)
    |
Local DuckDB
    |
    |-- bronze  (optional)
    |     Raw ERP data, all columns.
    |     Use as dev cache (iterate transforms without hitting source DB)
    |     or compliance audit trail (strategy: append, insert-only).
    |
    |-- silver  (primary working layer)
    |     Canonical names, selected columns, light cleaning.
    |     Always views — lazy, always current, no pipeline step.
    |     Client analysts + LLM agents work here.
    |
    |-- gold    (dashboard layer)
    |     KPIs, aggregations, denormalized tables.
    |     Views by default; materialized tables where performance requires.
    |     Only layer synced to MotherDuck.
    |
    v  (optional sync)
MotherDuck → Rill Data / BI tools
```

## Supported Sources

| Source | Reader | Change Detection | Incremental |
|--------|--------|-----------------|-------------|
| CSV | `read_csv()` | mtime + MD5 hash | — |
| DuckDB file | ATTACH | mtime + MD5 hash | ✓ |
| SQLite | `sqlite_scan()` | mtime + MD5 hash | ✓ |
| Excel `.xlsx` | `read_xlsx()` (excel ext) | mtime + MD5 hash | — |
| Excel `.xls` | openpyxl fallback | mtime + MD5 hash | — |
| JSON | `read_json()` | mtime + MD5 hash | — |
| SQL Server | pyodbc → PyArrow | CHECKSUM_AGG + COUNT(*) | ✓ |
| PostgreSQL | psycopg2 → PyArrow | md5(row_to_json) + COUNT(*) | ✓ |

**CSV note:** `source_table` must include the `.csv` extension (e.g., `orders.csv` not `orders`).

**Change detection** is file-level — for multi-table sources like DuckDB, modifying any table in the file triggers re-extraction of all tables from that file.

New sources: implement the `Source` Protocol (~30 lines for file sources, ~80 lines for database sources) and register in the source registry.

## Load Strategies

| Strategy | Behavior | When to use |
|----------|----------|-------------|
| `full` | Atomic swap (drop + recreate) | Small reference tables, no history needed |
| `incremental` | Partition overwrite (delete + insert on watermark window) | Large transactional tables |
| `append` | Insert only, never delete | Audit trail, compliance, full history |

All three strategies are idempotent — safe to re-run after partial failures.

## This Repo vs Client Projects

**feather-flow** (this repo) is a Python package only — no client config, no client data. See [Installation](#installation) for install options.

Each client lives in its own GitHub repository, scaffolded with `feather init`:

```bash
feather init client-abc
# → creates client-abc/ with feather.yaml, pyproject.toml, .gitignore, .env.example
# → creates transforms/silver/, transforms/gold/, tables/, extracts/ directories
```

## Installation

> The PyPI package is named `feather-flow`; the installed command is `feather`.

### Recommended — global CLI tool

For most users (scaffolding clients, running pipelines locally), install feather-flow as a global `uv` tool:

```bash
uv tool install feather-flow
feather --help
```

Upgrade or pin to a specific version:

```bash
uv tool upgrade feather-flow
uv tool install feather-flow@X.Y.Z
```

### Alternative — project dependency

For teams that need per-project version pinning or reproducibility, add feather-flow to a client project's `pyproject.toml`:

```bash
uv add feather-flow
uv run feather --help
```

Every command then runs via `uv run feather …`.

### One-off — no install

Run any feather command without installing, using `uvx`:

```bash
uvx feather-flow init client-abc     # scaffold a new client
uvx feather-flow validate            # validate a config
uvx feather-flow run                 # run the pipeline
```

`uvx` downloads feather-flow into a throwaway environment for each invocation, so nothing is left on your PATH and there's no `feather` command afterwards — every call must be prefixed with `uvx feather-flow …`. Good for CI, quick trials, and scaffolding the very first client. For day-to-day use, prefer the global install above.

## CLI

```bash
# New client setup
feather init client-abc                # scaffold a new client project directory

# Configuration
feather validate                       # validate config, resolve paths — no execution
feather discover                       # discover source schema, write JSON, and auto-open the bundled schema viewer
feather view [PATH] [--port 8000]      # serve an existing schema output folder manually

# Pipeline operations
feather setup                          # init state DB + schemas + apply transforms (optional — run creates them automatically)
feather run                            # run all tables (extract + transform in one verb)
feather run --table sales              # run a single table only
feather status                         # last run status per table (all-time history)
feather history                        # show run history (recent runs, filterable)
feather history --trigger transform    # show only rows written by `feather transform`

# Verb trio (dev/test iteration loop — see note below)
feather extract                        # pull curated tables into bronze.* (dev-only, isolated state)
feather extract --table sales,customer # comma-separated table filter
feather extract --source afans,nim     # comma-separated source_db filter
feather extract --refresh              # force re-pull, ignore change detection
feather extract --plan-only            # print pre-flight banner and exit; no extraction
feather transform                      # re-run silver/gold against existing destination — no source touch
feather transform --mode prod          # honour `-- materialized: true` markers; gold becomes TABLEs

# Per-table extraction overrides (stored in state DB)
feather extract override list                                    # show all active overrides
feather extract override set <table> --window-column "Col"      # fix auto-derived window column
feather extract override set <table> --window-strategy single   # force single-window extract
feather extract override clear <table>                          # remove override for a table
```

> **Breaking change — `feather cache` → `feather extract`.** The previous `feather cache` verb has been renamed to `feather extract`. The old name no longer works; running `feather cache` returns "No such command". Update any scripts that invoke `feather cache`. The rename is hard (no alias) — see [`openspec/changes/feather-transform/specs/extract-verb-rename/spec.md`](openspec/changes/feather-transform/specs/extract-verb-rename/spec.md) for the rationale.

All commands accept `--config PATH` (default: `feather.yaml`). `run`, `setup`, `extract`, and `transform` accept `--mode dev|prod|test` to override the config mode. `history` accepts `--table`, `--limit`, and `--trigger <run|extract|transform|schedule>`.

**Note:** `feather setup --mode prod` applies gold transforms as materialized tables, which requires bronze/silver data to already exist. Run `feather run` first, then `feather setup --mode prod` to materialize gold.

### Dev iteration loop — `extract && transform`

The canonical dev/test iteration loop is:

```bash
uv run feather extract && uv run feather transform   # bronze pull + silver/gold rebuild, no source on the second call
```

This trio (`extract`, `transform`, `run`) is the **dev/test** iteration loop. For production deployments, use `feather run` — the verb composability promise is honest about a known asymmetry in prod (`feather run` in prod skips bronze and writes column-mapped rows directly to `silver.*`, while `feather extract` always writes to `bronze.*`). See [`docs/issues/rethink-mode-system.md`](docs/issues/rethink-mode-system.md) for the open issue and planned cleanup.

Run history filter: `uv run feather history --trigger transform` shows only rows written by `feather transform` (similarly for `extract`, `run`, `schedule`). Pre-rename rows have `trigger=NULL` and appear only in unfiltered output.

### `feather extract`

Pull curated source tables into local `bronze.*` for offline development. Skips unchanged sources on re-run. Isolated state — never touches `feather run`'s watermarks.

```bash
feather extract                                # all curated tables, skip unchanged
feather extract --table sales,customer         # comma-separated, by bronze name
feather extract --source afans,nimbalyst       # comma-separated, by source_db
feather extract --table sales --source afans   # intersect
feather extract --refresh                      # force re-pull of all tables
feather extract --refresh --table sales        # force re-pull of specific tables
```

`feather extract` requires `discovery/curation.json`. Run `feather discover` first to generate it.

`feather extract` never runs silver/gold transforms. After a fresh extract, run `feather transform` (re-runs silver/gold with no source touch) or `feather run` (full pipeline) to create silver views and materialize gold tables.

`feather extract` is dev-only. It will refuse to run when the effective mode is `prod` (via `mode: prod` in `feather.yaml` or `FEATHER_MODE=prod`).

#### Pre-flight banner

Before extracting from any DB source (SQL Server, PostgreSQL, MySQL), feather-flow classifies each table by estimated row and column count, derives the extraction shape, and prints a banner:

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

Each line explained:

- `rows (est)` — cheap rowcount from the DB catalog (e.g. `sys.dm_db_partition_stats` for SQL Server); never a full `COUNT(*)`.
- `columns` — total columns in the source; if a bronze projection SQL exists, the projected count is shown in parentheses.
- `bucket` — SMALL / MEDIUM / LARGE, derived from the row and column thresholds in `defaults.extract.*`.
- `transport` — which transport will be used and with what parameters (`partition_on`, `partitions`).
- `windowing` — strategy (date / pk_range / single), the chosen window column, and the window grain.
- `windows planned / done / to run` — total windows, already committed (skipped), and remaining.
- `est wall time` — derived from the last 5 successful runs in `_runs`; falls back to `2,000 rows/sec` (arrow-odbc) or `6,000 rows/sec` (connectorx with partition_on) if no history exists.

Three conditions block extraction and exit code 3 (operator decision required):

- **Wide table, no projection** — `cols > t_wide_cols` (default 60) and no `transforms/bronze/<table>.sql` exists. Bypass: `--accept-wide`.
- **No window column** — bucket is MEDIUM or LARGE and no timestamp/integer-PK column can be auto-derived and no override is set. Bypass: `--single-window`.
- **LARGE without connectorx** — bucket is LARGE and connectorx is not in the transport registry. Bypass: `--accept-slow-transport`.

All active blockers are shown before exiting. Bypass flags are one-shot — the cron line explicitly names each accepted trade-off. There is no blanket `--yes`.

In a TTY with no blockers, feather prompts `Proceed? [Y/n]` (default Y). In a non-TTY (cron, CI), it proceeds silently if no blockers are active. `--plan-only` exits 0 after printing the banner, without opening any source connection.

File sources (CSV, DuckDB, Excel, JSON, SQLite) are not subject to pre-flight — no banner, no rowcount call, no behaviour change.

#### `feather extract override`

Per-table overrides let operators correct auto-derived extraction parameters without touching `feather.yaml`. Overrides are stored in `_extract_overrides` in the state DuckDB and persist across runs.

```bash
feather extract override list
# → prints the current override table (table_name, window_strategy, window_column, window_grain, partition_on)

feather extract override set zakya_dbo_sales --window-column "Invoice Date"
# → next run uses "Invoice Date" as the window column instead of the auto-derived choice

feather extract override set zakya_dbo_sales --window-strategy single
# → bypass windowing for this table permanently (no --single-window flag needed each run)

feather extract override set zakya_dbo_sales --window-grain 7d
# → use 7-day windows instead of the default 1d

feather extract override clear zakya_dbo_sales
# → remove the override; auto-derivation resumes
```

`set` is an upsert — only the supplied fields are written; unset fields remain NULL (inherit auto-derivation). See `feather extract override --help` for all options.

### `feather transform`

Re-run all silver/gold transforms against the existing destination DuckDB. Opens no source connection. Honours the same mode-resolution chain as `feather run` (`--mode` flag > `FEATHER_MODE` env > `feather.yaml` `mode:` > default `dev`).

```bash
feather transform                      # default mode (dev unless configured)
feather transform --mode prod          # honour `-- materialized: true` markers
feather transform --config alt.yaml    # alternate config path
```

DDL kind is a two-axis rule: silver is always VIEW; gold is VIEW by default; gold becomes TABLE only when both (a) the SQL declares `-- materialized: true` and (b) mode is `prod`. Dev and test forcibly downgrade marked gold to views. Each executed transform writes a row to `_runs` with `trigger='transform'`. Bronze tables referenced (via `FROM`/`JOIN`) by any silver transform's SQL body but missing from the destination emit advisory `WARNING:` lines on stderr (collapsed into one summary line if more than five are missing) but never abort. Bronze refs are derived from the SQL body via sqlglot — issue #54 removed the `-- depends_on: bronze.<table>` header convention. Exit codes: `0` success (including zero discovered transforms), `1` transform error, `2` config/destination error.

### Browsing a source schema

Primary flow:

`feather discover` discovers the source schema, writes an auto-named `schema_*.json` in the current working directory, and auto-serves/opens the bundled schema viewer.

```bash
feather discover
```

It uses the schema output directory as the viewer root, prefers port `8000` first, and falls back automatically if that port is busy.

Manual hosting is still possible when you want to serve an existing schema folder yourself:

```bash
feather view
feather view ./schemas
feather view ./schemas --port 8010
```

`feather view` serves an existing directory, so the path must already exist. It uses the same smart port selection as discover: prefer the requested port, then fall back automatically if needed.

## Client Project Layout

`feather init` generates this structure (one repo per client):

```
client-abc/                         # separate GitHub repo per client
├── .gitignore                      # excludes *.duckdb, .env
├── pyproject.toml                  # depends on feather-flow
├── .env.example                    # credential placeholders
├── feather.yaml                    # source, destination, sync, schedules, alerts
├── tables/                         # optional — split by domain
│   ├── sales.yaml
│   └── inventory.yaml
├── transforms/
│   ├── silver/                     # canonical mapping views
│   │   └── sales_invoice.sql       # deps inferred from FROM clauses
│   └── gold/                       # client-specific output
│       └── sales_summary.sql       # -- materialized: true
└── extracts/                       # optional — custom source SELECT queries
    └── sales_invoice.sql
```

`feather_state.duckdb` and `feather_data.duckdb` are created at runtime, gitignored.

## Configuration

### SMB client — silver-direct (no bronze)

Small clients land data directly into silver with column selection at extraction time. No bronze layer needed.

```yaml
sources:
  - type: sqlserver
    connection_string: "${SQL_SERVER_CONNECTION_STRING}"

destination:
  path: ./feather_data.duckdb

sync:
  type: motherduck
  token: "${MOTHERDUCK_TOKEN}"
  database: "client_analytics"

alerts:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  smtp_user: "${ALERT_EMAIL_USER}"
  smtp_password: "${ALERT_EMAIL_PASSWORD}"
  alert_to: "operator@example.com"

schedule_tiers:
  hot: "twice daily"
  cold: weekly

tables:
  - name: sales_invoice
    source_table: dbo.SALESINVOICE
    target_table: silver.sales_invoice
    strategy: incremental
    timestamp_column: ModifiedDate
    schedule: hot
    primary_key: [ID]
    column_map:                       # select 8 of 120 columns, rename to canonical
      ID: invoice_id
      SI_NO: invoice_no
      Custome_Code: customer_code
      NetAmount: net_amount
      ModifiedDate: modified_date
    quality_checks:
      not_null: [invoice_id, customer_code]
```

### Enterprise client — bronze + append (compliance)

```yaml
tables:
  - name: sales_invoice
    source_table: dbo.SALESINVOICE
    target_table: bronze.sales_invoice   # raw, all columns
    strategy: append                     # insert-only, full audit trail
    timestamp_column: ModifiedDate
    schedule: hot
    primary_key: [ID]
```

Silver views over bronze live in `transforms/silver/sales_invoice.sql`. Transform files contain only the SELECT query — the system wraps it in the appropriate `CREATE OR REPLACE VIEW` or `CREATE OR REPLACE TABLE` DDL based on mode and the `-- materialized` flag:

```sql
-- silver: reads from bronze; bronze refs don't create transform-layer edges
SELECT
    ID           AS invoice_id,
    SI_NO        AS invoice_no,
    Custome_Code AS customer_code,
    NetAmount    AS net_amount,
    ModifiedDate AS modified_date,
    _etl_loaded_at,
    _etl_run_id
FROM bronze.sales_invoice
```

### Table splitting (large deployments)

```
client-project/
├── feather.yaml
└── tables/
    ├── sales.yaml       # 8 sales tables
    ├── inventory.yaml   # 6 inventory tables
    └── hr.yaml          # 4 HR tables
```

feather-flow auto-discovers and merges all `.yaml` files in the `tables/` directory.

## Transform Dependencies

Dependencies are inferred from the SQL body. The system parses each transform with `sqlglot`, extracts every `silver.*` / `gold.*` reference from `FROM` / `JOIN` clauses, and builds the topological execution order automatically. No header markup declares edges:

```sql
-- materialized: true
SELECT ...
FROM silver.sales_invoice si
JOIN silver.customer_master cm ON si.customer_code = cm.customer_code
```

The above produces edges to `silver.sales_invoice` and `silver.customer_master`. CTEs, comments, string literals, and table-valued functions (e.g. `read_csv(...)`) do not create phantom edges.

Header comments retain meaning only for information the SQL body cannot express: `-- materialized: true` (gold materialisation in prod mode) and `-- fact_table: <name>` (join-health-check declaration). The `-- depends_on:` header is no longer recognised — see GitHub issue #54.

Transform files contain only the SELECT body plus optional `-- materialized:` / `-- fact_table:` headers. The system generates the DDL: silver transforms become VIEWs, gold transforms with `-- materialized: true` become TABLEs in prod mode (VIEWs in dev/test).

## Dependencies

| Package | Purpose |
|---------|---------|
| duckdb | Local processing, file readers, MotherDuck sync |
| pyarrow | Zero-copy data interchange |
| pyyaml | Config parsing |
| typer | CLI |
| pyodbc | SQL Server / SAP B1 extraction |
| apscheduler | Built-in scheduling |
| openpyxl | Excel `.xls` fallback (`.xlsx` handled natively by DuckDB) |

Alerting uses Python stdlib `smtplib` — no extra dependency.

## Alerting

SMTP email via Python stdlib — works with Gmail, any corporate SMTP relay, or transactional email services. No Slack account, no webhook setup.

| Severity | Trigger |
|----------|---------|
| `[CRITICAL]` | Pipeline failure, load error, type cast quarantine |
| `[WARNING]` | DQ check failure, schema drift |
| `[INFO]` | Schema drift (informational) |

No-op if `alerts` section is not configured.

## Observability

Every run is recorded in `feather_state.duckdb`:

```sql
-- What happened in the last 24 hours?
SELECT table_name, status, rows_extracted, rows_loaded, duration_sec
FROM _runs
WHERE started_at > now() - INTERVAL 1 DAY
ORDER BY started_at DESC;

-- Any DQ failures?
SELECT * FROM _dq_results WHERE result = 'fail';

-- Schema drift detected?
SELECT table_name, schema_changes FROM _runs
WHERE schema_changes IS NOT NULL;
```

## Documentation

- [PRD v1.5](docs/prd.md) — Full requirements, EARS spec, design decisions
- [Research](docs/research.md) — Design research synthesis from 7 research agents

## Roadmap

**v1 (current):** Core pipeline — extraction, loading (full/incremental/append), silver/gold transforms, scheduling, DQ checks, schema drift detection, SMTP alerts, state management.

**v2:** `feather-connectors` — canonical silver transform library for SAP B1, SAP S4 HANA, and common Indian ERPs. Reuse silver mappings across all clients on the same source system.

**v3+:** LLM agent interface for client analysts — last-mile dashboard customisation guided by AI, working against the canonical silver layer.

## Status

**Pre-alpha** — Architecture complete, implementation starting. See [PRD](docs/prd.md) for the full feature roadmap (25 features across 5 phases).

## License

MIT
