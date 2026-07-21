"""Persistent state for `feather discover` — feather_discover_state.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from feather_flow.config import schema_output_path

SCHEMA_VERSION = 1
STATE_FILENAME = "feather_discover_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DiscoverState:
    config_dir: Path
    sources: dict[str, dict] = field(default_factory=dict)
    auto_enumeration: dict[str, dict] = field(default_factory=dict)
    last_run_at: str | None = None

    @classmethod
    def load(cls, config_dir: Path) -> "DiscoverState":
        path = config_dir / STATE_FILENAME
        if not path.is_file():
            return cls(config_dir=config_dir)
        raw = json.loads(path.read_text())
        return cls(
            config_dir=config_dir,
            sources=raw.get("sources", {}),
            auto_enumeration=raw.get("auto_enumeration", {}),
            last_run_at=raw.get("last_run_at"),
        )

    def save(self) -> None:
        self.last_run_at = _now_iso()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "last_run_at": self.last_run_at,
            "sources": self.sources,
            "auto_enumeration": self.auto_enumeration,
        }
        (self.config_dir / STATE_FILENAME).write_text(json.dumps(payload, indent=2))

    def record_ok(
        self,
        *,
        name: str,
        type_: str,
        fingerprint: str,
        table_count: int,
        output_path: Path,
        host: str | None = None,
        database: str | None = None,
    ) -> None:
        self.sources[name] = {
            "type": type_,
            "host": host,
            "database": database,
            "fingerprint": fingerprint,
            "status": "ok",
            "discovered_at": _now_iso(),
            "table_count": table_count,
            "output_path": str(output_path),
        }

    def record_failed(
        self,
        *,
        name: str,
        type_: str,
        fingerprint: str,
        error: str,
        host: str | None = None,
        database: str | None = None,
    ) -> None:
        prev = self.sources.get(name, {})
        attempts = int(prev.get("attempt_count", 0)) + 1
        self.sources[name] = {
            "type": type_,
            "host": host,
            "database": database,
            "fingerprint": fingerprint,
            "status": "failed",
            "attempted_at": _now_iso(),
            "error": error,
            "attempt_count": attempts,
        }

    def record_removed(self, name: str) -> None:
        if name in self.sources:
            self.sources[name]["status"] = "removed"
            self.sources[name]["removed_detected_at"] = _now_iso()

    def record_orphaned(self, name: str, note: str = "") -> None:
        if name in self.sources:
            self.sources[name]["status"] = "orphaned"
            self.sources[name]["orphaned_detected_at"] = _now_iso()
            if note:
                self.sources[name]["note"] = note

    def record_auto_enum(
        self,
        *,
        parent_name: str,
        type_: str,
        host: str | None,
        databases_seen: list[str],
    ) -> None:
        self.auto_enumeration[parent_name] = {
            "type": type_,
            "host": host,
            "last_enumerated_at": _now_iso(),
            "databases_seen": list(databases_seen),
        }


# --- classification ---------------------------------------------------------


def classify(
    *, state: DiscoverState, current_names: list[str], flag: str | None
) -> dict[str, str]:
    """Return per-name decision for this run.

    flag: None, "refresh", "retry-failed", "prune".
    """
    decisions: dict[str, str] = {}
    state_names = set(state.sources)
    current = list(current_names)

    for name in current:
        entry = state.sources.get(name)
        if entry is None:
            decisions[name] = "new"
            continue
        status = entry.get("status", "ok")
        if flag == "refresh":
            decisions[name] = "rerun"
        elif flag == "retry-failed":
            decisions[name] = "retry" if status == "failed" else "skip"
        elif flag == "prune":
            decisions[name] = "skip"
        else:
            if status == "ok":
                decisions[name] = "cached"
            elif status == "failed":
                decisions[name] = "retry"
            else:  # removed/orphaned coming back into config
                decisions[name] = "rerun"

    for name in state_names - set(current):
        decisions[name] = "removed"
    return decisions


def detect_renames(
    *, state: DiscoverState, current: list[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, list[str]]]]:
    """Infer rename proposals from matching fingerprints."""
    current_names = {name for name, _ in current}
    eligible_state = {
        name: entry
        for name, entry in state.sources.items()
        if name not in current_names
        and entry.get("status") in ("ok", "removed", "failed")
    }

    proposals: list[tuple[str, str]] = []
    ambiguous: list[tuple[str, list[str]]] = []
    used_state_names: set[str] = set()

    for new_name, fingerprint in current:
        if new_name in state.sources:
            continue
        candidates = [
            name
            for name, entry in eligible_state.items()
            if entry.get("fingerprint") == fingerprint
        ]
        available = [name for name in candidates if name not in used_state_names]
        if len(candidates) == 1 and len(available) == 1:
            old_name = available[0]
            proposals.append((old_name, new_name))
            used_state_names.add(old_name)
        elif candidates:
            ambiguous.append((new_name, candidates))

    return proposals, ambiguous


def apply_renames(
    *,
    state: DiscoverState,
    renames: list[tuple[str, str]],
    config_dir: Path,
    sources: list,
) -> None:
    """Move matched state entries and schema files to their new names."""
    source_by_name = {source.name: source for source in sources}

    for old, new in renames:
        if old not in state.sources:
            continue

        old_prefix = f"{old}__"
        new_prefix = f"{new}__"

        parent_entry = state.sources.pop(old)
        state.sources[new] = parent_entry

        if old in state.auto_enumeration:
            state.auto_enumeration[new] = state.auto_enumeration.pop(old)

        child_keys = [
            name for name in list(state.sources) if name.startswith(old_prefix)
        ]
        for child_old in child_keys:
            child_new = new_prefix + child_old[len(old_prefix) :]
            child_entry = state.sources.pop(child_old)
            child_entry["output_path"] = _rename_schema_file(
                config_dir=config_dir,
                output_path=child_entry.get("output_path"),
                new_source=source_by_name.get(child_new),
            )
            state.sources[child_new] = child_entry

        parent_entry["output_path"] = _rename_schema_file(
            config_dir=config_dir,
            output_path=parent_entry.get("output_path"),
            new_source=source_by_name.get(new),
        )


def _rename_schema_file(
    *, config_dir: Path, output_path: str | None, new_source
) -> str | None:
    if output_path is None:
        return None
    if new_source is None:
        return output_path

    current = Path(output_path)
    current_name = current.name
    new_path = str(current.with_name(schema_output_path(new_source).name))
    if new_path == output_path:
        return output_path

    source_file = config_dir / current_name
    target_file = config_dir / Path(new_path).name
    if source_file.is_file():
        source_file.rename(target_file)
    return new_path
