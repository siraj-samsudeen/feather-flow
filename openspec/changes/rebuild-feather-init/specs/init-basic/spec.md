## ADDED Requirements

### Requirement: Stamp project files
`feather init` SHALL create the following files in the target directory:
- `feather.yaml`
- `feather.example.yaml`
- `pyproject.toml`
- `.env.example`
- `.gitignore`

#### Scenario: Init in new directory
- **WHEN** user runs `feather init my-proj` and `my-proj` does not exist
- **THEN** directory `my-proj` is created with all five files

#### Scenario: Init in current directory
- **WHEN** user runs `feather init .` inside `/projects/rama_dw`
- **THEN** all five files are created in `/projects/rama_dw` and `pyproject.toml` contains `name = "rama_dw"`

#### Scenario: Init creates directory if it doesn't exist
- **WHEN** user runs `feather init new-client` and `new-client` doesn't exist
- **THEN** directory `new-client` is created before files are stamped

### Requirement: feather.yaml content
The stamped `feather.yaml` SHALL contain only `sources: []`. No destination, no tables, no commented-out configurations.

#### Scenario: Stamped feather.yaml is minimal
- **WHEN** init creates `feather.yaml`
- **THEN** the file content is `sources: []` (valid YAML, empty list)

### Requirement: feather.example.yaml content
The stamped `feather.example.yaml` SHALL contain a commented-out example entry for every source type supported by feather-etl. Each entry SHALL show the required fields with placeholder values and inline comments explaining each field.

#### Scenario: Contains all source types
- **WHEN** init creates `feather.example.yaml`
- **THEN** the file contains example blocks for: duckdb, csv, sqlite, excel, json, sqlserver, postgres, mysql

#### Scenario: SQL Server example shows required fields
- **WHEN** an agent or human reads the sqlserver block
- **THEN** it shows `type`, `name`, `host`, `port`, `user`, `password`, `databases` fields with placeholder values

### Requirement: pyproject.toml content
The stamped `pyproject.toml` SHALL use the directory basename as the project name. It SHALL declare `feather-etl` as a published dependency with a commented-out local dev pointer.

#### Scenario: Name from directory
- **WHEN** init runs in directory `rama_dw`
- **THEN** `pyproject.toml` contains `name = "rama_dw"`

#### Scenario: Published dependency with local pointer
- **WHEN** init creates `pyproject.toml`
- **THEN** it contains `dependencies = ["feather-etl>=0.1.0"]` and a commented `[tool.uv.sources]` block with an editable local path

### Requirement: .env.example content
The stamped `.env.example` SHALL contain environment variable templates for sqlserver and postgres, all commented out. Two DB types demonstrate that prefixes change per source.

#### Scenario: Shows sqlserver and postgres vars
- **WHEN** init creates `.env.example`
- **THEN** it contains `SQLSERVER_HOST`, `SQLSERVER_PORT`, `SQLSERVER_USER`, `SQLSERVER_PASSWORD` and `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, all commented out

### Requirement: .gitignore content
The stamped `.gitignore` SHALL contain feather-specific entries: `*.duckdb`, `*.duckdb.wal`, `feather_validation.json`, `.env`, `__pycache__/`, `.venv/`.

#### Scenario: Contains feather entries
- **WHEN** init creates `.gitignore`
- **THEN** all six feather entries are present

### Requirement: Project name from directory basename
`feather init` SHALL derive the project name from the target directory's basename. This name is used in `pyproject.toml`.

#### Scenario: Named directory
- **WHEN** user runs `feather init my-proj`
- **THEN** project name is `my-proj`

#### Scenario: Dot resolves to CWD basename
- **WHEN** user runs `feather init .` inside `/projects/rama_dw`
- **THEN** project name is `rama_dw`
