"""Slot consumption: schema-qualified identity, batch boundaries, TOAST (#3079)."""
from __future__ import annotations

import psycopg
import pytest

from pg_md_cdc import publication, snapshot, stream


def _slot(dsn, slot):
    handle = snapshot.create_slot_with_snapshot(dsn, slot)
    handle.close()
    return handle


def _table_names(batch, namespace):
    """Table names carried by the marks in one namespace's item list."""
    names = set()
    for item in batch.items_by_namespace.get(namespace, []):
        hints = getattr(getattr(item, "meta", None), "hints", None)
        if hints:
            names.add(hints["table_name"])
        table = getattr(getattr(item, "meta", None), "table_name", None)
        if table:
            names.add(table)
    return names


def _rows(batch, namespace, table):
    out = []
    for item in batch.items_by_namespace.get(namespace, []):
        if getattr(getattr(item, "meta", None), "table_name", None) == table:
            out.extend(item.data)
    return out


def test_an_empty_slot_yields_no_batches(fresh_db, names):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key)")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    _slot(fresh_db, names["slot"])
    assert list(stream.drain(fresh_db, names["slot"], names["publication"], 0)) == []
    assert stream.max_lsn(fresh_db, names["slot"], names["publication"]) is None


def test_two_schemas_with_the_same_table_name_stay_apart(fresh_db, names):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        for schema in ("a", "b"):
            c.execute(f'create schema "{schema}"')
            c.execute(f'create table "{schema}".t (id int primary key, v text)')
    rels = [("a", "t"), ("b", "t")]
    publication.reconcile(fresh_db, names["publication"], rels)
    publication.ensure_replica_identity(fresh_db, rels, "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into a.t values (1, 'from-a')")
        c.execute("insert into b.t values (1, 'from-b')")

    keys = {r: ["id"] for r in rels}
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation=keys))
    merged = {}
    for b in batches:
        for ns, items in b.items_by_namespace.items():
            merged.setdefault(ns, []).extend(items)
    assert set(merged) == {"a", "b"}
    a_rows = [d for item in merged["a"] if getattr(getattr(item, "meta", None), "table_name", None) == "t"
              for d in item.data]
    b_rows = [d for item in merged["b"] if getattr(getattr(item, "meta", None), "table_name", None) == "t"
              for d in item.data]
    assert [r["v"] for r in a_rows] == ["from-a"]
    assert [r["v"] for r in b_rows] == ["from-b"]


def test_the_declared_key_overrides_the_replica_identity_flags(fresh_db, names):
    """REPLICA IDENTITY FULL flags EVERY column as key; a whole-row merge key turns each
    UPDATE into a duplicate (the #2851 regression this override exists to prevent)."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    publication.ensure_replica_identity(fresh_db, [("public", "t")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.t values (1, 'a')")

    batch = next(iter(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                   keys_by_relation={("public", "t"): ["id"]})))
    meta = batch.items_by_namespace["public"][0]
    columns = meta.meta.hints["columns"]
    assert columns["id"]["primary_key"] is True
    assert columns["v"]["primary_key"] is False
    assert meta.meta.hints["write_disposition"] == "merge"
    assert columns["lsn"]["dedup_sort"] == "desc"
    assert columns["deleted_ts"]["hard_delete"] is True


def test_a_transaction_larger_than_the_batch_size_lands_whole(fresh_db, names):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db) as c:   # ONE transaction, 50 rows
        for i in range(50):
            c.execute("insert into public.t values (%s, %s)", (i, str(i)))
        c.commit()
        commit_lsn = None

    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                target_batch_size=10,
                                keys_by_relation={("public", "t"): ["id"]}))
    assert len(batches) == 1, "a commit was split across batches"
    rows = _rows(batches[0], "public", "t")
    assert len(rows) == 50
    assert batches[0].consumed_all is True
    assert batches[0].last_commit_lsn > 0


def test_many_transactions_split_into_several_batches(fresh_db, names):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
        publication.reconcile(fresh_db, names["publication"], [("public", "t")])
        _slot(fresh_db, names["slot"])
        for i in range(40):
            c.execute("insert into public.t values (%s, %s)", (i, str(i)))
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                target_batch_size=5,
                                keys_by_relation={("public", "t"): ["id"]}))
    assert len(batches) > 1
    assert sum(len(_rows(b, "public", "t")) for b in batches) == 40
    assert batches[-1].consumed_all is True
    # last_commit_lsn is monotonic — the checkpoint may only move forward
    lsns = [b.last_commit_lsn for b in batches]
    assert lsns == sorted(lsns)


def test_a_large_unchanged_toast_value_survives_an_unrelated_update(fresh_db, names):
    payload = "x" * 100_000
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, blob text, note text)")
        # STORAGE EXTERNAL forces the value out of line UNCOMPRESSED — a repeating string
        # otherwise compresses inline and the test never exercises the TOAST path at all.
        c.execute("alter table public.t alter column blob set storage external")
        c.execute("insert into public.t values (1, %s, 'before')", (payload,))
        assert c.execute(
            "select pg_relation_size(reltoastrelid) from pg_class "
            "where oid = 'public.t'::regclass").fetchone()[0] > 0, "value did not go out of line"
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    publication.ensure_replica_identity(fresh_db, [("public", "t")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("update public.t set note = 'after' where id = 1")

    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "t"): ["id"]}))
    rows = [r for b in batches for r in _rows(b, "public", "t")]
    assert len(rows) == 1
    assert rows[0]["note"] == "after"
    assert rows[0]["blob"] == payload, "the unchanged TOAST value was not spliced back in"


def test_an_unchanged_toast_update_without_replica_identity_full_fails_loudly(fresh_db, names):
    payload = "y" * 100_000
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, blob text, note text)")
        c.execute("alter table public.t alter column blob set storage external")
        c.execute("insert into public.t values (1, %s, 'before')", (payload,))
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("update public.t set note = 'after' where id = 1")
    with pytest.raises(ValueError, match="REPLICA IDENTITY FULL"):
        list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                          keys_by_relation={("public", "t"): ["id"]}))


def test_advance_moves_the_slot_only_where_told(fresh_db, names):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key)")
        publication.reconcile(fresh_db, names["publication"], [("public", "t")])
        _slot(fresh_db, names["slot"])
        for i in range(5):
            c.execute("insert into public.t values (%s)", (i,))
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                target_batch_size=1))
    first = batches[0].last_commit_lsn
    stream.advance(fresh_db, names["slot"], first)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        confirmed = int(c.execute(
            "select (confirmed_flush_lsn - '0/0')::bigint from pg_replication_slots "
            "where slot_name = %s", (names["slot"],)).fetchone()[0])
    assert confirmed == first
    # restarting at 0 resumes from confirmed_flush — measured on PG 17, not assumed
    resumed = list(stream.drain(fresh_db, names["slot"], names["publication"], 0))
    assert resumed and resumed[0].last_commit_lsn >= first


def test_the_vendored_single_schema_resource_still_yields_a_flat_stream(fresh_db, names):
    """`ItemGenerator.__iter__` now delegates to the namespace-grouped `collect()`; the upstream
    `replication_resource` shape must not change under it."""
    from dlt.sources.credentials import ConnectionStringCredentials
    from pg_replication.helpers import ItemGenerator

    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
        publication.reconcile(fresh_db, names["publication"], [("public", "t")])
        _slot(fresh_db, names["slot"])
        c.execute("insert into public.t values (1, 'a')")

    creds = ConnectionStringCredentials(fresh_db)
    options = {"publication_names": names["publication"], "proto_version": "1"}
    gen = ItemGenerator(credentials=creds, slot_name=names["slot"], options=options,
                        upto_lsn=stream.max_lsn(fresh_db, names["slot"], names["publication"]),
                        start_lsn=0, target_batch_size=1000,
                        include_columns=None, columns=None)
    items = list(gen)
    assert len(items) == 2                     # meta item + one marked payload
    assert items[1].meta.table_name == "t"
    assert [r["v"] for r in items[1].data] == ["a"]
    assert gen.generated_all is True and gen.last_commit_lsn > 0


def test_a_replication_connection_that_cannot_open_reports_the_real_error(names):
    """An unopenable replication connection (unreachable source, or the slot already has a
    consumer) used to die in `finally` with `UnboundLocalError: cur`, hiding the cause."""
    from dlt.sources.credentials import ConnectionStringCredentials
    from pg_replication.helpers import ItemGenerator

    gen = ItemGenerator(credentials=ConnectionStringCredentials(
        "postgresql://cdc@127.0.0.1:1/nope"), slot_name=names["slot"],
        options={"publication_names": names["publication"], "proto_version": "1"}, upto_lsn=1)
    with pytest.raises(Exception) as exc:
        gen.collect()
    assert not isinstance(exc.value, UnboundLocalError), exc.value
    assert "connect" in str(exc.value).lower() or "connection" in str(exc.value).lower()
