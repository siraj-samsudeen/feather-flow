"""Integration: feather_flow.validate.run_validate — exercises validate + config
+ sources connection probing.
"""

from __future__ import annotations

from feather_flow.config import load_config
from feather_flow.validate import run_validate


def _setup_sqlite_project(project, *, valid_path: bool = True):
    """Build a minimal feather project with a SQLite source. Returns config path."""
    if valid_path:
        project.copy_fixture("sample_erp.sqlite")
        # Rename copied fixture to the path referenced by the config.
        (project.root / "sample_erp.sqlite").rename(project.root / "source.sqlite")
        source_path = "./source.sqlite"
    else:
        source_path = "./missing.sqlite"

    project.write_config(
        sources=[{"type": "sqlite", "name": "erp", "path": source_path}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("erp", "orders", "orders")])
    return project.config_path


def test_reports_each_source_status_when_all_ok(project):
    cfg = load_config(_setup_sqlite_project(project))
    report = run_validate(cfg)

    assert report.all_ok is True
    assert report.tables_count == 1
    assert len(report.sources) == 1
    assert report.sources[0].type == "sqlite"
    assert report.sources[0].ok is True
    assert report.sources[0].error is None


def test_returns_all_ok_false_when_any_source_check_fails(project):
    cfg = load_config(_setup_sqlite_project(project, valid_path=False))
    report = run_validate(cfg)

    assert report.all_ok is False
    assert report.sources[0].ok is False


def test_propagates_last_error_for_failed_sources(project):
    cfg = load_config(_setup_sqlite_project(project))

    # Replace the source with a fake that fails with a diagnostic error —
    # mirrors the real-world case (DB sources set `_last_error` on failure)
    # that the `validate` command surfaces as "Details: ...".
    class FakeFailingSource:
        type = "duckdb"
        path = "/nope.duckdb"

        def __init__(self) -> None:
            self._last_error = "TLS Provider: certificate verify failed"

        def check(self) -> bool:
            return False

    cfg.sources[0] = FakeFailingSource()
    report = run_validate(cfg)

    assert report.sources[0].ok is False
    assert report.sources[0].error == "TLS Provider: certificate verify failed"


def test_label_uses_path_for_file_sources(project):
    cfg = load_config(_setup_sqlite_project(project))
    report = run_validate(cfg)

    # File-based sources expose `path` — the label should be the path.
    assert "source.sqlite" in str(report.sources[0].label)
