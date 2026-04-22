"""feather CLI entrypoint — empty Typer app registry.

No commands are registered yet. Each command will land in its own
package under `commands/<name>/` and register itself into `app` via a
`register(app)` function called from here.
"""

from __future__ import annotations

import typer

app = typer.Typer(help="feather-etl — config-driven ETL for heterogeneous sources.")


if __name__ == "__main__":  # pragma: no cover
    app()
