# feather-etl test suite

This directory is organised by **what a test exercises**, not by what source
file it happens to cover. The three-way layout mirrors the classic test
pyramid:

- `tests/e2e/` — CLI journeys; reading top-to-bottom = user story.
- `tests/integration/` — multi-module slices invoked via the Python API.
- `tests/unit/` — single-module tests, mirroring `src/feather_etl/`.

> **Migration status (2026-04-19):** Wave A of issue #40 is complete. The
> `e2e/`, `integration/`, and `unit/` trees exist but are mostly empty;
> most tests still live at `tests/test_*.py` and `tests/commands/test_*.py`
> and will be migrated in Waves B/C/D.

## The three-way decision rule

For any new or existing test, ask in order:

1. **Does it invoke the CLI?** (either `CliRunner.invoke(app, ...)` or
   spawns the `feather` binary via subprocess) → `tests/e2e/`. File chosen
   by workflow stage (`test_02_validate.py`, `test_04_extract_full.py`,
   etc. — see the list below).
2. **Does it exercise 2+ modules from `src/feather_etl/` through a
   pipeline-level API** (e.g., `pipeline.run_pipeline()`,
   `extract.run_extract()`)? → `tests/integration/`. File chosen by
   feature/capability (`test_incremental.py`, `test_schema_drift.py`,
   etc.).
3. **Otherwise — exercises a single module's functions/classes** →
   `tests/unit/`. File mirrors the source path:
   `src/feather_etl/sources/csv.py` → `tests/unit/sources/test_csv.py`.

`CliRunner` alone makes a test e2e even when it only tests one command;
that is intentional.

## Workflow-stage file layout (`tests/e2e/`)

Files are numbered so that reading them in order mirrors the user's
journey:

| File | Covers |
|---|---|
| `test_00_cli_structure.py` | CLI surface — commands registered, `--help` renders |
| `test_01_scaffold.py` | `feather init` |
| `test_02_validate.py` | `feather validate` |
| `test_03_discover.py` | `feather discover` (including multi-source and --explicit-name) |
| `test_04_extract_full.py` | `feather setup` + `feather run` happy path |
| `test_05_change_detection.py` | re-run skip, modify-and-re-extract |
| `test_06_incremental.py` | watermark advance, overlap, filter |
| `test_07_transforms.py` | silver views, gold materialization via CLI |
| `test_08_dq.py` | `not_null` / `unique` / `row_count` via CLI |
| `test_09_schema_drift.py` | added/removed/type-changed columns |
| `test_10_error_handling.py` | partial failure, exit codes, stream routing |
| `test_11_path_resolution.py` | CWD independence, absolute `--config` |
| `test_12_extract.py` | `feather extract` |
| `test_13_multi_source.py` | multiple `sources:` entries |
| `test_14_status.py` | `feather status` |
| `test_15_history.py` | `feather history` |
| `test_16_view.py` | `feather view` |
| `test_17_json_output.py` | `--json` flag across commands |
| `test_18_sources_e2e.py` | non-default source types (SQLite, Postgres, Excel, JSON) end-to-end |

Add new numbered files only when a wholly new workflow stage appears. If a
scenario fits an existing stage, add a test function to that file.

## `ProjectFixture` and `cli` — the e2e harness

Every test in `tests/e2e/` gets a `project` and a `cli` fixture from
`tests/e2e/conftest.py`.

### `project`: a `ProjectFixture`

A `ProjectFixture` represents "a feather project on disk, ready to use."
Its public API is small and closed:

| Attribute / method | Purpose |
|---|---|
| `project.root` | `Path` to the project directory (`tmp_path`). |
| `project.config_path` | `Path` to `feather.yaml`. |
| `project.data_db_path` | `Path` to the destination DuckDB. |
| `project.state_db_path` | `Path` to the state DuckDB. |
| `project.write_config(**fields)` | Write `fields` as YAML to `feather.yaml`. |
| `project.write_curation([(src, table, alias), ...])` | Write `discovery/curation.json` with simple entries. For richer entries call `tests.helpers.make_curation_entry` + `write_curation` directly. |
| `project.copy_fixture(name)` | Copy a file or directory from `tests/fixtures/` into the project root. Returns the destination path. |
| `project.query(sql)` | Execute `sql` against the data DB (read-only), return all rows as a list of tuples. |

### `cli`: a callable

`cli(*args)` invokes the feather Typer app via `CliRunner` and forwards
`--config` pointing at `project.config_path`. Returns the `CliRunner`
result:

```python
def test_validate_happy_path(project, cli):
    project.copy_fixture("sample_erp.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "name": "erp", "path": "./sample_erp.sqlite"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("erp", "orders", "orders")])
    result = cli("validate")
    assert result.exit_code == 0
```

For scenarios needing real OS-level stdout/stderr separation (CliRunner
merges streams), fall back to `subprocess.run` with the `feather` binary
— see `test_10_error_handling.py` for the pattern.

## Test style

- **Flat functions are the default.** `def test_foo(project, cli): ...` at
  module level.
- **Classes only when they add grouping value** — e.g., multiple tests
  share a `@pytest.fixture` defined inside the class, or a clear
  sub-concept exists (`class TestCsvGlobChangeDetection:`). When in doubt,
  prefer flat.
- **Never `unittest.TestCase`.** Plain `assert`, `pytest.raises`,
  `pytest.fixture`. `unittest.mock` is fine — that is the standard mock
  library.

## Regression tests and `BUG-N` labels

When a bug is discovered:

1. Write a test in the appropriate layer (`e2e/`, `integration/`, or
   `unit/`) that asserts the current wrong behaviour. Name it
   `test_BUG_<N>_<short_description>` and include a docstring:
   ```
   BUG-N: <one sentence on current wrong behaviour>.
   After fix: <one sentence on correct behaviour>.
   ```
   The test must PASS while the bug is open — i.e., it asserts the wrong
   behavior — acting as a regression guard.
2. When the bug is fixed: invert the assertion, rename the test (drop the
   `BUG-N` prefix), and — if it was in a "known bugs" group — move it into
   a positive-behavior file.

## Running

```bash
uv run pytest -q                # full suite
uv run pytest tests/e2e/ -q     # e2e layer only
uv run pytest tests/unit/ -q    # unit layer only
```
