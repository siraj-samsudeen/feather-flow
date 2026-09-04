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

from control_plane import ControlPlane
from pg_md_cdc import publication, runner, stream, telemetry
from pg_md_cdc.destination import Destination, DestinationSpec
from pg_md_cdc.lifecycle import RETIRED, STREAMING
from pg_md_cdc.ownership import OwnershipLost, OwnerSession
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


def finish_record(wal_dir, extractor="test_cdc"):
    """The `run_finish` record a WAL-only ControlPlane wrote — what control.run would hold."""
    import json
    import pathlib
    records = []
    for path in sorted(pathlib.Path(wal_dir, extractor).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("kind") == "run_finish":
                records.append(rec)
    return records[-1]


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
