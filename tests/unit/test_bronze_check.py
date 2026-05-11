"""Unit tests for ``check_bronze_dependencies`` (advisory bronze check)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from feather_etl.transforms import TransformMeta, check_bronze_dependencies


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection with empty bronze schema."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA bronze")
    yield c
    c.close()


def _silver(name: str, depends_on: list[str] | None = None) -> TransformMeta:
    return TransformMeta(
        name=name,
        schema="silver",
        sql="SELECT 1",
        depends_on=depends_on or [],
        materialized=False,
        path=Path(f"silver/{name}.sql"),
    )


def _gold(name: str, depends_on: list[str] | None = None) -> TransformMeta:
    return TransformMeta(
        name=name,
        schema="gold",
        sql="SELECT 1",
        depends_on=depends_on or [],
        materialized=False,
        path=Path(f"gold/{name}.sql"),
    )


class TestCheckBronzeDependencies:
    def test_zero_declared_deps_returns_empty(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        transforms = [_silver("emp"), _silver("orders")]
        assert check_bronze_dependencies(con, transforms) == []

    def test_one_declared_dep_present_returns_empty(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        con.execute("CREATE TABLE bronze.orders (id INTEGER)")
        transforms = [_silver("orders", depends_on=["bronze.orders"])]
        assert check_bronze_dependencies(con, transforms) == []

    def test_one_declared_dep_missing_returns_one_warning(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        transforms = [_silver("orders", depends_on=["bronze.orders"])]
        warnings = check_bronze_dependencies(con, transforms)
        assert len(warnings) == 1
        assert "bronze.orders" in warnings[0]
        assert warnings[0].startswith("WARNING:")
        assert "feather extract" in warnings[0]

    def test_six_missing_deps_returns_six_warnings(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        transforms = [
            _silver(f"t{i}", depends_on=[f"bronze.t{i}"]) for i in range(6)
        ]
        warnings = check_bronze_dependencies(con, transforms)
        # Helper returns the full raw list — caller collapses when > 5.
        assert len(warnings) == 6
        for i, w in enumerate(warnings):
            assert f"bronze.t{i}" in w

    def test_mixed_with_and_without_depends_on(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        con.execute("CREATE TABLE bronze.present (id INTEGER)")
        transforms = [
            _silver("no_deps"),
            _silver("ok", depends_on=["bronze.present"]),
            _silver("missing", depends_on=["bronze.absent"]),
        ]
        warnings = check_bronze_dependencies(con, transforms)
        assert len(warnings) == 1
        assert "bronze.absent" in warnings[0]

    def test_silver_dep_on_silver_not_checked(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        # depends_on: silver.X is a cross-transform dep, not a bronze dep.
        # check_bronze_dependencies must ignore it (no warning, no false
        # positive even though silver.x doesn't exist in the destination).
        transforms = [_silver("derived", depends_on=["silver.x"])]
        assert check_bronze_dependencies(con, transforms) == []

    def test_gold_with_bronze_dep_not_checked(
        self, con: duckdb.DuckDBPyConnection
    ) -> None:
        # The helper name and contract scope it to silver transforms.
        # A gold transform that (improperly) declares a bronze dep is out of
        # scope. This guards against scope creep.
        transforms = [_gold("g", depends_on=["bronze.absent"])]
        assert check_bronze_dependencies(con, transforms) == []
