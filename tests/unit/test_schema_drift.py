"""Tests for schema drift detection (V10)."""

from __future__ import annotations

from pathlib import Path

from feather_flow.schema_drift import detect_drift


class TestDetectDrift:
    def test_no_drift_same_schema(self):
        current = [("id", "INTEGER"), ("name", "VARCHAR")]
        stored = [("id", "INTEGER"), ("name", "VARCHAR")]
        drift = detect_drift(current, stored)
        assert not drift.has_drift
        assert drift.added == []
        assert drift.removed == []
        assert drift.type_changed == []

    def test_added_column(self):
        current = [("id", "INTEGER"), ("name", "VARCHAR"), ("phone", "VARCHAR")]
        stored = [("id", "INTEGER"), ("name", "VARCHAR")]
        drift = detect_drift(current, stored)
        assert drift.has_drift
        assert drift.added == [("phone", "VARCHAR")]
        assert drift.removed == []

    def test_removed_column(self):
        current = [("id", "INTEGER")]
        stored = [("id", "INTEGER"), ("name", "VARCHAR")]
        drift = detect_drift(current, stored)
        assert drift.has_drift
        assert drift.removed == [("name", "VARCHAR")]
        assert drift.added == []

    def test_type_changed(self):
        current = [("id", "INTEGER"), ("name", "BIGINT")]
        stored = [("id", "INTEGER"), ("name", "VARCHAR")]
        drift = detect_drift(current, stored)
        assert drift.has_drift
        assert drift.type_changed == [("name", "VARCHAR", "BIGINT")]

    def test_multiple_changes(self):
        current = [("id", "INTEGER"), ("email", "VARCHAR")]
        stored = [("id", "INTEGER"), ("name", "VARCHAR")]
        drift = detect_drift(current, stored)
        assert drift.has_drift
        assert drift.added == [("email", "VARCHAR")]
        assert drift.removed == [("name", "VARCHAR")]

    def test_severity_info_for_added_removed(self):
        current = [("id", "INTEGER"), ("phone", "VARCHAR")]
        stored = [("id", "INTEGER")]
        drift = detect_drift(current, stored)
        assert drift.severity == "INFO"

    def test_severity_critical_for_type_changed(self):
        current = [("id", "BIGINT")]
        stored = [("id", "INTEGER")]
        drift = detect_drift(current, stored)
        assert drift.severity == "CRITICAL"


class TestToJsonDict:
    def test_added_only(self):
        current = [("id", "INTEGER"), ("phone", "VARCHAR")]
        stored = [("id", "INTEGER")]
        d = detect_drift(current, stored).to_json_dict()
        assert d == {"added": ["phone"]}

    def test_removed_only(self):
        current = [("id", "INTEGER")]
        stored = [("id", "INTEGER"), ("legacy", "VARCHAR")]
        d = detect_drift(current, stored).to_json_dict()
        assert d == {"removed": ["legacy"]}

    def test_type_changed_only(self):
        current = [("id", "BIGINT")]
        stored = [("id", "INTEGER")]
        d = detect_drift(current, stored).to_json_dict()
        assert d == {
            "type_changed": [{"column": "id", "from": "INTEGER", "to": "BIGINT"}]
        }

    def test_all_three_kinds(self):
        current = [("id", "BIGINT"), ("email", "VARCHAR")]
        stored = [("id", "INTEGER"), ("legacy", "VARCHAR")]
        d = detect_drift(current, stored).to_json_dict()
        assert set(d.keys()) == {"added", "removed", "type_changed"}
        assert d["added"] == ["email"]
        assert d["removed"] == ["legacy"]
        assert d["type_changed"] == [
            {"column": "id", "from": "INTEGER", "to": "BIGINT"}
        ]

    def test_no_drift_returns_empty_dict(self):
        current = [("id", "INTEGER")]
        stored = [("id", "INTEGER")]
        d = detect_drift(current, stored).to_json_dict()
        assert d == {}


class TestStateSnapshot:
    def test_save_and_read_snapshot(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        schema = [("id", "INTEGER"), ("name", "VARCHAR")]
        sm.save_schema_snapshot("orders", schema)

        stored = sm.get_schema_snapshot("orders")
        assert stored == [("id", "INTEGER"), ("name", "VARCHAR")]

    def test_no_snapshot_returns_none(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        assert sm.get_schema_snapshot("nonexistent") is None

    def test_upsert_overwrites(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.save_schema_snapshot("orders", [("id", "INTEGER"), ("name", "VARCHAR")])
        sm.save_schema_snapshot("orders", [("id", "INTEGER"), ("email", "VARCHAR")])

        stored = sm.get_schema_snapshot("orders")
        cols = [c[0] for c in stored]
        assert "email" in cols
        assert "name" not in cols
