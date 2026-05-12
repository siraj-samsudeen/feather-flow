"""Tests for the silver/gold transform engine (Slice 8)."""

from __future__ import annotations

import graphlib
from pathlib import Path

import duckdb
import pytest

from feather_etl.transforms import (
    TransformMeta,
    build_execution_order,
    discover_transforms,
    execute_transforms,
    parse_transform_file,
    rebuild_materialized_gold,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_sql(base: Path, schema: str, name: str, content: str) -> Path:
    """Write a .sql file under base/transforms/{schema}/{name}.sql."""
    d = base / "transforms" / schema
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.sql"
    p.write_text(content)
    return p


def _make_bronze_tables(con: duckdb.DuckDBPyConnection) -> None:
    """Create sample bronze tables for transform tests."""
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")

    con.execute("""
        CREATE TABLE bronze.employees (
            id INTEGER,
            employee_code VARCHAR,
            first_name VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO bronze.employees VALUES
        (1, 'E001', 'Alice'),
        (2, 'E002', 'Bob'),
        (3, 'E003', 'Charlie')
    """)

    con.execute("""
        CREATE TABLE bronze.items (
            id INTEGER,
            item_code VARCHAR,
            item_name VARCHAR,
            category VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO bronze.items VALUES
        (1, 'I001', 'Widget', 'Hardware'),
        (2, 'I002', 'Gadget', 'Electronics')
    """)

    con.execute("""
        CREATE TABLE bronze.sales (
            id INTEGER,
            employee_code VARCHAR,
            item_code VARCHAR,
            amount DECIMAL(10,2)
        )
    """)
    con.execute("""
        INSERT INTO bronze.sales VALUES
        (1, 'E001', 'I001', 100.00),
        (2, 'E002', 'I002', 250.50),
        (3, 'E001', 'I002', 75.00)
    """)


# ===========================================================================
# Task 1: SQL file parser
# ===========================================================================


class TestParseTransformFile:
    def test_parse_silver_no_metadata(self, tmp_path: Path):
        p = _write_sql(
            tmp_path, "silver", "employees", "SELECT * FROM bronze.employees"
        )
        meta = parse_transform_file(p)

        assert meta.name == "employees"
        assert meta.schema == "silver"
        assert meta.sql == "SELECT * FROM bronze.employees"
        assert meta.depends_on == []
        assert meta.materialized is False
        assert meta.path == p
        assert meta.qualified_name == "silver.employees"

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

    def test_parse_gold_non_materialized(self, tmp_path: Path):
        content = "SELECT count(*) AS total FROM silver.employees"
        p = _write_sql(tmp_path, "gold", "emp_count", content)
        meta = parse_transform_file(p)

        assert meta.schema == "gold"
        assert meta.materialized is False

    def test_parse_preserves_non_metadata_comments(self, tmp_path: Path):
        content = (
            "-- This is a regular comment\n"
            "SELECT amount FROM bronze.sales"
        )
        p = _write_sql(tmp_path, "silver", "sales_clean", content)
        meta = parse_transform_file(p)

        assert "-- This is a regular comment" in meta.sql
        assert meta.depends_on == []  # bronze ref produces no transform-layer edge

    def test_parse_strips_leading_trailing_blank_lines(self, tmp_path: Path):
        content = "\n\n-- materialized: true\n\nSELECT 1\n\n"
        p = _write_sql(tmp_path, "gold", "stripped", content)
        meta = parse_transform_file(p)

        assert meta.sql == "SELECT 1"
        assert meta.materialized is True

    def test_parse_invalid_schema_directory(self, tmp_path: Path):
        d = tmp_path / "transforms" / "platinum"
        d.mkdir(parents=True)
        p = d / "test.sql"
        p.write_text("SELECT 1")

        with pytest.raises(ValueError, match="platinum"):
            parse_transform_file(p)

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


# ===========================================================================
# Task 2: Discovery + dependency graph
# ===========================================================================


class TestDiscoverTransforms:
    def test_discover_finds_silver_and_gold(self, tmp_path: Path):
        _write_sql(tmp_path, "silver", "a", "SELECT 1")
        _write_sql(tmp_path, "silver", "b", "SELECT 2")
        _write_sql(tmp_path, "gold", "c", "SELECT 3")

        transforms = discover_transforms(tmp_path)
        names = [t.qualified_name for t in transforms]

        assert len(transforms) == 3
        assert "silver.a" in names
        assert "silver.b" in names
        assert "gold.c" in names

    def test_discover_returns_silver_before_gold(self, tmp_path: Path):
        _write_sql(tmp_path, "gold", "x", "SELECT 1")
        _write_sql(tmp_path, "silver", "y", "SELECT 2")

        transforms = discover_transforms(tmp_path)
        schemas = [t.schema for t in transforms]

        assert schemas == ["silver", "gold"]

    def test_discover_no_transforms_dir(self, tmp_path: Path):
        result = discover_transforms(tmp_path)
        assert result == []

    def test_discover_empty_transforms_dir(self, tmp_path: Path):
        (tmp_path / "transforms").mkdir()
        result = discover_transforms(tmp_path)
        assert result == []

    def test_discover_ignores_non_sql_files(self, tmp_path: Path):
        _write_sql(tmp_path, "silver", "valid", "SELECT 1")
        (tmp_path / "transforms" / "silver" / "notes.txt").write_text("ignore me")

        transforms = discover_transforms(tmp_path)
        assert len(transforms) == 1
        assert transforms[0].name == "valid"

    def test_discover_sorted_alphabetically(self, tmp_path: Path):
        _write_sql(tmp_path, "silver", "zebra", "SELECT 1")
        _write_sql(tmp_path, "silver", "alpha", "SELECT 2")

        transforms = discover_transforms(tmp_path)
        names = [t.name for t in transforms]
        assert names == ["alpha", "zebra"]


class TestBuildExecutionOrder:
    def test_simple_chain(self):
        silver = TransformMeta(name="a", schema="silver", sql="SELECT 1")
        gold = TransformMeta(
            name="b",
            schema="gold",
            sql="SELECT * FROM silver.a",
            depends_on=["silver.a"],
        )

        ordered = build_execution_order([gold, silver])
        names = [t.qualified_name for t in ordered]

        assert names.index("silver.a") < names.index("gold.b")

    def test_no_dependencies(self):
        a = TransformMeta(name="a", schema="silver", sql="SELECT 1")
        b = TransformMeta(name="b", schema="silver", sql="SELECT 2")

        ordered = build_execution_order([a, b])
        assert len(ordered) == 2

    def test_diamond_dependency(self):
        a = TransformMeta(name="a", schema="silver", sql="SELECT 1")
        b = TransformMeta(name="b", schema="silver", sql="S", depends_on=["silver.a"])
        c = TransformMeta(name="c", schema="silver", sql="S", depends_on=["silver.a"])
        d = TransformMeta(
            name="d",
            schema="gold",
            sql="S",
            depends_on=["silver.b", "silver.c"],
        )

        ordered = build_execution_order([d, c, b, a])
        names = [t.qualified_name for t in ordered]

        assert names.index("silver.a") < names.index("silver.b")
        assert names.index("silver.a") < names.index("silver.c")
        assert names.index("silver.b") < names.index("gold.d")
        assert names.index("silver.c") < names.index("gold.d")

    def test_missing_dependency_raises(self):
        t = TransformMeta(
            name="bad",
            schema="gold",
            sql="SELECT 1",
            depends_on=["silver.nonexistent"],
        )

        with pytest.raises(ValueError, match="nonexistent"):
            build_execution_order([t])

    def test_cycle_raises(self):
        a = TransformMeta(name="a", schema="silver", sql="S", depends_on=["silver.b"])
        b = TransformMeta(name="b", schema="silver", sql="S", depends_on=["silver.a"])

        with pytest.raises(graphlib.CycleError):
            build_execution_order([a, b])


# ===========================================================================
# Task 3: Transform execution engine
# ===========================================================================


class TestExecuteTransforms:
    def test_silver_creates_view(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)

        t = TransformMeta(
            name="employee_clean",
            schema="silver",
            sql="SELECT id, employee_code, first_name FROM bronze.employees",
        )

        results = execute_transforms(con, [t])
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].type == "view"
        assert results[0].schema == "silver"

        rows = con.execute("SELECT * FROM silver.employee_clean").fetchall()
        assert len(rows) == 3
        con.close()

    def test_gold_non_materialized_creates_view(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.emp AS SELECT * FROM bronze.employees")

        t = TransformMeta(
            name="emp_count",
            schema="gold",
            sql="SELECT count(*) AS total FROM silver.emp",
        )

        results = execute_transforms(con, [t])
        assert results[0].status == "success"
        assert results[0].type == "view"

        row = con.execute("SELECT * FROM gold.emp_count").fetchone()
        assert row[0] == 3
        con.close()

    def test_gold_materialized_creates_table(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.emp AS SELECT * FROM bronze.employees")

        t = TransformMeta(
            name="emp_snapshot",
            schema="gold",
            sql="SELECT * FROM silver.emp",
            materialized=True,
        )

        results = execute_transforms(con, [t])
        assert results[0].status == "success"
        assert results[0].type == "table"

        info = con.execute(
            "SELECT table_type FROM information_schema.tables "
            "WHERE table_schema='gold' AND table_name='emp_snapshot'"
        ).fetchone()
        assert info[0] == "BASE TABLE"
        con.close()

    def test_error_continues_to_next(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        con.execute("CREATE SCHEMA IF NOT EXISTS silver")
        con.execute("CREATE SCHEMA IF NOT EXISTS gold")

        bad = TransformMeta(
            name="broken",
            schema="silver",
            sql="SELECT * FROM nonexistent_table",
        )
        good = TransformMeta(
            name="ok",
            schema="silver",
            sql="SELECT 1 AS val",
        )

        results = execute_transforms(con, [bad, good])
        assert results[0].status == "error"
        assert results[0].error is not None
        assert results[1].status == "success"
        con.close()

    def test_template_variable_substitution(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)

        t = TransformMeta(
            name="filtered",
            schema="silver",
            sql="SELECT * FROM bronze.employees WHERE first_name = '${name}'",
        )

        results = execute_transforms(con, [t], variables={"name": "Alice"})
        assert results[0].status == "success"

        rows = con.execute("SELECT * FROM silver.filtered").fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "Alice"
        con.close()

    def test_safe_substitute_leaves_unknown_vars(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)

        t = TransformMeta(
            name="with_unknown",
            schema="silver",
            sql="SELECT '${unknown}' AS val, * FROM bronze.employees",
        )

        results = execute_transforms(con, [t], variables={})
        assert results[0].status == "success"
        con.close()


class TestRebuildMaterializedGold:
    def test_rebuilds_only_materialized_gold(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.emp AS SELECT * FROM bronze.employees")

        mat = TransformMeta(
            name="emp_snap",
            schema="gold",
            sql="SELECT * FROM silver.emp",
            materialized=True,
        )
        view = TransformMeta(
            name="emp_view",
            schema="gold",
            sql="SELECT count(*) FROM silver.emp",
            materialized=False,
        )
        silver = TransformMeta(
            name="emp",
            schema="silver",
            sql="SELECT * FROM bronze.employees",
        )

        results = rebuild_materialized_gold(con, [mat, view, silver])
        assert len(results) == 1
        assert results[0].name == "emp_snap"
        assert results[0].type == "table"
        con.close()

    def test_rebuild_reflects_new_data(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.emp AS SELECT * FROM bronze.employees")

        mat = TransformMeta(
            name="emp_snap",
            schema="gold",
            sql="SELECT * FROM silver.emp",
            materialized=True,
        )

        execute_transforms(con, [mat])
        count1 = con.execute("SELECT count(*) FROM gold.emp_snap").fetchone()[0]
        assert count1 == 3

        con.execute("INSERT INTO bronze.employees VALUES (4, 'E004', 'Diana')")

        rebuild_materialized_gold(con, [mat])
        count2 = con.execute("SELECT count(*) FROM gold.emp_snap").fetchone()[0]
        assert count2 == 4
        con.close()

    def test_rebuild_returns_empty_when_no_materialized(self, tmp_path: Path):
        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        t = TransformMeta(name="v", schema="gold", sql="SELECT 1")
        results = rebuild_materialized_gold(con, [t])
        assert results == []

    def test_rebuild_emits_warning_when_join_health_fails(self, tmp_path: Path, caplog):
        """rebuild_materialized_gold runs check_join_health on each rebuilt
        gold; a non-None warning is passed to logger.warning.
        (transforms.py:272)"""
        import logging

        con = duckdb.connect(str(tmp_path / "test.duckdb"))
        _make_bronze_tables(con)
        # Silver fact: 3 rows
        con.execute("CREATE VIEW silver.emp_fact AS SELECT * FROM bronze.employees")

        # Gold transform that duplicates the fact (inflation) + declares the
        # fact table so check_join_health has something to compare against.
        mat = TransformMeta(
            name="emp_inflated",
            schema="gold",
            sql="SELECT * FROM silver.emp_fact UNION ALL SELECT * FROM silver.emp_fact",
            materialized=True,
            fact_table="silver.emp_fact",
        )

        with caplog.at_level(logging.WARNING, logger="feather_etl.transforms"):
            results = rebuild_materialized_gold(con, [mat])

        assert len(results) == 1
        assert results[0].status == "success"
        # The health warning is emitted via logger.warning
        assert "JOIN INFLATION" in caplog.text
        assert "emp_inflated" in caplog.text
        con.close()


class TestParseTransformFileInvalidSchema:
    def test_invalid_schema_directory_raises(self, tmp_path: Path):
        """Files placed outside ``transforms/silver`` or ``transforms/gold``
        are rejected — the parser refuses to build a TransformMeta for a
        bronze or unknown schema. (transforms.py:81)"""
        from feather_etl.transforms import parse_transform_file

        bad_dir = tmp_path / "transforms" / "bronze"
        bad_dir.mkdir(parents=True)
        path = bad_dir / "oops.sql"
        path.write_text("SELECT 1")

        with pytest.raises(ValueError, match="expected one of"):
            parse_transform_file(path)

    def test_fact_table_comment_is_recorded(self, tmp_path: Path):
        """``-- fact_table: <name>`` header is parsed into ``fact_table``
        while being stripped from the SQL body. (transforms.py:70)"""
        from feather_etl.transforms import parse_transform_file

        sql_dir = tmp_path / "transforms" / "gold"
        sql_dir.mkdir(parents=True)
        path = sql_dir / "joined.sql"
        path.write_text(
            "-- depends_on: silver.fact_x\n"
            "-- fact_table: silver.fact_x\n"
            "-- materialized: true\n"
            "SELECT * FROM silver.fact_x\n"
        )

        meta = parse_transform_file(path)
        assert meta.fact_table == "silver.fact_x"
        assert meta.materialized is True
        assert meta.depends_on == ["silver.fact_x"]
        # The comment lines are stripped from the SQL
        assert "fact_table" not in meta.sql
        assert "SELECT * FROM silver.fact_x" in meta.sql


class TestCheckJoinHealth:
    def test_returns_none_when_fact_table_not_declared(self, tmp_path: Path):
        """No ``-- fact_table:`` header → check_join_health is a no-op."""
        from feather_etl.transforms import check_join_health

        con = duckdb.connect(str(tmp_path / "h.duckdb"))
        t = TransformMeta(name="x", schema="gold", sql="SELECT 1")
        assert check_join_health(con, t) is None
        con.close()

    def test_detects_join_inflation(self, tmp_path: Path):
        """Gold has MORE rows than its declared fact table → JOIN INFLATION."""
        from feather_etl.transforms import check_join_health

        con = duckdb.connect(str(tmp_path / "h.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.fact AS SELECT * FROM bronze.employees")
        # Gold is a UNION ALL with itself → doubled
        con.execute(
            "CREATE TABLE gold.doubled AS "
            "SELECT * FROM silver.fact UNION ALL SELECT * FROM silver.fact"
        )

        t = TransformMeta(
            name="doubled",
            schema="gold",
            sql="...",
            fact_table="silver.fact",
        )
        warning = check_join_health(con, t)
        assert warning is not None
        assert "JOIN INFLATION" in warning
        assert "gold.doubled" in warning
        assert "silver.fact" in warning
        con.close()

    def test_detects_join_loss(self, tmp_path: Path):
        """Gold has FEWER rows than its declared fact table → JOIN LOSS."""
        from feather_etl.transforms import check_join_health

        con = duckdb.connect(str(tmp_path / "h.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.fact AS SELECT * FROM bronze.employees")
        # Gold is a filter that drops some rows
        con.execute(
            "CREATE TABLE gold.filtered AS SELECT * FROM silver.fact WHERE id <= 1"
        )

        t = TransformMeta(
            name="filtered",
            schema="gold",
            sql="...",
            fact_table="silver.fact",
        )
        warning = check_join_health(con, t)
        assert warning is not None
        assert "JOIN LOSS" in warning
        assert "gold.filtered" in warning
        con.close()

    def test_returns_none_when_counts_match(self, tmp_path: Path):
        from feather_etl.transforms import check_join_health

        con = duckdb.connect(str(tmp_path / "h.duckdb"))
        _make_bronze_tables(con)
        con.execute("CREATE VIEW silver.fact AS SELECT * FROM bronze.employees")
        con.execute("CREATE TABLE gold.matched AS SELECT * FROM silver.fact")

        t = TransformMeta(
            name="matched",
            schema="gold",
            sql="...",
            fact_table="silver.fact",
        )
        assert check_join_health(con, t) is None
        con.close()

    def test_query_failure_produces_diagnostic_message(self, tmp_path: Path):
        """If one of the COUNT queries raises (e.g. the declared fact table
        doesn't exist), check_join_health returns an explanatory string
        rather than propagating the exception."""
        from feather_etl.transforms import check_join_health

        con = duckdb.connect(str(tmp_path / "h.duckdb"))
        con.execute("CREATE SCHEMA gold")
        con.execute("CREATE TABLE gold.g AS SELECT 1 AS x")

        t = TransformMeta(
            name="g",
            schema="gold",
            sql="...",
            fact_table="silver.definitely_not_here",
        )
        warning = check_join_health(con, t)
        assert warning is not None
        assert "Join health check failed" in warning
        assert "gold.g" in warning
        con.close()
        con.close()


# ===========================================================================
# Task 6: E2E test with realistic transforms
# ===========================================================================


class TestE2ETransformPipeline:
    """End-to-end: bronze data -> discover -> order -> execute -> verify."""

    def test_full_pipeline_with_fixtures(self, tmp_path: Path):
        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        _make_bronze_tables(con)

        _write_sql(
            tmp_path,
            "silver",
            "employee_master",
            (
                "-- depends_on: bronze.employees\n"
                "SELECT\n"
                "    id AS employee_id,\n"
                "    employee_code,\n"
                "    first_name AS employee_name\n"
                "FROM bronze.employees"
            ),
        )
        _write_sql(
            tmp_path,
            "silver",
            "item_master",
            (
                "-- depends_on: bronze.items\n"
                "SELECT\n"
                "    id AS item_id,\n"
                "    item_code,\n"
                "    item_name,\n"
                "    category\n"
                "FROM bronze.items"
            ),
        )
        _write_sql(
            tmp_path,
            "gold",
            "sales_summary",
            (
                "-- depends_on: silver.employee_master\n"
                "-- depends_on: silver.item_master\n"
                "-- materialized: true\n"
                "SELECT\n"
                "    s.id AS sale_id,\n"
                "    e.employee_name,\n"
                "    i.item_name,\n"
                "    i.category,\n"
                "    s.amount\n"
                "FROM bronze.sales s\n"
                "LEFT JOIN silver.employee_master e ON s.employee_code = e.employee_code\n"
                "LEFT JOIN silver.item_master i ON s.item_code = i.item_code"
            ),
        )

        transforms = discover_transforms(tmp_path)
        assert len(transforms) == 3

        ordered = build_execution_order(transforms)
        names = [t.qualified_name for t in ordered]

        assert names.index("silver.employee_master") < names.index("gold.sales_summary")
        assert names.index("silver.item_master") < names.index("gold.sales_summary")

        results = execute_transforms(con, ordered)
        assert all(r.status == "success" for r in results)

        emp_rows = con.execute("SELECT * FROM silver.employee_master").fetchall()
        assert len(emp_rows) == 3
        assert emp_rows[0][2] == "Alice"

        item_rows = con.execute("SELECT * FROM silver.item_master").fetchall()
        assert len(item_rows) == 2

        gold_rows = con.execute(
            "SELECT * FROM gold.sales_summary ORDER BY sale_id"
        ).fetchall()
        assert len(gold_rows) == 3
        assert gold_rows[0][1] == "Alice"
        assert gold_rows[0][2] == "Widget"

        ttype = con.execute(
            "SELECT table_type FROM information_schema.tables "
            "WHERE table_schema='gold' AND table_name='sales_summary'"
        ).fetchone()
        assert ttype[0] == "BASE TABLE"

        con.execute("INSERT INTO bronze.sales VALUES (4, 'E003', 'I001', 500.00)")
        rebuild_results = rebuild_materialized_gold(con, ordered)
        assert len(rebuild_results) == 1
        assert rebuild_results[0].status == "success"

        gold_after = con.execute("SELECT count(*) FROM gold.sales_summary").fetchone()
        assert gold_after[0] == 4

        con.close()

    def test_bronze_dependencies_silently_skipped(self, tmp_path: Path):
        db_path = tmp_path / "test.duckdb"
        con = duckdb.connect(str(db_path))
        _make_bronze_tables(con)

        _write_sql(
            tmp_path,
            "silver",
            "emp",
            ("-- depends_on: bronze.employees\nSELECT * FROM bronze.employees"),
        )

        transforms = discover_transforms(tmp_path)
        ordered = build_execution_order(transforms)

        assert len(ordered) == 1
        assert ordered[0].qualified_name == "silver.emp"

        results = execute_transforms(con, ordered)
        assert results[0].status == "success"
        con.close()

    def test_missing_silver_dependency_still_raises(self, tmp_path: Path):
        _write_sql(
            tmp_path, "gold", "bad", ("-- depends_on: silver.nonexistent\nSELECT 1")
        )

        transforms = discover_transforms(tmp_path)
        with pytest.raises(ValueError, match="silver.nonexistent"):
            build_execution_order(transforms)
