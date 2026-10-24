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
        dest.pipeline_for_target("public").run(by["public"])
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
        dest.pipeline_for_target("public").run(by["public"])

    assert 3 not in {r["id"] for r in snapshot_rows["t1"]}, "snapshot leaked a post-slot insert"
    assert {r["v"] for r in snapshot_rows["t2"] if r["id"] == 1} == {"t2-a"}

    pipe = dest.pipeline_for_target("public")
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
        dest.pipeline_for_target("public").run(res)
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


# --------------------------------------------------------------------------- deny.columns (#4346)


def _person(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("create table public.person (id int primary key, email text, password_hash text)")
        c.execute("insert into public.person values (1, 'a@x', 'h1'), (2, 'b@x', 'h2')")


def test_a_denied_column_is_neither_hinted_nor_read_by_the_baseline(fresh_db, names, duckdb_path,
                                                                   tmp_path):
    _person(fresh_db)
    with psycopg.connect(fresh_db) as conn:
        hints = snapshot.column_hints(conn, "public", "person", ["id"], ["password_hash"])
        assert set(hints) == {"id", "email"}
        select_list = snapshot._select_list(conn, "public", "person", ["password_hash"])
        assert "password_hash" not in select_list.as_string(conn)

    dest = _dest(duckdb_path, tmp_path)
    with snapshot.consistent_baseline(fresh_db, names["slot"], ("public", "person"), ["id"],
                                      deny_columns=["password_hash"]) as (res, _boundary):
        dest.pipeline_for_target("public").run(res)
    con = duckdb.connect(duckdb_path)
    cols = {r[0] for r in con.execute(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = 'person'").fetchall()}
    assert "password_hash" not in cols and {"id", "email"} <= cols
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a@x"), (2, "b@x")]
    con.close()


def test_a_deny_that_would_empty_the_select_list_raises_instead_of_selecting_star(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.only_secret (secret text)")
    with psycopg.connect(fresh_db) as conn:
        with pytest.raises(ValueError, match="every column is denied"):
            snapshot._select_list(conn, "public", "only_secret", ["secret"])


def test_an_oid_deny_on_a_key_column_refuses_the_baseline_before_reading(fresh_db, names):
    _person(fresh_db)
    with psycopg.connect(fresh_db) as c:
        oid = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    with pytest.raises(ValueError, match="merge key"):
        with snapshot.consistent_baseline(fresh_db, names["slot"], ("public", "person"), ["id"],
                                          deny_by_oid={oid: ["id"]}):
            pass


def test_a_rename_cannot_slip_in_between_the_oid_binding_and_the_read(fresh_db, names):
    """Codex round 8 (#4346): names resolve against the CURRENT catalogue in each statement, so
    the baseline pins its relations (ACCESS SHARE) before binding OIDs; a swap must wait until
    the read is over, never land between resource construction and extraction."""
    _person(fresh_db)
    handle = snapshot.create_slot_with_snapshot(fresh_db, names["slot"])
    try:
        with snapshot.snapshot_resources(fresh_db, handle.snapshot_name, [("public", "person")],
                                         {("public", "person"): ["id"]},
                                         deny_columns={("public", "person"): ["password_hash"]}
                                         ) as by_schema:
            handle.close()
            with psycopg.connect(fresh_db, autocommit=True) as other:
                other.execute("set lock_timeout = '500ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    other.execute("alter table public.person rename to member")
            (res,) = by_schema["public"]
            rows = list(res)
        assert rows and all("password_hash" not in r for r in rows)
    finally:
        handle.close()


def test_a_swap_between_snapshot_export_and_import_projects_by_the_current_relation(
        fresh_db, names):
    """Codex round 9 (#4346): `person` (protected OID) and `member` swap names AFTER the slot
    exported its snapshot but BEFORE the baseline imports it. A catalogue join by name inside
    the imported snapshot still sees the OLD names; `LOCK`/`SELECT` see the new ones. Every
    lookup must agree with what is read."""
    _person(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.member (id int primary key, email text, password_hash text)")
        protected = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    handle = snapshot.create_slot_with_snapshot(fresh_db, names["slot"])
    try:
        with psycopg.connect(fresh_db, autocommit=True) as c:
            c.execute("alter table public.person rename to tmp_swap")
            c.execute("alter table public.member rename to person")
            c.execute("alter table public.tmp_swap rename to member")
        oids: dict = {}
        with snapshot.snapshot_resources(fresh_db, handle.snapshot_name, [("public", "member")],
                                         {("public", "member"): ["id"]}, oids_out=oids,
                                         deny_by_oid={protected: ["password_hash"]}) as by_schema:
            handle.close()
            (res,) = by_schema["public"]
            rows = list(res)
        assert oids[("public", "member")] == protected
        assert rows and all("password_hash" not in r for r in rows)
    finally:
        handle.close()


def test_a_column_swap_between_export_and_import_never_reads_the_secret(fresh_db, names):
    """Codex round 14 (#4346): swap `email` and `password_hash` NAMES after the slot exported
    its snapshot. Inside the snapshot the catalogue still shows the old names, but the SELECT
    resolves current ones — so names must come from a fresh read taken under the locks."""
    _person(fresh_db)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        oid = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    handle = snapshot.create_slot_with_snapshot(fresh_db, names["slot"])
    try:
        with psycopg.connect(fresh_db, autocommit=True) as c:
            c.execute("alter table public.person rename column email to tmp_swap")
            c.execute("alter table public.person rename column password_hash to email")
            c.execute("alter table public.person rename column tmp_swap to password_hash")
        with snapshot.snapshot_resources(fresh_db, handle.snapshot_name, [("public", "person")],
                                         {("public", "person"): ["id"]},
                                         deny_columns={("public", "person"): ["password_hash"]},
                                         deny_attnums_by_oid={oid: [3]}) as by_schema:
            handle.close()
            (res,) = by_schema["public"]
            rows = list(res)
        values = {v for r in rows for v in r.values()}
        assert rows and not ({"h1", "h2"} & values), "the secret column was read"
    finally:
        handle.close()


def _baseline_outputs(dsn, slot, ddl=(), **kw):
    """Run one snapshot_resources over public.person with `ddl` applied between the export and
    the import; return (layouts_out, bound_out)."""
    layouts, bound = {}, {}
    handle = snapshot.create_slot_with_snapshot(dsn, slot)
    try:
        with psycopg.connect(dsn, autocommit=True) as c:
            for s in ddl:
                c.execute(s)
        with snapshot.snapshot_resources(dsn, handle.snapshot_name, [("public", "person")],
                                         {("public", "person"): ["id"]},
                                         deny_columns={("public", "person"): ["password_hash"]},
                                         layouts_out=layouts, bound_out=bound, **kw) as by_schema:
            handle.close()
            list(by_schema["public"][0])
    finally:
        handle.close()
    return layouts, bound


def test_a_baseline_records_its_reviewed_layout_and_binds_the_denied_attnum(fresh_db, names):
    """Codex round 16 (#4346): the layout later descriptions are proven against, and the denied
    name's attnum, both read under the baseline's locks."""
    _person(fresh_db)
    with psycopg.connect(fresh_db) as c:
        oid = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    layouts, bound = _baseline_outputs(fresh_db, names["slot"])
    # The full published description (Codex round 17): name, type OID, typmod and key flag per
    # attnum — what every later Relation message of the table must equal.
    oid_, layout = layouts[("public", "person")]
    assert oid_ == oid and layout.hidden == frozenset()
    assert [(a, n) for a, n, *_ in layout.columns] == [(1, "id"), (2, "email"),
                                                       (3, "password_hash")]
    assert [(t, m) for _a, _n, t, m, _k in layout.columns] == [(23, -1), (25, -1), (25, -1)]
    assert bound[("public", "person")] == [("password_hash", 3)]


def test_a_column_dropped_between_export_and_lock_leaves_no_reviewed_layout(fresh_db, names):
    """Codex round 16 (#4346): WAL written in the export-to-lock window, before a drop, cannot
    be proven against the layout read after it — so the baseline records none, and the decoder
    then quarantines the table (no reviewed layout) rather than trust today's names."""
    _person(fresh_db)
    layouts, _ = _baseline_outputs(fresh_db, names["slot"],
                                   ddl=["alter table public.person drop column email"])
    (_oid, layout), = layouts.values()
    assert layout is None


def test_a_denied_name_bound_to_no_column_refuses_the_baseline(fresh_db, names):
    """Codex round 16 (#4346): `password_hash` renamed before anything bound it. Under the
    locks the name is not a column and the table has no bound attnum: which column holds the
    secret is unknown, so the baseline refuses rather than read `pw`."""
    _person(fresh_db)
    with pytest.raises(ValueError, match="never bound"):
        _baseline_outputs(fresh_db, names["slot"],
                          ddl=["alter table public.person rename column password_hash to pw"])
    # A bound attnum covers the same rename: the baseline reads, without `pw`.
    with psycopg.connect(fresh_db, autocommit=True) as c:
        oid = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    snapshot.drop_slot_if_exists(fresh_db, names["slot"])
    layouts, _ = _baseline_outputs(fresh_db, names["slot"], deny_attnums_by_oid={oid: [3]})
    assert layouts[("public", "person")][1].columns[2][:2] == (3, "pw")
