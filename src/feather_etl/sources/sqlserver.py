"""SQL Server source — reads from SQL Server via pyodbc."""

from __future__ import annotations

import datetime
import decimal
import platform
import uuid
from pathlib import Path
from typing import ClassVar, Iterator

import pyarrow as pa
import pyodbc

from feather_etl.sources import ChangeResult, StreamSchema
from feather_etl.sources.database_source import DatabaseSource


def _sqlserver_coerce_row(val, _col_name):
    """Per-cell coercion for pyodbc types PyArrow can't auto-convert."""
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    return val

_SQLSERVER_CONN_TEMPLATE = (
    "DRIVER={{ODBC Driver 18 for SQL Server}};"
    "SERVER={host},{port};DATABASE={database};UID={user};PWD={password};"
    "TrustServerCertificate=yes"
)

# pyodbc type code → PyArrow type mapping
# pyodbc cursor.description[1] returns Python types — map them all
_PYODBC_TYPE_MAP: dict[type, pa.DataType] = {
    int: pa.int64(),
    float: pa.float64(),
    str: pa.string(),
    bool: pa.bool_(),
    bytes: pa.binary(),
    bytearray: pa.binary(),
    decimal.Decimal: pa.float64(),
    datetime.datetime: pa.timestamp("us"),
    datetime.date: pa.date32(),
    datetime.time: pa.time64("us"),
    uuid.UUID: pa.string(),
}

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


def _pyodbc_type_to_arrow(type_code: type) -> pa.DataType:
    """Map a pyodbc cursor.description type_code to a PyArrow type."""
    return _PYODBC_TYPE_MAP.get(type_code, pa.string())


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
        self._last_error: str | None = None

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
            schemas.append(
                StreamSchema(
                    name=qualified,
                    columns=[(c[0], c[1]) for c in cols],
                    primary_key=None,
                    supports_incremental=True,
                )
            )

        cursor.close()
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
        """Stream `pa.RecordBatch` chunks from SQL Server with progress heartbeat."""
        from feather_etl.sources.fetch_batches import fetch_batches

        if columns:
            bad = [c for c in columns if "]" in c]
            if bad:
                raise ValueError(
                    f"SQL Server column names containing ']' cannot be "
                    f"bracket-quoted: {bad}"
                )

        con = pyodbc.connect(self.connection_string)
        try:
            cursor = con.cursor()
            cursor.execute("SET NOCOUNT ON")

            col_clause = ", ".join(f"[{c}]" for c in columns) if columns else "*"
            where = self._build_where_clause(filter, watermark_column, watermark_value)
            query = f"SELECT {col_clause} FROM {table}{where}"
            cursor.execute(query)

            if cursor.description is None:
                cursor.close()
                return

            col_names = [desc[0] for desc in cursor.description]
            col_types = [_pyodbc_type_to_arrow(desc[1]) for desc in cursor.description]
            arrow_schema = pa.schema(
                [pa.field(name, typ) for name, typ in zip(col_names, col_types)]
            )

            try:
                yield from fetch_batches(
                    cursor=cursor,
                    arrow_schema=arrow_schema,
                    col_names=col_names,
                    batch_size=self.batch_size,
                    table_label=table,
                    coerce_row=_sqlserver_coerce_row,
                    heartbeat_every_rows=heartbeat_every_rows,
                    heartbeat_every_seconds=heartbeat_every_seconds,
                )
            finally:
                cursor.close()
        finally:
            con.close()

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
