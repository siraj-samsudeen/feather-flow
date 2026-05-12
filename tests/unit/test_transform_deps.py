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
