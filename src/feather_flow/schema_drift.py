"""Schema drift detection for feather-flow (V10)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DriftReport:
    """Result of comparing current schema against stored snapshot."""

    added: list[tuple[str, str]] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)
    type_changed: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.type_changed)

    @property
    def severity(self) -> str:
        """INFO for added/removed only, CRITICAL if any type_changed."""
        if self.type_changed:
            return "CRITICAL"
        return "INFO"

    def to_json_dict(self) -> dict:
        """Serialize drift for _runs.schema_changes JSON column."""
        d: dict = {}
        if self.added:
            d["added"] = [col for col, _ in self.added]
        if self.removed:
            d["removed"] = [col for col, _ in self.removed]
        if self.type_changed:
            d["type_changed"] = [
                {"column": col, "from": old, "to": new}
                for col, old, new in self.type_changed
            ]
        return d


def detect_drift(
    current_schema: list[tuple[str, str]],
    stored_schema: list[tuple[str, str]],
) -> DriftReport:
    """Compare current source schema against stored snapshot.

    Each schema is a list of (column_name, data_type) tuples.
    """
    current_map = {name: dtype for name, dtype in current_schema}
    stored_map = {name: dtype for name, dtype in stored_schema}

    added = [(name, dtype) for name, dtype in current_schema if name not in stored_map]
    removed = [
        (name, dtype) for name, dtype in stored_schema if name not in current_map
    ]
    type_changed = [
        (name, stored_map[name], current_map[name])
        for name in current_map
        if name in stored_map and current_map[name] != stored_map[name]
    ]

    return DriftReport(added=added, removed=removed, type_changed=type_changed)
