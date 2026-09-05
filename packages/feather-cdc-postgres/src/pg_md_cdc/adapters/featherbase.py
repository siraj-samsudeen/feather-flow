"""Featherbase catalog adapter — `table_def` decides which relations exist (#2209, #3079 D4/D-13).

Constraints (each one is a live behaviour of Featherbase, not a preference):
  - physical name = lower(Table name) with spaces → underscores ('Access Log' → access_log);
  - only kind in (table, sub_table) — settings Tables are EAV rows, not relations;
  - data_source IS NULL — a row with one is a reflection of an external source, not ours;
  - a declared Table with no physical relation yet is SKIPPED, never an error;
  - `options.system_tables` given ⇒ two-way rule: `table_def."system" = true` rows only when
    named there, `false` rows (user-created) always; absent ⇒ everything, catalog included.
"""
from __future__ import annotations

from typing import Any, Dict, List

import psycopg

from . import NAMED_SYSTEM, USER_CREATED, AdapterRelation

# The catalog itself. Descriptions match the retired scripts/generate_featherbase_sources.py, which this
# adapter supersedes — the generated dbt file must not churn on the switch.
CATALOG_TABLES: Dict[str, str] = {
    "table_def": "Featherbase catalog — one row per declared Table (the generation input for this file).",
    "column_def": "Featherbase catalog — one row per declared Column.",
}


def select(dsn: str, options: Dict[str, Any] | None = None) -> List[AdapterRelation]:
    """Relations Featherbase's own catalog declares. `options.modules` is an allow-list of
    module names; null/absent means every module. `options.schema` defaults to public.
    `options.system_tables` (list of physical names) switches on the two-way rule: a platform
    table (`"system" = true`) is offered ONLY when named there — `table_def`/`column_def`
    included — and a user-created table (`"system" = false`) is always offered."""
    options = options or {}
    schema = str(options.get("schema") or "public")
    raw_modules = options.get("modules")
    modules = None if raw_modules is None else {str(m) for m in raw_modules}
    raw_system = options.get("system_tables")
    system_tables = None if raw_system is None else {str(t) for t in raw_system}

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
        if system_tables is not None and "system" not in cols:
            # Without the flag every row would read as user-created and the allow-list would
            # admit every platform table — the opposite of what the option promises.
            raise RuntimeError(
                f"{schema}.table_def has no \"system\" column — options.system_tables needs it"
            )
        desc = '"description"' if "description" in cols else "null"
        system = '"system"' if "system" in cols else "false"
        rows = conn.execute(
            f'select "name", module, data_source, kind, {desc}, {system} from "{schema}".table_def'
        ).fetchall()
        physical = {
            r[0]
            for r in conn.execute(
                "select tablename from pg_tables where schemaname = %s", (schema,)
            ).fetchall()
        }

    out: Dict[str, AdapterRelation] = {}
    for name, module, data_source, kind, description, is_system in rows:
        tbl = str(name).lower().replace(" ", "_")
        if kind not in ("table", "sub_table"):
            continue
        if data_source is not None:
            continue
        if tbl in CATALOG_TABLES:
            continue  # the catalog's own rows are handled below
        if modules is not None and (module or "") not in modules:
            continue
        if tbl not in physical:
            continue  # declared but not materialised yet
        origin = None
        if system_tables is not None:
            if bool(is_system) and tbl not in system_tables:
                continue
            origin = NAMED_SYSTEM if bool(is_system) else USER_CREATED
        out[tbl] = AdapterRelation(schema, tbl, module, description or None, origin)
    for tbl, description in CATALOG_TABLES.items():
        if tbl not in physical:
            continue
        if system_tables is None:
            out[tbl] = AdapterRelation(schema, tbl, None, description)   # unconditional (legacy)
        elif tbl in system_tables:
            out[tbl] = AdapterRelation(schema, tbl, None, description, NAMED_SYSTEM)
    return [out[k] for k in sorted(out)]
