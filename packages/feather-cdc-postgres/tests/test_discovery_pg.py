"""Catalog discovery + compilation against a real Postgres (#3079 D2/D4/D6/D7)."""
from __future__ import annotations

import psycopg
import pytest

from pg_md_cdc import discovery, schema_registry as sr, selectors as sel
from pg_md_cdc.manifest import parse


def _ddl(dsn, *statements):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in statements:
            c.execute(s)


def _m(**over):
    raw = {
        "api_version": "pg-md-cdc/v1",
        "source_id": "demo",
        "source": {"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["*"]}},
        "destination": {"token_env": "MOTHERDUCK_TOKEN", "database": "bronze_demo"},
        "replication": {"slot": "demo_slot", "publication": "demo_pub"},
        "control": {"extractor": "demo"},
    }
    for k, v in over.items():
        raw[k] = {**raw[k], **v} if isinstance(v, dict) and isinstance(raw.get(k), dict) else v
    return parse(raw)


def test_two_schemas_with_the_same_table_name_stay_separate(fresh_db):
    _ddl(
        fresh_db,
        "create schema a", "create schema b",
        "create table a.t (id int primary key, v text)",
        "create table b.t (id int primary key, v text)",
    )
    rels = discovery.discover(fresh_db)
    assert [(r.schema, r.table) for r in rels] == [("a", "t"), ("b", "t")]
    plan = sel.compile(_m(), rels)
    assert [r.target for r in plan.relations] == ["a.t", "b.t"]
    assert len({r.target for r in plan.relations}) == 2


def test_composite_primary_key_and_replica_identity_are_read(fresh_db):
    _ddl(
        fresh_db,
        "create table public.t (a int not null, b int not null, v text, primary key (a, b))",
        "alter table public.t replica identity full",
    )
    (rel,) = discovery.discover(fresh_db)
    assert rel.primary_key == ("a", "b")
    assert rel.replica_identity == "f"
    plan = sel.compile(_m(replication={"replica_identity": "full"}), [rel])
    assert plan.relations[0].key == ("a", "b")
    assert plan.replica_identity_alters == []  # already FULL — no ALTER proposed


def test_a_table_without_a_key_is_rejected_by_default_and_kept_when_append_only(fresh_db):
    _ddl(fresh_db, "create table public.events (v text, at timestamptz)")
    rels = discovery.discover(fresh_db)
    rejected = sel.compile(_m(), rels)
    assert rejected.relations == []
    assert "no_key_rejected" in {p.code for p in rejected.problems if p.severity == "error"}
    allowed = sel.compile(_m(replication={"tables_without_key": "append_only"}), rels)
    assert [(r.table, r.key_policy) for r in allowed.relations] == [("events", sel.APPEND_ONLY)]


def test_a_not_null_unique_index_becomes_the_merge_key(fresh_db):
    _ddl(
        fresh_db,
        "create table public.t (code text not null, v text)",
        "create unique index uq_code on public.t (code)",
        "alter table public.t replica identity full",
    )
    (rel,) = discovery.discover(fresh_db)
    assert [i.name for i in rel.unique_indexes] == ["uq_code"]
    plan = sel.compile(_m(replication={"replica_identity": "full"}), [rel])
    assert (plan.relations[0].key, plan.relations[0].key_policy, plan.relations[0].key_index) == (
        ("code",), sel.UNIQUE_INDEX, "uq_code",
    )


def test_a_partial_unique_index_is_not_offered_as_a_key(fresh_db):
    """A partial unique index guarantees uniqueness only over its predicate, so merging on it
    would silently overwrite rows outside it."""
    _ddl(
        fresh_db,
        "create table public.t (code text not null, live bool not null, v text)",
        "create unique index uq_live on public.t (code) where live",
    )
    (rel,) = discovery.discover(fresh_db)
    assert rel.unique_indexes == ()
    assert sel.compile(_m(), [rel]).relations == []


def test_an_undeclared_type_surfaces_a_named_warning(fresh_db):
    _ddl(fresh_db, "create table public.t (id int primary key, addr inet, where_at point, tags text[])")
    (rel,) = discovery.discover(fresh_db)
    cols, problems = sr.compile_relation(rel, ("id",))
    text = " ".join(p.message for p in problems)
    assert "public.t.addr" in text and "public.t.where_at" in text and "public.t.tags" in text
    assert {c.name for c in cols if c.compiled.mapping == sr.TEXT_FALLBACK} == {"addr", "where_at", "tags"}
    assert {c.name: c.compiled.dest_type for c in cols}["id"] == "bigint"


def test_arrays_are_detected_from_the_catalog(fresh_db):
    _ddl(fresh_db, "create table public.t (id int primary key, tags text[])")
    (rel,) = discovery.discover(fresh_db)
    assert rel.column("tags").is_array is True and rel.column("id").is_array is False


def test_deny_matches_a_real_relation(fresh_db):
    _ddl(fresh_db, "create table public.access_token (id int primary key)", "create table public.ok (id int primary key)")
    m = _m(source={"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["*"]}, "deny": {"tables": ["public.access_token"]}})
    plan = sel.compile(m, discovery.discover(fresh_db))
    assert plan.denied_hits == ["public.access_token"]
    assert [r.table for r in plan.relations] == ["ok"]


def test_system_schemas_are_never_discovered(fresh_db):
    assert discovery.discover(fresh_db) == []


def test_publication_and_slot_state(fresh_db, names):
    _ddl(
        fresh_db,
        "create table public.t (id int primary key)",
        f'create publication "{names["publication"]}" for table public.t',
    )
    assert discovery.publication_relations(fresh_db, names["publication"]) == {("public", "t")}
    assert discovery.publication_relations(fresh_db, "no_such_pub") is None

    absent = discovery.slot_state(fresh_db, names["slot"])
    assert absent["exists"] is False
    assert absent["max_slot_wal_keep_size_bytes"] is None or isinstance(absent["max_slot_wal_keep_size_bytes"], int)

    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("select pg_create_logical_replication_slot(%s, 'pgoutput')", (names["slot"],))
    live = discovery.slot_state(fresh_db, names["slot"])
    assert live["exists"] is True and live["wal_status"] == "reserved" and live["lag_bytes"] >= 0


def test_featherbase_adapter_reproduces_the_pilot_catalog_rules(fresh_db):
    """Every filter the #2851 drainer applies, exercised at once: physical name derivation,
    kind filter, data_source filter, module allow-list, the always-on catalog tables, and the
    declared-but-not-materialised skip. A regression here silently changes what gets replicated."""
    from pg_md_cdc.adapters.featherbase import select

    _ddl(
        fresh_db,
        'create table public.table_def (id serial primary key, "name" text, module text, '
        "data_source text, kind text, description text)",
        "create table public.column_def (id int primary key)",
        "create table public.access_log (id int primary key)",
        "create table public.comment (id int primary key)",
        "create table public.reflected (id int primary key)",
        "create table public.settings_eav (id int primary key)",
        "create table public.other_module (id int primary key)",
        """insert into public.table_def ("name", module, data_source, kind, description) values
             ('Access Log', 'Core', null, 'table', 'Audit trail.'),
             ('Comment', 'Core', null, 'sub_table', null),
             ('Reflected', 'Core', 'sap', 'table', null),
             ('Settings Eav', 'Core', null, 'settings', null),
             ('Other Module', 'Billing', null, 'table', null),
             ('Not Yet Built', 'Core', null, 'table', null),
             ('Table', null, null, 'table', null),
             ('Column', null, null, 'table', null)""",
    )
    every = select(fresh_db, {})
    assert [r.table for r in every] == [
        "access_log", "column_def", "comment", "other_module", "table_def",
    ]
    assert {r.table: r.description for r in every}["access_log"] == "Audit trail."
    assert {r.table: r.module for r in every}["other_module"] == "Billing"

    core_only = select(fresh_db, {"modules": ["Core"]})
    # the catalog itself rides along whatever the allow-list says
    assert [r.table for r in core_only] == ["access_log", "column_def", "comment", "table_def"]
