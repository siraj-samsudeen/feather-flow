"""Workflow stage 02: feather validate — config parsing and validation errors.

Scenarios in this file cover the CLI-visible behavior of `feather validate`:
friendly error messages, structural rejections, and happy-path smoke.
"""

from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from feather_flow.cli import app


def test_validate_missing_config_shows_friendly_error(tmp_path, monkeypatch):
    """S13/BUG-3: running validate in a directory with no feather.yaml
    must print 'Config file not found', not a Python traceback.

    This test does NOT use the `project`/`cli` fixtures because the whole
    point is that there is no config at all — `cli` would pass --config
    pointing at a path that doesn't exist, which changes what's being tested.
    """
    runner = CliRunner()

    # Arrange: a completely empty directory.
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "feather.yaml").exists()

    # Act: validate with no flags, so it uses CWD discovery.
    result = runner.invoke(app, ["validate"])

    # Assert: exits non-zero with a friendly message, not a traceback.
    assert result.exit_code != 0, result.output
    assert "Config file not found" in result.output
    assert "Traceback" not in result.output


def test_csv_source_rejects_file_path(project, cli):
    """S16a: CSV source.path must be a directory, not a file.

    The CSV source type globs `path/*.csv`, so passing a file is a
    configuration error. validate must catch it (non-zero exit).
    """
    # Arrange: a file at the path (not a directory).
    csv_file = project.root / "source.csv"
    csv_file.write_text("a,b\n1,2\n")
    assert csv_file.is_file()

    project.write_config(
        sources=[{"type": "csv", "name": "csvs", "path": "./source.csv"}],
        destination={"path": "./feather_data.duckdb"},
    )

    # Act + assert: validate rejects the config.
    result = cli("validate")
    assert result.exit_code != 0, (
        f"validate unexpectedly succeeded; output was:\n{result.output}"
    )


def test_valid_config(project, cli):
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])

    result = cli("validate")
    assert result.exit_code == 0
    assert (project.root / "feather_validation.json").exists()


def test_invalid_config(project, cli):
    bad_config = project.root / "feather.yaml"
    bad_config.write_text(
        yaml.dump(
            {
                "sources": [{"type": "mongodb", "path": "/nope"}],
                "destination": {"path": "./data.duckdb"},
            }
        )
    )
    result = cli("validate")
    assert result.exit_code != 0


def test_missing_config_file_shows_friendly_error(project, cli):
    """BUG-3: Missing feather.yaml should not show a Python traceback."""
    missing = project.root / "nope.yaml"
    result = cli("validate", config=missing)
    assert result.exit_code != 0
    assert "Config file not found" in result.output


def test_validate_prints_details_on_source_failure(project, cli, monkeypatch):
    """When source.check() fails, the real exception must be surfaced as 'Details: ...'.

    Regression for siraj-samsudeen/feather-flow#2: the CLI used to print only
    'Source connection failed.' and swallow the real error, making remote DB
    misconfig (TLS, creds, driver) impossible to diagnose without code changes.
    """
    import feather_flow.config as _config_mod

    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])

    real_load = _config_mod.load_config

    class FakeFailingSource:
        def __init__(self):
            self._last_error = "TLS Provider: certificate verify failed"
            self.type = "duckdb"

        def check(self) -> bool:
            return False

    def fake_load_config(path, **kwargs):
        cfg = real_load(path, **kwargs)
        cfg.sources[0] = FakeFailingSource()
        return cfg

    monkeypatch.setattr(_config_mod, "load_config", fake_load_config)

    result = cli("validate")
    assert result.exit_code == 2
    assert "Source connection failed." in result.output
    assert "Details: TLS Provider: certificate verify failed" in result.output


def test_validate_shows_state_path(project, cli):
    """UX-9: validate output should show where the state DB will be created."""
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])

    result = cli("validate")
    assert result.exit_code == 0
    assert "State:" in result.output
    assert "feather_state.duckdb" in result.output


def test_validate_json_outputs_json(project, cli):
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": "./client.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])

    result = cli("--json", "validate")
    assert result.exit_code == 0
    parsed = json.loads(result.output.strip())
    assert parsed["valid"] is True
    assert "tables_count" in parsed
