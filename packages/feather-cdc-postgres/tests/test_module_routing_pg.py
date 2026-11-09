"""Ownerless Featherbase Tables land in a bronze schema named after their module (#4588): read from
table_def on every run, so a Table made later lands correctly with no edit, and one moved to another
module is baselined in its new schema.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import duckdb
import psycopg
import pytest

from feather_cdc_postgres import cli, runner

MANIFEST = """\
api_version: pg-md-cdc/v1
source_id: fb_demo
source:
  dsn_env: FB_DEMO_PG_DSN
  include:
    schemas: ["*"]
  discovery:
    adapter: featherbase
    options:
      schema: featherbase
      modules: null
      system_tables: [table_def]
destination:
  token_env: FB_DEMO_MD_TOKEN
  owner: demo_cdc_service_account
  database: bronze_fb_demo
  schema_mapping: preserve_source
  table_schemas:
    - {{match: "featherbase.table_def", schema: catalog}}
    - {{match: "featherbase.*", schema: application, from_module: true}}
  retirement_policy: freeze
replication:
  slot: {slot}
  publication: {publication}
  replica_identity: full
control:
  extractor: fb_demo_cdc
  wal_dir: {wal_dir}
"""

SCHEMA = [
    "create schema featherbase",
    'create table featherbase.table_def (name text primary key, module text, kind text, '
    'data_source text, description text, "system" boolean not null, owner_app text, '
    "physical_schema text, physical_relation text)",
    "create table featherbase.site_master (row_id text primary key, site_name text)",
    "create table featherbase.section (row_id text primary key, label text)",
    "create table featherbase.loose (row_id text primary key)",
    "create table featherbase.empty_one (row_id text primary key)",
    "insert into featherbase.table_def (name, module, kind, \"system\") values "
    "('Table', 'Core', 'table', true), ('Site Master', 'Organisation', 'table', false), "
    "('Section', 'Store Layout', 'table', false), ('Loose', null, 'table', false), "
    "('Empty One', 'People', 'table', false)",
    "insert into featherbase.site_master values ('ATK', 'Attakulangara')",
    "insert into featherbase.section values ('s1', 'Tops'), ('s2', 'Frock')",
    "insert into featherbase.loose values ('x')",
]


def _sql(dsn, *statements):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in statements:
            c.execute(s)


def _rows(path, sql):
    con = duckdb.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    monkeypatch.setenv("FB_DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("FB_DEMO_MD_TOKEN", raising=False)
    path = tmp_path / "fb_demo.yml"
    path.write_text(MANIFEST.format(slot=names["slot"], publication=names["publication"],
                                    wal_dir=str(tmp_path / "wal")), encoding="utf-8")
    return str(path)


def test_tables_land_by_module_and_a_later_table_needs_no_edit(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    assert _rows(duckdb_path, "select row_id from organisation.site_master") == [("ATK",)]
    assert sorted(_rows(duckdb_path, "select row_id from store_layout.section")) == [("s1",), ("s2",)]
    assert _rows(duckdb_path, "select row_id from application.loose") == [("x",)]  # no module

    _sql(fresh_db, "create table featherbase.employee (row_id text primary key)",
         "insert into featherbase.employee values ('E1')",
         "insert into featherbase.table_def (name, module, kind, \"system\") values ('Employee', 'People', 'table', false)")
    assert cli.main(["run", path, "--dev"]) == 0
    assert _rows(duckdb_path, "select row_id from people.employee") == [("E1",)]


def test_a_table_moved_to_another_module_is_baselined_there_and_the_old_copy_stays(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    _sql(fresh_db, "update featherbase.table_def set module = 'Merchandising' where name = 'Section'")
    assert cli.main(["run", path, "--dev"]) == 0
    assert sorted(_rows(duckdb_path, "select row_id from merchandising.section")) == [("s1",), ("s2",)]  # the rows from before the move too
    assert sorted(_rows(duckdb_path, "select row_id from store_layout.section")) == [("s1",), ("s2",)]  # frozen
    _sql(fresh_db, "insert into featherbase.section values ('s3', 'Midi')")
    assert cli.main(["run", path, "--dev"]) == 0
    assert sorted(_rows(duckdb_path, "select row_id from merchandising.section")) == [("s1",), ("s2",), ("s3",)]
    assert len(_rows(duckdb_path, "select row_id from store_layout.section")) == 2
    # A third run moves nothing again.
    before = _rows(duckdb_path, 'select boundary_lsn from _cdc_runtime.baseline where "table" = \'section\'')
    assert cli.main(["run", path, "--dev"]) == 0
    assert _rows(duckdb_path, 'select boundary_lsn from _cdc_runtime.baseline where "table" = \'section\'') == before


def test_a_table_returning_to_a_schema_it_left_is_baselined_again(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """A -> B -> A: A's frozen copy misses what changed while the table was in B."""
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    _sql(fresh_db, "update featherbase.table_def set module = 'Merchandising' where name = 'Section'")
    assert cli.main(["run", path, "--dev"]) == 0
    _sql(fresh_db, "insert into featherbase.section values ('s3', 'Midi')",
         "update featherbase.section set label = 'Tops 2' where row_id = 's1'",
         "delete from featherbase.section where row_id = 's2'")
    assert cli.main(["run", path, "--dev"]) == 0
    _sql(fresh_db, "update featherbase.table_def set module = 'Store Layout' where name = 'Section'")
    assert cli.main(["run", path, "--dev"]) == 0
    assert sorted(_rows(duckdb_path, "select row_id, label from store_layout.section")) == [("s1", "Tops 2"), ("s3", "Midi")]


def test_a_table_in_drift_review_stays_where_its_rows_are_until_resnapshot(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    _sql(fresh_db, "alter table featherbase.section drop column label")
    assert cli.main(["run", path, "--dev"]) == 0
    state = _rows(duckdb_path, 'select state from _cdc_runtime.lifecycle where "table" = \'section\' order by since desc limit 1')
    assert state == [("drift_review",)]
    _sql(fresh_db, "update featherbase.table_def set module = 'Merchandising' where name = 'Section'",
         "insert into featherbase.section values ('s3')")
    cli.main(["run", path, "--dev"])
    assert ("s3",) in _rows(duckdb_path, "select row_id from store_layout.section")
    assert not _rows(duckdb_path, "select 1 from information_schema.tables where table_schema = 'merchandising' and table_name = 'section'")
    # The operator's resnapshot lands it in the new schema, whole.
    assert cli.main(["resnapshot", path, "--yes", "--dev"]) == 0
    assert sorted(_rows(duckdb_path, "select row_id from merchandising.section")) == [("s1",), ("s2",), ("s3",)]


def test_queued_changes_under_a_renamed_tables_old_name_reach_the_schema_holding_its_rows(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    _sql(fresh_db, "update featherbase.section set label = 'Tops 2' where row_id = 's1'",
         "alter table featherbase.section rename to floor_section",
         "update featherbase.table_def set name = 'Floor Section' where name = 'Section'")
    assert cli.main(["run", path, "--dev"]) == 0
    assert ("s1", "Tops 2") in _rows(duckdb_path, "select row_id, label from store_layout.section")
    assert not _rows(duckdb_path, "select 1 from information_schema.tables where table_schema = 'application' and table_name = 'section'")
    assert sorted(_rows(duckdb_path, "select row_id from store_layout.floor_section")) == [("s1",), ("s2",)]


def test_turning_module_routing_on_for_an_older_baseline_brings_its_rows(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """An upgrade: baselines from before targets were recorded, and application until now."""
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace(", from_module: true", ""))
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    assert len(_rows(duckdb_path, "select row_id from application.section")) == 2
    con = duckdb.connect(duckdb_path)
    con.execute("update _cdc_runtime.baseline set target_schema = null")
    con.close()
    open(path, "w", encoding="utf-8").write(text)
    assert cli.main(["run", path, "--dev"]) == 0
    assert sorted(_rows(duckdb_path, "select row_id from store_layout.section")) == [("s1",), ("s2",)]
    assert _rows(duckdb_path, "select target_schema from _cdc_runtime.baseline where \"table\" = 'section'") == [("store_layout",)]


def test_an_adopted_table_with_no_baseline_row_moves_with_its_rows(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    con.execute("delete from _cdc_runtime.baseline where \"table\" = 'section'")  # as _adopt_streaming leaves it
    con.close()
    assert cli.main(["run", path, "--dev"]) == 0  # its copy is where it routes: recorded, not baselined
    assert _rows(duckdb_path, "select boundary_lsn, target_schema from _cdc_runtime.baseline where \"table\" = 'section'") == [(None, "store_layout")]
    _sql(fresh_db, "update featherbase.table_def set module = 'Merchandising' where name = 'Section'")
    assert cli.main(["run", path, "--dev"]) == 0
    assert sorted(_rows(duckdb_path, "select row_id from merchandising.section")) == [("s1",), ("s2",)]


def test_an_unrecorded_table_in_drift_review_stays_with_its_rows(fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Adopted, no baseline row, drift before the first run with module routing."""
    path = _setup(fresh_db, names, duckdb_path, tmp_path, monkeypatch)
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace(", from_module: true", ""))
    _sql(fresh_db, *SCHEMA)
    assert cli.main(["run", path, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    con.execute("delete from _cdc_runtime.baseline")
    con.close()
    open(path, "w", encoding="utf-8").write(text)
    _sql(fresh_db, "alter table featherbase.section drop column label", "insert into featherbase.section values ('s3')")
    cli.main(["run", path, "--dev"])
    assert ("s3",) in _rows(duckdb_path, "select row_id from application.section")
    assert not _rows(duckdb_path, "select 1 from information_schema.tables where table_schema = 'store_layout' and table_name = 'section'")


@pytest.mark.parametrize("state", ["drift_review", "resnapshot_required"])
def test_nothing_lands_on_a_copy_waiting_for_its_resnapshot(state):
    held = NS(schema="featherbase", table="section", target_schema="merchandising")
    app = NS(schema="store_layout", table="section", target_schema="store_layout")
    dest = NS(read_baseline_targets=lambda registration: {("featherbase", "section"): "store_layout"})
    cfg = NS(destination=dest, registration="r", target_schema=lambda s, t: "application")
    targets = runner._Targets(cfg, [held, app])
    lc = NS(known=lambda: {("featherbase", "section"): state, ("store_layout", "section"): "streaming"})
    with pytest.raises(RuntimeError, match="would land on"):   # a run, or a resnapshot of the other table
        runner._refuse_overwriting_held(cfg, [held, app], lc=lc, targets=targets)
    with pytest.raises(RuntimeError, match="would land on"):
        runner._refuse_overwriting_held(cfg, [app], lc=lc, targets=targets, resolving={("store_layout", "section")})
    # Its own resnapshot, alone or with the other table, is the way out.
    runner._refuse_overwriting_held(cfg, [held], lc=lc, targets=targets, resolving={("featherbase", "section")})
    runner._refuse_overwriting_held(cfg, [held, app], lc=lc, targets=targets,
                                    resolving={("featherbase", "section"), ("store_layout", "section")})


def test_a_copy_with_no_recorded_target_is_protected_at_the_older_route():
    held = NS(schema="featherbase", table="section", target_schema="merchandising")
    app = NS(schema="application", table="section", target_schema="application")
    lc = NS(known=lambda: {("featherbase", "section"): "drift_review", ("application", "section"): "streaming"})
    for recorded in ({("featherbase", "section"): None}, {}):  # a baseline from before targets, an adopted table
        dest = NS(read_baseline_targets=lambda registration, r=recorded: r)
        cfg = NS(destination=dest, registration="r", target_schema=lambda s, t: "application")
        targets = runner._Targets(cfg, [held, app])
        with pytest.raises(RuntimeError, match="would land on"):
            runner._refuse_overwriting_held(cfg, [app], lc=lc, targets=targets, resolving={("application", "section")})
