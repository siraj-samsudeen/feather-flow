# Code Conventions

## Code Style

### Formatting & Linting
- **Formatter/Linter:** Ruff v0.14.4 (`.ruff_cache/` present; no explicit `[tool.ruff]` section in `pyproject.toml` — uses default settings)
- **Commands:** `ruff check .` (lint) and `ruff format .` (format)
- **Build system:** Hatchling (`[build-system]` in `pyproject.toml`)
- **Package manager:** `uv` (project uses `uv sync`, `uv run pytest`, etc.)
- **Python version:** `>=3.10` (uses `from __future__ import annotations` in every module for PEP 604 union types like `str | None`)
- **Line length:** Default ruff (88 chars). Code generally stays under this limit with natural wrapping.
- **Quotes:** Double quotes throughout (Python default, ruff default)

### Future Annotations
Every `.py` file in `src/feather/` starts with:
```python
from __future__ import annotations
```
This enables `str | None` syntax and deferred type evaluation. Test files use it inconsistently — some have it, some don't.

## Naming Conventions

### Variables & Functions
- **snake_case** for all functions, methods, variables, and parameters
- Private helpers prefixed with underscore: `_resolve_env_vars()`, `_parse_tables()`, `_build_where_clause()`, `_compute_pk_hashes()`
- Test helper functions also use underscore prefix: `_write_config()`, `_minimal_config()`, `_make_bronze_tables()`, `_sample_arrow_table()`

### Classes
- **PascalCase** for all classes: `FeatherConfig`, `StateManager`, `DuckDBDestination`, `FileSource`, `DuckDBFileSource`
- Dataclasses used extensively for value objects: `SourceConfig`, `DestinationConfig`, `TableConfig`, `DefaultsConfig`, `AlertsConfig`, `FeatherConfig`, `RunResult`, `DQResult`, `StreamSchema`, `ChangeResult`, `TransformMeta`, `TransformResult`, `DriftReport`
- Test dataclasses for mocking: `FakeAlertsConfig` (in `tests/test_alerts.py`)

### Files & Modules
- **snake_case** for all Python files: `file_source.py`, `duckdb_file.py`, `json_source.py`, `database_source.py`
- Source modules named after the technology: `csv.py`, `sqlite.py`, `postgres.py`, `sqlserver.py`, `excel.py`
- Test files named `test_<module>.py` or `test_<feature>.py` — see `docs/TESTING-PHILOSOPHY.md` for full mapping

### Constants
- **UPPER_SNAKE_CASE** for module-level constants: `FILE_SOURCE_TYPES`, `VALID_STRATEGIES`, `VALID_SCHEMA_PREFIXES`, `VALID_MODES`, `SCHEMAS`, `SCHEMA_VERSION`, `SOURCE_REGISTRY`
- Private constants also uppercased with underscore: `_SQL_IDENTIFIER_RE`, `_UNRESOLVED_ENV_RE`, `_BACKOFF_BASE_MINUTES`, `_BACKOFF_CAP_MINUTES`

### Database/SQL Naming
- State tables use underscore prefix: `_state_meta`, `_watermarks`, `_runs`, `_run_steps`, `_dq_results`, `_schema_snapshots`
- DuckDB schemas: `bronze`, `silver`, `gold`, `_quarantine`
- ETL metadata columns: `_etl_loaded_at`, `_etl_run_id`

## Patterns & Idioms

### Dataclasses (Primary Data Pattern)
All configuration, results, and DTOs are `@dataclass` objects (from `dataclasses` module — no Pydantic). Found in:
- `src/feather/config.py` — `SourceConfig`, `DestinationConfig`, `DefaultsConfig`, `TableConfig`, `AlertsConfig`, `FeatherConfig`
- `src/feather/pipeline.py` — `RunResult`
- `src/feather/dq.py` — `DQResult`
- `src/feather/sources/__init__.py` — `StreamSchema`, `ChangeResult`
- `src/feather/transforms.py` — `TransformMeta`, `TransformResult`
- `src/feather/schema_drift.py` — `DriftReport`

### Protocol (Source Interface)
The `Source` protocol (`src/feather/sources/__init__.py`) defines the interface for all source connectors:
```python
class Source(Protocol):
    def check(self) -> bool: ...
    def discover(self) -> list[StreamSchema]: ...
    def extract(self, table, columns, filter, watermark_column, watermark_value) -> pa.Table: ...
    def detect_changes(self, table, last_state) -> ChangeResult: ...
    def get_schema(self, table) -> list[tuple[str, str]]: ...
```
No ABC inheritance — just structural typing via Protocol.

### Inheritance Hierarchy (Sources)
Two base classes for sources:
- `FileSource` (`src/feather/sources/file_source.py`) — base for DuckDB, CSV, SQLite, Excel, JSON file sources. Provides `detect_changes()` with mtime→MD5 two-tier check.
- `DatabaseSource` (`src/feather/sources/database_source.py`) — base for PostgreSQL, SQL Server. Provides `_build_where_clause()` and `_format_watermark()`.

### Registry Pattern
`src/feather/sources/registry.py` maps type strings to classes:
```python
SOURCE_REGISTRY: dict[str, type[Any]] = {
    "duckdb": DuckDBFileSource,
    "csv": CsvSource,
    ...
}
```
Factory function `create_source(config)` resolves and instantiates.

### Connection Management
DuckDB connections are opened and closed within each method (no connection pooling). Pattern:
```python
con = self._connect()
try:
    # ... operations ...
finally:
    con.close()
```
State DB and destination DB both use this pattern. File permissions set to `0o600` on creation (Unix only).

### Transaction Pattern
Destination loads use explicit transactions:
```python
con.execute("BEGIN TRANSACTION")
try:
    # ... load data ...
    con.execute("COMMIT")
except Exception:
    con.execute("ROLLBACK")
    raise
```

### Logging
- Module-level logger: `logger = logging.getLogger(__name__)` in `pipeline.py`, `transforms.py`, `alerts.py`
- JSONL structured logging to file: `feather_log.jsonl` via `_JsonlFormatter` in `src/feather/pipeline.py`
- Log levels: `logger.info()` for success events, `logger.warning()` for non-fatal errors (DQ, schema drift), `logger.error()` for failures
- Extra fields passed via `extra={"table": ..., "status": ..., "rows_loaded": ...}`

### CLI Output Pattern
Dual output mode — human-readable text (default) or NDJSON (`--json` flag):
```python
if _is_json(ctx):
    emit_line({...}, json_mode=True)
else:
    typer.echo(f"Config valid: {len(cfg.tables)} table(s)")
```
JSON output uses `src/feather/output.py` helpers: `emit()` for lists, `emit_line()` for single dicts.

## Error Handling

### Config Errors
- `ValueError` raised with descriptive messages for all config validation failures in `src/feather/config.py`
- Multiple validation errors collected into a list and joined with `"; "`: `raise ValueError("; ".join(errors))`
- Config parsing uses `from None` to suppress chained exceptions: `raise ValueError(...) from None`

### CLI Errors
- `typer.Exit(code=1)` for user errors (bad config, missing files), `code=2` for connection failures
- Errors printed to stderr via `typer.echo(..., err=True)` before exit
- CLI wraps `load_config()` in `_load_and_validate()` which catches `ValueError` and `FileNotFoundError`

### Pipeline Errors
- Individual table failures caught with broad `except Exception as e:` in `run_table()` — failure is recorded to state DB and doesn't stop other tables
- Error isolation: one table's failure doesn't prevent other tables from running
- Failed runs increment retry counter; successful runs reset it
- `RuntimeError` used for version mismatch in state DB schema

### External Service Errors
- SMTP failures caught and logged, never propagated: `except smtplib.SMTPException:` + `logger.exception()`
- Schema drift detection wrapped in try/except with `logger.warning()` on failure
- DQ check failures logged but don't fail the pipeline run

### Retry + Backoff
- Linear backoff: `retry_count * 15 minutes`, capped at 120 minutes
- Managed by `StateManager.increment_retry()`, `reset_retry()`, `should_skip_retry()`
- Skipped tables during backoff get status `"skipped"` with error message in run history

## Import Organization

### Standard Pattern (observed in all source files)
```python
"""Module docstring."""

from __future__ import annotations

# 1. Standard library
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 2. Third-party
import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import yaml

# 3. Internal (absolute imports from package root)
from feather.config import FeatherConfig, TableConfig
from feather.destinations.duckdb import DuckDBDestination
from feather.sources.registry import create_source
from feather.state import StateManager
```

- **Always absolute imports** — never relative imports like `from .config import ...`
- Grouped: stdlib → third-party → internal (per PEP 8)
- Some lazy imports inside functions to avoid circular dependencies (e.g., `from feather.sources.registry import SOURCE_REGISTRY` inside `_validate()` in config.py)

### Test Imports
Tests use a mix:
- Module-level imports for fixtures and pytest: `import pytest`, `import yaml`, `from tests.conftest import FIXTURES_DIR`
- Function/method-level imports for the code under test (allows isolated testing):
  ```python
  def test_valid_config_parses(self, tmp_path):
      from feather.config import load_config
      # ...
  ```

## Documentation

### Docstrings
- Module-level docstrings on every file: `"""Configuration parsing and validation for feather-flow."""`
- Class docstrings describe purpose and list provided methods — e.g., `FileSource` docstring lists `__init__`, `check()`, `detect_changes()`
- Method docstrings describe behavior and return value — e.g., `"""Derive effective target from mode unless explicitly set."""`
- Triple-double-quote style, imperative mood
- No type info in docstrings (type hints used instead)

### Inline Comments
- Step-by-step comments in algorithms: `# Step 3: First run`, `# Step 4: Mtime unchanged`
- Section comments with `# ---` separators in longer files
- Feature references: `# (FR4.3)`, `# (V10)`, `# (V14)`, `# (V16)`, `# (V17)` — referencing PRD requirement IDs
- Fix references: `# (H-1 fix)`, `# (R-1)` — referencing review findings

### README & Docs
- `README.md` — architecture overview, design principles, supported sources, load strategies, CLI reference
- `docs/prd.md` — product requirements
- `docs/TESTING-PHILOSOPHY.md` — comprehensive testing guidelines
- `docs/CONTRIBUTING.md` — plan/review/fix document chain, test conventions, fixture inventory
- `docs/walkthrough/` — scenario-based walkthroughs with data, CLI commands, code traces

## Configuration Patterns

### YAML Config
Config loaded from `feather.yaml` via `src/feather/config.py`:
- `load_config(config_path, mode_override)` → `FeatherConfig` dataclass
- Environment variable substitution: `${VAR}` resolved via `os.path.expandvars()`
- Path resolution: relative paths resolved against config file directory, not CWD
- Tables can be defined inline in `feather.yaml` or split across `tables/*.yaml` files (merged at load time)

### Mode System
Three modes: `dev`, `prod`, `test` — resolved with precedence: CLI `--mode` > `FEATHER_MODE` env var > YAML `mode:` key > default `"dev"`.
- **dev**: targets `bronze.*`, transforms as views
- **prod**: targets `silver.*`, applies `column_map`, gold tables materialized
- **test**: like dev but with `row_limit` support

### Validation
- `_validate()` returns a list of error strings (never throws)
- Errors joined and raised as `ValueError` from `load_config()`
- Validation JSON written to `feather_validation.json` alongside config (for CI/tooling integration)
- Source-type-aware validation: DuckDB requires `schema.table`, SQLite requires plain name, CSV allows filenames

### Config Propagation
- `FeatherConfig` dataclass passed through pipeline: `run_all(config, config_path)` → `run_table(config, table, working_dir)`
- Working directory derived from `config.config_dir` (parent of `feather.yaml`)
- State DB always at `{working_dir}/feather_state.duckdb`
- Destination DB at `config.destination.path` (user-configurable)
