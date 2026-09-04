"""Featherbase catalog adapter — `table_def` decides which relations exist (#2209, #3079 D4).

Constraints (each one is a live behaviour of Featherbase, not a preference):
  - physical name = lower(Table name) with spaces → underscores ('Access Log' → access_log);
  - only kind in (table, sub_table) — settings Tables are EAV rows, not relations;
  - data_source IS NULL — a row with one is a reflection of an external source, not ours;
  - a declared Table with no physical relation yet is SKIPPED, never an error;
  - `table_def`/`column_def` themselves are always included so the warehouse is self-describing.
"""
from __future__ import annotations

from typing import Any, Dict, List

import psycopg

from . import AdapterRelation

# The catalog itself. Descriptions match the retired scripts/generate_featherbase_sources.py, which this
# adapter supersedes — the generated dbt file must not churn on the switch.
CATALOG_TABLES: Dict[str, str] = {
    "table_def": "Featherbase catalog — one row per declared Table (the generation input for this file).",
    "column_def": "Featherbase catalog — one row per declared Column.",
}


def select(dsn: str, options: Dict[str, Any] | None = None) -> List[AdapterRelation]:
    """Relations Featherbase's own catalog declares. `options.modules` is an allow-list of
    module names; null/absent means every module. `options.schema` defaults to public."""
    options = options or {}
    schema = str(options.get("schema") or "public")
    raw_modules = options.get("modules")
    modules = None if raw_modules is None else {str(m) for m in raw_modules}

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.read_only = True
        cols = {
            r[0]
            for r in conn.execute(
                "select column_name from information_schema.columns "
                "where table_schema = %s and table_name = 'table_def'",
                (schema,),
            ).fetchall()
        }
        if not cols:
            raise RuntimeError(f"{schema}.table_def not found — is this a Featherbase database?")
        desc = '"description"' if "description" in cols else "null"
        rows = conn.execute(
            f'select "name", module, data_source, kind, {desc} from "{schema}".table_def'
        ).fetchall()
        physical = {
            r[0]
            for r in conn.execute(
                "select tablename from pg_tables where schemaname = %s", (schema,)
            ).fetchall()
        }

    out: Dict[str, AdapterRelation] = {}
    for name, module, data_source, kind, description in rows:
        tbl = str(name).lower().replace(" ", "_")
        if kind not in ("table", "sub_table"):
            continue
        if data_source is not None:
            continue
        if tbl in CATALOG_TABLES:
            continue  # added unconditionally below, module filter or not
        if modules is not None and (module or "") not in modules:
            continue
        if tbl not in physical:
            continue  # declared but not materialised yet
        out[tbl] = AdapterRelation(schema, tbl, module, description or None)
    for tbl, description in CATALOG_TABLES.items():
        if tbl in physical:
            out[tbl] = AdapterRelation(schema, tbl, None, description)
    return [out[k] for k in sorted(out)]
