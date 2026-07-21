"""`feather view` command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from feather_flow import viewer_server


def view(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Existing directory to serve the schema viewer from.",
        ),
    ] = Path("."),
    port: Annotated[
        int,
        typer.Option(
            "--port",
            min=1,
            max=65535,
            help="Preferred port for the local viewer server.",
        ),
    ] = 8000,
) -> None:
    """Serve the schema viewer in a browser."""
    viewer_server.serve_and_open(path, preferred_port=port)


def register(app: typer.Typer) -> None:
    app.command(name="view")(view)
