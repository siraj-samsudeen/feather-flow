"""Exported-snapshot baselines with nothing written to the source (#3079 Deviation D-1)."""
from __future__ import annotations

import duckdb
import psycopg
import pytest

from pg_md_cdc import publication, snapshot, stream
from pg_md_cdc.destination import Destination, DestinationSpec


def _dest(duckdb_path, tmp_path):
    return Destination(DestinationSpec(kind="duckdb", database="test", path=duckdb_path,
                                       pipelines_dir=str(tmp_path / "dlt")))


@pytest.fixture
def seeded(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        for t in ("t1", "t2", "t3"):
            c.execute(f'create table public."{t}" (id int primary key, v text)')
            c.execute(f'insert into public."{t}" values (1, %s), (2, %s)', (f"{t}-a", f"{t}-b"))
    return fresh_db


def test_set_transaction_snapshot_must_precede_closing_the_exporter(seeded, names):
    """The ordering the whole design rests on, asserted rather than assumed."""
    handle = snapshot.create_slot_with_snapshot(seeded, names["slot"])
    handle.close()
    with psycopg.connect(seeded) as conn:
        conn.execute("begin isolation level repeatable read")
        with pytest.raises(psycopg.errors.UndefinedObject):
            conn.execute(f"set transaction snapshot '{handle.snapshot_name}'")


def test_the_read_survives_the_exporter_closing_after_the_import(seeded, names):
    handle = snapshot.create_slot_with_snapshot(seeded, names["slot"])
    keys = {("public", "t1"): ["id"]}
    with snapshot.snapshot_resources(seeded, handle.snapshot_name, [("public", "t1")], keys) as by:
        handle.close()
        rows = list(by["public"][0])
    assert sorted(r["id"] for r in rows) == [1, 2]


def test_the_source_gains_no_staging_tables(seeded, names, duckdb_path, tmp_path):
    dest = _dest(duckdb_path, tmp_path)
    handle = snapshot.create_slot_with_snapshot(seeded, names["slot"])
    rels = [("public", t) for t in ("t1", "t2", "t3")]
    keys = {r: ["id"] for r in rels}
    with snapshot.snapshot_resources(seeded, handle.snapshot_name, rels, keys) as by:
        handle.close()
        dest.pipeline_for_schema("public", "public").run(by["public"])
    with psycopg.connect(seeded) as c:
        assert c.execute("select count(*) from pg_tables where schemaname='public' "
                         "and tablename like '\\_dlt%'").fetchone()[0] == 0


def test_snapshot_excludes_rows_written_after_the_slot_and_the_stream_carries_them(
        seeded, names, duckdb_path, tmp_path):
    """No gap, no duplicate at key grain — the convergence claim, end to end."""
    dest = _dest(duckdb_path, tmp_path)
    slot, pub = names["slot"], names["publication"]
    rels = [("public", t) for t in ("t1", "t2", "t3")]
    publication.reconcile(seeded, pub, rels)
    publication.ensure_replica_identity(seeded, rels, "full")
    handle = snapshot.create_slot_with_snapshot(seeded, slot)
    keys = {r: ["id"] for r in rels}

    with snapshot.snapshot_resources(seeded, handle.snapshot_name, rels, keys) as by:
        handle.close()
        # concurrent writes AFTER the slot exists but BEFORE the snapshot is read
        with psycopg.connect(seeded, autocommit=True) as c:
            c.execute("insert into public.t1 values (3, 'late')")
            c.execute("update public.t2 set v = 'changed' where id = 1")
            c.execute("delete from public.t3 where id = 2")
        snapshot_rows = {t: list(res) for t, res in zip(("t1", "t2", "t3"), by["public"])}
        dest.pipeline_for_schema("public", "public").run(by["public"])

    assert 3 not in {r["id"] for r in snapshot_rows["t1"]}, "snapshot leaked a post-slot insert"
    assert {r["v"] for r in snapshot_rows["t2"] if r["id"] == 1} == {"t2-a"}

    pipe = dest.pipeline_for_schema("public", "public")
    for batch in stream.drain(seeded, slot, pub, 0, keys_by_relation=keys):
        Destination.load(pipe, batch.items_by_namespace["public"], resource_name=slot)

    con = duckdb.connect(duckdb_path)
    assert sorted(con.execute("select id, v from public.t1 order by id").fetchall()) == [
        (1, "t1-a"), (2, "t1-b"), (3, "late")]
    assert con.execute("select v from public.t2 where id = 1").fetchone() == ("changed",)
    assert con.execute("select id from public.t3 order by id").fetchall() == [(1,)]
    for t in ("t1", "t2", "t3"):
        total, distinct = con.execute(
            f"select count(*), count(distinct id) from public.{t}").fetchone()
        assert total == distinct, f"{t} duplicated at key grain"
    con.close()


def test_column_types_come_from_the_catalog_not_from_the_values(seeded, names):
    """int4 must compile the same on the snapshot path as on the CDC path (plan Appendix A)."""
    with psycopg.connect(seeded, autocommit=True) as c:
        c.execute("create table public.typed (id int primary key, big bigint, flag bool, "
                  "amount numeric(12,2), payload jsonb, ts timestamptz)")
    with psycopg.connect(seeded) as conn:
        hints = snapshot.column_hints(conn, "public", "typed", ["id"])
    assert hints["id"]["primary_key"] is True and hints["big"]["primary_key"] is False
    assert hints["flag"]["data_type"] == "bool"
    assert hints["amount"]["data_type"] == "decimal" and hints["amount"]["scale"] == 2
    assert hints["payload"]["data_type"] == "json"
    assert hints["ts"]["data_type"] == "timestamp" and hints["ts"]["timezone"] is True
    # int4 and int8 must compile IDENTICALLY here and on the CDC path — the live
    # BIGINT-vs-INTEGER split in bronze_featherbase came from two different mappers.
    assert hints["id"]["data_type"] == hints["big"]["data_type"] == "bigint"
    from pg_replication.decoders import ColumnType
    from pg_replication.schema_types import _to_dlt_column_schema
    assert _to_dlt_column_schema(ColumnType(1, "id", 23, -1))["data_type"] == hints["id"]["data_type"]


def test_baseline_resource_reads_a_table_added_later(seeded, names, duckdb_path, tmp_path):
    dest = _dest(duckdb_path, tmp_path)
    with snapshot.baseline_resource(seeded, ("public", "t1"), ["id"]) as res:
        dest.pipeline_for_schema("public", "public").run(res)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) from public.t1").fetchone() == (2,)
    con.close()


def test_the_baseline_slot_is_temporary_and_dies_with_its_session(seeded, names, monkeypatch):
    """A container killed between creating `<slot>_bl` and dropping it must not leave a
    persistent slot retaining WAL on the source — nothing drops it if the table is then
    narrowed out. TEMPORARY makes PostgreSQL drop it when the session ends, whatever we do."""
    temp = snapshot.temp_slot_name(names["slot"])

    def slot_row(column):
        with psycopg.connect(seeded) as c:
            return c.execute(f"select {column} from pg_replication_slots where slot_name = %s",
                             (temp,)).fetchone()

    handle = snapshot.create_slot_with_snapshot(seeded, temp, temporary=True)
    assert slot_row("temporary") == (True,) and handle.consistent_point_lsn > 0
    handle.close()
    assert slot_row("1") is None, "the exporter closed — PostgreSQL dropped the slot itself"

    monkeypatch.setattr(snapshot, "drop_slot_if_exists", lambda dsn, slot: False)  # the kill
    with pytest.raises(RuntimeError):
        with snapshot.consistent_baseline(seeded, names["slot"], ("public", "t1"), ["id"]):
            raise RuntimeError("killed mid-baseline")
    assert slot_row("1") is None
