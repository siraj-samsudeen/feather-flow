"""`feather run` command."""

from __future__ import annotations

from pathlib import Path

import typer

from feather_flow.commands._common import (
    _is_json,
    _load_and_validate,
)
from feather_flow.output import emit


def run(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    table: str | None = typer.Option(None, "--table", help="Extract only this table."),
    force_views: bool = typer.Option(
        False,
        "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Override defaults.row_limit for this invocation.",
    ),
    plan_only: bool = typer.Option(
        False,
        "--plan-only",
        help="Print the pre-flight plan for each DB-source table and exit without "
        "extracting. File sources are not shown (no planning needed).",
    ),
    accept_wide: bool = typer.Option(
        False,
        "--accept-wide",
        help="Bypass the wide_table blocker and proceed with the full column set.",
    ),
    single_window: bool = typer.Option(
        False,
        "--single-window",
        help="Bypass the no_window_column blocker and extract in a single window.",
    ),
    accept_slow_transport: bool = typer.Option(
        False,
        "--accept-slow-transport",
        help="Bypass the slow_transport blocker and proceed without connectorx.",
    ),
) -> None:
    """Extract all configured tables (or a single table with --table)."""
    from feather_flow.commands._preflight import run_preflight
    from feather_flow.pipeline import run_all
    from feather_flow.state import StateManager

    cfg = _load_and_validate(config)
    if limit is not None:
        cfg.defaults.row_limit = limit

    # Pre-flight: size, plan, and banner for DB-source tables.
    state = StateManager(cfg.config_dir / "feather_state.duckdb")
    state.init_state()

    preflight_result = run_preflight(
        cfg,
        state,
        table_filter=table,
        accept_wide=accept_wide,
        single_window=single_window,
        accept_slow_transport=accept_slow_transport,
        plan_only=plan_only,
    )

    # plan_only: exit 0 without running.
    if preflight_result is None:
        return

    # Build pre-planned windows map from preflight to avoid re-planning in run_all.
    pre_planned_windows: dict[str, list] = {}
    for tbl, plan in preflight_result:
        if plan is not None and plan.windows:
            pre_planned_windows[tbl.name] = plan.windows

    try:
        results = run_all(
            cfg,
            table_filter=table,
            force_views=force_views,
            pre_planned_windows=pre_planned_windows or None,
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    if _is_json(ctx):
        emit(
            [
                {
                    "table_name": r.table_name,
                    "status": r.status,
                    "rows_loaded": r.rows_loaded,
                    "error_message": r.error_message,
                }
                for r in results
            ],
            json_mode=True,
        )
    else:
        for r in results:
            if r.status == "success":
                typer.echo(f"  {r.table_name}: {r.status} ({r.rows_loaded} rows)")
            elif r.status == "skipped":
                typer.echo(f"  {r.table_name}: skipped (unchanged)")
            else:
                typer.echo(f"  {r.table_name}: {r.status} — {r.error_message}")

        successes = sum(1 for r in results if r.status == "success")
        skipped = sum(1 for r in results if r.status == "skipped")

        parts = [f"{successes}/{len(results)} tables extracted"]
        if skipped:
            parts.append(f"{skipped} skipped")
        typer.echo(f"\n{', '.join(parts)}.")

    failures = sum(
        1
        for r in results
        if r.status == "failure" or (r.status == "skipped" and r.error_message)
    )
    if failures > 0:
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    app.command(name="run")(run)
