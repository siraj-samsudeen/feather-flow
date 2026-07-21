# Project Structure

## Directory Layout

```
feather-flow/
├── CLAUDE.md                          # Agent context — points to key docs
├── README.md                          # Full architecture doc with layer model, config examples
├── pyproject.toml                     # Package config (hatchling), deps, CLI entry point
├── uv.lock                            # Locked dependencies
├── .python-version                    # Python version pin
├── .nvmrc                             # Node version (for tooling)
│
├── src/feather/                       # Main package (installed as `feather`)
│   ├── __init__.py                    # Version string only
│   ├── cli.py                         # Typer CLI — 6 commands (init, validate, discover, setup, run, status, history)
│   ├── config.py                      # YAML parsing, env var resolution, validation, dataclasses
│   ├── pipeline.py                    # Core orchestrator — run_table(), run_all(), column mapping, dedup
│   ├── state.py                       # StateManager — watermarks, run history, DQ results, schema snapshots, retry backoff
│   ├── transforms.py                  # SQL transform engine — parse, discover, topo-sort, execute, join health
│   ├── dq.py                          # Data quality checks (not_null, unique, duplicate, row_count)
│   ├── schema_drift.py                # Schema drift detection (added/removed/type_changed columns)
│   ├── alerts.py                      # SMTP email alerting (CRITICAL/WARNING/INFO)
│   ├── output.py                      # JSON/NDJSON output helpers for --json mode
│   ├── init_wizard.py                 # Project scaffolding + interactive/non-interactive wizard
│   ├── py.typed                       # PEP 561 marker
│   │
│   ├── sources/                       # Source connectors (Protocol-based)
│   │   ├── __init__.py                # Source Protocol, StreamSchema, ChangeResult dataclasses
│   │   ├── registry.py                # SOURCE_REGISTRY dict + create_source() factory
│   │   ├── file_source.py             # FileSource base — mtime+MD5 change detection, path resolution
│   │   ├── database_source.py         # DatabaseSource base — watermark formatting, WHERE building
│   │   ├── duckdb_file.py             # DuckDB file source (ATTACH-based)
│   │   ├── csv.py                     # CSV source (directory of .csv files, glob support)
│   │   ├── sqlite.py                  # SQLite source (sqlite_scan)
│   │   ├── excel.py                   # Excel source (.xlsx native, .xls via openpyxl)
│   │   ├── json_source.py             # JSON source (read_json)
│   │   ├── sqlserver.py               # SQL Server source (pyodbc → PyArrow, batched)
│   │   └── postgres.py                # PostgreSQL source (psycopg2 → PyArrow)
│   │
│   └── destinations/                  # Destination connectors
│       ├── __init__.py                # Destination Protocol
│       └── duckdb.py                  # DuckDBDestination — load_full, load_incremental, load_append
│
├── tests/                             # Test suite (~341 tests)
│   ├── __init__.py
│   ├── conftest.py                    # Shared fixtures (client_db, config_path, csv_data_dir, etc.)
│   ├── fixtures/                      # Test data files
│   │   ├── client.duckdb              # DuckDB fixture with icube schema (SALESINVOICE, etc.)
│   │   ├── client_update.duckdb       # Modified fixture for change detection tests
│   │   ├── sample_erp.duckdb          # Fixture with erp.sales for incremental tests
│   │   ├── sample_erp.sqlite          # SQLite fixture
│   │   ├── csv_data/                  # CSV fixtures (customers.csv, orders.csv, products.csv)
│   │   ├── csv_glob/                  # CSV glob fixtures (sales_jan.csv, sales_feb.csv)
│   │   ├── excel_data/                # Excel fixtures (.xlsx)
│   │   └── json_data/                 # JSON fixtures
│   ├── test_e2e.py                    # End-to-end pipeline tests
│   ├── test_integration.py            # Integration tests (multi-table runs)
│   ├── test_pipeline.py               # Pipeline orchestration tests
│   ├── test_config.py                 # Config parsing/validation tests
│   ├── test_cli.py                    # CLI command tests (via typer.testing.CliRunner)
│   ├── test_sources.py                # Source connector tests (DuckDB, CSV, SQLite)
│   ├── test_destinations.py           # DuckDB destination load tests
│   ├── test_transforms.py             # Transform engine tests
│   ├── test_state.py                  # State manager tests
│   ├── test_dq.py                     # Data quality check tests
│   ├── test_schema_drift.py           # Schema drift detection tests
│   ├── test_alerts.py                 # SMTP alert tests
│   ├── test_incremental.py            # Incremental strategy tests
│   ├── test_append.py                 # Append strategy tests
│   ├── test_boundary_dedup.py         # Boundary deduplication tests
│   ├── test_dedup.py                  # Dedup configuration tests
│   ├── test_csv_glob.py               # CSV glob pattern tests
│   ├── test_excel.py                  # Excel source tests
│   ├── test_json.py                   # JSON source tests
│   ├── test_json_output.py            # --json output format tests
│   ├── test_mode.py                   # dev/prod/test mode behavior tests
│   ├── test_retry.py                  # Retry backoff tests
│   ├── test_init_wizard.py            # Project scaffolding wizard tests
│   ├── test_sqlserver.py              # SQL Server source tests (mocked)
│   ├── test_postgres.py               # PostgreSQL source tests (mocked)
│   └── test_table_filter_and_history.py  # --table filter + history command tests
│
├── scripts/                           # Utility scripts
│   ├── hands_on_test.sh               # Integration test suite (72 checks, bash)
│   ├── create_test_fixture.py         # Generate client.duckdb fixture
│   ├── create_csv_sqlite_fixtures.py  # Generate CSV + SQLite fixtures
│   ├── create_excel_fixture.py        # Generate Excel fixtures
│   ├── create_postgres_test_fixture.py # Generate Postgres test data
│   └── create_sample_erp_fixture.py   # Generate sample_erp.duckdb fixture
│
├── docs/                              # Project documentation
│   ├── prd.md                         # Product Requirements Document (v1.5, EARS spec)
│   ├── research.md                    # Design research synthesis
│   ├── CONTRIBUTING.md                # Work conventions
│   ├── TESTING-PHILOSOPHY.md          # Testing approach
│   ├── testing-feather-flow.md         # Testing guide
│   ├── plans/                         # Implementation plan history (per-slice)
│   └── reviews/                       # Code review records
│
├── guided-tours/                      # Interactive walkthrough materials
│
├── test_csv_project/                  # Example client project (CSV source)
├── test_duckdb_project/               # Example client project (DuckDB source)
├── test_large_project/                # Example client project (large dataset)
└── sample_erp.duckdb                  # Sample ERP data for demos
```

## Key Locations

| What | Where |
|------|-------|
| Package source | `src/feather/` |
| CLI entry point | `src/feather/cli.py` → `app` (Typer) |
| Pipeline orchestrator | `src/feather/pipeline.py` |
| Configuration | `src/feather/config.py` |
| Source connectors | `src/feather/sources/` (7 implementations) |
| Destination connectors | `src/feather/destinations/` (1 implementation) |
| Tests | `tests/` (~341 pytest tests) |
| Integration tests (bash) | `scripts/hands_on_test.sh` (72 checks) |
| Test fixtures | `tests/fixtures/` |
| Fixture generators | `scripts/create_*.py` |
| Requirements doc | `docs/prd.md` |
| Agent context | `CLAUDE.md` + `.claude/rules/feather-flow-project.md` |

## Module Map

| Module | Purpose | LOC (approx) |
|--------|---------|------|
| `cli.py` | Typer CLI commands — thin wrappers that delegate to config/pipeline/state | ~250 |
| `config.py` | YAML parsing, env var resolution, path resolution, validation, dataclasses | ~290 |
| `pipeline.py` | Central orchestrator — run_table (extract→load→DQ→state), run_all (+ transforms) | ~545 |
| `state.py` | StateManager — DuckDB-backed watermarks, run history, DQ results, schema snapshots, retry | ~350 |
| `transforms.py` | SQL transform discovery, parsing, topological sort, execution, join health | ~250 |
| `dq.py` | Data quality checks (not_null, unique, duplicate, row_count) | ~80 |
| `schema_drift.py` | Compare current vs stored schema — added/removed/type_changed | ~60 |
| `alerts.py` | SMTP email sending — CRITICAL/WARNING/INFO severity | ~60 |
| `output.py` | JSON/NDJSON emit helpers for `--json` mode | ~20 |
| `init_wizard.py` | `feather init` — project scaffolding, interactive/non-interactive wizard | ~250 |
| `sources/__init__.py` | `Source` Protocol, `StreamSchema`, `ChangeResult` dataclasses | ~35 |
| `sources/registry.py` | `SOURCE_REGISTRY` dict, `create_source()` factory | ~30 |
| `sources/file_source.py` | `FileSource` base — mtime + MD5 change detection, path/hash helpers | ~75 |
| `sources/database_source.py` | `DatabaseSource` base — watermark formatting, WHERE clause building | ~35 |
| `sources/duckdb_file.py` | DuckDB file source — ATTACH + query | ~85 |
| `sources/csv.py` | CSV directory source — glob support, per-file change detection | ~160 |
| `sources/sqlite.py` | SQLite source — sqlite_scan() | ~80 |
| `sources/excel.py` | Excel source — .xlsx (DuckDB native) + .xls (openpyxl fallback) | ~90 |
| `sources/json_source.py` | JSON source — read_json() | ~70 |
| `sources/sqlserver.py` | SQL Server — pyodbc, batched fetch, CHECKSUM_AGG change detection | ~200 |
| `sources/postgres.py` | PostgreSQL — psycopg2, batched fetch, md5 aggregate change detection | ~180 |
| `destinations/__init__.py` | `Destination` Protocol | ~10 |
| `destinations/duckdb.py` | DuckDBDestination — full/incremental/append load strategies | ~120 |

## Naming Conventions

### Files
- **Source modules:** Named after the source type — `csv.py`, `sqlite.py`, `sqlserver.py`, `postgres.py`, `duckdb_file.py`, `excel.py`, `json_source.py` (`json_source` avoids collision with stdlib `json`)
- **Test files:** `test_<module>.py` or `test_<feature>.py` — one test file per module or major feature
- **Fixture scripts:** `scripts/create_<fixture_name>.py`

### Classes
- Sources: `<Type>Source` — `CsvSource`, `DuckDBFileSource`, `SqlServerSource`, `PostgresSource`, `SqliteSource`, `ExcelSource`, `JsonSource`
- Base classes: `FileSource`, `DatabaseSource`
- Destination: `DuckDBDestination`
- Config: `FeatherConfig`, `SourceConfig`, `DestinationConfig`, `TableConfig`, `DefaultsConfig`, `AlertsConfig`
- State: `StateManager`
- Transforms: `TransformMeta`, `TransformResult`
- DQ: `DQResult`
- Schema: `StreamSchema`, `ChangeResult`, `DriftReport`

### Functions
- Pipeline: `run_table()`, `run_all()`, `_apply_column_map()`, `_apply_dedup()`, `_filter_boundary_rows()`
- Config: `load_config()`, `write_validation_json()`
- Transforms: `discover_transforms()`, `build_execution_order()`, `execute_transforms()`, `rebuild_materialized_gold()`
- Prefixed with `_` for internal/private helpers

### YAML Configuration
- `feather.yaml` — main config file
- `tables/*.yaml` — optional split table definitions (auto-merged)
- `transforms/silver/*.sql` — silver transform SQL files
- `transforms/gold/*.sql` — gold transform SQL files

## Important Files

| File | Role |
|------|------|
| `src/feather/pipeline.py` | **Heart of the system** — orchestrates the entire extract→load→DQ→state cycle |
| `src/feather/config.py` | **Config contract** — defines all dataclasses, parsing, and validation logic |
| `src/feather/sources/__init__.py` | **Source Protocol** — the interface all sources must satisfy |
| `src/feather/sources/registry.py` | **Source factory** — maps type strings to source classes |
| `src/feather/state.py` | **State persistence** — watermarks, run history, retry backoff, boundary hashes |
| `src/feather/destinations/duckdb.py` | **Load strategies** — full (atomic swap), incremental (partition overwrite), append |
| `src/feather/transforms.py` | **Transform engine** — SQL file discovery, dependency ordering, VIEW/TABLE creation |
| `src/feather/cli.py` | **User interface** — all CLI commands |
| `pyproject.toml` | **Package metadata** — deps, CLI registration, build system |
| `tests/conftest.py` | **Test infrastructure** — shared fixtures for all test modules |
| `scripts/hands_on_test.sh` | **Integration validation** — 72-check bash test suite for end-to-end scenarios |
| `docs/prd.md` | **Requirements** — full PRD with EARS spec, 25 features across 5 phases |
