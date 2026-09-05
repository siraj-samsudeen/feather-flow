"""The shipped Featherbase registration against the live catalog shape (#3079 D-13).

Two-way selection (Siraj, 05-Sep-2026): every user-created table flows with no manifest edit;
Featherbase's own platform tables flow only when named. Compiled against the fixture (the
live `table_def` rows, `"system"` flag included) the desired set must be exactly 12 relations
in four bronze schemas — and the navigation, scheduler and `version` tables must NOT be in it.
"""
from __future__ import annotations

import json
import pathlib

import psycopg
import pytest

from pg_md_cdc import cli, manifest as mf, runner
from pg_md_cdc.adapters import NAMED_SYSTEM, USER_CREATED

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "featherbase_public_schema.sql"
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "registrations" / "featherbase.yml"

EXPECTED = {
    "catalog": {"table_def", "column_def"},
    "access": {"user", "role", "has_role", "permission", "data_scope", "share"},
    "activity": {"activity_log", "user_event", "comment"},
    "application": {"test"},
}
NEVER = {"background_job", "job_execution", "home_page", "home_page_link", "home_page_role", "version"}


@pytest.fixture
def featherbase_db(fresh_db, monkeypatch):
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute(FIXTURE.read_text(encoding="utf-8"))
        assert c.execute('select count(*) from public.table_def where "system"').fetchone()[0] >= 49
        assert c.execute('select count(*) from public.table_def where not "system"').fetchone()[0] == 1
    monkeypatch.setenv("FEATHERBASE_PG_DSN", fresh_db)
    return fresh_db


def test_the_shipped_registration_compiles_to_exactly_twelve_routed_relations(featherbase_db):
    m = mf.load(str(MANIFEST))
    _, plan = runner._compile(m)
    assert [p for p in plan.problems if p.severity == "error"] == []
    by_target: dict = {}
    for r in plan.relations:
        by_target.setdefault(r.target_schema, set()).add(r.table)
    assert by_target == EXPECTED
    assert len(plan.relations) == 12
    assert NEVER.isdisjoint({r.table for r in plan.relations})
    assert {r.schema for r in plan.relations} == {"public"}

    origins = {r.table: r.origin for r in plan.relations}
    assert origins["test"] == USER_CREATED
    assert {o for t, o in origins.items() if t != "test"} == {NAMED_SYSTEM}
    classes = {r.table: r.table_class for r in plan.relations}
    assert classes["test"] == "application" and classes["comment"] == "application"
    assert classes["table_def"] == "source_system" and classes["user"] == "source_system"


def test_plan_prints_the_twelve_with_their_origin_and_target(featherbase_db, capsys):
    assert cli.main(["plan", str(MANIFEST)]) == 0
    out = capsys.readouterr().out
    assert "relations (12):" in out
    assert "public.test" in out and "-> application.test" in out and "origin=user_created" in out
    assert "-> catalog.table_def" in out and "-> access.user" in out and "-> activity.comment" in out
    for t in NEVER:
        assert f"public.{t} " not in out
    assert cli.main(["--json", "plan", str(MANIFEST)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {(r["target_schema"], r["table"]) for r in payload["relations"]} == {
        (s, t) for s, ts in EXPECTED.items() for t in ts}


def test_a_new_user_created_table_is_selected_with_no_manifest_edit(featherbase_db):
    """Half one of the rule: a Featherbase user declares a Table, it is materialised, and the
    next compile carries it — into `application`, with no registration change."""
    with psycopg.connect(featherbase_db, autocommit=True) as c:
        c.execute("create table public.invoice (row_id varchar(140) primary key, total numeric)")
        c.execute('insert into public.table_def ("name", module, kind, "system") '
                  "values ('Invoice', 'Custom', 'table', false)")
        # and a new PLATFORM table stays out until it is named
        c.execute("create table public.email_queue_v2 (row_id varchar(140) primary key)")
        c.execute('insert into public.table_def ("name", module, kind, "system") '
                  "values ('Email Queue V2', 'Core', 'table', true)")
    _, plan = runner._compile(mf.load(str(MANIFEST)))
    by = {r.table: r for r in plan.relations}
    assert len(plan.relations) == 13
    assert (by["invoice"].target_schema, by["invoice"].origin, by["invoice"].table_class) == (
        "application", USER_CREATED, "application")
    assert "email_queue_v2" not in by
