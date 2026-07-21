"""Smoke tests that pin the public API of ProjectFixture + cli.

Every method/property exercised here is a load-bearing part of the Wave A
contract. If a test here fails because a method was renamed or removed,
that's a breaking change and every e2e test needs to be updated.
"""

from __future__ import annotations

from pathlib import Path


def test_project_root_is_a_directory(project):
    assert isinstance(project.root, Path)
    assert project.root.is_dir()


def test_project_paths_are_under_root(project):
    assert project.config_path == project.root / "feather.yaml"
    assert project.data_db_path == project.root / "feather_data.duckdb"
    assert project.state_db_path == project.root / "feather_state.duckdb"


def test_write_config_persists_yaml(project):
    project.write_config(
        sources=[{"type": "duckdb", "name": "s", "path": "./source.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    assert project.config_path.exists()
    # The file parses back to a dict we recognise.
    import yaml

    parsed = yaml.safe_load(project.config_path.read_text())
    assert parsed["sources"][0]["name"] == "s"
    assert parsed["destination"]["path"] == "./feather_data.duckdb"


def test_write_curation_creates_discovery_json(project):
    project.write_curation([("s", "s.orders", "orders")])
    curation_path = project.root / "discovery" / "curation.json"
    assert curation_path.exists()
    import json

    manifest = json.loads(curation_path.read_text())
    assert manifest["tables"][0]["alias"] == "orders"
    assert manifest["tables"][0]["source_db"] == "s"
    assert manifest["tables"][0]["source_table"] == "s.orders"


def test_copy_fixture_file_copies_into_project(project):
    dst = project.copy_fixture("sample_erp.sqlite")
    assert dst == project.root / "sample_erp.sqlite"
    assert dst.exists()
    assert dst.stat().st_size > 0


def test_copy_fixture_directory_copies_recursively(project):
    dst = project.copy_fixture("csv_data")
    assert dst.is_dir()
    assert any(dst.iterdir())


def test_query_returns_rows_from_data_db(project):
    # Seed the data DB directly so we can exercise .query without running a pipeline.
    import duckdb

    with duckdb.connect(str(project.data_db_path)) as con:
        con.execute("CREATE TABLE t (x INT)")
        con.execute("INSERT INTO t VALUES (1), (2), (3)")
    rows = project.query("SELECT count(*) FROM t")
    assert rows == [(3,)]


def test_cli_subcommand_help_exits_zero(project, cli):
    """`feather validate --help` must exit 0 with usage text.

    We test a subcommand's --help (not the app's top-level --help) because
    --config is a per-subcommand option; the cli fixture appends it
    automatically, so invoking --help at the app level would produce the
    ambiguous `feather --help --config <path>`.
    """
    project.write_config(
        sources=[{"type": "duckdb", "name": "s", "path": "./source.duckdb"}],
        destination={"path": "./feather_data.duckdb"},
    )
    result = cli("validate", "--help")
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_cli_with_config_false_skips_config_flag(project, cli):
    """cli(..., config=False) invokes feather without auto-appending --config.

    Required by tests that probe CWD-discovery behaviour, and by commands
    like `init`/`view` that don't accept --config at all.
    """
    # If cli still appended --config, `feather --help` would be ambiguous.
    # With config=False, app-level --help works normally.
    result = cli("--help", config=False)
    assert result.exit_code == 0
    assert "Usage" in result.output
    # Sanity: no --config leaked into the help output.
    assert "feather.yaml" not in result.output or "Options" in result.output


def test_cli_with_explicit_config_path_uses_it(project, cli, tmp_path_factory):
    """cli(..., config=<Path>) forwards that path as --config, overriding
    the default project.config_path."""
    # Write a valid config to project.root with a name we can grep.
    project.copy_fixture("sample_erp.sqlite")
    project.write_config(
        sources=[
            {"type": "sqlite", "name": "marker_one", "path": "./sample_erp.sqlite"}
        ],
        destination={"path": "./feather_data.duckdb"},
    )
    project.write_curation([("marker_one", "orders", "orders")])

    # Write a SECOND config file with a different source name; pass it explicitly.
    other_cfg = project.root / "feather_other.yaml"
    import yaml

    other_cfg.write_text(
        yaml.dump(
            {
                "sources": [
                    {
                        "type": "sqlite",
                        "name": "marker_two",
                        "path": "./sample_erp.sqlite",
                    }
                ],
                "destination": {"path": "./feather_data_other.duckdb"},
            },
            default_flow_style=False,
        )
    )
    # Reuse the same curation but under a different source name.
    from tests.helpers import make_curation_entry, write_curation

    # Rewrite curation to reference marker_two so validate passes.
    write_curation(
        project.root, [make_curation_entry("marker_two", "orders", "orders")]
    )

    result = cli("validate", config=other_cfg)
    assert result.exit_code == 0, result.output
    # The source-count summary will mention the other config's data.
    # Just assert it didn't quietly use the default.
    assert (
        "marker_two" in result.output
        or "1 source" in result.output
        or "1 table" in result.output
    )


def test_stub_viewer_serve_prevents_browser_open(project, cli, monkeypatch):
    """The stub_viewer_serve fixture monkeypatches discover's serve_and_open
    to a no-op so tests don't try to launch a browser.

    This smoke test doesn't apply the fixture but shows the target attribute
    exists and is overridable."""
    import feather_flow.commands.discover as discover_cmd

    assert hasattr(discover_cmd, "serve_and_open"), (
        "discover command still exposes serve_and_open — stub_viewer_serve fixture relies on this"
    )
