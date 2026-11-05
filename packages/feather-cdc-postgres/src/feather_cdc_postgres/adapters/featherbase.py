"""Featherbase catalog adapter — `table_def` decides which relations exist (#2209, #3079 D4/D-13).

Constraints (each one is a live behaviour of Featherbase, not a preference):
  - platform physical name = lower(Table name) with spaces → underscores
    ('Access Log' → access_log) in the catalog schema;
  - current app Tables name their physical schema/relation in the catalog;
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
        physical_cols = {"physical_schema", "physical_relation"}
        if physical_cols & cols and not physical_cols <= cols:
            raise RuntimeError(
                f"{schema}.table_def needs both physical_schema and physical_relation"
            )
        has_physical = physical_cols <= cols
        desc = '"description"' if "description" in cols else "null"
        system = '"system"' if "system" in cols else "false"
        physical_schema = "physical_schema" if has_physical else "null"
        physical_relation = "physical_relation" if has_physical else "null"
        rows = conn.execute(
            f'select "name", module, data_source, kind, {desc}, {system}, '
            f'{physical_schema}, {physical_relation} from "{schema}".table_def'
        ).fetchall()
        physical = {
            (r[0], r[1])
            for r in conn.execute(
                "select schemaname, tablename from pg_tables"
            ).fetchall()
        }

    out: Dict[tuple[str, str], AdapterRelation] = {}
    for name, module, data_source, kind, description, is_system, physical_schema, physical_relation in rows:
        derived_table = str(name).lower().replace(" ", "_")
        if kind not in ("table", "sub_table"):
            continue
        if data_source is not None:
            continue
        if derived_table in CATALOG_TABLES:
            continue  # the catalog's own rows are handled below
        if modules is not None and (module or "") not in modules:
            continue
        relation_schema = schema
        relation_table = derived_table
        if has_physical and not bool(is_system):
            if physical_schema and physical_relation:
                relation_schema = str(physical_schema)
                relation_table = str(physical_relation)
            elif "." in str(name):
                # #4113: Featherbase's tableRelation() — a qualified (app-owned) Table is never
                # located by its spelling; a plain one lives at the platform default above.
                raise RuntimeError(
                    f"{schema}.table_def row {name!r} has no "
                    "physical_schema/physical_relation"
                )
        if (relation_schema, relation_table) not in physical:
            continue  # declared but not materialised yet
        origin = None
        if system_tables is not None:
            if bool(is_system) and relation_table not in system_tables:
                continue
            origin = NAMED_SYSTEM if bool(is_system) else USER_CREATED
        out[(relation_schema, relation_table)] = AdapterRelation(
            relation_schema, relation_table, module, description or None, origin
        )
    for tbl, description in CATALOG_TABLES.items():
        if (schema, tbl) not in physical:
            continue
        if system_tables is None:
            out[(schema, tbl)] = AdapterRelation(
                schema, tbl, None, description
            )  # unconditional (legacy)
        elif tbl in system_tables:
            out[(schema, tbl)] = AdapterRelation(
                schema, tbl, None, description, NAMED_SYSTEM
            )
    return [out[k] for k in sorted(out)]
