"""`feather extract` orchestrator — bronze pull, isolated state.

Renamed from `feather_etl.cache` (verb `feather cache`) per the
feather-transform change. The local-snapshot state is written to
`_watermarks` inside `state.py` with `source_db` set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pyarrow as pa

from feather_etl.config import FeatherConfig, TableConfig
from feather_etl.curation import resolve_source
from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.extract_windows import plan_windows
from feather_etl.pipeline import _setup_jsonl_logging
from feather_etl.state import StateManager

logger = logging.getLogger(__name__)


def _iter_source_batches(
    source,
    source_table: str,
    heartbeat_every_rows: int,
    heartbeat_every_seconds: int,
) -> Iterator[pa.RecordBatch]:
    """Yield RecordBatches from a source. Adapts non-streaming (file) sources.

    SQL sources (sqlserver/mysql/postgres) implement `extract_batches` and
    stream + heartbeat natively. Other sources still go through `extract()`
    and we slice the materialized table into batches so the streaming-load
    session writes uniformly.
    """
    if hasattr(source, "extract_batches"):
        yield from source.extract_batches(
            source_table,
            heartbeat_every_rows=heartbeat_every_rows,
            heartbeat_every_seconds=heartbeat_every_seconds,
        )
        return

    table = source.extract(source_table)
    if table.num_rows == 0:
        # Streaming-load session needs at least one batch to learn the schema.
        yield pa.RecordBatch.from_pylist([], schema=table.schema)
        return
    yield from table.to_batches()


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
    refresh_all: bool = False,
) -> list[ExtractResult]:
    """Pull curated tables into bronze. Dev-only. No transforms, no DQ, no drift.

    Per-table errors (including unresolvable ``source_db`` and extract/load
    failures) are captured as ``ExtractResult(status="failure")`` and never
    raised — the caller can decide exit-code semantics from the result list.
    """
    state = StateManager(working_dir / "feather_state.duckdb")
    state.init_state()

    if refresh_all:
        state.clear_all_windows()

    # Safety: if the destination DB has been deleted out-of-band while
    # state still claims windows are committed, silently re-extracting
    # would leave the new bronze data inconsistent with the predicate-based
    # cleanup contract (today fine for 1=1, wrong for future date / pk_range
    # windows). Force the operator to acknowledge via --refresh-all.
    if not config.destination.path.exists():
        orphan_tables = state.get_all_window_table_names()
        # Double-guard: refresh_all already cleared the log above, so this list
        # is always empty in that path — but the explicit `not refresh_all` keeps
        # the invariant readable.
        if orphan_tables and not refresh_all:
            sample = ", ".join(orphan_tables[:3])
            if len(orphan_tables) > 3:
                sample += "..."
            msg = (
                f"feather_data.duckdb does not exist but _extract_windows has "
                f"{len(orphan_tables)} table(s) with committed windows ({sample}). "
                f"State and destination are out of sync — rerun with --refresh-all "
                f"to clear the commit log and re-extract from scratch."
            )
            return [
                ExtractResult(
                    table_name=t.name,
                    source_db=t.database or (t.source_name or ""),
                    status="failure",
                    error_message=msg,
                )
                for t in tables
            ]

    dest = DuckDBDestination(path=config.destination.path)
    dest.setup_schemas()
    _setup_jsonl_logging(working_dir)
    extract_defaults = config.defaults.extract

    results: list[ExtractResult] = []
    for table in tables:
        source_db = table.database or (table.source_name or "")
        now = datetime.now(timezone.utc)
        run_id = f"extract_{table.name}_{now.isoformat()}"

        try:
            source = resolve_source(source_db, config.sources)
        except ValueError as e:
            ended_at = datetime.now(timezone.utc)
            state.record_run(
                run_id=run_id,
                table_name=table.name,
                started_at=now,
                ended_at=ended_at,
                status="failure",
                error_message=str(e),
                trigger="extract",
            )
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="failure",
                    error_message=str(e),
                )
            )
            continue

        wm = state.read_watermark(table.name)
        change = source.detect_changes(table.source_table, last_state=wm)

        if not change.changed and not refresh:
            ended_at = datetime.now(timezone.utc)
            state.record_run(
                run_id=run_id,
                table_name=table.name,
                started_at=now,
                ended_at=ended_at,
                status="skipped",
                trigger="extract",
            )
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="cached",
                )
            )
            continue

        try:
            target = f"bronze.{table.name}"
            if refresh:
                state.clear_windows_for_table(target)

            committed = state.get_committed_windows(target)
            windows = [w for w in plan_windows(table) if w.window_key not in committed]

            rows = 0
            # Sources implementing the transport-registry contract (#61) expose
            # transport_name. Sources that don't (csv, sqlite, file sources) fall
            # back to the class name — those paths don't trip the transport choice.
            transport_used = getattr(source, "transport_name", type(source).__name__)
            # NOTE (#62/#63): `_iter_source_batches` streams the full source table for
            # every window. Today windows is always `[WindowSpec("all", "1=1", ...)]`,
            # so this is fine. Issue #63 introduces real `date` / `pk_range` windows;
            # the planner there MUST also push the window predicate into the source
            # fetch (per-window SELECT WHERE) — otherwise each window would re-fetch
            # everything and the DELETE+INSERT would overwrite the prior window.
            for window in windows:
                result = dest.load_batched_append(
                    table=target,
                    window=window,
                    batches=_iter_source_batches(
                        source,
                        table.source_table,
                        heartbeat_every_rows=extract_defaults.heartbeat_every_rows,
                        heartbeat_every_seconds=extract_defaults.heartbeat_every_seconds,
                    ),
                    run_id=run_id,
                    state=state,
                    transport_used=transport_used,
                )
                rows += result.rows_loaded

            logger.info(
                "extract_complete",
                extra={
                    "table": table.source_table,
                    "rows_loaded": rows,
                    "status": "success",
                },
            )
            state.write_watermark(
                table_name=table.name,
                strategy=None,
                last_run_at=now,
                last_file_mtime=change.metadata.get("file_mtime"),
                last_file_hash=change.metadata.get("file_hash"),
                last_checksum=change.metadata.get("checksum"),
                last_row_count=change.metadata.get("row_count"),
                source_db=source_db,
            )
            ended_at = datetime.now(timezone.utc)
            state.record_run(
                run_id=run_id,
                table_name=table.name,
                started_at=now,
                ended_at=ended_at,
                status="success",
                rows_extracted=rows,
                rows_loaded=rows,
                trigger="extract",
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
            ended_at = datetime.now(timezone.utc)
            state.record_run(
                run_id=run_id,
                table_name=table.name,
                started_at=now,
                ended_at=ended_at,
                status="failure",
                error_message=str(e),
                trigger="extract",
            )
            results.append(
                ExtractResult(
                    table_name=table.name,
                    source_db=source_db,
                    status="failure",
                    error_message=str(e),
                )
            )

    return results
