"""`feather status` command — thin Typer wrapper over feather_flow.status."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_flow.commands._common import (
    _is_json,
    _load_and_validate,
)
from feather_flow.exceptions import StateDBMissingError
from feather_flow.output import emit
from feather_flow.status import load_status


def status(
    ctx: typer.Context, config: Path = typer.Option("feather.yaml", "--config")
) -> None:
    """Show last run status for all tables."""
    cfg = _load_and_validate(config)
    state_path = cfg.config_dir / "feather_state.duckdb"

    try:
        rows = load_status(state_path)
    except StateDBMissingError:
        typer.echo("No state DB found. Run 'feather setup' first.", err=True)
        raise typer.Exit(code=1)

    if not rows:
        if _is_json(ctx):
            return
        typer.echo("No runs recorded yet.")
        return

    if _is_json(ctx):
        emit(
            [
                {
                    "table_name": row["table_name"],
                    "last_run_at": str(row.get("ended_at", "")),
                    "status": row["status"],
                    "watermark": row.get("watermark"),
                    "rows_loaded": row.get("rows_loaded"),
                }
                for row in rows
            ],
            json_mode=True,
        )
    else:
        typer.echo(f"{'Table':<30} {'Status':<12} {'Rows':<10} {'Last Run'}")
        typer.echo("-" * 75)
        for row in rows:
            typer.echo(
                f"{row['table_name']:<30} {row['status']:<12} "
                f"{row.get('rows_loaded', '-'):<10} {row.get('ended_at', '-')}"
            )
            if row["status"] == "failure" and row.get("error_message"):
                error = str(row["error_message"])
                if len(error) > 80:
                    error = error[:77] + "..."
                typer.echo(f"  Error: {error}")


def register(app: typer.Typer) -> None:
    app.command(name="status")(status)
