"""Typer adapter for feather init."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl import __version__
from feather_etl.commands.init import core


def register(app: typer.Typer) -> None:
    @app.command(name="init")
    def init(
        dir: str | None = typer.Argument(None),
        dev: bool = typer.Option(False, "--dev"),
    ) -> None:
        """Stamp feather project files into the current directory."""
        target = Path(dir) if dir else Path.cwd()
        try:
            result = core.init_project(target, dev=dev)
        except core.DevModeUnavailableError as e:
            typer.echo(
                f"--dev requires a local feather-etl checkout. Checked: {e.checked_path}. "
                "No pyproject.toml found there. Either install feather-etl from a local source, "
                "or omit --dev to use PyPI.",
                err=True,
            )
            raise typer.Exit(code=1)
        for m in result.messages:
            typer.echo(m, err=True)
        if result.messages:
            typer.echo("", err=True)
        if result.feather_etl_source_path:
            typer.echo(f"feather-etl: editable from {result.feather_etl_source_path}", err=True)
        else:
            typer.echo(f"feather-etl: pinned to PyPI (>={__version__})", err=True)
