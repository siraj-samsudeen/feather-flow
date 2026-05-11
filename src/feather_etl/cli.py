"""feather CLI — thin wrapper over config, pipeline, state, and sources."""

from __future__ import annotations

import typer

from feather_etl.commands.discover import register as register_discover
from feather_etl.commands.extract import register as register_extract
from feather_etl.commands.history import register as register_history
from feather_etl.commands.init import register as register_init
from feather_etl.commands.run import register as register_run
from feather_etl.commands.setup import register as register_setup
from feather_etl.commands.status import register as register_status
from feather_etl.commands.view import register as register_view
from feather_etl.commands.validate import register as register_validate

app = typer.Typer(name="feather", help="feather-etl: config-driven ETL")


@app.callback()
def main(
    ctx: typer.Context,
    json: bool = typer.Option(False, "--json", help="Output as NDJSON"),
) -> None:
    """feather-etl: config-driven ETL."""
    ctx.ensure_object(dict)["json_mode"] = json


register_init(app)
register_validate(app)
register_discover(app)
register_view(app)
register_setup(app)
register_run(app)
register_history(app)
register_status(app)
register_extract(app)
