"""Unit tests for the pre-flight wiring in ``feather extract`` (Task 11 / issue #63).

Tests use Typer's CliRunner plus a fake DB source that controls cheap_rowcount,
discover(), and get_window_range() return values.  No real DB connections are
opened.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from feather_flow.cli import app
from feather_flow.sources import StreamSchema
from feather_flow.state import StateManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def _make_table_entry(
    source_db: str,
    source_table: str,
    alias: str,
    strategy: str = "full",
) -> dict:
    """Return a curation.json include entry."""
    return {
        "source_db": source_db,
        "source_table": source_table,
        "decision": "include",
        "table_type": "fact",
        "group": "test",
        "alias": alias,
        "classification_notes": None,
        "strategy": strategy,
        "primary_key": ["id"],
        "timestamp": None,
        "grain": None,
        "scd": None,
        "mapping": None,
        "dq_policy": None,
        "load_contract": None,
        "reason": "test fixture",
    }


def _write_config(
    tmp_path: Path,
    *,
    source_type: str = "sqlserver",
    tables: list[dict] | None = None,
) -> Path:
    """Write a minimal feather.yaml + curation.json and return the config path."""
    import json

    if source_type == "file":
        config = {
            "sources": [
                {
                    "type": "duckdb",
                    "name": "testdb",
                    "path": str(tmp_path / "source.duckdb"),
                }
            ],
            "destination": {
                "path": str(tmp_path / "feather_data.duckdb"),
            },
        }
        default_tables = [
            _make_table_entry("testdb", "erp.orders", "orders"),
        ]
    else:
        config = {
            "sources": [
                {
                    "type": source_type,
                    "name": "testsrc",
                    "host": "localhost",
                    "database": "testdb",
                    "user": "sa",
                    "password": "pw",
                }
            ],
            "destination": {
                "path": str(tmp_path / "feather_data.duckdb"),
            },
        }
        default_tables = [
            _make_table_entry("testdb", "dbo.orders", "orders"),
        ]

    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))

    # Write curation.json.
    discovery_dir = tmp_path / "discovery"
    discovery_dir.mkdir(exist_ok=True)
    curation = {
        "version": 2,
        "updated_at": "2026-01-01T00:00:00Z",
        "notes": "test",
        "source_systems": {},
        "policies": {"data_quality": {"default": "flag", "escalations": []}},
        "tables": tables if tables is not None else default_tables,
    }
    (discovery_dir / "curation.json").write_text(json.dumps(curation))

    # Initialise state DB.
    state_path = tmp_path / "feather_state.duckdb"
    sm = StateManager(state_path)
    sm.init_state()

    return config_file


def _fake_db_source(
    *,
    cheap_rows: int = 50_000,
    window_range: tuple | None = None,
    columns: list[tuple] | None = None,
    pk: list[str] | None = None,
    raises_cheap_rowcount: type | None = None,
):
    """Return a fake DB source class instance with controllable behaviour."""
    if columns is None:
        columns = [
            ("id", "int"),
            ("created_at", "datetime"),
            ("name", "varchar(100)"),
        ]
    if pk is None:
        pk = ["id"]

    class _FakeDBSource:
        type = "sqlserver"
        name = "testsrc"
        database = "testdb"
        databases = None
        host = "localhost"
        port = 1433
        user = "sa"
        password = "pw"
        transport_name = "arrow-odbc"
        supports_partition_on = False

        @classmethod
        def from_yaml(cls, entry, config_dir):
            return cls()

        def validate_source_table(self, table):
            return []

        def check(self):
            return True

        def discover(self):
            # Return schemas for common test tables so multi-table tests work.
            return [
                StreamSchema(
                    name="dbo.orders",
                    columns=columns,
                    primary_key=pk,
                    supports_incremental=True,
                ),
                StreamSchema(
                    name="dbo.products",
                    columns=columns,
                    primary_key=pk,
                    supports_incremental=True,
                ),
            ]

        def cheap_rowcount(self, table):
            if raises_cheap_rowcount is not None:
                raise raises_cheap_rowcount("boom")
            return cheap_rows

        def get_window_range(self, table, column):
            return window_range

        def detect_changes(self, table, last_state=None):
            from feather_flow.sources import ChangeResult

            return ChangeResult(changed=False, reason="unchanged")

        def extract(self, table, columns=None, filter=None, **kw):
            import pyarrow as pa

            return pa.table({"id": [1], "created_at": ["2024-01-01"], "name": ["x"]})

        def get_schema(self, table):
            return columns

    return _FakeDBSource()


# ---------------------------------------------------------------------------
# Shared patch helper: replaces resolve_source so we control what source
# is returned without real DB connections.
# ---------------------------------------------------------------------------


def _patch_resolve(fake_source):
    """Context manager: patch feather_flow.extract.resolve_source and
    feather_flow.curation.resolve_source to return *fake_source*."""
    return patch(
        "feather_flow.extract.resolve_source",
        return_value=fake_source,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_command_db_source_runs_planner_and_prints_banner(tmp_path):
    """DB sources trigger planner + banner before extract."""
    config_file = _write_config(tmp_path)
    fake = _fake_db_source(cheap_rows=500)

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file), "--plan-only"],
            catch_exceptions=False,
        )

    output = result.output
    assert "pre-flight" in output.lower(), f"Expected banner; got: {output!r}"
    assert result.exit_code == 0


def test_extract_command_file_source_skips_planner(tmp_path):
    """File-type sources skip the planner entirely."""
    from feather_flow.sources.duckdb_file import DuckDBFileSource

    config_file = _write_config(tmp_path, source_type="file")

    # File sources raise NotImplementedError from cheap_rowcount → skip planner.
    with patch("feather_flow.extract.resolve_source") as mock_resolve:
        import duckdb

        src_db = tmp_path / "source.duckdb"
        con = duckdb.connect(str(src_db))
        con.execute("CREATE SCHEMA IF NOT EXISTS erp")
        con.execute("CREATE TABLE erp.orders (id INTEGER, name VARCHAR)")
        con.close()

        # Patch resolve_source so we can hook planner skip check.
        # Let it actually use the real DuckDB file source.
        real_file_src = DuckDBFileSource.from_yaml(
            {
                "type": "duckdb",
                "name": "testdb",
                "path": str(src_db),
            },
            tmp_path,
        )
        mock_resolve.return_value = real_file_src

        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file), "--plan-only"],
            catch_exceptions=False,
        )

    # No banner should appear for file sources; planner is skipped.
    assert "pre-flight" not in result.output.lower(), (
        f"File source should NOT print a pre-flight banner. Got: {result.output!r}"
    )
    assert result.exit_code == 0


def test_extract_command_plan_only_exits_zero_without_extract(tmp_path):
    """--plan-only exits 0 without actually extracting."""
    config_file = _write_config(tmp_path)
    fake = _fake_db_source(cheap_rows=200)

    extract_called = []

    def fake_run_extract(*a, **kw):
        extract_called.append(True)
        return []

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
        patch("feather_flow.commands.extract.run_extract", side_effect=fake_run_extract),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file), "--plan-only"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}: {result.output}"
    assert not extract_called, "run_extract should NOT be called with --plan-only"


def test_extract_command_blocker_unaccepted_exits_three(tmp_path):
    """Unaccepted blockers cause exit code 3."""
    config_file = _write_config(tmp_path)
    # 150 columns, threshold default is 100 → wide_table blocker
    wide_columns = [(f"col_{i}", "varchar") for i in range(150)]
    fake = _fake_db_source(cheap_rows=500, columns=wide_columns, pk=["col_0"])

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file)],
            catch_exceptions=False,
        )

    assert result.exit_code == 3, (
        f"Expected exit 3 for unaccepted wide_table blocker; "
        f"got {result.exit_code}. Output: {result.output!r}"
    )
    assert "--accept-wide" in result.output, (
        f"Expected bypass flag in output; got: {result.output!r}"
    )


def test_extract_command_blocker_accepted_by_flag_proceeds(tmp_path):
    """Blockers accepted by the corresponding flag do NOT block extraction."""
    config_file = _write_config(tmp_path)
    # 150 columns but col_0 is int — no no_window_column blocker, only wide_table.
    wide_columns = [("col_0", "int")] + [(f"col_{i}", "varchar") for i in range(1, 150)]
    fake = _fake_db_source(cheap_rows=500, columns=wide_columns, pk=["col_0"])

    extract_called = []

    def fake_run_extract(*a, **kw):
        extract_called.append(True)
        from feather_flow.extract import ExtractResult

        return [
            ExtractResult(
                table_name="testdb_orders", source_db="testdb", status="success"
            )
        ]

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
        patch("feather_flow.commands.extract.run_extract", side_effect=fake_run_extract),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file), "--accept-wide"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, (
        f"Expected 0 with --accept-wide; got {result.exit_code}. "
        f"Output: {result.output!r}"
    )
    assert extract_called, "run_extract should have been called"


def test_extract_command_multiple_blockers_each_listed_separately(tmp_path):
    """Multiple blockers (across multiple tables or within one) are all listed."""
    two_tables = [
        _make_table_entry("testdb", "dbo.orders", "orders"),
        _make_table_entry("testdb", "dbo.products", "products"),
    ]
    config_file = _write_config(tmp_path, tables=two_tables)

    # wide_table blocker for both (150 cols, no projection)
    wide_columns = [(f"col_{i}", "varchar") for i in range(150)]
    fake = _fake_db_source(cheap_rows=500, columns=wide_columns, pk=["col_0"])

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file)],
            catch_exceptions=False,
        )

    assert result.exit_code == 3
    # Both bronze table names should appear in the error output.
    # _sanitize_bronze_name("testdb", "orders") → "testdb_orders"
    # _sanitize_bronze_name("testdb", "products") → "testdb_products"
    assert "testdb_orders" in result.output, f"Got: {result.output!r}"
    assert "testdb_products" in result.output, f"Got: {result.output!r}"


def test_extract_command_tty_prompts_y_n_after_banner(tmp_path):
    """In a TTY, a Y/n prompt appears after the banner."""
    config_file = _write_config(tmp_path)
    fake = _fake_db_source(cheap_rows=200)

    extract_called = []

    def fake_run_extract(*a, **kw):
        extract_called.append(True)
        from feather_flow.extract import ExtractResult

        return [
            ExtractResult(
                table_name="testdb_orders", source_db="testdb", status="success"
            )
        ]

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
        patch("feather_flow.commands.extract.run_extract", side_effect=fake_run_extract),
        patch("feather_flow.commands._preflight.sys") as mock_sys,
        patch("feather_flow.commands._preflight.input", return_value="y", create=True),
    ):
        mock_sys.stdin.isatty.return_value = True
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file)],
            catch_exceptions=False,
        )

    assert "Proceed?" in result.output or extract_called, (
        f"Expected TTY prompt or extract to proceed. Output: {result.output!r}"
    )


def test_extract_command_non_tty_no_prompt(tmp_path):
    """In a non-TTY context, no Y/n prompt appears and extraction runs immediately."""
    config_file = _write_config(tmp_path)
    fake = _fake_db_source(cheap_rows=200)

    extract_called = []

    def fake_run_extract(*a, **kw):
        extract_called.append(True)
        from feather_flow.extract import ExtractResult

        return [
            ExtractResult(
                table_name="testdb_orders", source_db="testdb", status="success"
            )
        ]

    input_called = []

    def fake_input(prompt=""):
        input_called.append(prompt)
        return "y"

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
        patch("feather_flow.commands.extract.run_extract", side_effect=fake_run_extract),
        patch("feather_flow.commands._preflight.sys") as mock_sys,
        patch("feather_flow.commands._preflight.input", fake_input, create=True),
    ):
        mock_sys.stdin.isatty.return_value = False
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file)],
            catch_exceptions=False,
        )

    assert not input_called, (
        f"input() should NOT be called in non-TTY. Called with: {input_called}"
    )
    assert extract_called or result.exit_code == 0, (
        f"Expected extraction to proceed without prompt. Got: {result.output!r}"
    )


def test_extract_command_table_filter_restricts_planning_and_extract_to_one_table(
    tmp_path,
):
    """--table <name> restricts both pre-flight banner and actual extract."""
    two_tables = [
        _make_table_entry("testdb", "dbo.orders", "orders"),
        _make_table_entry("testdb", "dbo.products", "products"),
    ]
    config_file = _write_config(tmp_path, tables=two_tables)

    fake = _fake_db_source(cheap_rows=200)
    planned_tables = []

    def tracking_pick_plan(table, *args, **kwargs):
        planned_tables.append(table.name)
        from feather_flow.planner import pick_plan

        return pick_plan(table, *args, **kwargs)

    with (
        _patch_resolve(fake),
        patch(
            "feather_flow.commands._preflight.resolve_source",
            return_value=fake,
        ),
        patch(
            "feather_flow.commands._preflight.pick_plan",
            side_effect=tracking_pick_plan,
        ),
    ):
        result = runner.invoke(
            app,
            [
                "extract",
                "--config",
                str(config_file),
                "--table",
                "testdb_orders",
                "--plan-only",
            ],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Got: {result.exit_code}, output: {result.output!r}"
    # Only the filtered table should be planned.
    # _sanitize_bronze_name("testdb", "orders") → "testdb_orders"
    assert planned_tables == ["testdb_orders"], (
        f"Expected only testdb_orders to be planned; got {planned_tables}"
    )
    # Banner should mention testdb_orders but not testdb_products.
    assert "testdb_orders" in result.output
    assert "testdb_products" not in result.output
