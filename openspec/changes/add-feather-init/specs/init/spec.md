## ADDED Requirements

### Requirement: 1. Target directory resolution

`feather init` SHALL resolve the target directory from its argument:

- No argument → target is the current working directory.
- One argument `DIR` → target is `./DIR`. If `DIR` does not exist, it is created (including parent directories).

#### Scenario: 1a. Init with no arg stamps files into CWD, if empty
- **WHEN** the operator runs `feather init` inside an empty `/projects/rama_dw`
- **THEN** files are stamped into `/projects/rama_dw`

#### Scenario: 1b. Init with new dir name creates the dir and stamps files inside
- **WHEN** the operator runs `feather init rama_dw` and `./rama_dw/` does not exist
- **THEN** `./rama_dw/` is created and files are stamped inside it

#### Scenario: 1c. Init reuses an existing sub-directory, ignoring sibling files
- **WHEN** the operator runs `feather init rama_dw` and `./rama_dw/` exists, possibly containing unrelated files such as `README.md`, `data/`, or `notes.txt`
- **THEN** missing init files (`feather.yaml`, `pyproject.toml`, `.env`, `.gitignore`) are stamped; existing init files are skipped with the per-file message specified in their own requirement (e.g. the `feather.yaml stamping` requirement for `feather.yaml`); files unrelated to init are neither inspected nor modified

### Requirement: 2. feather.yaml stamping

`feather init` SHALL create a `feather.yaml` in the target directory with the following content:

```yaml
# feather-etl project configuration
# Add sources with: feather source add

# sources:        # populated by `feather source add`

defaults:
  sample_threshold: 100_000   # rows; switch from LIMIT to TABLESAMPLE above this
```

If `feather.yaml` already exists in the target, init SHALL NOT modify it. Init SHALL print this exact message (tests assert it byte-for-byte):

```
feather.yaml: present (delete this file and re-run to reset)
```

#### Scenario: 2a. Stamped feather.yaml content matches template
- **WHEN** init creates `feather.yaml`
- **THEN** the file contains the `defaults.sample_threshold: 100_000` line and the commented `sources:` line

#### Scenario: 2b. feather.yaml present is preserved
- **WHEN** `feather.yaml` already exists in the target
- **THEN** the file is not modified; a message naming the file and instructing `rm` to reset is printed; other files are still processed per their own rules; init exits 0

### Requirement: 3. pyproject.toml stamping

`feather init` SHALL create a `pyproject.toml` in the target directory with a `[project]` block — `name` equal to the target directory's basename, `version = "0.1.0"`, `requires-python = ">=3.10"`, and a `feather-etl` dependency. The dependency form is controlled by the `--dev` flag:

- **Default mode (no `--dev`):**
  - SHALL write `dependencies = ["feather-etl>=0.1.0"]`.
  - SHALL NOT write a `[tool.uv.sources]` block.
  - Result: portable `pyproject.toml` that resolves `feather-etl` from PyPI on any machine.
  - Right mode for beta customers, post-v1 PyPI users, and any project that may be committed to git and used elsewhere.
- **`--dev` mode:**
  - SHALL auto-detect a local feather-etl checkout. (Detection mechanism: design.md Decision 8.)
  - If detected, SHALL write `dependencies = ["feather-etl"]` plus a `[tool.uv.sources]` block with `feather-etl = { path = "<absolute-detected-path>", editable = true }`.
  - If not detected, SHALL exit non-zero with a message explaining that `--dev` requires a local feather-etl checkout.
  - Right mode for the feather-etl core development team working against bleeding-edge source.

If `pyproject.toml` already exists in the target, init SHALL NOT modify it (same reset-by-delete rule as `feather.yaml`). This applies in both modes. Init SHALL print this exact message (tests assert it byte-for-byte):

```
pyproject.toml: present (delete this file and re-run to reset)
```

If `--dev` was passed but auto-detection cannot find a local feather-etl checkout, init SHALL exit non-zero with this exact message (tests assert it byte-for-byte; `<path>` is the directory the detection checked):

```
--dev requires a local feather-etl checkout. Checked: <path>. No pyproject.toml found there. Either install feather-etl from a local source, or omit --dev to use PyPI.
```

#### Scenario: 3a. Default mode writes pyproject.toml with PyPI version pin
- **WHEN** init runs without `--dev` (e.g., `feather init rama_dw`)
- **THEN** the stamped `pyproject.toml` contains `dependencies = ["feather-etl>=0.1.0"]` and no `[tool.uv.sources]` block, and the project's `name` matches the target directory's basename

#### Scenario: 3b. --dev with local checkout writes pyproject.toml with editable source block
- **WHEN** init runs with `--dev` and auto-detection finds a local feather-etl checkout
- **THEN** the stamped `pyproject.toml` contains `dependencies = ["feather-etl"]` and a `[tool.uv.sources]` block with `feather-etl = { path = "<detected-path>", editable = true }`

#### Scenario: 3c. --dev without local checkout exits non-zero
- **WHEN** init runs with `--dev` and auto-detection cannot find a local feather-etl checkout (e.g., feather-etl was installed from PyPI into site-packages)
- **THEN** init exits non-zero with a message naming the directory it checked and explaining that `--dev` requires a local feather-etl checkout — the operator either drops `--dev` (to use PyPI) or installs feather-etl from a local source

#### Scenario: 3d. pyproject.toml present is preserved
- **WHEN** `pyproject.toml` already exists in the target (in either mode)
- **THEN** the file is not modified; a message naming the file and instructing `rm` to reset is printed; other files are still processed per their own rules; init exits 0

### Requirement: 4. .env stamping

`feather init` SHALL ensure a `.env` file exists in the target directory. Existing `.env` content is never overwritten.

#### Scenario: 4a. Create empty .env when absent
- **WHEN** `.env` does not exist in the target
- **THEN** an empty `.env` is created (zero bytes)

#### Scenario: 4b. Preserve existing .env silently
- **WHEN** `.env` exists in the target with any content
- **THEN** the file is left byte-identical AND no per-file output line is printed for `.env` (the silent-skip distinguishes it from the marker files, which print a reset hint)

### Requirement: 5. .gitignore stamping

`feather init` SHALL create a `.gitignore` in the target directory containing exactly one line — `.env` — when no `.gitignore` is present. Other entries (`explore/`, `*.duckdb`, `.feather/`) are added by the verbs that create those files; each verb owns its own gitignore entries.

If a `.gitignore` already exists, init SHALL NOT modify it. Init SHALL print this exact message (tests assert it byte-for-byte):

```
.gitignore: present (ensure .env is listed)
```

The instruction differs from `feather.yaml` / `pyproject.toml` because re-running cannot fix a `.gitignore` content gap; the operator must edit it.

#### Scenario: 5a. Create .gitignore when absent
- **WHEN** `.gitignore` does not exist in the target
- **THEN** a `.gitignore` is created containing exactly the line `.env` (one line, newline-terminated)

#### Scenario: 5b. Existing .gitignore is preserved
- **WHEN** `.gitignore` already exists in the target with any content
- **THEN** the file is not modified; a message stating that `.env` should be listed is printed; init exits 0

### Requirement: 6. Source-wiring confirmation

After successful stamping, `feather init` SHALL print a single confirmation line naming how `feather-etl` was wired into the project's dependencies. The line surfaces mode + source so the operator can sanity-check the result at a glance. The confirmation is printed after the per-file outcome lines, separated by a blank line.

The exact wording (tests assert byte-for-byte; `{version}` is the runtime value of `feather_etl.__version__`; `{path}` is the absolute auto-detected `feather_etl_source_path`):

- Default mode: `feather-etl: pinned to PyPI (>={version})`
- `--dev` mode: `feather-etl: editable from {path}`

#### Scenario: 6a. Default mode confirms PyPI as the source
- **WHEN** init completes successfully without `--dev`
- **THEN** the output contains a line naming PyPI as the source and the version pin (e.g., `feather-etl: pinned to PyPI (>=0.1.0)`)

#### Scenario: 6b. --dev mode confirms the editable path
- **WHEN** init completes successfully with `--dev` (and detection succeeded)
- **THEN** the output contains a line naming the auto-detected local-checkout path (e.g., `feather-etl: editable from /Users/x/Projects/feather-etl`)
