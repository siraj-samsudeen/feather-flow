# Auto-Discover Transform DAG from SQL Refs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-infer transform DAG edges from SQL `FROM`/`JOIN` references via sqlglot AST parsing, and **remove `-- depends_on:` as a dependency-declaration mechanism entirely**. The SQL body becomes the single source of truth for the DAG.

**Architecture:** Add a new module `src/feather_etl/transform_deps.py` that owns `extract_dependencies(sql, dialect="duckdb") -> list[str]`. It parses the SQL with `sqlglot`, walks `exp.Table` nodes, filters to `silver.*` / `gold.*`, and returns a sorted list. `parse_transform_file` in `transforms.py` is rewritten so `TransformMeta.depends_on` is **sourced solely** from `extract_dependencies` — the `-- depends_on:` header branch is deleted. Parse failures raise `TransformDepParseError` at discover-time with the file path. Bronze references stay literal in SQL and are ignored. CTEs, comments, and string literals do not generate edges. `-- materialized: true` and `-- fact_table:` headers remain (they encode information the SQL body cannot).

**Backward compatibility:** intentionally none. Any `-- depends_on:` line in an existing `.sql` file becomes an inert comment after this change. There are zero `.sql` transform files on disk in this repo today, so the operator-visible blast radius is zero. Test fixtures that author SQL with `-- depends_on:` headers are rewritten to drop them (Tasks 4–7); the headers are redundant because the same tests already contain `FROM silver.X` clauses that produce the identical edge via inference.

**Tech Stack:** Python stdlib (`graphlib` already in use), [`sqlglot`](https://github.com/tobymao/sqlglot) (new prod dependency, pure Python, no native extensions), `pytest`.

**Reference:** GitHub issue #54 — `feat: auto-discover transform DAG from SQL via AST parsing (sqlglot), no markup required`. Issue body is the spec; acceptance criteria are reproduced in Task 7.

---

## File Structure

| File | Responsibility | New / Modified |
|---|---|---|
| `pyproject.toml` | Add `sqlglot>=26.0` to `[project] dependencies`. | Modified |
| `src/feather_etl/transform_deps.py` | Owns `extract_dependencies(sql, dialect) -> list[str]` (silver/gold) **and** `extract_bronze_dependencies(sql, dialect) -> list[str]` (bronze). Defines `TransformDepParseError`. Isolates the sqlglot import so the rest of the codebase doesn't import it directly. | **New** |
| `src/feather_etl/transforms.py` | `parse_transform_file` is rewritten: the `-- depends_on:` header branch is deleted; `TransformMeta.depends_on = extract_dependencies(sql)`. `-- materialized:` and `-- fact_table:` parsing stays. `check_bronze_dependencies()` is rewritten to walk `t.sql` via `extract_bronze_dependencies(...)` instead of reading `t.depends_on` for bronze entries. | Modified |
| `tests/unit/test_transform_deps.py` | Unit tests for `extract_dependencies` (12 cases) plus `extract_bronze_dependencies` (mirror set: bronze-detected / silver-ignored / CTE-shadow / string-literal / quoted / 3-part / dedupe / no-bronze). | **New** |
| `tests/unit/test_transforms.py` | Rewrite `TestParseTransformFile` fixtures to drop `-- depends_on:` headers (the `FROM silver.X` clauses already in those tests produce the identical edges via inference). Delete tests that asserted only on bronze deps captured via header. Add auto-inference + parse-failure cases. | Modified |
| `tests/unit/test_bronze_check.py` | Rewrite all 7 fixtures to author `TransformMeta` with `sql="... FROM bronze.X"` instead of `depends_on=["bronze.X"]`. Tests preserve their original intent (zero/one/six unmet warnings, scope = silver only). The `check_bronze_dependencies()` public signature is unchanged. | Modified |
| `tests/integration/test_transforms.py` | Strip `-- depends_on:` headers from the 4 existing fixtures (their `FROM silver.X` lines suffice). Add a new test where the no-header style is the primary point of the test (in new Task 8). | Modified |
| `tests/integration/test_transform_command.py` | Strip `-- depends_on: bronze.X` headers from Wave E fixtures; their `FROM bronze.X` clauses in the silver SQL bodies cover the bronze-check input. 19 tests must keep passing. | Modified |
| `tests/integration/test_mode.py`, `tests/integration/test_setup.py`, `tests/e2e/test_07_transforms.py` | Strip `-- depends_on:` headers from fixture SQL. The `FROM silver.X` clauses already present cover the edges via inference. | Modified |
| `docs/issues/54-auto-discover-dag.md` | Pin the issue context per `docs/issues/README.md` conventions. | **New** |
| `docs/issues/README.md` | Add the new doc to the Contents list. | Modified |
| `docs/architecture/warehouse-layers.md` | Replace any mention of `-- depends_on:` with the SQL-inferred contract. The header is no longer part of the public surface. | Modified |
| `README.md`, `docs/prd.md`, `docs/testing-feather-etl.md` | Rewrite operator-facing dependency-declaration sections so the SQL body is the *only* source of truth. Remove `-- depends_on:` from snippets and directory-tree comments. (Task 10.) | Modified |
| `openspec/changes/feather-transform/proposal.md`, `design.md`, `specs/transform-command/spec.md`, `tasks.md` (entries 6.1, 6.3, 7.9), `docs/plans/2026-05-11-feather-transform.md` | PR #56 codified `-- depends_on: bronze.<table>` as the bronze-check input. Update each to reflect the new contract: the bronze-check helper now walks the SQL body. (Task 10.) | Modified |
| `src/feather_etl/transforms.py` (`parse_transform_file` + `check_bronze_dependencies` docstrings) | Update both docstrings to describe SQL-as-source-of-truth and the parse-failure escalation. (Task 10, but updated alongside the code change in Tasks 4 and 7.) | Modified |
| `.planning/codebase/ARCHITECTURE.md`, `docs/plans/2026-03-27-slice8-transforms.md`, `docs/plans/2026-03-29-remaining-slices.md` | **Leave untouched.** Historical commit-time records, not operator-facing material. | Not modified |

---

## Task 1: Add sqlglot dependency

**Files:**
- Modify: `pyproject.toml:23-33`

- [ ] **Step 1: Run the existing test suite from the repo root and confirm green**

Run: `uv run pytest -q`
Expected: all tests pass (CLAUDE.md says ~720 tests at the time this plan was written).

- [ ] **Step 2: Add `sqlglot>=26.0` to `[project].dependencies`**

Edit `pyproject.toml` and add the line shown:

```toml
dependencies = [
    "duckdb>=1.0",
    "pyarrow>=15.0",
    "pyyaml>=6.0",
    "typer>=0.9",
    "pyodbc>=5.0",
    "psycopg2-binary>=2.9",
    "python-dotenv>=1.0",
    "pytz",  # not imported directly, but DuckDB needs it for timestamp→Python conversion
    "mysql-connector-python>=8.0",
    "sqlglot>=26.0",  # SQL AST parsing for transform DAG auto-discovery (issue #54)
]
```

- [ ] **Step 3: Sync the lockfile and verify the import works**

Run:
```bash
uv sync
uv run python -c "import sqlglot; from sqlglot import exp; print(sqlglot.__version__)"
```
Expected: prints a version string `>= 26.0` with no `ModuleNotFoundError`.

- [ ] **Step 4: Re-run the full suite to confirm nothing broke**

Run: `uv run pytest -q`
Expected: same pass count as Step 1.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add sqlglot dependency for transform DAG auto-discovery (#54)"
```

---

## Task 2: Create `extract_dependencies` with a failing test

**Files:**
- Test: `tests/unit/test_transform_deps.py` (new)
- Create (stub only in this task): `src/feather_etl/transform_deps.py`

- [ ] **Step 1: Write the first failing test — a single `FROM silver.foo` is detected**

Create `tests/unit/test_transform_deps.py`:

```python
"""Unit tests for transform_deps.extract_dependencies +
extract_bronze_dependencies (issue #54)."""

from __future__ import annotations

import pytest

from feather_etl.transform_deps import (
    TransformDepParseError,
    extract_bronze_dependencies,
    extract_dependencies,
)


class TestExtractDependenciesBasic:
    def test_single_silver_from(self):
        sql = "SELECT * FROM silver.foo"
        assert extract_dependencies(sql) == ["silver.foo"]
```

- [ ] **Step 2: Run the test and confirm it fails with ModuleNotFoundError**

Run: `uv run pytest tests/unit/test_transform_deps.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'feather_etl.transform_deps'`.

- [ ] **Step 3: Create the module with the minimal implementation that passes the test**

Create `src/feather_etl/transform_deps.py`:

```python
"""Extract transform-layer and bronze-layer dependencies from SQL via
sqlglot AST walk.

Used by ``transforms.parse_transform_file`` to auto-infer DAG edges
(silver / gold) so operators don't have to repeat themselves with
``-- depends_on:`` comments that duplicate the ``FROM``/``JOIN`` clauses
below them, AND by ``transforms.check_bronze_dependencies`` to derive the
advisory bronze-existence input from the same SQL body (so there is one
source of truth for *all* dependency information).

See GitHub issue #54.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

TRANSFORM_SCHEMAS: frozenset[str] = frozenset({"silver", "gold"})
BRONZE_SCHEMA: str = "bronze"


class TransformDepParseError(ValueError):
    """Raised when sqlglot cannot parse a transform's SQL body."""


def _walk_tables_in_schemas(
    sql: str, schemas: frozenset[str], dialect: str
) -> list[str]:
    """Internal helper: walk the AST of ``sql`` and return every
    ``exp.Table`` whose ``db`` is in ``schemas``, deduped + sorted as
    ``"<schema>.<name>"``.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as e:
        raise TransformDepParseError(f"sqlglot parse failed: {e}") from e

    refs: set[str] = set()
    for table in tree.find_all(exp.Table):
        if table.db in schemas:
            refs.add(f"{table.db}.{table.name}")
    return sorted(refs)


def extract_dependencies(sql: str, dialect: str = "duckdb") -> list[str]:
    """Return the sorted, deduplicated list of ``silver.*`` / ``gold.*``
    tables referenced anywhere in ``sql``.

    Walks the AST produced by ``sqlglot.parse_one`` and returns every
    ``exp.Table`` whose ``db`` part is in ``TRANSFORM_SCHEMAS``. CTE names,
    string literals, comments, and table-valued functions like
    ``read_csv(...)`` are not ``exp.Table`` nodes and are therefore ignored
    automatically.

    Raises ``TransformDepParseError`` if the SQL cannot be parsed.
    """
    return _walk_tables_in_schemas(sql, TRANSFORM_SCHEMAS, dialect)


def extract_bronze_dependencies(sql: str, dialect: str = "duckdb") -> list[str]:
    """Return the sorted, deduplicated list of ``bronze.*`` tables
    referenced anywhere in ``sql``. Used by the advisory bronze-existence
    check (see ``transforms.check_bronze_dependencies``).

    Same AST-walk guarantees as :func:`extract_dependencies` — CTEs,
    string literals, comments, and TVFs do not match.

    Raises ``TransformDepParseError`` if the SQL cannot be parsed.
    """
    return _walk_tables_in_schemas(sql, frozenset({BRONZE_SCHEMA}), dialect)
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `uv run pytest tests/unit/test_transform_deps.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/feather_etl/transform_deps.py tests/unit/test_transform_deps.py
git commit -m "feat(transforms): add extract_dependencies for SQL AST DAG inference (#54)"
```

---

## Task 3: Cover every acceptance-criteria edge case for `extract_dependencies`

**Files:**
- Modify: `tests/unit/test_transform_deps.py`

These tests verify that the same `extract_dependencies` already written in Task 2 handles real-world SQL — they should all pass without further code changes because the AST walk is correct by construction. If any test fails, fix `extract_dependencies` before continuing.

- [ ] **Step 1: Add the full edge-case test class**

Append to `tests/unit/test_transform_deps.py` (below the `TestExtractDependenciesBasic` class):

```python
class TestExtractDependenciesEdgeCases:
    def test_join_silver_and_gold(self):
        sql = "SELECT * FROM silver.a JOIN gold.b USING (id)"
        assert extract_dependencies(sql) == ["gold.b", "silver.a"]

    def test_bronze_references_are_ignored(self):
        # bronze.* is the extraction layer — not a transform-layer edge.
        sql = "SELECT * FROM bronze.foo JOIN silver.bar USING (id)"
        assert extract_dependencies(sql) == ["silver.bar"]

    def test_cte_does_not_shadow_silver(self):
        # WITH foo AS (...) SELECT * FROM foo must NOT create silver.foo.
        sql = (
            "WITH foo AS (SELECT * FROM bronze.bar) "
            "SELECT * FROM foo JOIN silver.baz USING (id)"
        )
        assert extract_dependencies(sql) == ["silver.baz"]

    def test_string_literal_mentioning_silver_is_ignored(self):
        # A literal that *looks* like a table reference must not match.
        sql = (
            "SELECT * FROM silver.real "
            "WHERE label = 'FROM silver.fake'"
        )
        assert extract_dependencies(sql) == ["silver.real"]

    def test_comment_mentioning_silver_is_ignored(self):
        # Comments are stripped by sqlglot during parse.
        sql = (
            "-- this comment mentions silver.fake but it isn't a dep\n"
            "SELECT * FROM silver.real"
        )
        assert extract_dependencies(sql) == ["silver.real"]

    def test_duplicate_references_deduped(self):
        sql = "SELECT * FROM silver.foo UNION ALL SELECT * FROM silver.foo"
        assert extract_dependencies(sql) == ["silver.foo"]

    def test_quoted_identifiers(self):
        # DuckDB accepts double-quoted identifiers; the walk should match.
        sql = 'SELECT * FROM "silver"."foo"'
        assert extract_dependencies(sql) == ["silver.foo"]

    def test_three_part_name_collapses_to_schema_table(self):
        # db.silver.foo is a fully-qualified reference; we still want silver.foo.
        sql = "SELECT * FROM mydb.silver.foo"
        assert extract_dependencies(sql) == ["silver.foo"]

    def test_table_valued_function_ignored(self):
        # read_csv(...) is parsed as a function call, not a Table — no edge.
        sql = "SELECT * FROM read_csv('x.csv') JOIN silver.dim USING (id)"
        assert extract_dependencies(sql) == ["silver.dim"]

    def test_no_transform_layer_references(self):
        sql = "SELECT 1 AS val"
        assert extract_dependencies(sql) == []

    def test_subquery_dependency(self):
        sql = "SELECT * FROM (SELECT id FROM silver.inner) sub"
        assert extract_dependencies(sql) == ["silver.inner"]


class TestExtractDependenciesParseFailure:
    def test_malformed_sql_raises(self):
        # A genuinely broken statement.
        with pytest.raises(TransformDepParseError):
            extract_dependencies("SELECT FROM WHERE")


class TestExtractBronzeDependencies:
    def test_single_bronze_from(self):
        sql = "SELECT * FROM bronze.foo"
        assert extract_bronze_dependencies(sql) == ["bronze.foo"]

    def test_multiple_bronze_refs_sorted_deduped(self):
        sql = (
            "SELECT * FROM bronze.b JOIN bronze.a USING (id) "
            "UNION ALL SELECT * FROM bronze.b"
        )
        assert extract_bronze_dependencies(sql) == ["bronze.a", "bronze.b"]

    def test_silver_and_gold_refs_ignored(self):
        # The bronze walker must NOT pick up silver/gold tables.
        sql = "SELECT * FROM bronze.real JOIN silver.dim USING (id) JOIN gold.fact USING (id)"
        assert extract_bronze_dependencies(sql) == ["bronze.real"]

    def test_cte_named_bronze_x_does_not_shadow(self):
        # WITH bronze AS (...) is invalid SQL because `bronze` is a schema
        # name, not an identifier; but a CTE named `b` containing a join
        # against bronze.x must still produce bronze.x.
        sql = (
            "WITH b AS (SELECT * FROM bronze.real) "
            "SELECT * FROM b"
        )
        assert extract_bronze_dependencies(sql) == ["bronze.real"]

    def test_string_literal_mentioning_bronze_is_ignored(self):
        sql = "SELECT * FROM bronze.real WHERE label = 'FROM bronze.fake'"
        assert extract_bronze_dependencies(sql) == ["bronze.real"]

    def test_quoted_bronze_identifier(self):
        sql = 'SELECT * FROM "bronze"."foo"'
        assert extract_bronze_dependencies(sql) == ["bronze.foo"]

    def test_three_part_bronze_name(self):
        sql = "SELECT * FROM mydb.bronze.foo"
        assert extract_bronze_dependencies(sql) == ["bronze.foo"]

    def test_no_bronze_refs(self):
        sql = "SELECT * FROM silver.foo"
        assert extract_bronze_dependencies(sql) == []

    def test_malformed_sql_raises(self):
        with pytest.raises(TransformDepParseError):
            extract_bronze_dependencies("SELECT FROM WHERE")
```

- [ ] **Step 2: Run the new tests and confirm they all pass**

Run: `uv run pytest tests/unit/test_transform_deps.py -q`
Expected: all tests pass (12 silver/gold cases + 9 bronze cases = 21 total in this file).

> If any AST-walk test fails: that's a real bug in `extract_dependencies` / `extract_bronze_dependencies`, not in the test. Read the sqlglot docs for the failing case and adjust the helper. Do **not** loosen the test. The two most likely root causes are (a) the dialect arg being wrong for a DuckDB-specific syntax, or (b) sqlglot returning `db=""` for unqualified names — which is fine, because the filter checks for explicit schema membership. Do not add CTE-specific logic; sqlglot already excludes CTE names from `exp.Table` traversal.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_transform_deps.py
git commit -m "test(transforms): cover CTE/comment/string-literal edges in extract_dependencies (#54)"
```

---

## Task 4: Rewrite `parse_transform_file` — delete the `-- depends_on:` branch, source `depends_on` from SQL only (TDD)

**Files:**
- Test: `tests/unit/test_transforms.py` — extend and rewrite `TestParseTransformFile`
- Modify: `src/feather_etl/transforms.py:46-93` (loader)

This is the load-bearing change. After this task, `-- depends_on:` is **not part of the transform contract**. The header line, if encountered, is silently ignored (it falls through to `sql_lines` like any non-recognised comment and is stripped by sqlglot at parse). The only mechanism for declaring DAG edges is `FROM` / `JOIN` references in the SQL body.

- [ ] **Step 1: Add a failing test — auto-inferred edge with no header**

Open `tests/unit/test_transforms.py` and append to class `TestParseTransformFile` (right after `test_parse_multiple_depends_on_interleaved`):

```python
    def test_parse_auto_infers_dep_from_from_clause(self, tmp_path: Path):
        # No -- depends_on: header. The FROM clause alone must produce the edge.
        content = (
            "-- materialized: true\n"
            "SELECT store_name, SUM(daily_cost) AS total_cost\n"
            "FROM silver.storewise_hrcost\n"
            "GROUP BY store_name"
        )
        p = _write_sql(tmp_path, "gold", "store_cost", content)
        meta = parse_transform_file(p)

        assert meta.depends_on == ["silver.storewise_hrcost"]
        assert meta.materialized is True
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/unit/test_transforms.py::TestParseTransformFile::test_parse_auto_infers_dep_from_from_clause -q`
Expected: FAIL — `meta.depends_on` is `[]` because the current loader only reads the `-- depends_on:` comment.

- [ ] **Step 3: Rewrite `parse_transform_file` — delete the header branch entirely**

Open `src/feather_etl/transforms.py`. Add the import at the top alongside the others:

```python
from feather_etl.transform_deps import extract_dependencies, TransformDepParseError
```

Replace the entire body of `parse_transform_file` (currently lines 46–93) with this. The `-- depends_on:` branch in the for-loop is **removed**; the docstring is rewritten:

```python
def parse_transform_file(path: Path) -> TransformMeta:
    """Parse a .sql file into TransformMeta.

    DAG edges (`depends_on`) are derived **solely** from the SQL body via
    :func:`feather_etl.transform_deps.extract_dependencies` — every
    ``silver.*`` / ``gold.*`` table referenced in a ``FROM``/``JOIN``
    clause becomes an edge. CTEs, comments, string literals, and
    table-valued functions like ``read_csv(...)`` do not create edges.

    Header comment lines retain meaning only for information the SQL body
    cannot encode: ``-- materialized: true`` (gold materialisation) and
    ``-- fact_table: <name>`` (join-health-check declaration). The
    ``-- depends_on:`` header is no longer recognised — see GitHub
    issue #54; any such line in an existing file is silently ignored
    (treated as a regular SQL comment).

    Raises ``TransformDepParseError`` if the SQL body cannot be parsed.
    """
    text = path.read_text()
    lines = text.splitlines()

    materialized = False
    fact_table: str | None = None
    sql_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "-- materialized: true":
            materialized = True
        elif stripped.startswith("-- fact_table:"):
            fact_table = stripped.removeprefix("-- fact_table:").strip() or None
        else:
            sql_lines.append(line)

    # Strip leading/trailing blank lines from SQL body
    sql = "\n".join(sql_lines).strip()

    name = path.stem  # sales_invoice.sql -> sales_invoice
    schema = path.parent.name  # transforms/silver/ -> silver

    if schema not in VALID_SCHEMAS:
        raise ValueError(
            f"Transform '{path}' is in directory '{schema}', "
            f"expected one of {sorted(VALID_SCHEMAS)}"
        )

    # Derive deps from the SQL body. Issue #54 — sole source of truth.
    try:
        depends_on = extract_dependencies(sql)
    except TransformDepParseError as e:
        raise TransformDepParseError(
            f"Failed to parse transform SQL at {path}: {e}"
        ) from e

    return TransformMeta(
        name=name,
        schema=schema,
        sql=sql,
        depends_on=depends_on,
        materialized=materialized,
        fact_table=fact_table,
        path=path,
    )
```

> Note the import at the top of `transforms.py`. `TransformDepParseError` is re-raised with the file path wrapped in; the original sqlglot error message is preserved via `from e`.

- [ ] **Step 4: Run the new auto-inference test and confirm it passes**

Run: `uv run pytest tests/unit/test_transforms.py::TestParseTransformFile::test_parse_auto_infers_dep_from_from_clause -q`
Expected: 1 passed.

- [ ] **Step 5: Triage the existing `TestParseTransformFile` failures**

Run: `uv run pytest tests/unit/test_transforms.py::TestParseTransformFile -q`
Expected: several existing tests now fail because their fixtures author SQL with `-- depends_on:` headers and assert on `meta.depends_on` populated from those headers. We rewrite each failing test below. **Each rewrite is surgical — do not delete unless the test is no longer meaningful.**

> Open the failing tests one by one and apply the rewrites in Steps 6–11 below. Each rewrite preserves the *intent* of the original test (e.g. "multiple deps captured", "blank lines stripped") but sources the dep through the SQL body instead of the header.

- [ ] **Step 6: Delete `test_parse_with_depends_on`**

This test (around lines 107–119) declared bronze deps in `-- depends_on:` headers and asserted those exact bronze entries appeared in `depends_on`. Under the new contract, bronze references **never** appear in `depends_on` (they're the extraction layer). The test is no longer meaningful. Delete it entirely. The "multiple inferred deps captured" intent is now covered by the next rewrite.

- [ ] **Step 7: Rewrite `test_parse_materialized_gold`**

Replace the existing test (around lines 121–132) with:

```python
    def test_parse_materialized_gold(self, tmp_path: Path):
        content = (
            "-- materialized: true\n"
            "SELECT * FROM silver.employees"
        )
        p = _write_sql(tmp_path, "gold", "employee_summary", content)
        meta = parse_transform_file(p)

        assert meta.schema == "gold"
        assert meta.materialized is True
        assert meta.depends_on == ["silver.employees"]
        assert meta.qualified_name == "gold.employee_summary"
```

(The `-- depends_on: silver.employees` line is removed; the `FROM silver.employees` clause produces the same edge via inference.)

- [ ] **Step 8: Rewrite `test_parse_preserves_non_metadata_comments`**

The original test asserted (a) a regular `-- comment` line survives into `meta.sql`, and (b) `-- depends_on: bronze.sales` was captured. Under the new contract, only (a) matters. Replace the existing test (around lines 142–152) with:

```python
    def test_parse_preserves_non_metadata_comments(self, tmp_path: Path):
        content = (
            "-- This is a regular comment\n"
            "SELECT amount FROM bronze.sales"
        )
        p = _write_sql(tmp_path, "silver", "sales_clean", content)
        meta = parse_transform_file(p)

        assert "-- This is a regular comment" in meta.sql
        assert meta.depends_on == []  # bronze ref produces no transform-layer edge
```

- [ ] **Step 9: Rewrite `test_parse_strips_leading_trailing_blank_lines`**

The original test used `-- depends_on: bronze.x` as the header. Swap to a meaningful header that the new contract still recognises:

```python
    def test_parse_strips_leading_trailing_blank_lines(self, tmp_path: Path):
        content = "\n\n-- materialized: true\n\nSELECT 1\n\n"
        p = _write_sql(tmp_path, "gold", "stripped", content)
        meta = parse_transform_file(p)

        assert meta.sql == "SELECT 1"
        assert meta.materialized is True
```

(Schema switched from `silver` to `gold` because `-- materialized:` is only meaningful for gold; intent unchanged.)

- [ ] **Step 10: Rewrite `test_parse_multiple_depends_on_interleaved`**

Original test had two `-- depends_on:` lines interleaved with `-- materialized: true`. Replace with one that sources both deps from the SQL body and keeps the materialised flag:

```python
    def test_parse_multiple_deps_from_sql_body(self, tmp_path: Path):
        # Two inferred deps, returned in sorted order. Materialized flag still
        # honoured. Issue #54 — replaces the old header-interleaved variant.
        content = (
            "-- materialized: true\n"
            "SELECT * FROM silver.a JOIN silver.b USING (id)"
        )
        p = _write_sql(tmp_path, "gold", "joined", content)
        meta = parse_transform_file(p)

        assert meta.depends_on == ["silver.a", "silver.b"]
        assert meta.materialized is True
```

> Rename the test method as shown — the old name `test_parse_multiple_depends_on_interleaved` is now misleading.

- [ ] **Step 11: Confirm the `-- depends_on:` header is silently ignored when present (grandfathered files)**

Add this new test to `TestParseTransformFile` so the silent-ignore behaviour is pinned (some fixtures elsewhere in the codebase still contain old headers; this test guarantees they don't break):

```python
    def test_parse_legacy_depends_on_header_is_silently_ignored(
        self, tmp_path: Path
    ):
        # Issue #54 removed -- depends_on: as a contract. A legacy file
        # that still contains the header must parse successfully — the
        # header line is treated as a regular SQL comment (and stripped
        # by sqlglot during AST walk). Only inferred edges count.
        content = (
            "-- depends_on: silver.legacy_marker\n"
            "-- materialized: true\n"
            "SELECT * FROM silver.real"
        )
        p = _write_sql(tmp_path, "gold", "legacy", content)
        meta = parse_transform_file(p)

        assert meta.depends_on == ["silver.real"]
        assert "silver.legacy_marker" not in meta.depends_on
        assert meta.materialized is True
```

- [ ] **Step 12: Run the full `TestParseTransformFile` class and confirm all tests pass**

Run: `uv run pytest tests/unit/test_transforms.py::TestParseTransformFile -q`
Expected: every test in the class passes (the original 6 we kept/rewrote, plus the auto-inference test from Step 1, plus the legacy-header test from Step 11). The deleted `test_parse_with_depends_on` is gone.

- [ ] **Step 13: Commit**

```bash
git add src/feather_etl/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms)!: derive depends_on solely from SQL body (#54)

BREAKING: the `-- depends_on:` header is no longer part of the transform
contract. DAG edges are derived only from FROM/JOIN references in the
SQL body via sqlglot. Legacy headers are silently ignored — they fall
through to the SQL body and sqlglot strips them at parse. There are
zero .sql transform files on disk in this repo today, so operator
blast radius is zero."
```

---

## Task 5: Cover CTE-shadow, string-literal, and parse-failure cases at the loader level

**Files:**
- Modify: `tests/unit/test_transforms.py` — extend `TestParseTransformFile`

These tests pin the acceptance criteria listed in the issue body at the loader-level (the lower-level `extract_dependencies` is already covered in Task 3, but pinning the same behaviour through the file-loading code path catches integration regressions). All tests pass without further code changes after Task 4.

> Removed from this task vs. the prior plan revision: the "declared+inferred union" and "declared data-only override" tests. They're obsolete under the no-backcompat decision — there is no declared path to union with.

- [ ] **Step 1: Add the acceptance-criteria tests**

Open `tests/unit/test_transforms.py` and add inside `TestParseTransformFile`:

```python
    def test_parse_cte_shadow_no_phantom_dep(self, tmp_path: Path):
        # A CTE named `foo` must not create silver.foo as a dependency,
        # even if the SQL uses `FROM foo` elsewhere.
        content = (
            "WITH foo AS (SELECT * FROM bronze.bar)\n"
            "SELECT * FROM foo JOIN silver.baz USING (id)"
        )
        p = _write_sql(tmp_path, "silver", "cte_user", content)
        meta = parse_transform_file(p)

        assert meta.depends_on == ["silver.baz"]
        assert "silver.foo" not in meta.depends_on

    def test_parse_string_literal_does_not_create_dep(self, tmp_path: Path):
        # 'FROM silver.boom' is a string value, not a table reference.
        content = (
            "SELECT * FROM silver.real WHERE label = 'FROM silver.boom'"
        )
        p = _write_sql(tmp_path, "gold", "no_boom", content)
        meta = parse_transform_file(p)

        assert meta.depends_on == ["silver.real"]
        assert "silver.boom" not in meta.depends_on

    def test_parse_raises_on_unparseable_sql(self, tmp_path: Path):
        from feather_etl.transform_deps import TransformDepParseError

        p = _write_sql(tmp_path, "silver", "bad", "SELECT FROM WHERE")
        with pytest.raises(TransformDepParseError, match="bad.sql"):
            parse_transform_file(p)

    def test_parse_raises_on_empty_sql_body(self, tmp_path: Path):
        # A file with only headers and no SELECT body must fail loudly at
        # discover-time, not silently install an empty view at execute-time.
        from feather_etl.transform_deps import TransformDepParseError

        content = "-- materialized: true\n"  # no SELECT body at all
        p = _write_sql(tmp_path, "gold", "empty", content)
        with pytest.raises(TransformDepParseError, match="empty.sql"):
            parse_transform_file(p)
```

- [ ] **Step 2: Run the new tests and confirm they all pass**

Run: `uv run pytest tests/unit/test_transforms.py::TestParseTransformFile -q`
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_transforms.py
git commit -m "test(transforms): cover CTE/string-literal/empty-body cases at loader level (#54)"
```

---

## Task 6: Topo sort with auto-inferred edges (failing tests first) + rewrite existing fixtures

**Files:**
- Modify: `tests/unit/test_transforms.py` — extend `TestBuildExecutionOrder`, rewrite `test_missing_dependency_raises`

`build_execution_order` is unchanged in this issue — it reads `t.depends_on` and doesn't care where the entries came from. But its existing `test_missing_dependency_raises` test (around line 778) currently writes a fixture with `-- depends_on: silver.nonexistent\nSELECT 1` and routes through `_write_sql` + `discover_transforms`. Under the new contract `-- depends_on:` is inert, so the fixture would no longer trigger the missing-dependency path. Rewrite it to use `FROM silver.nonexistent`.

- [ ] **Step 1: Add the auto-inferred-orders-correctly test**

Append to `tests/unit/test_transforms.py` inside `TestBuildExecutionOrder`:

```python
    def test_auto_inferred_dep_orders_correctly_via_parse(self, tmp_path: Path):
        # Full pipeline: parse a transform with no header, then topo-sort
        # it against its (no-dep) predecessor.
        from feather_etl.transforms import discover_transforms

        _write_sql(tmp_path, "silver", "foo", "SELECT 1 AS id")
        _write_sql(
            tmp_path,
            "gold",
            "bar",
            "SELECT * FROM silver.foo",  # inferred dep only
        )

        transforms = discover_transforms(tmp_path)
        ordered = build_execution_order(transforms)
        names = [t.qualified_name for t in ordered]

        assert names.index("silver.foo") < names.index("gold.bar")

    def test_missing_inferred_dep_raises_same_error_as_before(
        self, tmp_path: Path
    ):
        # Acceptance criterion (issue #54): "A transform that references a
        # non-existent silver.bar raises the same missing-dependency error
        # as today."
        from feather_etl.transforms import discover_transforms

        _write_sql(
            tmp_path,
            "gold",
            "bar",
            "SELECT * FROM silver.nonexistent",
        )

        transforms = discover_transforms(tmp_path)
        with pytest.raises(ValueError, match="nonexistent"):
            build_execution_order(transforms)
```

- [ ] **Step 2: Rewrite the existing `test_missing_dependency_raises`**

Locate the existing test (it constructs `TransformMeta` in-memory with `depends_on=["silver.nonexistent"]` — that's around line 281; the version that uses `_write_sql` is around line 778 in the end-to-end class). The in-memory variant is fine and stays unchanged because it constructs `TransformMeta` directly without going through the loader. **Verify** it still passes:

Run: `uv run pytest tests/unit/test_transforms.py::TestBuildExecutionOrder::test_missing_dependency_raises -q`
Expected: PASS.

For the file-based end-to-end variant near line 778 (it writes `-- depends_on: silver.nonexistent\nSELECT 1` to disk), rewrite the fixture body:

```python
        # Before:
        _write_sql(
            tmp_path, "gold", "bad", ("-- depends_on: silver.nonexistent\nSELECT 1")
        )

        # After (issue #54 — depends_on derived from SQL body):
        _write_sql(
            tmp_path, "gold", "bad", "SELECT * FROM silver.nonexistent"
        )
```

Run the rewritten test:

Run: `uv run pytest tests/unit/test_transforms.py -k "missing" -q`
Expected: all "missing"-named tests pass (both the in-memory `TransformMeta` variant and the file-based variant).

- [ ] **Step 3: Run the full `TestBuildExecutionOrder` class**

Run: `uv run pytest tests/unit/test_transforms.py::TestBuildExecutionOrder -q`
Expected: all tests pass.

- [ ] **Step 4: Audit and rewrite any other `-- depends_on:` usage in `tests/unit/test_transforms.py`**

Run:
```bash
git grep -n "depends_on:" tests/unit/test_transforms.py
```
For every hit:
- If the line is a `_write_sql(...)` fixture body containing `-- depends_on:`, delete that one line from the fixture string. The `FROM silver.X` clause already present in the same SQL body produces the equivalent inferred edge.
- If the line is an assertion (`assert meta.depends_on == [...]`), verify the asserted list matches what `extract_dependencies` would return for that fixture's SQL body (sorted, deduplicated, transform-layer only). If yes, leave it. If not, fix it.
- If the line is a Python comment referring to the old contract, update or delete it.

The end-to-end tests near the bottom of the file (lines ~661–784) author multi-transform fixtures with `-- depends_on: silver.employee_master`, `-- depends_on: silver.item_master`, etc. Each of those fixtures already has the equivalent `FROM` / `JOIN` clause in its SQL body, so deleting the headers leaves the test behaviour identical.

Run the full unit suite to confirm:

Run: `uv run pytest tests/unit/test_transforms.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_transforms.py
git commit -m "test(transforms): topo-sort with inferred edges; rewrite legacy header fixtures (#54)"
```

---

## Task 7: Rewrite `check_bronze_dependencies` to walk SQL via sqlglot (TDD)

**Files:**
- Test: `tests/unit/test_bronze_check.py` — rewrite all 7 fixtures
- Modify: `src/feather_etl/transforms.py` — body of `check_bronze_dependencies` (around lines 277–317 on the current main; the function exists as of PR #56)

PR #56 added `check_bronze_dependencies(con, transforms) -> list[str]` which warns operators when a silver transform declares a `bronze.<table>` dependency that doesn't exist in the destination. It currently reads `t.depends_on` for entries starting with `bronze.`. Under Task 4's new contract, `parse_transform_file` never puts bronze entries into `depends_on` (it filters to silver/gold only). So without this rewrite, the helper becomes a permanent no-op in production — and PR #56's 19 Wave E integration tests (`tests/integration/test_transform_command.py`) would all break because they author silver SQL with `-- depends_on: bronze.X` headers expecting warnings.

Pivot: derive the bronze input from the SQL body via `extract_bronze_dependencies` (added in Task 2). Public signature is **unchanged** — same args, same return type, same warning string format. The 7 existing unit tests in `test_bronze_check.py` get their fixtures rewritten from `depends_on=["bronze.X"]` to `sql="...FROM bronze.X"`.

- [ ] **Step 1: Rewrite the 7 fixtures in `tests/unit/test_bronze_check.py`**

For each test, replace the `TransformMeta` construction so the bronze dependency comes from the SQL body, not from `depends_on`. Concretely:

```python
# Before (anywhere in the file):
TransformMeta(name="orders_clean", schema="silver", sql="SELECT 1",
              depends_on=["bronze.orders"])

# After:
TransformMeta(name="orders_clean", schema="silver",
              sql="SELECT * FROM bronze.orders")
```

Apply this rewrite to every test in the file (`test_zero_declared_deps_returns_empty`, `test_one_declared_dep_present_returns_empty`, `test_one_declared_dep_missing_returns_one_warning`, `test_six_missing_deps_returns_six_warnings`, `test_mixed_with_and_without_depends_on`, `test_silver_dep_on_silver_not_checked`, `test_gold_with_bronze_dep_not_checked`). For `test_six_missing_deps_returns_six_warnings`, use six distinct silver transforms each with `sql="SELECT * FROM bronze.t{i}"`.

For `test_silver_dep_on_silver_not_checked`: change the fixture's SQL to `"SELECT * FROM silver.x"` (a silver→silver reference — the helper must ignore it because it walks bronze only).

For `test_gold_with_bronze_dep_not_checked`: change to gold-schema fixture with `sql="SELECT * FROM bronze.absent"` (scope is silver only — gold's bronze refs are out of scope for this helper). Note: under the new contract a gold transform referencing `bronze.X` is *unusual* (golds normally read silver) but the test still pins the helper's silver-only-scope behaviour, and the rewrite preserves intent.

> Don't touch the test class name, the `con` fixture, the assertion strings (`"WARNING: bronze dependency missing: bronze.orders"` etc.), or the `feather extract` substring assertions. The public contract of `check_bronze_dependencies` is unchanged — only its input source flips from `depends_on` to SQL.

- [ ] **Step 2: Run the rewritten tests; expect failures because the implementation still reads `depends_on`**

Run: `uv run pytest tests/unit/test_bronze_check.py -q`
Expected: most tests fail because the current implementation iterates `t.depends_on`, which is now empty (or silver/gold-only). One or two tests that already asserted "no warnings" will still pass coincidentally.

- [ ] **Step 3: Rewrite `check_bronze_dependencies` in `src/feather_etl/transforms.py`**

Locate the function (around lines 277–317). Add the import at the top of the file (next to the `extract_dependencies` import added in Task 4):

```python
from feather_etl.transform_deps import (
    extract_bronze_dependencies,
    extract_dependencies,
    TransformDepParseError,
)
```

(Combine into the existing single-line import if it already imports `extract_dependencies` and `TransformDepParseError`.)

Replace the body of the function with:

```python
def check_bronze_dependencies(
    con: duckdb.DuckDBPyConnection,
    transforms: list[TransformMeta],
) -> list[str]:
    """Return a warning string for each unmet ``bronze.<table>`` dependency.

    Walks each silver transform's SQL body via sqlglot
    (:func:`feather_etl.transform_deps.extract_bronze_dependencies`) and
    checks every ``bronze.<table>`` reference against the destination's
    ``information_schema.tables`` (filtered to the ``bronze`` schema).

    Returns one warning string per unmet dependency, in the order
    discovered. The caller decides whether to render each entry as its own
    line or collapse the list into a summary — that presentation concern
    is not the helper's job.

    Scope is silver→bronze only. Cross-transform deps (``silver.X`` /
    ``gold.X``) are out of scope here — ``build_execution_order`` already
    validates them. Gold transforms referencing bronze are also out of
    scope; production transforms read bronze only from silver.

    The SQL body is the only source of truth for bronze deps. Issue #54
    removed the ``-- depends_on: bronze.<table>`` header convention.
    """
    rows = con.execute(
        "SELECT table_schema || '.' || table_name "
        "FROM information_schema.tables "
        "WHERE table_schema = 'bronze'"
    ).fetchall()
    available: set[str] = {row[0] for row in rows}

    warnings: list[str] = []
    for t in transforms:
        if t.schema != "silver":
            continue
        for dep in extract_bronze_dependencies(t.sql):
            if dep not in available:
                warnings.append(
                    f"WARNING: bronze dependency missing: {dep} "
                    f"(run `feather extract` first)"
                )
    return warnings
```

The structure (silver-only filter, duckdb lookup, sorted warnings list, warning string format) is unchanged. Only the per-transform inner loop swaps `t.depends_on` for `extract_bronze_dependencies(t.sql)`.

- [ ] **Step 4: Run the bronze-check tests and confirm they pass**

Run: `uv run pytest tests/unit/test_bronze_check.py -q`
Expected: all 7 tests pass.

- [ ] **Step 5: Run the full unit suite to confirm no regression**

Run: `uv run pytest tests/unit/ -q`
Expected: all unit tests pass (the previous tests from Tasks 4–6 + the rewritten bronze-check tests).

- [ ] **Step 6: Commit**

```bash
git add src/feather_etl/transforms.py tests/unit/test_bronze_check.py
git commit -m "feat(transforms)!: walk SQL for bronze-check input, not depends_on (#54)

BREAKING: check_bronze_dependencies() now derives bronze refs from each
silver transform's SQL body via extract_bronze_dependencies, replacing
the prior read of TransformMeta.depends_on for entries prefixed with
'bronze.'. Public signature and warning-string format are unchanged.
This change is forced by issue #54's deletion of the -- depends_on:
header parser; the bronze-check helper would otherwise become a
permanent no-op in production."
```

---

## Task 8: Integration / E2E regression — rewrite header-based fixtures and add a no-header end-to-end run

**Files:**
- Modify: `tests/integration/test_transforms.py`
- Modify: `tests/integration/test_transform_command.py` (Wave E suite, 19 tests, added by PR #56)
- Modify: `tests/integration/test_mode.py`
- Modify: `tests/integration/test_setup.py`
- Modify: `tests/e2e/test_07_transforms.py`

All five files currently author fixture SQL with `-- depends_on:` headers alongside `FROM silver.X` clauses. Under the new contract the headers are inert; remove them. Each fixture's edge survives via inference (and, for Wave E's bronze advisory, via `extract_bronze_dependencies` after Task 7).

- [ ] **Step 1: Strip `-- depends_on:` from `tests/integration/test_transforms.py`**

In `test_run_rebuilds_materialized_gold` (around lines 56–61) and `test_run_mode_switch_rematerializes_gold` (around lines 105–110), the gold fixture is:

```python
        "-- depends_on: silver.emp_clean\n"
        "-- materialized: true\n"
        "SELECT * FROM silver.emp_clean"
```

Replace each occurrence with:

```python
        "-- materialized: true\n"
        "SELECT * FROM silver.emp_clean"
```

- [ ] **Step 2: Append the new no-header end-to-end run test**

Append to `tests/integration/test_transforms.py`:

```python
def test_run_auto_infers_silver_to_gold_dep(tmp_path: Path):
    """A gold transform with no -- depends_on: header but a `FROM silver.X`
    clause is correctly ordered after silver.X by run_all. Regression for
    issue #54 (the headline feature)."""
    source_db = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source_db))
    con.execute("CREATE SCHEMA IF NOT EXISTS erp")
    con.execute("CREATE TABLE erp.employees (id INT, name VARCHAR)")
    con.execute("INSERT INTO erp.employees VALUES (1, 'Alice'), (2, 'Bob')")
    con.close()

    dest_db = tmp_path / "feather_data.duckdb"
    config = {
        "sources": [{"type": "duckdb", "name": "src", "path": str(source_db)}],
        "destination": {"path": str(dest_db)},
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config))
    write_curation(
        tmp_path,
        [make_curation_entry("src", "erp.employees", "employees")],
    )

    _write_sql(
        tmp_path,
        "silver",
        "emp_clean",
        "SELECT id, name AS employee_name FROM bronze.src_employees",
    )
    # gold: only -- materialized: true, NO -- depends_on:.
    # The dep must be inferred from `FROM silver.emp_clean`.
    _write_sql(
        tmp_path,
        "gold",
        "emp_snapshot",
        (
            "-- materialized: true\n"
            "SELECT * FROM silver.emp_clean"
        ),
    )

    cfg = load_config(config_file)
    results = run_all(cfg, config_file)

    assert any(
        r.status == "success" and r.schema == "gold" and r.name == "emp_snapshot"
        for r in results
    ), f"gold.emp_snapshot did not succeed: {results}"

    con = duckdb.connect(str(dest_db))
    gold_rows = con.execute("SELECT * FROM gold.emp_snapshot").fetchall()
    assert len(gold_rows) == 2
    con.close()
```

- [ ] **Step 3: Strip `-- depends_on:` from `tests/integration/test_mode.py`**

Locate the silver fixture (around line 74) and the gold fixture (around line 80). The current fixtures are:

```python
    (silver_dir / "customers_clean.sql").write_text(
        "-- depends_on: bronze.erp_customers\n"
        "SELECT customer_id, name AS customer_name, city\n"
        ...
    )
    (gold_dir / "customer_summary.sql").write_text(
        "-- depends_on: silver.customers_clean\n"
        "-- materialized: true\n"
        ...
    )
```

Remove the `-- depends_on:` lines. The silver fixture loses its bronze-header (irrelevant under the new contract). The gold fixture keeps `-- materialized: true` and its body's `FROM silver.customers_clean` clause.

- [ ] **Step 4: Strip `-- depends_on:` from `tests/integration/test_setup.py`**

The fixture helper around line 68 currently builds:

```python
    header = "-- depends_on: silver.orders_clean\n"
    if materialized:
        header += "-- materialized: true\n"
```

Replace with:

```python
    header = "-- materialized: true\n" if materialized else ""
```

Inspect the rest of the helper to confirm the resulting gold SQL body still contains a `FROM silver.orders_clean` (or equivalent) clause. If the test was relying on the header alone to produce the edge, add the missing `FROM` to the body to preserve test intent.

- [ ] **Step 5: Strip `-- depends_on:` from `tests/e2e/test_07_transforms.py`**

Two occurrences around lines 96 and 145. Both look like:

```python
        "emp_summary",
        "-- depends_on: silver.emp_clean\nSELECT COUNT(*) AS n FROM silver.emp_clean",
```

Drop the `-- depends_on:` prefix:

```python
        "emp_summary",
        "SELECT COUNT(*) AS n FROM silver.emp_clean",
```

For the second occurrence (which also has `-- materialized: true`):

```python
        "emp_summary",
        "-- depends_on: silver.emp_clean\n"
        "-- materialized: true\n"
        ...
```

Drop only the `-- depends_on:` line:

```python
        "emp_summary",
        "-- materialized: true\n"
        ...
```

- [ ] **Step 6: Strip `-- depends_on:` from `tests/integration/test_transform_command.py` (Wave E)**

PR #56 added 19 integration tests for the `feather transform` CLI. Several of them author silver SQL with `-- depends_on: bronze.<table>` headers and assert on the advisory bronze-check warning output (`test_advisory_warning_single_missing_bronze`, `test_zero_warnings_when_bronze_present`, `test_collapse_rule_more_than_five_missing`, and `test_happy_path_creates_transforms_and_records_runs` among others).

Run:
```bash
git grep -n "depends_on:" tests/integration/test_transform_command.py
```
For every hit:
- If the line is a `_write_sql(...)` / `.write_text(...)` fixture body containing `-- depends_on: bronze.<table>`, delete that one line. The fixture's silver SQL must contain a `FROM bronze.<table>` clause — if it doesn't, add one (the test was likely relying on the header alone, which is no longer the contract).
- If the line is a `-- depends_on: silver.<name>` header on a gold transform, delete it. The gold body's `FROM silver.<name>` clause covers the topo edge.
- If the line is an assertion or Python comment that mentions `-- depends_on:` as the operator-facing contract (e.g. inside a docstring), reword to refer to "the silver SQL body's `FROM bronze.X` clause" instead.

The advisory-warning tests (`test_advisory_warning_single_missing_bronze`, `test_zero_warnings_when_bronze_present`, `test_collapse_rule_more_than_five_missing`) are the most sensitive — confirm that each one's silver SQL still actually references the bronze table the test expects to flag as missing. Without the `FROM bronze.X`, the new `check_bronze_dependencies` finds no bronze refs and emits zero warnings, which would silently break the test's assertion that one (or six) warning(s) appear on stderr.

Run:
```bash
uv run pytest tests/integration/test_transform_command.py -q
```
Expected: all 19 tests pass.

- [ ] **Step 7: Run the entire integration + e2e suite**

Run:
```bash
uv run pytest tests/integration/ tests/e2e/ -q
```
Expected: all tests pass. If any test fails because a fixture had `-- depends_on:` *but no equivalent `FROM` clause*, the original test was relying on the header path that's now deleted — add the appropriate `FROM` clause to the SQL body to restore the intent.

- [ ] **Step 8: Final grep — no operator-facing `-- depends_on:` should remain outside historical docs and the new "legacy header silently ignored" unit test**

Run:
```bash
git grep -n "depends_on:" -- \
  src/ tests/ README.md docs/prd.md docs/testing-feather-etl.md docs/architecture/
```
Expected: hits only in:
- `tests/unit/test_transforms.py::test_parse_legacy_depends_on_header_is_silently_ignored` (intentional — pins the silent-ignore behaviour).
- Nothing else.

If anything else remains, fix it.

- [ ] **Step 9: Commit**

```bash
git add tests/integration/test_transforms.py tests/integration/test_transform_command.py tests/integration/test_mode.py tests/integration/test_setup.py tests/e2e/test_07_transforms.py
git commit -m "test: drop -- depends_on: headers from integration & e2e fixtures (#54)"
```

---

## Task 9: Migration docs — issue note + warehouse-layers update + issues README

**Files:**
- Create: `docs/issues/54-auto-discover-dag.md`
- Modify: `docs/issues/README.md`
- Modify: `docs/architecture/warehouse-layers.md`

- [ ] **Step 1: Pin the issue context**

Create `docs/issues/54-auto-discover-dag.md` with this content:

```markdown
# Issue #54 — Auto-discover transform DAG from SQL refs

**GitHub:** https://github.com/siraj-samsudeen/feather-etl/issues/54
**Status:** implemented (see `docs/superpowers/plans/2026-05-11-auto-discover-transform-dag.md`)

## Why this exists

Before this change, the transform DAG was built from `-- depends_on:` header
comments that paraphrased the `FROM`/`JOIN` clauses one line below. Two sources
of truth, manually kept in sync. Silent drift was possible whenever an operator
added a `JOIN` and forgot to update the header.

## What changed

`parse_transform_file` now derives `TransformMeta.depends_on` **solely** from
the SQL body via `feather_etl.transform_deps.extract_dependencies`. It parses
the SQL with `sqlglot`, walks every `exp.Table` node, and adds an edge for
every `silver.*` / `gold.*` reference. CTE names, string literals, comments,
and table-valued functions like `read_csv(...)` are not `exp.Table` nodes in
sqlglot's AST, so they are not treated as edges — no phantom dependencies.

The `-- depends_on:` header is no longer part of the transform contract. The
parser branch that recognised it has been removed. Any legacy `.sql` file
that still contains the header is unaffected: the line falls through to the
SQL body and sqlglot strips it as a comment at parse time. There are zero
`.sql` transform files on disk in this repository at the time of the change.

`-- materialized: true` and `-- fact_table: <name>` headers retain their
original meaning — they encode information the SQL body cannot express.

## Why AST parsing, not regex; why not `{{ ref(...) }}`

See the issue body. The short version: a regex matches comments and string
literals; dbt's `ref()` exists primarily for environment-aware schema
resolution (which feather-etl doesn't need because its schemas are fixed).

## Failure modes

If sqlglot cannot parse a transform's SQL, `parse_transform_file` raises
`feather_etl.transform_deps.TransformDepParseError` with the file path. This
is intentional: a transform whose SQL is unparseable is a transform whose DAG
edges cannot be trusted, so we surface it at discover-time rather than at
execute-time. The same error is raised for a header-only file with no SELECT
body.

A transform that references a non-existent `silver.X` (whether via SQL body
or — historically — via header) still raises the same `ValueError` from
`build_execution_order` as before. The error message and the validation path
in `build_execution_order` are unchanged.

## Scope

In scope: `silver.*` and `gold.*` references (the transform schemas defined in
`docs/architecture/warehouse-layers.md`).

Out of scope: column-level lineage; full Jinja templating; `source()` macros
for bronze references; cross-project refs.

## No backward compatibility

The `-- depends_on:` syntax is removed without a deprecation period. This is
safe because:

- Zero `.sql` transform files exist on disk in the feather-etl repository
  itself at the time of the change.
- Legacy headers in any *consumer* repo silently no-op (treated as SQL
  comments). Operators may discover the deletion via a missing-dependency
  error from `build_execution_order` only if their SQL body fails to
  reference a table that was *previously* declared via header only — a
  pathological case (it would have meant the SQL body and the header were
  already inconsistent under the old contract).

If a future operator needs an explicit data-only dependency (a transform
whose data must exist before this one, but which this one doesn't `SELECT`
from), the recommended workaround is to add a no-op `LEFT JOIN
<dep> ON FALSE` clause to the SQL body — this keeps the contract simple and
the DAG fully recoverable from SQL alone. If the workaround feels ugly in
practice, re-introducing `-- depends_on:` as an explicit-override syntax is
a possible future change.
```

- [ ] **Step 2: Add the new doc to the issues README contents**

Edit `docs/issues/README.md`. After the `replace-hands-on-test.md` line in the Contents list, add:

```markdown
- [`54-auto-discover-dag.md`](54-auto-discover-dag.md) — Issue #54 (auto-discover transform DAG via sqlglot AST parsing). Captures the rationale for AST over regex over `{{ ref(...) }}`, the no-backcompat removal of `-- depends_on:`, and the parse-failure escalation policy. Cited by `docs/superpowers/plans/2026-05-11-auto-discover-transform-dag.md`.
```

- [ ] **Step 3: Update the warehouse-layers doc**

Open `docs/architecture/warehouse-layers.md`. Find the section that describes how Gold and Silver transforms declare dependencies (search for `depends_on`). Replace the operator-facing instruction so it reads:

```markdown
**Dependency edges are inferred from SQL.** feather-etl parses each
transform's `FROM`/`JOIN` clauses with `sqlglot` and builds the DAG from the
`silver.*` / `gold.*` references it finds. The SQL body is the only source
of truth for DAG edges — there is no `-- depends_on:` header. CTE names,
string literals, comments, and table-valued functions like `read_csv(...)`
are not treated as edges. See GitHub issue #54.
```

If the existing doc doesn't currently mention `-- depends_on:` (it may pre-date this feature), insert this paragraph after the `-- materialized: true` paragraph in the Gold section so the dependency mechanism is documented in the same place as materialization.

- [ ] **Step 4: Run the docs lint / full suite once more to make sure nothing is wired off the docs**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add docs/issues/54-auto-discover-dag.md docs/issues/README.md docs/architecture/warehouse-layers.md
git commit -m "docs(transforms): document auto-inferred DAG and -- depends_on override (#54)"
```

---

## Task 10: Backfill operator-facing docs, README, PRD, parser docstring, and PR #56's openspec change

Now that the loader infers edges solely from SQL and `-- depends_on:` is no longer part of the contract, the operator-facing docs that still teach the header mechanism are misleading. Update them so the SQL body is the **only** source of truth. Scope is operator-facing artifacts plus PR #56's openspec change docs (which codified the old `-- depends_on: bronze.<table>` convention in normative SHALL clauses) — historical plans under `docs/plans/` and `.planning/` are commit-time records and stay untouched.

**Files:**
- Modify: `src/feather_etl/transforms.py` — docstrings of `parse_transform_file` (already updated in Task 4) and `check_bronze_dependencies` (already updated in Task 7); only revisit if the docstrings still drift from the new contract.
- Modify: `README.md` — `Transform Dependencies` section (lines ~324–337) plus the two inline directory-tree comments (line ~226 and the `transforms/silver/sales_invoice.sql` example block around line ~298)
- Modify: `docs/prd.md` — FR5.5 (line ~624), AC-FR5.c (line ~647), and the `feather init` directory tree example (line ~1517)
- Modify: `docs/testing-feather-etl.md` — Area 10 SQL example (line ~423)
- Modify: `openspec/changes/feather-transform/proposal.md` — strike the `-- depends_on: bronze.*` operator convention; replace with "bronze refs in silver SQL `FROM`/`JOIN` clauses, derived via sqlglot."
- Modify: `openspec/changes/feather-transform/design.md` — lines ~60 (explicit reference to "issue #54") and ~93 ("honour-system today"); update to reflect that #54 has now landed and the bronze-check input is the SQL body, not the header.
- Modify: `openspec/changes/feather-transform/specs/transform-command/spec.md` — normative SHALL clause citing `-- depends_on: bronze.<table>`; rewrite to require that the helper derive bronze refs from the silver SQL body via sqlglot.
- Modify: `openspec/changes/feather-transform/tasks.md` — entries 6.1, 6.3, 7.9 reference the convention; rewrite or strike.
- Modify: `docs/plans/2026-05-11-feather-transform.md` — PR #56's plan doc describes the bronze advisory walking `-- depends_on: bronze.x`. Add a one-paragraph note at the top that issue #54 has since superseded the header mechanism; the helper now walks SQL.
- **Do not modify:** `.planning/codebase/ARCHITECTURE.md`, `docs/plans/2026-03-27-slice8-transforms.md`, `docs/plans/2026-03-29-remaining-slices.md` (historical artifacts predating this change).
- **Do not modify:** any file under `tests/` (their `-- depends_on:` references were already addressed in Tasks 4–8).

> Caution before editing the openspec docs: feather-etl uses OpenSpec to manage normative change-spec deltas; editing the merged change's `specs/` files post-merge is unusual. Either (a) edit in place with a clear "superseded by issue #54" note inside the changed section, or (b) create a new openspec delta `openspec/changes/issue-54-supersede-bronze-header/` that retracts the relevant SHALL clauses. **Default to option (a)** unless your local OpenSpec workflow demands the delta form.

- [ ] **Step 1: Confirm the `parse_transform_file` and `check_bronze_dependencies` docstrings are up to date**

The docstring rewrites happened inline with the code rewrites in Tasks 4 and 7. Re-read both functions and confirm they describe the SQL-as-sole-source-of-truth contract and that neither mentions `-- depends_on:` as an active mechanism. If either drifts, fix it now.

Run:
```bash
git grep -n "depends_on" src/feather_etl/transforms.py
```
Expected: zero hits, or hits only inside historical comments that explicitly call out "issue #54 removed this." If anything else, edit accordingly.

- [ ] **Step 2: Update the README — `Transform Dependencies` section**

Open `README.md`. The current section reads roughly:

```markdown
## Transform Dependencies

Declare dependencies with a SQL comment — the system builds the execution order automatically:

```sql
-- depends_on: silver.sales_invoice
-- depends_on: silver.customer_master
-- materialized: true
SELECT ...
FROM silver.sales_invoice si
JOIN silver.customer_master cm ON si.customer_code = cm.customer_code
```

Transform files contain only the SELECT body plus header comments. The system generates the DDL: silver transforms become VIEWs, gold transforms with `-- materialized: true` become TABLEs in prod mode (VIEWs in dev/test).
```

Replace it with:

```markdown
## Transform Dependencies

Dependencies are inferred from the SQL body. The system parses each transform with `sqlglot`, extracts every `silver.*` / `gold.*` reference from `FROM` / `JOIN` clauses, and builds the topological execution order automatically. No header markup declares edges:

```sql
-- materialized: true
SELECT ...
FROM silver.sales_invoice si
JOIN silver.customer_master cm ON si.customer_code = cm.customer_code
```

The above produces edges to `silver.sales_invoice` and `silver.customer_master`. CTEs, comments, string literals, and table-valued functions (e.g. `read_csv(...)`) do not create phantom edges.

Header comments retain meaning only for information the SQL body cannot express: `-- materialized: true` (gold materialisation in prod mode) and `-- fact_table: <name>` (join-health-check declaration). The `-- depends_on:` header is no longer recognised — see GitHub issue #54.

Transform files contain only the SELECT body plus optional `-- materialized:` / `-- fact_table:` headers. The system generates the DDL: silver transforms become VIEWs, gold transforms with `-- materialized: true` become TABLEs in prod mode (VIEWs in dev/test).
```

- [ ] **Step 3: Update the two inline directory-tree examples in README**

In `README.md`, find the directory tree near line 226:

```
│   ├── silver/                     # canonical mapping views
│   │   └── sales_invoice.sql       # -- depends_on: (none)
```

Change to:

```
│   ├── silver/                     # canonical mapping views
│   │   └── sales_invoice.sql       # deps inferred from FROM clauses
```

Then find the example silver SQL near line 298 that starts with `-- depends_on: (none — reads from bronze directly)`. Replace that single header line so the snippet becomes:

```sql
-- silver: reads from bronze; bronze refs don't create transform-layer edges
SELECT
    ID           AS invoice_id,
    SI_NO        AS invoice_no,
    Custome_Code AS customer_code,
    NetAmount    AS net_amount,
    ModifiedDate AS modified_date,
    _etl_loaded_at,
    _etl_run_id
FROM bronze.sales_invoice
```

(The body is unchanged; only the comment line at the top is rewritten.)

- [ ] **Step 4: Update the PRD — FR5.5**

Open `docs/prd.md`. Locate FR5.5 (around line 620). Current text:

```
# FR5.5
THE SYSTEM SHALL determine transform execution order via
graphlib.TopologicalSorter (stdlib) based on declared dependencies.
Dependencies are declared via SQL comment in the transform file:
-- depends_on: silver.sales_invoice
Multiple dependencies are declared on separate lines.
The system SHALL parse these comments at setup time to build the dependency graph.
IF a declared dependency does not resolve to a known transform
THE SYSTEM SHALL raise an error at setup time naming the missing dependency.
```

Replace with:

```
# FR5.5
THE SYSTEM SHALL determine transform execution order via
graphlib.TopologicalSorter (stdlib) based on the dependency graph derived
from each transform's SQL body. Dependencies are auto-inferred at setup
time by parsing the SQL with sqlglot and extracting every `silver.*` /
`gold.*` reference from `FROM` / `JOIN` clauses (issue #54). The `-- depends_on:`
header is not recognised — the SQL body is the only source of truth for
the DAG.
IF a referenced transform-layer dependency does not resolve to a known
transform
THE SYSTEM SHALL raise an error at setup time naming the missing dependency.
IF the SQL body cannot be parsed by sqlglot
THE SYSTEM SHALL raise a TransformDepParseError at setup time naming the
file path.
```

- [ ] **Step 5: Update PRD acceptance criterion AC-FR5.c**

In `docs/prd.md`, locate AC-FR5.c (around line 647). Replace:

```
**AC-FR5.c:** Given a gold transform with `-- depends_on: silver.sales_invoice` and a silver transform for `sales_invoice`, when `feather setup` runs, then the silver view is created before the gold transform — verified by gold containing correct data.
```

with:

```
**AC-FR5.c:** Given a gold transform whose SQL body contains `FROM silver.sales_invoice` and a silver transform for `sales_invoice`, when `feather setup` runs, then the silver view is created before the gold transform — verified by gold containing correct data. No `-- depends_on:` header is required or recognised; the edge is derived from the `FROM` clause alone.
```

- [ ] **Step 6: Update the PRD `feather init` directory tree**

In `docs/prd.md`, locate the directory tree near line 1515. Current:

```
│   ├── silver/                     # canonical mapping views
│   │   ├── sales_invoice.sql       # -- depends_on: (none)
│   │   └── customer_master.sql
```

Replace with:

```
│   ├── silver/                     # canonical mapping views
│   │   ├── sales_invoice.sql       # deps inferred from FROM clauses
│   │   └── customer_master.sql
```

- [ ] **Step 7: Update `docs/testing-feather-etl.md` — Area 10**

Open `docs/testing-feather-etl.md`. Find the Area 10 SQL example (around line 420):

```sql
-- depends_on: silver.customers
-- materialized: true
SELECT COUNT(*) AS n FROM silver.customers
```

Replace with:

```sql
-- materialized: true
SELECT COUNT(*) AS n FROM silver.customers
```

Then, in the bullet list immediately below the snippet, locate the line:

```
- Dependencies on `silver.*`/`gold.*` that don't exist among discovered transforms &rarr; error
```

Insert two new bullets immediately above it:

```
- Dependencies are inferred from the SQL body (`silver.*`/`gold.*` references in `FROM`/`JOIN`). The `-- depends_on:` header is not part of the contract — only the SQL body declares edges.
- CTE names, string literals, comments, and table-valued functions like `read_csv(...)` are not treated as dependency edges.
```

- [ ] **Step 8: Update PR #56's openspec change docs**

PR #56 codified `-- depends_on: bronze.<table>` as the operator convention in a normative SHALL clause + supporting design/proposal/tasks. Under issue #54, that convention is removed. Update each file with a clear "superseded by issue #54" framing:

For each of the following files:

- `openspec/changes/feather-transform/proposal.md`
- `openspec/changes/feather-transform/design.md`
- `openspec/changes/feather-transform/specs/transform-command/spec.md`
- `openspec/changes/feather-transform/tasks.md`

Open and scan for `-- depends_on:` or `depends_on: bronze`. For every occurrence:

1. In `design.md`, locate the line around 60 that says `"AST-based DAG inference (issue #54). The advisory bronze check uses the existing -- depends_on: comment convention"`. Replace with: `"AST-based DAG inference (issue #54) — landed; the advisory bronze check now derives bronze refs from the silver SQL body via feather_etl.transform_deps.extract_bronze_dependencies, completing the convergence."`
2. In `design.md`, locate the line around 93 that calls the bronze convention "honour-system today". Update to: `"Bronze refs are sourced from the silver SQL FROM/JOIN clauses via sqlglot — the same source of truth as the silver→gold DAG edges. Issue #54 removed the honour-system header convention in favour of this."`
3. In `proposal.md`, strike or rewrite any "operator declares `-- depends_on: bronze.X`" framing to refer to "operator's silver SQL `FROM`/`JOIN` clauses referencing `bronze.X`."
4. In `specs/transform-command/spec.md`, locate the SHALL clause that requires the helper to consume `-- depends_on: bronze.<table>` declarations. Rewrite the requirement to: `"THE SYSTEM SHALL emit an advisory warning to stderr for each bronze table that is referenced (via FROM or JOIN) by any silver transform but does not exist in the destination's bronze schema. The bronze references SHALL be derived from each silver transform's SQL body via sqlglot AST walk; the -- depends_on: header convention is not honoured (see issue #54)."`. Update any nearby `THEN` clauses citing the header form.
5. In `tasks.md`, find entries 6.1, 6.3, and 7.9. Update each to mention the SQL-sourced bronze refs.

Also open `docs/plans/2026-05-11-feather-transform.md` (the PR #56 implementation plan). At the very top, immediately after the `# ...` title, add a one-paragraph banner:

```markdown
> **Superseded note (issue #54):** when this plan was written, the
> advisory bronze-existence check consumed `-- depends_on: bronze.<table>`
> headers from each silver transform. Issue #54 has since removed the
> header convention entirely; the check now derives bronze refs from
> the silver SQL body via `extract_bronze_dependencies`. The plan steps
> below are preserved for historical context — the implementation in
> `transforms.py` differs at the points where the body says "read
> `t.depends_on`."
```

- [ ] **Step 9: Run the full suite to confirm the doc edits didn't break anything**

Run: `uv run pytest -q`
Expected: all tests pass — same count as the end of Task 8. No test should assert against the contents of the docs we edited.

- [ ] **Step 10: Sanity-check that no operator-facing doc still teaches `-- depends_on:` as part of the contract**

Run:
```bash
git grep -n "depends_on" README.md docs/prd.md docs/testing-feather-etl.md docs/architecture/warehouse-layers.md openspec/changes/feather-transform/
```
Expected: zero hits, OR hits only inside an explicit "superseded by issue #54" framing. (`-- depends_on:` is also mentioned inside `docs/issues/54-auto-discover-dag.md` for historical context, and inside the unit test `test_parse_legacy_depends_on_header_is_silently_ignored`.) If anything else remains, fix it before committing.

- [ ] **Step 11: Commit**

```bash
git add src/feather_etl/transforms.py README.md docs/prd.md docs/testing-feather-etl.md \
        openspec/changes/feather-transform/proposal.md \
        openspec/changes/feather-transform/design.md \
        openspec/changes/feather-transform/specs/transform-command/spec.md \
        openspec/changes/feather-transform/tasks.md \
        docs/plans/2026-05-11-feather-transform.md
git commit -m "docs: rewrite operator-facing dep docs around SQL-inferred DAG (#54)

Includes openspec change-spec rewrites for PR #56 (feather-transform) —
the SHALL clause that required -- depends_on: bronze.<table> as the
advisory-check input is replaced with the new SQL-body-via-sqlglot
contract. Plan doc 2026-05-11-feather-transform.md gets a superseded
banner."
```

---

## Task 11: Full-suite sanity check + final commit

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite and confirm green**

Run: `uv run pytest -q`
Expected: all tests pass. Net new test count vs. before this branch: roughly **+16** (1 in Task 2, 11 in Task 3, +4 in Task 4 [auto-inferred + 3 rewrites of deleted/renamed legacy tests + 1 new legacy-header-ignored], +4 in Task 5 [CTE / string / unparseable / empty body], +2 in Task 6 [auto-orders + missing-inferred], +1 in Task 7 [integration]) — and **−1** for the deleted `test_parse_with_depends_on`. The arithmetic is approximate; what matters is the suite is **green**.

- [ ] **Step 2: Manual end-to-end smoke against the reference-implementation example from the issue**

Run a quick interactive check using `uv run python`:

```bash
uv run python - <<'PY'
from feather_etl.transform_deps import extract_dependencies

sql_from_issue = """
-- materialized: true
SELECT
    store_name,
    SUM(daily_cost) AS total_cost
FROM silver.storewise_hrcost
GROUP BY store_name
"""

print(extract_dependencies(sql_from_issue))
PY
```

Expected output: `['silver.storewise_hrcost']`

- [ ] **Step 3: Confirm the worktree is clean and ready for PR**

Run: `git status`
Expected: working tree clean (all task commits already in place).

Run: `git log --oneline main..HEAD`
Expected: roughly 10 commits, one per task that produced commits (Task 1 through Task 10).

---

## Self-Review Notes

- **Spec coverage** — Each acceptance criterion from the issue body maps to a test:
  - "FROM silver.foo with no header → topo-sorted after silver.foo" → Task 6 `test_auto_inferred_dep_orders_correctly_via_parse` + Task 8 `test_run_auto_infers_silver_to_gold_dep`.
  - "JOIN silver.foo + `-- depends_on: silver.foo` → dedup" → **n/a** under the no-backcompat decision; the `-- depends_on:` header is no longer recognised. The acceptance criterion is satisfied by demonstrating the same dedupe semantics for inferred-only references (Task 3 `test_duplicate_references_deduped`).
  - "CTE shadow → no phantom dep" → Task 3 `test_cte_does_not_shadow_silver` + Task 5 `test_parse_cte_shadow_no_phantom_dep`.
  - "string-literal `'FROM silver.boom'` → no phantom dep" → Task 3 + Task 5.
  - "missing silver.bar → same error as today" → Task 6 `test_missing_inferred_dep_raises_same_error_as_before`. The existing `test_missing_dependency_raises` (in-memory `TransformMeta`) also continues to pass.
  - **Plus** a non-issue-body contract: empty SQL body raises at discover-time → Task 5 `test_parse_raises_on_empty_sql_body`.
  - **Plus** silent-ignore of legacy `-- depends_on:` headers → Task 4 `test_parse_legacy_depends_on_header_is_silently_ignored`.
  - **Plus** bronze-check pivot (necessitated by PR #56's merge): bronze refs sourced from SQL body, not headers → Task 3's 9 `extract_bronze_dependencies` cases + Task 7's rewrite of the 7 existing `test_bronze_check.py` tests.
- **Breaking-change scope** — `-- depends_on:` is removed from the parser contract with no deprecation period. Justified because (a) zero `.sql` transform files exist on disk in this repository today, (b) legacy headers in consumer repos silently no-op, and (c) PR #56's `check_bronze_dependencies` is rewritten in-place to consume the SQL body (Task 7) — public signature unchanged. The commits in Tasks 4 and 7 are both marked `feat(transforms)!:` to flag the breaks.
- **Merge interaction with PR #56** — local main was confirmed at `e05e17c` (origin/main, post-#56). The plan is sized against the post-merge codebase, which now includes `feather transform` CLI (Wave E) and `check_bronze_dependencies`. Both are accounted for in Tasks 7, 8, and 10.
- **No placeholders** — every step has runnable commands or full code blocks.
- **Type consistency** — `extract_dependencies`, `extract_bronze_dependencies`, and `TransformDepParseError` are defined in Task 2 and referenced unchanged in Tasks 4, 7, and beyond. `TransformMeta` is read-only here.
- **TDD ordering** — every behaviour change is added test-first (Task 2 fails → Task 2 passes; Task 4 auto-inference test fails → loader rewrite makes it pass; Task 7's rewritten bronze-check tests fail before the helper rewrite → pass after; integration regressions in Task 8 are appended only after the loader and helper rewrites are in place).
