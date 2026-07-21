"""Unit tests for discover_state.py — state file I/O + classification."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


class TestDiscoverStateRoundTrip:
    def test_load_returns_empty_when_file_missing(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        assert s.sources == {}
        assert s.auto_enumeration == {}

    def test_save_then_load(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="duckdb",
            fingerprint="duckdb:/tmp/x.duckdb",
            table_count=3,
            output_path=Path("./schema_a.json"),
        )
        s.save()

        reloaded = DiscoverState.load(tmp_path)
        assert "a" in reloaded.sources
        assert reloaded.sources["a"]["status"] == "ok"
        assert reloaded.sources["a"]["table_count"] == 3
        assert reloaded.sources["a"]["fingerprint"] == "duckdb:/tmp/x.duckdb"

    def test_record_failed_stores_error_and_increments_attempt(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_failed(
            name="a",
            type_="sqlserver",
            fingerprint="sqlserver:h:1433:DB",
            error="Login failed",
        )
        s.record_failed(
            name="a",
            type_="sqlserver",
            fingerprint="sqlserver:h:1433:DB",
            error="Login failed",
        )
        assert s.sources["a"]["attempt_count"] == 2

    def test_schema_version_in_payload(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/tmp",
            table_count=0,
            output_path=Path("./schema_a.json"),
        )
        s.save()
        payload = json.loads((tmp_path / "feather_discover_state.json").read_text())
        assert payload["schema_version"] == 1


class TestClassify:
    def test_new_source_classified_new(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        decisions = classify(state=s, current_names=["a", "b"], flag=None)
        assert decisions["a"] == "new"
        assert decisions["b"] == "new"

    def test_cached_source_classified_cached(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        decisions = classify(state=s, current_names=["a"], flag=None)
        assert decisions["a"] == "cached"

    def test_failed_classified_retry_default(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.record_failed(name="a", type_="csv", fingerprint="csv:/x", error="oops")
        decisions = classify(state=s, current_names=["a"], flag=None)
        assert decisions["a"] == "retry"

    def test_refresh_forces_rerun(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        decisions = classify(state=s, current_names=["a"], flag="refresh")
        assert decisions["a"] == "rerun"

    def test_retry_failed_skips_cached(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        s.record_failed(name="b", type_="csv", fingerprint="csv:/y", error="x")
        decisions = classify(state=s, current_names=["a", "b"], flag="retry-failed")
        assert decisions["a"] == "skip"
        assert decisions["b"] == "retry"

    def test_removed_when_state_has_name_not_in_current(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        decisions = classify(state=s, current_names=[], flag=None)
        assert decisions["a"] == "removed"

    def test_prune_skips_current_and_marks_removed(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        s.record_ok(
            name="b",
            type_="csv",
            fingerprint="csv:/y",
            table_count=2,
            output_path=Path("./schema_b.json"),
        )
        decisions = classify(state=s, current_names=["a"], flag="prune")
        assert decisions["a"] == "skip"
        assert decisions["b"] == "removed"


class TestRecordMutations:
    def test_record_removed_sets_status(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        s.record_removed("a")
        assert s.sources["a"]["status"] == "removed"
        assert "removed_detected_at" in s.sources["a"]

    def test_record_removed_ignores_unknown_name(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_removed("nonexistent")  # should not raise
        assert "nonexistent" not in s.sources

    def test_record_orphaned_sets_status_and_note(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_ok(
            name="a",
            type_="csv",
            fingerprint="csv:/x",
            table_count=1,
            output_path=Path("./schema_a.json"),
        )
        s.record_orphaned("a", note="rename rejected")
        assert s.sources["a"]["status"] == "orphaned"
        assert s.sources["a"]["note"] == "rename rejected"


class TestRenameInference:
    def test_unambiguous_match_proposes_rename(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, detect_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-1",
            "output_path": "schema_erp.json",
        }

        proposals, ambiguous = detect_renames(
            state=state,
            current=[("erp_main", "fp-1")],
        )

        assert proposals == [("erp", "erp_main")]
        assert ambiguous == []

    def test_no_match_no_proposal(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, detect_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-1",
            "output_path": "schema_erp.json",
        }

        proposals, ambiguous = detect_renames(
            state=state,
            current=[("erp_main", "fp-2")],
        )

        assert proposals == []
        assert ambiguous == []

    def test_ambiguous_two_state_entries_one_fingerprint(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, detect_renames

        state = DiscoverState.load(tmp_path)
        state.sources["a"] = {
            "status": "ok",
            "fingerprint": "fp-1",
            "output_path": "schema_a.json",
        }
        state.sources["b"] = {
            "status": "removed",
            "fingerprint": "fp-1",
            "output_path": "schema_b.json",
        }

        proposals, ambiguous = detect_renames(
            state=state,
            current=[("c", "fp-1")],
        )

        assert proposals == []
        assert ambiguous == [("c", ["a", "b"])]

    def test_duplicate_current_fingerprint_collision_is_ambiguous(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, detect_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-1",
            "output_path": "schema_erp.json",
        }

        proposals, ambiguous = detect_renames(
            state=state,
            current=[("erp_main", "fp-1"), ("erp_copy", "fp-1")],
        )

        assert proposals == [("erp", "erp_main")]
        assert ambiguous == [("erp_copy", ["erp"])]

    def test_apply_renames_moves_state_entries(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, apply_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-parent",
            "output_path": "schema_erp.json",
        }
        state.sources["erp__db1"] = {
            "status": "ok",
            "fingerprint": "fp-child",
            "output_path": "schema_erp__db1.json",
        }
        state.auto_enumeration["erp"] = {
            "type": "postgres",
            "host": "localhost",
            "last_enumerated_at": "2026-04-16T00:00:00+00:00",
            "databases_seen": ["db1"],
        }
        (tmp_path / "schema_erp.json").write_text("parent")
        (tmp_path / "schema_erp__db1.json").write_text("child")

        apply_renames(
            state=state,
            renames=[("erp", "erp_main")],
            config_dir=tmp_path,
            sources=[
                SimpleNamespace(name="erp_main", type="postgres", _explicit_name=True),
                SimpleNamespace(
                    name="erp_main__db1",
                    type="postgres",
                    _explicit_name=True,
                ),
            ],
        )

        assert "erp" not in state.sources
        assert "erp_main" in state.sources
        assert (
            state.sources["erp_main"]["output_path"] == "schema_postgres_erp_main.json"
        )
        assert "erp__db1" not in state.sources
        assert "erp_main__db1" in state.sources
        assert state.sources["erp_main__db1"]["output_path"] == (
            "schema_postgres_erp_main__db1.json"
        )
        assert "erp" not in state.auto_enumeration
        assert "erp_main" in state.auto_enumeration
        assert (tmp_path / "schema_erp.json").exists() is False
        assert (tmp_path / "schema_erp__db1.json").exists() is False
        assert (tmp_path / "schema_postgres_erp_main.json").is_file()
        assert (tmp_path / "schema_postgres_erp_main__db1.json").is_file()

    def test_apply_renames_keeps_unrelated_files(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState, apply_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-parent",
            "output_path": "schema_erp.json",
        }
        (tmp_path / "schema_erp.json").write_text("parent")
        (tmp_path / "schema_erp2.json").write_text("other")

        apply_renames(
            state=state,
            renames=[("erp", "erp_main")],
            config_dir=tmp_path,
            sources=[
                SimpleNamespace(name="erp_main", type="sqlite", _explicit_name=True),
            ],
        )

        assert (tmp_path / "schema_sqlite_erp_main.json").is_file()
        assert (tmp_path / "schema_erp2.json").is_file()

    def test_apply_renames_ignores_renames_missing_from_state(self, tmp_path: Path):
        """If an ``old`` name isn't in state.sources, the rename is silently skipped."""
        from feather_flow.discover_state import DiscoverState, apply_renames

        state = DiscoverState.load(tmp_path)
        # state is empty — no "erp" entry

        apply_renames(
            state=state,
            renames=[("erp", "erp_main")],
            config_dir=tmp_path,
            sources=[
                SimpleNamespace(name="erp_main", type="sqlite", _explicit_name=True),
            ],
        )

        assert "erp" not in state.sources
        assert "erp_main" not in state.sources

    def test_apply_renames_skips_when_output_path_is_none(self, tmp_path: Path):
        """Parent/child without an output_path is renamed in state but no file move."""
        from feather_flow.discover_state import DiscoverState, apply_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-parent",
            "output_path": None,
        }

        apply_renames(
            state=state,
            renames=[("erp", "erp_main")],
            config_dir=tmp_path,
            sources=[
                SimpleNamespace(name="erp_main", type="sqlite", _explicit_name=True),
            ],
        )

        assert "erp_main" in state.sources
        assert state.sources["erp_main"]["output_path"] is None

    def test_apply_renames_preserves_output_when_new_source_missing(
        self, tmp_path: Path
    ):
        """If the rename target isn't in ``sources`` the output_path is preserved
        unchanged (``new_source is None`` branch in _rename_schema_file)."""
        from feather_flow.discover_state import DiscoverState, apply_renames

        state = DiscoverState.load(tmp_path)
        state.sources["erp"] = {
            "status": "ok",
            "fingerprint": "fp-parent",
            "output_path": "schema_erp.json",
        }
        (tmp_path / "schema_erp.json").write_text("parent")

        apply_renames(
            state=state,
            renames=[("erp", "erp_main")],
            config_dir=tmp_path,
            sources=[],  # erp_main not listed
        )

        # Entry renamed in state, but output_path untouched because we
        # couldn't look up the new source to compute its schema filename.
        assert "erp_main" in state.sources
        assert state.sources["erp_main"]["output_path"] == "schema_erp.json"
        assert (tmp_path / "schema_erp.json").is_file()


class TestRenameSchemaFileNoop:
    def test_new_path_equal_to_current_returns_as_is(self, tmp_path: Path):
        """When the new source's schema filename matches the existing path,
        ``_rename_schema_file`` short-circuits and returns the original path."""
        from feather_flow.discover_state import _rename_schema_file
        from feather_flow.config import schema_output_path

        class _Src:
            type = "sqlite"
            name = "alpha"
            path = "/tmp/alpha.sqlite"

        # Compute what the canonical name would be and seed output_path with it
        expected = schema_output_path(_Src()).name
        result = _rename_schema_file(
            config_dir=tmp_path,
            output_path=expected,
            new_source=_Src(),
        )
        assert result == expected


class TestClassifyOrphanedComesBack:
    def test_orphaned_source_back_in_current_classified_rerun(self, tmp_path: Path):
        """An orphaned source reappearing in current is classified as 'rerun'."""
        from feather_flow.discover_state import DiscoverState, classify

        s = DiscoverState.load(tmp_path)
        s.sources["a"] = {
            "status": "orphaned",
            "fingerprint": "fp-a",
        }

        decisions = classify(state=s, current_names=["a"], flag=None)
        assert decisions["a"] == "rerun"


class TestRecordAutoEnum:
    def test_record_auto_enum_stores_parent_entry(self, tmp_path: Path):
        from feather_flow.discover_state import DiscoverState

        s = DiscoverState.load(tmp_path)
        s.record_auto_enum(
            parent_name="erp",
            type_="postgres",
            host="db.example.com",
            databases_seen=["sales", "erp"],
        )
        assert "erp" in s.auto_enumeration
        entry = s.auto_enumeration["erp"]
        assert entry["type"] == "postgres"
        assert entry["host"] == "db.example.com"
        assert entry["databases_seen"] == ["sales", "erp"]
        assert "last_enumerated_at" in entry
