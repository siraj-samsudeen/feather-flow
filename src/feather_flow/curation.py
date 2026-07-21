"""Curation manifest loader — reads discovery/curation.json as the table manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

from feather_flow.config import TableConfig

_UNSAFE_CHARS = re.compile(r"[^a-z0-9_]")


def _sanitize_bronze_name(source_db: str, alias: str) -> str:
    """Derive a sanitized bronze table name from source_db and alias.

    Format: <source_db>_<alias>, lowercased, non-alphanum replaced with underscore.
    """
    raw = f"{source_db}_{alias}".lower()
    return _UNSAFE_CHARS.sub("_", raw)


def _bind_db(matched: object, source_db: str) -> object:
    """If ``matched`` is a multi-DB source (``databases:[...]`` with
    ``database is None``), return a child source freshly bound to
    ``source_db``. Otherwise return ``matched`` unchanged.

    Multi-DB sources are config containers: their ``_connect_kwargs`` carry
    no ``database`` key, so any connection opened on them lands the client
    in MySQL's "no database selected" state (or the equivalent for other
    flavors). The curation loop already knows which DB each table belongs
    to, so we bind here, exactly as ``expand_db_sources`` does for the
    discover loop (``sources/expand.py:54-65``).

    Single-DB sources (``database:`` set) and file sources are returned
    as-is — identity is preserved.
    """
    databases = getattr(matched, "databases", None)
    if not databases:
        return matched
    if getattr(matched, "database", None) is not None:
        return matched

    child = type(matched).from_yaml(
        {
            "name": f"{matched.name}__{source_db}",
            "type": matched.type,
            "host": matched.host,
            "port": matched.port,
            "user": matched.user,
            "password": matched.password,
            "database": source_db,
        },
        Path("."),
    )
    child._explicit_name = getattr(matched, "_explicit_name", False)
    # Preserve the parent's YAML ``name`` so CLI output and logs can still
    # show the source the operator wrote, not the synthetic ``__<db>``
    # disambiguator. ``commands/cache.py:_lookup_source_name`` reads this.
    child._parent_name = matched.name
    # Propagate any tuning the parent carried that isn't part of
    # ``from_yaml``'s kwargs (batch_size today; future settings here).
    if hasattr(matched, "batch_size"):
        child.batch_size = matched.batch_size
    return child


def resolve_source(source_db: str, sources: list) -> object:
    """Find the source that owns the given database name.

    Resolution order:
    1. source.database == source_db (single-database source)
    2. source_db in source.databases (multi-database source)
    3. source.name == source_db (file sources — no database concept)

    For multi-DB matches (rule 2), returns a child source freshly bound to
    ``source_db`` via ``_bind_db`` — so callers can call ``extract()`` /
    ``detect_changes()`` without manually threading the database. The
    parent multi-DB source is a config container only.

    Raises ValueError if no match or ambiguous match.
    """
    matches = []
    for src in sources:
        db = getattr(src, "database", None)
        dbs = getattr(src, "databases", None)
        if db is not None and db == source_db:
            matches.append(src)
        elif dbs is not None and source_db in dbs:
            matches.append(src)
        elif src.name == source_db:
            matches.append(src)

    if len(matches) == 0:
        available = ", ".join(s.name for s in sources)
        raise ValueError(
            f"source_db '{source_db}' does not match any declared source. "
            f"Available sources: {available}"
        )
    if len(matches) > 1:
        names = ", ".join(s.name for s in matches)
        raise ValueError(
            f"source_db '{source_db}' is ambiguous — matches multiple sources: {names}"
        )
    return _bind_db(matches[0], source_db)


def load_curation_tables(config_dir: Path) -> list[TableConfig]:
    """Load discovery/curation.json and produce TableConfig list from include entries.

    Raises FileNotFoundError if curation.json does not exist.
    Raises ValueError if no tables have decision 'include'.
    Raises ValueError if two include entries normalize to the same bronze table name.
    """
    curation_path = config_dir / "discovery" / "curation.json"
    if not curation_path.exists():
        raise FileNotFoundError(
            f"discovery/curation.json not found in {config_dir}. "
            f"Run 'feather discover' and curate tables first."
        )

    manifest = json.loads(curation_path.read_text())
    raw_tables = manifest.get("tables", [])
    includes = [t for t in raw_tables if t.get("decision") == "include"]

    if not includes:
        raise ValueError(
            "No tables with decision 'include' in discovery/curation.json. "
            "Curate at least one table before running."
        )

    tables: list[TableConfig] = []
    seen_bronze_names: dict[str, str] = {}  # bronze_name -> "source_db.alias"
    for entry in includes:
        source_db = entry["source_db"]
        alias = entry.get("alias") or entry["source_table"].split(".")[-1]
        bronze_name = _sanitize_bronze_name(source_db, alias)

        # Collision check on normalized bronze name
        origin = f"{source_db}.{alias}"
        if bronze_name in seen_bronze_names:
            raise ValueError(
                f"duplicate bronze target name '{bronze_name}' — "
                f"entries {seen_bronze_names[bronze_name]} and {origin} "
                f"normalize to the same identifier. Use distinct aliases."
            )
        seen_bronze_names[bronze_name] = origin

        timestamp_column = None
        ts = entry.get("timestamp")
        if ts and isinstance(ts, dict):
            timestamp_column = ts.get("column")

        tables.append(
            TableConfig(
                name=bronze_name,
                source_table=entry["source_table"],
                strategy=entry["strategy"],
                target_table="",  # mode-derived at runtime by _resolve_target
                primary_key=entry.get("primary_key"),
                timestamp_column=timestamp_column,
                source_name=source_db,
                database=source_db,
            )
        )

    return tables
