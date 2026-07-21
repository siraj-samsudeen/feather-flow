"""DuckDB file source — reads from a .duckdb file via ATTACH."""

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


class DuckDBFileSource(FileSource):
    """Source that reads tables from a DuckDB file using ATTACH."""

    type: ClassVar[str] = "duckdb"

    def __init__(self, path: Path, *, name: str = "") -> None:
        super().__init__(path)
        self.name = name

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "DuckDBFileSource":
        _reject_db_fields(entry, cls.type)
        path = _resolve_file_path(entry, config_dir)
        source = cls(path=path, name=entry.get("name", ""))
        source._explicit_name = bool(entry.get("name"))
        return source

    def validate_source_table(self, source_table: str) -> list[str]:
        if "." not in source_table:
            return [
                f"source_table '{source_table}' must be in schema.table "
                f"format for DuckDB sources."
            ]
        st_schema, st_table = source_table.split(".", 1)
        if not _SQL_IDENTIFIER_RE.match(st_schema) or not _SQL_IDENTIFIER_RE.match(
            st_table
        ):
            return [
                f"source_table '{source_table}' contains invalid identifier "
                f"characters. Use letters, digits, and underscores only."
            ]
        return []

    def _connect_direct(self) -> duckdb.DuckDBPyConnection:
        """Connect directly to the source DB (read-only) for metadata queries."""
        return duckdb.connect(str(self.path), read_only=True)

    def _connect_attached(self) -> duckdb.DuckDBPyConnection:
        """Connect in-memory with source ATTACH'd for data queries."""
        con = duckdb.connect(":memory:")
        con.execute(f"ATTACH '{self.path}' AS source_db (READ_ONLY)")
        return con

    def check(self) -> bool:
        if not self.path.exists():
            return False
        try:
            con = self._connect_direct()
            con.close()
            return True
        except Exception:
            return False

    def discover(self) -> list[StreamSchema]:
        con = self._connect_direct()
        rows = con.execute(
            "SELECT table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_schema, table_name"
        ).fetchall()

        schemas: list[StreamSchema] = []
        for schema_name, table_name in rows:
            qualified = f"{schema_name}.{table_name}"
            cols = con.execute(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                [schema_name, table_name],
            ).fetchall()
            schemas.append(
                StreamSchema(
                    name=qualified,
                    columns=[(c[0], c[1]) for c in cols],
                    primary_key=None,
                    supports_incremental=False,
                )
            )
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
        con = self._connect_attached()
        schema, tbl = table.split(".", 1)
        col_sql = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        query = f'SELECT {col_sql} FROM source_db."{schema}"."{tbl}"'
        query += self._build_where_clause(watermark_column, watermark_value, filter)
        result = con.execute(query).arrow().read_all()
        con.close()
        return result

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        con = self._connect_direct()
        parts = table.split(".")
        schema_name, table_name = parts[0], parts[1]
        cols = con.execute(
            "SELECT column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [schema_name, table_name],
        ).fetchall()
        con.close()
        return [(c[0], c[1]) for c in cols]
