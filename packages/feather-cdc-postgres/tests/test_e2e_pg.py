"""End-to-end runtime: snapshot → stream → checkpoint, and the recovery paths (#3079 Phase 2).

Every test drives the real `runner.run_relations` against a real Postgres and a real dlt load
into duckdb, because the invariants under test (the checkpoint never leads the commit, replay
does not duplicate, ownership blocks a second run) only exist across those three.
"""
from __future__ import annotations

import contextlib
import threading

import duckdb
import psycopg
import pytest

from conftest import finish_record
from control_plane import ControlPlane
from feather_cdc_postgres import publication, runner, stream, telemetry
from feather_cdc_postgres.destination import Destination, DestinationSpec
from feather_cdc_postgres.lifecycle import RETIRED, STREAMING
from feather_cdc_postgres.ownership import OwnershipLost, OwnerSession
from feather_cdc_postgres.selectors import Relation


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


def test_a_crash_between_slot_creation_and_the_snapshot_still_gets_its_baseline(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The worst crash window in the whole runtime: the slot EXISTS but the snapshot never
    loaded. The next run sees a live slot, goes straight to steady state, and the rows that
    were there before the consistent point are in no WAL record — so unless the interrupted
    `snapshotting` state is resumed, they are lost silently and forever."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'pre-existing'), (2, 'also')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)

    monkeypatch.setattr(runner.snapshot, "snapshot_resources",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("container killed")))
    with pytest.raises(RuntimeError):
        tick(cfg, relations)
    monkeypatch.undo()
    assert telemetry.slot_probe(fresh_db, cfg.slot) is not None, "the slot outlived the crash"
    assert dest.read_lifecycle(cfg.registration) == {("public", "t"): "snapshotting"}

    sql(fresh_db, "insert into public.t values (3, 'after')")
    run, _ = tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.t order by id").fetchall() == [
        (1, "pre-existing"), (2, "also"), (3, "after")]
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()
    assert dest.read_lifecycle(cfg.registration) == {("public", "t"): STREAMING}


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
    assert [e["entity"] for e in run.units("backfill")] == ["public.b"]
    assert table(duckdb_path, "public.b", "id") and len(table(duckdb_path, "public.b")) == 2
    # A newcomer is never ADOPTED as streaming: adoption is only for a relation the publication
    # already carries (evidence that some earlier drainer baselined it). Claiming `streaming`
    # for one that has never been read makes the lifecycle trail — an operator's only record of
    # whether a table was ever baselined — say the opposite of what happened.
    trail = [e["extra"]["to"] for e in run.units("lifecycle") if e["entity"] == "public.b"]
    assert trail[0] == "snapshotting" and trail[-1] == STREAMING

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


def test_a_lost_owner_lock_stops_the_slot_rebuild_before_it_drops_anything(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The invalidated-slot path DROPS the publication and the slot. If the owner session died
    in the meantime the lock is already free, so a peer may be re-initialising — the drop must
    not happen first and be discovered afterwards."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    owner = OwnerSession(fresh_db, cfg.slot)
    assert owner.acquire()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        for (pid,) in c.execute(
                "select pid from pg_locks where locktype = 'advisory' and granted").fetchall():
            c.execute("select pg_terminate_backend(%s)", (pid,))

    _report_lost_once(monkeypatch)
    with pytest.raises(OwnershipLost):
        runner.run_relations(cfg, relations, run=FakeRun(), owner=owner)

    assert publication.published_relations(fresh_db, cfg.publication) == {("public", "t")}
    assert telemetry.slot_probe(fresh_db, cfg.slot) is not None, "the slot was dropped anyway"


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

    assert result["paused"] is True and result["outcome"]["status"] == "failed"
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


# --------------------------------------------------------------------------- append_only


def test_an_append_only_replay_after_a_crash_lands_each_change_exactly_once(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """A keyless relation gets primary_key=[], so a plain "merge" DEGRADES TO APPEND and the
    batch replayed after a crash between the load commit and the checkpoint write lands twice.
    append_only merges on `lsn` — unique per WAL record — so the replay is exact."""
    sql(fresh_db, "create table public.events (id int, note text)",
        "insert into public.events values (0, 'baseline')")
    relations = [rel("public", "events", policy="append_only")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)   # the snapshot creates the table with NO key; CDC then adds `lsn`
    sql(fresh_db, "insert into public.events values (1, 'a'), (2, 'b')")

    original = dest.write_checkpoint
    monkeypatch.setattr(dest, "write_checkpoint", lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("the load committed, then the checkpoint write died")))
    with pytest.raises(RuntimeError):
        tick(cfg, relations)
    monkeypatch.setattr(dest, "write_checkpoint", original)

    tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, note from public.events order by id").fetchall() == [
        (0, "baseline"), (1, "a"), (2, "b")]
    assert con.execute("select count(*) - count(distinct lsn) from public.events "
                       "where lsn is not null").fetchone() == (0,)
    con.close()


def test_an_append_only_update_adds_a_row_version_while_a_keyed_update_replaces(
        fresh_db, names, duckdb_path, tmp_path):
    """append_only promises inserts: an UPDATE is recorded as an arrival, not applied, and a
    DELETE record removes nothing. The keyed table beside it is the positive control."""
    sql(fresh_db,
        "create table public.events (id int, note text)",
        "create table public.keyed (id int primary key, note text)",
        "insert into public.events values (1, 'first')",
        "insert into public.keyed values (1, 'first')")
    relations = [rel("public", "events", policy="append_only"), rel("public", "keyed", "id")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db, "update public.events set note = 'second' where id = 1",
        "update public.keyed set note = 'second' where id = 1",
        "insert into public.events values (2, 'gone')",
        "delete from public.events where id = 2")
    tick(cfg, relations)

    con = duckdb.connect(duckdb_path)
    assert con.execute("select note from public.events where id = 1 order by lsn nulls first").fetchall() == [
        ("first",), ("second",)], "the update landed as an additional row version"
    assert con.execute("select count(*) from public.events where id = 2").fetchone() == (2,), \
        "the insert AND the delete are both recorded as arrivals; neither removes the other"
    assert con.execute("select count(*) from public.events where id = 2 "
                       "and deleted_ts is not null").fetchone() == (1,)
    assert con.execute("select note from public.keyed").fetchall() == [("second",)]
    con.close()


def test_resuming_an_interrupted_append_only_snapshot_replaces_the_baseline(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """A snapshot row carries no `lsn` to dedup on, so re-reading a keyless table on top of a
    partial copy doubles it. The resume must DROP the destination copy before re-baselining."""
    sql(fresh_db, "create table public.events (id int, note text)",
        "insert into public.events values (1, 'pre'), (2, 'also')")
    relations = [rel("public", "events", policy="append_only")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)

    # The slot is created, the baseline load lands, and THEN the run dies — so the lifecycle
    # row still says `snapshotting` and the next run retakes the same baseline.
    real = runner.snapshot.snapshot_resources

    @contextlib.contextmanager
    def load_then_die(*a, **kw):
        with real(*a, **kw) as by_schema:
            yield by_schema
        raise RuntimeError("container killed after the snapshot load")

    monkeypatch.setattr(runner.snapshot, "snapshot_resources", load_then_die)
    with pytest.raises(RuntimeError):
        tick(cfg, relations)
    monkeypatch.undo()
    assert dest.read_lifecycle(cfg.registration) == {("public", "events"): "snapshotting"}
    assert len(table(duckdb_path, "public.events")) == 2, "the interrupted baseline did land"

    tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, note from public.events order by id").fetchall() == [
        (1, "pre"), (2, "also")]
    con.close()
    assert dest.read_lifecycle(cfg.registration) == {("public", "events"): STREAMING}


def test_a_crash_between_the_publication_add_and_the_baseline_still_gets_its_baseline(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The same silent-loss window as the snapshot crash, one phase later: the table is IN the
    publication but never baselined. The next tick sees nothing to add, and a relation with no
    lifecycle row that is already published is ADOPTED as `streaming` — so its pre-add rows are
    in no WAL record and no phase would ever read them."""
    sql(fresh_db, "create table public.a (id int primary key, v text)",
        "insert into public.a values (1, 'a')")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    first = [rel("public", "a", "id")]
    tick(cfg, first)

    sql(fresh_db, "create table public.b (id int primary key, v text)",
        "insert into public.b values (1, 'pre-existing'), (2, 'also')")
    widened = first + [rel("public", "b", "id")]
    monkeypatch.setattr(runner.snapshot, "consistent_baseline",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("container killed")))
    with pytest.raises(RuntimeError):
        tick(cfg, widened)
    monkeypatch.undo()
    with psycopg.connect(fresh_db, autocommit=True) as c:
        published = {r[0] for r in c.execute(
            "select tablename from pg_publication_tables where pubname = %s",
            (cfg.publication,)).fetchall()}
    assert published == {"a", "b"}, "the pub-add landed before the crash — that is the window"

    sql(fresh_db, "insert into public.b values (3, 'after')")
    tick(cfg, widened)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.b order by id").fetchall() == [
        (1, "pre-existing"), (2, "also"), (3, "after")]
    assert con.execute("select count(*) - count(distinct id) from public.b").fetchone() == (0,)
    con.close()


def test_a_checkpoint_ahead_of_the_slot_is_re_asserted_instead_of_replayed(
        fresh_db, names, duckdb_path, tmp_path):
    """The crash window between "the load committed and the checkpoint was written" and "the slot
    was advanced": the slot still holds work whose rows are already durable at the destination.
    The next tick must push the slot up to the checkpoint rather than re-reading — replaying is
    survivable under merge, but on a busy slot it is unbounded repeated work every tick."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db, "insert into public.t values (2, 'b')")
    ahead = stream.max_lsn(fresh_db, cfg.slot, cfg.publication)
    dest.write_checkpoint(cfg.registration, cfg.slot, ahead)   # committed, never advanced
    assert confirmed_flush(fresh_db, cfg.slot) < ahead

    run, result = tick(cfg, relations)
    assert confirmed_flush(fresh_db, cfg.slot) >= ahead, "the slot was left behind the checkpoint"
    assert result["start_lsn"] == ahead
    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()


# --------------------------------------------------------------------------- review: baselines
# A baseline taken while the table is already published overlaps the WAL: a row committed
# after the publication add and before the snapshot is in BOTH. Merge hides that for a keyed
# table; an append_only row carries no key, so it lands twice. Every non-initial baseline must
# therefore come with a boundary LSN, and the drain must drop what the baseline already saw.


def _insert_between(monkeypatch, dsn, statement):
    """Commit `statement` after the publication add and BEFORE the baseline reads — the
    reviewer's reproduction, made deterministic."""
    real = runner.snapshot.consistent_baseline

    @contextlib.contextmanager
    def race(*a, **kw):
        sql(dsn, statement)
        with real(*a, **kw) as got:
            yield got

    monkeypatch.setattr(runner.snapshot, "consistent_baseline", race)


def _versions(duckdb_path, qualified="public.events"):
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute(f"select id, count(*) from {qualified} group by id order by id").fetchall()
    finally:
        con.close()


def test_an_append_only_table_added_mid_life_lands_a_concurrent_insert_once(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    sql(fresh_db, "create table public.a (id int primary key, v text)",
        "insert into public.a values (1, 'a')")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [rel("public", "a", "id")])

    sql(fresh_db, "create table public.events (id int, note text)",
        "insert into public.events values (1, 'pre')")
    widened = [rel("public", "a", "id"), rel("public", "events", policy="append_only")]
    _insert_between(monkeypatch, fresh_db, "insert into public.events values (2, 'between')")
    tick(cfg, widened)
    sql(fresh_db, "insert into public.events values (3, 'after')")
    tick(cfg, widened)
    assert _versions(duckdb_path) == [(1, 1), (2, 1), (3, 1)]
    assert dest.read_baseline_lsns(cfg.registration)[("public", "events")] > 0


def test_a_resumed_append_only_snapshot_lands_a_concurrent_insert_once(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The interrupted-snapshot path: the slot exists, the publication carries the table, and
    the retaken baseline overlaps everything that entered the WAL since the crash."""
    sql(fresh_db, "create table public.events (id int, note text)",
        "insert into public.events values (1, 'pre')")
    relations = [rel("public", "events", policy="append_only")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    monkeypatch.setattr(runner.snapshot, "snapshot_resources",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("container killed")))
    with pytest.raises(RuntimeError):
        tick(cfg, relations)
    monkeypatch.undo()
    assert dest.read_lifecycle(cfg.registration) == {("public", "events"): "snapshotting"}

    sql(fresh_db, "insert into public.events values (2, 'since the crash')")
    _insert_between(monkeypatch, fresh_db, "insert into public.events values (3, 'between')")
    tick(cfg, relations)
    sql(fresh_db, "insert into public.events values (4, 'after')")
    tick(cfg, relations)
    assert _versions(duckdb_path) == [(1, 1), (2, 1), (3, 1), (4, 1)]


def test_a_baseline_waits_for_a_transaction_in_flight_at_the_snapshot(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """PG's consistent point is reached only once every transaction in flight has finished,
    so a row committed while the baseline is being taken is in the snapshot — and its commit
    LSN is below the boundary, so the drain drops the WAL copy. Once, and the baseline waited."""
    sql(fresh_db, "create table public.a (id int primary key, v text)",
        "insert into public.a values (1, 'a')")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [rel("public", "a", "id")])
    sql(fresh_db, "create table public.events (id int, note text)",
        "insert into public.events values (1, 'pre')")
    widened = [rel("public", "a", "id"), rel("public", "events", policy="append_only")]

    real = runner.snapshot.consistent_baseline
    timing = {}

    @contextlib.contextmanager
    def commit_late(*a, **kw):
        import time as _t
        inflight = psycopg.connect(fresh_db)   # after the publication add and the ALTERs
        inflight.execute("insert into public.events values (2, 'in flight')")

        def later():
            _t.sleep(1.5)
            inflight.commit()
            timing["committed"] = _t.monotonic()
            inflight.close()

        threading.Thread(target=later).start()
        with real(*a, **kw) as got:
            timing["snapshot_ready"] = _t.monotonic()
            yield got

    monkeypatch.setattr(runner.snapshot, "consistent_baseline", commit_late)
    tick(cfg, widened)
    assert timing["snapshot_ready"] >= timing["committed"], "the baseline did not wait"
    tick(cfg, widened)
    assert _versions(duckdb_path) == [(1, 1), (2, 1)]


# --------------------------------------------------------------------------- review: ownership


def _terminate_owner(dsn, owner_conn):
    with psycopg.connect(dsn, autocommit=True) as c:
        for (pid,) in c.execute(
                "select pid from pg_locks where locktype = 'advisory' and granted "
                "and pid <> pg_backend_pid()").fetchall():
            c.execute("select pg_terminate_backend(%s)", (pid,))


def test_losing_the_owner_during_a_load_stops_the_checkpoint_and_the_slot_advance(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The lock is released by Postgres the moment the owner backend dies — from then on a
    peer may own or rebuild the slot. A load that committed in that window must stay
    REPLAYABLE: no checkpoint write, no slot advance, OwnershipLost out of the run."""
    sql(fresh_db, "create table public.t (id int primary key, v text)")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    before = (dest.read_checkpoint(cfg.registration), confirmed_flush(fresh_db, cfg.slot))
    sql(fresh_db, "insert into public.t values (1, 'a')")

    real_load = Destination.load

    def load_then_lose_the_lock(pipeline, items, **kw):
        info = real_load(pipeline, items, **kw)
        _terminate_owner(fresh_db, None)
        return info

    monkeypatch.setattr(Destination, "load", staticmethod(load_then_lose_the_lock))
    with pytest.raises(OwnershipLost):
        tick(cfg, relations)
    assert (dest.read_checkpoint(cfg.registration), confirmed_flush(fresh_db, cfg.slot)) == before
    assert len(table(duckdb_path, "public.t")) == 1, "the committed load stays — and replays"

    monkeypatch.undo()
    tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) - count(distinct id) from public.t").fetchone() == (0,)
    con.close()
    assert dest.read_checkpoint(cfg.registration) != before[0]


# --------------------------------------------------------------------------- review: truncate


def test_a_source_truncate_reaches_the_destination(fresh_db, names, duckdb_path, tmp_path):
    """A keyed mirror empties with its source; an append_only history keeps every arrival and
    records the truncate as an event. Rows inserted after the TRUNCATE in the same drain land
    exactly once, after the delete — batch order is preserved."""
    sql(fresh_db,
        "create table public.keyed (id int primary key, v text)",
        "create table public.events (id int, note text)",
        "insert into public.keyed values (1, 'old'), (2, 'old')",
        "insert into public.events values (1, 'old')")
    relations = [rel("public", "keyed", "id"), rel("public", "events", policy="append_only")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db,
        "insert into public.keyed values (3, 'doomed')",
        "truncate public.keyed, public.events",
        "insert into public.keyed values (4, 'new')",
        "insert into public.events values (2, 'new')")
    run, _ = tick(cfg, relations)

    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.keyed order by id").fetchall() == [(4, "new")]
    assert con.execute("select id from public.events order by id").fetchall() == [(1,), (2,)]
    con.close()
    truncated = {e["entity"] for e in run.units("truncate")}
    assert truncated == {"public.keyed", "public.events"}


def test_an_existing_publication_is_upgraded_to_publish_truncate(fresh_db, names, duckdb_path,
                                                                 tmp_path):
    """The pilot's publication was created with `publish = insert, update, delete`; reconcile
    must ALTER it, not inherit the gap."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        f"create publication {names['publication']} for table public.t "
        "with (publish = 'insert, update, delete')")
    relations = [rel("public", "t", "id")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    with psycopg.connect(fresh_db, autocommit=True) as c:
        assert c.execute("select pubtruncate from pg_publication where pubname = %s",
                         (cfg.publication,)).fetchone() == (True,)


# --------------------------------------------------------------------------- review: identity


def test_load_events_carry_the_schema_qualified_entity(fresh_db, names, duckdb_path, tmp_path):
    """Two schemas, one table name: a bare `person` in control.event cannot say which one
    loaded. Lifecycle and reconcile already say `schema.table`; the load events must too."""
    sql(fresh_db,
        "create schema sales", "create schema hr",
        "create table sales.person (id int primary key, v text)",
        "create table hr.person (id int primary key, v text)",
        "insert into sales.person values (1, 'customer')",
        "insert into hr.person values (1, 'employee')")
    relations = [rel("sales", "person", "id"), rel("hr", "person", "id")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    run1, _ = tick(cfg, relations)
    assert {e["entity"] for e in run1.units("snapshot")} == {"sales.person", "hr.person"}
    sql(fresh_db, "insert into hr.person values (2, 'employee2')")
    run2, _ = tick(cfg, relations)
    assert [e["entity"] for e in run2.units("cdc")] == ["hr.person"]


# --------------------------------------------------------------------------- review: outcome


def test_a_paused_registration_does_not_finish_its_run_ok(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """`control.run` is the first thing the runbook tells an operator to trust. A slot lost
    under `slot_loss_policy: pause` drains nothing — recording that as `ok` with every
    relation counted in n_ok is a green light on a stopped pipe."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path, slot_loss_policy="pause")
    tick(cfg, relations)
    _report_lost_once(monkeypatch)
    wal = str(tmp_path / "wal")
    assert runner.execute(cfg, relations, cp=ControlPlane("test_cdc", dsn=None, wal_dir=wal)) != 0
    rec = finish_record(wal)
    assert rec["status"] == "failed" and rec["stop_reason"] == "error"
    assert (rec["n_ok"], rec["n_err"], rec["n_skipped"]) == (0, 1, 1)


def test_a_drift_blocked_table_makes_the_run_partial_with_honest_counts(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db,
        "create table public.t (id int primary key, alt int not null unique, v text)",
        "create table public.other (id int primary key, v text)",
        "insert into public.t values (1, 100, 'a')", "insert into public.other values (1, 'x')")
    relations = [rel("public", "t", "id"), rel("public", "other", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    wal = str(tmp_path / "wal")
    assert runner.execute(cfg, relations,
                          cp=ControlPlane("test_cdc", dsn=None, wal_dir=wal)) == 0
    assert finish_record(wal)["status"] == "ok"

    sql(fresh_db, "alter table public.t drop constraint t_pkey",
        "alter table public.t add primary key (alt)")
    recompiled = [rel("public", "t", "alt"), rel("public", "other", "id")]  # what `run` compiles
    rc = runner.execute(cfg, recompiled, cp=ControlPlane("test_cdc", dsn=None, wal_dir=wal))
    rec = finish_record(wal)
    assert rec["status"] == "partial" and rc == 0, "partial is a finished run, not a crash"
    assert (rec["n_ok"], rec["n_err"], rec["n_skipped"]) == (1, 1, 1)


def test_a_reconcile_mismatch_makes_the_run_partial(fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    con = duckdb.connect(duckdb_path)
    con.execute("insert into public.t (id, v, _dlt_load_id, _dlt_id) "
                "values (99, 'not at the source', 'hand', 'hand')")
    con.close()
    wal = str(tmp_path / "wal")
    runner.execute(cfg, relations, cp=ControlPlane("test_cdc", dsn=None, wal_dir=wal))
    rec = finish_record(wal)
    assert rec["status"] == "partial"
    assert (rec["n_ok"], rec["n_err"], rec["n_skipped"]) == (0, 1, 0)


def test_an_append_only_relation_reconciles_as_skipped_end_to_end(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.events (id int, note text)",
        "insert into public.events values (1, 'first')")
    relations = [rel("public", "events", policy="append_only")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    sql(fresh_db, "update public.events set note = 'second' where id = 1")
    run, result = tick(cfg, relations)
    (event,) = [e for e in run.units("reconcile") if e["entity"] == "public.events"]
    assert event["status"] == "zero_rows" and event["extra"]["append_only"] is True
    assert result["outcome"]["status"] == "ok"


# --------------------------------------------------------------------------- review: drop_table


def test_drop_table_raises_on_anything_but_a_missing_staging_schema(
        fresh_db, names, duckdb_path, tmp_path):
    """A view in the way stands in for a permission failure or a MotherDuck error: the drop
    must raise, not report the table as gone."""
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    dest.ensure_runtime_tables()
    con = duckdb.connect(duckdb_path)
    con.execute("create schema public")
    con.execute("create table public.real (id int)")
    con.execute("create view public.v as select 1 as id")
    con.close()
    assert dest.drop_table("public", "real") == ["public.real"], "no staging schema: tolerated"
    with pytest.raises(duckdb.Error):
        dest.drop_table("public", "v")


# --------------------------------------------------------------------------- review: partitions


def test_partition_leaves_replicate_and_a_row_routed_after_the_snapshot_lands(
        fresh_db, names, duckdb_path, tmp_path):
    """Leaves only (relkind 'r'): pgoutput emits leaf identity by default, so the leaf tables
    are what both the publication and the destination must name. Two ticks; the second sees
    a row inserted THROUGH the parent."""
    sql(fresh_db,
        "create table public.events (id int, day date, v text, primary key (id, day)) "
        "partition by range (day)",
        "create table public.events_2026 partition of public.events "
        "for values from ('2026-01-01') to ('2027-01-01')",
        "create table public.events_2027 partition of public.events "
        "for values from ('2027-01-01') to ('2028-01-01')",
        "insert into public.events values (1, '2026-05-01', 'a'), (2, '2027-05-01', 'b')")
    relations = [rel("public", "events_2026", "id", "day"), rel("public", "events_2027", "id", "day")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    sql(fresh_db, "insert into public.events values (3, '2026-06-01', 'c')",
        "update public.events set v = 'b2' where id = 2")
    run, _ = tick(cfg, relations)
    assert publication.published_relations(fresh_db, cfg.publication) == {
        ("public", "events_2026"), ("public", "events_2027")}
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.events_2026 order by id").fetchall() == [
        (1, "a"), (3, "c")]
    assert con.execute("select id, v from public.events_2027").fetchall() == [(2, "b2")]
    con.close()


# --------------------------------------------------------------------------- final review: re-baselines
# Every re-baseline lands on top of an EXISTING destination copy (resume, slot-loss re-init,
# a table re-widened after `retired`). Two things have to hold: the copy is REPLACED — a merge
# keeps a row the source deleted, and the boundary skip now drops the very DELETE that used to
# heal it — and dlt's `drop_resources` must actually find the table, which it does by the
# schema's `resource` attribute; a CDC load run under a resource named after the SLOT flips
# that attribute, and from then on `refresh="drop_resources"` drops nothing.


def _interrupt_after_the_snapshot_load(monkeypatch):
    real = runner.snapshot.snapshot_resources

    @contextlib.contextmanager
    def load_then_die(*a, **kw):
        with real(*a, **kw) as by_schema:
            yield by_schema
        raise RuntimeError("container killed after the snapshot load")

    monkeypatch.setattr(runner.snapshot, "snapshot_resources", load_then_die)


def test_a_cdc_load_leaves_the_table_owned_by_a_resource_of_its_own_name(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    sql(fresh_db, "insert into public.t values (2, 'b')")
    tick(cfg, relations)
    pipe = runner._Pipelines(cfg, ["public"]).get("public")
    assert pipe.default_schema.tables["t"]["resource"] == "t"


@pytest.mark.parametrize("path", ["slot_loss", "rewiden"])
def test_an_append_only_table_re_baselined_after_cdc_traffic_is_replaced_not_doubled(
        fresh_db, names, duckdb_path, tmp_path, path):
    sql(fresh_db, "create table public.a (id int primary key)", "insert into public.a values (1)",
        "create table public.events (id int, note text)",
        "insert into public.events values (1, 'a')")
    keyed, events = rel("public", "a", "id"), rel("public", "events", policy="append_only")
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [keyed, events])
    sql(fresh_db, "insert into public.events values (2, 'b')")
    tick(cfg, [keyed, events])                      # CDC traffic touches the table
    if path == "slot_loss":
        sql(fresh_db, f"select pg_drop_replication_slot('{cfg.slot}')")
    else:
        tick(cfg, [keyed])                          # retired ...
    tick(cfg, [keyed, events])                      # ... and re-baselined
    assert _versions(duckdb_path) == [(1, 1), (2, 1)]


@pytest.mark.parametrize("path", ["resume", "slot_loss", "rewiden"])
def test_a_keyed_re_baseline_does_not_resurrect_a_row_deleted_in_between(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch, path):
    sql(fresh_db, "create table public.a (id int primary key)", "insert into public.a values (1)",
        "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a'), (2, 'doomed')")
    keyed_a, t = rel("public", "a", "id"), rel("public", "t", "id")
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    if path == "resume":
        _interrupt_after_the_snapshot_load(monkeypatch)
        with pytest.raises(RuntimeError):
            tick(cfg, [keyed_a, t])
        monkeypatch.undo()
    else:
        tick(cfg, [keyed_a, t])
        if path == "slot_loss":
            sql(fresh_db, f"select pg_drop_replication_slot('{cfg.slot}')")
        else:
            tick(cfg, [keyed_a])                    # retired
    sql(fresh_db, "delete from public.t where id = 2")
    tick(cfg, [keyed_a, t])
    tick(cfg, [keyed_a, t])
    assert [r[:2] for r in table(duckdb_path, "public.t")] == [(1, "a")]


def test_a_rename_swap_then_truncate_in_one_transaction_leaves_only_the_post_truncate_rows(
        fresh_db, names, duckdb_path, tmp_path):
    """#4345 Codex adversarial, end to end: two keyed tables swap names, the new `a` is
    truncated and refilled — one transaction. The destination `a` must hold only the
    post-truncate row, and the tick must checkpoint past the transaction."""
    sql(fresh_db,
        "create table public.a (id int primary key, v text)",
        "create table public.b (id int primary key, v text)",
        "insert into public.a values (1, 'a1')",
        "insert into public.b values (1, 'b1')")
    relations = [rel("public", "a", "id"), rel("public", "b", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    with psycopg.connect(fresh_db) as c:
        c.execute("update public.a set v = 'a1-changed' where id = 1")
        c.execute("alter table public.a rename to tmp")
        c.execute("alter table public.b rename to a")
        c.execute("alter table public.tmp rename to b")
        c.execute("update public.a set v = 'b1-changed' where id = 1")
        c.execute("truncate public.a")
        c.execute("insert into public.a values (2, 'after-truncate')")
        c.commit()
    tick(cfg, relations)

    assert [r[:2] for r in table(duckdb_path, "public.a")] == [(2, "after-truncate")]
    assert dest.read_checkpoint(cfg.registration)[0] == confirmed_flush(fresh_db, names["slot"])


def test_a_crash_with_a_later_round_pending_loses_no_post_truncate_row(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """#4345 Codex adversarial round 3: a keyed batch TRUNCATEs, inserts A, changes the table's
    description, inserts B — two load rounds. Round 1 commits A; the run dies with round 2
    normalized but unloaded. dlt's `run` loads a PENDING package and returns WITHOUT extracting
    the data it was given, so the retry (same pipeline directory) used to re-truncate, skip
    round 1 and lose A for good while the checkpoint advanced."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'old'), (2, 'old')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    with psycopg.connect(fresh_db) as c:
        c.execute("truncate public.t")
        c.execute("insert into public.t values (10, 'A')")
        c.execute("alter table public.t add column w text")
        c.execute("insert into public.t values (11, 'B', 'w')")
        c.commit()

    real_load = Destination.load
    calls = []

    def dying_load(pipeline, items, *, resource_name="cdc"):
        calls.append(1)
        if len(calls) == 2:   # round 2: extracted and normalized, never loaded
            import dlt
            by_table = {}
            for item in items:
                by_table.setdefault(Destination.table_of(item) or resource_name, []).append(item)
            pipeline.extract([dlt.resource(v, name=k) for k, v in by_table.items()])
            pipeline.normalize()
            raise RuntimeError("injected crash after normalize")
        return real_load(pipeline, items, resource_name=resource_name)

    checkpoint = dest.read_checkpoint(cfg.registration)
    import pytest
    monkeypatch.setattr(Destination, "load", staticmethod(dying_load))
    with pytest.raises(RuntimeError, match="injected"):
        tick(cfg, relations)
    monkeypatch.setattr(Destination, "load", staticmethod(real_load))
    assert dest.read_checkpoint(cfg.registration) == checkpoint, "no checkpoint on a crash"

    tick(cfg, relations)
    assert [r[:2] for r in table(duckdb_path, "public.t", "id")] == [(10, "A"), (11, "B")]
    assert dest.read_checkpoint(cfg.registration)[0] == confirmed_flush(fresh_db, names["slot"])


# --------------------------------------------------------------------------- deny.columns (#4346)


import dataclasses  # noqa: E402


def _person_rel(*deny):
    return dataclasses.replace(rel("public", "person", "id"), deny_columns=tuple(deny))


def _columns(duckdb_path, schema, table):
    con = duckdb.connect(duckdb_path)
    try:
        return {r[0] for r in con.execute(
            "select column_name from information_schema.columns "
            "where table_schema = ? and table_name = ?", [schema, table]).fetchall()}
    finally:
        con.close()


def _person_source(dsn):
    sql(dsn, "create table public.person (id int primary key, email text, password_hash text)",
        "insert into public.person values (1, 'a@x', 'h1'), (2, 'b@x', 'h2')")


def test_a_denied_column_never_reaches_the_table_or_its_staging_twin(
        fresh_db, names, duckdb_path, tmp_path):
    _person_source(fresh_db)
    relations = [_person_rel("password_hash")]
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)                                   # baseline
    sql(fresh_db, "update public.person set email = 'a2@x', password_hash = 'h1b' where id = 1",
        "insert into public.person values (3, 'c@x', 'h3')")
    run, result = tick(cfg, relations)                     # CDC merge → creates public_staging
    assert _columns(duckdb_path, "public_staging", "person"), "the merge staged through the twin"
    for schema in ("public", "public_staging"):
        assert "password_hash" not in _columns(duckdb_path, schema, "person")
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a2@x"), (2, "b@x"), (3, "c@x")]
    con.close()
    assert run.units("deny_columns") == [] and result["denied_columns_at_destination"] == 0
    assert result["outcome"]["status"] == "ok"


def test_the_tripwire_fires_on_a_denied_column_already_at_the_destination(
        fresh_db, names, duckdb_path, tmp_path):
    """The prod state today: bronze already holds the column. Adding the deny must turn the
    run red until the purge, naming the column — never a value."""
    _person_source(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [_person_rel()])                             # loaded before the deny existed
    sql(fresh_db, "update public.person set email = 'a2@x' where id = 1")
    tick(cfg, [_person_rel()])                             # and its staging twin too
    run, result = tick(cfg, [_person_rel("password_hash")])
    (event,) = run.units("deny_columns")
    assert event["status"] == "error" and event["entity"] == "public.person"
    assert event["extra"]["columns"] == ["public.person.password_hash",
                                         "public_staging.person.password_hash"]
    assert "h1" not in repr(event) and "h2" not in repr(event)
    assert "resnapshot" in event["extra"]["remedy"]
    assert result["denied_columns_at_destination"] == 2
    assert result["outcome"]["status"] == "partial" and result["outcome"]["n_err"] == 1


def test_the_tripwire_watches_a_frozen_copy_outside_the_selection(
        fresh_db, names, duckdb_path, tmp_path):
    """`featherbase.user` → frozen `access.user`: no longer selected, still holding the hash."""
    _person_source(fresh_db)
    sql(fresh_db, "create table public.other (id int primary key)", "insert into public.other values (1)")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [_person_rel()])
    cfg2, _ = make(fresh_db, names, duckdb_path, tmp_path,
                   deny_columns={("public", "person"): ("password_hash",)})
    run, result = tick(cfg2, [rel("public", "other", "id")])   # person retired, copy frozen
    (event,) = run.units("deny_columns")
    assert event["entity"] == "public.person"
    assert "frozen copy" in event["extra"]["remedy"]
    assert result["outcome"]["status"] == "partial"


DENY_MANIFEST = """\
api_version: pg-md-cdc/v1
source_id: demo
source:
  dsn_env: DEMO_PG_DSN
  include:
    schemas: [public]
{deny}
destination:
  token_env: DEMO_MD_TOKEN
  owner: demo_cdc_service_account
  database: bronze_demo
replication:
  slot: {slot}
  publication: {publication}
  replica_identity: full
control:
  extractor: demo_cdc
  wal_dir: {wal_dir}
"""


def test_a_resnapshot_removes_a_column_that_has_just_become_denied(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The purge path for `person` (memo §3.3): does dlt's stored schema bring a column back
    after the table is rebuilt without it? Proved through the operator's own entry points."""
    from feather_cdc_postgres import cli, manifest as mf

    path = tmp_path / "demo.yml"

    def write(deny: str) -> str:
        path.write_text(DENY_MANIFEST.format(
            deny=deny, slot=names["slot"], publication=names["publication"],
            wal_dir=str(tmp_path / "wal")), encoding="utf-8")
        return str(path)

    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)

    _person_source(fresh_db)
    manifest = write("")
    assert cli.main(["run", manifest, "--dev"]) == 0
    sql(fresh_db, "update public.person set email = 'a2@x' where id = 1")
    assert cli.main(["run", manifest, "--dev"]) == 0
    for schema in ("public", "public_staging"):
        assert "password_hash" in _columns(duckdb_path, schema, "person"), "the leak, reproduced"

    manifest = write('  deny:\n    columns:\n      "public.person": [password_hash]')
    assert cli.main(["run", manifest, "--dev"]) == 0      # tripwire red: run is partial, not failed
    assert "password_hash" in _columns(duckdb_path, "public", "person"), "a run never drops"

    assert runner.resnapshot(mf.load(manifest), tables=["public.person"], yes=True, dev=True) == 0
    for schema in ("public", "public_staging"):
        assert "password_hash" not in _columns(duckdb_path, schema, "person")

    # The next CDC merges: dlt must not re-add the column from its stored schema.
    sql(fresh_db, "update public.person set email = 'a3@x', password_hash = 'h1c' where id = 1",
        "insert into public.person values (4, 'd@x', 'h4')")
    assert cli.main(["run", manifest, "--dev"]) == 0
    sql(fresh_db, "delete from public.person where id = 2")
    assert cli.main(["run", manifest, "--dev"]) == 0
    for schema in ("public", "public_staging"):
        assert "password_hash" not in _columns(duckdb_path, schema, "person")
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a3@x"), (4, "d@x")]
    con.close()

    # And the tripwire is green again, checked directly against the same destination.
    m = mf.load(manifest)
    cfg = runner.config_from_manifest(m, dsn=fresh_db, destination=runner._destination(m))
    run = FakeRun()
    assert runner._check_denied_columns(cfg, [], run=run, targets=runner._Targets(cfg, [])) == 0
    assert run.events == []


def test_a_table_renamed_between_runs_keeps_its_deny_through_the_stored_oid(
        fresh_db, names, duckdb_path, tmp_path):
    """(b), end to end: `person` is renamed at the source BETWEEN two runs, to a name with no
    deny entry. The old name no longer resolves; the OID in its stored signature still does."""
    _person_source(fresh_db)
    deny = {("public", "person"): ("password_hash",)}
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path, deny_columns=deny)
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person rename to member",
        "update public.member set email = 'a2@x', password_hash = 'h1b' where id = 1",
        "insert into public.member values (3, 'c@x', 'h3')")
    tick(cfg, [])  # `person` left the selection; its WAL (now `member`) still drains
    con = duckdb.connect(duckdb_path)
    leaked = con.execute("select table_schema, table_name from information_schema.columns "
                         "where column_name = 'password_hash'").fetchall()
    con.close()
    assert leaked == []


def test_a_renamed_table_still_selected_under_its_new_name_is_baselined_without_the_column(
        fresh_db, names, duckdb_path, tmp_path):
    """Codex (#4346, round 2): the new name IS selected, has no destination copy, so it gets a
    fresh baseline — which must inherit the deny through the OID, and the tripwire must watch it."""
    _person_source(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person rename to member",
        "insert into public.member values (3, 'c@x', 'h3')")
    run, result = tick(cfg, [rel("public", "member", "id")])
    con = duckdb.connect(duckdb_path)
    leaked = con.execute("select table_schema, table_name from information_schema.columns "
                         "where column_name = 'password_hash'").fetchall()
    emails = con.execute("select id, email from public.member order by id").fetchall()
    con.close()
    assert leaked == []
    assert emails == [(1, "a@x"), (2, "b@x"), (3, "c@x")]
    assert run.units("deny_columns") == []


def _leaks(duckdb_path):
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute("select table_schema, table_name from information_schema.columns "
                           "where column_name = 'password_hash' order by all").fetchall()
    finally:
        con.close()


def test_a_new_table_reusing_a_denied_name_does_not_erase_the_renamed_tables_deny(
        fresh_db, names, duckdb_path, tmp_path):
    """Codex round 3 (#4346): person (OID A) -> member, then a NEW person (OID B). Both are
    selected. The deny must hold on member across ticks even after person's signature and
    catalogue OID move to B — the OID association is persisted, never re-derived from a name."""
    _person_source(fresh_db)
    deny = {("public", "person"): ("password_hash",)}
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path, deny_columns=deny)
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person rename to member",
        "create table public.person (id int primary key, email text, password_hash text)",
        "insert into public.person values (10, 'n@x', 'hn')")
    both = [_person_rel("password_hash"), rel("public", "member", "id")]
    tick(cfg, both)
    sql(fresh_db, "update public.member set password_hash = 'h1b', email = 'a2@x' where id = 1",
        "insert into public.member values (5, 'e@x', 'h5')")
    tick(cfg, both)
    tick(cfg, both)
    assert _leaks(duckdb_path) == []
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.member order by id").fetchall() == [
        (1, "a2@x"), (2, "b@x"), (5, "e@x")]
    con.close()
    assert len({r[0] for r in dest.read_denied_oids(cfg.registration)}) == 2


def _pending_leak(pipelines_dir, duckdb_path, pipeline_name):
    """A crashed run's leftover: a package extracted AND normalized with the hash, never loaded."""
    import dlt
    dest = Destination(DestinationSpec(kind="duckdb", database="test", path=duckdb_path,
                                       pipelines_dir=pipelines_dir))
    pipe = dlt.pipeline(pipeline_name=pipeline_name, destination=dest._dlt_destination(),
                        dataset_name="public", pipelines_dir=pipelines_dir)
    pipe.extract(dlt.resource([{"id": 99, "email": "z@x", "password_hash": "PENDINGLEAK"}],
                              name="person", write_disposition="merge", primary_key="id"))
    pipe.normalize()
    assert pipe.has_pending_data


def _leaked_value_rows(duckdb_path):
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute("select count(*) from public.person "
                           "where password_hash = 'PENDINGLEAK'").fetchone()[0]
    except duckdb.BinderException:
        return 0   # the column is gone altogether
    finally:
        con.close()


def test_a_pending_package_from_before_the_deny_is_discarded_not_loaded(
        fresh_db, names, duckdb_path, tmp_path):
    """Codex round 3 (#4346): `finish_pending` would load a stale package unchanged."""
    _person_source(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [_person_rel()])                               # before the deny
    _pending_leak(str(tmp_path / "dlt"), duckdb_path, "test_cdc__public")
    sql(fresh_db, "update public.person set email = 'a2@x' where id = 1")
    cfg2, _ = make(fresh_db, names, duckdb_path, tmp_path,
                   deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg2, [_person_rel("password_hash")])
    assert _leaked_value_rows(duckdb_path) == 0
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a2@x"), (2, "b@x")]
    con.close()


def test_a_resnapshot_discards_a_stale_pending_package_and_still_purges(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """dlt's `run` loads a pending package and returns WITHOUT extracting what it was handed —
    a resnapshot would load the stale hash and skip the safe baseline."""
    from feather_cdc_postgres import cli, manifest as mf
    path = tmp_path / "demo.yml"
    path.write_text(DENY_MANIFEST.format(
        deny='  deny:\n    columns:\n      "public.person": [password_hash]',
        slot=names["slot"], publication=names["publication"], wal_dir=str(tmp_path / "wal")),
        encoding="utf-8")
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)
    _person_source(fresh_db)
    assert cli.main(["run", str(path), "--dev"]) == 0
    _pending_leak(str(tmp_path / "dlt"), duckdb_path, "demo_cdc__public")
    assert runner.resnapshot(mf.load(str(path)), tables=["public.person"], yes=True, dev=True) == 0
    assert _leaks(duckdb_path) == []
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a@x"), (2, "b@x")]
    con.close()


@pytest.mark.parametrize("table_name,column_name", [
    ("person", "password$hash"),          # dlt lands the column as password_hash
    ("person$archive", "password_hash"),  # dlt lands the table as person_archive
])
def test_a_pending_package_is_discarded_under_dlt_normalised_names(
        fresh_db, names, duckdb_path, tmp_path, table_name, column_name):
    """Codex round 4 (#4346): the guard must compare dlt's normalised spellings, not the raw
    source identifiers, or a stale package with the denied value is loaded."""
    import dlt
    from dlt.common.normalizers.naming.snake_case import NamingConvention
    norm = NamingConvention().normalize_identifier
    qt, qc = f'"{table_name}"', f'"{column_name}"'
    sql(fresh_db, f"create table public.{qt} (id int primary key, email text, {qc} text)",
        f"insert into public.{qt} values (1, 'a@x', 'h1')")
    relation = rel("public", table_name, "id")
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [relation])                                     # before the deny
    pipelines_dir = str(tmp_path / "dlt")
    dest = Destination(DestinationSpec(kind="duckdb", database="test", path=duckdb_path,
                                       pipelines_dir=pipelines_dir))
    pipe = dlt.pipeline(pipeline_name="test_cdc__public", destination=dest._dlt_destination(),
                        dataset_name="public", pipelines_dir=pipelines_dir)
    pipe.extract(dlt.resource([{"id": 99, "email": "z@x", column_name: "PENDINGLEAK"}],
                              name=table_name, write_disposition="merge", primary_key="id"))
    pipe.normalize()
    assert pipe.has_pending_data
    sql(fresh_db, f"update public.{qt} set email = 'a2@x' where id = 1")
    cfg2, _ = make(fresh_db, names, duckdb_path, tmp_path,
                   deny_columns={("public", table_name): (column_name,)})
    run, _ = tick(cfg2, [dataclasses.replace(relation, deny_columns=(column_name,))])
    con = duckdb.connect(duckdb_path)
    landed = con.execute(f"select count(*) from public.{norm(table_name)} where id = 99"
                         ).fetchone()[0]
    con.close()
    assert landed == 0, "the stale package was loaded"
    (event,) = run.units("deny_columns")   # the pre-deny copy is still reported, under dlt's name
    assert event["extra"]["columns"][0] == f"public.{norm(table_name)}.{norm(column_name)}"


def test_an_oid_missed_by_the_startup_lookup_is_bound_by_the_baseline_and_the_drain(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 5 (#4346): a table replaced AFTER the startup OID lookup is read under its
    denied name by the baseline (and the decoder) — that OID must be persisted there, or a
    rename before the next run releases the column."""
    _person_source(fresh_db)
    deny = {("public", "person"): ("password_hash",)}
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path, deny_columns=deny)
    real = runner._denied_oids
    monkeypatch.setattr(runner, "_denied_oids", lambda cfg, denied, **_kw: {})   # the lookup "missed"
    tick(cfg, [_person_rel("password_hash")])                            # baseline binds it
    with psycopg.connect(fresh_db) as c:
        oid = c.execute("select 'public.person'::regclass::oid").fetchone()[0]
    assert {r[0] for r in dest.read_denied_oids(cfg.registration)} == {int(oid)}
    monkeypatch.setattr(runner, "_denied_oids", real)
    sql(fresh_db, "alter table public.person rename to member",
        "insert into public.member values (3, 'c@x', 'h3')")
    tick(cfg, [rel("public", "member", "id")])
    assert _leaks(duckdb_path) == []


def test_an_oid_bound_at_no_trusted_moment_is_quarantined_by_the_decoder(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 16 (#4346), superseding round 10's decoder binding: an OID met under a
    denied name that neither the run start nor a baseline bound (a table renamed onto the name
    mid-run) has no known denied column. The decoder quarantines it and binds NOTHING from a
    name — a name met in WAL proves no identity."""
    _person_source(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    monkeypatch.setattr(runner, "_denied_oids", lambda cfg, denied, **_kw: {})
    monkeypatch.setattr(runner, "_persist_bound", lambda cfg, bound: None)   # baseline missed it
    tick(cfg, [_person_rel("password_hash")])
    assert dest.read_denied_oids(cfg.registration) == []
    sql(fresh_db, "update public.person set email = 'a2@x' where id = 1")
    run, result = tick(cfg, [_person_rel("password_hash")])          # the decoder meets it
    assert dest.read_denied_oids(cfg.registration) == []
    assert ("public", "person") in dest.quarantined_names(cfg.registration)
    assert [e for e in run.units("deny_columns")
            if (e.get("extra") or {}).get("action") == "quarantined"]
    con = duckdb.connect(duckdb_path)
    assert con.execute("select email from public.person where id = 1").fetchone() == ("a@x",)
    con.close()
    assert _leaks(duckdb_path) == []


def test_a_renamed_table_whose_key_is_an_inherited_denied_column_is_refused(
        fresh_db, names, duckdb_path, tmp_path):
    """Codex round 5 (#4346): the compiler refuses an explicit deny on a key column; one
    inherited through a rename must be refused at run time too — withholding a key breaks
    every merge — and nothing is replicated."""
    sql(fresh_db, "create table public.person (id int, email text, password_hash text primary key)",
        "insert into public.person values (1, 'a@x', 'h1')")
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("email",)})
    tick(cfg, [dataclasses.replace(rel("public", "person", "password_hash"),
                                   deny_columns=("email",))])
    sql(fresh_db, "alter table public.person rename to member")
    member = rel("public", "member", "email")      # a key that is now an inherited deny
    sql(fresh_db, "alter table public.member drop constraint person_pkey",
        "alter table public.member add primary key (email)")
    run, result = tick(cfg, [member])
    assert result["paused"] is True and result["outcome"]["status"] == "failed"
    (event,) = run.units("deny_columns")
    assert "public.member: email" in event["error_detail"]
    assert table(duckdb_path, "public.member") == []


def test_a_rename_between_the_baseline_and_the_drain_of_one_run_cannot_leak(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 6 (#4346): the OID the baseline binds must reach THIS run's decoder, not
    only the next run's — rename and write between the baseline and the drain of one tick."""
    _person_source(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    monkeypatch.setattr(runner, "_denied_oids", lambda cfg, denied, **_kw: {})   # startup lookup missed
    real_drain = runner._drain

    def rename_then_drain(*a, **kw):
        sql(fresh_db, "alter table public.person rename to member",
            "insert into public.member values (3, 'c@x', 'h3')",
            "update public.member set password_hash = 'h1b', email = 'a2@x' where id = 1")
        return real_drain(*a, **kw)

    monkeypatch.setattr(runner, "_drain", rename_then_drain)
    # The tick's LAST step, the row-count reconcile, counts the selected `public.person`, which
    # this test renamed mid-run; that pre-existing probe raising is not under test here.
    with pytest.raises(psycopg.errors.UndefinedTable):
        tick(cfg, [_person_rel("password_hash")])
    assert _leaks(duckdb_path) == []
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.member order by id").fetchall() == [
        (1, "a2@x"), (3, "c@x")]
    con.close()


def test_a_name_swap_after_the_run_resolves_its_denies_cannot_leak_through_the_baseline(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 7 (#4346): `person` and `member` swap names after the run resolved its deny
    map but before the baseline snapshot opens. The baseline must project by the OID it finds
    INSIDE the snapshot, not only by the name map resolved earlier."""
    _person_source(fresh_db)
    sql(fresh_db, "create table public.member (id int primary key, email text, password_hash text)",
        "insert into public.member values (7, 'm@x', NULL)")
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    real = runner._with_inherited_denies
    swapped = []

    def resolve_then_swap(cfg, relations):
        out = real(cfg, relations)
        if not swapped:
            sql(fresh_db, "alter table public.person rename to tmp_swap",
                "alter table public.member rename to person",
                "alter table public.tmp_swap rename to member")
            swapped.append(True)
        return out

    monkeypatch.setattr(runner, "_with_inherited_denies", resolve_then_swap)
    tick(cfg, [_person_rel("password_hash"), rel("public", "member", "id")])
    con = duckdb.connect(duckdb_path)
    member_cols = {r[0] for r in con.execute(
        "select column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = 'member'").fetchall()}
    member_ids = con.execute("select id from public.member order by id").fetchall()
    con.close()
    assert member_ids == [(1,), (2,)], "member now holds the protected rows"
    assert "password_hash" not in member_cols


def test_two_denied_relations_routed_to_one_destination_table_union_their_denies(
        fresh_db, names, duckdb_path, tmp_path):
    """Codex round 7 (#4346): `old.person` (retired) and `public.person` both land in
    `access.person`; neither deny-list may replace the other in the tripwire."""
    _person_source(fresh_db)
    sql(fresh_db, "alter table public.person add column reset_token text")
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  table_schemas=[("*.person", "access")])
    tick(cfg, [dataclasses.replace(_person_rel(), target_schema="access")])   # before any deny
    cfg2, _ = make(fresh_db, names, duckdb_path, tmp_path,
                   table_schemas=[("*.person", "access")],
                   deny_columns={("old", "person"): ("password_hash",),
                                 ("public", "person"): ("reset_token",)})
    run, _ = tick(cfg2, [dataclasses.replace(_person_rel("reset_token"), target_schema="access")])
    reported = {c for e in run.units("deny_columns") for c in e["extra"]["columns"]}
    assert {"access.person.password_hash", "access.person.reset_token"} <= reported


def test_an_interrupted_baseline_still_persists_the_oid_it_bound(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 8 (#4346): the OID a baseline binds is persisted BEFORE its load, so a load
    that dies (then a rename before the retry) cannot leave that OID without its deny."""
    _person_source(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    real_oids = runner._denied_oids
    monkeypatch.setattr(runner, "_denied_oids", lambda cfg, denied, **_kw: {})   # startup missed it
    real_pending = runner._pending_safe

    def die(*a, **kw):
        raise RuntimeError("killed before the load")

    monkeypatch.setattr(runner, "_pending_safe", die)
    with pytest.raises(RuntimeError, match="killed"):
        tick(cfg, [_person_rel("password_hash")])
    with psycopg.connect(fresh_db) as c:
        oid = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    assert {r[0] for r in dest.read_denied_oids(cfg.registration)} == {oid}
    monkeypatch.setattr(runner, "_pending_safe", real_pending)
    monkeypatch.setattr(runner, "_denied_oids", real_oids)
    sql(fresh_db, "alter table public.person rename to member")
    tick(cfg, [rel("public", "member", "id")])
    assert _leaks(duckdb_path) == []


def test_a_failed_cdc_load_still_persists_the_quarantine_the_decoder_raised(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 10, restated for round 16 (#4346): what the decoder learns is persisted
    before any destination change — now the quarantine of an unbound OID, with that OID — so a
    load that fails (then a rename before the retry) cannot lose it."""
    _person_source(fresh_db)
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    relations = [_person_rel("password_hash"), rel("public", "t", "id")]
    monkeypatch.setattr(runner, "_denied_oids", lambda cfg, denied, **_kw: {})
    monkeypatch.setattr(runner, "_persist_bound", lambda cfg, bound: None)
    tick(cfg, relations)                                      # nothing bound yet
    # `t`'s row keeps the batch loading, so the failing load below is really attempted.
    sql(fresh_db, "update public.person set email = 'a2@x' where id = 1",
        "insert into public.t values (2, 'b')")

    def failing_load(*a, **kw):
        raise RuntimeError("destination down")

    monkeypatch.setattr(Destination, "load", staticmethod(failing_load))
    with pytest.raises(RuntimeError, match="destination down"):
        tick(cfg, relations)
    with psycopg.connect(fresh_db) as c:
        oid = int(c.execute("select 'public.person'::regclass::oid").fetchone()[0])
    assert dest.read_quarantined_oids(cfg.registration) == {oid}, "recorded before the load"
    assert dest.read_denied_oids(cfg.registration) == []


# ------------------------------------------------- a renamed denied COLUMN (Sonnet review F1)


def _secret_columns(duckdb_path, *names):
    names = names or ("password_hash", "pw")
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute(
            "select table_schema, table_name, column_name from information_schema.columns "
            "where column_name in (select unnest(?::varchar[])) order by all",
            [list(names)]).fetchall()
    finally:
        con.close()


def _hash_rows(duckdb_path):
    """Rows anywhere whose text carries the source's hash literals (count only)."""
    con = duckdb.connect(duckdb_path)
    try:
        n = 0
        for schema, tbl in con.execute(
                "select table_schema, table_name from information_schema.tables "
                "where table_schema not like '\\_%' escape '\\'").fetchall():
            cols = [c for (c,) in con.execute(
                "select column_name from information_schema.columns where table_schema = ? "
                "and table_name = ? and data_type = 'VARCHAR'", [schema, tbl]).fetchall()]
            for c in cols:
                n += con.execute(f'select count(*) from "{schema}"."{tbl}" '
                                 f"where \"{c}\" like 'H\\_%' escape '\\'").fetchone()[0]
        return n
    finally:
        con.close()


def _person_hashed(dsn):
    sql(dsn, "create table public.person (id int primary key, email text, password_hash text)",
        "insert into public.person values (1, 'a@x', 'H_1'), (2, 'b@x', 'H_2')")


def test_a_denied_column_renamed_between_runs_quarantines_until_a_resnapshot(
        fresh_db, names, duckdb_path, tmp_path):
    """Codex round 16 (#4346): any rename on a table that carries a deny breaks the layout
    proof, so its changes are quarantined (stale, never leaked) until a resnapshot. The deny
    still follows the attnum, so drift does not call it a `drop_column`, and the resnapshot
    rebaselines without `pw`."""
    _person_hashed(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person rename column password_hash to pw",
        "insert into public.person values (3, 'c@x', 'H_3')",
        "update public.person set pw = 'H_1b', email = 'a2@x' where id = 1")
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a@x"), (2, "b@x")], "quarantined: stale, not loaded"
    con.close()
    assert "drop_column" not in {d["kind"] for e in run.units("schema_drift")
                                 for d in e["extra"]["drifts"]}
    assert ("public", "person") in dest.quarantined_names(cfg.registration)
    assert result["outcome"]["status"] == "partial"


def test_a_denied_column_renamed_mid_run_is_quarantined_by_the_decoder(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The rename lands after the run resolved its denies: the decoder meets `pw` where the
    reviewed layout says `password_hash` — a rename, so the table is quarantined (Codex round
    16), never loaded."""
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    real_drain = runner._drain

    def rename_then_drain(*a, **kw):
        sql(fresh_db, "alter table public.person rename column password_hash to pw",
            "insert into public.person values (3, 'c@x', 'H_3')")
        return real_drain(*a, **kw)

    monkeypatch.setattr(runner, "_drain", rename_then_drain)
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0
    assert [e for e in run.units("deny_columns")
            if (e.get("extra") or {}).get("action") == "quarantined"]
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id from public.person order by id").fetchall() == [(1,), (2,)]
    con.close()


def test_a_denied_column_renamed_before_a_baseline_is_withheld_by_its_attnum(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Baseline path: the column is renamed AFTER the run bound its attnum and BEFORE the
    snapshot — the baseline resolves the attnum inside its own snapshot."""
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    real = runner._with_inherited_denies
    done = []

    def resolve_then_rename(cfg, relations):
        out = real(cfg, relations)
        if not done:
            sql(fresh_db, "alter table public.person rename column password_hash to pw")
            done.append(True)
        return out

    monkeypatch.setattr(runner, "_with_inherited_denies", resolve_then_rename)
    _, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, [_person_rel("password_hash")])
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0
    # Codex round 12: the signature this run records must not approve `pw` either...
    stored = dest.read_signatures(cfg.registration)[("public", "person")]
    assert "pw" not in stored["columns"]
    # ...so later writes to it stay out too.
    sql(fresh_db, "insert into public.person values (3, 'c@x', 'H_3')")
    tick(cfg, [_person_rel("password_hash")])
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0


def test_a_resnapshot_after_a_column_rename_keeps_it_out(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    from feather_cdc_postgres import cli, manifest as mf
    path = tmp_path / "demo.yml"
    path.write_text(DENY_MANIFEST.format(
        deny='  deny:\n    columns:\n      "public.person": [password_hash]',
        slot=names["slot"], publication=names["publication"], wal_dir=str(tmp_path / "wal")),
        encoding="utf-8")
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)
    _person_hashed(fresh_db)
    assert cli.main(["run", str(path), "--dev"]) == 0
    sql(fresh_db, "alter table public.person rename column password_hash to pw")
    assert runner.resnapshot(mf.load(str(path)), tables=["public.person"], yes=True, dev=True) == 0
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0


def test_a_denied_column_dropped_and_re_added_under_the_same_name_is_still_denied(
        fresh_db, names, duckdb_path, tmp_path):
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person drop column password_hash",
        "alter table public.person add column password_hash text",
        "insert into public.person values (3, 'c@x', 'H_3')",
        "update public.person set password_hash = 'H_1c' where id = 1")
    tick(cfg, [_person_rel("password_hash")])
    with psycopg.connect(fresh_db) as c:   # the re-added column is a NEW attnum
        assert c.execute("select attnum from pg_attribute where attrelid = 'public.person'::regclass "
                         "and attname = 'password_hash'").fetchone()[0] == 4
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0


def _deny_manifest(tmp_path, names, fresh_db, duckdb_path, monkeypatch):
    from feather_cdc_postgres import manifest as mf
    path = tmp_path / "demo.yml"
    path.write_text(DENY_MANIFEST.format(
        deny='  deny:\n    columns:\n      "public.person": [password_hash]',
        slot=names["slot"], publication=names["publication"], wal_dir=str(tmp_path / "wal")),
        encoding="utf-8")
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)
    return path, mf.load(str(path))


def test_any_added_column_on_a_denied_table_quarantines_until_a_resnapshot(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 17 (#4346): an added column changes the description, so the table carrying a
    deny is quarantined — not withheld column by column — and stays so, even once the column is
    denied too, until a resnapshot re-reviews it."""
    from feather_cdc_postgres import cli
    path, m = _deny_manifest(tmp_path, names, fresh_db, duckdb_path, monkeypatch)
    _person_hashed(fresh_db)
    assert cli.main(["run", str(path), "--dev"]) == 0
    sql(fresh_db, "alter table public.person add column recovery_code text",
        "insert into public.person values (3, 'c@x', 'H_3', 'rc_3')")
    assert cli.main(["run", str(path), "--dev"]) == 0
    dest = runner._destination(m)
    (oid,) = dest.read_quarantined_oids("demo")
    assert "differs from the reviewed layout" in dest.read_quarantine("demo")[oid][2]
    assert _emails(duckdb_path) == [(1, "a@x"), (2, "b@x")], "nothing after the DDL loads"
    assert _secret_columns(duckdb_path, "recovery_code") == [] and _hash_rows(duckdb_path) == 0
    sql(fresh_db, "update public.person set recovery_code = 'rc_1' where id = 1")
    assert cli.main(["run", str(path), "--dev"]) == 0
    assert dest.read_quarantined_oids("demo") == {oid}, "sticky until a resnapshot"
    assert _secret_columns(duckdb_path, "recovery_code") == [] and _hash_rows(duckdb_path) == 0
    # The resnapshot records the new layout and clears the mark; the column is reviewed now.
    assert runner.resnapshot(m, tables=["public.person"], yes=True, dev=True) == 0
    assert dest.read_quarantine("demo") == {}
    assert _emails(duckdb_path) == [(1, "a@x"), (2, "b@x"), (3, "c@x")]
    assert _hash_rows(duckdb_path) == 0 and _secret_columns(duckdb_path) == []
    sql(fresh_db, "insert into public.person values (4, 'd@x', 'H_4', null)")
    assert cli.main(["run", str(path), "--dev"]) == 0
    assert dest.read_quarantine("demo") == {}
    assert (4, "d@x") in _emails(duckdb_path) and _hash_rows(duckdb_path) == 0


def test_r17_a_second_table_taking_a_quarantined_name_is_quarantined_too(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 17 finding 2: OID A is quarantined as `person`; mid-run it is renamed away
    and OID B takes the name and writes a row (its deny was bound at no trusted moment). B gets
    its own mark — marks were de-duplicated by NAME, so B had none and resumed on the next run
    without a resnapshot."""
    _person_hashed(fresh_db)
    sql(fresh_db, "create table public.member (id int primary key, email text, password_hash text)",
        "insert into public.member values (7, 'm@x', 'pm7')")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    rels = [_person_rel("password_hash"), rel("public", "member", "id")]
    tick(cfg, rels)
    sql(fresh_db, "alter table public.person rename column email to mail",
        "insert into public.person values (3, 'c@x', 'H_3')")
    tick(cfg, rels)
    (a,) = dest.read_quarantined_oids(cfg.registration)
    real_drain = runner._drain

    def take_the_name_then_drain(*args, **kw):
        sql(fresh_db, "alter table public.person rename to person_old",
            "alter table public.member rename to person",
            "insert into public.person values (8, 'n@x', 'pm8')")
        return real_drain(*args, **kw)

    monkeypatch.setattr(runner, "_drain", take_the_name_then_drain)
    with contextlib.suppress(psycopg.errors.UndefinedTable):   # end-of-run counts `member`
        tick(cfg, rels)
    b = int(sql_one(fresh_db, "select 'public.person'::regclass::oid"))
    assert a != b
    assert dest.read_quarantined_oids(cfg.registration) == {a, b}
    assert {o: v[:2] for o, v in dest.read_quarantine(cfg.registration).items()} == {
        a: ("public", "person"), b: ("public", "person")}
    assert _hash_rows(duckdb_path) == 0


def test_r17_a_mark_of_a_dropped_table_is_cleared_when_its_name_is_rebuilt(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """A quarantined table dropped at the source can emit nothing more; when the name it fed is
    rebuilt by a resnapshot, its mark goes too (clearing by OID alone would keep it for ever)."""
    from feather_cdc_postgres import cli
    path, m = _deny_manifest(tmp_path, names, fresh_db, duckdb_path, monkeypatch)
    _person_hashed(fresh_db)
    assert cli.main(["run", str(path), "--dev"]) == 0
    sql(fresh_db, "alter table public.person rename column email to mail",
        "insert into public.person values (3, 'c@x', 'H_3')")
    assert cli.main(["run", str(path), "--dev"]) == 0
    dest = runner._destination(m)
    assert len(dest.read_quarantined_oids("demo")) == 1
    sql(fresh_db, "drop table public.person")
    _person_hashed(fresh_db)
    assert runner.resnapshot(m, tables=["public.person"], yes=True, dev=True) == 0
    assert dest.read_quarantine("demo") == {}
    assert _hash_rows(duckdb_path) == 0


def test_r17_a_quarantined_table_renamed_and_resnapshotted_is_cleared_by_its_oid(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 17 finding 3: clearing was by the CURRENT name, so a quarantined table
    renamed and rebuilt under its new name kept its OID quarantined, and its mirror stale."""
    from feather_cdc_postgres import cli
    path, m = _deny_manifest(tmp_path, names, fresh_db, duckdb_path, monkeypatch)
    _person_hashed(fresh_db)
    assert cli.main(["run", str(path), "--dev"]) == 0
    sql(fresh_db, "alter table public.person rename column email to mail",
        "insert into public.person values (3, 'c@x', 'H_3')")
    assert cli.main(["run", str(path), "--dev"]) == 0
    dest = runner._destination(m)
    (a,) = dest.read_quarantined_oids("demo")
    sql(fresh_db, "alter table public.person rename to account")
    assert cli.main(["run", str(path), "--dev"]) == 0
    assert runner.resnapshot(m, tables=["public.account"], yes=True, dev=True) == 0
    assert a not in dest.read_quarantined_oids("demo")
    assert _hash_rows(duckdb_path) == 0


def test_a_table_and_column_renamed_mid_run_stay_withheld(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 12: rename `person` -> `member` AND `password_hash` -> `pw` after the run
    resolved its denies; the reviewed shape and the deny both follow the OID and the attnum."""
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    real_drain = runner._drain

    def rename_then_drain(*a, **kw):
        sql(fresh_db, "alter table public.person rename to member",
            "alter table public.member rename column password_hash to pw",
            "insert into public.member values (3, 'c@x', 'H_3')")
        return real_drain(*a, **kw)

    monkeypatch.setattr(runner, "_drain", rename_then_drain)
    with pytest.raises(psycopg.errors.UndefinedTable):   # the end-of-run reconcile counts `person`
        tick(cfg, [_person_rel("password_hash")])
    assert _secret_columns(duckdb_path) == [] and _hash_rows(duckdb_path) == 0


def test_a_denied_column_renamed_onto_a_reviewed_name_mid_run_stays_withheld(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 12: drop `email`, then rename `password_hash` to `email` — a name the reviewed
    shape approves. Identity is the attnum, so the new `email` is still the denied column."""
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    real_drain = runner._drain

    def swap_then_drain(*a, **kw):
        sql(fresh_db, "alter table public.person drop column email",
            "alter table public.person rename column password_hash to email",
            "insert into public.person values (3, 'H_3')",
            "update public.person set email = 'H_1b' where id = 1")
        return real_drain(*a, **kw)

    monkeypatch.setattr(runner, "_drain", swap_then_drain)
    tick(cfg, [_person_rel("password_hash")])
    assert _hash_rows(duckdb_path) == 0
    assert _secret_columns(duckdb_path) == []


def test_a_column_name_swap_and_restore_before_the_drain_never_loads_the_hash(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 13 (#4346), end to end: the run-start layout is the reviewed order; a
    queued description that does not line up with it is quarantined and reported red."""
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person rename column email to tmp_swap",
        "alter table public.person rename column password_hash to email",
        "alter table public.person rename column tmp_swap to password_hash",
        "insert into public.person (id, email, password_hash) values (3, 'H_3', 'c@x')",
        "alter table public.person rename column password_hash to tmp_swap",
        "alter table public.person rename column email to password_hash",
        "alter table public.person rename column tmp_swap to email")
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert _hash_rows(duckdb_path) == 0 and _secret_columns(duckdb_path) == []
    assert [e for e in run.units("deny_columns")
            if (e.get("extra") or {}).get("action") == "quarantined"]
    assert result["outcome"]["status"] == "partial"


def test_a_quarantine_is_reported_every_tick_until_a_resnapshot_clears_it(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 14 (#4346): quarantined changes are skipped and the checkpoint moves on, so
    the relation is marked persistently and reported on every later tick (the alarm stays red)
    until a resnapshot rebuilds it."""
    from feather_cdc_postgres import cli, manifest as mf
    path = tmp_path / "demo.yml"
    path.write_text(DENY_MANIFEST.format(
        deny='  deny:\n    columns:\n      "public.person": [password_hash]',
        slot=names["slot"], publication=names["publication"], wal_dir=str(tmp_path / "wal")),
        encoding="utf-8")
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)
    _person_hashed(fresh_db)
    m = mf.load(str(path))
    assert cli.main(["run", str(path), "--dev"]) == 0
    sql(fresh_db, "alter table public.person rename column email to tmp_swap",
        "alter table public.person rename column password_hash to email",
        "alter table public.person rename column tmp_swap to password_hash",
        "insert into public.person (id, email, password_hash) values (3, 'H_3', 'c@x')",
        "alter table public.person rename column password_hash to tmp_swap",
        "alter table public.person rename column email to password_hash",
        "alter table public.person rename column tmp_swap to email")
    assert cli.main(["run", str(path), "--dev"]) == 0
    dest = runner._destination(m)
    assert ("public", "person") in dest.quarantined_names("demo")
    # A later tick still reports it, and skips the table's changes even though their
    # description (names restored) would prove: the quarantine persists by OID (Codex round 16).
    sql(fresh_db, "insert into public.person values (4, 'd@x', 'H_4')")
    cfg = runner.config_from_manifest(m, dsn=fresh_db, destination=dest)
    rel_ = dataclasses.replace(_person_rel("password_hash"), target_schema="public")
    run, result = tick(cfg, [rel_])
    assert [e for e in run.units("deny_columns")
            if (e.get("extra") or {}).get("action") == "quarantine_pending"]
    assert result["outcome"]["status"] == "partial"
    assert (4, "d@x") not in _emails(duckdb_path)
    # The resnapshot rebuilds the copy and clears the mark.
    assert runner.resnapshot(m, tables=["public.person"], yes=True, dev=True) == 0
    assert dest.read_quarantine("demo") == {}
    assert _hash_rows(duckdb_path) == 0 and _secret_columns(duckdb_path) == []
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, email from public.person order by id").fetchall() == [
        (1, "a@x"), (2, "b@x"), (3, "c@x"), (4, "d@x")]
    con.close()


# ------------------------------------- Codex round 16: DDL timed against a run (#4346)


def sql_one(dsn, query):
    with psycopg.connect(dsn) as c:
        return c.execute(query).fetchone()[0]


def _emails(duckdb_path):
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute("select id, email from public.person order by id").fetchall()
    finally:
        con.close()


def test_r16_a_table_renamed_onto_a_denied_name_mid_run_with_a_key_swap_never_loads_the_hash(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """P1, the reviewed sequence: `member` takes `person`'s name mid-run, its text merge key and
    `password_hash` swap names, a row is written (by name, the KEY now holds the hash), and the
    names swap back — all before the drain. `member`'s OID was bound at no trusted moment, so
    it is quarantined; its reviewed layout alone would also refuse the swapped description."""
    _person_hashed(fresh_db)
    sql(fresh_db, "create table public.member (code text primary key, email text, "
                  "password_hash text)",
        "insert into public.member values ('m1', 'm@x', 'pm1')")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    rels = [_person_rel("password_hash"), rel("public", "member", "code")]
    tick(cfg, rels)
    real_drain = runner._drain

    def swap_then_drain(*a, **kw):
        sql(fresh_db, "alter table public.person rename to person_old",
            "alter table public.member rename to person",
            "alter table public.person rename column code to tmp_swap",
            "alter table public.person rename column password_hash to code",
            "alter table public.person rename column tmp_swap to password_hash",
            "insert into public.person (code, email, password_hash) values ('H_KEY', 'x@x', 'k2')",
            "alter table public.person rename column password_hash to tmp_swap",
            "alter table public.person rename column code to password_hash",
            "alter table public.person rename column tmp_swap to code")
        return real_drain(*a, **kw)

    monkeypatch.setattr(runner, "_drain", swap_then_drain)
    with pytest.raises(psycopg.errors.UndefinedTable):   # the end-of-run reconcile counts `member`
        tick(cfg, rels)
    assert _hash_rows(duckdb_path) == 0
    member = int(sql_one(fresh_db, "select 'public.person'::regclass::oid"))
    assert member in dest.read_quarantined_oids(cfg.registration)


def _drop_rename_readd(dsn):
    """HIGH 1: drop `email`, rename `password_hash` onto it, write, drop that, add a new `email`.
    Afterwards both today's catalogue and the queued description read [id, email]."""
    sql(dsn, "alter table public.person drop column email",
        "alter table public.person rename column password_hash to email",
        "insert into public.person values (3, 'H_3')",
        "alter table public.person drop column email",
        "alter table public.person add column email text")


def test_r16_drop_rename_write_drop_readd_is_quarantined_against_the_reviewed_layout(
        fresh_db, names, duckdb_path, tmp_path):
    """HIGH 1: the reviewed layout (the baseline's) has attnum 3; it has gone, so no
    description can be proven — quarantined, and it stays so (later rows do not load either)
    until a resnapshot re-reviews the table."""
    _person_hashed(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    _drop_rename_readd(fresh_db)
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert _hash_rows(duckdb_path) == 0
    assert ("public", "person") in dest.quarantined_names(cfg.registration)
    assert result["outcome"]["status"] == "partial"
    sql(fresh_db, "insert into public.person values (4, 'd@x')")
    tick(cfg, [_person_rel("password_hash")])
    assert _emails(duckdb_path) == [(1, "a@x"), (2, "b@x")], "sticky: nothing more loads"
    assert _hash_rows(duckdb_path) == 0


def test_r16_drop_rename_write_drop_readd_with_no_reviewed_layout_is_quarantined(
        fresh_db, names, duckdb_path, tmp_path):
    """HIGH 1, legacy: a table baselined before reviewed layouts existed. Without one, only a
    table that never dropped a column can be proven; this one has, so it is quarantined."""
    _person_hashed(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    con = duckdb.connect(duckdb_path)
    con.execute('delete from "_cdc_runtime".reviewed_layout')
    con.close()
    _drop_rename_readd(fresh_db)
    tick(cfg, [_person_rel("password_hash")])
    assert _hash_rows(duckdb_path) == 0
    assert ("public", "person") in dest.quarantined_names(cfg.registration)


def test_r16_a_denied_column_that_binds_to_no_attnum_refuses_the_run(
        fresh_db, names, duckdb_path, tmp_path):
    """HIGH 2: the deny is new and `password_hash` was renamed before anything bound it (the
    race between the OID lookup and the attnum query is gone — both are one statement — but the
    outcome it produced is still possible). Its attnum is unknown, so the run refuses: nothing
    is replicated, `pw` least of all."""
    _person_hashed(fresh_db)
    sql(fresh_db, "alter table public.person rename column password_hash to pw")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert result["paused"] is True and result["outcome"]["status"] == "failed"
    (event,) = run.units("deny_columns")
    assert event["extra"]["unbound"] == ["public.person.password_hash"]
    assert _hash_rows(duckdb_path) == 0 and _secret_columns(duckdb_path) == []
    assert dest.read_denied_oids(cfg.registration) == []


def test_r16_a_dropped_bound_column_quarantines_instead_of_refusing(
        fresh_db, names, duckdb_path, tmp_path):
    """The denied column dropped at the source (once bound): its identity is known and its
    value is gone, so the run goes on (no registration-wide refusal); the table is quarantined
    until a resnapshot re-reviews it."""
    _person_hashed(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person drop column password_hash",
        "insert into public.person values (3, 'c@x')")
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert not result.get("paused")
    assert ("public", "person") in dest.quarantined_names(cfg.registration)
    assert _hash_rows(duckdb_path) == 0


def test_r16_a_baseline_of_an_oid_whose_denied_name_binds_nowhere_refuses(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """HIGH 2, baseline path: after the run bound `person`, it is replaced by a table whose
    secret column is not called `password_hash`. The baseline meets an OID with no binding and
    no column of that name under its locks, and refuses rather than read the table."""
    _person_hashed(fresh_db)
    cfg, _ = make(fresh_db, names, duckdb_path, tmp_path,
                  deny_columns={("public", "person"): ("password_hash",)})
    real = runner._with_inherited_denies
    done = []

    def resolve_then_replace(cfg, relations):
        out = real(cfg, relations)
        if not done:
            sql(fresh_db, "alter table public.person rename to person_old",
                "create table public.person (id int primary key, email text, pw text)",
                "insert into public.person values (9, 'z@x', 'H_9')")
            done.append(True)
        return out

    monkeypatch.setattr(runner, "_with_inherited_denies", resolve_then_replace)
    with pytest.raises(ValueError, match="never bound"):
        tick(cfg, [_person_rel("password_hash")])
    assert _hash_rows(duckdb_path) == 0


def test_r16_a_table_that_dropped_a_column_before_its_baseline_streams_on_its_reviewed_layout(
        fresh_db, names, duckdb_path, tmp_path):
    """Liveness: a long-ago drop leaves `attisdropped` rows for good, so without a layout the
    table could never be proven. The baseline's reviewed layout is what lets it stream."""
    _person_hashed(fresh_db)
    sql(fresh_db, "alter table public.person add column legacy text",
        "alter table public.person drop column legacy")
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "insert into public.person values (3, 'c@x', 'H_3')")
    run, result = tick(cfg, [_person_rel("password_hash")])
    assert _emails(duckdb_path) == [(1, "a@x"), (2, "b@x"), (3, "c@x")]
    assert dest.read_quarantine(cfg.registration) == {}
    assert _hash_rows(duckdb_path) == 0 and result["outcome"]["status"] == "ok"


def test_r16_a_truncate_of_a_quarantined_table_is_not_applied(
        fresh_db, names, duckdb_path, tmp_path):
    """Nothing of a quarantined table applies until its resnapshot — a TRUNCATE included."""
    _person_hashed(fresh_db)
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path,
                     deny_columns={("public", "person"): ("password_hash",)})
    tick(cfg, [_person_rel("password_hash")])
    sql(fresh_db, "alter table public.person rename column email to mail",
        "insert into public.person values (3, 'c@x', 'H_3')",
        "truncate public.person")
    tick(cfg, [_person_rel("password_hash")])
    assert ("public", "person") in dest.quarantined_names(cfg.registration)
    assert _emails(duckdb_path) == [(1, "a@x"), (2, "b@x")], "stale, not emptied"


def test_r16_wal_older_than_a_resnapshot_does_not_re_quarantine_it(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """A quarantine is raised by a CHANGE past the relation's baseline, never by a description
    alone: WAL queued before a resnapshot still carries the pre-rename description, and its rows
    are below the new boundary — so the rebuilt table streams, unflagged."""
    from feather_cdc_postgres import cli, manifest as mf
    path = tmp_path / "demo.yml"
    path.write_text(DENY_MANIFEST.format(
        deny='  deny:\n    columns:\n      "public.person": [password_hash]',
        slot=names["slot"], publication=names["publication"], wal_dir=str(tmp_path / "wal")),
        encoding="utf-8")
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)
    _person_hashed(fresh_db)
    m = mf.load(str(path))
    assert cli.main(["run", str(path), "--dev"]) == 0
    sql(fresh_db, "insert into public.person values (3, 'c@x', 'H_3')",   # old description
        "alter table public.person rename column email to mail")
    assert runner.resnapshot(m, tables=["public.person"], yes=True, dev=True) == 0
    sql(fresh_db, "insert into public.person values (4, 'd@x', 'H_4')")
    assert cli.main(["run", str(path), "--dev"]) == 0
    dest = runner._destination(m)
    assert dest.read_quarantine("demo") == {}
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, mail from public.person order by id").fetchall() == [
        (1, "a@x"), (2, "b@x"), (3, "c@x"), (4, "d@x")]
    con.close()
    assert _hash_rows(duckdb_path) == 0
