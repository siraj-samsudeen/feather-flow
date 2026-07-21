"""`feather validate` core — orchestration without Typer."""

from __future__ import annotations

from dataclasses import dataclass

from feather_flow.config import FeatherConfig


@dataclass
class SourceCheckResult:
    """Connection-check result for a single source."""

    type: str
    label: str  # path or host, whichever the source exposes; "configured" otherwise
    ok: bool
    error: str | None  # source._last_error when ok is False


@dataclass
class ValidateReport:
    """Result of running `feather validate` against a loaded config."""

    sources: list[SourceCheckResult]
    tables_count: int
    all_ok: bool


def run_validate(cfg: FeatherConfig) -> ValidateReport:
    """Test connection for each configured source. Pure read; no side effects."""
    results: list[SourceCheckResult] = []
    for source in cfg.sources:
        ok = source.check()
        label = (
            getattr(source, "path", None)
            or getattr(source, "host", None)
            or "configured"
        )
        error = getattr(source, "_last_error", None) if not ok else None
        results.append(
            SourceCheckResult(
                type=source.type,
                label=str(label),
                ok=ok,
                error=error,
            )
        )

    return ValidateReport(
        sources=results,
        tables_count=len(cfg.tables),
        all_ok=all(r.ok for r in results),
    )
