# feather-flow

Config-driven Python ETL for extracting from heterogeneous ERP sources into local DuckDB.
See `docs/prd.md` for requirements, `README.md` for architecture, `docs/CONTRIBUTING.md` for work conventions.

## Before You Start

Run both test suites and confirm green:

```bash
uv run pytest -q               # currently: 460 tests
bash scripts/hands_on_test.sh  # currently: 61 checks
```

If anything is red before you touch anything, report immediately.

## Tech Stack

- **Language:** Python >=3.10, managed with `uv`
- **Core:** DuckDB (local processing + file readers), PyArrow (interchange), PyYAML (config), Typer (CLI)
- **Connectors:** pyodbc (SQL Server), psycopg2-binary (PostgreSQL), openpyxl (Excel)
- **Future (in pyproject.toml, not yet used):** APScheduler (scheduling), smtplib (alerting)
- **Testing:** pytest, pytest-cov | **Quality:** ruff

## Directory Structure

```
src/feather_flow/
  cli.py              Typer CLI: validate, discover, run, setup, status, init
  config.py           YAML config loader + validation
  pipeline.py         run_all() orchestration + change detection integration
  transforms.py       Silver/gold SQL transform execution + mode-aware DDL
  state.py            StateManager: _runs + _watermarks tables in DuckDB
  init_wizard.py      `feather init` client scaffolding
  sources/            Source protocol + implementations
    file_source.py    FileSource base: detect_changes() with mtime + MD5 hash
    database_source.py DatabaseSource base with _format_watermark hook
    duckdb_file.py    DuckDB file reader
    csv.py            CSV file reader
    sqlite.py         SQLite database reader
    excel.py          Excel (.xlsx) reader via DuckDB excel extension
    json_source.py    JSON (.json/.jsonl) reader via DuckDB read_json_auto
    sqlserver.py      SQL Server reader (pyodbc)
    postgres.py       PostgreSQL reader (psycopg2)
    registry.py       Source type registry (type string -> class)
  destinations/       Destination protocol (duckdb only for now)
tests/                pytest (real DuckDB fixtures, no mocking)
scripts/              hands_on_test.sh (CLI integration), fixture generators
docs/                 PRD, plans/, reviews/, CONTRIBUTING.md
```

## Test Fixtures

| File | Schema | Rows | Use for |
|---|---|---|---|
| `tests/fixtures/client.duckdb` | `icube.*`, 6 tables | ~14K | Real-world ERP, messy schemas |
| `tests/fixtures/client_update.duckdb` | `icube.*`, 3 tables | ~12K | Future incremental tests |
| `tests/fixtures/sample_erp.duckdb` | `erp.*`, 3 tables | 12 | Fast tests, NULL handling |
| `tests/fixtures/sample_erp.sqlite` | `erp.*`, 3 tables | 12 | SQLite source tests |
| `tests/fixtures/csv_data/` | 3 CSV files (orders, customers, products) | ~10 each | CSV source tests |

Regenerate: `python scripts/create_sample_erp_fixture.py` (DuckDB) and `python scripts/create_csv_sqlite_fixtures.py` (CSV + SQLite)

## Dev Commands

| Task | Command |
|------|---------|
| Install | `uv sync` |
| Test | `uv run pytest -q` |
| CLI integration | `bash scripts/hands_on_test.sh` |
| Coverage | `uv run pytest -q --cov=src --cov-fail-under=80` |
| Lint | `ruff check .` |
| Format | `ruff format .` |

## Current State

| Slice | Plan | Status |
|---|---|---|
| 1 — foundation + CSV/SQLite sources | `docs/plans/2026-03-26-slice1-foundation.md` | VERIFIED |
| 2 — change detection | `docs/plans/2026-03-27-slice2-change-detection.md` | VERIFIED |
| 3 — incremental extraction | `docs/plans/2026-03-27-slice3-incremental-extraction.md` | VERIFIED |
| mode — dev/prod/test | `docs/plans/2026-03-28-mode-dev-prod-test.md` | VERIFIED |
| MVP test bugfixes | `docs/plans/2026-03-28-mvp-test-bugfixes.md` | VERIFIED |
| parallel slices + postgres | `docs/plans/2026-03-28-parallel-slices-postgres.md` | VERIFIED |

## Work Conventions

Full conventions in `docs/CONTRIBUTING.md`. The short version:

- **Always create a plan doc before starting work.** Use today's date, copy header format from existing plans.
- Plan chain: `docs/plans/YYYY-MM-DD-<slice>-<topic>.md` → `docs/reviews/` → `docs/plans/...-fixes.md`
- Bug fixing: read review doc, create fixes plan, graduate `TestKnownBugs` tests, update `hands_on_test.sh`. Never edit review findings.
- All tests use real DuckDB fixtures, no mocking.

## Source Protocol

New file sources: extend `FileSource` (~30 lines), implement `_reader_sql()` and `_source_path_for_table()`. DB sources: implement full `Source` protocol (~80 lines). Register in `sources/registry.py`.

Change detection: `FileSource.detect_changes()` checks mtime first, falls back to MD5 hash. Returns `ChangeResult` with reason and metadata. Pipeline skips unchanged tables, updates watermarks on success.

## Entry Point

`feather = "feather_flow.cli:app"` (Typer). All commands accept `--config PATH` (default: `feather.yaml`).
