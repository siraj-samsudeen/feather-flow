"""`feather extract` orchestrator — dev-only bronze pull, isolated state.

Renamed from `feather_etl.cache` (verb `feather cache`) per the
feather-transform change. The local-snapshot state machinery still
uses `_cache_watermarks` etc. inside `state.py`; only the verb-facing
public API was renamed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from feather_etl.config import FeatherConfig, TableConfig
from feather_etl.curation import resolve_source
from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.state import StateManager

logger = logging.getLogger(__name__)


@dataclass
class ExtractResult:
    """Result of extracting one table into bronze."""

    table_name: str
    source_db: str
    status: str  # "success" | "cached" | "failure"
    rows_loaded: int = 0
    error_message: str | None = None


def run_extract(
    config: FeatherConfig,
    tables: list[TableConfig],
    working_dir: Path,
    refresh: bool = False,
) -> list[ExtractResult]:
    """Pull curated tables into bronze. Dev-only. No transforms, no DQ, no drift.

    Per-table errors (including unresolvable ``source_db`` and extract/load
    failures) are captured as ``ExtractResult(status="failure")`` and never
    raised — the caller can decide exit-code semantics from the result list.
    """
    state = StateManager(working_dir / "feather_state.duckdb")
    state.init_state()
    dest = DuckDBDestination(path=config.destination.path)
    dest.setup_schemas()

    results: list[ExtractResult] = []
    for table in tables:
        source_db = table.database or (table.source_name or "")
        try:
            source = resolve_source(source_db, config.sources)
        except ValueError as e:
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="failure",
                    error_message=str(e),
                )
            )
            continue

        now = datetime.now(timezone.utc)
        run_id = f"extract_{table.name}_{now.isoformat()}"

        wm = state.read_cache_watermark(table.name)
        change = source.detect_changes(table.source_table, last_state=wm)

        if not change.changed and not refresh:
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="cached",
                )
            )
            continue

        try:
            data = source.extract(table.source_table)
            rows = dest.load_full(f"bronze.{table.name}", data, run_id)
            state.write_cache_watermark(
                table_name=table.name,
                source_db=source_db,
                last_run_at=now,
                last_file_mtime=change.metadata.get("file_mtime"),
                last_file_hash=change.metadata.get("file_hash"),
                last_checksum=change.metadata.get("checksum"),
                last_row_count=change.metadata.get("row_count"),
            )
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="success",
                    rows_loaded=rows,
                )
            )
        except Exception as e:
            logger.error("Extract failed for %s: %s", table.name, e)
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="failure",
                    error_message=str(e),
                )
            )

    return results
