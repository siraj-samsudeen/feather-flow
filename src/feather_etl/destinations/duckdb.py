"""DuckDB destination — loads data into local DuckDB file."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pyarrow as pa


SCHEMAS = ["bronze", "silver", "gold", "_quarantine"]


def _parse_qualified_table(table: str, method: str) -> tuple[str, str]:
    """Split a `schema.table` identifier, rejecting bare names with a clear error."""
    parts = table.split(".")
    if len(parts) != 2:
        raise ValueError(f"{method} expects schema.table, got: {table}")
    return parts[0], parts[1]


class _StreamingFullLoadSession:
    """Per-batch full-refresh load session. Atomicity matches `load_full`.

    Each `append(batch)` INSERTs into a staging table `<schema>.<table>_new`
    (auto-commit per INSERT — short WAL, no multi-hour transaction). On clean
    `__exit__`, the swap (DROP final + RENAME staging) runs inside one small
    transaction so consumers never see a half-replaced table. On exception,
    the staging table is dropped — final table is never touched mid-extract.
    """

    def __init__(
        self, dest: "DuckDBDestination", table: str, run_id: str
    ) -> None:
        self._dest = dest
        self.run_id = run_id
        self.schema_name, self.table_name = _parse_qualified_table(
            table, "streaming_full_load"
        )
        self.staging = f"{self.schema_name}.{self.table_name}_new"
        self.final = f"{self.schema_name}.{self.table_name}"
        self.con: duckdb.DuckDBPyConnection | None = None
        self._staging_created = False
        self.rows_loaded = 0

    def __enter__(self) -> "_StreamingFullLoadSession":
        self.con = self._dest._connect()
        # Clean any stale staging from a previously crashed run.
        self.con.execute(f"DROP TABLE IF EXISTS {self.staging}")
        return self

    def append(self, batch: pa.RecordBatch) -> int:
        if self.con is None:
            raise RuntimeError("streaming_full_load session is not active")
        self.con.register("_arrow_batch", batch)
        try:
            if not self._staging_created:
                # First batch defines the schema for the staging table.
                # WHERE 1=0 → schema only, no rows inserted by the CREATE.
                self.con.execute(
                    f"CREATE TABLE {self.staging} AS "
                    f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                    f"'{self.run_id}' AS _etl_run_id FROM _arrow_batch WHERE 1=0"
                )
                self._staging_created = True
            if batch.num_rows > 0:
                self.con.execute(
                    f"INSERT INTO {self.staging} "
                    f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                    f"'{self.run_id}' AS _etl_run_id FROM _arrow_batch"
                )
                self.rows_loaded += batch.num_rows
        finally:
            self.con.unregister("_arrow_batch")
        return batch.num_rows

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self.con is None:
            return False
        try:
            if exc_type is None and self._staging_created:
                self.con.execute("BEGIN TRANSACTION")
                try:
                    self.con.execute(f"DROP TABLE IF EXISTS {self.final}")
                    self.con.execute(
                        f"ALTER TABLE {self.staging} RENAME TO {self.table_name}"
                    )
                    self.con.execute("COMMIT")
                except Exception:
                    self.con.execute("ROLLBACK")
                    raise
            else:
                # Failure path (or zero batches yielded): drop staging if it exists.
                try:
                    self.con.execute(f"DROP TABLE IF EXISTS {self.staging}")
                except Exception:
                    pass
        finally:
            self.con.close()
            self.con = None
        return False  # never swallow exceptions


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

    def streaming_full_load(
        self, table: str, run_id: str
    ) -> "_StreamingFullLoadSession":
        """Open a per-batch streaming-load session that preserves swap atomicity.

        Usage:
            with dest.streaming_full_load("bronze.foo", run_id) as session:
                for batch in source.extract_batches("foo"):
                    session.append(batch)
            rows = session.rows_loaded

        Atomicity contract: the final destination table is only replaced on
        clean `__exit__`. Mid-extract failures leave a `<table>_new` staging
        table that the next run drops on entry — the previous good data
        in the final table is never touched.
        """
        return _StreamingFullLoadSession(self, table, run_id)

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
