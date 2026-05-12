"""Shared test utility functions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import ClassVar

import yaml

from feather_etl.sources import ChangeResult, StreamSchema


def write_config(tmp_path: Path, config: dict, directory: Path | None = None) -> Path:
    config_file = (directory or tmp_path) / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    return config_file


def write_curation(tmp_path: Path, tables: list[dict]) -> Path:
    """Write a discovery/curation.json for testing."""
    discovery_dir = tmp_path / "discovery"
    discovery_dir.mkdir(exist_ok=True)
    manifest = {
        "version": 2,
        "updated_at": "2026-04-18T00:00:00Z",
        "notes": "test fixture",
        "source_systems": {},
        "policies": {"data_quality": {"default": "flag", "escalations": []}},
        "tables": tables,
    }
    path = discovery_dir / "curation.json"
    path.write_text(json.dumps(manifest))
    return path


# ---------------------------------------------------------------------------
# Transform-command fixtures (Wave E)
# ---------------------------------------------------------------------------

TRANSFORM_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "transform_command"


class RaisingSource:
    """A source whose every method raises — used to prove the no-source-touch
    invariant of ``feather transform``.

    Registered into the source registry by the transform-command tests so
    a config can declare ``type: raising`` and ``feather transform`` will
    instantiate it. Because ``feather transform`` MUST NOT open a source
    connection, none of these methods should ever fire. The class-level
    ``connect_called`` / ``called_methods`` flags let tests assert that
    invariant after the run.
    """

    type: ClassVar[str] = "raising"

    # Class-level flags so a test can detect any call from any instance.
    connect_called: ClassVar[bool] = False
    called_methods: ClassVar[list[str]] = []

    def __init__(self, *, name: str = "raising_src", path: Path | None = None) -> None:
        self.name = name
        self.path = path

    @classmethod
    def reset(cls) -> None:
        cls.connect_called = False
        cls.called_methods = []

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "RaisingSource":
        # Side-effect free per Source protocol — this is the only method
        # allowed to run without flipping the flag.
        path_value = entry.get("path")
        path = Path(path_value) if path_value else None
        if path is not None and not path.is_absolute():
            path = (config_dir / path).resolve()
        return cls(name=entry.get("name", "raising_src"), path=path)

    def _record(self, method: str) -> None:
        type(self).connect_called = True
        type(self).called_methods.append(method)

    def _connect(self):
        self._record("_connect")
        raise RuntimeError("should not be called")

    def validate_source_table(self, source_table: str) -> list[str]:
        # validate_source_table is called during config validation (before
        # any extraction) — it must NOT trip the invariant flag.
        return []

    def check(self) -> bool:
        self._record("check")
        raise RuntimeError("should not be called")

    def discover(self) -> list[StreamSchema]:
        self._record("discover")
        raise RuntimeError("should not be called")

    def extract(
        self,
        table: str,
        columns: list[str] | None = None,
        filter: str | None = None,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
    ):
        self._record("extract")
        raise RuntimeError("should not be called")

    def detect_changes(
        self, table: str, last_state: dict[str, object] | None = None
    ) -> ChangeResult:
        self._record("detect_changes")
        raise RuntimeError("should not be called")

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        self._record("get_schema")
        raise RuntimeError("should not be called")


def register_raising_source() -> None:
    """Register RaisingSource into the source registry under ``type: raising``.

    Idempotent — safe to call from multiple tests.
    """
    from feather_etl.sources.registry import SOURCE_CLASSES

    SOURCE_CLASSES["raising"] = "tests.helpers.RaisingSource"


def copy_transform_fixture(target_dir: Path) -> Path:
    """Copy the bundled transform_command fixture tree into ``target_dir``.

    Returns the target directory. After this call, ``target_dir`` contains
    a ``transforms/`` tree (silver_orders, gold_summary, gold_facts) and a
    ``csv_data/orders.csv`` bronze input. The caller writes the config.
    """
    src = TRANSFORM_FIXTURE_DIR
    for sub in ("transforms", "csv_data"):
        s = src / sub
        d = target_dir / sub
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
    return target_dir


def make_curation_entry(
    source_db: str,
    source_table: str,
    alias: str,
    strategy: str = "full",
    primary_key: list[str] | None = None,
    timestamp_column: str | None = None,
    filter: str | None = None,
    quality_checks: dict | None = None,
    column_map: dict[str, str] | None = None,
    dedup: bool = False,
    dedup_columns: list[str] | None = None,
    schedule: str | None = None,
) -> dict:
    """Create a curation.json include entry for testing."""
    timestamp = None
    if timestamp_column:
        timestamp = {"column": timestamp_column, "reason": None, "rejected": []}
    return {
        "source_db": source_db,
        "source_table": source_table,
        "decision": "include",
        "table_type": "fact",
        "group": "test",
        "alias": alias,
        "classification_notes": None,
        "strategy": strategy,
        "primary_key": primary_key or ["id"],
        "timestamp": timestamp,
        "grain": None,
        "scd": None,
        "mapping": None,
        "dq_policy": None,
        "load_contract": None,
        "reason": "test fixture",
    }
