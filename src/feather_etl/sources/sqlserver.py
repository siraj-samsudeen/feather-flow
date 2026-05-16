"""SQL Server source — reads from SQL Server via pyodbc."""

from __future__ import annotations

import logging
import platform
import re
from pathlib import Path
from typing import Any, ClassVar, Iterator

import pyarrow as pa
import pyodbc

from feather_etl.sources import ChangeResult, StreamSchema
from feather_etl.sources.database_source import DatabaseSource

_log = logging.getLogger(__name__)

# Validates identifiers (column / schema / table names) before interpolating
# them into SQL.  Allows letters, digits, underscore and internal spaces
# (SQL Server permits spaces in identifiers when bracket-quoted).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w\s]*$")


_SQLSERVER_CONN_TEMPLATE = (
    "DRIVER={{ODBC Driver 18 for SQL Server}};"
    "SERVER={host},{port};DATABASE={database};UID={user};PWD={password};"
    "TrustServerCertificate=yes"
)

# SQL Server INFORMATION_SCHEMA type name → PyArrow type
_SQL_TYPE_MAP: dict[str, pa.DataType] = {
    "int": pa.int64(),
    "bigint": pa.int64(),
    "smallint": pa.int16(),
    "tinyint": pa.int8(),
    "bit": pa.bool_(),
    "float": pa.float64(),
    "real": pa.float64(),
    "decimal": pa.float64(),
    "numeric": pa.float64(),
    "money": pa.float64(),
    "smallmoney": pa.float64(),
    "varchar": pa.string(),
    "nvarchar": pa.string(),
    "char": pa.string(),
    "nchar": pa.string(),
    "text": pa.string(),
    "ntext": pa.string(),
    "datetime": pa.timestamp("us"),
    "datetime2": pa.timestamp("us"),
    "smalldatetime": pa.timestamp("us"),
    "date": pa.date32(),
    "time": pa.time64("us"),
    "uniqueidentifier": pa.string(),
    "binary": pa.binary(),
    "varbinary": pa.binary(),
    "image": pa.binary(),
    "xml": pa.string(),
}


def _is_missing_odbc_driver_18_error(message: str, connection_string: str) -> bool:
    """Return True only when evidence points to missing ODBC Driver 18."""
    msg = message.lower()
    conn = connection_string.lower()
    targets_driver_18 = "odbc driver 18" in conn
    mentions_driver_18 = "odbc driver 18" in msg
    explicit_driver_18 = targets_driver_18 or mentions_driver_18

    if "can't open lib" in msg and explicit_driver_18:
        return True
    if "file not found" in msg and explicit_driver_18:
        return True

    # IM002 / data source name not found can also mean a missing DSN.
    if ("im002" in msg or "data source name not found" in msg) and explicit_driver_18:
        return True
    return False


class SqlServerSource(DatabaseSource):
    """Source that reads tables from SQL Server via pyodbc."""

    type: ClassVar[str] = "sqlserver"

    def __init__(
        self,
        connection_string: str,
        *,
        name: str = "",
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        databases: list[str] | None = None,
        batch_size: int = 120_000,
        transport: str = "arrow-odbc",
    ) -> None:
        super().__init__(connection_string)
        self.name = name
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.databases = databases
        self.batch_size = batch_size

        # Resolve at construction so config errors surface at config-load,
        # not at extract time after the user has waited for connect+plan.
        from feather_etl.transports.registry import get_transport_class

        self._transport_cls = get_transport_class(transport)
        self._transport = transport

        self._last_error: str | None = None

    @property
    def transport_name(self) -> str:
        return self._transport

    @property
    def supports_partition_on(self) -> bool:
        """Whether the configured transport supports parallel reads via
        partition_on. Read by #63's pre-flight planner to decide whether
        the `large` bucket can recommend partition_num > 1 on this source.
        """
        return self._transport_cls().supports_partition_on()

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "SqlServerSource":
        name = entry.get("name", "")
        explicit_conn = entry.get("connection_string")
        host = entry.get("host")
        port = entry.get("port", 1433)
        user = entry.get("user")
        password = entry.get("password")
        database = entry.get("database")
        databases = entry.get("databases")

        if database is not None and databases is not None:
            raise ValueError("database and databases are mutually exclusive; use one.")
        if databases is not None and not databases:
            raise ValueError("databases list must be non-empty.")

        if explicit_conn:
            conn_str = explicit_conn
        elif host:
            conn_str = _SQLSERVER_CONN_TEMPLATE.format(
                host=host,
                port=port,
                database=database or "",
                user=user or "",
                password=password or "",
            )
        else:
            raise ValueError(
                "sqlserver source requires either 'connection_string' or "
                "'host' (with user/password/database)."
            )

        source = cls(
            connection_string=conn_str,
            name=name,
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            databases=databases,
            transport=entry.get("transport", "arrow-odbc"),
        )
        source._explicit_name = bool(entry.get("name"))
        return source

    def validate_source_table(self, source_table: str) -> list[str]:
        # SQL Server: lenient — allow 'schema.table' or 'table'. Real
        # validation happens at extract time when the server rejects bad SQL.
        return []

    def list_databases(self) -> list[str]:
        """Return user databases on the server (excludes master, tempdb, model, msdb).

        Raises pyodbc.Error on connection/query failure (caller handles).
        """
        con = pyodbc.connect(self.connection_string, timeout=10)
        try:
            cursor = con.cursor()
            cursor.execute(
                "SELECT name FROM sys.databases "
                "WHERE name NOT IN ('master', 'tempdb', 'model', 'msdb') "
                "ORDER BY name"
            )
            rows = cursor.fetchall()
            cursor.close()
            return [r[0] for r in rows]
        finally:
            con.close()

    def _format_watermark(self, value: str) -> str:
        """SQL Server datetime: replace ISO 'T' separator, truncate to 3ms precision."""
        wm_val = value.replace("T", " ")
        if "." in wm_val:
            base, frac = wm_val.rsplit(".", 1)
            wm_val = f"{base}.{frac[:3]}"
        return wm_val

    def check(self) -> bool:
        self._last_error = None
        try:
            con = pyodbc.connect(self.connection_string, timeout=10)
            con.close()
            return True
        except pyodbc.Error as e:
            msg = str(e)
            if _is_missing_odbc_driver_18_error(msg, self.connection_string):
                if platform.system() == "Darwin":
                    hint = (
                        "\n  Hint: ODBC Driver 18 for SQL Server is not installed."
                        "\n  Fix:  brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release"
                        "\n        HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18"
                        "\n        odbcinst -q -d  # verify"
                    )
                elif platform.system() == "Windows":
                    hint = (
                        "\n  Hint: ODBC Driver 18 for SQL Server is not installed."
                        "\n  See:  https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server"
                    )
                else:
                    hint = (
                        "\n  Hint: ODBC Driver 18 for SQL Server is not installed."
                        "\n  See:  https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server"
                    )
                msg += hint
            self._last_error = msg
            return False

    def discover(self) -> list[StreamSchema]:
        con = pyodbc.connect(self.connection_string)
        try:
            cursor = con.cursor()

            # Get all user tables
            cursor.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME "
                "FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' "
                "ORDER BY TABLE_SCHEMA, TABLE_NAME"
            )
            tables = cursor.fetchall()

            schemas: list[StreamSchema] = []
            for schema_name, table_name in tables:
                qualified = f"{schema_name}.{table_name}"
                cursor.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
                    "ORDER BY ORDINAL_POSITION",
                    [schema_name, table_name],
                )
                cols = cursor.fetchall()

                # Populate primary_key from INFORMATION_SCHEMA.  A single query per
                # table; negligible overhead.  Falls back to None on any error (e.g.
                # missing SELECT permission on INFORMATION_SCHEMA.KEY_COLUMN_USAGE).
                primary_key: list[str] | None = None
                try:
                    cursor.execute(
                        "SELECT kcu.COLUMN_NAME "
                        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
                        "JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
                        "  ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME "
                        "WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' "
                        "  AND kcu.TABLE_SCHEMA = ? "
                        "  AND kcu.TABLE_NAME = ? "
                        "ORDER BY kcu.ORDINAL_POSITION",
                        [schema_name, table_name],
                    )
                    pk_rows = cursor.fetchall()
                    if pk_rows:
                        primary_key = [r[0] for r in pk_rows]
                except pyodbc.Error:
                    _log.warning(
                        "Could not read PK for %s.%s from INFORMATION_SCHEMA; "
                        "falling back to primary_key=None",
                        schema_name,
                        table_name,
                    )

                schemas.append(
                    StreamSchema(
                        name=qualified,
                        columns=[(c[0], c[1]) for c in cols],
                        primary_key=primary_key,
                        supports_incremental=True,
                    )
                )

            cursor.close()
        finally:
            con.close()
        return schemas

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        con = pyodbc.connect(self.connection_string)
        cursor = con.cursor()

        parts = table.split(".")
        if len(parts) == 2:
            schema_name, table_name = parts
        else:
            schema_name, table_name = "dbo", parts[0]

        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
            "ORDER BY ORDINAL_POSITION",
            [schema_name, table_name],
        )
        cols = cursor.fetchall()
        cursor.close()
        con.close()
        return [(c[0], c[1]) for c in cols]

    def extract_batches(
        self,
        table: str,
        columns: list[str] | None = None,
        filter: str | None = None,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]:
        """Stream `pa.RecordBatch` chunks via the configured transport."""
        if columns:
            bad = [c for c in columns if "]" in c]
            if bad:
                raise ValueError(
                    f"SQL Server column names containing ']' cannot be "
                    f"bracket-quoted: {bad}"
                )

        col_clause = ", ".join(f"[{c}]" for c in columns) if columns else "*"
        where = self._build_where_clause(filter, watermark_column, watermark_value)
        query = f"SELECT {col_clause} FROM {table}{where}"

        transport = self._transport_cls()
        yield from transport.stream_batches(
            self.connection_string,
            query,
            batch_size=self.batch_size,
            table_label=table,
            heartbeat_every_rows=heartbeat_every_rows,
            heartbeat_every_seconds=heartbeat_every_seconds,
        )

    def cheap_rowcount(self, table: str) -> int:
        """Return an approximate row count using sys.dm_db_partition_stats.

        This DMV is always up-to-date (updated by SQL Server after each
        DML operation) and runs in microseconds — much cheaper than
        ``SELECT COUNT(*)``.  Sums index_id IN (0, 1) to avoid double-
        counting on tables with both a heap (0) and a clustered index (1).

        Raises pyodbc.Error on connection or permission failure.
        """
        con = pyodbc.connect(self.connection_string)
        try:
            cursor = con.cursor()
            cursor.execute(
                "SELECT SUM(p.row_count) "
                "FROM sys.dm_db_partition_stats p "
                "JOIN sys.indexes i "
                "  ON p.object_id = i.object_id AND p.index_id = i.index_id "
                "WHERE i.index_id IN (0, 1) "
                "  AND p.object_id = OBJECT_ID(?)",
                [table],
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
            con.close()
        return int(row[0]) if row and row[0] is not None else 0

    def get_window_range(self, table: str, column: str) -> tuple[Any, Any] | None:
        """Return (MIN, MAX) of *column* in *table*, or None for an empty table.

        **Performance note:** this runs a full table scan unless *column* is
        indexed.  The operator should ensure the window column (typically a
        datetime watermark) has an index on the source table.

        *column* and the schema/table components of *table* are validated
        against ``^[A-Za-z_][\\w\\s]*$`` before interpolation to prevent
        SQL injection.  The planner (Task 9) additionally gates on known-safe
        column names from the discovered schema.

        Raises:
            ValueError: if *column* or any identifier in *table* fails
                validation.
            pyodbc.Error: on connection or permission failure.
        """
        if not _IDENTIFIER_RE.match(column):
            raise ValueError(
                f"Invalid column name {column!r}: must match ^[A-Za-z_][\\w\\s]*$"
            )

        # Parse schema.table — default schema to dbo
        parts = table.split(".")
        if len(parts) == 2:
            schema_name, table_name = parts
        else:
            schema_name, table_name = "dbo", parts[0]

        for ident, label in ((schema_name, "schema"), (table_name, "table")):
            if not _IDENTIFIER_RE.match(ident):
                raise ValueError(
                    f"Invalid {label} name {ident!r}: must match ^[A-Za-z_][\\w\\s]*$"
                )

        sql = (
            f"SELECT MIN([{column}]), MAX([{column}]) "
            f"FROM [{schema_name}].[{table_name}]"
        )
        con = pyodbc.connect(self.connection_string)
        try:
            cursor = con.cursor()
            cursor.execute(sql)
            row = cursor.fetchone()
        finally:
            cursor.close()
            con.close()

        if row is None or (row[0] is None and row[1] is None):
            return None
        return (row[0], row[1])

    def detect_changes(
        self, table: str, last_state: dict[str, object] | None = None
    ) -> ChangeResult:
        # For incremental strategy, always return changed — watermark handles skip
        # For full strategy, use CHECKSUM_AGG
        if last_state is not None and last_state.get("strategy") == "incremental":
            return ChangeResult(changed=True, reason="incremental")

        try:
            con = pyodbc.connect(self.connection_string)
            cursor = con.cursor()
            cursor.execute(
                f"SELECT CHECKSUM_AGG(BINARY_CHECKSUM(*)) AS checksum, "
                f"COUNT(*) AS row_count FROM {table}"
            )
            row = cursor.fetchone()
            cursor.close()
            con.close()
        except pyodbc.Error:
            # If we can't compute checksum, assume changed
            return ChangeResult(changed=True, reason="checksum_error")

        if row is None:
            return ChangeResult(changed=True, reason="first_run")

        current_checksum = row[0]
        current_row_count = row[1]

        # First run — no prior state
        if last_state is None or last_state.get("last_checksum") is None:
            return ChangeResult(
                changed=True,
                reason="first_run",
                metadata={
                    "checksum": current_checksum,
                    "row_count": current_row_count,
                },
            )

        # Compare against previous
        if current_checksum == last_state.get(
            "last_checksum"
        ) and current_row_count == last_state.get("last_row_count"):
            return ChangeResult(changed=False, reason="unchanged")

        return ChangeResult(
            changed=True,
            reason="checksum_changed",
            metadata={
                "checksum": current_checksum,
                "row_count": current_row_count,
            },
        )
