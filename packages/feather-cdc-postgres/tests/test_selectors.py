"""Desired-state compilation: patterns, deny, key policy, collisions, hash (#3079 D4/D6/D12)."""
from __future__ import annotations

import pytest

from pg_md_cdc import selectors as sel
from pg_md_cdc.adapters import AdapterRelation
from pg_md_cdc.discovery import SourceColumn, SourceRelation, UniqueIndex
from pg_md_cdc.manifest import parse

BASE = {
    "api_version": "pg-md-cdc/v1",
    "source_id": "demo",
    "source": {"dsn_env": "DEMO_PG_DSN"},
    "destination": {"token_env": "MOTHERDUCK_TOKEN", "database": "bronze_demo"},
    "replication": {"slot": "demo_slot", "publication": "demo_pub"},
    "control": {"extractor": "demo"},
}


def _m(**over):
    raw = {k: (dict(v) if isinstance(v, dict) else v) for k, v in BASE.items()}
    for k, v in over.items():
        raw[k] = {**raw[k], **v} if isinstance(v, dict) and isinstance(raw.get(k), dict) else v
    return parse(raw)


def rel(schema, table, *, pk=(), cols=None, uniq=(), ri="d"):
    cols = cols or [("id", 23, True), ("payload", 25, False)]
    return SourceRelation(
        schema=schema,
        table=table,
        oid=abs(hash((schema, table))) % 100000,
        columns=[SourceColumn(n, i + 1, o, -1, nn, "t") for i, (n, o, nn) in enumerate(cols)],
        primary_key=tuple(pk),
        unique_indexes=tuple(UniqueIndex(n, tuple(c)) for n, c in uniq),
        replica_identity=ri,
    )


def codes(plan, severity=None):
    return {p.code for p in plan.problems if severity is None or p.severity == severity}


def test_two_schemas_with_the_same_table_name_compile_separately():
    plan = sel.compile(_m(), [rel("a", "t", pk=["id"]), rel("b", "t", pk=["id"])])
    assert [r.target for r in plan.relations] == ["a.t", "b.t"]
    assert "target_collision" not in codes(plan)


def test_schema_mapping_that_collapses_two_schemas_is_an_error():
    m = _m(destination={"schema_mapping": {"a": "x", "b": "x"}})
    plan = sel.compile(m, [rel("a", "t", pk=["id"]), rel("b", "t", pk=["id"])])
    assert "target_collision" in codes(plan, "error")


def test_exclude_glob_narrows_and_include_tables_pattern_selects():
    disc = [rel("public", "orders", pk=["id"]), rel("public", "_dlt_loads", pk=["id"])]
    m = _m(source={"dsn_env": "DEMO_PG_DSN", "exclude": {"tables": ["public._dlt_*"]}})
    assert [r.table for r in sel.compile(m, disc).relations] == ["orders"]
    m2 = _m(source={"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["public"], "tables": ["public.o*"]}})
    assert [r.table for r in sel.compile(m2, disc).relations] == ["orders"]


def test_deny_hit_is_an_error_and_the_relation_is_dropped():
    m = _m(source={"dsn_env": "DEMO_PG_DSN", "deny": {"tables": ["public.access_token"]}})
    plan = sel.compile(m, [rel("public", "access_token", pk=["id"]), rel("public", "orders", pk=["id"])])
    assert plan.denied_hits == ["public.access_token"]
    assert "deny_hit" in codes(plan, "error")
    assert [r.table for r in plan.relations] == ["orders"]


def test_deny_only_fires_on_what_selection_actually_produced():
    """Catches the ordering bug where deny runs before the adapter narrows the set: a denied
    table the adapter never offers would fail validate on every registration that has a tripwire."""
    m = _m(
        source={
            "dsn_env": "DEMO_PG_DSN",
            "deny": {"tables": ["public.access_token"]},
            "discovery": {"adapter": "featherbase", "options": {}},
        }
    )
    disc = [rel("public", "access_token", pk=["id"]), rel("public", "orders", pk=["id"])]
    plan = sel.compile(m, disc, [AdapterRelation("public", "orders", "Core", None)])
    assert plan.denied_hits == []
    assert [r.table for r in plan.relations] == ["orders"]


def test_primary_key_wins_then_a_not_null_unique_index():
    plan = sel.compile(
        _m(replication={"replica_identity": "full"}),
        [
            rel("public", "with_pk", pk=["id"]),
            rel("public", "with_uniq", cols=[("code", 25, True), ("v", 25, False)], uniq=[("uq_code", ["code"])]),
        ],
    )
    by = {r.table: r for r in plan.relations}
    assert (by["with_pk"].key_policy, by["with_pk"].key) == (sel.PRIMARY_KEY, ("id",))
    assert (by["with_uniq"].key_policy, by["with_uniq"].key, by["with_uniq"].key_index) == (
        sel.UNIQUE_INDEX,
        ("code",),
        "uq_code",
    )


def test_a_nullable_unique_index_is_not_a_merge_key():
    """NULL = NULL is never true, so dlt's merge join would insert a duplicate every load."""
    r = rel("public", "t", cols=[("code", 25, False), ("v", 25, False)], uniq=[("uq_code", ["code"])])
    plan = sel.compile(_m(), [r])
    assert plan.relations == []
    assert "no_key_rejected" in codes(plan, "error")


def test_unique_index_key_without_full_identity_warns():
    r = rel("public", "t", cols=[("code", 25, True)], uniq=[("uq_code", ["code"])], ri="d")
    assert "unique_key_needs_identity" in codes(sel.compile(_m(), [r]), "warning")


def test_tables_without_key_append_only_keeps_the_table():
    r = rel("public", "events", cols=[("v", 25, False)])
    plan = sel.compile(_m(replication={"tables_without_key": "append_only"}), [r])
    assert [(x.table, x.key_policy) for x in plan.relations] == [("events", sel.APPEND_ONLY)]
    assert "no_key_append_only" in codes(plan, "warning")


def test_key_overrides_full_row_warns_about_cost():
    r = rel("public", "t", pk=["id"])
    plan = sel.compile(_m(replication={"key_overrides": {"public.t": "full_row"}}), [r])
    assert plan.relations[0].key_policy == sel.FULL_ROW
    assert plan.relations[0].key == ("id", "payload")
    assert "full_row_key_cost" in codes(plan, "warning")


def test_publication_diff_and_replica_identity_alters():
    m = _m(replication={"replica_identity": "full"})
    disc = [rel("public", "a", pk=["id"], ri="f"), rel("public", "b", pk=["id"], ri="d")]
    plan = sel.compile(m, disc, None, {("public", "b"), ("public", "gone")})
    assert plan.publication_add == [("public", "a")]
    assert plan.publication_drop == [("public", "gone")]
    assert plan.replica_identity_alters == [("public", "b")]


def test_missing_publication_makes_everything_an_add():
    add, drop = sel.diff_publication({("public", "a")}, None)
    assert (add, drop) == ([("public", "a")], [])


def test_selection_hash_is_order_independent_and_key_sensitive():
    a = sel.compile(_m(), [rel("public", "x", pk=["id"]), rel("public", "y", pk=["id"])])
    b = sel.compile(_m(), [rel("public", "y", pk=["id"]), rel("public", "x", pk=["id"])])
    assert a.selection_hash == b.selection_hash
    c = sel.compile(_m(replication={"key_overrides": {"public.x": "append_only"}}),
                    [rel("public", "x", pk=["id"]), rel("public", "y", pk=["id"])])
    assert c.selection_hash != a.selection_hash


@pytest.mark.parametrize(
    "source_id,schema,table,expected",
    [
        ("featherbase", "public", "background_job", "source_operational"),
        ("featherbase", "public", "job_execution", "source_operational"),
        ("featherbase", "public", "table_def", "source_system"),
        ("featherbase", "public", "user", "source_system"),
        ("featherbase", "public", "comment", "application"),
        ("featherbase", "public", "_dlt_loads", "loader_internal"),
        ("featherbase", "_cdc_runtime", "checkpoint", "loader_internal"),
        ("control_plane", "control", "run", "warehouse_control"),
        ("demo", "control", "run", "application"),
    ],
)
def test_table_class_defaults(source_id, schema, table, expected):
    assert sel.table_class(_m(source_id=source_id), schema, table) == expected


def test_manifest_classification_rule_beats_the_default():
    m = _m(classification={"rules": [{"match": "public.comment", "table_class": "source_system"}]})
    assert sel.table_class(m, "public", "comment") == "source_system"
