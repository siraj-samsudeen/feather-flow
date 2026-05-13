"""DuckDB destination — loads data into local DuckDB file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import duckdb
import pyarrow as pa

if TYPE_CHECKING:
    from feather_etl.extract_windows import WindowResult, WindowSpec
    from feather_etl.state import StateManager


SCHEMAS = ["bronze", "silver", "gold", "_quarantine"]


def _parse_qualified_table(table: str, method: str) -> tuple[str, str]:
    """Split a `schema.table` identifier, rejecting bare names with a clear error."""
    parts = table.split(".")
    if len(parts) != 2:
        raise ValueError(f"{method} expects schema.table, got: {table}")
    return parts[0], parts[1]


class DuckDBDestination:
    """Loads PyArrow Tables into a local DuckDB file."""

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

    def setup_schemas(self) -> None:
        con = self._connect()
        for schema in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.close()

    def load_full(self, table: str, data: pa.Table, run_id: str) -> int:
        """Full refresh via swap pattern (FR4.3)."""
        schema, table_name = _parse_qualified_table(table, "load_full")
        staging = f"{schema}.{table_name}_new"
        final = f"{schema}.{table_name}"

        con = self._connect()

        # Register arrow data so DuckDB can query it
        con.register("_arrow_data", data)

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                f"CREATE TABLE {staging} AS "
                f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                f"'{run_id}' AS _etl_run_id FROM _arrow_data"
            )
            con.execute(f"DROP TABLE IF EXISTS {final}")
            # RENAME keeps table in same schema; result is {schema}.{table_name}
            con.execute(f"ALTER TABLE {staging} RENAME TO {table_name}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("_arrow_data")
            con.close()

        return data.num_rows

    def load_batched_append(
        self,
        table: str,
        window: "WindowSpec",
        batches: "Iterator[pa.RecordBatch]",
        run_id: str,
        state: "StateManager",
        transport_used: str,
    ) -> "WindowResult":
        """Transactional per-window batched append (#62).

        Two sequential commits — bronze first, then the commit-log row:

            # Transaction 1: all bronze writes atomic within the destination DB
            BEGIN;
              CREATE TABLE IF NOT EXISTS <table> (...);
              DELETE FROM <table> WHERE <window.window_predicate>;
              for each batch: INSERT INTO <table> ...;
            COMMIT;

            # Transaction 2: record the window in the state DB
            state.record_committed_window(...)

        If the process crashes between the two commits the window is absent
        from _extract_windows, so the next run re-runs it. The DELETE before
        INSERT makes the window idempotent — a retry is safe.

        On any exception in the batch loop, ROLLBACK leaves the bronze table
        unchanged and the window is NOT marked committed.

        Note: DuckDB 1.4+ enforces single-database transactions and does not
        allow cross-DB writes in one txn, so we cannot fold both commits into
        one. The idempotent DELETE + INSERT contract compensates.
        """
        from feather_etl.extract_windows import WindowResult

        schema_name, table_name = _parse_qualified_table(table, "load_batched_append")
        final = f"{schema_name}.{table_name}"

        con = self._connect()
        rows_loaded = 0
        schema_created = False
        try:
            con.execute("BEGIN TRANSACTION")
            try:
                for batch in batches:
                    con.register("_arrow_batch", batch)
                    try:
                        if not schema_created:
                            # First batch (even zero rows) defines the schema.
                            con.execute(
                                f"CREATE TABLE IF NOT EXISTS {final} AS "
                                f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                                f"CAST(NULL AS VARCHAR) AS _etl_run_id "
                                f"FROM _arrow_batch WHERE 1=0"
                            )
                            # Window-level DELETE happens exactly once, after
                            # the table exists, before any INSERT.
                            con.execute(
                                f"DELETE FROM {final} WHERE {window.window_predicate}"
                            )
                            schema_created = True
                        if batch.num_rows > 0:
                            con.execute(
                                f"INSERT INTO {final} "
                                f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                                f"'{run_id}' AS _etl_run_id FROM _arrow_batch"
                            )
                            rows_loaded += batch.num_rows
                    finally:
                        con.unregister("_arrow_batch")

                # Edge case: iterator yielded zero batches AND table doesn't exist.
                # Without a schema we can't CREATE; require the table to exist
                # from a prior window.
                if not schema_created:
                    exists = con.execute(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = ? AND table_name = ?",
                        [schema_name, table_name],
                    ).fetchone()[0]
                    if not exists:
                        raise RuntimeError(
                            f"load_batched_append: iterator yielded no batches and "
                            f"{final} does not exist — cannot infer schema."
                        )
                    # Table exists from prior window — just DELETE for idempotency.
                    con.execute(f"DELETE FROM {final} WHERE {window.window_predicate}")

                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()

        # Record the committed window in the state DB. This is a separate
        # transaction — see docstring for the crash-safety contract.
        state.record_committed_window(
            table_name=final,
            window_key=window.window_key,
            window_start=window.window_start,
            window_end=window.window_end,
            rows_loaded=rows_loaded,
            transport_used=transport_used,
            run_id=run_id,
        )

        return WindowResult(
            window_key=window.window_key,
            rows_loaded=rows_loaded,
        )

    def load_append(self, table: str, data: pa.Table, run_id: str) -> int:
        """Append-only insert — never deletes existing rows."""
        schema, table_name = _parse_qualified_table(table, "load_append")
        final = f"{schema}.{table_name}"

        con = self._connect()

        con.register("_arrow_data", data)

        con.execute("BEGIN TRANSACTION")
        try:
            # Create the table (with ETL metadata) if it doesn't exist yet.
            # The WHERE 1=0 ensures no rows are inserted on CREATE — only schema.
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {final} AS "
                f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                f"'{run_id}' AS _etl_run_id FROM _arrow_data WHERE 1=0"
            )
            con.execute(
                f"INSERT INTO {final} "
                f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                f"'{run_id}' AS _etl_run_id FROM _arrow_data"
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("_arrow_data")
            con.close()

        return data.num_rows

    def load_incremental(
        self,
        table: str,
        data: pa.Table,
        run_id: str,
        timestamp_column: str,
    ) -> int:
        """Partition-overwrite: DELETE rows >= min batch timestamp, then INSERT."""
        if data.num_rows == 0:
            return 0

        import pyarrow.compute as pc

        min_ts = pc.min(data.column(timestamp_column)).as_py()

        schema, table_name = _parse_qualified_table(table, "load_incremental")
        final = f"{schema}.{table_name}"

        con = self._connect()

        con.register("_arrow_data", data)

        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DELETE FROM {final} WHERE {timestamp_column} >= ?", [min_ts])
            con.execute(
                f"INSERT INTO {final} "
                f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                f"'{run_id}' AS _etl_run_id FROM _arrow_data"
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("_arrow_data")
            con.close()

        return data.num_rows
