# Agent Handoff: Code Reorganization Before Views Implementation

**Task:** Refactor `run_discover` and apply the DHH TOC + Stepdown + Agent-first file-layout template to all source + test files touched by issue #26 (view discovery). Do this BEFORE any view-feature implementation work.

**Scope:** In — reformat 12 source files, their test files, and refactor `run_discover` into named step functions. Out — implementing view discovery itself (that's issue #26 proper).

**Date captured:** 2026-04-21

---

## Decisions already made

- **File-layout template:** DHH TOC + Stepdown + Agent-first. Full spec in `docs/conventions/code-layout.md`.
- **Section order within every file:** Public Interface → Data Types → Private Helpers. (Stepdown: functions are policy, dataclasses are detail.)
- **Banner style:** `# == Section ==` (top-level), `# ── Sub-group ──` (sub-banners when a section has distinct sub-groups).
- **Docstring sizing:** *Just Enough* — lead sentence states what the function does; demote caller/context/preconditions to the bottom. See `principle-just-enough.md`.
- **Define domain vocabulary up front** in module docstrings. A reader should learn what project-specific terms mean before the terms get used. See `principle-just-enough.md` rule #5.
- **Pipeline in Prose:** when temporal order and importance order disagree, encode the temporal sequence in the docstring `CALL ORDER` block and let code order follow importance (with a sub-banner inline explaining the position). See `principle-pipeline-in-prose.md`.
- **`discover.py` is the canonical reference** — study it before reformatting others.
- **`run_discover` refactor target:** ~10-line body, each line a named helper function, so the body reads like a pipeline TOC (e.g. `_resolve_mode_flag`, `_run_prune`, `_run_discovery_loop`, `_mark_orphaned_as_removed`).
- **Accepted-risk coverage gap on `run_discover`:** two partial branches at lines 108→113 and 110→113 in the prune path. Do NOT add tests to close them — decision recorded per `principle-test-worthiness.md` (low likelihood, low blast radius, SQLite migration obviates them soon).

---

## Artifacts produced in this conversation

| Artifact                 | Location                                                                       | Role for this task                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| Layout convention doc    | `docs/conventions/code-layout.md`                                              | The template to apply; read first.                                                                 |
| Reference implementation | `src/feather_flow/discover.py`                                                  | Canonical example of the template; study before reformatting others.                               |
| Issue #26 context        | `docs/issues/26-discover-views.md`                                             | Background on why these files are being touched; do NOT implement these requirements in this task. |

---

## Prerequisites

- `uv run pytest -q` must be green before starting. Expected: `770 passed, 16 skipped`. The 16 skips are environmental (Postgres + SQL Server not running locally); see issue #48.
- Run `uv run pytest -q` after each file touched. Same baseline expected every time. Red → stop, diagnose, do not push through.
- Read `docs/conventions/code-layout.md` in full before editing any file.
- Open `src/feather_flow/discover.py` and mentally map each region to the template sections.

---

## Where to start

1. **Read** `docs/conventions/code-layout.md`. It is self-contained; vault access is not required.
2. **Study** `src/feather_flow/discover.py` as the working example.
3. **Refactor `run_discover`** into named step functions. Incrementally — one extract at a time, tests between each. Target: a `run_discover` body that reads like a TOC of named steps. Accept the two coverage gaps explicitly.
4. **Batch-reformat the 12 source files** (listed below). Apply the template. Run tests after each.
5. **Reformat the test files** (listed below) using the same template where it applies. Run tests after each.

---

## Source files in scope (12; `discover.py` already done)

- `src/feather_flow/sources/__init__.py` — Source protocol, `StreamSchema`
- `src/feather_flow/sources/file_source.py` — base for sqlite + duckdb_file
- `src/feather_flow/sources/database_source.py` — base for postgres / sqlserver / mysql
- `src/feather_flow/sources/sqlite.py`
- `src/feather_flow/sources/postgres.py`
- `src/feather_flow/sources/sqlserver.py`
- `src/feather_flow/sources/mysql.py`
- `src/feather_flow/sources/duckdb_file.py`
- `src/feather_flow/sources/expand.py`
- `src/feather_flow/discover_state.py`
- `src/feather_flow/commands/discover.py` — Typer wrapper
- `src/feather_flow/viewer_server.py`

---

## Test files in scope (verify each exists before editing)

- `tests/unit/test_discover_io.py`
- `tests/unit/test_discover_state.py`
- `tests/unit/commands/test_discover.py`
- `tests/integration/test_discover.py`
- `tests/e2e/test_03_discover.py`
- `tests/unit/sources/test_*.py` — one per source in scope (sqlite, postgres, sqlserver, mysql, duckdb_file)
- Any `tests/integration/test_*.py` that touches the source files above

---

## Deferred / out of scope

- View-discovery feature implementation — that's issue #26 proper.
- Auto-prune with TTL — issue #49.
- Edge-case tests for `run_discover` prune branches — risk accepted.
- SQLite metadata DB migration — separate thread, not part of this task.

---

## References

- Issue #26: `docs/issues/26-discover-views.md`
- Layout convention: `docs/conventions/code-layout.md`
