"""End-to-end runtime: snapshot → stream → checkpoint, and the recovery paths (#3079 Phase 2).

Every test drives the real `runner.run_relations` against a real Postgres and a real dlt load
into duckdb, because the invariants under test (the checkpoint never leads the commit, replay
does not duplicate, ownership blocks a second run) only exist across those three.
"""
from __future__ import annotations

import threading

import duckdb
import psycopg
import pytest

from control_plane import ControlPlane
from pg_md_cdc import publication, runner, stream, telemetry
from pg_md_cdc.destination import Destination, DestinationSpec
from pg_md_cdc.lifecycle import RETIRED, STREAMING
from pg_md_cdc.ownership import OwnerSession
from pg_md_cdc.selectors import Relation


class FakeRun:
    def __init__(self):
        self.events = []
        self.finished = None

    def event(self, **kw):
        self.events.append(kw)

    def finish(self, status, **kw):
        self.finished = (status, kw)

    def units(self, unit):
        return [e for e in self.events if e.get("unit") == unit]


def rel(schema, table, *key, policy="primary_key"):
    return Relation(schema=schema, table=table, key=tuple(key), key_policy=policy,
                    table_class="application", target_schema=schema)


def make(fresh_db, names, duckdb_path, tmp_path, **over):
    dest = Destination(DestinationSpec(kind="duckdb", database="test", path=duckdb_path,
                                       pipelines_dir=str(tmp_path / "dlt")))
    cfg = runner.RuntimeConfig(
        registration=names["registration"], source_id="test", source_dsn=fresh_db,
        slot=names["slot"], publication=names["publication"], extractor="test_cdc",
        destination=dest, replica_identity="full", **over)
    return cfg, dest


def tick(cfg, relations, *, run=None):
    """One full run under a freshly acquired owner lock."""
    run = run or FakeRun()
    with OwnerSession(cfg.source_dsn, cfg.slot) as owner:
        assert owner.held
        result = runner.run_relations(cfg, relations, run=run, owner=owner)
    return run, result


def sql(dsn, *statements):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in statements:
            c.execute(s)


def table(duckdb_path, qualified, order="1"):
    """Rows at the destination; a table dlt never had reason to create reads as empty."""
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute(f"select * from {qualified} order by {order}").fetchall()
    except duckdb.CatalogException:
        return []
    finally:
        con.close()


def confirmed_flush(dsn, slot):
    with psycopg.connect(dsn, autocommit=True) as c:
        row = c.execute("select (confirmed_flush_lsn - '0/0')::bigint from pg_replication_slots "
                        "where slot_name = %s", (slot,)).fetchone()
    return int(row[0]) if row else None


# --------------------------------------------------------------------------- 1 · convergence


def test_insert_update_delete_converge_across_two_drains(fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db,
        "create table public.orders (id int primary key, status text)",
        "create table public.lines (order_id int, line_no int, qty int, "
        "primary key (order_id, line_no))",
        "insert into public.orders values (1, 'new'), (2, 'new')",
        "insert into public.lines values (1, 1, 5), (1, 2, 7)")
    relations = [rel("public", "orders", "id"), rel("public", "lines", "order_id", "line_no")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)

    run1, _ = tick(cfg, relations)
    assert {e["unit"] for e in run1.events} >= {"snapshot", "lifecycle", "probe"}
    assert table(duckdb_path, "public.orders", "id") and len(table(duckdb_path, "public.lines")) == 2

    sql(fresh_db,
        "insert into public.orders values (3, 'new')",
        "update public.orders set status = 'shipped' where id = 1",
        "delete from public.orders where id = 2",
        "update public.lines set qty = 9 where order_id = 1 and line_no = 2",
        "delete from public.lines where order_id = 1 and line_no = 1")
    tick(cfg, relations)

    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, status from public.orders order by id").fetchall() == [
        (1, "shipped"), (3, "new")]
    assert con.execute("select order_id, line_no, qty from public.lines").fetchall() == [(1, 2, 9)]
    # composite key grain: no duplicates
    assert con.execute("select count(*) - count(distinct (order_id, line_no)) "
                       "from public.lines").fetchone() == (0,)
    con.close()


def test_a_large_unchanged_toast_value_survives_the_runtime(fresh_db, names, duckdb_path, tmp_path):
    payload = "z" * 120_000
    sql(fresh_db, "create table public.docs (id int primary key, blob text, note text)",
        "alter table public.docs alter column blob set storage external")
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("insert into public.docs values (1, %s, 'before')", (payload,))
        assert c.execute("select pg_relation_size(reltoastrelid) from pg_class "
                         "where oid = 'public.docs'::regclass").fetchone()[0] > 0
    relations = [rel("public", "docs", "id")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)                      # runtime applies REPLICA IDENTITY FULL here
    sql(fresh_db, "update public.docs set note = 'after' where id = 1")
    tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    got_note, got_md5 = con.execute("select note, md5(blob) from public.docs").fetchone()
    con.close()
    import hashlib
    assert got_note == "after"
    assert got_md5 == hashlib.md5(payload.encode()).hexdigest()


def test_a_transaction_larger_than_the_batch_lands_whole_and_checkpoints_at_its_commit(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path, target_batch_size=10)
    tick(cfg, relations)
    with psycopg.connect(fresh_db) as c:      # ONE transaction of 5 × target_batch_size
        for i in range(50):
            c.execute("insert into public.t values (%s, %s)", (i, str(i)))
        c.commit()
    tick(cfg, relations)
    assert len(table(duckdb_path, "public.t", "id")) == 50
    lsn, _ = dest.read_checkpoint(cfg.registration)
    assert lsn == confirmed_flush(fresh_db, cfg.slot), "slot and checkpoint disagree"


# --------------------------------------------------------------------------- 2 · quiet ticks


def test_an_empty_slot_run_probes_and_leaves_the_checkpoint_alone(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key)",
        "insert into public.t values (1)")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    before = dest.read_checkpoint(cfg.registration)

    run, result = tick(cfg, relations)
    assert result["batches"] == 0
    assert run.units("cdc") == [] and len(run.units("probe")) == 1
    assert dest.read_checkpoint(cfg.registration) == before
    assert len(table(duckdb_path, "public.t")) == 1


# --------------------------------------------------------------------------- 3 · recovery


def test_a_lost_slot_re_inits_and_re_snapshots(fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    # the fuse firing looks exactly like this to the next tick: the slot is simply gone
    sql(fresh_db, f"select pg_drop_replication_slot('{cfg.slot}')")
    sql(fresh_db, "insert into public.t values (2, 'b')")

    run, _ = tick(cfg, relations)
    moves = [(e["extra"]["from"], e["extra"]["to"]) for e in run.units("lifecycle")]
    # a streaming table may not jump back to snapshotting — the state history has to say WHY
    assert ("streaming", "resnapshot_required") in moves
    assert ("resnapshot_required", "snapshotting") in moves
    assert [e["unit"] for e in run.events].count("snapshot") >= 1
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.t order by id").fetchall() == [(1, "a"), (2, "b")]
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()
    assert telemetry.slot_probe(fresh_db, cfg.slot)["wal_status"] == "reserved"


def test_an_ambiguous_destination_commit_replays_without_duplicating(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The load committed but the checkpoint write did not. The next run MUST replay that
    batch — and merge must make the replay a no-op at key grain."""
    sql(fresh_db, "create table public.t (id int primary key, v text)")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    sql(fresh_db, "insert into public.t values (1, 'a'), (2, 'b')",
        "update public.t set v = 'a2' where id = 1")

    original = dest.write_checkpoint
    calls = {"n": 0}

    def exploding(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("destination acknowledged the load, then the checkpoint write died")

    monkeypatch.setattr(dest, "write_checkpoint", exploding)
    with pytest.raises(RuntimeError):
        tick(cfg, relations)
    assert calls["n"] == 1
    monkeypatch.setattr(dest, "write_checkpoint", original)

    tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.t order by id").fetchall() == [(1, "a2"), (2, "b")]
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()


def test_a_failed_load_leaves_the_checkpoint_and_the_slot_where_they_were(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    sql(fresh_db, "create table public.t (id int primary key, v text)")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    before_checkpoint = dest.read_checkpoint(cfg.registration)
    before_flush = confirmed_flush(fresh_db, cfg.slot)
    sql(fresh_db, "insert into public.t values (1, 'a')")

    monkeypatch.setattr(Destination, "load",
                        staticmethod(lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))))
    with pytest.raises(RuntimeError):
        tick(cfg, relations)

    assert dest.read_checkpoint(cfg.registration) == before_checkpoint
    assert confirmed_flush(fresh_db, cfg.slot) == before_flush
    assert table(duckdb_path, "public.t") == []


# --------------------------------------------------------------------------- 4 · namespaces


def test_two_source_schemas_with_the_same_table_name_land_separately(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db,
        "create schema sales", "create schema hr",
        "create table sales.person (id int primary key, v text)",
        "create table hr.person (id int primary key, v text)",
        "insert into sales.person values (1, 'customer')",
        "insert into hr.person values (1, 'employee')")
    relations = [rel("sales", "person", "id"), rel("hr", "person", "id")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    sql(fresh_db, "update sales.person set v = 'customer2' where id = 1",
        "insert into hr.person values (2, 'employee2')")
    tick(cfg, relations)

    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from sales.person order by id").fetchall() == [(1, "customer2")]
    assert con.execute("select id, v from hr.person order by id").fetchall() == [
        (1, "employee"), (2, "employee2")]
    con.close()


# --------------------------------------------------------------------------- 5 · selection


def test_a_table_added_mid_life_gets_one_baseline_and_later_changes_exactly_once(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.a (id int primary key, v text)",
        "insert into public.a values (1, 'a')")
    first = [rel("public", "a", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, first)

    # created and populated BEFORE it is selected — "created empty" does not close the race
    sql(fresh_db, "create table public.b (id int primary key, v text)",
        "insert into public.b values (1, 'pre-existing'), (2, 'also')")
    widened = first + [rel("public", "b", "id")]
    run, _ = tick(cfg, widened)
    assert [e["entity"] for e in run.units("backfill")] == ["b"]
    assert table(duckdb_path, "public.b", "id") and len(table(duckdb_path, "public.b")) == 2

    sql(fresh_db, "update public.b set v = 'changed' where id = 1",
        "insert into public.b values (3, 'new')")
    run2, _ = tick(cfg, widened)
    assert run2.units("backfill") == [], "a table was baselined twice"
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.b order by id").fetchall() == [
        (1, "changed"), (2, "also"), (3, "new")]
    assert con.execute("select count(*) - count(distinct id) from public.b").fetchone() == (0,)
    con.close()


def test_narrowing_drops_from_the_publication_and_freezes_the_destination_copy(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db,
        "create table public.a (id int primary key, v text)",
        "create table public.b (id int primary key, v text)",
        "insert into public.a values (1, 'a')", "insert into public.b values (1, 'b')")
    wide = [rel("public", "a", "id"), rel("public", "b", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, wide)
    assert len(table(duckdb_path, "public.b")) == 1

    narrow = [rel("public", "a", "id")]
    tick(cfg, narrow)
    assert publication.published_relations(fresh_db, cfg.publication) == {("public", "a")}
    assert dest.read_lifecycle(cfg.registration)[("public", "b")] == RETIRED
    assert dest.read_lifecycle(cfg.registration)[("public", "a")] == STREAMING
    # frozen mirror: bronze never deletes history
    assert len(table(duckdb_path, "public.b")) == 1

    # ...and the flow really stopped
    sql(fresh_db, "insert into public.b values (2, 'after narrowing')")
    tick(cfg, narrow)
    assert len(table(duckdb_path, "public.b")) == 1


# --------------------------------------------------------------------------- 6 · ownership


def test_a_second_concurrent_run_touches_nothing(fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    before = (publication.published_relations(fresh_db, cfg.publication),
              dest.read_checkpoint(cfg.registration), confirmed_flush(fresh_db, cfg.slot))
    sql(fresh_db, "insert into public.t values (2, 'b')")

    holder = OwnerSession(fresh_db, cfg.slot)
    assert holder.acquire()
    try:
        cp = ControlPlane("test_cdc", dsn=None, wal_dir=str(tmp_path / "wal"))
        assert runner.execute(cfg, relations, cp=cp) == 0
    finally:
        holder.release()

    assert (publication.published_relations(fresh_db, cfg.publication),
            dest.read_checkpoint(cfg.registration),
            confirmed_flush(fresh_db, cfg.slot)) == before
    assert len(table(duckdb_path, "public.t")) == 1, "the skipped run loaded rows anyway"

    # the lock released, the next run picks the change up
    assert runner.execute(cfg, relations,
                          cp=ControlPlane("test_cdc", dsn=None, wal_dir=str(tmp_path / "wal"))) == 0
    assert len(table(duckdb_path, "public.t")) == 2


def test_two_simultaneous_first_runs_produce_one_slot_owner(fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    outcomes: list = []

    def go(i):
        cp = ControlPlane("test_cdc", dsn=None, wal_dir=str(tmp_path / f"wal{i}"))
        try:
            outcomes.append(runner.execute(cfg, relations, cp=cp))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert outcomes == [0, 0], outcomes
    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()


def _report_lost_once(monkeypatch):
    """Make the next slot probe read `wal_status='lost'` — the fuse firing, without a 2 GB write."""
    real = telemetry.slot_probe
    calls = {"n": 0}

    def fake(dsn, slot):
        calls["n"] += 1
        probe = real(dsn, slot)
        if calls["n"] == 1 and probe is not None:
            return {**probe, "wal_status": "lost"}
        return probe

    monkeypatch.setattr(runner.telemetry, "slot_probe", fake)
    return calls


def test_an_invalidated_slot_is_rebuilt_and_the_data_converges(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    old_slot_created = confirmed_flush(fresh_db, cfg.slot)
    sql(fresh_db, "insert into public.t values (2, 'b')")

    _report_lost_once(monkeypatch)
    run, _ = tick(cfg, relations)

    moves = [(e["extra"]["from"], e["extra"]["to"]) for e in run.units("lifecycle")]
    assert ("streaming", "resnapshot_required") in moves
    assert any("invalidated" in (e["extra"].get("detail") or "") for e in run.units("lifecycle"))
    assert telemetry.slot_probe(fresh_db, cfg.slot)["wal_status"] == "reserved"
    assert publication.published_relations(fresh_db, cfg.publication) == {("public", "t")}
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.t order by id").fetchall() == [(1, "a"), (2, "b")]
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()
    assert old_slot_created is not None


def test_slot_loss_policy_pause_stops_instead_of_re_snapshotting(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path, slot_loss_policy="pause")
    tick(cfg, relations)
    before = dest.read_checkpoint(cfg.registration)

    _report_lost_once(monkeypatch)
    run, result = tick(cfg, relations)

    assert result == {"paused": True}
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == "resnapshot_required"
    assert dest.read_checkpoint(cfg.registration) == before
    assert run.units("cdc") == [] and run.units("snapshot") == []


def test_resnapshot_refuses_without_explicit_approval():
    """Dropping a destination table is the one separately-approved destructive act (D6)."""
    with pytest.raises(SystemExit) as exc:
        runner.resnapshot(object(), tables=["public.t"], yes=False)
    assert "--yes" in str(exc.value)


def test_drop_table_removes_the_merge_staging_twin_too(fresh_db, names, duckdb_path, tmp_path):
    """A stale `<schema>_staging` twin replays into the fresh table on the next merge."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    sql(fresh_db, "update public.t set v = 'b' where id = 1")
    tick(cfg, relations)   # a merge load creates public_staging.t

    con = duckdb.connect(duckdb_path)
    before = {r[0] for r in con.execute(
        "select table_schema || '.' || table_name from information_schema.tables "
        "where table_name = 't'").fetchall()}
    con.close()
    assert before == {"public.t", "public_staging.t"}

    dest.drop_table("public", "t")
    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) from information_schema.tables "
                       "where table_name = 't'").fetchone() == (0,)
    con.close()


def test_a_steady_state_run_records_lifecycle_for_tables_it_did_not_baseline(
        fresh_db, names, duckdb_path, tmp_path):
    """A takeover (the pilot's slot, or a `_cdc_runtime` rebuilt underneath us) leaves tables
    streaming with NO lifecycle row. The run adopts them rather than reporting an empty
    lifecycle table — but adoption must never overwrite a state that already says otherwise."""
    sql(fresh_db, "create table public.a (id int primary key, v text)",
        "create table public.b (id int primary key, v text)",
        "insert into public.a values (1, 'a')", "insert into public.b values (1, 'b')")
    relations = [rel("public", "a", "id"), rel("public", "b", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    con = duckdb.connect(duckdb_path)
    con.execute('delete from "_cdc_runtime".lifecycle')
    con.close()
    assert dest.read_lifecycle(cfg.registration) == {}

    sql(fresh_db, "insert into public.a values (2, 'a2')")
    tick(cfg, relations)
    assert dest.read_lifecycle(cfg.registration) == {
        ("public", "a"): STREAMING, ("public", "b"): STREAMING}

    dest.write_lifecycle(cfg.registration, "public", "b", "drift_review", "hand-set")
    tick(cfg, relations)
    assert dest.read_lifecycle(cfg.registration)[("public", "b")] == "drift_review"
