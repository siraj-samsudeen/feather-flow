"""Shared helpers for feather_etl command modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    pass


def _is_json(ctx: typer.Context) -> bool:
    """Read --json flag from Typer context."""
    return ctx.ensure_object(dict).get("json_mode", False)


def _load_and_validate(
    config_path: Path,
    discover_mode: bool = False,
):
    """Load config, validate, write validation JSON. Raises on failure.

    When *discover_mode* is True the loader skips table validation because
    ``feather discover`` runs **before** curation.json exists.
    """
    from feather_etl.config import load_config, write_validation_json

    try:
        cfg = load_config(
            config_path,
            validate=not discover_mode,
        )
        write_validation_json(config_path, cfg)
        return cfg
    except (ValueError, FileNotFoundError) as e:
        if isinstance(e, FileNotFoundError):
            typer.echo(f"Config file not found: {config_path}", err=True)
        else:
            write_validation_json(config_path, None, errors=[str(e)])
            typer.echo(f"Validation failed: {e}", err=True)
        raise typer.Exit(code=1)
