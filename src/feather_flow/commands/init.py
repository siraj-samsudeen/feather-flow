"""`feather init` command."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_flow.commands._common import _is_json
from feather_flow.output import emit_line


def init(
    ctx: typer.Context,
    project_name: str | None = typer.Argument(None, help="Project directory name."),
) -> None:
    """Scaffold a new client project with template files."""
    if project_name is None:
        project_name = typer.prompt("Project name")

    project_path = Path(project_name).resolve()
    if project_path.exists():
        non_hidden = [f for f in project_path.iterdir() if not f.name.startswith(".")]
        if non_hidden:
            typer.echo(
                f"Directory '{project_name}' already exists and is not empty.",
                err=True,
            )
            raise typer.Exit(code=1)

    from feather_flow.init_wizard import scaffold_project

    files_created = scaffold_project(project_path)
    if _is_json(ctx):
        emit_line(
            {"project": str(project_path), "files_created": files_created},
            json_mode=True,
        )
    else:
        typer.echo(f"Project scaffolded at {project_path}")


def register(app: typer.Typer) -> None:
    app.command(name="init")(init)
