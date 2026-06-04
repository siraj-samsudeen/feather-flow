"""Core logic for feather init — file stamping and orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from feather_etl import __version__

FEATHER_YAML_TEMPLATE = """\
# feather-etl project configuration
# Add sources with: feather source add

# sources:        # populated by `feather source add`

defaults:
  sample_threshold: 100_000   # rows; switch from LIMIT to TABLESAMPLE above this
"""

# Project version is always 0.1.0 for new client projects regardless of feather-etl version.
# {feather_version} is the installed feather-etl version, used only for the dependency pin.
PYPROJECT_DEFAULT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["feather-etl>={feather_version}"]
"""

PYPROJECT_DEV_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["feather-etl"]

[tool.uv.sources]
feather-etl = {{ path = "{source_path}", editable = true }}
"""


class DevModeUnavailableError(Exception):
    def __init__(self, checked_path: Path) -> None:
        self.checked_path = checked_path
        super().__init__(f"Dev mode unavailable. Checked: {checked_path}")


def feather_etl_source_path() -> Path | None:
    # Editable installs place core.py at <repo>/src/feather_etl/commands/init/core.py —
    # five parents reach the repo root. PyPI installs land in site-packages; no pyproject.toml there.
    candidate = Path(__file__).resolve().parent.parent.parent.parent.parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    return None


@dataclass(frozen=True)
class InitResult:
    target: Path
    files: dict[str, Literal["created", "skipped"]]
    messages: list[str]
    feather_etl_source_path: Path | None = None


def _validate_target_name(name: str) -> None:
    if not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise ValueError(
            f"Invalid project name: {name}. Only alphanumeric characters, "
            "hyphens, and underscores are allowed."
        )


def _stamp_feather_yaml(
    target: Path,
    files: dict[str, Literal["created", "skipped"]],
    messages: list[str],
) -> None:
    path = target / "feather.yaml"
    if path.exists():
        files["feather.yaml"] = "skipped"
        messages.append("feather.yaml: present (delete this file and re-run to reset)")
        return
    path.write_text(FEATHER_YAML_TEMPLATE)
    files["feather.yaml"] = "created"


def _stamp_pyproject(
    target: Path,
    files: dict[str, Literal["created", "skipped"]],
    messages: list[str],
    dev: bool = False,
    source_path: Path | None = None,
) -> None:
    path = target / "pyproject.toml"
    if path.exists():
        files["pyproject.toml"] = "skipped"
        messages.append("pyproject.toml: present (delete this file and re-run to reset)")
        return
    if dev:
        content = PYPROJECT_DEV_TEMPLATE.format(
            name=target.name,
            source_path=str(source_path),
        )
    else:
        content = PYPROJECT_DEFAULT_TEMPLATE.format(
            name=target.name,
            feather_version=__version__,
        )
    path.write_text(content)
    files["pyproject.toml"] = "created"


def _stamp_gitignore(
    target: Path,
    files: dict[str, Literal["created", "skipped"]],
    messages: list[str],
) -> None:
    path = target / ".gitignore"
    if path.exists():
        files[".gitignore"] = "skipped"
        messages.append(".gitignore: present (ensure .env is listed)")
        return
    path.write_text(".env\n")
    files[".gitignore"] = "created"


def _stamp_env(
    target: Path,
    files: dict[str, Literal["created", "skipped"]],
    messages: list[str],
) -> None:
    path = target / ".env"
    if path.exists():
        files[".env"] = "skipped"
        return
    path.write_text("")
    files[".env"] = "created"


def init_project(target: Path, dev: bool = False) -> InitResult:
    _validate_target_name(target.name)

    resolved_source_path: Path | None = None
    if dev:
        resolved_source_path = feather_etl_source_path()
        if resolved_source_path is None:
            checked_path = Path(__file__).resolve().parent.parent.parent.parent.parent
            raise DevModeUnavailableError(checked_path)

    target.mkdir(parents=True, exist_ok=True)
    files: dict[str, Literal["created", "skipped"]] = {}
    messages: list[str] = []
    _stamp_feather_yaml(target, files, messages)
    _stamp_pyproject(target, files, messages, dev=dev, source_path=resolved_source_path)
    _stamp_gitignore(target, files, messages)
    _stamp_env(target, files, messages)
    return InitResult(
        target=target,
        files=files,
        messages=messages,
        feather_etl_source_path=resolved_source_path,
    )
