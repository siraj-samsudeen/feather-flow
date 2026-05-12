## ADDED Requirements

### Requirement: `feather extract` is the verb name; `feather cache` no longer exists

The CLI SHALL register the verb under the name `extract` and SHALL NOT register it under the name `cache`. There SHALL be no deprecation alias. Invoking `feather cache` after this change MUST produce a "no such command" error from Typer, exactly as for any unknown command.

The behaviour, flags, and exit codes of the renamed verb SHALL be identical to the pre-change `feather cache`; this requirement covers only the name and its registration.

#### Scenario: `feather extract` runs the verb

- **WHEN** an operator invokes `feather extract` with arguments that would have been valid for `feather cache`
- **THEN** the verb runs and produces the same output (modulo the trigger value written to `_runs`, see below)

#### Scenario: `feather cache` returns "no such command"

- **WHEN** an operator invokes `feather cache`
- **THEN** Typer prints a "no such command" error and exits non-zero. There SHALL NOT be a deprecation warning that proceeds to run the verb anyway

#### Scenario: `feather --help` lists extract, not cache

- **WHEN** an operator runs `feather --help`
- **THEN** the command list contains `extract` and does not contain `cache`

### Requirement: Source files renamed to match the verb

The module files SHALL be renamed alongside the CLI verb:

- `src/feather_etl/commands/cache.py` → `src/feather_etl/commands/extract.py`
- `src/feather_etl/cache.py` → `src/feather_etl/extract.py` (if such a module exists at change time)
- Public functions previously named `cache(...)` or `run_cache(...)` SHALL be renamed to `extract(...)` / `run_extract(...)` correspondingly.

No legacy file SHALL remain on disk after the rename. Internal imports SHALL use the new module path.

#### Scenario: No legacy cache module present

- **WHEN** the working tree is inspected after the rename lands
- **THEN** there is no `src/feather_etl/commands/cache.py` and no `src/feather_etl/cache.py`. Imports referencing those paths fail at import time

### Requirement: Extract invocations write `trigger='extract'`

Each `feather extract` invocation SHALL write rows to `_runs` with `trigger='extract'`. The column is defined and constrained by the `transform-command` spec; this requirement covers only the value written by the renamed verb.

#### Scenario: Extract writes the new trigger value

- **WHEN** the operator runs `feather extract` against a destination
- **THEN** every row written to `_runs` during that invocation has `trigger='extract'` and no row has `trigger='cache'`
