"""Domain logic for feather init — creates a new feather project."""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InitResult:
    created: list[str] = field(default_factory=list)


def init_project(path: Path, name: str) -> InitResult:
    path.mkdir(parents=True, exist_ok=True)
    pkg = importlib.resources.files("feather_etl.resources.templates")
    result = InitResult()
    templates = ["feather.yaml", "pyproject.toml", ".env.example", ".gitignore", "feather.example.yaml"]
    for filename in templates:
        content = pkg.joinpath(filename).read_text(encoding="utf-8")
        if filename == "pyproject.toml":
            content = content.replace("{name}", name)
        (path / filename).write_text(content, encoding="utf-8")
        result.created.append(filename)
    return result
