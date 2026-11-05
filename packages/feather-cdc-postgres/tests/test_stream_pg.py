"""Slot consumption: schema-qualified identity, batch boundaries, TOAST (#3079)."""
from __future__ import annotations

import itertools

import psycopg
import pytest

from feather_cdc_postgres import publication, snapshot, stream


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


def test_a_stored_generated_column_streams_with_its_value(fresh_db, names):
    """Featherbase's `has_role.person` is STORED generated (person-rename 0101). On PG 18 the
    publication publishes it, so inserts, updates and deletes decode with the value."""
    with psycopg.connect(fresh_db) as c:
        if c.info.server_version < 180000:
            pytest.skip("publish_generated_columns is PostgreSQL 18+")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.g (id int primary key, kind text, v text, "
                  "person text generated always as (case when kind = 'P' then v end) stored)")
    publication.reconcile(fresh_db, names["publication"], [("public", "g")])
    publication.ensure_replica_identity(fresh_db, [("public", "g")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.g values (1, 'P', 'a'), (2, 'S', 'x')")
        c.execute("update public.g set v = 'b' where id = 1")
        c.execute("delete from public.g where id = 2")

    rows = []
    for batch in stream.drain(fresh_db, names["slot"], names["publication"], 0,
                              keys_by_relation={("public", "g"): ["id"]}):
        rows.extend(_rows(batch, "public", "g"))
    by_id = {}
    for r in rows:
        by_id.setdefault(r["id"], []).append(r)
    assert [r.get("person") for r in by_id[1]] == ["a", "b"]
    assert by_id[2][0]["person"] is None and by_id[2][-1].get("deleted_ts") is not None


def _hinted_columns(batch, namespace, table):
    """Column names of every hints (meta) item bound for `table`, in batch order."""
    out = []
    for item in batch.items_by_namespace.get(namespace, []):
        hints = getattr(getattr(item, "meta", None), "hints", None)
        if hints and hints.get("table_name") == table:
            out.append(set(hints.get("columns") or {}))
    return out


def test_rows_changed_then_the_table_renamed_in_one_transaction_drain_to_the_commit(
        fresh_db, names):
    """#4345: a transaction UPDATEs rows of a table and THEN renames it. pgoutput sends the new
    Relation message mid-transaction; the decoder used to stop there, before any commit, so the
    drain had no commit LSN and every later tick died on `None >> 32` after loading a partial
    transaction. The whole transaction must drain to its commit, each row under the name the
    table had when the row was written."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute('create table public."user" (id int primary key, v text)')
        c.execute("insert into public.\"user\" values (1, 'a'), (2, 'b')")
    publication.reconcile(fresh_db, names["publication"], [("public", "user")])
    publication.ensure_replica_identity(fresh_db, [("public", "user")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db) as c:   # ONE transaction: rows first, rename after
        c.execute("update public.\"user\" set v = 'a2' where id = 1")
        c.execute('alter table public."user" rename to person')
        c.execute("update public.person set v = 'b2' where id = 2")
        c.execute("insert into public.person values (3, 'c')")
        c.commit()
    with psycopg.connect(fresh_db, autocommit=True) as c:   # and one ordinary commit after it
        c.execute("insert into public.person values (4, 'd')")
    upto = stream.max_lsn(fresh_db, names["slot"], names["publication"])

    for size in (1000, 1):   # one batch, and one commit per batch
        batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                    target_batch_size=size,
                                    keys_by_relation={("public", "user"): ["id"],
                                                      ("public", "person"): ["id"]}))
        assert batches and batches[-1].consumed_all is True
        assert all(b.last_commit_lsn for b in batches), "a batch ended without a commit"
        assert batches[-1].last_commit_lsn == upto
        user = [r for b in batches for r in _rows(b, "public", "user")]
        person = [r for b in batches for r in _rows(b, "public", "person")]
        assert [(r["id"], r["v"]) for r in user] == [(1, "a2")]
        assert [(r["id"], r["v"]) for r in person] == [(2, "b2"), (3, "c"), (4, "d")]
        # the renaming transaction lands whole, in ONE batch, never split at the rename
        mixed = [b for b in batches if _rows(b, "public", "user")]
        assert len(mixed) == 1 and [r["id"] for r in _rows(mixed[0], "public", "person")][:2] \
            == [2, 3]


def test_a_column_added_mid_transaction_keeps_both_halves_with_their_own_hints(
        fresh_db, names):
    """#4345, the other mid-transaction Relation message: ALTER TABLE ADD COLUMN between two
    changes of the same table. Both halves land in one batch; the later half's hints carry the
    new column, the earlier half's do not."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
        c.execute("insert into public.t values (1, 'a'), (2, 'b')")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    publication.ensure_replica_identity(fresh_db, [("public", "t")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db) as c:
        c.execute("update public.t set v = 'a2' where id = 1")
        c.execute("alter table public.t add column w text")
        c.execute("update public.t set w = 'x' where id = 2")
        c.commit()

    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "t"): ["id"]}))
    assert len(batches) == 1 and batches[0].consumed_all and batches[0].last_commit_lsn
    rows = _rows(batches[0], "public", "t")
    assert [(r["id"], r["v"], r.get("w")) for r in rows] == [(1, "a2", None), (2, "b", "x")]
    hinted = _hinted_columns(batches[0], "public", "t")
    assert len(hinted) == 2 and "w" not in hinted[0] and "w" in hinted[1]


def test_a_truncate_after_a_mid_transaction_column_change_discards_both_halves(
        fresh_db, names):
    """#4345 review: rows of the table collected before a mid-transaction Relation message sit
    in an earlier group; a keyed TRUNCATE later in the batch must discard them too, or they
    would be loaded AFTER the runner's delete and resurrect truncated rows."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
        c.execute("insert into public.t values (1, 'a')")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    publication.ensure_replica_identity(fresh_db, [("public", "t")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db) as c:
        c.execute("update public.t set v = 'a2' where id = 1")
        c.execute("alter table public.t add column w text")
        c.execute("insert into public.t values (2, 'b', 'x')")
        c.execute("truncate public.t")
        c.execute("insert into public.t values (3, 'c', 'y')")
        c.commit()

    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "t"): ["id"]}))
    assert len(batches) == 1
    assert [t["table"] for t in batches[0].truncates] == ["t"]
    assert [r["id"] for r in _rows(batches[0], "public", "t")] == [3]


def test_a_batch_that_ends_without_a_commit_fails_closed_before_it_is_loaded(monkeypatch):
    """#4345 backstop: should the consumer ever stop without reaching a commit, the drain must
    raise a clear error BEFORE yielding — never hand the runner a partial transaction to load,
    and never loop on a `None` restart LSN."""
    class _NoCommit:
        def __init__(self, **kw):
            self.last_commit_lsn = None
            self.generated_all = False
            self.truncates = []
            self.key_problems = {}

        def collect(self):
            return {"public": ["a partial transaction"]}

    monkeypatch.setattr(stream, "ItemGenerator", _NoCommit)
    monkeypatch.setattr(stream, "get_max_lsn", lambda *a, **k: 100)
    drained = stream.drain("postgresql://unused", "s", "p", 0)
    with pytest.raises(RuntimeError, match="without reaching a commit"):
        next(drained)


def test_repeated_column_changes_still_split_batches_at_the_batch_size(fresh_db, names):
    """#4345 Codex review: rows retained under an earlier description still count towards
    `target_batch_size`, so a table altered in every transaction cannot grow one batch without
    limit."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    publication.ensure_replica_identity(fresh_db, [("public", "t")], "full")
    _slot(fresh_db, names["slot"])
    for i in range(20):
        with psycopg.connect(fresh_db) as c:
            for j in range(4):
                c.execute("insert into public.t (id, v) values (%s, 'x')", (i * 10 + j,))
            c.execute(f"alter table public.t add column c{i} text")
            for j in range(4, 8):
                c.execute("insert into public.t (id, v) values (%s, 'y')", (i * 10 + j,))
            c.commit()

    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                target_batch_size=10,
                                keys_by_relation={("public", "t"): ["id"]}))
    assert len(batches) >= 10, "the batch size was ignored"
    assert sum(len(_rows(b, "public", "t")) for b in batches) == 160
    assert all(b.last_commit_lsn for b in batches) and batches[-1].consumed_all


def test_a_truncate_after_a_rename_swap_discards_the_other_relations_rows_of_that_name(
        fresh_db, names):
    """#4345 Codex adversarial: `a` and `b` swap names in one transaction, then the new `a` is
    truncated. The rows written under the name `a` BEFORE the swap belong to the other relation
    but target the same destination table; they must be discarded too, or they load after the
    runner's delete and resurrect."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.a (id int primary key, v text)")
        c.execute("create table public.b (id int primary key, v text)")
        c.execute("insert into public.a values (1, 'a1')")
        c.execute("insert into public.b values (1, 'b1')")
    rels = [("public", "a"), ("public", "b")]
    publication.reconcile(fresh_db, names["publication"], rels)
    publication.ensure_replica_identity(fresh_db, rels, "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db) as c:
        c.execute("update public.a set v = 'a1-changed' where id = 1")
        c.execute("alter table public.a rename to tmp")
        c.execute("alter table public.b rename to a")
        c.execute("alter table public.tmp rename to b")
        c.execute("update public.a set v = 'b1-changed' where id = 1")
        c.execute("truncate public.a")
        c.execute("insert into public.a values (2, 'after-truncate')")
        c.commit()

    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={r: ["id"] for r in rels}))
    assert len(batches) == 1
    assert [t["table"] for t in batches[0].truncates] == ["a"]
    assert [(r["id"], r["v"]) for r in _rows(batches[0], "public", "a")] == [
        (2, "after-truncate")]


def _primary_keys(batch, namespace, table):
    """Primary-key columns of every hints item bound for `table`, in batch order."""
    out = []
    for item in batch.items_by_namespace.get(namespace, []):
        hints = getattr(getattr(item, "meta", None), "hints", None)
        if hints and hints.get("table_name") == table:
            out.append(sorted(n for n, c in (hints.get("columns") or {}).items()
                              if c.get("primary_key")))
    return out


def test_a_swapped_name_never_lends_its_new_owner_s_key_to_an_undeclared_relation(
        fresh_db, names):
    """#4345 review: with no declared key the decoder reads the key from the source catalog
    NOW, by name. After a swap the name `a` belongs to the other table, whose key is `code`;
    the records written under `a` before the swap must not merge on `code`. The lookup checks
    the table's oid and reports the key problem instead."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.a (id int primary key, code text not null unique)")
        c.execute("create table public.b (id int not null unique, code text primary key)")
        c.execute("insert into public.a values (1, 'x')")
        c.execute("insert into public.b values (1, 'y')")
    rels = [("public", "a"), ("public", "b")]
    publication.reconcile(fresh_db, names["publication"], rels)
    publication.ensure_replica_identity(fresh_db, rels, "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db) as c:
        c.execute("update public.a set code = 'x2' where id = 1")
        c.execute("alter table public.a rename to tmp")
        c.execute("alter table public.b rename to a")
        c.execute("alter table public.tmp rename to b")
        c.execute("update public.b set code = 'x3' where id = 1")   # the former `a`
        c.commit()

    (batch,) = list(stream.drain(fresh_db, names["slot"], names["publication"], 0))
    assert _primary_keys(batch, "public", "a") != [["code"]], "the swapped-in table's key"
    assert "belongs to another table" in batch.key_problems[("public", "a")]
    # the same relation under its CURRENT name resolves its own key from the catalog
    assert _primary_keys(batch, "public", "b") == [["id"]]
    assert ("public", "b") not in batch.key_problems


def test_a_large_transaction_ending_a_batch_is_delivered_once(fresh_db, names):
    """#4345 review: the drain restarts at the last commit LSN. A transaction of at least
    `target_batch_size` rows that ends a batch must not be re-delivered by that restart — or
    every restart would stop at the same commit for ever. Measured with and without advancing
    the slot in between (the runner advances; a bare drain does not)."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.t (id int primary key, v text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "t")])
    _slot(fresh_db, names["slot"])
    for k in range(3):
        with psycopg.connect(fresh_db) as c:
            for i in range(20):
                c.execute("insert into public.t values (%s, 'x')", (k * 100 + i,))
            c.commit()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.t values (999, 'y')")

    for advance in (False, True):
        seen = []
        for batch in itertools.islice(
                stream.drain(fresh_db, names["slot"], names["publication"], 0,
                             target_batch_size=10, keys_by_relation={("public", "t"): ["id"]}),
                10):   # a re-delivery loop would run past this; four batches are expected
            seen.append((batch.last_commit_lsn, len(_rows(batch, "public", "t"))))
            if advance:
                stream.advance(fresh_db, names["slot"], batch.last_commit_lsn)
        assert [n for _, n in seen] == [20, 20, 20, 1]
        assert [lsn for lsn, _ in seen] == sorted({lsn for lsn, _ in seen})


# --------------------------------------------------------------------------- deny.columns (#4346)


def _all_rows(batches, namespace, table):
    return [r for b in batches for r in _rows(b, namespace, table)]


def _hint_columns(batches, namespace, table):
    out = set()
    for b in batches:
        for item in b.items_by_namespace.get(namespace, []):
            hints = getattr(getattr(item, "meta", None), "hints", None)
            if hints and hints.get("table_name") == table:
                out |= set(hints["columns"])
    return out


# The reviewed shape (a stored signature's columns): on a table that carries a deny, any column
# outside it is withheld until reviewed (#4346), so a test of other behaviour declares it.
REVIEWED = {("public", "person"): ["id", "email"]}


def _review(dsn, *qualified):
    """What a baseline records under its locks (#4346): each table's `ReviewedLayout`, by OID.
    Every description of a table that carries a deny must match it exactly."""
    from pg_replication.helpers import published_shape
    out = {}
    with psycopg.connect(dsn, autocommit=True) as c:
        for q in qualified:
            oid = int(c.execute("select %s::regclass::oid", (q,)).fetchone()[0])
            out[oid] = published_shape(c.cursor(), oid)[0]
    return out


def _person_stream(dsn, names, schemas=("public",)):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in schemas:
            if s != "public":
                c.execute(f'create schema "{s}"')
            c.execute(f'create table "{s}".person (id int primary key, email text, '
                      "password_hash text)")
    rels = [(s, "person") for s in schemas]
    publication.reconcile(dsn, names["publication"], rels)
    publication.ensure_replica_identity(dsn, rels, "full")
    _slot(dsn, names["slot"])
    return rels


def test_insert_update_delete_land_without_the_denied_column(fresh_db, names):
    _person_stream(fresh_db, names)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1'), (2, 'b@x', 'h2')")
        c.execute("update public.person set email = 'a2@x', password_hash = 'h1b' where id = 1")
        c.execute("delete from public.person where id = 2")
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "person"): ["id"]},
                                deny_columns_by_relation={("public", "person"): ["password_hash"]},
                                approved_columns_by_relation=REVIEWED,
                                reviewed_layout_by_oid=_review(fresh_db, "public.person")))
    rows = _all_rows(batches, "public", "person")
    assert len(rows) == 4                       # 2 inserts, 1 update, 1 delete
    assert all("password_hash" not in r for r in rows)
    assert [r["email"] for r in rows if "deleted_ts" not in r] == ["a@x", "b@x", "a2@x"]
    assert [r["id"] for r in rows if "deleted_ts" in r] == [2]
    hinted = _hint_columns(batches, "public", "person")
    assert "password_hash" not in hinted and {"id", "email", "lsn"} <= hinted


def test_a_deny_on_a_t_does_not_strip_b_t(fresh_db, names):
    """The vendored consumer looked `include_columns` up by BARE table name; a projection keyed
    that way would reach every schema's `person`. Identity is (schema, table)."""
    _person_stream(fresh_db, names, schemas=("a", "b"))
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into a.person values (1, 'a@x', 'ha')")
        c.execute("insert into b.person values (1, 'b@x', 'hb')")
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("a", "person"): ["id"], ("b", "person"): ["id"]},
                                deny_columns_by_relation={("a", "person"): ["password_hash"]},
                                reviewed_layout_by_oid=_review(fresh_db, "a.person")))
    (a_row,) = _all_rows(batches, "a", "person")
    (b_row,) = _all_rows(batches, "b", "person")
    assert "password_hash" not in a_row
    assert b_row["password_hash"] == "hb"
    assert "password_hash" in _hint_columns(batches, "b", "person")


def test_an_update_of_only_the_denied_column_is_a_row_version_with_no_visible_change(
        fresh_db, names):
    _person_stream(fresh_db, names)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
        c.execute("update public.person set password_hash = 'rotated' where id = 1")
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "person"): ["id"]},
                                deny_columns_by_relation={("public", "person"): ["password_hash"]},
                                reviewed_layout_by_oid=_review(fresh_db, "public.person")))
    insert, update = _all_rows(batches, "public", "person")
    assert update["lsn"] > insert["lsn"]
    assert {k: v for k, v in update.items() if k != "lsn"} == \
           {k: v for k, v in insert.items() if k != "lsn"}


def test_a_key_change_tombstone_carries_no_denied_column(fresh_db, names):
    _person_stream(fresh_db, names)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
        c.execute("update public.person set id = 7 where id = 1")
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "person"): ["id"]},
                                deny_columns_by_relation={("public", "person"): ["password_hash"]},
                                reviewed_layout_by_oid=_review(fresh_db, "public.person")))
    rows = _all_rows(batches, "public", "person")
    tombstones = [r for r in rows if "deleted_ts" in r]
    assert [t["id"] for t in tombstones] == [1]
    assert all("password_hash" not in r for r in rows)


def test_the_deny_holds_in_every_group_of_a_mid_transaction_relation_change(fresh_db, names):
    """#4345 splits a batch into one group per relation description; each group builds its own
    hints and rows, so each must carry the projection — including a group written under a
    table's OLD name (the `user` -> `person` rename), denied in its own right. A table rename
    leaves the column description as reviewed, so it loads (a column change would quarantine,
    Codex round 17)."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute('create table public."user" (id int primary key, email text, password_hash text)')
    publication.reconcile(fresh_db, names["publication"], [("public", "user")])
    publication.ensure_replica_identity(fresh_db, [("public", "user")], "full")
    _slot(fresh_db, names["slot"])
    reviewed = _review(fresh_db, 'public."user"')
    with psycopg.connect(fresh_db) as c:   # ONE transaction across two descriptions
        c.execute("insert into public.\"user\" values (1, 'a@x', 'h1')")
        c.execute("insert into public.\"user\" values (2, 'b@x', 'h2')")
        c.execute('alter table public."user" rename to person')
        c.execute("update public.person set password_hash = 'h2b', email = 'b2@x' where id = 2")
        c.commit()
    deny = {("public", "user"): ["password_hash"], ("public", "person"): ["password_hash"]}
    batches = list(stream.drain(fresh_db, names["slot"], names["publication"], 0,
                                keys_by_relation={("public", "user"): ["id"],
                                                  ("public", "person"): ["id"]},
                                deny_columns_by_relation=deny,
                                reviewed_layout_by_oid=reviewed))
    rows = _all_rows(batches, "public", "user") + _all_rows(batches, "public", "person")
    assert len(rows) == 3
    assert all("password_hash" not in r for r in rows)
    for table in ("user", "person"):
        hinted = _hint_columns(batches, "public", table)
        assert hinted and "password_hash" not in hinted


def _drain_all(dsn, names, **kw):
    return list(stream.drain(dsn, names["slot"], names["publication"], 0, **kw))


def test_any_column_added_mid_batch_quarantines_a_denied_table(fresh_db, names):
    """Codex round 17 (#4346): an added column changes the description, so the table is
    quarantined at the first change under it — added columns are no longer withheld. The row
    written under the reviewed description still loads; nothing after it does."""
    _person_stream(fresh_db, names)
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
        c.execute("alter table public.person add column reset_token text")
        c.execute("insert into public.person values (2, 'b@x', 'h2', 'tok')")
        c.commit()
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         approved_columns_by_relation=REVIEWED, reviewed_layout_by_oid=reviewed)
    rows = _all_rows(batches, "public", "person")
    assert [r["email"] for r in rows] == ["a@x"]
    assert all("password_hash" not in r and "reset_token" not in r for r in rows)
    (why,) = [w for b in batches for _s, _t, w in b.quarantined.values()]
    assert "differs from the reviewed layout" in why


def test_a_rename_mid_transaction_cannot_release_a_denied_column(fresh_db, names):
    """(b) Privacy-critical: `person` renamed mid-transaction to a name with NO deny entry. The
    rows after the rename arrive in a group under the new name; the deny follows the OID."""
    _person_stream(fresh_db, names)
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
        c.execute("alter table public.person rename to member")
        c.execute("insert into public.member values (2, 'b@x', 'h2')")
        c.execute("update public.member set password_hash = 'h1b' where id = 1")
        c.commit()
    with psycopg.connect(fresh_db, autocommit=True) as c:   # and a LATER batch, same drain
        c.execute("insert into public.member values (3, 'c@x', 'h3')")
    batches = _drain_all(fresh_db, names, target_batch_size=1,
                         keys_by_relation={("public", "person"): ["id"],
                                           ("public", "member"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         reviewed_layout_by_oid=reviewed)
    assert len(batches) == 2
    rows = _all_rows(batches, "public", "person") + _all_rows(batches, "public", "member")
    assert len(rows) == 4
    assert all("password_hash" not in r for r in rows)
    assert "password_hash" not in _hint_columns(batches, "public", "member")


def test_a_rename_before_the_drain_is_caught_by_the_seeded_oid(fresh_db, names):
    """(b) Across runs: the rename committed before this drain, so `person` never appears by
    name — only the OID the runner seeds (catalogue now, or the stored signature) catches it."""
    _person_stream(fresh_db, names)
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        oid = c.execute("select 'public.person'::regclass::oid").fetchone()[0]
        c.execute("alter table public.person rename to member")
        c.execute("insert into public.member values (1, 'a@x', 'h1')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "member"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         deny_columns_by_oid={int(oid): ["password_hash"]},
                         approved_columns_by_oid={int(oid): ["id", "email"]},
                         reviewed_layout_by_oid=reviewed)
    (row,) = _all_rows(batches, "public", "member")
    assert "password_hash" not in row and row["email"] == "a@x"


def test_a_denied_column_in_the_middle_does_not_shift_the_later_values(fresh_db, names):
    """(c) The filter runs by NAME after the positional zip; filtering first would shift every
    later value one column left."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.p (id int primary key, password_hash text, email text, "
                  "age int)")
    publication.reconcile(fresh_db, names["publication"], [("public", "p")])
    publication.ensure_replica_identity(fresh_db, [("public", "p")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.p values (1, 'h1', 'a@x', 30)")
        c.execute("update public.p set age = 31 where id = 1")
        c.execute("delete from public.p where id = 1")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "p"): ["id"]},
                         deny_columns_by_relation={("public", "p"): ["password_hash"]},
                         approved_columns_by_relation={("public", "p"): ["id", "email", "age"]},
                         reviewed_layout_by_oid=_review(fresh_db, "public.p"))
    ins, upd, dele = _all_rows(batches, "public", "p")
    assert (ins["id"], ins["email"], ins["age"]) == (1, "a@x", 30)
    assert (upd["email"], upd["age"]) == ("a@x", 31)
    assert dele["id"] == 1 and "password_hash" not in dele


def test_a_fallback_full_identity_key_never_puts_a_denied_column_in_a_tombstone(fresh_db, names):
    """Codex adversarial (#4346): a relation whose key resolves nowhere merges on REPLICA
    IDENTITY FULL's flags — every column, the denied one too. A key-changing UPDATE must not
    emit a tombstone keyed on (and carrying) the denied value; it is reported instead."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.nokey (id int, email text, password_hash text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "nokey")])
    publication.ensure_replica_identity(fresh_db, [("public", "nokey")], "full")
    _slot(fresh_db, names["slot"])
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.nokey values (1, 'a@x', 'h1')")
        c.execute("update public.nokey set id = 2 where id = 1")
    batches = _drain_all(fresh_db, names,
                         deny_columns_by_relation={("public", "nokey"): ["password_hash"]},
                         reviewed_layout_by_oid=_review(fresh_db, "public.nokey"))
    rows = _all_rows(batches, "public", "nokey")
    assert rows and all("password_hash" not in r for r in rows)
    assert not any("deleted_ts" in r for r in rows), "no tombstone keyed on a denied column"
    problems = {k: v for b in batches for k, v in b.key_problems.items()}
    assert "denied column" in problems[("public", "nokey")]


def test_a_column_renamed_away_from_a_denied_name_quarantines_the_table(fresh_db, names):
    """Sonnet review F1, Codex round 16 (#4346): `password_hash` renamed to `pw`. A renamed
    column on a table that carries a deny breaks the layout proof (a name proves nothing), so
    the table is quarantined: none of its changes load, the hash least of all."""
    _person_stream(fresh_db, names)
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
        c.execute("alter table public.person rename column password_hash to pw")
        c.execute("insert into public.person values (2, 'b@x', 'h2')")
        c.execute("update public.person set pw = 'h1b' where id = 1")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         approved_columns_by_relation={("public", "person"): ["id", "email"]},
                         reviewed_layout_by_oid=reviewed)
    rows = _all_rows(batches, "public", "person")
    assert [r["id"] for r in rows] == [1], "only the row under the reviewed description"
    assert ("public", "person") in {k for b in batches for k in b.quarantined_names}


def _oid(dsn, qualified):
    with psycopg.connect(dsn) as c:
        return int(c.execute("select %s::regclass::oid", (qualified,)).fetchone()[0])


def test_the_decoder_follows_a_table_rename_by_oid_and_quarantines_a_column_rename(
        fresh_db, names):
    """The deny follows the OID through a table rename (the description is unchanged, so it
    loads); a column rename changes the description, so the table is quarantined (Codex round
    17: any DDL on a denied table needs a resnapshot)."""
    _person_stream(fresh_db, names)
    oid = _oid(fresh_db, "public.person")
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.person rename to member")
        c.execute("insert into public.member values (1, 'a@x', 'h1')")
        c.execute("alter table public.member rename column password_hash to pw")
        c.execute("insert into public.member values (2, 'b@x', 'h2')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "member"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         deny_columns_by_oid={oid: ["password_hash"]},
                         deny_attnums_by_oid={oid: [3]},
                         approved_columns_by_oid={oid: ["id", "email"]},
                         reviewed_layout_by_oid=reviewed)
    (row,) = _all_rows(batches, "public", "member")
    assert "pw" not in row and "password_hash" not in row and row["email"] == "a@x"
    assert {o for b in batches for o in b.quarantined} == {oid}


def test_a_denied_column_renamed_onto_a_reviewed_name_quarantines_the_table(fresh_db, names):
    """Drop `email`, rename `password_hash` onto it: the description no longer matches the
    reviewed layout, so nothing of it loads."""
    _person_stream(fresh_db, names)
    oid = _oid(fresh_db, "public.person")
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.person drop column email")
        c.execute("alter table public.person rename column password_hash to email")
        c.execute("insert into public.person values (1, 'h1')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         deny_attnums_by_oid={oid: [3]},
                         approved_columns_by_oid={oid: ["id", "email"]},
                         reviewed_layout_by_oid=reviewed)
    assert _all_rows(batches, "public", "person") == []
    assert ("public", "person") in {k for b in batches for k in b.quarantined_names}


def test_a_swap_and_restore_of_column_names_quarantines_the_historical_description(
        fresh_db, names):
    """Codex round 13 (#4346): swap `email` and `password_hash` names, write, swap back, THEN
    drain. The queued Relation message names the secret `email`, and today's catalogue says
    `email` is the safe column — names cannot prove identity. Column ORDER can: the description
    does not line up with the run-start layout, so none of its rows are emitted."""
    _person_stream(fresh_db, names)
    oid = _oid(fresh_db, "public.person")
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
        c.execute("alter table public.person rename column email to tmp_swap")
        c.execute("alter table public.person rename column password_hash to email")
        c.execute("alter table public.person rename column tmp_swap to password_hash")
        # by NAME: under the swapped names, `email` is the secret column (attnum 3)
        c.execute("insert into public.person (id, email, password_hash) "
                  "values (2, 'HASH_SENTINEL', 'b@x')")
        c.execute("alter table public.person rename column password_hash to tmp_swap")
        c.execute("alter table public.person rename column email to password_hash")
        c.execute("alter table public.person rename column tmp_swap to email")
        c.execute("insert into public.person values (3, 'c@x', 'h3')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         deny_attnums_by_oid={oid: [3]},
                         approved_columns_by_oid={oid: ["id", "email"]},
                         reviewed_layout_by_oid=reviewed)
    rows = _all_rows(batches, "public", "person")
    assert all(r.get("email") != "HASH_SENTINEL" for r in rows)
    assert all("password_hash" not in r for r in rows)
    # The unprovable description quarantines the relation, and a quarantine is sticky: the
    # row written after the names were restored does not load either (Codex round 16).
    assert sorted(r["id"] for r in rows) == [1]
    assert {o for b in batches for o in b.quarantined} == {oid}


def test_a_denied_column_renamed_onto_the_merge_key_is_quarantined(fresh_db, names):
    """Codex round 13 (#4346): drop the text key, rename `password_hash` to its name. A merge key
    is never withheld — so a key that resolves to the denied identity stops the description."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.tok (code text primary key, email text, password_hash text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "tok")])
    publication.ensure_replica_identity(fresh_db, [("public", "tok")], "full")
    _slot(fresh_db, names["slot"])
    oid = _oid(fresh_db, "public.tok")
    reviewed = _review(fresh_db, "public.tok")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.tok drop column code")
        c.execute("alter table public.tok rename column password_hash to code")
        c.execute("insert into public.tok values ('e@x', 'HASH_SENTINEL')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "tok"): ["code"]},
                         deny_columns_by_relation={("public", "tok"): ["password_hash"]},
                         deny_attnums_by_oid={oid: [3]},
                         approved_columns_by_oid={oid: ["code", "email"]},
                         reviewed_layout_by_oid=reviewed)
    # (the description mismatch catches it; the next test isolates the merge-key guard)
    assert _all_rows(batches, "public", "tok") == []
    assert ("public", "tok") in {k for b in batches for k in b.quarantined_names}


def test_a_merge_key_on_the_denied_attnum_is_quarantined_even_when_the_layout_proves(
        fresh_db, names):
    """No DDL, so the description matches its reviewed layout; but the merge key the decoder is
    handed names the denied attnum. A key is never withheld, so the guard must quarantine."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.tok (code text primary key, email text, password_hash text)")
    publication.reconcile(fresh_db, names["publication"], [("public", "tok")])
    publication.ensure_replica_identity(fresh_db, [("public", "tok")], "full")
    _slot(fresh_db, names["slot"])
    oid = _oid(fresh_db, "public.tok")
    reviewed = _review(fresh_db, "public.tok")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.tok values ('k1', 'e@x', 'HASH_SENTINEL')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "tok"): ["password_hash"]},
                         deny_columns_by_relation={("public", "tok"): ["password_hash"]},
                         deny_attnums_by_oid={oid: [3]}, reviewed_layout_by_oid=reviewed)
    assert _all_rows(batches, "public", "tok") == []
    assert ("public", "tok") in {k for b in batches for k in b.quarantined_names}


def test_a_table_renamed_onto_a_denied_name_mid_run_is_quarantined_as_unbound(fresh_db, names):
    """Codex round 16 P1 (#4346): `member` takes the denied name `person` mid-run and its text
    merge key and `password_hash` swap names (left swapped). The catalogue read now agrees with
    the swapped WAL, so a layout adopted now would bind the KEY as the denied column and emit
    the hash as the key. Its deny was bound at no trusted moment, so it is quarantined."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.person (id int primary key, email text, password_hash text)")
        c.execute("create table public.member (code text primary key, email text, "
                  "password_hash text)")
    rels = [("public", "person"), ("public", "member")]
    publication.reconcile(fresh_db, names["publication"], rels)
    publication.ensure_replica_identity(fresh_db, rels, "full")
    _slot(fresh_db, names["slot"])
    person, member = _oid(fresh_db, "public.person"), _oid(fresh_db, "public.member")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.person rename to person_old")
        c.execute("alter table public.member rename to person")
        c.execute("alter table public.person rename column code to tmp_swap")
        c.execute("alter table public.person rename column password_hash to code")
        c.execute("alter table public.person rename column tmp_swap to password_hash")
        c.execute("insert into public.person (code, email, password_hash) "
                  "values ('HASH_SENTINEL', 'x@x', 'k2')")
    batches = _drain_all(fresh_db, names,
                         keys_by_relation={("public", "person"): ["code"],
                                           ("public", "member"): ["code"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         deny_columns_by_oid={person: ["password_hash"]},
                         deny_attnums_by_oid={person: [3]},
                         bound_oids={person})
    rows = _all_rows(batches, "public", "person")
    assert all("HASH_SENTINEL" not in map(str, r.values()) for r in rows)
    assert rows == []
    assert {o: (s, t) for b in batches for o, (s, t, _w) in b.quarantined.items()} == {
        member: ("public", "person")}


def test_a_drop_rename_readd_that_reproduces_the_reviewed_description_is_caught_by_the_drop(
        fresh_db, names):
    """Codex round 17 (#4346): drop `email`, rename `password_hash` to `email`, add a new
    `password_hash`. The Relation message then reads exactly like the review — same count,
    names, types and key flags — but its `email` is the secret's attnum. Only the catalogue
    shows it: a reviewed attnum was dropped, so the table is quarantined."""
    _person_stream(fresh_db, names)
    oid = _oid(fresh_db, "public.person")
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.person drop column email")
        c.execute("alter table public.person rename column password_hash to email")
        c.execute("alter table public.person add column password_hash text")
        c.execute("insert into public.person values (1, 'HASH_SENTINEL', 'x')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         deny_attnums_by_oid={oid: [3]},
                         approved_columns_by_oid={oid: ["id", "email"]},
                         reviewed_layout_by_oid=reviewed)
    assert _all_rows(batches, "public", "person") == []
    (why,) = [w for b in batches for _s, _t, w in b.quarantined.values()]
    assert "reviewed column was dropped" in why


def test_a_retyped_column_quarantines_a_denied_table(fresh_db, names):
    """Codex round 17 (#4346): the type OID is part of the description, so a type change on a
    table that carries a deny quarantines it too."""
    _person_stream(fresh_db, names)
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.person alter column email type varchar(40)")
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         approved_columns_by_relation=REVIEWED, reviewed_layout_by_oid=reviewed)
    assert _all_rows(batches, "public", "person") == []
    assert ("public", "person") in {k for b in batches for k in b.quarantined_names}


def test_a_table_with_no_reviewed_layout_is_quarantined(fresh_db, names):
    """Codex round 17 (#4346): no layout recorded at a baseline means no reference to compare
    with — the table is quarantined at its first change, never adopted from today's catalogue."""
    _person_stream(fresh_db, names)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         approved_columns_by_relation=REVIEWED)
    assert _all_rows(batches, "public", "person") == []
    (why,) = [w for b in batches for _s, _t, w in b.quarantined.values()]
    assert "no reviewed layout" in why


def test_a_replica_identity_change_quarantines_a_denied_table(fresh_db, names):
    """Codex round 17 (#4346): the per-column key flags are part of the description (FULL flags
    every column, DEFAULT only the primary key), so changing the replica identity quarantines."""
    _person_stream(fresh_db, names)
    reviewed = _review(fresh_db, "public.person")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("alter table public.person replica identity default")
        c.execute("insert into public.person values (1, 'a@x', 'h1')")
    batches = _drain_all(fresh_db, names, keys_by_relation={("public", "person"): ["id"]},
                         deny_columns_by_relation={("public", "person"): ["password_hash"]},
                         approved_columns_by_relation=REVIEWED, reviewed_layout_by_oid=reviewed)
    assert _all_rows(batches, "public", "person") == []
    assert ("public", "person") in {k for b in batches for k in b.quarantined_names}
