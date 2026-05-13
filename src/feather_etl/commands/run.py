"""`feather run` command."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl.commands._common import (
    _is_json,
    _load_and_validate,
)
from feather_etl.output import emit


def run(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    table: str | None = typer.Option(None, "--table", help="Extract only this table."),
    force_views: bool = typer.Option(
        False, "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
    limit: int | None = typer.Option(
        None, "--limit",
        help="Override defaults.row_limit for this invocation.",
    ),
) -> None:
    """Extract all configured tables (or a single table with --table)."""
    from feather_etl.pipeline import run_all

    cfg = _load_and_validate(config)
    if limit is not None:
        cfg.defaults.row_limit = limit
    try:
        results = run_all(cfg, config, table_filter=table, force_views=force_views)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    if _is_json(ctx):
        emit(
            [
                {
                    "table_name": r.table_name,
                    "status": r.status,
                    "rows_loaded": r.rows_loaded,
                    "error_message": r.error_message,
                }
                for r in results
            ],
            json_mode=True,
        )
    else:
        for r in results:
            if r.status == "success":
                typer.echo(f"  {r.table_name}: {r.status} ({r.rows_loaded} rows)")
            elif r.status == "skipped":
                typer.echo(f"  {r.table_name}: skipped (unchanged)")
            else:
                typer.echo(f"  {r.table_name}: {r.status} — {r.error_message}")

        successes = sum(1 for r in results if r.status == "success")
        skipped = sum(1 for r in results if r.status == "skipped")

        parts = [f"{successes}/{len(results)} tables extracted"]
        if skipped:
            parts.append(f"{skipped} skipped")
        typer.echo(f"\n{', '.join(parts)}.")

    failures = sum(
        1
        for r in results
        if r.status == "failure" or (r.status == "skipped" and r.error_message)
    )
    if failures > 0:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    app.command(name="run")(run)
