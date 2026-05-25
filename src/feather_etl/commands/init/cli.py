"""Typer adapter for feather init."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl.commands.init import core


def register(app: typer.Typer) -> None:
    @app.command(name="init")
    def init() -> None:
        """Stamp feather project files into the current directory."""
        core.init_project(Path.cwd())
