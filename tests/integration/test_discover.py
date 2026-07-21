"""Integration: feather_flow.discover — run_discover, detect_renames_for_sources,
apply_rename_decision.

Exercises discover + config + discover_state + sources.expand cross-module
behavior. Tests build real SQLite fixtures and invoke the discover module
directly (not through the CLI; for CLI coverage see tests/e2e/test_03_discover.py).
"""

from __future__ import annotations

import shutil

import yaml

from feather_flow.config import load_config
from feather_flow.discover import (
    _fingerprint_for,
    apply_rename_decision,
    detect_renames_for_sources,
    run_discover,
)
from feather_flow.discover_state import DiscoverState
from feather_flow.sources.expand import expand_db_sources

from tests.conftest import FIXTURES_DIR


def _setup_sqlite_project(project, source_name: str | None = None):
    """Copy sample_erp.sqlite + write a feather.yaml into the project root.

    Returns the project.config_path for convenience.
    """
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    source: dict = {"type": "sqlite", "path": "./source.sqlite"}
    if source_name is not None:
        source["name"] = source_name
    project.write_config(
        sources=[source],
        destination={"path": "./feather_data.duckdb"},
        tables=[
            {
                "name": "orders",
                "source_table": "orders",
                "target_table": "bronze.orders",
                "strategy": "full",
            }
        ],
    )
    return project.config_path


# --- detect_renames_for_sources ---------------------------------------------


def test_detect_renames_returns_empty_proposals_when_state_is_empty(project):
    cfg = load_config(_setup_sqlite_project(project), validate=False)
    state = DiscoverState.load(project.root)
    sources = expand_db_sources(cfg.sources)

    detection = detect_renames_for_sources(state, sources)

    assert detection.proposals == []
    assert detection.ambiguous == []


def test_detect_renames_finds_rename_when_fingerprint_matches_under_new_name(project):
    """Rename a source in YAML; same fingerprint → proposal."""
    # First load with name "old_db" — record state.
    cfg = load_config(
        _setup_sqlite_project(project, source_name="old_db"), validate=False
    )
    state = DiscoverState.load(project.root)
    sources = expand_db_sources(cfg.sources)
    # Simulate a prior successful discover under "old_db".
    state.record_ok(
        name="old_db",
        type_=sources[0].type,
        fingerprint=_fingerprint_for(sources[0]),
        table_count=1,
        output_path=project.root / "schemas-sqlite-old_db.json",
    )

    # Now rename source to "new_db".
    cfg = load_config(
        _setup_sqlite_project(project, source_name="new_db"), validate=False
    )
    sources = expand_db_sources(cfg.sources)

    detection = detect_renames_for_sources(state, sources)

    assert detection.proposals == [("old_db", "new_db")]
    assert detection.ambiguous == []


def test_detect_renames_returns_ambiguous_when_multiple_state_entries_match(project):
    """Two stale state entries with the same fingerprint as one current
    source → ambiguous (cannot decide which old name was renamed)."""
    cfg = load_config(
        _setup_sqlite_project(project, source_name="new_db"), validate=False
    )
    state = DiscoverState.load(project.root)
    sources = expand_db_sources(cfg.sources)
    fp = _fingerprint_for(sources[0])
    state.record_ok(
        name="old_a",
        type_=sources[0].type,
        fingerprint=fp,
        table_count=1,
        output_path=project.root / "schemas-sqlite-old_a.json",
    )
    state.record_ok(
        name="old_b",
        type_=sources[0].type,
        fingerprint=fp,
        table_count=1,
        output_path=project.root / "schemas-sqlite-old_b.json",
    )

    detection = detect_renames_for_sources(state, sources)

    assert detection.proposals == []
    assert len(detection.ambiguous) == 1
    new_name, candidates = detection.ambiguous[0]
    assert new_name == "new_db"
    assert set(candidates) == {"old_a", "old_b"}


# --- apply_rename_decision --------------------------------------------------


def test_apply_rename_decision_renames_state_and_files_for_accepted_proposals(project):
    cfg = load_config(
        _setup_sqlite_project(project, source_name="new_db"), validate=False
    )
    state = DiscoverState.load(project.root)
    sources = expand_db_sources(cfg.sources)
    fp = _fingerprint_for(sources[0])
    old_path = project.root / "schemas-sqlite-old_db.json"
    old_path.write_text("[]")
    state.record_ok(
        name="old_db",
        type_=sources[0].type,
        fingerprint=fp,
        table_count=1,
        output_path=old_path,
    )

    apply_rename_decision(
        state,
        accepted=[("old_db", "new_db")],
        rejected=[],
        sources=sources,
        config_dir=project.root,
    )

    assert "new_db" in state.sources
    assert "old_db" not in state.sources


def test_apply_rename_decision_marks_orphaned_for_rejected_proposals(project):
    cfg = load_config(
        _setup_sqlite_project(project, source_name="new_db"), validate=False
    )
    state = DiscoverState.load(project.root)
    sources = expand_db_sources(cfg.sources)
    fp = _fingerprint_for(sources[0])
    state.record_ok(
        name="old_db",
        type_=sources[0].type,
        fingerprint=fp,
        table_count=1,
        output_path=project.root / "schemas-sqlite-old_db.json",
    )

    apply_rename_decision(
        state,
        accepted=[],
        rejected=[("old_db", "new_db")],
        sources=sources,
        config_dir=project.root,
    )

    assert state.sources["old_db"].get("status") == "orphaned"


# --- run_discover -----------------------------------------------------------


def test_run_discover_records_succeeded_sources_with_table_counts(project):
    cfg = load_config(_setup_sqlite_project(project), validate=False)

    report = run_discover(
        cfg,
        project.root,
        refresh=False,
        retry_failed=False,
        prune=False,
    )

    assert report.succeeded_count == 1
    assert report.failed_count == 0
    assert len(report.results) == 1
    r = report.results[0]
    assert r.status == "succeeded"
    assert r.table_count > 0
    assert r.output_path is not None
    assert r.output_path.exists()


def test_run_discover_records_failed_sources_with_error_message(project):
    """Source pointing at a missing file → failed, error captured in result."""
    cfg_dict = {
        "sources": [{"type": "sqlite", "path": "./missing.sqlite"}],
        "destination": {"path": "./feather_data.duckdb"},
        "tables": [
            {
                "name": "orders",
                "source_table": "orders",
                "target_table": "bronze.orders",
                "strategy": "full",
            }
        ],
    }
    project.config_path.write_text(yaml.dump(cfg_dict))
    # discover_mode skips table validation
    cfg = load_config(project.config_path, validate=False)

    report = run_discover(
        cfg,
        project.root,
        refresh=False,
        retry_failed=False,
        prune=False,
    )

    assert report.failed_count == 1
    assert report.succeeded_count == 0
    assert report.results[0].status == "failed"
    assert report.results[0].error is not None


def test_run_discover_records_failed_when_write_schema_raises(project, monkeypatch):
    """If ``_write_schema`` raises (e.g. source.discover() blows up after
    check() already succeeded), run_discover must:
      - record the source as failed in DiscoverState with the error string,
      - append a matching SourceDiscoveryResult(status='failed'),
      - continue processing subsequent sources.
    (discover.py:251-269)"""
    from feather_flow import discover as discover_mod
    from feather_flow.discover_state import DiscoverState

    cfg = load_config(_setup_sqlite_project(project), validate=False)

    def _boom(source, target_dir):
        raise RuntimeError("schema serialization blew up")

    monkeypatch.setattr(discover_mod, "_write_schema", _boom)

    report = run_discover(
        cfg,
        project.root,
        refresh=False,
        retry_failed=False,
        prune=False,
    )

    assert report.succeeded_count == 0
    assert report.failed_count == 1
    assert len(report.results) == 1
    result = report.results[0]
    assert result.status == "failed"
    assert "schema serialization blew up" in (result.error or "")

    # State is also updated so retry-failed picks it up next run.
    state = DiscoverState.load(project.root)
    # There's exactly one source in this project.
    name, entry = next(iter(state.sources.items()))
    assert entry["status"] == "failed"
    assert "schema serialization blew up" in entry.get("error", "")


def test_run_discover_with_refresh_ignores_cached_state(project):
    """A second run with refresh=True re-discovers a previously-discovered source."""
    cfg = load_config(_setup_sqlite_project(project), validate=False)

    # First run — establishes state.
    run_discover(cfg, project.root, refresh=False, retry_failed=False, prune=False)

    # Second run with refresh — should re-discover, not return cached.
    report = run_discover(
        cfg, project.root, refresh=True, retry_failed=False, prune=False
    )

    assert report.succeeded_count == 1
    assert report.cached_count == 0


def test_run_discover_second_run_without_refresh_reports_cached(project):
    cfg = load_config(_setup_sqlite_project(project), validate=False)

    run_discover(cfg, project.root, refresh=False, retry_failed=False, prune=False)
    report = run_discover(
        cfg, project.root, refresh=False, retry_failed=False, prune=False
    )

    assert report.cached_count == 1
    assert report.succeeded_count == 0


def test_run_discover_with_prune_removes_state_and_files_for_removed_sources(project):
    cfg = load_config(_setup_sqlite_project(project), validate=False)

    # Seed a state entry for a now-removed source with a file on disk.
    state = DiscoverState.load(project.root)
    old_path = project.root / "schemas-sqlite-stale.json"
    old_path.write_text("[]")
    state.record_ok(
        name="stale",
        type_="sqlite",
        fingerprint="sqlite:/non/existent",
        table_count=1,
        output_path=old_path,
    )
    state.save()

    report = run_discover(
        cfg, project.root, refresh=False, retry_failed=False, prune=True
    )

    assert report.pruned_count >= 1
    assert not old_path.exists()

    state = DiscoverState.load(project.root)
    assert "stale" not in state.sources
