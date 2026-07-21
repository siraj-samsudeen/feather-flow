"""CSV source — reads CSV files from a directory using DuckDB read_csv()."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

import duckdb
import pyarrow as pa

from feather_flow.sources import ChangeResult, StreamSchema
from feather_flow.sources.file_source import (
    FileSource,
    _reject_db_fields,
    _resolve_file_path,
)


def _is_glob(table: str) -> bool:
    """Check if a source_table string contains glob characters."""
    return "*" in table or "?" in table


class CsvSource(FileSource):
    """Source that reads CSV files from a directory.

    Supports glob patterns in source_table (e.g., 'sales_*.csv')
    for multi-file tables. Glob tables use per-file change detection.
    """

    type: ClassVar[str] = "csv"

    def __init__(self, path: Path, *, name: str = "") -> None:
        super().__init__(path)
        self.name = name

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "CsvSource":
        _reject_db_fields(entry, cls.type)
        path = _resolve_file_path(entry, config_dir)
        if not path.is_dir():
            raise ValueError(f"CSV source path must be a directory: {path}")
        source = cls(path=path, name=entry.get("name", ""))
        source._explicit_name = bool(entry.get("name"))
        return source

    def validate_source_table(self, source_table: str) -> list[str]:
        # CSV: filename or glob pattern; no SQL identifier rule.
        return []

    def _resolve_glob_files(self, table: str) -> list[Path]:
        """Resolve glob pattern to sorted list of matching files."""
        return sorted(self.path.glob(table))

    def _source_path_for_table(self, table: str) -> Path:
        """CSV: each table is a separate file in the directory."""
        return self.path / table

    def check(self) -> bool:
        return self.path.is_dir()

    def discover(self) -> list[StreamSchema]:
        csv_files = sorted(self.path.glob("*.csv"))
        schemas: list[StreamSchema] = []
        con = duckdb.connect(":memory:")
        try:
            for csv_file in csv_files:
                cols = con.execute(
                    "SELECT column_name, column_type "
                    "FROM (DESCRIBE SELECT * FROM read_csv(?))",
                    [str(csv_file)],
                ).fetchall()
                schemas.append(
                    StreamSchema(
                        name=csv_file.name,
                        columns=[(c[0], c[1]) for c in cols],
                        primary_key=None,
                        supports_incremental=False,
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
        con = duckdb.connect(":memory:")
        try:
            col_sql = ", ".join(f'"{c}"' for c in columns) if columns else "*"

            if _is_glob(table):
                files = self._resolve_glob_files(table)
                if not files:
                    raise FileNotFoundError(
                        f"No files matching glob pattern '{table}' in {self.path}"
                    )
                file_list = "[" + ", ".join(f"'{f}'" for f in files) + "]"
                query = f"SELECT {col_sql} FROM read_csv({file_list})"
            else:
                file_path = str(self.path / table)
                query = f"SELECT {col_sql} FROM read_csv('{file_path}')"

            query += self._build_where_clause(watermark_column, watermark_value, filter)
            result = con.execute(query).arrow().read_all()
        finally:
            con.close()
        return result

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        con = duckdb.connect(":memory:")
        try:
            if _is_glob(table):
                files = self._resolve_glob_files(table)
                if not files:
                    return []
                file_path = str(files[0])
            else:
                file_path = str(self.path / table)

            cols = con.execute(
                "SELECT column_name, column_type "
                "FROM (DESCRIBE SELECT * FROM read_csv(?))",
                [file_path],
            ).fetchall()
        finally:
            con.close()
        return [(c[0], c[1]) for c in cols]

    def detect_changes(
        self, table: str, last_state: dict[str, object] | None = None
    ) -> ChangeResult:
        """Change detection: per-file tracking for globs, single-file for regular."""
        if not _is_glob(table):
            return super().detect_changes(table, last_state)

        # Per-file change detection for glob patterns
        files = self._resolve_glob_files(table)
        if not files:
            return ChangeResult(changed=False, reason="no_files")

        # Parse stored per-file state
        stored_files: dict[str, dict[str, object]] = {}
        if last_state and last_state.get("last_file_hash"):
            try:
                stored_files = json.loads(str(last_state["last_file_hash"]))
            except (json.JSONDecodeError, TypeError):
                pass

        if not stored_files:
            # First run — hash all files
            file_states = {}
            for f in files:
                mtime = os.path.getmtime(f)
                file_hash = self._compute_file_hash(f)
                file_states[f.name] = {"mtime": mtime, "hash": file_hash}
            return ChangeResult(
                changed=True,
                reason="first_run",
                metadata={"file_hash": json.dumps(file_states)},
            )

        current_names = {f.name for f in files}
        stored_names = set(stored_files.keys())

        # Check for added or removed files
        if current_names != stored_names:
            file_states = {}
            for f in files:
                mtime = os.path.getmtime(f)
                file_hash = self._compute_file_hash(f)
                file_states[f.name] = {"mtime": mtime, "hash": file_hash}
            return ChangeResult(
                changed=True,
                reason="files_changed",
                metadata={"file_hash": json.dumps(file_states)},
            )

        # Check each file: mtime first, then hash only if mtime changed
        any_changed = False
        file_states = {}
        for f in files:
            mtime = os.path.getmtime(f)
            stored = stored_files.get(f.name, {})
            stored_mtime = stored.get("mtime")
            stored_hash = stored.get("hash")

            if mtime == stored_mtime:
                # Unchanged — reuse stored hash
                file_states[f.name] = {"mtime": mtime, "hash": stored_hash}
            else:
                # Mtime changed — compute hash
                file_hash = self._compute_file_hash(f)
                file_states[f.name] = {"mtime": mtime, "hash": file_hash}
                if file_hash != stored_hash:
                    any_changed = True

        if any_changed:
            return ChangeResult(
                changed=True,
                reason="hash_changed",
                metadata={"file_hash": json.dumps(file_states)},
            )

        # All files unchanged — update mtime metadata
        return ChangeResult(
            changed=False,
            reason="unchanged",
            metadata={"file_hash": json.dumps(file_states)},
        )
