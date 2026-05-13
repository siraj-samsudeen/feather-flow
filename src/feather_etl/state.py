"""State management for feather-etl — watermarks, run history, schema versioning."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

import feather_etl

SCHEMA_VERSION = 2

# Accepted values for the _runs.trigger column. Names the CLI verb that
# wrote the row, not the invocation source. Legacy rows (written before
# this column existed) have trigger=NULL; no backfill.
VALID_TRIGGERS: frozenset[str] = frozenset({"run", "extract", "transform", "schedule"})


class StateManager:
    """Manages the feather_state.duckdb file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> duckdb.DuckDBPyConnection:
        is_new = not self.path.exists()
        con = duckdb.connect(str(self.path))
        if is_new and os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return con

    def _migrate_v1_to_v2(self, con: duckdb.DuckDBPyConnection) -> None:
        """One-time migration: add source_db to _watermarks, copy _cache_watermarks rows."""
        con.execute("ALTER TABLE _watermarks ADD COLUMN IF NOT EXISTS source_db VARCHAR")
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "_cache_watermarks" in tables:
            con.execute("""
                INSERT OR IGNORE INTO _watermarks
                    (table_name, source_db, last_file_mtime, last_file_hash,
                     last_checksum, last_row_count, last_run_at)
                SELECT
                    table_name, source_db, last_file_mtime, last_file_hash,
                    last_checksum, last_row_count, last_run_at
                FROM _cache_watermarks
            """)
            con.execute("DROP TABLE _cache_watermarks")

    def init_state(self) -> None:
        """Create all state tables. Idempotent."""
        con = self._connect()
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS _state_meta (
                    schema_version INTEGER PRIMARY KEY,
                    created_at TIMESTAMP,
                    feather_version VARCHAR
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS _watermarks (
                    table_name VARCHAR PRIMARY KEY,
                    strategy VARCHAR,
                    last_value VARCHAR,
                    last_checksum VARCHAR,
                    last_row_count INTEGER,
                    last_file_mtime DOUBLE,
                    last_file_hash VARCHAR,
                    last_run_at TIMESTAMP,
                    retry_count INTEGER DEFAULT 0,
                    retry_after TIMESTAMP,
                    boundary_hashes JSON,
                    source_db VARCHAR
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS _runs (
                    run_id VARCHAR PRIMARY KEY,
                    table_name VARCHAR,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP,
                    duration_sec DOUBLE,
                    status VARCHAR,
                    rows_extracted INTEGER,
                    rows_loaded INTEGER,
                    rows_skipped INTEGER,
                    error_message VARCHAR,
                    watermark_before VARCHAR,
                    watermark_after VARCHAR,
                    freshness_max_ts TIMESTAMP,
                    schema_changes JSON,
                    trigger VARCHAR
                )
            """)
            # Idempotent migration for state DBs created before this column
            # existed. Safe to run on every connect — ADD COLUMN IF NOT EXISTS
            # is a no-op once the column is present.
            con.execute("ALTER TABLE _runs ADD COLUMN IF NOT EXISTS trigger VARCHAR")

            con.execute("""
                CREATE TABLE IF NOT EXISTS _run_steps (
                    run_id VARCHAR,
                    step VARCHAR,
                    message VARCHAR,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS _dq_results (
                    run_id VARCHAR,
                    table_name VARCHAR,
                    check_type VARCHAR,
                    column_name VARCHAR,
                    result VARCHAR,
                    details VARCHAR,
                    checked_at TIMESTAMP
                )
            """)

            con.execute("""
                CREATE TABLE IF NOT EXISTS _schema_snapshots (
                    table_name VARCHAR,
                    column_name VARCHAR,
                    data_type VARCHAR,
                    snapshot_at TIMESTAMP,
                    PRIMARY KEY (table_name, column_name)
                )
            """)

            # Check version and handle init vs upgrade vs downgrade protection
            row = con.execute(
                "SELECT schema_version FROM _state_meta LIMIT 1"
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO _state_meta VALUES (?, ?, ?)",
                    [
                        SCHEMA_VERSION,
                        datetime.now(timezone.utc),
                        feather_etl.__version__,
                    ],
                )
            elif row[0] < SCHEMA_VERSION:
                if row[0] == 1:
                    self._migrate_v1_to_v2(con)
                con.execute(
                    "UPDATE _state_meta SET schema_version = ?", [SCHEMA_VERSION]
                )
            elif row[0] > SCHEMA_VERSION:
                raise RuntimeError(
                    f"State DB schema version {row[0]} is newer than feather-etl "
                    f"version {SCHEMA_VERSION}. Upgrade feather-etl."
                )
        finally:
            con.close()

    def read_watermark(self, table_name: str) -> dict[str, object] | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM _watermarks WHERE table_name = ?", [table_name]
            ).fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in con.description]
            return dict(zip(columns, row))
        finally:
            con.close()

    def read_cache_watermark(self, table_name: str) -> dict[str, object] | None:
        """Deprecated — delegates to read_watermark."""
        return self.read_watermark(table_name)

    def write_cache_watermark(
        self,
        table_name: str,
        source_db: str,
        last_run_at: datetime,
        last_file_mtime: float | None = None,
        last_file_hash: str | None = None,
        last_checksum: str | None = None,
        last_row_count: int | None = None,
    ) -> None:
        """Deprecated — delegates to write_watermark with source_db."""
        # Normalize checksum to str at the boundary — SQL Server's
        # CHECKSUM_AGG returns int, Postgres md5() returns a hex string.
        checksum_str = str(last_checksum) if last_checksum is not None else None
        self.write_watermark(
            table_name=table_name,
            strategy=None,
            last_run_at=last_run_at,
            last_file_mtime=last_file_mtime,
            last_file_hash=last_file_hash,
            last_checksum=checksum_str,
            last_row_count=last_row_count,
            source_db=source_db,
        )

    _SENTINEL = object()

    def write_watermark(
        self,
        table_name: str,
        strategy: str | None,
        last_run_at: datetime | None = None,
        last_file_mtime: float | None = None,
        last_file_hash: str | None = None,
        last_value: object = _SENTINEL,
        last_checksum: int | str | None = None,
        last_row_count: int | None = None,
        source_db: str | None = None,
    ) -> None:
        if last_run_at is None:
            last_run_at = datetime.now(timezone.utc)
        con = self._connect()
        try:
            existing = con.execute(
                "SELECT COUNT(*) FROM _watermarks WHERE table_name = ?", [table_name]
            ).fetchone()[0]
            if existing:
                if last_value is self._SENTINEL:
                    # Preserve existing last_value (H-1 fix)
                    con.execute(
                        "UPDATE _watermarks SET strategy = ?, last_run_at = ?, "
                        "last_file_mtime = ?, last_file_hash = ?, "
                        "last_checksum = ?, last_row_count = ?, source_db = ? "
                        "WHERE table_name = ?",
                        [
                            strategy,
                            last_run_at,
                            last_file_mtime,
                            last_file_hash,
                            last_checksum,
                            last_row_count,
                            source_db,
                            table_name,
                        ],
                    )
                else:
                    con.execute(
                        "UPDATE _watermarks SET strategy = ?, last_run_at = ?, "
                        "last_file_mtime = ?, last_file_hash = ?, last_value = ?, "
                        "last_checksum = ?, last_row_count = ?, source_db = ? "
                        "WHERE table_name = ?",
                        [
                            strategy,
                            last_run_at,
                            last_file_mtime,
                            last_file_hash,
                            last_value,
                            last_checksum,
                            last_row_count,
                            source_db,
                            table_name,
                        ],
                    )
            else:
                actual_value = None if last_value is self._SENTINEL else last_value
                con.execute(
                    "INSERT INTO _watermarks "
                    "(table_name, strategy, last_run_at, last_file_mtime, last_file_hash, "
                    "last_value, last_checksum, last_row_count, source_db) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        table_name,
                        strategy,
                        last_run_at,
                        last_file_mtime,
                        last_file_hash,
                        actual_value,
                        last_checksum,
                        last_row_count,
                        source_db,
                    ],
                )
        finally:
            con.close()

    def record_run(
        self,
        run_id: str,
        table_name: str,
        started_at: datetime,
        ended_at: datetime,
        status: str,
        rows_extracted: int = 0,
        rows_loaded: int = 0,
        rows_skipped: int = 0,
        error_message: str | None = None,
        watermark_before: str | None = None,
        watermark_after: str | None = None,
        freshness_max_ts: datetime | None = None,
        schema_changes: str | None = None,
        trigger: str | None = None,
    ) -> None:
        # Trigger validation — single chokepoint for every _runs writer.
        # None is permitted so internal call sites that don't know the verb
        # (currently none in production; reserved for tests / future legacy
        # writers) can omit it. Any non-None value MUST be in VALID_TRIGGERS.
        if trigger is not None and trigger not in VALID_TRIGGERS:
            raise ValueError(
                f"Invalid trigger value {trigger!r}. "
                f"Expected one of {sorted(VALID_TRIGGERS)} or None."
            )
        duration = (ended_at - started_at).total_seconds()
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO _runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    run_id,
                    table_name,
                    started_at,
                    ended_at,
                    duration,
                    status,
                    rows_extracted,
                    rows_loaded,
                    rows_skipped,
                    error_message,
                    watermark_before,
                    watermark_after,
                    freshness_max_ts,
                    schema_changes,
                    trigger,
                ],
            )
        finally:
            con.close()

    def record_dq_result(
        self,
        run_id: str,
        table_name: str,
        check_type: str,
        column_name: str | None,
        result: str,
        details: str,
    ) -> None:
        """Write a DQ check result to _dq_results."""
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO _dq_results VALUES (?,?,?,?,?,?,?)",
                [
                    run_id,
                    table_name,
                    check_type,
                    column_name,
                    result,
                    details,
                    datetime.now(timezone.utc),
                ],
            )
        finally:
            con.close()

    # --- Schema snapshots (V10) ---

    def save_schema_snapshot(
        self, table_name: str, schema: list[tuple[str, str]]
    ) -> None:
        """Save or replace the schema snapshot for a table."""
        con = self._connect()
        try:
            now = datetime.now(timezone.utc)
            # Delete existing snapshot for this table, then insert fresh
            con.execute(
                "DELETE FROM _schema_snapshots WHERE table_name = ?", [table_name]
            )
            for col_name, data_type in schema:
                con.execute(
                    "INSERT INTO _schema_snapshots VALUES (?,?,?,?)",
                    [table_name, col_name, data_type, now],
                )
        finally:
            con.close()

    def get_schema_snapshot(self, table_name: str) -> list[tuple[str, str]] | None:
        """Read stored schema snapshot. Returns None if no snapshot exists."""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT column_name, data_type FROM _schema_snapshots "
                "WHERE table_name = ? ORDER BY rowid",
                [table_name],
            ).fetchall()
            if not rows:
                return None
            return [(r[0], r[1]) for r in rows]
        finally:
            con.close()

    def get_history(
        self,
        table_name: str | None = None,
        limit: int = 20,
        trigger: str | None = None,
    ) -> list[dict[str, object]]:
        """Return recent runs ordered by started_at DESC.

        Optionally filter by table_name and/or trigger verb. Returns dicts
        with keys: run_id, table_name, started_at, ended_at, status,
        rows_loaded, error_message, trigger.

        When ``trigger`` is None, all rows are returned, including legacy
        rows whose trigger is NULL. When ``trigger`` is a string, only rows
        whose trigger column equals it are returned (NULL rows excluded).
        """
        con = self._connect()
        try:
            base_sql = (
                "SELECT run_id, table_name, started_at, ended_at, "
                "status, rows_loaded, error_message, trigger "
                "FROM _runs"
            )
            clauses: list[str] = []
            params: list[object] = []
            if table_name is not None:
                clauses.append("table_name = ?")
                params.append(table_name)
            if trigger is not None:
                clauses.append("trigger = ?")
                params.append(trigger)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = f"{base_sql}{where} ORDER BY started_at DESC LIMIT ?"
            params.append(limit)
            rows = con.execute(sql, params).fetchall()
            columns = [desc[0] for desc in con.description]
            return [dict(zip(columns, row)) for row in rows]
        finally:
            con.close()

    def get_status(self) -> list[dict[str, object]]:
        """Return last run per table (all-time history, not filtered by current config)."""
        con = self._connect()
        try:
            rows = con.execute("""
                SELECT r.table_name, r.status, r.rows_loaded, r.ended_at, r.run_id,
                       r.error_message, w.last_value AS watermark
                FROM _runs r
                INNER JOIN (
                    SELECT table_name, MAX(started_at) as max_started
                    FROM _runs GROUP BY table_name
                ) latest ON r.table_name = latest.table_name
                    AND r.started_at = latest.max_started
                LEFT JOIN _watermarks w ON r.table_name = w.table_name
                ORDER BY r.table_name
            """).fetchall()
            columns = [desc[0] for desc in con.description]
            return [dict(zip(columns, row)) for row in rows]
        finally:
            con.close()

    # --- Retry + backoff (V14) ---

    _BACKOFF_BASE_MINUTES = 15
    _BACKOFF_CAP_MINUTES = 120

    @staticmethod
    def _utcnow_naive() -> datetime:
        """Return current UTC time as a naive datetime (for DuckDB TIMESTAMP)."""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def increment_retry(self, table_name: str) -> None:
        """Increment retry_count and compute retry_after with linear backoff."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT retry_count FROM _watermarks WHERE table_name = ?",
                [table_name],
            ).fetchone()
            now = self._utcnow_naive()
            if row is None:
                # Table not yet in watermarks — insert with retry_count=1
                new_count = 1
                backoff_min = min(
                    new_count * self._BACKOFF_BASE_MINUTES,
                    self._BACKOFF_CAP_MINUTES,
                )
                retry_after = now + timedelta(minutes=backoff_min)
                con.execute(
                    "INSERT INTO _watermarks (table_name, retry_count, retry_after) "
                    "VALUES (?, ?, ?)",
                    [table_name, new_count, retry_after],
                )
            else:
                current = row[0] or 0
                new_count = current + 1
                backoff_min = min(
                    new_count * self._BACKOFF_BASE_MINUTES,
                    self._BACKOFF_CAP_MINUTES,
                )
                retry_after = now + timedelta(minutes=backoff_min)
                con.execute(
                    "UPDATE _watermarks SET retry_count = ?, retry_after = ? "
                    "WHERE table_name = ?",
                    [new_count, retry_after, table_name],
                )
        finally:
            con.close()

    def reset_retry(self, table_name: str) -> None:
        """Reset retry_count to 0 and clear retry_after."""
        con = self._connect()
        try:
            con.execute(
                "UPDATE _watermarks SET retry_count = 0, retry_after = NULL "
                "WHERE table_name = ?",
                [table_name],
            )
        finally:
            con.close()

    def should_skip_retry(self, table_name: str) -> tuple[bool, str | None]:
        """Check if a table is in backoff.

        Returns (True, original_error_message) if retry_after > now,
        (False, None) otherwise.
        """
        con = self._connect()
        try:
            row = con.execute(
                "SELECT retry_after FROM _watermarks WHERE table_name = ?",
                [table_name],
            ).fetchone()
            if row is None or row[0] is None:
                return False, None
            retry_after = row[0]
            if retry_after > self._utcnow_naive():
                error = self.get_last_failure_message(table_name)
                return True, error
            return False, None
        finally:
            con.close()

    def get_last_failure_message(self, table_name: str) -> str | None:
        """Return error_message from the most recent failed run for a table."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT error_message FROM _runs "
                "WHERE table_name = ? AND status = 'failure' "
                "ORDER BY started_at DESC LIMIT 1",
                [table_name],
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    # --- Boundary deduplication (V16) ---

    def write_boundary_hashes(self, table_name: str, hashes: list[str]) -> None:
        """Store PK hashes of boundary rows in _watermarks.boundary_hashes."""
        import json

        con = self._connect()
        try:
            con.execute(
                "UPDATE _watermarks SET boundary_hashes = ? WHERE table_name = ?",
                [json.dumps(hashes), table_name],
            )
        finally:
            con.close()

    def read_boundary_hashes(self, table_name: str) -> list[str]:
        """Read stored boundary hashes. Returns empty list if none."""
        import json

        con = self._connect()
        try:
            row = con.execute(
                "SELECT boundary_hashes FROM _watermarks WHERE table_name = ?",
                [table_name],
            ).fetchone()
            if row is None or row[0] is None:
                return []
            return json.loads(row[0])
        finally:
            con.close()
