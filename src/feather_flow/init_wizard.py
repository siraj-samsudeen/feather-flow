"""feather init — project scaffolding."""

from __future__ import annotations

from pathlib import Path

FEATHER_YAML_TEMPLATE = """\
# feather-flow configuration
# Docs: https://github.com/your-org/feather-flow
#
# Source types — uncomment and fill in one:
#
# --- File-based sources ---
#   type: duckdb
#   path: ./source.duckdb
#
#   type: csv
#   path: ./data/csv/                   # directory containing .csv files
#
#   type: sqlite
#   path: ./source.sqlite
#
#   type: excel
#   path: ./data/excel/                 # directory containing .xlsx files
#
#   type: json
#   path: ./data/json/                  # directory containing .json/.jsonl files
#
# --- Database sources (credentials go in .env — see .env.example) ---
#   type: sqlserver
#   host: ${SQLSERVER_HOST}
#   port: ${SQLSERVER_PORT}
#   database: ${SQLSERVER_DATABASE}
#   user: ${SQLSERVER_USER}
#   password: ${SQLSERVER_PASSWORD}
#
#   type: postgres
#   host: ${POSTGRES_HOST}
#   port: ${POSTGRES_PORT}
#   database: ${POSTGRES_DATABASE}
#   user: ${POSTGRES_USER}
#   password: ${POSTGRES_PASSWORD}

sources:
  - type: duckdb
    path: ./source.duckdb

destination:
  path: ./feather_data.duckdb

# defaults:
#   overlap_window_minutes: 2
#   batch_size: 120000

tables:
  - name: example_table
    source_table: schema.table_name     # e.g., icube.SALESINVOICE
    target_table: bronze.example_table
    strategy: full                      # full, incremental, append
    # primary_key: [id]
    # timestamp_column: modified_date   # required for incremental/append
"""

PYPROJECT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
description = "feather-flow client project"
requires-python = ">=3.10"
dependencies = ["feather-flow>=0.1.0"]
"""

GITIGNORE_TEMPLATE = """\
*.duckdb
*.duckdb.wal
feather_validation.json
.env
__pycache__/
.venv/
"""

ENV_EXAMPLE_TEMPLATE = """\
# Environment variables for feather-flow
# Copy to .env and fill in values

# --- SQL Server ---
# SQLSERVER_HOST=
# SQLSERVER_PORT=1433
# SQLSERVER_DATABASE=
# SQLSERVER_USER=
# SQLSERVER_PASSWORD=

# --- PostgreSQL ---
# POSTGRES_HOST=
# POSTGRES_PORT=5432
# POSTGRES_DATABASE=
# POSTGRES_USER=
# POSTGRES_PASSWORD=

# --- Other ---
# MOTHERDUCK_TOKEN=
# ALERT_EMAIL_USER=
# ALERT_EMAIL_PASSWORD=
"""


def scaffold_project(project_path: Path) -> list[str]:
    """Create a new client project directory with template files."""
    project_path.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    name = project_path.name

    files: dict[str, str] = {
        "feather.yaml": FEATHER_YAML_TEMPLATE,
        "pyproject.toml": PYPROJECT_TEMPLATE.format(name=name),
        ".gitignore": GITIGNORE_TEMPLATE,
        ".env.example": ENV_EXAMPLE_TEMPLATE,
    }
    for rel_path, content in files.items():
        fp = project_path / rel_path
        fp.write_text(content)
        created.append(rel_path)

    return created
