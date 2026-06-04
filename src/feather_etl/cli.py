"""Root Typer app — entry point for the feather CLI."""

from __future__ import annotations

import typer

from feather_etl.commands.init.cli import register as register_init

app = typer.Typer(name="feather")


@app.callback()
def _callback() -> None:
    """feather — config-driven ETL for heterogeneous ERP sources."""


register_init(app)

if __name__ == "__main__":
    app()
