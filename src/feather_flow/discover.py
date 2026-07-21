"""`feather discover` core — read each source's catalog, save it as schema JSON.

Discovery is metadata-only. For each configured source, it asks "what tables
and columns exist?" (via the source's `discover()` method) and writes the
answer to `schema_<source>.json` next to `feather.yaml`. It does not extract
data — that's `feather run`. The schema JSON feeds the user's curation step:
picking which tables to ingest in `feather.yaml`.

This module is the pure core behind the `feather discover` CLI — no Typer
imports, no stdin, no prompts. The Typer wrapper in `commands/discover.py`
owns user interaction and calls into this module.

CONTENTS
  == Public Interface ==
    • run_discover                  — main per-source discovery loop
    • detect_renames_for_sources    — pure rename detection (no I/O)
    • apply_rename_decision         — apply a resolved rename decision

  == Data Types ==
    • RenameDetection         — output of rename detection (proposals + ambiguous)
    • SourceDiscoveryResult   — per-source outcome from run_discover
    • DiscoverReport          — aggregate result of run_discover

  == Private Helpers ==
    • _write_schema                 — discover a source and write its schema JSON
    • _fingerprint_for              — identity string for rename detection

CALL ORDER (Typer wrapper invokes these in sequence)
  1. detect_renames_for_sources → RenameDetection
  2. (wrapper prompts the user to accept/reject proposals)
  3. apply_rename_decision        (applies the resolved decision)
  4. run_discover                  → DiscoverReport

SEE ALSO
  feather_flow.discover_state        — DiscoverState, classify(), detect_renames()
  feather_flow.sources.expand        — expand_db_sources() for multi-DB configs
  feather_flow.commands.discover     — Typer wrapper (stdin, --yes, --no-renames)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from feather_flow.config import FeatherConfig, schema_output_path
from feather_flow.discover_state import (
    DiscoverState,
    apply_renames,
    classify,
    detect_renames,
)


# == Public Interface ==

# ── Main discovery loop ──


def run_discover(
    cfg: FeatherConfig,
    config_dir: Path,
    *,
    refresh: bool,
    retry_failed: bool,
    prune: bool,
) -> DiscoverReport:
    """Run discovery on every configured source; write one schema JSON per source.

    Per-source isolation: one source failing (connection, DDL, permissions)
    does not abort the others — failures are recorded in the returned report,
    not raised. Also writes feather_discover.state.json to track per-source
    outcome across runs.

    Modes (at most one; default = new-and-changed-only):
      refresh       — re-discover every source
      retry_failed  — re-discover only previously-failed sources
      prune         — delete stale schema files + state entries; don't discover

    The Typer wrapper (commands/discover.py) resolves rename proposals and
    launches the viewer; this function does neither.
    """
    from feather_flow.sources.expand import expand_db_sources

    state = DiscoverState.load(config_dir)
    sources = expand_db_sources(cfg.sources)

    flag: str | None = None
    if refresh:
        flag = "refresh"
    elif retry_failed:
        flag = "retry-failed"
    elif prune:
        flag = "prune"

    names = [s.name for s in sources]
    decisions = classify(state=state, current_names=names, flag=flag)

    report = DiscoverReport(state_last_run_at=state.last_run_at)

    if flag == "prune":
        for name, dec in list(decisions.items()):
            entry = state.sources.get(name)
            if dec == "removed" or (
                entry and entry.get("status") in ("orphaned", "removed")
            ):
                output_path: Path | None = None
                if entry and entry.get("output_path"):
                    target = config_dir / Path(entry["output_path"]).name
                    if target.is_file():
                        output_path = target
                        target.unlink()
                state.sources.pop(name, None)
                report.pruned_count += 1
                report.results.append(
                    SourceDiscoveryResult(
                        name=name,
                        decision=dec,
                        status="pruned",
                        output_path=output_path,
                    )
                )
        state.save()
        return report

    for source in sources:
        decision = decisions.get(source.name, "new")
        fingerprint = _fingerprint_for(source)

        if decision == "cached":
            entry = state.sources[source.name]
            report.cached_count += 1
            report.results.append(
                SourceDiscoveryResult(
                    name=source.name,
                    decision=decision,
                    status="cached",
                    table_count=entry.get("table_count", 0),
                )
            )
            continue
        if decision == "skip":
            report.results.append(
                SourceDiscoveryResult(
                    name=source.name, decision=decision, status="skipped"
                )
            )
            continue

        # Source came from expand_db_sources with a pre-set error.
        if hasattr(source, "_last_error") and source._last_error:
            report.failed_count += 1
            state.record_failed(
                name=source.name,
                type_=source.type,
                fingerprint=fingerprint,
                error=source._last_error,
                host=getattr(source, "host", None),
                database=getattr(source, "database", None),
            )
            report.results.append(
                SourceDiscoveryResult(
                    name=source.name,
                    decision=decision,
                    status="failed",
                    error=source._last_error,
                )
            )
            continue

        if not source.check():
            err = getattr(source, "_last_error", "connection failed")
            report.failed_count += 1
            state.record_failed(
                name=source.name,
                type_=source.type,
                fingerprint=fingerprint,
                error=err,
                host=getattr(source, "host", None),
                database=getattr(source, "database", None),
            )
            report.results.append(
                SourceDiscoveryResult(
                    name=source.name, decision=decision, status="failed", error=err
                )
            )
            continue

        try:
            out, count = _write_schema(source, config_dir)
        except Exception as e:  # noqa: BLE001 — preserve existing broad-catch behavior
            report.failed_count += 1
            state.record_failed(
                name=source.name,
                type_=source.type,
                fingerprint=fingerprint,
                error=str(e),
                host=getattr(source, "host", None),
                database=getattr(source, "database", None),
            )
            report.results.append(
                SourceDiscoveryResult(
                    name=source.name,
                    decision=decision,
                    status="failed",
                    error=str(e),
                )
            )
            continue

        report.succeeded_count += 1
        state.record_ok(
            name=source.name,
            type_=source.type,
            fingerprint=fingerprint,
            table_count=count,
            output_path=out,
            host=getattr(source, "host", None),
            database=getattr(source, "database", None),
        )
        report.results.append(
            SourceDiscoveryResult(
                name=source.name,
                decision=decision,
                status="succeeded",
                table_count=count,
                output_path=out,
            )
        )

    # Mark state-only entries as removed (preserves prior CLI behavior).
    for name, dec in decisions.items():
        if dec == "removed" and state.sources.get(name, {}).get("status") != "orphaned":
            state.record_removed(name)

    state.save()
    return report


# ── Rename resolution (runs before run_discover in the CLI flow) ──


def detect_renames_for_sources(
    state: DiscoverState,
    sources: list,
) -> RenameDetection:
    """Pure detection. Returns proposals + ambiguous list. No I/O, no prompts."""
    current_pairs = [(source.name, _fingerprint_for(source)) for source in sources]
    proposals, ambiguous = detect_renames(state=state, current=current_pairs)
    return RenameDetection(proposals=list(proposals), ambiguous=list(ambiguous))


def apply_rename_decision(
    state: DiscoverState,
    accepted: list[tuple[str, str]],
    rejected: list[tuple[str, str]],
    sources: list,
    config_dir: Path,
) -> None:
    """Apply a pre-resolved rename decision.

    ``accepted`` proposals are applied via ``apply_renames`` (state + files
    are renamed). ``rejected`` proposals are recorded as orphaned (the new
    name is treated as a fresh source on the next discovery pass).
    """
    if accepted:
        apply_renames(
            state=state,
            renames=accepted,
            config_dir=config_dir,
            sources=sources,
        )
    for old_name, new_name in rejected:
        state.record_orphaned(
            old_name,
            note=f"rename rejected; new source discovered as {new_name}",
        )


# == Data Types ==


@dataclass
class RenameDetection:
    """Output of ``detect_renames_for_sources``."""

    proposals: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class SourceDiscoveryResult:
    """One source's outcome from ``run_discover``."""

    name: str
    decision: str  # "new" | "retry" | "rerun" | "cached" | "skip" | "removed"
    status: str  # "succeeded" | "failed" | "cached" | "skipped" | "pruned"
    table_count: int = 0
    output_path: Path | None = None
    error: str | None = None


@dataclass
class DiscoverReport:
    """Aggregate result of ``run_discover``."""

    results: list[SourceDiscoveryResult] = field(default_factory=list)
    succeeded_count: int = 0
    failed_count: int = 0
    cached_count: int = 0
    pruned_count: int = 0
    state_last_run_at: str | None = None


# == Private Helpers ==


def _write_schema(source, target_dir: Path) -> tuple[Path, int]:
    """Discover ``source`` and write its schema JSON. Returns (path, table_count)."""
    schemas = source.discover()
    payload = [
        {
            "table_name": s.name,
            "columns": [{"name": c[0], "type": c[1]} for c in s.columns],
        }
        for s in schemas
    ]
    out = target_dir / schema_output_path(source)
    out.write_text(json.dumps(payload, indent=2))
    return out, len(schemas)


def _fingerprint_for(source) -> str:
    """Composition per spec §6.7.

    DB sources: '<type>:<host>:<port>:<database>'. File sources: '<type>:<absolute_path>'.
    """
    if hasattr(source, "host") and source.host is not None:
        return (
            f"{source.type}:{source.host}:{source.port or ''}:{source.database or ''}"
        )
    return f"{source.type}:{Path(source.path).resolve()}"
