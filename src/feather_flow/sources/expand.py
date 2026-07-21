"""Shared database source expansion — used by discover and pipeline."""

from __future__ import annotations

from pathlib import Path

from feather_flow.sources.database_source import DatabaseSource


def expand_db_sources(sources: list) -> list:
    """Expand database sources without explicit database into child sources.

    For each source:
      - File sources → keep as-is.
      - DB source with `database` set → keep as-is.
      - DB source with `databases: [...]` → one child per entry.
      - DB source with neither → call list_databases() and expand.
    """
    expanded: list = []
    for src in sources:
        if not isinstance(src, DatabaseSource):
            expanded.append(src)
            continue
        if src.database is not None:
            expanded.append(src)
            continue
        databases = src.databases
        if databases is None:
            try:
                databases = src.list_databases()
            except Exception as e:
                src._last_error = (
                    f"Found 0 databases on host {src.host}. Either grant "
                    f"VIEW ANY DATABASE to this login, or specify "
                    f"`database:` / `databases: [...]` explicitly. ({e})"
                )
                expanded.append(src)
                continue
            if not databases:
                src._last_error = (
                    f"Found 0 databases on host {src.host}. Either grant "
                    f"VIEW ANY DATABASE to this login, or specify "
                    f"`database:` / `databases: [...]` explicitly."
                )
                expanded.append(src)
                continue
        for db in databases:
            child = type(src).from_yaml(
                {
                    "name": f"{src.name}__{db}",
                    "type": src.type,
                    "host": src.host,
                    "port": src.port,
                    "user": src.user,
                    "password": src.password,
                    "database": db,
                },
                Path("."),  # DB sources' from_yaml ignores config_dir (file-path only)
            )
            child._explicit_name = getattr(src, "_explicit_name", False)
            expanded.append(child)
    return expanded
