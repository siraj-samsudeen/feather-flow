"""Pipeline orchestrator for feather-etl."""

from __future__ import annotations

import hashlib
import json as json_mod
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from feather_etl.alerts import (
    alert_on_dq_failure,
    alert_on_failure,
    alert_on_schema_drift,
)
from feather_etl.config import FeatherConfig, TableConfig
from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.state import StateManager

logger = logging.getLogger(__name__)


class _JsonlFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        # Include extra fields if present
        for key in ("table", "status", "rows_loaded", "error"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json_mod.dumps(entry, default=str)


def _setup_jsonl_logging(config_dir: Path) -> None:
    """Add a JSONL FileHandler to the feather logger (idempotent)."""
    log_path = config_dir / "feather_log.jsonl"
    feather_logger = logging.getLogger("feather_etl")

    # Guard against duplicate handlers across multiple run_all() calls
    for h in feather_logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == str(
            log_path.resolve()
        ):
            return

    handler = logging.FileHandler(str(log_path), mode="a")
    handler.setFormatter(_JsonlFormatter())
    handler.setLevel(logging.INFO)
    feather_logger.addHandler(handler)
    feather_logger.setLevel(logging.INFO)


def _resolve_target(table: TableConfig) -> str:
    """Return explicit target_table or default to bronze.<name>."""
    return table.target_table or f"bronze.{table.name}"


def _apply_column_map(data: pa.Table, column_map: dict[str, str]) -> pa.Table:
    """Select and rename columns per column_map. Returns new table with only mapped columns."""
    source_cols = list(column_map.keys())
    # Select only the mapped columns
    data = data.select(source_cols)
    # Rename: build new names in order
    new_names = [column_map[c] for c in data.column_names]
    return data.rename_columns(new_names)


def _compute_pk_hashes(
    data: pa.Table,
    primary_key: list[str],
    ts_column: str,
    watermark_value: str,
) -> list[str]:
    """Compute SHA-256 hashes of PK values for rows at the given watermark timestamp."""
    ts_vals = data.column(ts_column).to_pylist()
    pk_cols = {pk: data.column(pk).to_pylist() for pk in primary_key}
    hashes = []
    for i in range(data.num_rows):
        if str(ts_vals[i]) == watermark_value:
            key = "|".join(str(pk_cols[pk][i]) for pk in primary_key)
            hashes.append(hashlib.sha256(key.encode()).hexdigest())
    return hashes


def _filter_boundary_rows(
    data: pa.Table,
    primary_key: list[str] | None,
    ts_column: str,
    old_watermark: str,
    prev_hashes: list[str],
) -> tuple[pa.Table, int]:
    """Filter out boundary rows whose PK hash matches stored hashes.

    Returns (filtered_data, rows_skipped).
    """
    if not primary_key or not prev_hashes:
        return data, 0

    prev_set = set(prev_hashes)
    ts_vals = data.column(ts_column).to_pylist()
    pk_cols = {pk: data.column(pk).to_pylist() for pk in primary_key}
    mask = []
    skipped = 0
    for i in range(data.num_rows):
        if str(ts_vals[i]) == old_watermark:
            key = "|".join(str(pk_cols[pk][i]) for pk in primary_key)
            h = hashlib.sha256(key.encode()).hexdigest()
            if h in prev_set:
                mask.append(False)
                skipped += 1
                continue
        mask.append(True)
    return data.filter(mask), skipped


def _apply_dedup(data: pa.Table, table: TableConfig) -> pa.Table:
    """Apply dedup to extracted data if configured."""
    if not table.dedup and not table.dedup_columns:
        return data
    con = duckdb.connect(":memory:")
    con.register("_dedup_data", data)
    if table.dedup:
        result = con.execute("SELECT DISTINCT * FROM _dedup_data").arrow().read_all()
    else:
        # dedup_columns: keep first occurrence per dedup key
        cols = ", ".join(f'"{c}"' for c in table.dedup_columns)
        result = (
            con.execute(
                f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
                f"(PARTITION BY {cols} ORDER BY 1) AS _rn FROM _dedup_data) "
                f"WHERE _rn = 1"
            )
            .arrow()
            .read_all()
        )
        # Remove the _rn column
        idx = result.schema.get_field_index("_rn")
        result = result.remove_column(idx)
    con.close()
    return result


@dataclass
class RunResult:
    table_name: str
    run_id: str
    status: str  # "success", "failure", "skipped"
    rows_loaded: int = 0
    error_message: str | None = None


def run_table(
    config: FeatherConfig,
    table: TableConfig,
    working_dir: Path,
    source: object | None = None,
) -> RunResult:
    """Run a single table through the pipeline: extract → load → update state."""
    logger.info("Starting extraction", extra={"table": table.name})
    now = datetime.now(timezone.utc)
    run_id = f"{table.name}_{now.isoformat()}"

    state_path = working_dir / "feather_state.duckdb"
    state = StateManager(state_path)
    state.init_state()

    # Retry backoff check: skip if table is in backoff window
    skip, skip_error = state.should_skip_retry(table.name)
    if skip:
        ended_at = datetime.now(timezone.utc)
        error_msg = f"In backoff (previous failure: {skip_error})"
        logger.info(
            "Skipped (retry backoff)", extra={"table": table.name, "status": "skipped"}
        )
        state.record_run(
            run_id=run_id,
            table_name=table.name,
            started_at=now,
            ended_at=ended_at,
            status="skipped",
            error_message=error_msg,
            trigger="run",
        )
        return RunResult(
            table_name=table.name,
            run_id=run_id,
            status="skipped",
            error_message=error_msg,
        )

    dest = DuckDBDestination(path=config.destination.path)
    dest.setup_schemas()

    if source is None:
        source = config.sources[0]
    effective_target = _resolve_target(table)

    # column_map present: extract only mapped columns
    prod_columns = list(table.column_map.keys()) if table.column_map else None

    # Change detection: check if source file changed since last run
    wm = state.read_watermark(table.name)
    change = source.detect_changes(table.source_table, last_state=wm)

    started_at = datetime.now(timezone.utc)

    if not change.changed:
        ended_at = datetime.now(timezone.utc)
        state.record_run(
            run_id=run_id,
            table_name=table.name,
            started_at=started_at,
            ended_at=ended_at,
            status="skipped",
            trigger="run",
        )
        # Touch scenario: mtime changed but hash identical — update watermark
        # so next run skips re-hashing
        if change.metadata:
            state.write_watermark(
                table.name,
                strategy=table.strategy,
                last_run_at=ended_at,
                last_file_mtime=change.metadata.get("file_mtime"),
                last_file_hash=change.metadata.get("file_hash"),
                last_checksum=change.metadata.get("checksum"),
                last_row_count=change.metadata.get("row_count"),
            )
        return RunResult(
            table_name=table.name,
            run_id=run_id,
            status="skipped",
        )

    try:
        is_incremental = table.strategy == "incremental"
        wm_last_value = wm.get("last_value") if wm else None
        watermark_before = str(wm_last_value) if wm_last_value else None

        # Schema drift detection (V10)
        schema_changes_json: str | None = None
        try:
            from feather_etl.schema_drift import detect_drift

            current_schema = source.get_schema(table.source_table)
            stored_schema = state.get_schema_snapshot(table.name)

            if stored_schema is None:
                # First run — save baseline, no drift reported
                state.save_schema_snapshot(table.name, current_schema)
            else:
                drift = detect_drift(current_schema, stored_schema)
                if drift.has_drift:
                    import json as _json

                    schema_changes_json = _json.dumps(drift.to_json_dict())
                    logger.info(
                        "Schema drift detected: %s",
                        schema_changes_json,
                        extra={"table": table.name},
                    )
                    alert_on_schema_drift(
                        table.name,
                        schema_changes_json,
                        severity=drift.severity,
                        config=config.alerts,
                    )

                    # Handle added columns: ALTER TABLE before load (FR4.11)
                    if drift.added:
                        dest_con = dest._connect()
                        try:
                            for col_name, col_type in drift.added:
                                try:
                                    dest_con.execute(
                                        f"ALTER TABLE {effective_target} "
                                        f'ADD COLUMN "{col_name}" {col_type}'
                                    )
                                except duckdb.CatalogException:  # pragma: no cover
                                    # Defensive: reachable only if the bronze
                                    # target table was deleted between runs
                                    # after a drift snapshot was persisted.
                                    pass  # Table/column may not exist yet
                        finally:
                            dest_con.close()

                    # Save updated snapshot
                    state.save_schema_snapshot(table.name, current_schema)
        except Exception as sd_err:
            logger.warning("Schema drift check failed: %s", sd_err)

        if is_incremental and wm_last_value is not None:
            # Subsequent incremental run: apply overlap window
            overlap = config.defaults.overlap_window_minutes
            wm_dt = datetime.fromisoformat(str(wm_last_value))
            effective_wm = wm_dt - timedelta(minutes=overlap)
            effective_wm_str = effective_wm.isoformat()

            data = source.extract(
                table.source_table,
                columns=prod_columns,
                filter=table.filter,
                watermark_column=table.timestamp_column,
                watermark_value=effective_wm_str,
            )
            if config.defaults.row_limit:
                data = data.slice(0, config.defaults.row_limit)
            if table.column_map:
                data = _apply_column_map(data, table.column_map)
            data = _apply_dedup(data, table)

            # Boundary dedup: filter out rows already loaded in previous run
            prev_hashes = state.read_boundary_hashes(table.name)
            data, rows_skipped_boundary = _filter_boundary_rows(
                data,
                table.primary_key,
                table.timestamp_column,
                str(wm_last_value),
                prev_hashes,
            )

            if data.num_rows == 0:
                ended_at = datetime.now(timezone.utc)
                state.record_run(
                    run_id=run_id,
                    table_name=table.name,
                    started_at=started_at,
                    ended_at=ended_at,
                    status="success",
                    rows_extracted=0,
                    rows_loaded=0,
                    watermark_before=watermark_before,
                    watermark_after=watermark_before,
                    trigger="run",
                )
                # Update file mtime/hash but keep last_value unchanged
                state.write_watermark(
                    table.name,
                    strategy=table.strategy,
                    last_run_at=ended_at,
                    last_file_mtime=change.metadata.get("file_mtime"),
                    last_file_hash=change.metadata.get("file_hash"),
                    last_value=str(wm_last_value),
                )
                state.reset_retry(table.name)
                return RunResult(
                    table_name=table.name,
                    run_id=run_id,
                    status="success",
                    rows_loaded=0,
                )

            rows_loaded = dest.load_incremental(
                effective_target, data, run_id, table.timestamp_column
            )
        elif table.strategy == "append":
            # Append strategy: extract full source, insert without deleting existing rows
            data = source.extract(
                table.source_table, columns=prod_columns, filter=table.filter
            )
            if config.defaults.row_limit:
                data = data.slice(0, config.defaults.row_limit)
            if table.column_map:
                data = _apply_column_map(data, table.column_map)
            data = _apply_dedup(data, table)
            rows_loaded = dest.load_append(effective_target, data, run_id)
        else:
            # Full strategy, or first incremental run (no watermark yet)
            if is_incremental and table.filter:
                data = source.extract(
                    table.source_table, columns=prod_columns, filter=table.filter
                )
            else:
                data = source.extract(table.source_table, columns=prod_columns)
            if config.defaults.row_limit:
                data = data.slice(0, config.defaults.row_limit)
            if table.column_map:
                data = _apply_column_map(data, table.column_map)
            data = _apply_dedup(data, table)
            rows_loaded = dest.load_full(effective_target, data, run_id)

        # Run DQ checks after load (FR8.5: against local DuckDB table)
        try:
            from feather_etl.dq import run_dq_checks

            dq_con = dest._connect()
            try:
                dq_results = run_dq_checks(
                    dq_con,
                    table.name,
                    effective_target,
                    table.quality_checks,
                    run_id,
                    primary_key=table.primary_key,
                )
            finally:
                dq_con.close()
            for dq in dq_results:
                state.record_dq_result(
                    run_id=run_id,
                    table_name=table.name,
                    check_type=dq.check_type,
                    column_name=dq.column_name,
                    result=dq.result,
                    details=dq.details,
                )
                if dq.result == "fail":
                    alert_on_dq_failure(
                        table.name,
                        f"{dq.check_type}({dq.column_name}): {dq.details}",
                        config=config.alerts,
                    )
        except Exception as dq_err:
            logger.warning("DQ checks failed: %s", dq_err)

        # Compute new watermark value for incremental tables
        new_last_value = None
        if is_incremental and data.num_rows > 0:
            max_ts = pc.max(data.column(table.timestamp_column)).as_py()
            new_last_value = max_ts.isoformat() if max_ts else None
        elif is_incremental and wm_last_value is not None:  # pragma: no cover
            # Defensive: the subsequent-incremental path already returns
            # early when data.num_rows == 0 (see the zero-rows branch
            # above), so this elif is unreachable in practice. Kept for
            # safety in case future edits change the early-return.
            new_last_value = str(wm_last_value)

        watermark_after = new_last_value

        # Store boundary hashes for next run's dedup
        if (
            is_incremental
            and new_last_value
            and table.primary_key
            and data.num_rows > 0
        ):
            new_hashes = _compute_pk_hashes(
                data,
                table.primary_key,
                table.timestamp_column,
                new_last_value,
            )
            state.write_boundary_hashes(table.name, new_hashes)

        ended_at = datetime.now(timezone.utc)
        state.write_watermark(
            table.name,
            strategy=table.strategy,
            last_run_at=ended_at,
            last_file_mtime=change.metadata.get("file_mtime"),
            last_file_hash=change.metadata.get("file_hash"),
            last_value=new_last_value,
            last_checksum=change.metadata.get("checksum"),
            last_row_count=change.metadata.get("row_count"),
        )
        state.record_run(
            run_id=run_id,
            table_name=table.name,
            started_at=started_at,
            ended_at=ended_at,
            status="success",
            rows_extracted=rows_loaded,
            rows_loaded=rows_loaded,
            watermark_before=watermark_before,
            watermark_after=watermark_after,
            schema_changes=schema_changes_json,
            trigger="run",
        )
        state.reset_retry(table.name)
        logger.info(
            "Loaded %d rows",
            rows_loaded,
            extra={
                "table": table.name,
                "status": "success",
                "rows_loaded": rows_loaded,
            },
        )
        return RunResult(
            table_name=table.name,
            run_id=run_id,
            status="success",
            rows_loaded=rows_loaded,
        )
    except Exception as e:
        ended_at = datetime.now(timezone.utc)
        error_msg = str(e)
        logger.error(
            "Failed: %s",
            error_msg,
            extra={"table": table.name, "status": "failure", "error": error_msg},
        )
        state.record_run(
            run_id=run_id,
            table_name=table.name,
            started_at=started_at,
            ended_at=ended_at,
            status="failure",
            error_message=error_msg,
            trigger="run",
        )
        state.increment_retry(table.name)
        alert_on_failure(table.name, error_msg, config=config.alerts)
        return RunResult(
            table_name=table.name,
            run_id=run_id,
            status="failure",
            error_message=error_msg,
        )


def run_all(
    config: FeatherConfig,
    table_filter: str | None = None,
    force_views: bool = False,
) -> list[RunResult]:
    """Run all configured tables (or a single table if table_filter is set).

    After extraction, rebuilds materialized gold transforms if any tables
    succeeded and transform SQL files exist.

    Raises ValueError if table_filter names a table not in config.
    """
    working_dir = config.config_dir
    _setup_jsonl_logging(working_dir)

    tables = config.tables
    if table_filter is not None:
        tables = [t for t in config.tables if t.name == table_filter]
        if not tables:
            available = ", ".join(t.name for t in config.tables)
            raise ValueError(
                f"Table '{table_filter}' not found in config. "
                f"Available tables: {available}"
            )

    from feather_etl.curation import resolve_source

    results: list[RunResult] = []
    for table in tables:
        table_source = (
            resolve_source(table.database, config.sources)
            if table.database
            else config.sources[0]
        )
        result = run_table(config, table, working_dir, source=table_source)
        results.append(result)

    # Post-extraction transforms
    # Include skipped tables: transforms must run even when extraction is skipped.
    any_ok = any(r.status in ("success", "skipped") for r in results)
    if any_ok:
        try:
            from feather_etl.transforms import run_transforms

            run_transforms(config, force_views=force_views)
        except Exception as e:
            logger.error("Transform rebuild failed: %s", e)

    return results
