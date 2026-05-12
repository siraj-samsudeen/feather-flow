## ADDED Requirements

### Requirement: Transform command executes silver and gold without re-extracting bronze

The system SHALL provide a `feather transform` CLI command that re-executes all discovered silver and gold SQL transforms against the existing destination DuckDB. The command MUST NOT open any source-system connection. It SHALL run transforms in topological order using the same execution path as `feather run`'s post-extract transform block.

#### Scenario: Transforms run against existing destination without touching sources

- **WHEN** an operator runs `feather transform` with a config whose sources have failing `_connect()` implementations
- **THEN** the command completes successfully, executes every discovered transform, and never invokes any source's connection method

#### Scenario: No transforms found is not an error

- **WHEN** an operator runs `feather transform` in a project whose `transforms/` directory is empty
- **THEN** the command exits with code 0 and prints a "0 transforms" summary line

### Requirement: Mode resolution chain matches `feather run`

The command SHALL resolve the active mode using the chain: CLI `--mode` flag, then `FEATHER_MODE` environment variable, then `mode:` field in `feather.yaml`, then default `"dev"`. The resolved mode SHALL determine materialization per the existing two-axis rule used by `feather run`:

- Silver is always materialized as VIEWs in every mode.
- Gold transforms WITHOUT a `-- materialized: true` header comment are always materialized as VIEWs in every mode.
- Gold transforms WITH a `-- materialized: true` header comment are materialized as TABLEs in `prod` mode only; they are downgraded to VIEWs in `dev` and `test`.

#### Scenario: CLI flag overrides environment and YAML

- **WHEN** `feather.yaml` declares `mode: prod`, `FEATHER_MODE=test` is set, and the operator runs `feather transform --mode dev`
- **THEN** the command resolves mode to `dev` and every transform — including any gold transform marked `-- materialized: true` — is created as a VIEW

#### Scenario: Default mode is dev when none configured

- **WHEN** the operator runs `feather transform` with no flag, no env var, and no `mode:` in `feather.yaml`
- **THEN** the command resolves mode to `dev` and every transform is created as a VIEW

#### Scenario: Prod mode materializes marked gold transforms as tables

- **WHEN** the operator runs `feather transform --mode prod` against a config with one gold transform marked `-- materialized: true` and one gold transform without the marker
- **THEN** the marked gold transform is created as a TABLE, the unmarked gold transform is created as a VIEW, and silver transforms remain as VIEWs

#### Scenario: Unmarked gold stays a view in prod

- **WHEN** the operator runs `feather transform --mode prod` against a config whose gold transforms have no `-- materialized: true` header
- **THEN** every gold transform is created as a VIEW

### Requirement: Exit-code contract

The command SHALL exit with code 0 on success (including the "no transforms" case), code 1 when at least one transform raised a SQL error, and code 2 when the configuration is invalid, the destination DuckDB is unreachable, or the `transforms/` directory is malformed.

#### Scenario: Transform SQL error returns exit 1

- **WHEN** the operator runs `feather transform` and one of the transforms references a non-existent column
- **THEN** the command prints a per-transform breakdown showing the failure and exits with code 1

#### Scenario: Missing config returns exit 2

- **WHEN** the operator runs `feather transform --config nonexistent.yaml`
- **THEN** the command exits with code 2 without executing any transform

### Requirement: Advisory bronze-dependency check

THE SYSTEM SHALL emit an advisory warning to stderr for each bronze table that is referenced (via FROM or JOIN) by any silver transform but does not exist in the destination's bronze schema. The bronze references SHALL be derived from each silver transform's SQL body via sqlglot AST walk; the `-- depends_on:` header convention is not honoured (see issue #54). For every unmet dependency, the command SHALL emit a `WARNING:` line to stderr but SHALL NOT abort execution. When the number of unmet dependencies exceeds five, the command SHALL collapse the warnings into a single summary line.

#### Scenario: Missing bronze dependency emits warning but continues

- **WHEN** a silver transform's SQL body contains `FROM bronze.orders` and `bronze.orders` does not exist in the destination DuckDB
- **THEN** the command writes a `WARNING:` to stderr naming the missing dependency and still attempts to execute every transform

#### Scenario: All bronze dependencies present produces no warnings

- **WHEN** every `bronze.*` table referenced (via FROM/JOIN) by any silver transform's SQL body exists in the destination
- **THEN** the command emits zero `WARNING:` lines for missing bronze dependencies

#### Scenario: Many missing dependencies collapse to summary

- **WHEN** more than five distinct unmet bronze dependencies are detected
- **THEN** the command emits a single summary `WARNING:` line stating the count of missing dependencies instead of one line per dependency

### Requirement: `_runs.trigger` column added by this change

The system SHALL add a new `trigger VARCHAR` column to the `_runs` table. Fresh destinations SHALL include the column in their initial `CREATE TABLE`. Existing destinations SHALL receive the column via idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS` on first connect after upgrade. Rows written prior to this change MAY have `trigger=NULL`; the system SHALL NOT backfill them.

Every code path that inserts into `_runs` SHALL populate `trigger` with one of the values `{'run', 'extract', 'transform', 'schedule'}`. The application-side validator SHALL accept those four values for new writes and SHALL reject other strings.

#### Scenario: Fresh destination has the trigger column

- **WHEN** a feather user runs any verb against a brand-new destination DuckDB
- **THEN** the `_runs` table is created with a `trigger` column and the inserted row's `trigger` value is one of the four accepted values

#### Scenario: Existing destination gets the column added on first connect

- **WHEN** the upgraded code opens a destination DuckDB whose `_runs` table was created before this change
- **THEN** the schema is altered to include a `trigger` column without erroring on the second open, and any rows written before the upgrade have `trigger=NULL`

#### Scenario: Invalid trigger value rejected

- **WHEN** code attempts to insert a `_runs` row with `trigger='something-else'`
- **THEN** the validator rejects the write before the row reaches DuckDB

### Requirement: Transform invocations write `trigger='transform'`

Each `feather transform` invocation SHALL write one row per executed transform to `_runs`, with `trigger='transform'`, `table_name=f"{schema}.{name}"` of the transform, and `status` set to `success` or `failure`.

#### Scenario: Successful run writes success rows

- **WHEN** the operator runs `feather transform` and three transforms execute successfully
- **THEN** the `_runs` table contains three new rows where `trigger='transform'`, each `table_name` matches one of the executed transforms, and each `status='success'`

### Requirement: `feather history --trigger` filters by verb

The `feather history` command SHALL accept a `--trigger <value>` option. When set, history output SHALL include only rows whose `trigger` column equals the given value. When the option is omitted, all rows are returned (including legacy rows with `trigger=NULL`).

#### Scenario: Filter returns only transform rows

- **WHEN** the operator runs `feather history --trigger transform` after a mix of `feather run` and `feather transform` invocations
- **THEN** only the rows written by the `feather transform` invocations are returned

#### Scenario: Unfiltered output includes legacy NULL-trigger rows

- **WHEN** a destination has rows from before this change (`trigger=NULL`) and the operator runs `feather history` with no `--trigger` flag
- **THEN** legacy rows are included in the output

### Requirement: Idempotent execution

The command SHALL be safe to re-run without manual cleanup. Repeated executions against unchanged transform SQL and unchanged bronze data SHALL produce the same destination state as a single execution.

#### Scenario: Two consecutive runs produce identical destination state

- **WHEN** the operator runs `feather transform` twice in succession with no source or SQL changes between runs
- **THEN** both runs exit 0, the destination schema and row counts are identical after each run, and no transform raises a "table already exists" error

### Requirement: Summary output mirrors `cache` command style

The command SHALL print to stdout a header line stating the resolved mode, followed by one line per transform showing `<schema>.<name>`, materialization kind (`view` or `table`), and status (`success` or `failure`). A trailing summary line SHALL state the total count and the number that succeeded.

#### Scenario: Output groups by transform and ends with summary

- **WHEN** the operator runs `feather transform --mode dev` with one silver and one gold transform both succeeding
- **THEN** stdout contains a `Mode: dev` line, one line per transform marked `view  success`, and a trailing `2 transforms: 2 succeeded.` line
