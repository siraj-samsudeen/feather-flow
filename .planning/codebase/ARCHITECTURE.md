# Architecture

## Overview

feather-flow is a **config-driven ETL pipeline** that extracts data from heterogeneous ERP sources (SQL Server, PostgreSQL, DuckDB files, CSV, SQLite, Excel, JSON) into a local DuckDB data warehouse with bronze/silver/gold layered transforms. It replaces the typical dlt + dbt + Dagster stack with a single ~1,200 LOC Python package.

**Architecture pattern:** Single-process, sequential ETL pipeline with YAML-driven configuration. One package deployed across many clients — each client gets their own config repo, same `feather-flow` dependency.

**Key architectural property:** The entire pipeline is local-first — all processing happens in DuckDB on the client machine. Only gold-layer tables optionally sync to MotherDuck (cloud).

## Entry Points

### CLI (primary entry point)
- **Binary:** `feather` (registered via `[project.scripts]` in `pyproject.toml`)
- **Implementation:** `src/feather/cli.py` — Typer app with 6 commands:
  - `feather init <project>` — scaffold client project (interactive/non-interactive wizard)
  - `feather validate --config PATH` — validate YAML config, write `feather_validation.json`
  - `feather discover --config PATH` — list tables/columns from source
  - `feather setup --config PATH [--mode dev|prod|test]` — init state DB + schemas + apply transforms
  - `feather run --config PATH [--mode dev|prod|test] [--table NAME]` — extract and load tables
  - `feather status --config PATH` — last run status per table
  - `feather history --config PATH [--table NAME] [--limit N]` — run history

All commands accept `--json` for NDJSON output mode (machine-readable).

### Programmatic API
- `feather.pipeline.run_all(config, config_path, table_filter)` — run full pipeline
- `feather.pipeline.run_table(config, table, working_dir)` — run single table
- `feather.config.load_config(config_path, mode_override)` — parse and validate YAML

## Core Abstractions

### Source Protocol (`src/feather/sources/__init__.py`)
```python
class Source(Protocol):
    def check(self) -> bool                           # connectivity test
    def discover(self) -> list[StreamSchema]           # enumerate tables
    def extract(self, table, ...) -> pa.Table          # extract data as PyArrow
    def detect_changes(self, table, last_state) -> ChangeResult  # change detection
    def get_schema(self, table) -> list[tuple[str, str]]         # column metadata
```
Two base classes implement shared behavior:
- `FileSource` (`src/feather/sources/file_source.py`) — mtime + MD5 two-tier change detection for file-based sources
- `DatabaseSource` (`src/feather/sources/database_source.py`) — watermark formatting + WHERE clause building for DB sources

### Destination Protocol (`src/feather/destinations/__init__.py`)
```python
class Destination(Protocol):
    def load_full(self, table, data, run_id) -> int
    def load_incremental(self, table, data, run_id, timestamp_column) -> int
    def execute_sql(self, sql) -> None
    def sync_to_remote(self, tables, remote_config) -> None
```
Single implementation: `DuckDBDestination` (`src/feather/destinations/duckdb.py`)

### Configuration Dataclasses (`src/feather/config.py`)
- `FeatherConfig` — top-level config container
- `SourceConfig` — source type + path/connection_string
- `DestinationConfig` — DuckDB output path
- `TableConfig` — per-table: name, source_table, strategy, column_map, quality_checks, etc.
- `DefaultsConfig` — overlap_window_minutes, batch_size, row_limit
- `AlertsConfig` — SMTP settings

### Transform Engine (`src/feather/transforms.py`)
- `TransformMeta` — parsed SQL file metadata (depends_on, materialized flag)
- `TransformResult` — execution outcome
- Topological sort via `graphlib.TopologicalSorter` for dependency ordering

### State Management (`src/feather/state.py`)
- `StateManager` — manages `feather_state.duckdb` with tables: `_watermarks`, `_runs`, `_dq_results`, `_schema_snapshots`, `_state_meta`, `_run_steps`

## Data Flow

```
1. CONFIG LOAD
   feather.yaml + tables/*.yaml → load_config() → FeatherConfig
   (env var resolution, path resolution, validation)

2. PER-TABLE EXTRACTION LOOP (pipeline.run_table)
   For each TableConfig:
   ┌─ Change Detection ─────────────────────────────────┐
   │  source.detect_changes(table, watermark)            │
   │  File sources: mtime → MD5 hash (two-tier)         │
   │  DB sources: CHECKSUM_AGG + COUNT(*)                │
   │  → skip if unchanged                               │
   └─────────────────────────────────────────────────────┘
           │ changed
           ▼
   ┌─ Schema Drift Detection ───────────────────────────┐
   │  source.get_schema() vs state.get_schema_snapshot() │
   │  → detect added/removed/type_changed columns        │
   │  → ALTER TABLE for added columns                    │
   │  → alert on drift                                   │
   └─────────────────────────────────────────────────────┘
           │
           ▼
   ┌─ Extract ──────────────────────────────────────────┐
   │  source.extract(table, columns, filter, watermark)  │
   │  → PyArrow Table                                    │
   │  → apply column_map (rename/select in prod mode)    │
   │  → apply dedup if configured                        │
   │  → boundary dedup for incremental (PK hash filter)  │
   └─────────────────────────────────────────────────────┘
           │
           ▼
   ┌─ Load (3 strategies) ──────────────────────────────┐
   │  full:        DROP + CREATE (atomic swap via staging)│
   │  incremental: DELETE >= min_ts + INSERT             │
   │  append:      INSERT only (audit trail)             │
   │  All add _etl_loaded_at + _etl_run_id columns      │
   └─────────────────────────────────────────────────────┘
           │
           ▼
   ┌─ Data Quality Checks ─────────────────────────────┐
   │  row_count (always), not_null, unique, duplicate    │
   │  Results → _dq_results table + alert on failure     │
   └─────────────────────────────────────────────────────┘
           │
           ▼
   ┌─ State Update ─────────────────────────────────────┐
   │  write_watermark(), record_run()                    │
   │  Retry backoff on failure (linear, 15min base, 2h cap)│
   └─────────────────────────────────────────────────────┘

3. POST-EXTRACTION TRANSFORMS (pipeline.run_all)
   transforms/silver/*.sql → CREATE OR REPLACE VIEW
   transforms/gold/*.sql   → VIEW (dev/test) or TABLE (prod, if materialized)
   Topological sort on -- depends_on: declarations
   Join health check for gold tables with -- fact_table: declared

4. OBSERVABILITY
   feather_state.duckdb: _runs, _watermarks, _dq_results, _schema_snapshots
   feather_log.jsonl: structured JSON logging
```

## Module Boundaries

```
                         ┌──────────┐
                         │  cli.py  │  ← Typer commands (thin wrapper)
                         └────┬─────┘
                              │
              ┌───────────────┼──────────────────┐
              │               │                  │
              ▼               ▼                  ▼
        ┌──────────┐   ┌───────────┐   ┌──────────────┐
        │config.py │   │pipeline.py│   │init_wizard.py│
        │(parse +  │   │(orchestr.)│   │(scaffolding) │
        │validate) │   └─────┬─────┘   └──────────────┘
        └──────────┘         │
                    ┌────────┼────────────┬──────────┐
                    │        │            │          │
                    ▼        ▼            ▼          ▼
             ┌──────────┐ ┌─────────┐ ┌──────┐ ┌────────────┐
             │sources/  │ │dest/    │ │state │ │transforms  │
             │registry  │ │duckdb.py│ │.py   │ │.py         │
             │+ 7 impls │ └─────────┘ └──────┘ └────────────┘
             └──────────┘
                                ┌──────────────────┐
                                │  Cross-cutting:  │
                                │  alerts.py       │
                                │  dq.py           │
                                │  schema_drift.py │
                                │  output.py       │
                                └──────────────────┘
```

### Dependency direction:
- `cli` → `config`, `pipeline`, `state`, `sources.registry`, `destinations.duckdb`, `transforms`, `init_wizard`, `output`
- `pipeline` → `config`, `sources.registry`, `destinations.duckdb`, `state`, `dq`, `schema_drift`, `alerts`, `transforms`
- `sources/*` → `sources.__init__` (Protocol), `sources.file_source` or `sources.database_source`
- `transforms` → standalone (only needs `duckdb`, `graphlib`)
- `dq` → standalone (only needs `duckdb`)
- `schema_drift` → standalone (pure Python)
- `alerts` → standalone (stdlib `smtplib`)
- `state` → standalone (only needs `duckdb`)

**No circular dependencies.** Pipeline is the central orchestrator. Sources and destinations are pluggable via Protocol/registry.

## Key Design Decisions

### 1. PyArrow as interchange format
All sources extract into `pa.Table`, all destinations load from `pa.Table`. This provides zero-copy data transfer between sources and DuckDB, and a clean boundary between source-specific extraction logic and destination-specific loading logic.

### 2. Protocol-based source abstraction (not inheritance)
Sources implement a Python `Protocol` (structural typing), not an ABC. This allows duck-typing — any class with the right methods works. Two optional base classes (`FileSource`, `DatabaseSource`) provide shared behavior without forcing inheritance.

### 3. Config-driven, not code-driven
Tables are defined in YAML, not Python. Adding a new table to a client means editing `feather.yaml`, not writing code. Transforms are raw SQL files. The framework wraps them in DDL.

### 4. Local DuckDB as the compute engine
DuckDB handles CSV/JSON/Excel/Parquet reading natively, runs SQL transforms, and serves as the destination store. This eliminates the need for Spark, Pandas, or separate transform engines.

### 5. Two-tier change detection for files
File sources use mtime first (cheap), then MD5 hash (expensive) only when mtime changes. This avoids unnecessary re-reads of large files while catching actual content changes (vs. mere `touch`).

### 6. Atomic load strategies
- `full`: staging table + rename (no window of missing data)
- `incremental`: DELETE + INSERT in a transaction (partition overwrite)
- `append`: INSERT only (audit-safe)

### 7. Bronze optional, silver as views
Silver layer is always VIEWs (lazy, no pipeline step needed). Bronze is optional — small clients skip it, enterprise clients use it for compliance/audit. Gold is views in dev/test, materialized tables in prod.

### 8. Mode-aware behavior (`dev` / `prod` / `test`)
- `dev`: all transforms as views, bronze target default
- `prod`: gold materialized, silver target default, column_map applied at extraction
- `test`: row_limit enforced, all transforms as views

### 9. State separated from data
`feather_state.duckdb` (watermarks, run history, DQ results) is separate from `feather_data.duckdb` (extracted data + transforms). This allows clearing data without losing run history, and vice versa.
