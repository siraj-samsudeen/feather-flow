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
