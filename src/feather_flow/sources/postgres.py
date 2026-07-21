"""PostgreSQL source — reads from PostgreSQL via psycopg2."""

from __future__ import annotations

import decimal
import logging
import re
from pathlib import Path
from typing import Any, ClassVar, Iterator

import psycopg2
import psycopg2.extras
import pyarrow as pa

from feather_flow.sources import ChangeResult, StreamSchema
from feather_flow.sources.database_source import DatabaseSource

_log = logging.getLogger(__name__)

# Validates identifiers (column / schema / table names) before interpolating
# them into SQL.  Allows letters, digits, underscore and internal spaces
# (Postgres permits spaces in double-quoted identifiers).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][\w\s]*$")


def _postgres_coerce_row(val, _col_name):
    if isinstance(val, decimal.Decimal):
        return float(val)
    return val


# psycopg2 cursor.description[1] is a PostgreSQL OID integer → PyArrow type
_PSYCOPG2_TYPE_MAP: dict[int, pa.DataType] = {
    16: pa.bool_(),  # bool
    17: pa.binary(),  # bytea
    20: pa.int64(),  # int8
    21: pa.int16(),  # int2
    23: pa.int64(),  # int4
    25: pa.string(),  # text
    114: pa.string(),  # json
    700: pa.float32(),  # float4
    701: pa.float64(),  # float8
    1042: pa.string(),  # bpchar (fixed-length char)
    1043: pa.string(),  # varchar
    1082: pa.date32(),  # date
    1083: pa.time64("us"),  # time
    1114: pa.timestamp("us"),  # timestamp
    1184: pa.timestamp("us", tz="UTC"),  # timestamptz
    1700: pa.float64(),  # numeric/decimal
    2950: pa.string(),  # uuid
    3802: pa.string(),  # jsonb
}

# INFORMATION_SCHEMA data_type string → PyArrow type
_PG_TYPE_MAP: dict[str, pa.DataType] = {
    "integer": pa.int64(),
    "bigint": pa.int64(),
    "smallint": pa.int16(),
    "boolean": pa.bool_(),
    "real": pa.float64(),
    "double precision": pa.float64(),
    "numeric": pa.float64(),
    "decimal": pa.float64(),
    "text": pa.string(),
    "character varying": pa.string(),
    "character": pa.string(),
    "varchar": pa.string(),
    "timestamp without time zone": pa.timestamp("us"),
    "timestamp with time zone": pa.timestamp("us", tz="UTC"),
    "date": pa.date32(),
    "time without time zone": pa.time64("us"),
    "bytea": pa.binary(),
    "uuid": pa.string(),
    "json": pa.string(),
    "jsonb": pa.string(),
    "xml": pa.string(),
}


def _psycopg2_type_to_arrow(type_code: int) -> pa.DataType:
    """Map a psycopg2 OID type_code to a PyArrow type."""
    return _PSYCOPG2_TYPE_MAP.get(type_code, pa.string())


class PostgresSource(DatabaseSource):
    """Source that reads tables from PostgreSQL via psycopg2."""

    type: ClassVar[str] = "postgres"

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
    def from_yaml(cls, entry: dict, config_dir: Path) -> "PostgresSource":
        name = entry.get("name", "")
        explicit_conn = entry.get("connection_string")
        host = entry.get("host")
        port = entry.get("port", 5432)
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
            parts = [f"host={host}", f"port={port}"]
            parts.append(f"dbname={database or 'postgres'}")
            if user:
                parts.append(f"user={user}")
            if password:
                parts.append(f"password={password}")
            conn_str = " ".join(parts)
        else:
            raise ValueError(
                "postgres source requires either 'connection_string' or 'host'."
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
        # Postgres: lenient — real validation happens at extract time when
        # the server rejects bad SQL.
        return []

    def list_databases(self) -> list[str]:
        """Return user databases on the cluster (excludes templates and 'postgres').

        Raises psycopg2.Error on connection/query failure (caller handles).
        """
        conn = psycopg2.connect(self.connection_string, connect_timeout=10)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT datname FROM pg_database "
                "WHERE datistemplate = false "
                "AND datname NOT IN ('postgres') "
                "ORDER BY datname"
            )
            rows = cursor.fetchall()
            cursor.close()
            return [r[0] for r in rows]
        finally:
            conn.close()

    # _format_watermark: uses base class default (ISO value passed through unchanged)

    def check(self) -> bool:
        self._last_error = None
        try:
            conn = psycopg2.connect(self.connection_string, connect_timeout=10)
            conn.close()
            return True
        except psycopg2.Error as e:
            self._last_error = str(e)
            return False

    def discover(self) -> list[StreamSchema]:
        conn = psycopg2.connect(self.connection_string)
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME "
                "FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' "
                "AND TABLE_SCHEMA NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY TABLE_SCHEMA, TABLE_NAME"
            )
            tables = cursor.fetchall()

            schemas: list[StreamSchema] = []
            for schema_name, table_name in tables:
                qualified = f"{schema_name}.{table_name}"
                cursor.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    [schema_name, table_name],
                )
                cols = cursor.fetchall()

                # Populate primary_key from INFORMATION_SCHEMA.  Falls back to
                # None on any error (e.g. missing SELECT permission).
                primary_key: list[str] | None = None
                try:
                    cursor.execute(
                        "SELECT kcu.COLUMN_NAME "
                        "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
                        "JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
                        "  ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME "
                        "  AND kcu.TABLE_SCHEMA = tc.TABLE_SCHEMA "
                        "WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY' "
                        "  AND kcu.TABLE_SCHEMA = %s "
                        "  AND kcu.TABLE_NAME = %s "
                        "ORDER BY kcu.ORDINAL_POSITION",
                        [schema_name, table_name],
                    )
                    pk_rows = cursor.fetchall()
                    if pk_rows:
                        primary_key = [r[0] for r in pk_rows]
                except psycopg2.Error:
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
            return schemas
        finally:
            conn.close()

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        conn = psycopg2.connect(self.connection_string)
        try:
            cursor = conn.cursor()

            parts = table.split(".")
            if len(parts) == 2:
                schema_name, table_name = parts
            else:
                schema_name, table_name = "public", parts[0]

            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION",
                [schema_name, table_name],
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
        """Stream `pa.RecordBatch` chunks from PostgreSQL with progress heartbeat."""
        from feather_flow.sources.fetch_batches import fetch_batches

        if columns:
            bad = [c for c in columns if '"' in c]
            if bad:
                raise ValueError(
                    f"Postgres column names containing '\"' cannot be "
                    f"double-quoted: {bad}"
                )

        conn = psycopg2.connect(self.connection_string)
        try:
            cursor = conn.cursor()
            col_clause = ", ".join(f'"{c}"' for c in columns) if columns else "*"
            where = self._build_where_clause(filter, watermark_column, watermark_value)
            query = f"SELECT {col_clause} FROM {table}{where}"
            cursor.execute(query)

            if cursor.description is None:
                cursor.close()
                return

            col_names = [desc[0] for desc in cursor.description]
            col_types = [
                _psycopg2_type_to_arrow(desc[1]) for desc in cursor.description
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
                    coerce_row=_postgres_coerce_row,
                    heartbeat_every_rows=heartbeat_every_rows,
                    heartbeat_every_seconds=heartbeat_every_seconds,
                )
            finally:
                cursor.close()
        finally:
            conn.close()

    def cheap_rowcount(self, table: str) -> int:
        """Return an approximate row count using pg_class.reltuples.

        ``reltuples`` is updated by ANALYZE/autovacuum and reads in microseconds —
        much cheaper than ``SELECT COUNT(*)``.  The estimate may be slightly stale
        on busy tables; cast to int with a 0 fallback when reltuples is negative
        or NULL (newly created table not yet analyzed).

        Raises psycopg2.Error on connection or permission failure.
        """
        con = psycopg2.connect(self.connection_string)
        try:
            cursor = con.cursor()
            cursor.execute(
                "SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass",
                [table],
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
            con.close()
        return int(row[0]) if row and row[0] is not None and row[0] >= 0 else 0

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
            psycopg2.Error: on connection or permission failure.
        """
        if not _IDENTIFIER_RE.match(column):
            raise ValueError(
                f"Invalid column name {column!r}: must match ^[A-Za-z_][\\w\\s]*$"
            )

        # Parse schema.table — default schema to public
        parts = table.split(".")
        if len(parts) == 2:
            schema_name, table_name = parts
        else:
            schema_name, table_name = "public", parts[0]

        for ident, label in ((schema_name, "schema"), (table_name, "table")):
            if not _IDENTIFIER_RE.match(ident):
                raise ValueError(
                    f"Invalid {label} name {ident!r}: must match ^[A-Za-z_][\\w\\s]*$"
                )

        sql = (
            f'SELECT MIN("{column}"), MAX("{column}") '
            f'FROM "{schema_name}"."{table_name}"'
        )
        con = psycopg2.connect(self.connection_string)
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

    def _discover_pk_columns(self, table: str) -> list[str]:
        """Discover primary key columns for a table via pg_index."""
        conn = psycopg2.connect(self.connection_string)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT a.attname "
                "FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "  AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = %s::regclass AND i.indisprimary",
                [table],
            )
            rows = cursor.fetchall()
            return [r[0] for r in rows]
        except psycopg2.Error:
            return []
        finally:
            cursor.close()
            conn.close()

    def detect_changes(
        self, table: str, last_state: dict[str, object] | None = None
    ) -> ChangeResult:
        if last_state is not None and last_state.get("strategy") == "incremental":
            return ChangeResult(changed=True, reason="incremental")

        try:
            pk_cols = self._discover_pk_columns(table)
            order_by = ", ".join(pk_cols) if pk_cols else "1"

            conn = psycopg2.connect(self.connection_string)
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*), md5(string_agg(row_to_json(t)::text, '' "
                f"ORDER BY {order_by})) FROM {table} t"
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()
        except psycopg2.Error:
            return ChangeResult(changed=True, reason="checksum_error")

        if row is None:
            return ChangeResult(changed=True, reason="first_run")

        current_row_count = row[0]
        current_checksum = row[1]

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
