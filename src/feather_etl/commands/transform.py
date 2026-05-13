"""`feather transform` command — re-runs silver/gold without re-extracting bronze.

Calls ``transforms.run_transforms(config)`` directly. No source connection is
ever opened — the no-source-touch invariant is what makes this verb cheap.

DDL kind: silver is always a VIEW; gold marked ``-- materialized: true``
becomes a TABLE by default; ``--force-views`` overrides that and creates
everything as VIEWs. Unmarked gold is always a VIEW.

Summary output: ``run_transforms`` returns one ``TransformResult``
per execute_transforms pass plus one per rebuild_materialized_gold pass —
so a materialized gold transform produces two entries (the initial VIEW
and the rebuilt TABLE). The summary de-duplicates by ``(schema, name)``,
keeping the *last* entry for each pair so the table-vs-view kind reflects
the final state of the destination. This matches what an operator sees
when inspecting the destination after the run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from feather_etl.commands._common import _load_and_validate


def transform(
    config: Path = typer.Option("feather.yaml", "--config"),
    force_views: bool = typer.Option(
        False,
        "--force-views",
        help="Create all transforms as VIEWs, skipping gold materialization.",
    ),
) -> None:
    """Re-run silver and gold transforms against the existing destination."""
    from feather_etl.destinations.duckdb import DuckDBDestination
    from feather_etl.state import StateManager
    from feather_etl.transforms import (
        check_bronze_dependencies,
        discover_transforms,
        run_transforms,
    )

    # 1. Load config. Spec: exit 2 if invalid. _load_and_validate raises
    #    typer.Exit(code=1) on its own; trap and re-raise with code=2.
    try:
        cfg = _load_and_validate(config)
    except typer.Exit:
        raise typer.Exit(code=2)
    except Exception as e:  # noqa: BLE001 - presenting the error is the goal
        typer.echo(f"Failed to load config: {e}", err=True)
        raise typer.Exit(code=2)

    # 2. Open destination. Spec: exit 2 if unreachable.
    try:
        dest = DuckDBDestination(path=cfg.destination.path)
        con = dest._connect()
    except Exception as e:  # noqa: BLE001
        typer.echo(f"Failed to open destination: {e}", err=True)
        raise typer.Exit(code=2)

    try:
        # 3. Discover transforms.
        transforms_list = discover_transforms(cfg.config_dir)

        # 4. Zero-transforms case.
        if not transforms_list:
            typer.echo("0 transforms.")
            return

        # 5. Advisory bronze check. Caller-side collapse rule: > 5 → summary.
        warnings = check_bronze_dependencies(con, transforms_list)
        if len(warnings) > 5:
            typer.echo(
                f"WARNING: {len(warnings)} bronze dependencies missing "
                "— run `feather extract` first",
                err=True,
            )
        else:
            for w in warnings:
                typer.echo(w, err=True)
    finally:
        # We only used `con` for the advisory check; run_transforms opens
        # its own connection. Close ours before running transforms so we
        # don't hold a second handle to the same DuckDB file.
        con.close()

    # 6. Execute transforms. run_transforms opens/closes its own connection.
    results = run_transforms(cfg, force_views=force_views)

    # 7. Write _runs rows. trigger='transform' for every entry.
    state_path = cfg.config_dir / "feather_state.duckdb"
    state = StateManager(state_path)
    state.init_state()
    now = datetime.now(timezone.utc)

    # De-duplicate by (schema, name) for both _runs writes and summary
    # rendering. When force_views=False, run_transforms returns N + M results
    # where M = materialized gold count; keep the last entry per key so the
    # table-vs-view kind reflects final destination state.
    deduped: dict[tuple[str, str], object] = {}
    for r in results:
        deduped[(r.schema, r.name)] = r
    final_results = list(deduped.values())

    for r in final_results:
        status = "success" if r.status == "success" else "failure"
        started_at = now
        ended_at = now
        run_id = f"{r.schema}.{r.name}_{started_at.isoformat()}"
        state.record_run(
            run_id=run_id,
            table_name=f"{r.schema}.{r.name}",
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            error_message=r.error,
            trigger="transform",
        )

    # 8. Summary output.
    for r in final_results:
        status = "success" if r.status == "success" else "failure"
        typer.echo(f"  {r.schema}.{r.name}  {r.type}  {status}")
    total = len(final_results)
    successes = sum(1 for r in final_results if r.status == "success")
    typer.echo(f"\n{total} transforms: {successes} succeeded.")

    # Exit code: 1 if any failure.
    if any(r.status != "success" for r in final_results):
        raise typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    app.command(name="transform")(transform)
