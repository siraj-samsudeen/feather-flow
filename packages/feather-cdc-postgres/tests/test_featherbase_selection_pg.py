"""The shipped Featherbase registration against the live catalog shape (#3079 D-13, #3894).

Two-way selection (Siraj, 05-Sep-2026): every user-created table flows with no manifest edit;
Featherbase's own platform tables flow only when named. Compiled against the fixture (the
live `table_def` rows, `"system"` flag included) the desired set must be exactly 13 relations
in four bronze schemas — and the navigation, scheduler and `version` tables must NOT be in it.
"""
from __future__ import annotations

import json
import pathlib

import psycopg
import pytest

from pg_md_cdc import cli, manifest as mf, runner
from pg_md_cdc.adapters import NAMED_SYSTEM, USER_CREATED

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "featherbase_namespaced_schema.sql"
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "registrations" / "featherbase.yml"

EXPECTED = {
    "catalog": {"table_def", "column_def"},
    "access": {"user", "role", "has_role", "permission", "data_scope", "share"},
    "activity": {"activity_log", "user_event", "comment"},
    "application": {"project", "task"},
}
NEVER = {"background_job", "job_execution", "home_page", "home_page_link", "home_page_role", "version"}


@pytest.fixture
def featherbase_db(fresh_db, monkeypatch):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute(FIXTURE.read_text(encoding="utf-8"))
        assert c.execute('select count(*) from featherbase.table_def where "system"').fetchone()[0] == 14
        assert c.execute('select count(*) from featherbase.table_def where not "system"').fetchone()[0] == 2
    monkeypatch.setenv("FEATHERBASE_PG_DSN", fresh_db)
    return fresh_db


def test_the_shipped_registration_compiles_to_exactly_thirteen_routed_relations(featherbase_db):
    m = mf.load(str(MANIFEST))
    _, plan = runner._compile(m)
    assert [p for p in plan.problems if p.severity == "error"] == []
    by_target: dict = {}
    for r in plan.relations:
        by_target.setdefault(r.target_schema, set()).add(r.table)
    assert by_target == EXPECTED
    assert len(plan.relations) == 13
    assert NEVER.isdisjoint({r.table for r in plan.relations})
    assert {(r.schema, r.table) for r in plan.relations if r.origin == USER_CREATED} == {
        ("tasker", "project"),
        ("tasker", "task"),
    }
    assert {r.schema for r in plan.relations if r.origin == NAMED_SYSTEM} == {"featherbase"}

    origins = {(r.schema, r.table): r.origin for r in plan.relations}
    assert origins[("tasker", "task")] == USER_CREATED
    assert {o for (s, _), o in origins.items() if s != "tasker"} == {NAMED_SYSTEM}
    classes = {r.table: r.table_class for r in plan.relations}
    assert classes["task"] == "application" and classes["comment"] == "application"
    assert classes["table_def"] == "source_system" and classes["user"] == "source_system"


def test_plan_prints_the_thirteen_with_their_origin_and_target(featherbase_db, capsys):
    assert cli.main(["plan", str(MANIFEST)]) == 0
    out = capsys.readouterr().out
    assert "relations (13):" in out
    assert "tasker.task" in out and "-> application.task" in out and "origin=user_created" in out
    assert "-> catalog.table_def" in out and "-> access.user" in out and "-> activity.comment" in out
    for t in NEVER:
        assert f"featherbase.{t} " not in out
    assert "public.site " not in out
    assert cli.main(["--json", "plan", str(MANIFEST)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {(r["target_schema"], r["table"]) for r in payload["relations"]} == {
        (s, t) for s, ts in EXPECTED.items() for t in ts}


def test_a_new_user_created_table_is_selected_with_no_manifest_edit(featherbase_db):
    """Half one of the rule: a Featherbase user declares a Table, it is materialised, and the
    next compile carries it — into `application`, with no registration change."""
    with psycopg.connect(featherbase_db, autocommit=True) as c:
        c.execute("create schema budgets")
        c.execute("create table budgets.invoice (row_id varchar(140) primary key, total numeric)")
        c.execute('insert into featherbase.table_def '
                  '(name, module, kind, "system", owner_app, physical_schema, physical_relation) '
                  "values ('budgets.invoice', 'Budgets', 'table', false, 'budgets', 'budgets', 'invoice')")
        # and a new PLATFORM table stays out until it is named
        c.execute("create table featherbase.email_queue_v2 (row_id varchar(140) primary key)")
        c.execute('insert into featherbase.table_def (name, module, kind, "system") '
                  "values ('Email Queue V2', 'Core', 'table', true)")
    _, plan = runner._compile(mf.load(str(MANIFEST)))
    by = {r.table: r for r in plan.relations}
    assert len(plan.relations) == 14
    assert (by["invoice"].target_schema, by["invoice"].origin, by["invoice"].table_class) == (
        "application", USER_CREATED, "application")
    assert "email_queue_v2" not in by


def test_a_plain_user_table_lives_at_featherbases_default_location(featherbase_db):
    """#4113: Featherbase stores a physical location only for qualified (app-owned) Tables; a
    plain Table such as the sales-target ones lives at `featherbase.<snake_case>` with NULL
    physical columns (featherbase `tableRelation()`). Discovery must find it there."""
    with psycopg.connect(featherbase_db, autocommit=True) as c:
        c.execute("create table featherbase.section_ownership (row_id varchar(140) primary key)")
        c.execute('insert into featherbase.table_def (name, module, kind, "system") '
                  "values ('Section Ownership', 'Store Sections', 'table', false)")
    _, plan = runner._compile(mf.load(str(MANIFEST)))
    by = {r.table: r for r in plan.relations}
    assert (by["section_ownership"].schema, by["section_ownership"].origin,
            by["section_ownership"].target_schema) == ("featherbase", USER_CREATED, "application")


def test_a_qualified_user_table_with_no_stored_location_still_refuses(featherbase_db):
    """The other half of Featherbase's rule: a qualified name is never located by its spelling."""
    with psycopg.connect(featherbase_db, autocommit=True) as c:
        c.execute('insert into featherbase.table_def (name, module, kind, "system") '
                  "values ('budgets.invoice', 'Budgets', 'table', false)")
    with pytest.raises(RuntimeError, match="budgets.invoice"):
        runner._compile(mf.load(str(MANIFEST)))
