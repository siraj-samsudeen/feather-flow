"""Core logic for feather init — file stamping and orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FEATHER_YAML_TEMPLATE = """\
# feather-etl project configuration
# Add sources with: feather source add

# sources:        # populated by `feather source add`

defaults:
  sample_threshold: 100_000   # rows; switch from LIMIT to TABLESAMPLE above this
"""


@dataclass(frozen=True)
class InitResult:
    target: Path
    files: dict[str, Literal["created", "skipped"]]


def _stamp_feather_yaml(
    target: Path, files: dict[str, Literal["created", "skipped"]]
) -> None:
    (target / "feather.yaml").write_text(FEATHER_YAML_TEMPLATE)
    files["feather.yaml"] = "created"


def init_project(target: Path) -> InitResult:
    files: dict[str, Literal["created", "skipped"]] = {}
    _stamp_feather_yaml(target, files)
    return InitResult(target=target, files=files)
