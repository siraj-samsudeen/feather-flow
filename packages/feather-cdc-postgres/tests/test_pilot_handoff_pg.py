"""The Featherbase cutover proof (#3079 Phase 4, #2851 pilot → this runtime).

This is the test that says it is safe to point the Railway service at `pg_md_cdc`: the pilot
(`featherbase_extract`'s dlt calls, verbatim — `init_replication(persist_snapshots=True)` +
`replication_resource`, pipeline `featherbase_cdc`, dataset `public`) runs first and builds the
destination, then `pg_md_cdc` takes over the SAME slot, publication and destination file and
must converge without re-snapshotting, without duplicating, and without splitting dlt's state
into a second schema name. Local PG + duckdb stand in for featherbase-db-prod + MotherDuck;
that is the same load path minus credentials.
"""
from __future__ import annotations

import dataclasses
import pathlib

import dlt
import duckdb
import psycopg
import pytest
from dlt.sources.credentials import ConnectionStringCredentials

from control_plane import ControlPlane
from pg_md_cdc import manifest as mf, runner
from pg_md_cdc.destination import Destination, DestinationSpec
from pg_replication import replication_resource
from pg_replication.helpers import init_replication

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "featherbase_public_schema.sql"
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "registrations" / "featherbase.yml"


def q(dsn, sql, *args):
    with psycopg.connect(dsn, autocommit=True) as c:
        cur = c.execute(sql, args or None)   # () would make psycopg parse '%' as a placeholder
        return cur.fetchall() if cur.description else None


def seed(dsn, n, tag):
    with psycopg.connect(dsn, autocommit=True) as c:
        for i in range(n):
            c.execute("insert into public.role (row_id, status, created_at, updated_at) "
                      "values (%s, %s, now(), now())", (f"{tag}-{i}", f"role {tag} {i}"))
            c.execute("insert into public.comment (row_id, content, created_at, updated_at, "
                      "ref_table, ref_name) values (%s, %s, now(), now(), %s, %s)",
                      (f"{tag}-{i}", "x" * 2000, "role", f"{tag}-{i}"))


def pilot_tables(dsn) -> list[str]:
    """featherbase_extract's own selection: the catalog's declared relations plus the catalog."""
    declared = sorted(r[0] for r in q(
        dsn, "select replace(lower(name), ' ', '_') from table_def "
             "where kind in ('table', 'sub_table') and data_source is null"))
    physical = {r[0] for r in q(dsn, "select tablename from pg_tables where schemaname = 'public'")}
    return sorted(set(declared + ["table_def", "column_def"]) & physical)


@pytest.fixture
def featherbase_db(fresh_db):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute(FIXTURE.read_text(encoding="utf-8"))
    return fresh_db


def test_the_pilot_hands_its_slot_and_destination_to_the_runtime_without_re_snapshotting(
        featherbase_db, names, duckdb_path, tmp_path, monkeypatch):
    dsn = featherbase_db
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
    monkeypatch.setenv("FEATHERBASE_PG_DSN", dsn)
    slot, pub = names["slot"], names["publication"]
    tables = pilot_tables(dsn)
    assert len(tables) > 40, "the fixture must carry the real catalog, not a stub"
    seed(dsn, 5, "a")

    # --- the pilot, verbatim: snapshot into `featherbase_cdc` / dataset `public` ---
    creds = ConnectionStringCredentials(dsn)
    pipe = dlt.pipeline(pipeline_name="featherbase_cdc",
                        destination=dlt.destinations.duckdb(credentials=duckdb_path),
                        dataset_name="public")
    pipe.run(init_replication(slot_name=slot, pub_name=pub, schema_name="public",
                              table_names=tables, credentials=creds, persist_snapshots=True))
    with psycopg.connect(dsn, autocommit=True) as c:
        for (t,) in c.execute(r"select tablename from pg_tables where schemaname = 'public' "
                              r"and tablename like '\_dlt\_%'").fetchall():
            c.execute(f'drop table "public"."{t}"')     # the pilot's own staging cleanup (#2874)
        for t in tables:
            c.execute(f'alter table "public"."{t}" replica identity full')

    seed(dsn, 3, "b")
    q(dsn, "update public.comment set ref_name = 'changed' where row_id = 'a-0'")
    q(dsn, "delete from public.role where row_id = 'a-1'")
    pipe.run(replication_resource(slot_name=slot, pub_name=pub, credentials=creds))

    # --- changes the pilot never drained; the runtime must pick them up from the same slot ---
    seed(dsn, 4, "c")
    q(dsn, "update public.comment set ref_name = 'changed-again' where row_id = 'b-0'")
    q(dsn, "delete from public.role where row_id = 'b-1'")
    src_role = q(dsn, "select count(*) from public.role")[0][0]
    src_comment = q(dsn, "select count(*) from public.comment")[0][0]

    # --- the takeover, built from the shipped registration overridden back to pilot
    # continuity. The shipped file is a CLEAN-RUN registration since D-13 (05-Sep-2026): no
    # `pipeline_name` pin and four target schemas. This test is the ROLLBACK proof (runbook
    # §h) — that the runtime can adopt the pilot's slot, publication and `featherbase_cdc`
    # state when told to — so it restores the pin and the single `public` dataset here.
    m = mf.load(str(MANIFEST))
    m = dataclasses.replace(
        m,
        source=dataclasses.replace(
            m.source,
            discovery=dataclasses.replace(
                m.source.discovery,
                options={**m.source.discovery.options, "schema": "public"},
            ),
        ),
        replication=dataclasses.replace(m.replication, slot=slot, publication=pub),
        destination=dataclasses.replace(m.destination, pipeline_name="featherbase_cdc",
                                        schema_mapping="preserve_source", table_schemas=[]))
    _, plan = runner._compile(m)
    assert [p for p in plan.problems if p.severity == "error"] == []
    # the two-way rule narrows the pilot's 50 published tables to the 12 the registration
    # selects; the other 38 retire (frozen), they are not re-baselined
    assert len(plan.relations) == 12
    assert {r.target_schema for r in plan.relations} == {"public"}

    destination = Destination(DestinationSpec(kind="duckdb", database="bronze_featherbase",
                                              path=duckdb_path,
                                              pipelines_dir=str(tmp_path / "dlt2")))
    cfg = runner.config_from_manifest(m, dsn=dsn, destination=destination)
    cp = ControlPlane(m.control.extractor, dsn=None, wal_dir=str(tmp_path / "wal"))
    assert runner.execute(cfg, plan.relations, cp=cp) == 0

    con = duckdb.connect(duckdb_path)
    try:
        assert con.execute("select count(*) from public.role").fetchone() == (src_role,)
        assert con.execute("select count(*) from public.comment").fetchone() == (src_comment,)
        assert con.execute("select count(*) - count(distinct row_id) from public.role"
                           ).fetchone() == (0,)
        assert con.execute("select count(*) - count(distinct row_id) from public.comment"
                           ).fetchone() == (0,)
        assert con.execute("select ref_name from public.comment where row_id = 'b-0'"
                           ).fetchone() == ("changed-again",)
        # the pin held: a second schema name would mean every table re-snapshots on cutover
        assert con.execute("select distinct schema_name from public._dlt_version"
                           ).fetchall() == [("featherbase_cdc",)]
        assert con.execute('select count(*) from "_cdc_runtime".checkpoint').fetchone() == (1,)
        assert con.execute('select count(*) from "_cdc_runtime".lifecycle where state '
                           "= 'streaming'").fetchone() == (len(plan.relations),)
        assert con.execute('select count(*) from "_cdc_runtime".lifecycle where state '
                           "= 'retired'").fetchone() == (len(tables) - len(plan.relations),)
    finally:
        con.close()

    assert q(dsn, "select wal_status from pg_replication_slots where slot_name = %s",
             slot)[0][0] == "reserved"
    assert q(dsn, "select count(*) from pg_publication_tables where pubname = %s",
             pub)[0][0] == len(plan.relations), "the 38 unselected tables left the publication"
    assert q(dsn, r"select count(*) from pg_tables where tablename like '\_dlt\_%'"
             )[0][0] == 0, "the runtime must leave no staging table on the source"
