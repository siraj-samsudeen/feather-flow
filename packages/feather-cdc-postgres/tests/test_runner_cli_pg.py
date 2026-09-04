"""`run` / `status` / `resnapshot` driven through the CLI against Local PG + duckdb (#3079 D1/D2).

These are the only tests that exercise the manifest→runtime binding the way an operator does:
argv in, exit code out, no hand-built RuntimeConfig. `PG_MD_CDC_DESTINATION=duckdb:<path>` is
what makes that possible without MotherDuck credentials.
"""
from __future__ import annotations

import pathlib

import duckdb
import psycopg
import pytest

from pg_md_cdc import cli, manifest as mf, runner

MANIFEST = """\
api_version: pg-md-cdc/v1
source_id: demo
source:
  dsn_env: DEMO_PG_DSN
  include:
    schemas: [public]
destination:
  token_env: DEMO_MD_TOKEN
  database: bronze_demo
replication:
  slot: {slot}
  publication: {publication}
  replica_identity: full
control:
  extractor: demo_cdc
  wal_dir: {wal_dir}
"""


@pytest.fixture
def registration(tmp_path, names, fresh_db, duckdb_path, monkeypatch):
    """A manifest on disk plus the env a `--dev` run needs, and nothing else."""
    path = tmp_path / "demo.yml"
    path.write_text(MANIFEST.format(slot=names["slot"], publication=names["publication"],
                                    wal_dir=str(tmp_path / "wal")), encoding="utf-8")
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("DEMO_MD_TOKEN", raising=False)
    return str(path)


def sql(dsn, *statements):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in statements:
            c.execute(s)


def test_run_then_status_both_exit_zero_without_a_control_plane_dsn(
        fresh_db, registration, duckdb_path, capsys):
    """`status` reads the SOURCE and the DESTINATION only — needing CONTROL_PG_DSN to ask
    "what state is this registration in" would make the answer unavailable exactly when the
    control plane is the thing that is down."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a'), (2, 'b')")

    assert cli.main(["run", registration, "--dev"]) == 0
    capsys.readouterr()

    assert cli.main(["status", registration]) == 0
    out = capsys.readouterr().out
    assert "checkpoint: " in out and "checkpoint: none" not in out
    assert "public.t" in out and "streaming" in out
    assert "wal_status" in out and "reserved" in out

    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) from public.t").fetchone() == (2,)
    con.close()


def test_a_second_run_drains_the_new_rows_through_the_same_cli_path(fresh_db, registration,
                                                                    duckdb_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    assert cli.main(["run", registration, "--dev"]) == 0
    sql(fresh_db, "insert into public.t values (2, 'b')",
        "update public.t set v = 'a2' where id = 1")
    assert cli.main(["run", registration, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.t order by id").fetchall() == [
        (1, "a2"), (2, "b")]
    con.close()


def test_a_malformed_destination_override_fails_loudly(registration, monkeypatch):
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", "motherduck:bronze_demo")
    with pytest.raises(SystemExit) as exc:
        runner._destination(mf.load(registration))
    assert "duckdb:<file path>" in str(exc.value)


def test_no_override_leaves_motherduck_as_the_destination(registration, monkeypatch):
    monkeypatch.delenv("PG_MD_CDC_DESTINATION")
    spec = runner._destination(mf.load(registration)).spec
    assert spec.kind == "motherduck" and spec.database == "bronze_demo"


# --------------------------------------------------------------------------- e · resnapshot


def test_resnapshot_after_a_key_change_rebuilds_the_table_and_restores_streaming(
        fresh_db, registration, duckdb_path):
    """The full D-3 remedy loop, end to end: key change → publication removal + blocked runs →
    `resnapshot --tables` → the table is back in the publication, streaming, and keyed on the
    NEW column with no duplicates."""
    sql(fresh_db,
        "create table public.t (id int primary key, alt int not null unique, v text)",
        "create table public.other (id int primary key, v text)",
        "insert into public.t values (1, 100, 'a'), (2, 200, 'b')",
        "insert into public.other values (1, 'x')")
    assert cli.main(["run", registration, "--dev"]) == 0

    sql(fresh_db, "alter table public.t drop constraint t_pkey",
        "alter table public.t add primary key (alt)",
        "insert into public.t values (3, 300, 'c')",
        "insert into public.other values (2, 'y')")
    assert cli.main(["run", registration, "--dev"]) == 0

    m = mf.load(registration)
    dest = runner._destination(m)
    assert dest.read_lifecycle("demo")[("public", "t")] == "resnapshot_required"
    with psycopg.connect(fresh_db, autocommit=True) as c:
        published = {r[1] for r in c.execute(
            "select schemaname, tablename from pg_publication_tables where pubname = %s",
            (m.replication.publication,)).fetchall()}
    assert published == {"other"}, "only the key-changed table leaves the publication"

    assert runner.resnapshot(m, tables=["public.t"], yes=True, dev=True) == 0

    assert dest.read_lifecycle("demo")[("public", "t")] == "streaming"
    with psycopg.connect(fresh_db, autocommit=True) as c:
        published = {r[0] for r in c.execute(
            "select tablename from pg_publication_tables where pubname = %s",
            (m.replication.publication,)).fetchall()}
        (src,) = c.execute("select count(*) from public.t").fetchone()
    assert published == {"t", "other"}
    con = duckdb.connect(duckdb_path)
    assert con.execute("select count(*) from public.t").fetchone() == (src,)
    assert con.execute("select count(*) - count(distinct alt) from public.t").fetchone() == (0,)
    con.close()

    # and the block is really gone: the next run neither re-reports nor re-removes it
    assert cli.main(["run", registration, "--dev"]) == 0
    sql(fresh_db, "insert into public.t values (4, 400, 'd')")
    assert cli.main(["run", registration, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    assert con.execute("select v from public.t where alt = 400").fetchone() == ("d",)
    con.close()


def test_resnapshot_still_refuses_without_yes(registration):
    with pytest.raises(SystemExit) as exc:
        cli.main(["resnapshot", registration, "--tables", "public.t", "--dev"])
    assert "--yes" in str(exc.value)
    assert pathlib.Path(registration).exists()


# --------------------------------------------------------------------------- review: gates


def _slot_and_publication(dsn, m):
    with psycopg.connect(dsn, autocommit=True) as c:
        slot = c.execute("select 1 from pg_replication_slots where slot_name = %s",
                         (m.replication.slot,)).fetchone()
        pub = {r[0] for r in c.execute(
            "select tablename from pg_publication_tables where pubname = %s",
            (m.replication.publication,)).fetchall()} if c.execute(
            "select 1 from pg_publication where pubname = %s",
            (m.replication.publication,)).fetchone() else None
    return slot is not None, pub


@pytest.mark.parametrize("command", [["run", "--dev"], ["status"], ["resnapshot", "--yes", "--dev"]])
def test_a_manifest_that_fails_static_validation_never_touches_anything(
        fresh_db, registration, duckdb_path, capsys, command):
    """`validate` rejecting `api_version: v999` means nothing if `run` executes it anyway. The
    mutating entry points must refuse BEFORE discovery, the publication, the slot and the
    destination — and say why on stderr."""
    sql(fresh_db, "create table public.t (id int primary key, v text)")
    text = pathlib.Path(registration).read_text(encoding="utf-8")
    pathlib.Path(registration).write_text(
        text.replace("api_version: pg-md-cdc/v1", "api_version: pg-md-cdc/v999"), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main([command[0], registration, *command[1:]])
    assert exc.value.code not in (0, None)
    assert "api_version" in capsys.readouterr().err
    m = mf.load(registration)
    assert _slot_and_publication(fresh_db, m) == (False, None)
    assert not pathlib.Path(duckdb_path).exists()


def test_an_empty_compiled_selection_fails_closed_and_records_the_run(
        fresh_db, registration, duckdb_path, tmp_path):
    """A declaration that now compiles to nothing is either a mistake or a decommission. Doing
    nothing and exiting 0 left the publication and the slot alive with no consumer — WAL
    retained until the fuse — and wrote no control.run row to say so."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    assert cli.main(["run", registration, "--dev"]) == 0
    text = pathlib.Path(registration).read_text(encoding="utf-8")
    pathlib.Path(registration).write_text(
        text.replace("schemas: [public]", "schemas: [nowhere]"), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", registration, "--dev"])
    assert exc.value.code not in (0, None)
    assert "decommission" in str(exc.value)
    m = mf.load(registration)
    assert _slot_and_publication(fresh_db, m) == (True, {"t"}), "nothing was touched"

    import json
    records = []
    for path in sorted((tmp_path / "wal" / "demo_cdc").glob("*.jsonl")):
        records += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    finishes = [r for r in records if r.get("kind") == "run_finish"]
    assert finishes[-1]["status"] == "failed" and finishes[-1]["n_err"] == 1
    assert any(r.get("kind") == "event" and r.get("entity") == "_selection"
               and r.get("status") == "error" for r in records)


def test_a_failed_destination_drop_aborts_resnapshot_before_it_touches_the_source(
        fresh_db, registration, duckdb_path, monkeypatch):
    """The drop is the first destructive act of a resnapshot. If it fails, the table must stay
    `resnapshot_required`, untouched at the destination, still published, with no snapshot
    taken — a merge into the undropped table would resurrect rows the source deleted."""
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a'), (2, 'gone later')")
    assert cli.main(["run", registration, "--dev"]) == 0
    sql(fresh_db, "delete from public.t where id = 2")
    m = mf.load(registration)
    dest = runner._destination(m)
    dest.write_lifecycle("demo", "public", "t", "resnapshot_required", "hand-set")
    signature_before = dest.read_signatures("demo")[("public", "t")]

    from pg_md_cdc.destination import Destination

    def refused(self, schema, table):
        raise RuntimeError("permission denied for table t")

    monkeypatch.setattr(Destination, "drop_table", refused)
    with pytest.raises(RuntimeError):
        runner.resnapshot(m, tables=["public.t"], yes=True, dev=True)

    assert dest.read_lifecycle("demo")[("public", "t")] == "resnapshot_required"
    assert dest.read_signatures("demo")[("public", "t")] == signature_before
    assert _slot_and_publication(fresh_db, m)[1] == {"t"}, "the publication was not touched"
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id from public.t order by id").fetchall() == [(1,), (2,)], \
        "untouched: the stale row is still there, visibly, rather than half-resnapshotted"
    con.close()


def test_resnapshot_of_one_blocked_table_leaves_the_other_blocked_table_out_of_the_publication(
        fresh_db, registration, duckdb_path):
    """Two key-changed tables, one resnapshot: re-admitting the OTHER one to the publication
    for even one tick streams its changes under the new key into the old-keyed table — the
    exact double-load the block exists to prevent."""
    sql(fresh_db,
        "create table public.t (id int primary key, alt int not null unique, v text)",
        "create table public.u (id int primary key, alt int not null unique, v text)",
        "insert into public.t values (1, 100, 'a')", "insert into public.u values (1, 100, 'a')")
    assert cli.main(["run", registration, "--dev"]) == 0
    for name in ("t", "u"):
        sql(fresh_db, f"alter table public.{name} drop constraint {name}_pkey",
            f"alter table public.{name} add primary key (alt)")
    assert cli.main(["run", registration, "--dev"]) == 0
    m = mf.load(registration)
    assert runner._destination(m).read_lifecycle("demo")[("public", "u")] == "resnapshot_required"
    assert runner.resnapshot(m, tables=["public.t"], yes=True, dev=True) == 0
    with psycopg.connect(fresh_db, autocommit=True) as c:
        published = {r[0] for r in c.execute(
            "select tablename from pg_publication_tables where pubname = %s",
            (m.replication.publication,)).fetchall()}
    assert published == {"t"}, "u is still blocked and must stay out until ITS resnapshot"


def test_an_unreachable_source_still_leaves_a_failed_run_row(registration, tmp_path, monkeypatch):
    """Discovery runs before the control-plane run opens, so a source outage used to exit 1
    with NO control.run row — which the runbook's first trust step reads as "the cron never
    fired". An outage must be a `failed` row, like every other way a tick can die."""
    monkeypatch.setenv("DEMO_PG_DSN", "postgresql://cdc@127.0.0.1:1/nowhere?connect_timeout=1")
    assert cli.main(["run", registration, "--dev"]) != 0
    from test_e2e_pg import finish_record
    rec = finish_record(str(tmp_path / "wal"), extractor="demo_cdc")
    assert rec["status"] == "failed" and rec["stop_reason"] == "error"
