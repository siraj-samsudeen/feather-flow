"""Workflow stage 03: discover — feather discover.

Scenarios here exercise `feather discover` across:
- single-source and multi-source configs
- explicit vs. auto-named sources
- discover rename/prune workflows
- heterogeneous source types (DuckDB, SQLite, Postgres)
- non-TTY and ambiguous-match edge cases

Most tests use `stub_viewer_serve` to prevent discover from launching a
real browser. Tests that exercise renamed config files (per --config flag
variability) use `cli(config=<other_path>)`; see Task B0 for the fixture
API.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
import typer
import yaml

from tests.conftest import FIXTURES_DIR


from tests.db_bootstrap import POSTGRES_DSN as CONN_STR, postgres_marker

postgres = postgres_marker()


# ---------------------------------------------------------------------------
# Core discover scenarios (formerly TestDiscover)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_writes_json_with_tables(project, cli, monkeypatch):
    client_db = project.root / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    result = cli("discover")
    assert result.exit_code == 0
    assert "discovered" in result.output

    schema_files = list(project.root.glob("schema_*.json"))
    assert len(schema_files) == 1
    payload = json.loads(schema_files[0].read_text())
    table_names = [entry["table_name"] for entry in payload]
    assert any("SALESINVOICE" in t for t in table_names)
    assert any("CUSTOMERMASTER" in t for t in table_names)


def test_invokes_shared_viewer_runtime_after_writing_json(
    project, cli, tmp_path, monkeypatch
):
    from feather_flow.commands import discover as discover_cmd

    config_dir = tmp_path / "config"
    work_dir = tmp_path / "work"
    config_dir.mkdir()
    work_dir.mkdir()
    shutil.copy2(FIXTURES_DIR / "client.duckdb", config_dir / "client.duckdb")

    config_path = config_dir / "feather.yaml"
    config = {
        "sources": [{"type": "duckdb", "path": "./client.duckdb"}],
        "destination": {"path": str(work_dir / "feather_data.duckdb")},
        "tables": [
            {
                "name": "inventory_group",
                "source_table": "icube.InventoryGroup",
                "target_table": "bronze.inventory_group",
                "strategy": "full",
            },
        ],
    }
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    monkeypatch.chdir(work_dir)

    seen: dict[str, object] = {}

    def fake_serve_and_open(target_dir: Path, preferred_port: int = 8000):
        seen["serve_target_dir"] = target_dir
        seen["preferred_port"] = preferred_port

    monkeypatch.setattr(discover_cmd, "serve_and_open", fake_serve_and_open)

    result = cli("discover", config=config_path)

    assert result.exit_code == 0, result.output
    schema_files = list(work_dir.glob("schema_*.json"))
    assert len(schema_files) == 1
    expected_target_dir = schema_files[0].parent.resolve()
    assert expected_target_dir != config_dir.resolve()
    assert seen["serve_target_dir"] == expected_target_dir
    assert seen["preferred_port"] == 8000
    assert "discovered" in result.output
    assert "Schema viewer" not in result.output


def test_runtime_emitted_output_line_is_surfaced(project, cli, monkeypatch):
    from feather_flow.commands import discover as discover_cmd

    client_db = project.root / "client.duckdb"
    shutil.copy2(FIXTURES_DIR / "client.duckdb", client_db)
    project.write_config(
        sources=[{"type": "duckdb", "name": "icube", "path": str(client_db)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    monkeypatch.setattr(
        discover_cmd,
        "serve_and_open",
        lambda *args, **kwargs: typer.echo("Schema viewer updated."),
    )

    result = cli("discover")

    assert result.exit_code == 0, result.output
    assert "Schema viewer updated." in result.output
    assert "discovered" in result.output


@pytest.mark.usefixtures("stub_viewer_serve")
def test_discover_bad_source_fails(project, cli, monkeypatch):
    monkeypatch.chdir(project.root)
    db = project.root / "empty.duckdb"
    db.write_bytes(b"not a duckdb file")
    project.write_config(
        sources=[{"type": "duckdb", "path": str(db)}],
        destination={"path": str(project.root / "data.duckdb")},
        tables=[
            {
                "name": "t",
                "source_table": "main.t",
                "target_table": "bronze.t",
                "strategy": "full",
            }
        ],
    )
    result = cli("discover")
    assert result.exit_code != 0
    assert "→ FAILED:" in result.output


@pytest.mark.usefixtures("stub_viewer_serve")
def test_writes_auto_named_file_for_sqlite(project, cli, monkeypatch):
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    result = cli("discover")
    assert result.exit_code == 0, result.output

    expected = project.root / "schema_sqlite-source.json"
    assert expected.exists()


@pytest.mark.usefixtures("stub_viewer_serve")
def test_prints_header_source_and_summary_lines(project, cli, monkeypatch):
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    result = cli("discover")
    assert result.exit_code == 0

    lines = [line for line in result.output.splitlines() if line.strip()]
    # header ("Discovering from ...") + [1/1] per-source line + summary line
    assert len(lines) == 3
    assert "Discovering from" in lines[0]
    assert "schema_sqlite-source.json" in lines[1]
    assert "discovered" in lines[2]


@pytest.mark.usefixtures("stub_viewer_serve")
def test_json_payload_has_expected_shape(project, cli, monkeypatch):
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    cli("discover")

    payload = json.loads((project.root / "schema_sqlite-source.json").read_text())
    assert isinstance(payload, list)
    assert len(payload) > 0
    assert set(payload[0].keys()) == {"table_name", "columns"}
    assert isinstance(payload[0]["columns"], list)
    if payload[0]["columns"]:
        assert set(payload[0]["columns"][0].keys()) == {"name", "type"}


@pytest.mark.usefixtures("stub_viewer_serve")
def test_user_name_overrides_auto_derivation(project, cli, monkeypatch):
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "name": "prod-erp", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    result = cli("discover")
    assert result.exit_code == 0
    assert (project.root / "schema_sqlite_prod-erp.json").exists()
    assert not (project.root / "schema_sqlite-source.json").exists()


@pytest.mark.usefixtures("stub_viewer_serve")
def test_user_name_with_unsafe_chars_is_sanitized(project, cli, monkeypatch):
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "name": "prod/erp", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    result = cli("discover")
    assert result.exit_code == 0
    assert (project.root / "schema_sqlite_prod_erp.json").exists()


@pytest.mark.usefixtures("stub_viewer_serve")
def test_second_run_uses_cache_by_default(project, cli, monkeypatch):
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    r1 = cli("discover")
    assert r1.exit_code == 0
    first_mtime = (project.root / "schema_sqlite-source.json").stat().st_mtime_ns

    # Sleep briefly so a rewrite would produce a different mtime.
    time.sleep(0.05)

    r2 = cli("discover")
    assert r2.exit_code == 0
    assert "cached" in r2.output
    # Cached run must NOT rewrite the schema file.
    second_mtime = (project.root / "schema_sqlite-source.json").stat().st_mtime_ns
    assert second_mtime == first_mtime


@pytest.mark.usefixtures("stub_viewer_serve")
def test_zero_tables_writes_empty_array(project, cli, monkeypatch):
    """An empty source still produces a valid file with '[]' content."""
    empty_db = project.root / "source.sqlite"
    conn = sqlite3.connect(empty_db)
    conn.close()

    project.write_config(
        sources=[{"type": "sqlite", "path": "./source.sqlite"}],
        destination={"path": "./feather_data.duckdb"},
        tables=[
            {
                "name": "placeholder",
                "source_table": "sqlite_master",
                "target_table": "bronze.placeholder",
                "strategy": "full",
            }
        ],
    )
    monkeypatch.chdir(project.root)

    result = cli("discover")
    assert result.exit_code == 0, result.output

    out = project.root / "schema_sqlite-source.json"
    assert out.exists()
    assert json.loads(out.read_text()) == []
    assert "0 tables" in result.output


# ---------------------------------------------------------------------------
# Prune output (formerly TestDiscoverPruneOutput)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_prune_does_not_emit_discovering_header(project, cli, monkeypatch):
    """Regression: --prune must NOT emit the 'Discovering from ...' header.

    The pre-refactor wrapper short-circuited prune mode before the header
    block. The Task 7 extraction accidentally moved the header above the
    short-circuit. Lock the contract here.
    """
    from feather_flow.discover_state import DiscoverState

    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    # Seed a state entry for a now-removed source with a file on disk,
    # so prune actually has work to do.
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

    result = cli("discover", "--prune")

    assert result.exit_code == 0
    assert "Discovering from" not in result.output, (
        f"--prune must not emit the 'Discovering from' header.\n"
        f"Got output:\n{result.output}"
    )
    assert "Pruned: schemas-sqlite-stale.json" in result.output
    assert "Pruned 1 removed/orphaned entries." in result.output


# ---------------------------------------------------------------------------
# Auto-enum permission error (formerly TestDiscoverAutoEnumPermissionError)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_empty_enumeration_records_failed_with_hint(project, cli, monkeypatch):
    """If list_databases() returns [], record FAILED with remediation hint (E1)."""
    from unittest.mock import MagicMock
    from feather_flow.sources.sqlserver import SqlServerSource

    # Patch list_databases to return [].
    monkeypatch.setattr(SqlServerSource, "list_databases", lambda self: [])
    # Patch pyodbc.connect to return a mock connection that closes cleanly.
    import pyodbc

    mock_conn = MagicMock()
    monkeypatch.setattr(pyodbc, "connect", lambda *args, **kwargs: mock_conn)

    cfg_text = """
sources:
  - name: erp
    type: sqlserver
    host: db.example.com
    user: u
    password: p
destination:
  path: ./out.duckdb
tables: []
"""
    (project.root / "feather.yaml").write_text(cfg_text)
    monkeypatch.chdir(project.root)

    r = cli("discover")
    assert r.exit_code == 2
    combined_output = r.output + (getattr(r, "stderr", "") or "")
    assert "Found 0 databases" in combined_output
    assert "VIEW ANY DATABASE" in combined_output


# ---------------------------------------------------------------------------
# Ambiguous rename match (formerly TestRenameAmbiguousMatch)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_ambiguous_fingerprint_match_errors(project, cli, monkeypatch):
    from feather_flow.discover_state import DiscoverState

    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", project.root / "source.sqlite")
    project.write_config(
        sources=[{"type": "sqlite", "name": "c", "path": "./source.sqlite"}],
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
    monkeypatch.chdir(project.root)

    state = DiscoverState.load(project.root)
    fingerprint = f"sqlite:{(project.root / 'source.sqlite').resolve()}"
    for name in ("a", "b"):
        state.record_ok(
            name=name,
            type_="sqlite",
            fingerprint=fingerprint,
            table_count=1,
            output_path=project.root / f"schema_{name}.json",
        )
    state.save()

    result = cli("discover")
    assert result.exit_code == 2
    output = result.output.lower()
    assert "ambiguous" in output
    assert "a" in result.output
    assert "b" in result.output


# ---------------------------------------------------------------------------
# Heterogeneous sources (formerly TestDiscoverHeterogeneousSources)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_single_csv_source_writes_one_file(project, cli, monkeypatch):
    csvs = project.root / "csv"
    shutil.copytree(FIXTURES_DIR / "csv_data", csvs)
    project.write_config(
        sources=[{"name": "sheets", "type": "csv", "path": str(csvs)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    r = cli("discover")
    assert r.exit_code == 0, r.output
    files = sorted(project.root.glob("schema_*.json"))
    assert [f.name for f in files] == ["schema_csv_sheets.json"]
    payload = json.loads(files[0].read_text())
    assert isinstance(payload, list)
    assert len(payload) == 3  # csv_data has 3 files


@pytest.mark.usefixtures("stub_viewer_serve")
def test_heterogeneous_sources_write_one_file_per_source(project, cli, monkeypatch):
    csvs = project.root / "csv"
    shutil.copytree(FIXTURES_DIR / "csv_data", csvs)
    sqlite = project.root / "src.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", sqlite)
    duckdb_f = project.root / "src.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", duckdb_f)

    project.write_config(
        sources=[
            {"name": "sheets", "type": "csv", "path": str(csvs)},
            {"name": "sqlite_db", "type": "sqlite", "path": str(sqlite)},
            {"name": "duck", "type": "duckdb", "path": str(duckdb_f)},
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    r = cli("discover")
    assert r.exit_code == 0, r.output
    names = {p.name for p in project.root.glob("schema_*.json")}
    assert names == {
        "schema_csv_sheets.json",
        "schema_sqlite_sqlite_db.json",
        "schema_duckdb_duck.json",
    }


# ---------------------------------------------------------------------------
# Resume behaviour (formerly TestDiscoverResume)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_second_run_skips_cached(project, cli, monkeypatch):
    sqlite = project.root / "src.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", sqlite)
    project.write_config(
        sources=[{"name": "db", "type": "sqlite", "path": str(sqlite)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    r1 = cli("discover")
    assert r1.exit_code == 0
    first_mtime = (project.root / "schema_sqlite_db.json").stat().st_mtime_ns

    # Touch the schema JSON so we can detect whether it was rewritten.
    time.sleep(0.05)

    r2 = cli("discover")
    assert r2.exit_code == 0
    assert "cached" in r2.output
    # Cached run does NOT rewrite the schema file.
    assert (project.root / "schema_sqlite_db.json").stat().st_mtime_ns == first_mtime


@pytest.mark.usefixtures("stub_viewer_serve")
def test_state_file_written(project, cli, monkeypatch):
    sqlite = project.root / "src.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", sqlite)
    project.write_config(
        sources=[{"name": "db", "type": "sqlite", "path": str(sqlite)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    r = cli("discover")
    assert r.exit_code == 0
    state_path = project.root / "feather_discover_state.json"
    assert state_path.is_file()
    payload = json.loads(state_path.read_text())
    assert payload["schema_version"] == 1
    assert "db" in payload["sources"]
    assert payload["sources"]["db"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Postgres multi-database (Postgres-gated; class retained for shared helpers)
# ---------------------------------------------------------------------------


@postgres
@pytest.mark.usefixtures("stub_viewer_serve")
class TestDiscoverPostgresMultiDatabase:
    def _create_databases(self, names):
        import psycopg2

        conn = psycopg2.connect(CONN_STR)
        conn.autocommit = True
        cur = conn.cursor()
        for n in names:
            cur.execute(f"DROP DATABASE IF EXISTS {n}")
            cur.execute(f"CREATE DATABASE {n}")
        cur.close()
        conn.close()

    def _drop_databases(self, names):
        import psycopg2

        conn = psycopg2.connect(CONN_STR)
        conn.autocommit = True
        cur = conn.cursor()
        for n in names:
            cur.execute(f"DROP DATABASE IF EXISTS {n}")
        cur.close()
        conn.close()

    def test_explicit_databases(self, project, cli, monkeypatch):
        names = ["feather_a", "feather_b"]
        self._create_databases(names)
        try:
            project.write_config(
                sources=[
                    {
                        "name": "wh",
                        "type": "postgres",
                        "host": "localhost",
                        "user": "",
                        "password": "",
                        "databases": names,
                    },
                ],
                destination={"path": str(project.root / "feather_data.duckdb")},
            )
            monkeypatch.chdir(project.root)
            r = cli("discover")
            assert r.exit_code == 0, r.output
            files = {p.name for p in project.root.glob("schema_*.json")}
            assert files == {
                "schema_postgres_wh__feather_a.json",
                "schema_postgres_wh__feather_b.json",
            }
        finally:
            self._drop_databases(names)

    def test_auto_enumerate(self, project, cli, monkeypatch):
        names = ["feather_x", "feather_y"]
        self._create_databases(names)
        try:
            project.write_config(
                sources=[
                    {
                        "name": "wh",
                        "type": "postgres",
                        "host": "localhost",
                        "user": "",
                        "password": "",
                    },
                ],
                destination={"path": str(project.root / "feather_data.duckdb")},
            )
            monkeypatch.chdir(project.root)
            r = cli("discover")
            assert r.exit_code == 0, r.output
            files = {p.name for p in project.root.glob("schema_*.json")}
            for n in names:
                assert f"schema_postgres_wh__{n}.json" in files
        finally:
            self._drop_databases(names)


# ---------------------------------------------------------------------------
# Flags: --refresh, --retry-failed, --prune (formerly TestDiscoverFlags)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_refresh_rewrites_schema_files(project, cli, monkeypatch):
    sqlite = project.root / "src.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", sqlite)
    project.write_config(
        sources=[{"name": "db", "type": "sqlite", "path": str(sqlite)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    cli("discover")
    first_mtime = (project.root / "schema_sqlite_db.json").stat().st_mtime_ns

    time.sleep(0.05)
    r = cli("discover", "--refresh")
    assert r.exit_code == 0
    assert (project.root / "schema_sqlite_db.json").stat().st_mtime_ns > first_mtime


@pytest.mark.usefixtures("stub_viewer_serve")
def test_retry_failed_only_retries_failures(project, cli, monkeypatch):
    """Simulate a failed source then verify --retry-failed retries only it."""
    sqlite = project.root / "src.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", sqlite)
    bogus = project.root / "missing.sqlite"  # doesn't exist → check() fails

    project.write_config(
        sources=[
            {"name": "ok", "type": "sqlite", "path": str(sqlite)},
            {"name": "bad", "type": "sqlite", "path": str(bogus)},
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)

    r1 = cli("discover")
    assert r1.exit_code == 2  # one failed
    ok_mtime = (project.root / "schema_sqlite_ok.json").stat().st_mtime_ns

    # Make the bogus path valid.
    shutil.copy2(sqlite, bogus)

    time.sleep(0.05)
    r2 = cli("discover", "--retry-failed")
    assert r2.exit_code == 0
    # ok was not re-discovered.
    assert (project.root / "schema_sqlite_ok.json").stat().st_mtime_ns == ok_mtime
    # bad now has a schema file.
    assert (project.root / "schema_sqlite_bad.json").is_file()


@pytest.mark.usefixtures("stub_viewer_serve")
def test_prune_removes_orphaned_state_and_file(project, cli, monkeypatch):
    a = project.root / "a.sqlite"
    b = project.root / "b.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", a)
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", b)
    project.write_config(
        sources=[
            {"name": "a", "type": "sqlite", "path": str(a)},
            {"name": "b", "type": "sqlite", "path": str(b)},
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    monkeypatch.chdir(project.root)
    cli("discover")
    assert (project.root / "schema_sqlite_a.json").exists()
    assert (project.root / "schema_sqlite_b.json").exists()

    # Remove 'b' from feather.yaml.
    project.write_config(
        sources=[
            {"name": "a", "type": "sqlite", "path": str(a)},
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    cli("discover")
    # 'b' marked removed in state, file still exists.
    assert (project.root / "schema_sqlite_b.json").exists()

    r = cli("discover", "--prune")
    assert r.exit_code == 0
    assert not (project.root / "schema_sqlite_b.json").exists()
    state = json.loads((project.root / "feather_discover_state.json").read_text())
    assert "b" not in state["sources"]


# ---------------------------------------------------------------------------
# Rename / non-TTY exit-3 flow (retained class — shared renamed-config setup)
# ---------------------------------------------------------------------------


def _write_rename_configs(project) -> tuple[Path, Path]:
    """Write two renamed configs sharing the same SQLite source.

    Returns (first_cfg, renamed_cfg) paths with differing source `name`s:
    `erp` → `erp_main`.
    """
    sqlite = project.root / "src.sqlite"
    shutil.copy2(FIXTURES_DIR / "sample_erp.sqlite", sqlite)

    first_cfg = project.root / "feather_erp.yaml"
    first_cfg.write_text(
        yaml.dump(
            {
                "sources": [{"name": "erp", "type": "sqlite", "path": str(sqlite)}],
                "destination": {"path": str(project.root / "feather_data.duckdb")},
            },
            default_flow_style=False,
        )
    )
    renamed_cfg = project.root / "feather_erp_main.yaml"
    renamed_cfg.write_text(
        yaml.dump(
            {
                "sources": [
                    {"name": "erp_main", "type": "sqlite", "path": str(sqlite)}
                ],
                "destination": {"path": str(project.root / "feather_data.duckdb")},
            },
            default_flow_style=False,
        )
    )
    return first_cfg, renamed_cfg


@pytest.mark.usefixtures("stub_viewer_serve")
class TestRenameNonTtyExit3:
    def test_rename_in_yaml_first_invocation_exits_3(self, project, cli, monkeypatch):
        first_cfg, renamed_cfg = _write_rename_configs(project)
        monkeypatch.chdir(project.root)

        first = cli("discover", config=first_cfg)
        assert first.exit_code == 0, first.output

        second = cli("discover", config=renamed_cfg)
        assert second.exit_code == 3, second.output
        output = second.output.lower()
        assert "rename" in output
        assert "--yes" in output
        assert "--no-renames" in output
        assert "erp" in second.output
        assert "erp_main" in second.output

    def test_yes_flag_migrates_state_and_files(self, project, cli, monkeypatch):
        first_cfg, renamed_cfg = _write_rename_configs(project)
        monkeypatch.chdir(project.root)

        first = cli("discover", config=first_cfg)
        assert first.exit_code == 0, first.output

        second = cli("discover", "--yes", config=renamed_cfg)
        assert second.exit_code == 0, second.output
        assert (project.root / "schema_sqlite_erp_main.json").is_file()
        assert not (project.root / "schema_sqlite_erp.json").exists()

        state = json.loads((project.root / "feather_discover_state.json").read_text())
        assert "erp_main" in state["sources"]
        assert "erp" not in state["sources"]

    def test_no_renames_orphans_old_entry(self, project, cli, monkeypatch):
        first_cfg, renamed_cfg = _write_rename_configs(project)
        monkeypatch.chdir(project.root)

        first = cli("discover", config=first_cfg)
        assert first.exit_code == 0, first.output

        second = cli("discover", "--no-renames", config=renamed_cfg)
        assert second.exit_code == 0, second.output

        state = json.loads((project.root / "feather_discover_state.json").read_text())
        assert state["sources"]["erp"]["status"] == "orphaned"
        assert state["sources"]["erp_main"]["status"] == "ok"

    def test_refresh_flag_bypasses_rename_confirmation(self, project, cli, monkeypatch):
        first_cfg, renamed_cfg = _write_rename_configs(project)
        monkeypatch.chdir(project.root)

        first = cli("discover", config=first_cfg)
        assert first.exit_code == 0, first.output

        refreshed = cli("discover", "--refresh", config=renamed_cfg)
        assert refreshed.exit_code == 0, refreshed.output
        assert "Rename confirmation required" not in refreshed.output
        assert (project.root / "schema_sqlite_erp_main.json").is_file()

        state = json.loads((project.root / "feather_discover_state.json").read_text())
        assert state["sources"]["erp_main"]["status"] == "ok"
        assert state["sources"]["erp"]["status"] == "removed"

    def test_prune_flag_bypasses_rename_confirmation(self, project, cli, monkeypatch):
        first_cfg, renamed_cfg = _write_rename_configs(project)
        monkeypatch.chdir(project.root)

        first = cli("discover", config=first_cfg)
        assert first.exit_code == 0, first.output
        assert (project.root / "schema_sqlite_erp.json").is_file()

        pruned = cli("discover", "--prune", config=renamed_cfg)
        assert pruned.exit_code == 0, pruned.output
        assert "Rename confirmation required" not in pruned.output
        assert not (project.root / "schema_sqlite_erp.json").exists()
        assert not (project.root / "schema_sqlite_erp_main.json").exists()

        state = json.loads((project.root / "feather_discover_state.json").read_text())
        assert "erp" not in state["sources"]


# ---------------------------------------------------------------------------
# Explicit-name vs auto-name discovery (formerly in test_explicit_name_flag.py)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_viewer_serve")
def test_discover_explicit_named_source_writes_typed_filename(
    project, cli, monkeypatch
):
    source_db = project.root / "source.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", source_db)

    project.write_config(
        sources=[
            {"type": "duckdb", "path": str(source_db), "name": "warehouse_primary"}
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
        tables=[],
    )
    monkeypatch.chdir(project.root)

    result = cli("discover")

    assert result.exit_code == 0, result.output
    files = sorted(p.name for p in project.root.glob("schema_*.json"))
    assert files == ["schema_duckdb_warehouse_primary.json"]


@pytest.mark.usefixtures("stub_viewer_serve")
def test_discover_auto_named_source_keeps_auto_derived_filename(
    project, cli, monkeypatch
):
    source_db = project.root / "source.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", source_db)

    project.write_config(
        sources=[{"type": "duckdb", "path": str(source_db)}],
        destination={"path": str(project.root / "feather_data.duckdb")},
        tables=[],
    )
    monkeypatch.chdir(project.root)

    result = cli("discover")

    assert result.exit_code == 0, result.output
    files = sorted(p.name for p in project.root.glob("schema_*.json"))
    assert files == ["schema_duckdb-source.json"]
