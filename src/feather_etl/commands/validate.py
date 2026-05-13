"""`feather validate` command — thin Typer wrapper over feather_etl.validate."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl.commands._common import (
    _is_json,
    _load_and_validate,
)
from feather_etl.output import emit_line
from feather_etl.validate import run_validate


def validate(
    ctx: typer.Context, config: Path = typer.Option("feather.yaml", "--config")
) -> None:
    """Validate config, test source connection, and write feather_validation.json."""
    cfg = _load_and_validate(config)

    report = run_validate(cfg)

    if not _is_json(ctx):
        for r in report.sources:
            conn_status = "connected" if r.ok else "FAILED"
            typer.echo(f"  Source: {r.type} ({r.label}) — {conn_status}")
            if not r.ok and r.error:
                typer.echo(f"    Details: {r.error}", err=True)

    if _is_json(ctx):
        emit_line(
            {
                "valid": True,
                "tables_count": report.tables_count,
                "source_type": cfg.sources[0].type,
                "destination": str(cfg.destination.path),
                "source_connected": report.all_ok,
            },
            json_mode=True,
        )
    else:
        typer.echo(f"Config valid: {report.tables_count} table(s)")
        typer.echo(f"  Destination: {cfg.destination.path}")
        typer.echo(f"  State: {cfg.config_dir / 'feather_state.duckdb'}")
        for t in cfg.tables:
            typer.echo(f"  Table: {t.name} → {t.target_table} ({t.strategy})")

    if not report.all_ok:
        typer.echo("Source connection failed.", err=True)
        raise typer.Exit(code=2)


def register(app: typer.Typer) -> None:
    app.command(name="validate")(validate)
