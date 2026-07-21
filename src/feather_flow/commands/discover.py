"""`feather discover` command — thin Typer wrapper over feather_flow.discover."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from feather_flow.commands._common import _load_and_validate
from feather_flow.discover import (
    apply_rename_decision,
    detect_renames_for_sources,
    run_discover,
)
from feather_flow.discover_state import DiscoverState
from feather_flow.sources.expand import expand_db_sources
from feather_flow.viewer_server import serve_and_open


def _resolve_rename_decision(
    proposals: list[tuple[str, str]],
    *,
    yes: bool,
    no_renames: bool,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Echo proposals and resolve --yes / --no-renames / TTY confirm.

    Returns ``(accepted, rejected)``. May raise ``typer.Exit(3)`` if the
    decision is required but stdin is not a TTY.
    """
    proposal_err = not sys.stdin.isatty()
    for old_name, new_name in proposals:
        typer.echo(
            f"  Rename inferred: {old_name} -> {new_name}",
            err=proposal_err,
        )

    if no_renames:
        for old_name, new_name in proposals:
            typer.echo(f"  Kept {old_name} orphaned; treating {new_name} as new")
        return [], list(proposals)

    if yes:
        return list(proposals), []

    if sys.stdin.isatty():
        if typer.confirm("Accept all?", default=True):
            return list(proposals), []
        for old_name, new_name in proposals:
            typer.echo(f"  Kept {old_name} orphaned; treating {new_name} as new")
        return [], list(proposals)

    typer.echo(
        "Rename confirmation required in non-interactive mode. "
        "Re-run with --yes to accept or --no-renames to reject.",
        err=True,
    )
    raise typer.Exit(code=3)


def discover(
    config: Path = typer.Option("feather.yaml", "--config"),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Re-run discovery for every source, ignoring cached state.",
    ),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Only retry sources that previously failed."
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Delete state entries and JSON files for removed/orphaned sources.",
    ),
    yes: bool = typer.Option(False, "--yes", help="Auto-accept inferred renames."),
    no_renames: bool = typer.Option(
        False,
        "--no-renames",
        help="Reject inferred renames; old entries become orphaned.",
    ),
) -> None:
    """Save each source's schema to an auto-named schema JSON file, then serve/open the schema viewer."""
    cfg = _load_and_validate(config, discover_mode=True)
    target_dir = Path(".")

    # Capture the prior `last_run_at` for the header BEFORE any state mutation.
    # The rename phase below may call `state.save()`, which updates the
    # timestamp; we want the truly-prior discover run, not the rename save.
    state = DiscoverState.load(target_dir)
    prior_last_run_at = state.last_run_at

    # Rename phase (only when not in refresh/prune mode — preserves prior behavior).
    if not (refresh or prune):
        sources = expand_db_sources(cfg.sources)
        detection = detect_renames_for_sources(state, sources)

        if detection.ambiguous:
            for new_name, candidates in detection.ambiguous:
                typer.echo(
                    f"Ambiguous rename for {new_name}: candidates "
                    f"{', '.join(candidates)}",
                    err=True,
                )
            raise typer.Exit(code=2)

        if detection.proposals:
            accepted, rejected = _resolve_rename_decision(
                detection.proposals, yes=yes, no_renames=no_renames
            )
            apply_rename_decision(
                state,
                accepted=accepted,
                rejected=rejected,
                sources=sources,
                config_dir=target_dir,
            )
            state.save()

    report = run_discover(
        cfg,
        target_dir,
        refresh=refresh,
        retry_failed=retry_failed,
        prune=prune,
    )

    # Prune mode short-circuits BEFORE the "Discovering from" header — matches
    # prior CLI behavior (the pre-refactor wrapper returned from the prune
    # branch before reaching the header block).
    if prune:
        for r in report.results:
            if r.status == "pruned" and r.output_path is not None:
                typer.echo(f"  Pruned: {r.output_path.name}")
        typer.echo(f"\nPruned {report.pruned_count} removed/orphaned entries.")
        return

    # Header line — uses the pre-rename-mutation timestamp captured above.
    if prior_last_run_at:
        typer.echo(
            f"Discovering from {config.name} (state file found, "
            f"last run {prior_last_run_at})..."
        )
    else:
        typer.echo(f"Discovering from {config.name}...")

    total = len(report.results)
    for idx, r in enumerate(report.results, start=1):
        prefix = f"  [{idx}/{total}] {r.name}"
        if r.status == "cached":
            typer.echo(f"{prefix}  (cached, {r.table_count} tables)")
        elif r.status == "skipped":
            typer.echo(f"{prefix}  (skipped)")
        elif r.status == "failed":
            typer.echo(f"{prefix}  → FAILED: {r.error}", err=True)
        elif r.status == "succeeded":
            assert r.output_path is not None
            typer.echo(
                f"{prefix}  ({r.decision})  → {r.table_count} tables → ./{r.output_path.name}"
            )

    parts: list[str] = []
    if report.succeeded_count:
        parts.append(f"{report.succeeded_count} discovered")
    if report.cached_count:
        parts.append(f"{report.cached_count} cached")
    if report.failed_count:
        parts.append(f"{report.failed_count} failed")
    typer.echo(f"\n{', '.join(parts)}.")

    serve_and_open(target_dir.resolve(), preferred_port=8000)
    if report.failed_count > 0:
        raise typer.Exit(code=2)


def register(app: typer.Typer) -> None:
    app.command(name="discover")(discover)
