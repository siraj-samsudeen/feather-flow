"""DatabaseSource base class — shared behavior for database sources."""

from __future__ import annotations

import pyarrow as pa


class DatabaseSource:
    """Base for database sources (SQL Server, PostgreSQL, etc.).

    Provides:
    - __init__(connection_string): stores the connection string
    - _format_watermark(value): format watermark value for SQL (override per DB)
    - _build_where_clause(): constructs WHERE from filter + watermark
    - extract(): materializes extract_batches into a pa.Table

    Subclasses implement: check(), discover(), extract_batches(),
    detect_changes(), get_schema().
    """

    def __init__(self, connection_string: str) -> None:
        self.connection_string = connection_string

    def extract(
        self,
        table: str,
        columns: list[str] | None = None,
        filter: str | None = None,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
    ) -> pa.Table:
        """Materialize a streaming extract into a single pa.Table.

        Thin adapter for the feather-run pipeline path, which still needs
        the full table in memory for dedup / watermark / boundary-hash ops.
        New code path (`feather extract`) consumes `extract_batches` directly.
        """
        batches = list(
            self.extract_batches(
                table,
                columns=columns,
                filter=filter,
                watermark_column=watermark_column,
                watermark_value=watermark_value,
            )
        )
        if not batches:
            return pa.table({})
        return pa.Table.from_batches(batches, schema=batches[0].schema)

    def _format_watermark(self, value: str) -> str:
        """Format a watermark value for use in a SQL WHERE clause.

        Default: pass through unchanged. Subclasses override for DB-specific formatting.
        """
        return value

    def _build_where_clause(
        self,
        filter: str | None = None,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
    ) -> str:
        """Build a WHERE clause from optional filter and watermark."""
        parts: list[str] = []
        if filter:
            parts.append(f"({filter})")
        if watermark_column and watermark_value:
            wm_val = self._format_watermark(watermark_value)
            parts.append(f"{watermark_column} > '{wm_val}'")
        if not parts:
            return ""
        return " WHERE " + " AND ".join(parts)
