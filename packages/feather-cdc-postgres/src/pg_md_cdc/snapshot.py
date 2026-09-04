"""Consistent baselines with NO staging tables on the source (#3079 Deviation D-1).

The slot's exported snapshot is read directly inside one REPEATABLE READ transaction, so the
source gains no `_dlt_*` tables (50 stray ones found live, #2874). Ordering invariant, proved
against PG 17 on 04-Sep-2026: `SET TRANSACTION SNAPSHOT` must run while the exporting
replication connection is still open (it fails "snapshot does not exist" otherwise); once the
import succeeds the exporter may close and the read continues.
Convergence: the publication is reconciled BEFORE the slot exists, so any concurrent change is
also in the stream and the merge write-disposition dedups on the declared key.
A baseline taken LATER (a table added mid-life, a resumed snapshot, an operator resnapshot) is
`consistent_baseline`: a throwaway slot hands us PG's own consistent point + exported snapshot,
so "visible in the baseline" ⟺ "commit LSN < boundary" by construction, and the drain drops
what the baseline already saw. That is what makes an append_only overlap exact (#3079 review).
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Iterator, Sequence

import dlt
import psycopg
import psycopg2
from psycopg import sql
from psycopg2.extras import LogicalReplicationConnection

from pg_replication.decoders import ColumnType
from pg_replication.schema_types import _PG_TYPES, _to_dlt_column_schema

Relation = tuple[str, str]


def lsn_hex_to_int(lsn: str) -> int:
    hi, lo = lsn.split("/")
    return (int(hi, 16) << 32) + int(lo, 16)


@dataclass
class ExportedSnapshot:
    """Handle on a freshly created slot + its exported snapshot. Keep it open until the
    importing transaction has run SET TRANSACTION SNAPSHOT (see module docstring)."""

    slot: str
    consistent_point_lsn: int
    snapshot_name: str
    _conn: object | None = field(default=None, repr=False)

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    def __iter__(self):  # so `lsn, name = handle` reads naturally at call sites
        return iter((self.consistent_point_lsn, self.snapshot_name))


def drop_slot_if_exists(dsn: str, slot: str) -> bool:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute("select 1 from pg_replication_slots where slot_name = %s",
                           (slot,)).fetchone()
        if row:
            conn.execute("select pg_drop_replication_slot(%s)", (slot,))
    return bool(row)


def temp_slot_name(slot: str) -> str:
    """The throwaway slot a consistent baseline is taken from. Ours by name; the caller holds
    the registration's owner lock, so a leftover (an older build's persistent one) is safe to
    drop. Created TEMPORARY, so a crash mid-baseline leaks nothing."""
    return (slot + "_bl")[:63]


def create_slot_with_snapshot(dsn: str, slot: str, *, temporary: bool = False) -> ExportedSnapshot:
    """Create `slot` with pgoutput and KEEP the replication connection open so its exported
    snapshot stays importable. Raises if the slot already exists (the caller drops it first).
    `temporary`: PostgreSQL drops the slot itself when this session ends — a killed container
    cannot leave it behind retaining WAL. Only the main slot must outlive the process."""
    conn = psycopg2.connect(dsn, connection_factory=LogicalReplicationConnection)
    try:
        cur = conn.cursor()
        if temporary:
            quoted = '"' + slot.replace('"', '""') + '"'
            cur.execute(f"CREATE_REPLICATION_SLOT {quoted} TEMPORARY LOGICAL pgoutput "
                        "EXPORT_SNAPSHOT")
        else:
            cur.create_replication_slot(slot, output_plugin="pgoutput")
        _, consistent_point, snapshot_name, _ = cur.fetchone()
    except Exception:
        conn.close()
        raise
    return ExportedSnapshot(slot, lsn_hex_to_int(consistent_point), snapshot_name, conn)


def column_hints(conn: psycopg.Connection, schema: str, table: str, keys: Sequence[str]) -> dict:
    """Destination column types compiled from the source catalog through the SAME
    OID→dlt mapper the CDC path uses — otherwise int4 lands BIGINT on one path and INTEGER
    on the other (observed live in bronze_featherbase, plan Appendix A)."""
    rows = conn.execute(
        "select a.attname, a.atttypid, a.atttypmod from pg_attribute a "
        "join pg_class c on c.oid = a.attrelid "
        "join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = %s and c.relname = %s and a.attnum > 0 and not a.attisdropped "
        "order by a.attnum",
        (schema, table),
    ).fetchall()
    key_set = set(keys or ())
    return {
        name: _to_dlt_column_schema(ColumnType(int(name in key_set), name, type_id, typmod))
        for name, type_id, typmod in rows
    }


def _select_list(conn: psycopg.Connection, schema: str, table: str):
    """`select *` except that every column with no declared OID mapping is cast to text.

    Those columns compile to `text_fallback` (schema_registry), and the CDC path already
    receives pgoutput's text rendering — but psycopg hands the SNAPSHOT path a native object
    (`inet` → IPv4Address), which dlt cannot serialise. Casting here makes both paths agree.
    """
    rows = conn.execute(
        "select a.attname, a.atttypid, t.typcategory = 'A' as is_array from pg_attribute a "
        "join pg_class c on c.oid = a.attrelid "
        "join pg_namespace n on n.oid = c.relnamespace "
        "join pg_type t on t.oid = a.atttypid "
        "where n.nspname = %s and c.relname = %s and a.attnum > 0 and not a.attisdropped "
        "order by a.attnum",
        (schema, table),
    ).fetchall()
    parts = []
    for name, type_oid, is_array in rows:
        if is_array or int(type_oid) not in _PG_TYPES:
            parts.append(sql.SQL("{}::text as {}").format(sql.Identifier(name),
                                                          sql.Identifier(name)))
        else:
            parts.append(sql.Identifier(name))
    return sql.SQL(", ").join(parts) if parts else sql.SQL("*")


def _rows(conn: psycopg.Connection, schema: str, table: str, cursor_name: str, itersize: int,
          select_list):
    """Server-side cursor so a large table never materialises in memory."""
    def gen():
        with conn.cursor(name=cursor_name) as cur:
            cur.itersize = itersize
            cur.execute(
                sql.SQL("select {} from {}.{}").format(
                    select_list, sql.Identifier(schema), sql.Identifier(table))
            )
            cols = [d.name for d in cur.description]
            for row in cur:
                yield dict(zip(cols, row))
    return gen


def _resource(conn, schema, table, keys, key_policy, cursor_name, itersize):
    hints = column_hints(conn, schema, table, keys)
    select_list = _select_list(conn, schema, table)
    merge = bool(keys) and key_policy != "append_only"
    return dlt.resource(
        _rows(conn, schema, table, cursor_name, itersize, select_list),
        name=table,
        write_disposition="merge" if merge else "append",
        primary_key=list(keys) if merge else None,
        columns=hints,
    )


@contextlib.contextmanager
def snapshot_resources(
    dsn: str,
    snapshot_name: str,
    relations: Sequence[Relation],
    keys: dict[Relation, Sequence[str]],
    *,
    key_policies: dict[Relation, str] | None = None,
    itersize: int = 5000,
) -> Iterator[dict[str, list]]:
    """Yields {source_schema: [dlt resources]} reading the exported snapshot. The importing
    transaction is opened EAGERLY (before this returns) so the caller may then close the
    exporting replication connection; the reads themselves stay lazy."""
    conn = psycopg.connect(dsn, connect_timeout=10)
    try:
        conn.execute("begin isolation level repeatable read")
        conn.execute(sql.SQL("set transaction snapshot {}").format(sql.Literal(snapshot_name)))
        by_schema: dict[str, list] = {}
        policies = key_policies or {}
        for i, (schema, table) in enumerate(relations):
            res = _resource(conn, schema, table, keys.get((schema, table), []),
                            policies.get((schema, table), "merge"), f"cdc_snap_{i}", itersize)
            by_schema.setdefault(schema, []).append(res)
        yield by_schema
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        with contextlib.suppress(Exception):
            conn.close()


@contextlib.contextmanager
def consistent_baseline(
    dsn: str,
    slot: str,
    relation: Relation,
    keys: Sequence[str],
    *,
    key_policy: str = "merge",
    itersize: int = 5000,
) -> Iterator[tuple[object, int]]:
    """Yields (resource, boundary_lsn) for ONE relation that is already published.

    The publication add happened before this, so the table's changes are in the main slot's
    WAL from that moment — and a plain snapshot read overlaps that WAL by however long the
    read takes. A keyed merge hides the overlap; an append_only row has no key and lands twice.
    A throwaway slot `<slot>_bl` gives the two things a plain read cannot: PG waits for every
    transaction in flight before exporting the snapshot, and the consistent point it returns is
    the exact LSN below which every transaction is in that snapshot (the decoder's own skip
    predicate, strict `<`). The runner persists the boundary per relation and the drain drops
    that relation's transactions with commit LSN < boundary. The slot is TEMPORARY — it dies
    with its session, so a kill mid-baseline leaks nothing — and is dropped on exit anyway.
    """
    schema, table = relation
    temp = temp_slot_name(slot)
    drop_slot_if_exists(dsn, temp)   # a persistent leftover from an older build
    handle = create_slot_with_snapshot(dsn, temp, temporary=True)
    try:
        with snapshot_resources(dsn, handle.snapshot_name, [relation], {relation: list(keys)},
                                key_policies={relation: key_policy}, itersize=itersize) as by_schema:
            handle.close()  # the import already happened; the exporter is no longer needed
            (resource,) = by_schema[schema]
            yield resource, handle.consistent_point_lsn
    finally:
        handle.close()
        drop_slot_if_exists(dsn, temp)


@contextlib.contextmanager
def baseline_resource(
    dsn: str,
    relation: Relation,
    keys: Sequence[str],
    *,
    key_policy: str = "merge",
    itersize: int = 5000,
) -> Iterator[object]:
    """Plain REPEATABLE READ read of one table — a reader for tests and telemetry. The runtime
    never baselines with it: a row committed between the publication add and this read would
    land from both paths (see `consistent_baseline`)."""
    schema, table = relation
    conn = psycopg.connect(dsn, connect_timeout=10)
    try:
        conn.execute("begin isolation level repeatable read")
        yield _resource(conn, schema, table, keys, key_policy, "cdc_base_0", itersize)
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        with contextlib.suppress(Exception):
            conn.close()
