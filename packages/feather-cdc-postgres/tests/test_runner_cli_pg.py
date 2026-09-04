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
