"""Unit tests for `feather extract override` CLI subcommand (Task 7 / issue #63).

Each test builds an isolated StateManager over a fresh DuckDB, writes a
minimal feather.yaml pointing at it, and invokes the Typer app via
CliRunner — no mocking.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from feather_flow.cli import app
from feather_flow.state import StateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path) -> Path:
    """Write a minimal feather.yaml in *tmp_path* and return its path.

    The state DB is located at ``tmp_path/feather_state.duckdb``, which is
    the conventional location derived from ``cfg.config_dir``.
    """
    config = {
        "sources": [
            {
                "type": "duckdb",
                "name": "test_source",
                "path": str(tmp_path / "source.duckdb"),
            }
        ],
        "destination": {
            "path": str(tmp_path / "feather_data.duckdb"),
        },
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))

    # Ensure state DB exists and schema is initialised so override commands
    # can write immediately without running setup.
    state_path = tmp_path / "feather_state.duckdb"
    sm = StateManager(state_path)
    sm.init_state()  # creates _extract_overrides table

    return config_file


def _cli(config_file: Path, *args: str):
    """Invoke ``feather <args>`` with the given config file via CliRunner.

    The ``--config`` option is passed after the subcommand name so that
    Typer routes it to the right command handler.  For nested commands like
    ``extract override set`` the config flag must come after all three
    sub-command tokens.
    """
    runner = CliRunner()
    # Split args into the command path and any trailing flags/values.
    # We inject --config just before the first flag (i.e. after the command
    # tokens). The simplest approach: append --config at the end since Typer
    # treats options as positionally flexible within a command's scope.
    return runner.invoke(app, [*args, "--config", str(config_file)])


def _state(config_file: Path) -> StateManager:
    """Return a StateManager pointing at the state DB next to *config_file*."""
    return StateManager(config_file.parent / "feather_state.duckdb")


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


class TestOverrideSet:
    def test_override_set_single_field_writes_one_column(self, tmp_path):
        config_file = _write_config(tmp_path)
        result = _cli(
            config_file,
            "extract",
            "override",
            "set",
            "orders",
            "--window-column",
            "updated_at",
        )
        assert result.exit_code == 0, result.output

        row = _state(config_file).get_override("orders")
        assert row is not None
        assert row.window_column == "updated_at"
        # Other fields stay None — only the one field was supplied.
        assert row.window_strategy is None
        assert row.window_grain is None
        assert row.partition_on is None

    def test_override_set_multiple_fields_writes_all(self, tmp_path):
        config_file = _write_config(tmp_path)
        result = _cli(
            config_file,
            "extract",
            "override",
            "set",
            "invoices",
            "--window-strategy",
            "date",
            "--window-column",
            "invoice_date",
            "--window-grain",
            "day",
            "--partition-on",
            "company_id",
        )
        assert result.exit_code == 0, result.output

        row = _state(config_file).get_override("invoices")
        assert row is not None
        assert row.window_strategy == "date"
        assert row.window_column == "invoice_date"
        assert row.window_grain == "day"
        assert row.partition_on == "company_id"

    def test_override_set_invalid_strategy_rejected_by_typer(self, tmp_path):
        config_file = _write_config(tmp_path)
        result = _cli(
            config_file,
            "extract",
            "override",
            "set",
            "orders",
            "--window-strategy",
            "bogus_value",
        )
        # Typer/Click rejects invalid enum values before reaching our code.
        assert result.exit_code != 0
        # The output should mention the invalid value or provide a usage hint.
        combined = (result.output or "") + (
            result.stderr if hasattr(result, "stderr") else ""
        )
        assert (
            "bogus_value" in combined
            or "invalid" in combined.lower()
            or "Invalid" in combined
        )


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestOverrideClear:
    def test_override_clear_removes_row(self, tmp_path):
        config_file = _write_config(tmp_path)
        sm = _state(config_file)
        sm.set_override("orders", window_column="updated_at")
        assert sm.get_override("orders") is not None  # pre-condition

        result = _cli(config_file, "extract", "override", "clear", "orders")
        assert result.exit_code == 0, result.output
        assert sm.get_override("orders") is None

    def test_override_clear_unknown_table_exits_zero_with_message(self, tmp_path):
        config_file = _write_config(tmp_path)
        result = _cli(config_file, "extract", "override", "clear", "nonexistent_table")
        assert result.exit_code == 0
        assert "nonexistent_table" in (result.output or "")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestOverrideList:
    def test_override_list_empty_prints_no_overrides_set(self, tmp_path):
        config_file = _write_config(tmp_path)
        result = _cli(config_file, "extract", "override", "list")
        assert result.exit_code == 0, result.output
        assert "No overrides set." in (result.output or "")

    def test_override_list_populated_prints_table(self, tmp_path):
        config_file = _write_config(tmp_path)
        sm = _state(config_file)
        sm.set_override("orders", window_strategy="date", window_column="updated_at")
        sm.set_override("invoices", partition_on="company_id")

        result = _cli(config_file, "extract", "override", "list")
        assert result.exit_code == 0, result.output
        output = result.output or ""
        assert "orders" in output
        assert "invoices" in output
        assert "updated_at" in output
        assert "company_id" in output
