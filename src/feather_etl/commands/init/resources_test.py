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


def test_feather_yaml_content() -> None:
    pkg = importlib.resources.files("feather_etl.resources.templates")
    content = pkg.joinpath("feather.yaml").read_text(encoding="utf-8")
    assert content == "sources: []\n"


def test_gitignore_content() -> None:
    pkg = importlib.resources.files("feather_etl.resources.templates")
    content = pkg.joinpath(".gitignore").read_text(encoding="utf-8")
    for entry in ["*.duckdb", "*.duckdb.wal", "feather_validation.json", ".env", "__pycache__/", ".venv/"]:
        assert entry in content, f".gitignore missing entry {entry!r}"
