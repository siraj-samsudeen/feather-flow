# Package Layout Convention

This document defines **where files live** in the feather-etl repo —
what package owns what, how tests colocate with code, and what lands
in `core/` versus a feature package.

**Scope:** repo-level package layout only. For in-file organization, see
the three rules in `docs/ORIENTATION.md` (docstring + public-first
stepdown + <150 lines); rules 1 and 3 are enforced by
`tests/invariants/test_file_style.py`.

**Audience:** team members and AI agents making structural changes
to the repo. Read this before adding a new module, moving a file, or
creating a new command or source type.

---

## The three axes

feather-etl organizes around three vertical axes plus a small
horizontal `core`:

| Axis | Examples | Extends by |
|---|---|---|
| **Sources** | `csv`, `sqlserver`, `postgres`, `excel`, `json`, `sqlite`, `duckdb`, `mysql` | Adding a new source type |
| **Destinations** | `duckdb` today; future: `motherduck`, `s3` | Adding a new write target |
| **Commands** | `init`, `validate`, `discover`, `run`, `setup`, `cache`, `status`, `history`, `view` | Adding a new CLI subcommand |

**`core/`** holds horizontal primitives (config, state, transforms,
output, exceptions, alerts, curation parser) used across multiple
axes. Be strict about what lands here — see Rule 6.

**`tests/invariants/`** holds repo-wide invariant tests that don't
belong to any single feature.

**`src/feather_etl/testing/`** holds importable test utilities (helpers,
fixture harnesses, DB bootstrap).

---

## Target tree

```
src/feather_etl/
  cli.py                         # Typer entrypoint only
  core/
    config.py                    # YAML config loader + validation
    state.py                     # StateManager (runs, watermarks, retries)
    transforms.py                # Silver/gold SQL transform execution
    output.py                    # JSON/text formatters (all commands)
    exceptions.py                # Exception hierarchy
    alerts.py                    # SMTP/notification infrastructure
    curation.py                  # Curation manifest parser
    <module>_test.py             # sibling unit tests
  sources/
    protocol.py                  # Source protocol
    file_base.py                 # FileSource base class
    db_base.py                   # DatabaseSource base class
    registry.py                  # type-string → class dispatch
    expand.py                    # expand_db_sources() multi-DB fan-out
    csv/
      source.py
      source_test.py
      <csv-specific helpers>.py + _test.py siblings
    sqlserver/
      source.py
      source_test.py
    postgres/                    # (same shape for each type)
    excel/
    json/
    sqlite/
    duckdb/
    mysql/
  destinations/
    base.py                      # Destination protocol (when formalized)
    duckdb/
      destination.py
      destination_test.py
  commands/
    _common.py                   # Typer/CLI wiring helpers
    <command>/
      cli.py                     # Typer wrapper (thin)
      cli_test.py
      core.py                    # domain logic (Typer-free)
      core_test.py
      <subdomain>.py             # e.g., discover/state.py, discover/naming.py
      <subdomain>_test.py
      SPEC.md                    # feature spec (owned by the feature)
      PLAN.md                    # active plan (archived plans go to docs/archive/)
      scenarios/                 # cross-module tests within this feature
        conftest.py
        test_<capability>.py
  testing/                       # importable test utilities
    helpers.py
    db_bootstrap.py
    project_fixture.py           # ProjectFixture class
  resources/                     # static packaged assets
    schema_viewer.html

tests/
  conftest.py                    # repo-wide fixtures (pytest wrappers
                                 # re-exporting from feather_etl.testing)
  fixtures/                      # sample DBs, CSVs — data, unchanged
  invariants/                    # repo-wide invariant tests
    test_cli_surface.py
    test_architecture_purity.py
    test_readonly_commands.py
    test_json_output_contract.py
    test_path_resolution.py
    test_fixture_smoke.py
    test_error_handling_contract.py
    test_sources_registry.py     # every source works end-to-end

scripts/                         # .py and .sh ONLY
docs/
```

---

## Placement rules

Apply in order — earlier rules win.

### 1. Entry-point rule
The Typer app entrypoint stays at `src/feather_etl/cli.py`.

### 2. Sources axis
A file specific to one source type → `sources/<type>/`.
Tests are `_test.py` siblings.

### 3. Destinations axis
A file specific to one destination → `destinations/<dest>/`.
Tests are `_test.py` siblings.

### 4. Commands axis
A file for a command's wrapper / orchestration / subdomain →
`commands/<command>/`. The thin Typer wrapper is `cli.py`; domain
logic is `core.py` + additional submodules as needed. Tests are
`_test.py` siblings. Cross-module scenarios go in `scenarios/`.

### 5. Axis-owner priority
Sources, destinations, and commands are **axis owners**. A utility
that operates on sources (e.g., `expand_db_sources`) belongs in
`sources/` even if multiple commands call it. Rule 6 applies only
when no axis owns the file.

### 6. Core rule (strict)
A file belongs in `core/` **only** when:

- no single axis owns it (not owned by a specific source, destination,
  or command), **and**
- it is either used across multiple axes today, or it is a platform
  primitive with no command-specific knowledge (config, state,
  output formatters, exceptions, alerts, curation parser).

"Used by one command" is **not** core — it stays in that command.

### 7. Promotion rule
A module in `commands/<cmd>/` gets promoted to `core/` when **either**:

- a second caller from a different axis appears, **or**
- the module has no command-specific knowledge (it would work
  identically for any future command).

Do **not** pre-emptively promote based on "it feels reusable."

### 8. Invariants tier
Tests that assert **repo-wide invariants** — not tied to any single
feature — live in `tests/invariants/`. A test belongs here if it:

- iterates the full command, source, or destination registry,
- enforces import boundaries or package layout,
- verifies cross-command consistency (`--json` contract, read-only
  command set, CWD independence, exit codes, stream routing),
- smoke-tests shared fixtures or test infrastructure.

If a test targets a single feature, it goes in that feature's
`scenarios/` or as a `_test.py` sibling — **not** here.

### 9. Importable test utilities
Shared test helpers, fixture classes, and bootstrap utilities live in
`src/feather_etl/testing/` (an importable sub-package). They are
**not** packaged for release (see pyproject excludes) but are
importable in-repo as `from feather_etl.testing.helpers import ...`.

### 10. Static assets
Non-Python packaged assets (HTML, templates, schemas) live in
`src/feather_etl/resources/`. The `scripts/` directory is `.py` and
`.sh` only.

### 11. Misfit fallback
If a file cannot be clearly placed under any of the above, document
it as a misfit in the PR description and propose a convention
addition — do **not** invent an extension silently.

### 12. Behavioral test placement
A test goes where the **behavior** under test is owned, not where the
tested module happens to live.

Example: `StateManager.increment_retry` lives on `StateManager` (in
`core/state.py`), but the retry policy exists **because of** run-time
concerns. Its test belongs in `commands/run/retry_test.py`, not
`core/state_test.py`. Tests should read as documentation of
*behavior*; behavior belongs to a feature.

### 13. Cross-command imports
`commands/<a>/core.py` **MAY** import from `commands/<b>/core.py`
when one command's domain logic legitimately composes another's
(e.g., cache calls pipeline internals).

`commands/<a>/cli.py` **MAY NOT** import from `commands/<b>/cli.py` —
Typer wrappers stay isolated. Cross-wrapper plumbing goes through
`commands/_common.py` or gets promoted to `core/`.

Rationale: core-to-core coupling is a design choice; cli-to-cli
coupling is an accident that makes Typer wiring dependencies
non-obvious.

### 14. Interactive command cores
`core.py` **MAY** contain TTY prompts and interactive loops when the
command is inherently interactive (e.g., `init`'s scaffolding wizard).
Typer-specific imports remain confined to `cli.py`; the `core.py`
file uses plain `input()` or a non-Typer prompt library.

Rationale: pretending an interactive wizard is a "pure orchestrator"
forces awkward plumbing. What `core.py` excludes is **Typer**, not
**all user interaction**.

### 15. ProjectFixture / test-fixture split
- **Fixture implementations** (classes, factory functions) live in
  `src/feather_etl/testing/` as importable modules. Example:
  `feather_etl/testing/project_fixture.py` exports
  `class ProjectFixture`.
- **pytest `@pytest.fixture` wrappers** live in `tests/conftest.py`
  (for repo-wide) or feature `scenarios/conftest.py` (for
  feature-scoped) and re-export from `feather_etl.testing`.

Rationale: scenarios tests inside `src/feather_etl/commands/<cmd>/scenarios/`
need to access fixtures, but cannot reach into `tests/conftest.py`
cleanly. Splitting implementation (importable) from pytest glue
(discoverable) makes fixtures work from both sides.

### 16. Test file naming
A test file's name must match its content. Example of the bad case:
`tests/unit/test_discover_io.py` whose body actually tests
`config._sanitize` and `config.resolved_source_name`. Such files
should be **renamed during placement** (e.g., to
`commands/discover/naming_test.py` when the helpers are extracted to
`naming.py`, or `core/config_test.py` additions if they stay).

**Drop redundant suffixes on move.** `sources/duckdb_file.py` →
`sources/duckdb/source.py` (drop `_file`; the folder disambiguates).
`sources/json_source.py` → `sources/json/source.py` (drop `_source`;
the folder disambiguates from stdlib `json`).

---

## Test placement sub-rules

Applies within the placement rules above.

- Tests that assert on ONE module's contract → `<module>_test.py`
  sibling.
- Tests that span multiple modules within the same feature →
  `scenarios/test_<capability>.py` inside the feature package.
- Tests that span a command AND a source (e.g., "discover over SQL
  Server") → scenarios/ inside the **command's** package. Commands
  integrate sources, not vice versa.
- Tests that assert repo-wide invariants — properties that must always
  hold across the whole system (e.g., every command is registered,
  import boundaries are respected, read-only commands never mutate,
  `--json` behaves uniformly) → `tests/invariants/`.
- Tests of `src/feather_etl/testing/` (the test-utility sub-package) →
  `tests/invariants/` (they validate shared test infrastructure).

---

## pyproject.toml requirements

The package layout requires these pyproject changes. They must be
in place **before** any `*_test.py` sibling lands in `src/`,
otherwise tests ship in the release wheel or pytest doesn't find them.

```toml
[tool.pytest.ini_options]
testpaths = ["src", "tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "--import-mode=importlib"

[tool.hatch.build.targets.wheel]
packages = ["src/feather_etl"]
exclude = [
  "src/feather_etl/**/*_test.py",
  "src/feather_etl/**/scenarios/**",
  "src/feather_etl/testing/**",
]
```

- `testpaths` must include `src` so pytest walks the package tree
  for sibling `*_test.py` files.
- `python_files` must include both `test_*.py` (for `tests/invariants/`
  and the existing style) and `*_test.py` (for siblings in `src/`).
- `--import-mode=importlib` prevents `sys.path` surprises when tests
  live next to package code.
- The wheel excludes keep test code out of released artifacts.
  `src/feather_etl/testing/` is excluded by default; if it ever
  becomes a public test harness (for downstream consumers), move it
  out of the exclude list and document the supported API.

---

## Key decisions recorded here

These are the non-obvious placement calls future contributors will
re-litigate if not written down:

- **`pipeline.py` → `commands/run/core.py`**, not `core/`. Single
  caller today. Promote via Rule 7 if a second command starts calling
  `pipeline.run_all` directly.
- **`dq.py` and `schema_drift.py` → `commands/run/`**, not `core/`.
  Same reasoning.
- **`alerts.py` → `core/`**. Cross-cutting infrastructure; alert
  emission is not run-specific.
- **`curation.py` → `core/`**. Scope narrows to the manifest *parser*
  only (curation production moves to an external skill). Consumed by
  validate, run, config.
- **`expand.py` stays in `sources/`**, not `core/`. It operates on
  source objects; sources is the axis owner (Rule 5).
- **`file_source.py` + `database_source.py` → `file_base.py` +
  `db_base.py`**. Rename only; no code merge.
- **`viewer_server.py` → `commands/view/server.py`** (not `core.py`).
  It's an HTTP server, not pure orchestration.
- **`schema_viewer.html` → `src/feather_etl/resources/`**. HTML
  belongs in `resources/`, not `scripts/`.
- **Test pyramid (`unit/ integration/ e2e/`) is replaced.** Tests
  colocate with code or live in `tests/invariants/`. The tiering was
  useful as Wave-A organization; the new convention makes tier
  obvious from file location.

---

## Related

- [`../ORIENTATION.md`](../ORIENTATION.md) — mission, ground rules,
  in-file style rules, feature migration order.
- [`../../CLAUDE.md`](../../CLAUDE.md) — agent context + workflow
  preferences for this worktree.
