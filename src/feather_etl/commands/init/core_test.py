"""Tests for commands/init/core.py — init_project domain logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from feather_etl.commands.init.core import InitResult, init_project

_FILES = ["feather.yaml", "pyproject.toml", ".env.example", ".gitignore", "feather.example.yaml"]


def test_creates_directory_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "new-project"
    init_project(target, "new-project")
    assert target.is_dir()


def test_writes_all_five_files(tmp_path: Path) -> None:
    target = tmp_path / "my-proj"
    init_project(target, "my-proj")
    for name in _FILES:
        assert (target / name).is_file(), f"{name!r} not created"


def test_pyproject_toml_contains_actual_name(tmp_path: Path) -> None:
    target = tmp_path / "rama_dw"
    init_project(target, "rama_dw")
    content = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "rama_dw" in content
    assert "{name}" not in content


def test_returns_init_result(tmp_path: Path) -> None:
    target = tmp_path / "my-proj"
    result = init_project(target, "my-proj")
    assert isinstance(result, InitResult)
