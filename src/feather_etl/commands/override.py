"""`feather extract override` subcommand group — manage per-table extract overrides.

Commands
--------
feather extract override set <table> [--window-strategy X] [--window-column Y]
                                      [--window-grain Z] [--partition-on W]
feather extract override clear <table>
feather extract override list
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from feather_etl.commands._common import _load_and_validate

override_app = typer.Typer(
    name="override",
    help="Manage per-table extract overrides stored in the state DB.",
    invoke_without_command=False,
)


# ---------------------------------------------------------------------------
# WindowStrategy enum — Typer validates incoming values for free.
# ---------------------------------------------------------------------------


class WindowStrategy(str, Enum):
    date = "date"
    pk_range = "pk_range"
    single = "single"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@override_app.command("set")
def set_override(
    table_name: str = typer.Argument(..., help="Table name to override."),
    config: Path = typer.Option("feather.yaml", "--config", help="Config file path."),
    window_strategy: Optional[WindowStrategy] = typer.Option(
        None,
        "--window-strategy",
        help="Extraction window strategy: date | pk_range | single.",
    ),
    window_column: Optional[str] = typer.Option(
        None,
        "--window-column",
        help="Column used as the extraction window cursor.",
    ),
    window_grain: Optional[str] = typer.Option(
        None,
        "--window-grain",
        help="Grain of the extraction window (e.g. 'day', 'hour').",
    ),
    partition_on: Optional[str] = typer.Option(
        None,
        "--partition-on",
        help="Column to partition on when fetching in parallel.",
    ),
) -> None:
    """Set (or update) per-table extract override fields."""
    from feather_etl.state import StateManager

    cfg = _load_and_validate(config, discover_mode=True)
    state = StateManager(cfg.config_dir / "feather_state.duckdb")

    # Build kwargs dict — only include fields the operator explicitly supplied.
    kwargs: dict[str, object] = {}
    if window_strategy is not None:
        kwargs["window_strategy"] = window_strategy.value
    if window_column is not None:
        kwargs["window_column"] = window_column
    if window_grain is not None:
        kwargs["window_grain"] = window_grain
    if partition_on is not None:
        kwargs["partition_on"] = partition_on

    state.set_override(table_name, **kwargs)
    typer.echo(f"Override set for {table_name!r}.")


@override_app.command("clear")
def clear_override(
    table_name: str = typer.Argument(..., help="Table name whose override to remove."),
    config: Path = typer.Option("feather.yaml", "--config", help="Config file path."),
) -> None:
    """Remove the per-table extract override for a table."""
    from feather_etl.state import StateManager

    cfg = _load_and_validate(config, discover_mode=True)
    state = StateManager(cfg.config_dir / "feather_state.duckdb")

    existing = state.get_override(table_name)
    if existing is None:
        typer.echo(f"No override found for {table_name!r}.")
        return

    state.clear_override(table_name)
    typer.echo(f"Override cleared for {table_name!r}.")


@override_app.command("list")
def list_overrides(
    config: Path = typer.Option("feather.yaml", "--config", help="Config file path."),
) -> None:
    """List all per-table extract overrides."""
    from feather_etl.state import StateManager

    cfg = _load_and_validate(config, discover_mode=True)
    state = StateManager(cfg.config_dir / "feather_state.duckdb")

    rows = state.list_overrides()
    if not rows:
        typer.echo("No overrides set.")
        return

    # Tab-aligned table — widths derived from column headers + data.
    col_headers = [
        "table_name",
        "window_strategy",
        "window_column",
        "window_grain",
        "partition_on",
        "created_at",
    ]

    def _fmt_row(r) -> list[str]:
        return [
            r.table_name or "-",
            r.window_strategy or "-",
            r.window_column or "-",
            r.window_grain or "-",
            r.partition_on or "-",
            str(r.created_at)[:19],  # trim microseconds
        ]

    data = [_fmt_row(r) for r in rows]

    # Compute column widths: max of header and each data cell.
    widths = [
        max(len(col_headers[i]), *(len(row[i]) for row in data))
        for i in range(len(col_headers))
    ]

    def _line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    typer.echo(_line(col_headers))
    typer.echo("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in data:
        typer.echo(_line(row))
