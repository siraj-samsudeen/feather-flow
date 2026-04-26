"""Tests that all five init templates are reachable via importlib.resources."""

from __future__ import annotations

import importlib.resources

_TEMPLATES = [
    "feather.yaml",
    "pyproject.toml",
    ".env.example",
    ".gitignore",
    "feather.example.yaml",
]


def test_all_templates_reachable() -> None:
    pkg = importlib.resources.files("feather_etl.resources.templates")
    for name in _TEMPLATES:
        assert pkg.joinpath(name).is_file(), f"template {name!r} missing from package"
