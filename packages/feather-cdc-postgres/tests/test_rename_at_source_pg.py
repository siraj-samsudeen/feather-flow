"""A platform table RENAMED at the source while the CDC is paused (#4338: Featherbase 0101,
`featherbase.user` → `featherbase.person`), then the registration switched to the new name.

PostgreSQL keeps a renamed table in its publication (membership is by OID), so after the
rename the publication already lists `featherbase.person`. The runner must still treat
`person` as NEW — a full baseline, not "adopted: already streaming" — and must retire the
old name, whose lifecycle row would otherwise say `streaming` for ever.
"""
from __future__ import annotations

import pathlib

import duckdb
import psycopg

from pg_md_cdc import cli, manifest as mf, runner

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
      system_tables: [table_def, {people_table}, has_role]
destination:
  token_env: FB_DEMO_MD_TOKEN
  database: bronze_fb_demo
  schema_mapping: preserve_source
  table_schemas:
    - {{match: "featherbase.table_def", schema: catalog}}
    - {{match: "featherbase.user", schema: access}}
    - {{match: "featherbase.person", schema: access}}
    - {{match: "featherbase.has_role", schema: access}}
    - {{match: "*.*", schema: application}}
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
    'create table featherbase."user" (row_id text primary key, full_name text)',
    "create table featherbase.has_role (row_id text primary key, parent text, role text)",
    "insert into featherbase.table_def (name, module, kind, \"system\") values "
    "('Table', 'Core', 'table', true), ('User', 'Core', 'table', true), "
    "('Has Role', 'Core', 'sub_table', true)",
    "insert into featherbase.\"user\" values ('Administrator', 'Administrator'), "
    "('Guest', 'Guest'), ('E001', 'Asha'), ('E002', 'Bala')",
    "insert into featherbase.has_role values ('hr1', 'E001', 'Store Manager'), "
    "('hr2', 'E002', 'Team Leader')",
]

# Featherbase 0101 in miniature: one transaction renames the table and its catalog row and
# re-keys every non-built-in person. The built-ins are never touched, so no WAL record carries
# them — only a baseline can put them in the new bronze table.
MIGRATION_0101 = [
    'alter table featherbase."user" rename to person',
    "update featherbase.table_def set name = 'Person' where name = 'User'",
    "update featherbase.person set row_id = 'psn_a' where row_id = 'E001'",
    "update featherbase.person set row_id = 'psn_b' where row_id = 'E002'",
    "update featherbase.has_role set parent = 'psn_a' where parent = 'E001'",
    "update featherbase.has_role set parent = 'psn_b' where parent = 'E002'",
]


def _sql(dsn, *statements, transaction=False):
    with psycopg.connect(dsn, autocommit=not transaction) as c:
        for s in statements:
            c.execute(s)


def _write(tmp_path, names, people_table: str) -> str:
    path = tmp_path / f"fb_demo_{people_table}.yml"
    path.write_text(MANIFEST.format(people_table=people_table, slot=names["slot"],
                                    publication=names["publication"],
                                    wal_dir=str(tmp_path / "wal")), encoding="utf-8")
    return str(path)


def _rows(duckdb_path, sql):
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _tables(duckdb_path) -> set:
    return {f"{s}.{t}" for s, t in _rows(
        duckdb_path,
        "select table_schema, table_name from information_schema.tables "
        "where table_schema not like '%_staging' and table_schema <> '_cdc_runtime' "
        "and table_name not like '\\_dlt\\_%' escape '\\'")}


def _status(tmp_path) -> str:
    from test_e2e_pg import finish_record
    return finish_record(str(tmp_path / "wal"), extractor="fb_demo_cdc")["status"]


def _env(monkeypatch, fresh_db, duckdb_path, tmp_path):
    monkeypatch.setenv("FB_DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{duckdb_path}")
    monkeypatch.setenv("PG_MD_CDC_PIPELINES_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)
    monkeypatch.delenv("FB_DEMO_MD_TOKEN", raising=False)


def test_a_table_renamed_while_paused_is_baselined_under_its_new_name_and_the_old_one_retired(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    new = _write(tmp_path, names, "person")

    assert cli.main(["run", old, "--dev"]) == 0
    assert _rows(duckdb_path, "select count(*) from access.user") == [(4,)]

    # The window: the worker is paused. A sign-in touches a row BEFORE the migration, then
    # 0101 runs as one transaction, and the operator merges the registration change.
    _sql(fresh_db, "update featherbase.\"user\" set full_name = 'Asha K' where row_id = 'E001'")
    _sql(fresh_db, *MIGRATION_0101, transaction=True)
    with psycopg.connect(fresh_db) as c:
        published = {r[0] for r in c.execute(
            "select tablename from pg_publication_tables where pubname = %s",
            (names["publication"],)).fetchall()}
    assert "person" in published and "user" not in published, \
        "premise: PostgreSQL keeps a renamed table in its publication"

    assert cli.main(["run", new, "--dev"]) == 0
    assert _status(tmp_path) == "ok", "every relation drained and every reconcile count tied"

    # person: a FULL baseline — the two built-ins no WAL record carries are there too
    assert _rows(duckdb_path, "select row_id, full_name from access.person order by 1") == [
        ("Administrator", "Administrator"), ("Guest", "Guest"),
        ("psn_a", "Asha K"), ("psn_b", "Bala")]
    lifecycle = runner._destination(mf.load(new)).read_lifecycle("fb_demo")
    assert lifecycle[("featherbase", "person")] == "streaming"
    # user: retired and frozen in place, holding the last state it had under its old name
    assert lifecycle[("featherbase", "user")] == "retired"
    assert _rows(duckdb_path, "select row_id, full_name from access.user order by 1") == [
        ("Administrator", "Administrator"), ("E001", "Asha K"), ("E002", "Bala"),
        ("Guest", "Guest")]
    # the pre-rename WAL record routed by the old name's rule, never into `application`
    assert _tables(duckdb_path) == {"catalog.table_def", "access.user", "access.person",
                                    "access.has_role"}
    # a key-changing UPDATE (table_def 'User' -> 'Person') leaves no ghost row behind
    assert _rows(duckdb_path, "select name from catalog.table_def order by 1") == [
        ("Has Role",), ("Person",), ("Table",)]
    # the re-keyed has_role rows streamed as ordinary updates
    assert _rows(duckdb_path, "select row_id, parent from access.has_role order by 1") == [
        ("hr1", "psn_a"), ("hr2", "psn_b")]

    # and person keeps streaming afterwards
    _sql(fresh_db, "insert into featherbase.person values ('psn_c', 'Chitra')")
    assert cli.main(["run", new, "--dev"]) == 0
    assert _rows(duckdb_path, "select count(*) from access.person") == [(5,)]
    assert _status(tmp_path) == "ok"
    assert cli.main(["status", new]) == 0


def test_a_table_gone_after_a_rebuilt_source_is_retired_on_the_re_baseline(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """The clean-start path (#4333): the source is wiped — slot and publication dropped — and
    rebuilt already carrying `person`. The re-initialising tick baselines the new selection;
    the old name, no longer selected, must still be marked retired rather than left `streaming`."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    new = _write(tmp_path, names, "person")
    assert cli.main(["run", old, "--dev"]) == 0

    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("select pg_drop_replication_slot(%s)", (names["slot"],))
        c.execute(f'drop publication {names["publication"]}')
    _sql(fresh_db, *MIGRATION_0101, transaction=True)

    assert cli.main(["run", new, "--dev"]) == 0
    lifecycle = runner._destination(mf.load(new)).read_lifecycle("fb_demo")
    assert lifecycle[("featherbase", "person")] == "streaming"
    assert lifecycle[("featherbase", "user")] == "retired"
    assert _rows(duckdb_path, "select count(*) from access.person") == [(4,)]
    assert _rows(duckdb_path, "select count(*) from access.user") == [(4,)]


def test_an_update_that_changes_the_primary_key_leaves_no_ghost_row(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Generic (#4338): the merge keys on the NEW tuple, so an UPDATE of the key used to keep
    the old key's row in bronze for ever. A key re-used later in the same batch must win."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    reg = _write(tmp_path, names, "user")
    assert cli.main(["run", reg, "--dev"]) == 0
    _sql(fresh_db,
         "update featherbase.\"user\" set row_id = 'E009' where row_id = 'E001'",
         "insert into featherbase.\"user\" values ('E001', 'Asha (new)')",
         "update featherbase.\"user\" set row_id = 'E010', full_name = 'Bala R' "
         "where row_id = 'E002'")
    assert cli.main(["run", reg, "--dev"]) == 0
    assert _rows(duckdb_path, "select row_id, full_name from access.user order by 1") == [
        ("Administrator", "Administrator"), ("E001", "Asha (new)"), ("E009", "Asha"),
        ("E010", "Bala R"), ("Guest", "Guest")]
    assert _status(tmp_path) == "ok"


def test_undoing_the_rename_after_the_switch_re_baselines_the_old_name(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """featherbase DEPLOY "After step 9": the release is undone and the registration put back
    to `user`. The re-selected `user` is RETIRED but already published (the rename back keeps
    the OID in the publication), so it must be re-baselined — its frozen copy is stale — and
    `person` must be retired."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    new = _write(tmp_path, names, "person")
    assert cli.main(["run", old, "--dev"]) == 0
    _sql(fresh_db, *MIGRATION_0101, transaction=True)
    assert cli.main(["run", new, "--dev"]) == 0

    _sql(fresh_db,
         'alter table featherbase.person rename to "user"',
         "update featherbase.table_def set name = 'User' where name = 'Person'",
         "update featherbase.\"user\" set row_id = 'E001' where row_id = 'psn_a'",
         "update featherbase.\"user\" set row_id = 'E002' where row_id = 'psn_b'",
         "insert into featherbase.\"user\" values ('E003', 'Chitra')",
         # and the restored shape differs from the signature `user` retired with: a dropped
         # column is breaking drift, which must not abort the tick that re-baselines it
         'alter table featherbase."user" drop column full_name',
         transaction=True)
    assert cli.main(["run", old, "--dev"]) == 0
    lifecycle = runner._destination(mf.load(old)).read_lifecycle("fb_demo")
    assert lifecycle[("featherbase", "user")] == "streaming"
    assert lifecycle[("featherbase", "person")] == "retired"
    assert _rows(duckdb_path, "select row_id from access.user order by 1") == [
        ("Administrator",), ("E001",), ("E002",), ("E003",), ("Guest",)]
    assert _status(tmp_path) == "ok"


def test_a_takeover_that_crashed_half_way_adopts_on_retry_and_never_re_baselines(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex adversarial (#4338): adoption must not hinge on "the lifecycle is empty". A
    takeover that wrote some lifecycle rows and crashed leaves the rest published with no row;
    the retry must ADOPT those that already have a destination copy, not re-baseline them (a
    baseline replaces the copy, which for append_only is history)."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    reg = _write(tmp_path, names, "user")
    assert cli.main(["run", reg, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    try:
        # bronze-only history the source no longer has, and a crash that lost has_role's row
        con.execute("insert into access.has_role (row_id, parent, role, _dlt_load_id, _dlt_id) "
                    "values ('hr-history', 'E009', 'Old Role', '0', 'history-1')")
        con.execute('delete from "_cdc_runtime".lifecycle where "table" = \'has_role\'')
    finally:
        con.close()
    assert cli.main(["run", reg, "--dev"]) == 0
    lifecycle = runner._destination(mf.load(reg)).read_lifecycle("fb_demo")
    assert lifecycle[("featherbase", "has_role")] == "streaming"
    assert _rows(duckdb_path, "select row_id from access.has_role order by 1") == [
        ("hr-history",), ("hr1",), ("hr2",)], "adopted, not re-baselined"


def test_a_slot_lost_during_the_rename_window_re_initialises_with_the_new_selection(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex adversarial round 2 (#4338): the slot is invalidated while the worker is paused.
    The re-initialising tick meets `person` (no lifecycle row yet) and must not abort on a
    `new → resnapshot_required` transition; it baselines the new selection and retires `user`."""
    from test_e2e_pg import _report_lost_once
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    new = _write(tmp_path, names, "person")
    assert cli.main(["run", old, "--dev"]) == 0
    _sql(fresh_db, *MIGRATION_0101, transaction=True)

    _report_lost_once(monkeypatch)
    assert cli.main(["run", new, "--dev"]) == 0
    lifecycle = runner._destination(mf.load(new)).read_lifecycle("fb_demo")
    assert lifecycle[("featherbase", "person")] == "streaming"
    assert lifecycle[("featherbase", "user")] == "retired"
    assert _rows(duckdb_path, "select count(*) from access.person") == [(4,)]
    assert _status(tmp_path) == "ok"


def test_a_retired_relation_with_no_recorded_key_is_reported_not_merged_silently(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex adversarial round 3 (#4338): if a retired name has no signature (a crash before
    signatures were written, on an older runner), its pre-rename WAL can only merge on replica
    identity. That must surface as an error event — the tick is `partial`, never silent."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    new = _write(tmp_path, names, "person")
    assert cli.main(["run", old, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    try:
        con.execute('delete from "_cdc_runtime".signature where "table" = \'user\'')
    finally:
        con.close()
    _sql(fresh_db, "update featherbase.\"user\" set full_name = 'Asha K' where row_id = 'E001'")
    _sql(fresh_db, *MIGRATION_0101, transaction=True)
    assert cli.main(["run", new, "--dev"]) == 0
    from test_e2e_pg import finish_record
    rec = finish_record(str(tmp_path / "wal"), extractor="fb_demo_cdc")
    assert rec["status"] == "partial"
    assert runner._destination(mf.load(new)).read_lifecycle("fb_demo")[
        ("featherbase", "user")] == "retired"


def test_a_retired_table_still_at_the_source_resolves_its_key_from_the_catalog_silently(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex round 4 (standard): a retired relation with no signature — the pilot's adopted
    and retired tables — still exists at the source, so its queued WAL resolves the key from
    the catalog. That must not raise anything: the tick stays `ok`."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    narrowed = tmp_path / "fb_demo_narrowed.yml"
    narrowed.write_text(pathlib.Path(old).read_text(encoding="utf-8").replace(
        "system_tables: [table_def, user, has_role]", "system_tables: [table_def, has_role]"),
        encoding="utf-8")
    assert cli.main(["run", old, "--dev"]) == 0
    con = duckdb.connect(duckdb_path)
    try:
        con.execute('delete from "_cdc_runtime".signature where "table" = \'user\'')
    finally:
        con.close()
    _sql(fresh_db, "update featherbase.\"user\" set full_name = 'Asha K' where row_id = 'E001'")
    assert cli.main(["run", str(narrowed), "--dev"]) == 0
    assert runner._destination(mf.load(str(narrowed))).read_lifecycle("fb_demo")[
        ("featherbase", "user")] == "retired"
    assert _status(tmp_path) == "ok"
    assert _rows(duckdb_path, "select row_id, full_name from access.user where row_id = 'E001'"
                 ) == [("E001", "Asha K")]


def test_a_rollback_interrupted_after_the_discovered_write_still_re_baselines_on_retry(
        fresh_db, names, duckdb_path, tmp_path, monkeypatch):
    """Codex adversarial round 5: re-admitting a retired, already-published table writes
    retired → discovered → snapshotting. A crash between the two leaves `discovered`; the retry
    must still baseline the frozen copy (rows that changed under `person` included)."""
    _env(monkeypatch, fresh_db, duckdb_path, tmp_path)
    _sql(fresh_db, *SCHEMA)
    old = _write(tmp_path, names, "user")
    new = _write(tmp_path, names, "person")
    assert cli.main(["run", old, "--dev"]) == 0
    _sql(fresh_db, *MIGRATION_0101, transaction=True)
    assert cli.main(["run", new, "--dev"]) == 0
    _sql(fresh_db, "insert into featherbase.person values ('psn_c', 'Chitra')")
    assert cli.main(["run", new, "--dev"]) == 0
    _sql(fresh_db, 'alter table featherbase.person rename to "user"',
         "update featherbase.table_def set name = 'User' where name = 'Person'",
         transaction=True)
    runner._destination(mf.load(old)).write_lifecycle(
        "fb_demo", "featherbase", "user", "discovered", "crash after the discovered write")

    assert cli.main(["run", old, "--dev"]) == 0
    assert runner._destination(mf.load(old)).read_lifecycle("fb_demo")[
        ("featherbase", "user")] == "streaming"
    assert _rows(duckdb_path, "select row_id from access.user order by 1") == [
        ("Administrator",), ("Guest",), ("psn_a",), ("psn_b",), ("psn_c",)]
