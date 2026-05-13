"""`feather setup` command — thin Typer wrapper over feather_etl.setup."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl.commands._common import (
    _is_json,
    _load_and_validate,
)
from feather_etl.output import emit_line
from feather_etl.setup import run_setup


def setup(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    force_views: bool = typer.Option(
        False,
        "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
) -> None:
    """Preview and initialize state DB and schemas. Optional — feather run creates them automatically."""
    cfg = _load_and_validate(config)

    result = run_setup(cfg, force_views=force_views)

    if not _is_json(ctx):
        typer.echo(f"State DB initialized: {result.state_db_path}")
        typer.echo(f"Schemas created in: {result.destination_path}")

    transforms_applied = 0
    if result.transform_results is not None:
        results = result.transform_results
        transforms_applied = sum(1 for r in results if r.status == "success")

        if not _is_json(ctx):
            silver_views = sum(
                1 for r in results if r.schema == "silver" and r.status == "success"
            )
            gold_views = sum(
                1
                for r in results
                if r.schema == "gold" and r.type == "view" and r.status == "success"
            )
            gold_tables = sum(
                1
                for r in results
                if r.schema == "gold" and r.type == "table" and r.status == "success"
            )
            parts = []
            if silver_views:
                parts.append(f"{silver_views} silver view(s)")
            if gold_views:
                parts.append(f"{gold_views} gold view(s)")
            if gold_tables:
                parts.append(f"{gold_tables} gold table(s)")
            typer.echo(f"Transforms applied: {', '.join(parts)}")

            errors = [r for r in results if r.status == "error"]
            for r in errors:
                typer.echo(
                    f"  Transform error: {r.schema}.{r.name} — {r.error}", err=True
                )

    if _is_json(ctx):
        emit_line(
            {
                "state_db": str(result.state_db_path),
                "destination": str(result.destination_path),
                "schemas_created": True,
                "transforms_applied": transforms_applied,
            },
            json_mode=True,
        )


def register(app: typer.Typer) -> None:
    app.command(name="setup")(setup)
