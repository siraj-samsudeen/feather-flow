"""Read-only PostgreSQL catalog discovery (#3079 D4/D6/D7).

Constraints:
  - every relation carries (schema, table) together — two schemas may hold the same table name
    and the runtime must never collapse them (D2).
  - nothing here writes, locks, or sets a session GUC; `plan` is provably non-mutating because
    this module has no DDL/DML in it.
  - the WAL fuse is READ from the server (`max_slot_wal_keep_size`), never declared (Deviation D-7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import psycopg

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


@dataclass(frozen=True)
class SourceColumn:
    name: str
    attnum: int
    type_oid: int
    typmod: int
    not_null: bool
    type_name: str
    is_array: bool = False
    elem_oid: int = 0


@dataclass(frozen=True)
class UniqueIndex:
    name: str
    columns: Tuple[str, ...]


@dataclass(frozen=True)
class SourceRelation:
    schema: str
    table: str
    oid: int
    columns: List[SourceColumn] = field(default_factory=list)
    primary_key: Tuple[str, ...] = ()
    unique_indexes: Tuple[UniqueIndex, ...] = ()
    replica_identity: str = "d"  # d default | n nothing | f full | i index
    row_estimate: float = 0.0
    kind: str = "r"              # relkind: r plain/leaf table | p partitioned parent
    parent: Optional[str] = None  # "schema.table" of the partitioned parent, for a leaf

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"

    def column(self, name: str) -> Optional[SourceColumn]:
        for c in self.columns:
            if c.name == name:
                return c
        return None


_REL_SQL = """
select c.oid, n.nspname, c.relname, c.relreplident, c.reltuples, c.relkind,
       pn.nspname || '.' || pc.relname as parent
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_inherits i on i.inhrelid = c.oid
left join pg_class pc on pc.oid = i.inhparent and pc.relkind = 'p'
left join pg_namespace pn on pn.oid = pc.relnamespace
where c.relkind in ('r', 'p')
  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
  and n.nspname not like 'pg\\_temp%%' and n.nspname not like 'pg\\_toast%%'
order by n.nspname, c.relname
"""

_COL_SQL = """
select a.attrelid, a.attname, a.attnum, a.atttypid, a.atttypmod, a.attnotnull,
       t.typname, t.typcategory = 'A' as is_array, t.typelem
from pg_attribute a
join pg_class c on c.oid = a.attrelid
join pg_namespace n on n.oid = c.relnamespace
join pg_type t on t.oid = a.atttypid
where a.attnum > 0 and not a.attisdropped
  and c.relkind in ('r', 'p')
  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
order by a.attrelid, a.attnum
"""

# Non-partial (indpred is null) and non-expression (indexprs is null): a partial unique index
# does not guarantee global uniqueness, so it is not a merge key. `k.ord <= i.indnkeyatts` drops
# INCLUDE columns: they sit in indkey but carry no uniqueness, and merging on one turns every
# UPDATE of it into a duplicate row.
_IDX_SQL = """
select i.indrelid, ic.relname, i.indisprimary, k.ord, a.attname
from pg_index i
join pg_class ic on ic.oid = i.indexrelid
join pg_class c on c.oid = i.indrelid
join pg_namespace n on n.oid = c.relnamespace
cross join lateral unnest(i.indkey::smallint[]) with ordinality as k(attnum, ord)
join pg_attribute a on a.attrelid = i.indrelid and a.attnum = k.attnum
where i.indisunique and i.indisvalid and i.indislive
  and i.indpred is null and i.indexprs is null
  and k.ord <= i.indnkeyatts
  and c.relkind in ('r', 'p')
  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
order by i.indrelid, ic.relname, k.ord
"""


def discover(dsn: str, *, connect_timeout: int = 10) -> List[SourceRelation]:
    """Every user table in the source, schema-qualified, with columns, keys and identity."""
    with psycopg.connect(dsn, connect_timeout=connect_timeout) as conn:
        conn.read_only = True
        rels = conn.execute(_REL_SQL).fetchall()
        cols = conn.execute(_COL_SQL).fetchall()
        idx = conn.execute(_IDX_SQL).fetchall()

    by_oid: Dict[int, List[SourceColumn]] = {}
    for attrelid, name, attnum, type_oid, typmod, not_null, type_name, is_array, elem in cols:
        by_oid.setdefault(attrelid, []).append(
            SourceColumn(
                name=name,
                attnum=attnum,
                type_oid=type_oid,
                typmod=typmod,
                not_null=bool(not_null),
                type_name=type_name,
                is_array=bool(is_array),
                elem_oid=elem or 0,
            )
        )

    pk: Dict[int, List[str]] = {}
    uniq: Dict[int, Dict[str, List[str]]] = {}
    for indrelid, indname, is_primary, _ord, attname in idx:
        if is_primary:
            pk.setdefault(indrelid, []).append(attname)
        else:
            uniq.setdefault(indrelid, {}).setdefault(indname, []).append(attname)

    out: List[SourceRelation] = []
    for oid, schema, table, relreplident, reltuples, relkind, parent in rels:
        out.append(
            SourceRelation(
                schema=schema,
                table=table,
                oid=oid,
                columns=by_oid.get(oid, []),
                primary_key=tuple(pk.get(oid, ())),
                unique_indexes=tuple(
                    UniqueIndex(name=n, columns=tuple(c)) for n, c in sorted(uniq.get(oid, {}).items())
                ),
                replica_identity=relreplident,
                row_estimate=float(reltuples or 0.0),
                kind=relkind,
                parent=parent,
            )
        )
    return sorted(out, key=lambda r: (r.schema, r.table))


def publication_relations(dsn: str, publication: str, *, connect_timeout: int = 10) -> Optional[set]:
    """{(schema, table)} currently published, or None when the publication does not exist yet
    (a first run — distinct from an existing but empty publication)."""
    with psycopg.connect(dsn, connect_timeout=connect_timeout) as conn:
        conn.read_only = True
        exists = conn.execute("select 1 from pg_publication where pubname = %s", (publication,)).fetchone()
        if exists is None:
            return None
        rows = conn.execute(
            "select schemaname, tablename from pg_publication_tables where pubname = %s", (publication,)
        ).fetchall()
    return {(s, t) for s, t in rows}


def slot_state(dsn: str, slot: str, *, connect_timeout: int = 10) -> Dict[str, object]:
    """Slot telemetry plus the server's own WAL retention fuse.

    `max_slot_wal_keep_size` is read, not declared: a manifest number that disagrees with the
    server is a false invariant (Deviation D-7). -1 on the server means unlimited → None here.
    """
    with psycopg.connect(dsn, connect_timeout=connect_timeout) as conn:
        conn.read_only = True
        row = conn.execute(
            """
            select active, wal_status, restart_lsn::text, confirmed_flush_lsn::text,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint
            from pg_replication_slots where slot_name = %s
            """,
            (slot,),
        ).fetchone()
        keep = conn.execute(
            "select setting, unit from pg_settings where name = 'max_slot_wal_keep_size'"
        ).fetchone()

    fuse: Optional[int] = None
    if keep is not None:
        setting, unit = int(keep[0]), (keep[1] or "")
        if setting >= 0:
            mult = {"B": 1, "kB": 1024, "MB": 1024**2, "GB": 1024**3, "8kB": 8192}.get(unit, 1)
            fuse = setting * mult

    state: Dict[str, object] = {"slot": slot, "exists": row is not None, "max_slot_wal_keep_size_bytes": fuse}
    if row is not None:
        state.update(
            active=row[0],
            wal_status=row[1],
            restart_lsn=row[2],
            confirmed_flush_lsn=row[3],
            lag_bytes=row[4],
        )
    return state


def relation_map(relations: Sequence[SourceRelation]) -> Dict[str, SourceRelation]:
    return {r.qualified: r for r in relations}
