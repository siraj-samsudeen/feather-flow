"""Integration: architectural invariants.

Tests that load-bearing structural constraints hold across the codebase —
currently the #43 no-typer-in-pure-cores rule (see
docs/superpowers/specs/2026-04-19-thin-cli-refactor-design.md).
"""

from __future__ import annotations

import importlib
import inspect

import pytest

CORE_MODULES = [
    "feather_etl.pipeline",
    "feather_etl.extract",
    "feather_etl.viewer_server",
    "feather_etl.init_wizard",
    "feather_etl.exceptions",
    "feather_etl.history",
    "feather_etl.status",
    "feather_etl.validate",
    "feather_etl.setup",
    "feather_etl.discover",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_core_module_does_not_import_typer(module_name: str) -> None:
    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    assert "import typer" not in source, (
        f"{module_name} must not import typer — push the Typer concern "
        f"into the corresponding commands/<name>.py wrapper."
    )
    assert "from typer" not in source, (
        f"{module_name} must not import from typer — push the Typer concern "
        f"into the corresponding commands/<name>.py wrapper."
    )
