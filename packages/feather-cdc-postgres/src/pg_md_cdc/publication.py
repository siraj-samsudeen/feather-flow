"""Publication + replica-identity reconciliation, always schema-qualified (#3079 D4/D6).

The publication is an EXPLICIT table list reconciled to the desired set every run — never
`FOR TABLES IN SCHEMA`, because a table that auto-joins gets no baseline from us. Relation
identity is `(schema, table)` throughout: two schemas may legally hold the same table name.
Replica-identity ALTERs are diff-based (they take a brief exclusive lock) and plannable.
The publish list is `insert, update, delete, truncate`: a mirror that silently kept every row
of a truncated source table would be stale bronze with no event to say so (#3079 review). An
existing publication with a narrower list (the pilot's) is ALTERed up, never inherited.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import psycopg
from psycopg import sql

Relation = tuple[str, str]


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, connect_timeout=10, autocommit=True)


def publication_exists(dsn: str, publication: str) -> bool:
    with _connect(dsn) as conn:
        return conn.execute(
            "select 1 from pg_publication where pubname = %s", (publication,)
        ).fetchone() is not None


def published_relations(dsn: str, publication: str) -> set[Relation]:
    with _connect(dsn) as conn:
        return {
            (r[0], r[1])
            for r in conn.execute(
                "select schemaname, tablename from pg_publication_tables where pubname = %s",
                (publication,),
            ).fetchall()
        }


def drop_publication(dsn: str, publication: str) -> None:
    with _connect(dsn) as conn:
        conn.execute(sql.SQL("drop publication if exists {}").format(sql.Identifier(publication)))


PUBLISH = "insert, update, delete, truncate"

_PUB_FLAGS = {"insert": "pubinsert", "update": "pubupdate", "delete": "pubdelete",
              "truncate": "pubtruncate"}


def reconcile(
    dsn: str,
    publication: str,
    desired: Iterable[Relation],
    *,
    publish: str = PUBLISH,
    plan_only: bool = False,
) -> tuple[list[Relation], list[Relation]]:
    """Bring `publication` to exactly `desired` (membership AND publish list). Returns
    (added, dropped), both sorted."""
    desired_set = {(s, t) for s, t in desired}
    wanted_ops = {op.strip() for op in publish.split(",") if op.strip()}
    with _connect(dsn) as conn:
        flags = conn.execute(
            "select pubinsert, pubupdate, pubdelete, pubtruncate from pg_publication "
            "where pubname = %s", (publication,)
        ).fetchone()
        exists = flags is not None
        # PG 18 refuses UPDATEs on a published REPLICA IDENTITY FULL table whose stored
        # generated columns are not published, so the publication always publishes them.
        gencols = conn.info.server_version >= 180000
        if exists and not plan_only:
            current_ops = {op for op, col in _PUB_FLAGS.items()
                           if flags[list(_PUB_FLAGS).index(op)]}
            if current_ops != wanted_ops:
                conn.execute(sql.SQL("alter publication {} set (publish = {})").format(
                    sql.Identifier(publication), sql.Literal(publish)))
            if gencols and conn.execute(
                "select pubgencols from pg_publication where pubname = %s", (publication,)
            ).fetchone()[0] != "s":
                conn.execute(sql.SQL("alter publication {} set (publish_generated_columns = stored)")
                             .format(sql.Identifier(publication)))
        current: set[Relation] = set()
        if exists:
            current = {
                (r[0], r[1])
                for r in conn.execute(
                    "select schemaname, tablename from pg_publication_tables where pubname = %s",
                    (publication,),
                ).fetchall()
            }
        added = sorted(desired_set - current)
        dropped = sorted(current - desired_set)
        if plan_only:
            return added, dropped
        if not exists:
            conn.execute(
                sql.SQL("create publication {} with (publish = {}{})").format(
                    sql.Identifier(publication), sql.Literal(publish),
                    sql.SQL(", publish_generated_columns = stored" if gencols else ""),
                )
            )
        for schema, table in added:
            conn.execute(
                sql.SQL("alter publication {} add table {}.{}").format(
                    sql.Identifier(publication), sql.Identifier(schema), sql.Identifier(table)
                )
            )
        for schema, table in dropped:
            conn.execute(
                sql.SQL("alter publication {} drop table {}.{}").format(
                    sql.Identifier(publication), sql.Identifier(schema), sql.Identifier(table)
                )
            )
    return added, dropped


def ensure_replica_identity(
    dsn: str,
    relations: Sequence[Relation],
    mode: str = "full",
    *,
    plan_only: bool = False,
) -> list[Relation]:
    """Diff-based: only tables whose `relreplident` differs from the declared mode are ALTERed.
    `full` is what makes unchanged-TOAST updates decodable (see resolve_unchanged_toast)."""
    if mode not in ("full", "default"):
        raise ValueError(f"replica_identity must be 'full' or 'default', not {mode!r}")
    want = "f" if mode == "full" else "d"
    rels = list(relations)
    if not rels:
        return []
    with _connect(dsn) as conn:
        wanted = {(s_, t_) for s_, t_ in rels}
        needs = [
            (r[0], r[1])
            for r in conn.execute(
                "select n.nspname, c.relname from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where c.relkind = 'r' and n.nspname = any(%s) and c.relreplident <> %s",
                (sorted({s_ for s_, _ in rels}), want),
            ).fetchall()
            if (r[0], r[1]) in wanted
        ]
        if plan_only:
            return sorted(needs)
        for schema, table in needs:
            conn.execute(
                sql.SQL("alter table {}.{} replica identity {}").format(
                    sql.Identifier(schema), sql.Identifier(table),
                    sql.SQL("full" if mode == "full" else "default"),
                )
            )
    return sorted(needs)
