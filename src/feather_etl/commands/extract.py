"""`feather extract` command — local bronze pull.

Renamed from `feather cache` per the feather-transform change.

The ``extract`` verb is implemented as a Typer sub-app so that sub-commands
(e.g. ``feather extract override``) can be nested beneath it.  Running
``feather extract`` with no sub-command invokes the default extract logic
(``invoke_without_command=True``).
"""

from __future__ import annotations

from pathlib import Path

import typer

from feather_etl.commands._common import _load_and_validate
from feather_etl.commands.override import override_app
from feather_etl.extract import run_extract

extract_app = typer.Typer(
    name="extract",
    help="Pull curated source tables into bronze.",
    invoke_without_command=True,
)
extract_app.add_typer(override_app, name="override")


@extract_app.callback()
def extract(
    ctx: typer.Context,
    config: Path = typer.Option("feather.yaml", "--config"),
    table: str | None = typer.Option(
        None,
        "--table",
        help="Comma-separated bronze table names to extract. "
        "Default: all curated tables.",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Comma-separated source_db values to extract. "
        "Default: all sources in curation.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Force re-pull even if source is unchanged.",
    ),
    refresh_all: bool = typer.Option(
        False,
        "--refresh-all",
        help="Clear ALL extract commit-log entries before running. Use when "
        "feather_data.duckdb has been deleted and state is stale.",
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
    """Pull curated source tables into bronze."""
    # When a sub-command (e.g. ``override``) is invoked, do not execute the
    # extract logic — just let Typer route to the sub-command.
    if ctx.invoked_subcommand is not None:
        return

    curation_path = Path(config).resolve().parent / "discovery" / "curation.json"
    if not curation_path.exists():
        typer.echo(
            f"discovery/curation.json not found in {curation_path.parent.parent}. "
            f"Run 'feather discover' and curate tables first.",
            err=True,
        )
        raise typer.Exit(code=2)

    cfg = _load_and_validate(config)
    if limit is not None:
        cfg.defaults.row_limit = limit

    # Parse selectors
    requested_tables = (
        [t.strip() for t in table.split(",") if t.strip()] if table else None
    )
    requested_sources = (
        [s.strip() for s in source.split(",") if s.strip()] if source else None
    )

    all_tables = cfg.tables
    all_table_names = [t.name for t in all_tables]
    all_source_dbs = sorted({t.database for t in all_tables if t.database})

    if requested_tables:
        unknown = [t for t in requested_tables if t not in all_table_names]
        if unknown:
            typer.echo(
                f"Unknown --table value(s): {', '.join(unknown)}. "
                f"Valid: {', '.join(all_table_names)}",
                err=True,
            )
            raise typer.Exit(code=2)

    if requested_sources:
        unknown = [s for s in requested_sources if s not in all_source_dbs]
        if unknown:
            typer.echo(
                f"Unknown --source value(s): {', '.join(unknown)}. "
                f"Valid: {', '.join(all_source_dbs)}",
                err=True,
            )
            raise typer.Exit(code=2)

    tables = all_tables
    if requested_tables:
        tables = [t for t in tables if t.name in requested_tables]
    if requested_sources:
        tables = [t for t in tables if t.database in requested_sources]

    # ------------------------------------------------------------------
    # Pre-flight: size, plan, and print banners for all DB-source tables.
    # Pass the already-filtered ``tables`` list directly so run_preflight
    # does not need to re-filter and we avoid mutating cfg.tables.
    # ------------------------------------------------------------------
    from feather_etl.commands._preflight import run_preflight
    from feather_etl.state import StateManager

    state = StateManager(cfg.config_dir / "feather_state.duckdb")
    state.init_state()

    preflight_result = run_preflight(
        cfg,
        state,
        table_filter=None,  # tables list already filtered above
        accept_wide=accept_wide,
        single_window=single_window,
        accept_slow_transport=accept_slow_transport,
        plan_only=plan_only,
        tables=tables,
    )

    # plan_only: exit 0 without extracting.
    if preflight_result is None:
        return

    # Build the ordered set of (table, plan) from preflight.
    planned_pairs = preflight_result  # list[(TableConfig, ExtractPlan | None)]

    # Build pre-planned windows map: table.name -> list[WindowSpec]
    pre_planned_windows: dict[str, list] = {}
    for tbl, plan in planned_pairs:
        if plan is not None and plan.windows:
            pre_planned_windows[tbl.name] = plan.windows

    # Use only tables returned by preflight (may be a subset due to filter).
    extract_tables = [tbl for tbl, _ in planned_pairs]

    results = run_extract(
        cfg,
        extract_tables,
        cfg.config_dir,
        refresh=refresh,
        refresh_all=refresh_all,
        pre_planned_windows=pre_planned_windows,
    )

    # Grouped-by-source_db output
    from collections import defaultdict

    groups: dict[str, list] = defaultdict(list)
    for r in results:
        groups[r.source_db].append(r)
    total_success = 0
    total_cached = 0
    total_failed = 0
    for source_db, rs in groups.items():
        succ = sum(1 for r in rs if r.status == "success")
        cach = sum(1 for r in rs if r.status == "cached")
        fail = sum(1 for r in rs if r.status == "failure")
        total_success += succ
        total_cached += cach
        total_failed += fail

        parts = []
        if succ:
            parts.append(f"{succ} extracted")
        if cach:
            parts.append(f"{cach} cached")
        if fail:
            parts.append(f"{fail} failed")
        # Look up the source 'name' for the parenthetical. For file sources,
        # source_db == source.name, so fall back to source_db.
        src_name = _lookup_source_name(cfg, source_db)
        line = f"  {source_db:<12} ({src_name}): {', '.join(parts) or '0 tables'}"
        typer.echo(line)
        for r in rs:
            if r.status == "failure":
                err = (r.error_message or "").splitlines()[0][:120]
                typer.echo(f"    ✗ {r.table_name} — {err}")

    total = len(results)
    summary_parts = []
    if total_success:
        summary_parts.append(f"{total_success} extracted")
    if total_cached:
        summary_parts.append(f"{total_cached} cached")
    if total_failed:
        summary_parts.append(f"{total_failed} failed")
    typer.echo(f"\n{total} tables: {', '.join(summary_parts) or '0 tables'}.")

    if total_failed:
        raise typer.Exit(code=1)


def _lookup_source_name(cfg, source_db: str) -> str:
    """Find the YAML source 'name' corresponding to a source_db.

    For multi-DB sources, ``resolve_source`` returns a per-DB child whose
    ``.name`` is the synthesized ``<parent>__<db>``. We prefer the parent
    name (stored on ``_parent_name`` by ``curation._bind_db``) so the CLI
    output keeps showing the source the operator wrote.
    """
    from feather_etl.curation import resolve_source

    try:
        src = resolve_source(source_db, cfg.sources)
        return getattr(src, "_parent_name", src.name)
    except ValueError:
        return source_db


def register(app: typer.Typer) -> None:
    app.add_typer(extract_app, name="extract")
