"""`feather history` command — thin Typer wrapper over feather_etl.history."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl.commands._common import (
    _is_json,
    _load_and_validate,
)
from feather_etl.exceptions import StateDBMissingError
from feather_etl.history import load_history
from feather_etl.output import emit


def history(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    table: str | None = typer.Option(None, "--table", help="Filter by table name."),
    limit: int = typer.Option(20, "--limit", help="Maximum number of runs to show."),
    trigger: str | None = typer.Option(
        None,
        "--trigger",
        help="Filter by writing verb (run, extract, transform, schedule).",
    ),
) -> None:
    """Show recent run history."""
    cfg = _load_and_validate(config)
    state_path = cfg.config_dir / "feather_state.duckdb"

    try:
        rows = load_history(state_path, table=table, limit=limit, trigger=trigger)
    except StateDBMissingError:
        typer.echo("No state DB found. Run 'feather run' first.", err=True)
        raise typer.Exit(code=1)

    if not rows:
        if not _is_json(ctx):
            typer.echo("No runs recorded yet.")
        return

    if _is_json(ctx):
        emit(
            [
                {
                    "run_id": row["run_id"],
                    "table_name": row["table_name"],
                    "started_at": str(row.get("started_at", "")),
                    "ended_at": str(row.get("ended_at", "")),
                    "status": row["status"],
                    "trigger": row.get("trigger"),
                    "rows_loaded": row.get("rows_loaded"),
                    "error_message": row.get("error_message"),
                }
                for row in rows
            ],
            json_mode=True,
        )
    else:
        typer.echo(
            f"{'Table':<30} {'Status':<12} {'Trigger':<10} "
            f"{'Rows':<10} {'Started':<28} {'Run ID'}"
        )
        typer.echo("-" * 110)
        for row in rows:
            typer.echo(
                f"{row['table_name']:<30} {row['status']:<12} "
                f"{str(row.get('trigger') or '-'):<10} "
                f"{row.get('rows_loaded', '-') or '-'!s:<10} "
                f"{str(row.get('started_at', '-')):<28} {row.get('run_id', '-')}"
            )
            if row.get("error_message"):
                error = str(row["error_message"])
                if len(error) > 80:
                    error = error[:77] + "..."
                typer.echo(f"  Error: {error}")


def register(app: typer.Typer) -> None:
    app.command(name="history")(history)
