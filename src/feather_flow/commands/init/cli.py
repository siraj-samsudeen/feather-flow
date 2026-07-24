"""Typer adapter for feather init."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_flow.commands.init import core


def register(app: typer.Typer) -> None:
    @app.command(name="init")
    def init(dir: str | None = typer.Argument(default=None)) -> None:
        """Stamp feather project files into the current directory."""
        target = Path.cwd() if dir is None else Path.cwd() / dir
        result = core.init_project(target)
        for m in result.messages:
            typer.echo(m, err=True)
