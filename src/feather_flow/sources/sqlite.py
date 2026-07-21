"""SQLite source — reads from a SQLite database using DuckDB sqlite_scan()."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import duckdb
import pyarrow as pa

from feather_flow.sources import StreamSchema
from feather_flow.sources.file_source import (
    FileSource,
    _SQL_IDENTIFIER_RE,
    _reject_db_fields,
    _resolve_file_path,
)


class SqliteSource(FileSource):
    """Source that reads tables from a SQLite database file."""

    type: ClassVar[str] = "sqlite"

    def __init__(self, path: Path, *, name: str = "") -> None:
        super().__init__(path)
        self.name = name

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "SqliteSource":
        _reject_db_fields(entry, cls.type)
        path = _resolve_file_path(entry, config_dir)
        source = cls(path=path, name=entry.get("name", ""))
        source._explicit_name = bool(entry.get("name"))
        return source

    def validate_source_table(self, source_table: str) -> list[str]:
        if "." in source_table:
            return [
                f"source_table '{source_table}' is invalid: SQLite tables "
                f"are unqualified (no schema prefix). Use just "
                f"'{source_table.split('.', 1)[1]}'."
            ]
        if not _SQL_IDENTIFIER_RE.match(source_table):
            return [
                f"source_table '{source_table}' contains invalid identifier "
                f"characters. Use letters, digits, and underscores only."
            ]
        return []

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """In-memory connection with sqlite_scanner loaded."""
        con = duckdb.connect(":memory:")
        con.execute("INSTALL sqlite_scanner; LOAD sqlite_scanner")
        return con

    def check(self) -> bool:
        if not self.path.exists():
            return False
        try:
            con = self._connect()
            try:
                con.execute(
                    "SELECT name FROM sqlite_scan(?, 'sqlite_master') WHERE type = 'table'",
                    [str(self.path)],
                )
                return True
            finally:
                con.close()
        except Exception:
            return False

    def discover(self) -> list[StreamSchema]:
        con = self._connect()
        try:
            tables = con.execute(
                "SELECT name FROM sqlite_scan(?, 'sqlite_master') WHERE type = 'table' ORDER BY name",
                [str(self.path)],
            ).fetchall()

            schemas: list[StreamSchema] = []
            for (table_name,) in tables:
                cols = con.execute(
                    "SELECT column_name, column_type "
                    "FROM (DESCRIBE SELECT * FROM sqlite_scan(?, ?))",
                    [str(self.path), table_name],
                ).fetchall()
                schemas.append(
                    StreamSchema(
                        name=table_name,
                        columns=[(c[0], c[1]) for c in cols],
                        primary_key=None,
                        supports_incremental=True,
                    )
                )
        finally:
            con.close()
        return schemas

    def extract(
        self,
        table: str,
        columns: list[str] | None = None,
        filter: str | None = None,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
    ) -> pa.Table:
        con = self._connect()
        try:
            col_sql = ", ".join(f'"{c}"' for c in columns) if columns else "*"
            query = f"SELECT {col_sql} FROM sqlite_scan('{self.path}', '{table}')"
            query += self._build_where_clause(watermark_column, watermark_value, filter)
            result = con.execute(query).arrow().read_all()
        finally:
            con.close()
        return result

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        con = self._connect()
        try:
            cols = con.execute(
                "SELECT column_name, column_type "
                "FROM (DESCRIBE SELECT * FROM sqlite_scan(?, ?))",
                [str(self.path), table],
            ).fetchall()
        finally:
            con.close()
        return [(c[0], c[1]) for c in cols]
