"""MySQL source — reads from MySQL via mysql-connector-python."""

from __future__ import annotations

import decimal
from pathlib import Path
from typing import ClassVar, Iterator

import mysql.connector
import pyarrow as pa
from mysql.connector.constants import FieldFlag

from feather_etl.sources import ChangeResult, StreamSchema
from feather_etl.sources.database_source import DatabaseSource


def _mysql_coerce_row(val, _col_name):
    if isinstance(val, decimal.Decimal):
        return float(val)
    return val

# mysql.connector FieldType constants → PyArrow type.
#
# BLOB-family codes (249/250/251/252) are intentionally absent: mysql-connector
# reuses code 252 for both ``BLOB`` and ``TEXT``, and the family more broadly
# overlaps with their binary/text variants. The only discriminator is the
# ``FieldFlag.BINARY`` bit on the per-column ``cursor.description`` tuple, so
# those codes are handled dynamically in ``_mysql_field_type_to_arrow``.
_MYSQL_FIELD_TYPE_MAP: dict[int, pa.DataType] = {
    0: pa.float64(),  # DECIMAL
    1: pa.int8(),  # TINY
    2: pa.int16(),  # SHORT
    3: pa.int64(),  # LONG
    4: pa.float32(),  # FLOAT
    5: pa.float64(),  # DOUBLE
    7: pa.timestamp("us"),  # TIMESTAMP
    8: pa.int64(),  # LONGLONG
    9: pa.int64(),  # INT24
    10: pa.date32(),  # DATE
    11: pa.time64("us"),  # TIME
    12: pa.timestamp("us"),  # DATETIME
    13: pa.int16(),  # YEAR
    15: pa.string(),  # VARCHAR
    16: pa.bool_(),  # BIT
    246: pa.float64(),  # NEWDECIMAL
    253: pa.string(),  # VAR_STRING
    254: pa.string(),  # STRING
}

# BLOB / TEXT family — code 252 is shared between BLOB and TEXT, 249/250/251
# similarly straddle TINYBLOB↔TINYTEXT etc. Disambiguated by FieldFlag.BINARY.
_BLOB_TYPE_CODES: frozenset[int] = frozenset({249, 250, 251, 252})

# INFORMATION_SCHEMA DATA_TYPE string → PyArrow type
_MYSQL_INFO_SCHEMA_TYPE_MAP: dict[str, pa.DataType] = {
    "int": pa.int64(),
    "bigint": pa.int64(),
    "smallint": pa.int16(),
    "tinyint": pa.int8(),
    "mediumint": pa.int64(),
    "float": pa.float32(),
    "double": pa.float64(),
    "decimal": pa.float64(),
    "numeric": pa.float64(),
    "bit": pa.bool_(),
    "boolean": pa.bool_(),
    "char": pa.string(),
    "varchar": pa.string(),
    "text": pa.string(),
    "tinytext": pa.string(),
    "mediumtext": pa.string(),
    "longtext": pa.string(),
    "enum": pa.string(),
    "set": pa.string(),
    "date": pa.date32(),
    "time": pa.time64("us"),
    "datetime": pa.timestamp("us"),
    "timestamp": pa.timestamp("us"),
    "year": pa.int16(),
    "binary": pa.binary(),
    "varbinary": pa.binary(),
    "blob": pa.binary(),
    "tinyblob": pa.binary(),
    "mediumblob": pa.binary(),
    "longblob": pa.binary(),
    "json": pa.string(),
}


def _mysql_field_type_to_arrow(type_code: int, flags: int = 0) -> pa.DataType:
    """Map a mysql.connector FieldType code (+ field flags) to a PyArrow type.

    ``flags`` is the bitmask from ``cursor.description[i][7]``. It only
    matters for the BLOB family (codes 249-252), which mysql-connector
    overloads: ``FieldFlag.BINARY`` (=128) set → genuine binary column
    (``BLOB``, ``VARBINARY``); unset → text column (``TEXT``,
    ``TINYTEXT``, ``MEDIUMTEXT``, ``LONGTEXT``). All other type codes
    ignore ``flags``.
    """
    if type_code in _BLOB_TYPE_CODES:
        return pa.binary() if flags & FieldFlag.BINARY else pa.string()
    return _MYSQL_FIELD_TYPE_MAP.get(type_code, pa.string())


class MySQLSource(DatabaseSource):
    """Source that reads tables from MySQL via mysql-connector-python."""

    type: ClassVar[str] = "mysql"

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
        self._connect_kwargs: dict = {}
        self._explicit_name: bool = False

    def _connect(self):
        """Return a MySQL connection using stored kwargs or connection_string."""
        if self._connect_kwargs:
            return mysql.connector.connect(**self._connect_kwargs)
        return mysql.connector.connect(connection_string=self.connection_string)

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "MySQLSource":
        name = entry.get("name", "")
        explicit_conn = entry.get("connection_string")
        host = entry.get("host")
        port = entry.get("port", 3306)
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
            connect_kwargs: dict = {}
        elif host:
            # charset=utf8mb4 + use_unicode=True ensure mysql-connector returns
            # string columns as Python ``str`` rather than ``bytes``. Without
            # them, the C extension's default behaviour makes pyarrow infer
            # ``binary()`` for VARCHAR/TEXT columns even though our schema
            # declares ``string()`` — landing every text column in destination
            # DuckDB as BLOB. See issue #52.
            connect_kwargs = {
                "host": host,
                "port": port,
                "charset": "utf8mb4",
                "use_unicode": True,
            }
            if database:
                connect_kwargs["database"] = database
            if user:
                connect_kwargs["user"] = user
            if password:
                connect_kwargs["password"] = password
            conn_str = (
                f"host={host};port={port};database={database or ''};user={user or ''}"
            )
        else:
            raise ValueError(
                "mysql source requires either 'connection_string' or 'host'."
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
        source._connect_kwargs = connect_kwargs
        source._explicit_name = bool(entry.get("name"))
        return source

    def validate_source_table(self, source_table: str) -> list[str]:
        return []

    def check(self) -> bool:
        self._last_error = None
        try:
            conn = self._connect()
            conn.close()
            return True
        except mysql.connector.Error as e:
            self._last_error = str(e)
            return False

    def discover(self) -> list[StreamSchema]:
        if not self.database:
            raise ValueError("discover() requires database to be set.")
        conn = self._connect()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT TABLE_NAME "
                "FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = %s "
                "AND TABLE_TYPE = 'BASE TABLE' "
                "ORDER BY TABLE_NAME",
                (self.database,),
            )
            tables = cursor.fetchall()

            schemas: list[StreamSchema] = []
            for (table_name,) in tables:
                cursor.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (self.database, table_name),
                )
                cols = cursor.fetchall()
                schemas.append(
                    StreamSchema(
                        name=table_name,
                        columns=[(c[0], c[1]) for c in cols],
                        primary_key=None,
                        supports_incremental=True,
                    )
                )

            cursor.close()
            return schemas
        finally:
            conn.close()

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION",
                (self.database, table),
            )
            cols = cursor.fetchall()
            cursor.close()
            return [(c[0], c[1]) for c in cols]
        finally:
            conn.close()

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
        """Stream `pa.RecordBatch` chunks from MySQL with progress heartbeat."""
        from feather_etl.sources.fetch_batches import fetch_batches

        if columns:
            bad = [c for c in columns if "`" in c]
            if bad:
                raise ValueError(
                    f"MySQL column names containing '`' cannot be "
                    f"backtick-quoted: {bad}"
                )

        conn = self._connect()
        try:
            cursor = conn.cursor()
            col_clause = ", ".join(f"`{c}`" for c in columns) if columns else "*"
            where = self._build_where_clause(filter, watermark_column, watermark_value)
            query = f"SELECT {col_clause} FROM {table}{where}"
            cursor.execute(query)

            if cursor.description is None:
                cursor.close()
                return

            col_names = [desc[0] for desc in cursor.description]
            # desc[7] (flags) carries the BLOB-vs-TEXT discriminator — see
            # the _MYSQL_FIELD_TYPE_MAP docstring above for the rationale.
            col_types = [
                _mysql_field_type_to_arrow(desc[1], desc[7])
                for desc in cursor.description
            ]
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
                    coerce_row=_mysql_coerce_row,
                    heartbeat_every_rows=heartbeat_every_rows,
                    heartbeat_every_seconds=heartbeat_every_seconds,
                )
            finally:
                cursor.close()
        finally:
            conn.close()

    def detect_changes(
        self, table: str, last_state: dict[str, object] | None = None
    ) -> ChangeResult:
        if last_state is not None and last_state.get("strategy") == "incremental":
            return ChangeResult(changed=True, reason="incremental")

        try:
            conn = self._connect()
            cursor = conn.cursor()
            qualified = f"{self.database}.{table}" if self.database else table
            cursor.execute(f"CHECKSUM TABLE {qualified}")
            row = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(*) FROM {qualified}")
            count_row = cursor.fetchone()
            cursor.close()
            conn.close()
        except mysql.connector.Error:
            return ChangeResult(changed=True, reason="checksum_error")

        if row is None:
            return ChangeResult(changed=True, reason="first_run")

        current_checksum = row[1]
        current_row_count = count_row[0] if count_row else 0

        if last_state is None or last_state.get("last_checksum") is None:
            return ChangeResult(
                changed=True,
                reason="first_run",
                metadata={
                    "checksum": current_checksum,
                    "row_count": current_row_count,
                },
            )

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

    def list_databases(self) -> list[str]:
        """Return user databases on the server (excludes system databases).

        Raises mysql.connector.Error on connection/query failure (caller handles).
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA "
                "WHERE SCHEMA_NAME NOT IN "
                "('information_schema', 'mysql', 'performance_schema', 'sys') "
                "ORDER BY SCHEMA_NAME"
            )
            rows = cursor.fetchall()
            cursor.close()
            return [r[0] for r in rows]
        finally:
            conn.close()
