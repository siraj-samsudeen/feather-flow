# Contributing conventions

This document describes the file conventions for specs, plans, reviews, and
fixes. Every agent working on this repo should read it before starting.

---

## Agent setup

This repo ships with project-local Claude Code configuration so teammates
get identical agent behavior without per-machine setup.

- **Workflow preferences** — see the `# Standing workflow preferences for
  superpowers skills` section in [`../CLAUDE.md`](../CLAUDE.md). Covers
  Subagent-Driven autopilot, worktree-by-default branch creation, and the
  preserved spec-review gate.
- **Vendored superpowers skills** — patched copies of `brainstorming` and
  `writing-plans` live in [`../.claude/skills/`](../.claude/skills/). See
  [`../.claude/skills/README.md`](../.claude/skills/README.md) for
  provenance and how to re-sync with upstream.
- **Spec template** — every design spec in `docs/superpowers/specs/`
  follows the shape of [`conventions/spec-template.md`](conventions/spec-template.md).
  See [`../docs/superpowers/specs/2026-04-21-db-bootstrap-for-tests-design.md`](superpowers/specs/2026-04-21-db-bootstrap-for-tests-design.md)
  for a worked example.
- **Code layout template** — Python source files follow the DHH Stepdown
  shape documented in [`conventions/code-layout.md`](conventions/code-layout.md).
  [`src/feather_etl/discover.py`](../src/feather_etl/discover.py) is the
  canonical reference implementation.

You need nothing beyond Claude Code itself + the `superpowers` plugin
installed. The repo's local configuration takes precedence over plugin
defaults.

---

## Document chain

Each piece of work follows this chain:

```
docs/plans/YYYY-MM-DD-<slice>-<topic>.md       ← implementation plan
docs/reviews/YYYY-MM-DD-<slice>-review.md      ← review of that implementation
docs/plans/YYYY-MM-DD-<slice>-fixes.md         ← plan to address review findings
docs/reviews/YYYY-MM-DD-<slice>-review-2.md    ← re-review after fixes
...
```

Links are two-way: every plan has a `Review:` field once reviewed; every review
has a `Plan:` field pointing to what was reviewed and (once it exists) a
`Fixes:` field pointing to the fix plan.

---

## Plan document header

```markdown
# <Title>

Created: YYYY-MM-DD
Status: DRAFT | APPROVED | IN PROGRESS | VERIFIED
Approved: Yes | No | Pending
Slice: N
Type: Feature | Fix | Refactor | Review-fixes
Review: <path to review doc, added after review>
```

## Review document header

```markdown
# <Slice> Review

Created: YYYY-MM-DD
Slice: N
Plan: <path to plan doc>
Commit: <short hash reviewed>
Status: OPEN | RESOLVED — see <fixes plan path>
Prior review: <path, if this is review-2+>
```

---

## Rules for the fixing agent

1. **Never edit findings in a review doc.**  The review is a permanent record.
   Editing it destroys the audit trail.

2. **Create a fixes plan** at `docs/plans/YYYY-MM-DD-<slice>-fixes.md`.
   Scope it to the findings you intend to address.  List any you are deferring
   and why.

3. **Graduate `TestKnownBugs` tests** as you fix each bug:
   - Read the test docstring — it states current wrong behaviour and correct
     post-fix behaviour.
   - Invert the assertion.
   - Remove the `BUG-N` prefix from the test name.
   - Move the test to the appropriate positive-behaviour test class.

4. **Update BUG-labelled tests** in the relevant layer (`tests/e2e/`,
   `tests/integration/`, or `tests/unit/`) to assert the corrected
   behaviour. Per `tests/README.md`, regression guards use `BUG-N` in the
   test docstring — invert the assertion, rename the function if it
   dropped the prefix, and move it out of any `TestKnownBugs` grouping
   if one existed.

5. **Run the test suite before opening for re-review:**
   ```bash
   uv run pytest -q   # must be all green
   ```

6. **Update the review doc Status line** (and only that line) to:
   `RESOLVED — see docs/plans/YYYY-MM-DD-<slice>-fixes.md`

7. **Do not request a re-review yourself.**  Tell the human what was fixed,
   what was deferred, and why.  The human decides when to ask for a re-review.

---

## Rules for the reviewing agent

1. Read `docs/reviews/` — find the most recent review for the slice.  Its
   `Status:` line tells you whether fixes have been applied.

2. Run both test suites first.  If any test fails before you read a line of
   code, note it immediately.

3. Create a new review file `docs/reviews/YYYY-MM-DD-<slice>-review-N.md`.
   Add `Prior review: <path>` to the header.

4. Cover:
   - Which findings from the prior review were fixed correctly.
   - Which were fixed incorrectly or partially.
   - Which are still open.
   - Any new issues introduced by the fixes.
   - New regression scenarios to add under the appropriate layer in
     `tests/` (e2e / integration / unit per `tests/README.md`).

5. Update artefacts:
   - Add new `BUG-N`-labelled regression tests for any new bugs found,
     in the appropriate `tests/` layer.
   - If new fixtures are needed, add a script in `scripts/` to regenerate them.

---

## Test suite conventions

See [`tests/README.md`](../tests/README.md) for the canonical contract:

- **Three-way decision rule** — where a given test belongs
  (`tests/e2e/` for CLI journeys; `tests/integration/` for multi-module
  Python-API slices; `tests/unit/` mirroring `src/feather_etl/` for
  single-module tests).
- **`ProjectFixture` / `cli` harness** — the fixtures every e2e test uses.
- **Workflow-stage file layout** for `tests/e2e/` (numbered 00–18).
- **Regression guard (`BUG-N`) pattern** — how to record an open bug as
  a failing test, and how to graduate it after the fix lands.

All tests use real DuckDB fixtures where possible; `unittest.mock` is
used only for module-isolation unit tests (e.g., SMTP, pyodbc).

### Fixtures

| File | Schema | Use for |
|---|---|---|
| `tests/fixtures/client.duckdb` | `icube.*`, 6 tables, ~14K rows | Real-world ERP data, messy schemas |
| `tests/fixtures/client_update.duckdb` | `icube.*`, 3 tables, ~12K rows | Future incremental/append tests |
| `tests/fixtures/sample_erp.duckdb` | `erp.*`, 3 tables, 12 rows | Fast tests, NULL handling, simple schema |
| `tests/fixtures/sample_erp.sqlite` | `erp.*`, 3 tables, 12 rows | SQLite source tests |
| `tests/fixtures/csv_data/` | 3 CSV files (orders, customers, products) | CSV source tests |

To regenerate fixtures:
```bash
python scripts/create_sample_erp_fixture.py        # DuckDB
python scripts/create_csv_sqlite_fixtures.py       # CSV + SQLite
```

---

## Testing co-location

Tests track with the **module of origin**, not with the feature that
motivated them. When you edit `SqlServerSource`, every constraint on your
change lives in `tests/test_sqlserver.py`. When you edit the discover
command, every flag combo lives in `tests/commands/test_discover.py`.

The one exception: happy-path E2E tests stand alone as "how does a user
actually use this end-to-end." They live in
`tests/commands/test_<workflow>.py`.

Do **not** create feature-named test files (e.g. `test_multi_source.py`) —
tests belong with the code they exercise.

---

## Local DB prerequisites

Two DB-gated test groups run against your local machine's brew-managed
servers:

- **PostgreSQL** — 5 integration tests in `tests/unit/sources/test_postgres.py`
  and `tests/e2e/test_03_discover.py`.
- **MySQL** — integration tests in `tests/unit/sources/test_mysql.py`.

Both expect a `feather_test` database to exist. The test suite creates
it automatically at session start if the servers are up:

```bash
brew services start postgresql@17 mysql
uv run pytest -q     # 0 DB-gated skips on a healthy setup
```

If a server is down, the suite prints a banner naming the exact
`brew services start …` command, the gated tests skip with a clear
reason, and the run still exits 0. Nothing else is needed — no manual
`createdb`, no `.env` secrets. Localhost auth uses brew defaults
(Postgres peer/trust, MySQL `root` without password).

---

## Dev shortcuts (`poe`)

Composite dev commands live in `[tool.poe.tasks]` in `pyproject.toml`. List
them with `uv run poe` (no args).

| Command | What it does |
|---|---|
| `uv run poe cov <keyword>` | Run tests matching `<keyword>` with branch coverage scoped to `feather_etl.<keyword>`, terminal miss list + HTML report, open the report. `<keyword>` is required — e.g. `uv run poe cov discover`, `uv run poe cov pipeline`. |
| `uv run poe cov-all` | Branch coverage across the whole `feather_etl` package, then open the HTML report. |

Single-command aliases (`ruff check .`, `ruff format .`, `pytest -q`) are
intentionally **not** wrapped — a shortcut that saves zero keystrokes is
cognitive overhead.

**Adding a task:** add an entry under `[tool.poe.tasks]` in `pyproject.toml`.
Prefer the `shell = "…"` form whenever the command uses pipes, `;`, `&&`, or
redirects (poe's default exec mode is argv-based and won't interpret those).
For parameterised tasks, use `${name}` interpolation with an `args = [...]`
declaration — see the existing `cov` task for the pattern.

**Reading the HTML coverage report:** open `htmlcov/index.html`, click a
module, then press `?` for the keyboard shortcuts. The core loop is `m` (show
only missed) then `n` / `p` to walk through gaps. Yellow lines = partial
branch coverage (one side of an `if` never ran); that's why `cov` uses
`--cov-branch`.

---

## Continuous integration

[`.github/workflows/ci.yaml`](../.github/workflows/ci.yaml) runs on every pull
request and on every push to `main`.

| Job | Runs | Blocking? |
|---|---|---|
| `Interpreter pin is a version we claim` | `scripts/check_pinned_python_version.py` | Yes |
| `pytest (Python 3.12 / 3.13)` | `uv sync --all-extras` + `uv run pytest -q` | Yes |
| `pytest (feather-control-plane)` | the member's suite against a `postgres:16` service container | Yes |
| `pytest (feather-cdc-postgres)` | the member's suite against a `postgres:17` service container with `wal_level=logical` | Yes |

**Why 3.10 is non-blocking.** The package advertises 3.10 (`requires-python = ">=3.10"`
plus a 3.10 classifier) but the suite is not green there: 22 failures, all in
`tests/unit/sources/test_sqlserver.py`, all `AttributeError: module
'feather_flow.sources' has no attribute 'sqlserver'`. The file passes when run alone on
3.10 and fails when `tests/unit/sources/` runs as a directory — import-order pollution
between test modules, not a language incompatibility. Keeping it visible but
non-blocking avoids a permanently-red required check, which teaches people to ignore
every check. Fix the pollution, then delete the `experimental` flag.

**The interpreter guard.** `.python-version` must name a version the project claims, in
*both* halves of `[project]`: inside `requires-python`, and carried by a `Programming
Language :: Python :: X.Y` classifier. Both are needed —
`requires-python = ">=3.10"` is open-ended upward, so it alone would accept the 3.14 pin
that broke the suite (issue #83). Run it yourself:

```bash
uv run --no-project --python 3.13 scripts/check_pinned_python_version.py
```

**Bumping `.python-version`** therefore means widening the classifiers in the same
commit, which is the deliberate decision the guard exists to force.

**Workspace members are gated separately.** They version and release on their own cadence, and
most of their tests need a real database, which feather-flow's own suite does not. Two rules
learned the hard way, both written into the workflow as comments:

- Each member job uses `working-directory: packages/<member>`, **not** `uv run --package …
  pytest` from the repo root. `--package` picks the environment, not the tests: pytest reads its
  config from the nearest `pyproject.toml` above the working directory, so from the root it
  collects feather-flow's own suite and reports green having tested nothing of the member.
- A database-gated suite that cannot find a database *skips and exits 0*. `feather-cdc-postgres`
  needs `wal_level=logical`, which a service container cannot be given by `command` — it goes in
  through `POSTGRES_INITDB_ARGS` — so the job asserts the setting took effect before running the
  suite rather than trusting 522 collected tests to mean 522 executed ones.

---

## Current open reviews

| File | Slice | Status |
|---|---|---|
| [docs/reviews/2026-03-26-slice1-review.md](reviews/2026-03-26-slice1-review.md) | 1 | RESOLVED — see [fixes plan](plans/2026-03-26-slice1-fixes.md) |
