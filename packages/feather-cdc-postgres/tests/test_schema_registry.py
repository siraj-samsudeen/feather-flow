"""Type matrix and drift classification (#3079 D7)."""
from __future__ import annotations

import duckdb
import pytest

from pg_md_cdc import schema_registry as sr
from pg_md_cdc.discovery import SourceColumn, SourceRelation

# Every OID observed on the two live sources, plus the ones the pilot silently text-ed.
MATRIX = [
    (16, -1, "bool", sr.EXACT), (17, -1, "binary", sr.EXACT),
    (19, -1, "text", sr.EXACT_AS_TEXT), (20, -1, "bigint", sr.EXACT),
    (21, -1, "bigint", sr.WIDENED), (23, -1, "bigint", sr.WIDENED),
    (25, -1, "text", sr.EXACT), (26, -1, "bigint", sr.WIDENED),
    (114, -1, "json", sr.EXACT), (700, -1, "double", sr.WIDENED),
    (701, -1, "double", sr.EXACT), (1042, 14, "text", sr.EXACT),
    (1043, 54, "text", sr.EXACT), (1082, -1, "date", sr.EXACT),
    (1083, -1, "time", sr.EXACT), (1114, -1, "timestamp", sr.EXACT),
    (1184, -1, "timestamp", sr.EXACT), (1186, -1, "text", sr.LOSSY_TEXT),
    (1266, -1, "text", sr.LOSSY_TEXT), (1700, 655366, "decimal", sr.EXACT),
    (2950, -1, "text", sr.EXACT_AS_TEXT), (3802, -1, "json", sr.EXACT),
]


@pytest.mark.parametrize("oid,typmod,dest,mapping", MATRIX)
def test_type_matrix(oid, typmod, dest, mapping):
    ct = sr.compile_column(oid, typmod)
    assert (ct.dest_type, ct.mapping) == (dest, mapping)


def test_the_integer_family_is_pinned_to_bigint_not_integer():
    """Catches the live discrepancy in #3079 Appendix A: bronze_featherbase.public.comment.position
    is BIGINT while its staging twin is INTEGER, because dlt renders precision 32 as INTEGER.
    Dropping precision makes the snapshot and CDC paths agree, widening rather than narrowing."""
    for oid in (21, 23, 20, 26):
        ct = sr.compile_column(oid, -1)
        assert (ct.dest_type, ct.precision) == ("bigint", None)


def test_duckdb_renders_the_compiled_integer_as_bigint():
    """Proven against duckdb itself rather than asserted: precision 32 really does become
    INTEGER, which is why the compiler drops it."""
    from dlt.destinations import duckdb as duckdb_dest
    from dlt.destinations.impl.duckdb.factory import DuckDbTypeMapper

    tm = DuckDbTypeMapper(duckdb_dest().capabilities())
    assert tm.to_destination_type({"name": "c", "data_type": "bigint", "precision": 32}, None) == "INTEGER"
    assert tm.to_destination_type({"name": "c", "data_type": "bigint"}, None) == "BIGINT"
    con = duckdb.connect()
    con.execute("create table t (c BIGINT)")
    assert con.execute("select data_type from information_schema.columns where table_name='t'").fetchone()[0] == "BIGINT"


def test_numeric_carries_precision_and_scale():
    ct = sr.compile_column(1700, 655366)  # numeric(10,2)
    assert (ct.dest_type, ct.precision, ct.scale) == ("decimal", 10, 2)


def _rel(cols):
    return SourceRelation(
        schema="public",
        table="t",
        oid=1,
        columns=[SourceColumn(n, i + 1, o, tm, nn, "x", arr) for i, (n, o, tm, nn, arr) in enumerate(cols)],
    )


def test_array_and_unknown_oid_fall_back_to_text_with_a_named_warning():
    rel = _rel([("tags", 1009, -1, False, True), ("addr", 869, -1, False, False)])
    cols, problems = sr.compile_relation(rel)
    assert [c.compiled.dest_type for c in cols] == ["text", "text"]
    assert {c.compiled.mapping for c in cols} == {sr.TEXT_FALLBACK}
    msgs = " ".join(p.message for p in problems)
    assert "public.t.tags" in msgs and "public.t.addr" in msgs and "869" in msgs
    assert all(p.severity == "warning" for p in problems)


def test_a_declared_type_produces_no_fallback_warning():
    _, problems = sr.compile_relation(_rel([("id", 23, -1, True, False), ("body", 25, -1, False, False)]))
    assert problems == []


def _sig(cols, key=("id",), ri="f"):
    rel = SourceRelation(
        schema="public", table="t", oid=1, replica_identity=ri,
        columns=[SourceColumn(n, i + 1, o, tm, nn, "x") for i, (n, o, tm, nn) in enumerate(cols)],
    )
    return sr.signature(rel, key)


ID = ("id", 23, -1, True)


def test_add_nullable_column_is_additive():
    d = sr.classify_drift(_sig([ID]), _sig([ID, ("note", 25, -1, False)]))
    assert [(x.kind, x.severity) for x in d] == [("add_nullable_column", "additive")]


def test_add_not_null_column_is_flagged():
    d = sr.classify_drift(_sig([ID]), _sig([ID, ("note", 25, -1, True)]))
    assert [(x.kind, x.severity) for x in d] == [("add_not_null_column", "warning")]


def test_drop_column_is_breaking():
    d = sr.classify_drift(_sig([ID, ("a", 25, -1, False), ("b", 25, -1, False)]), _sig([ID, ("a", 25, -1, False)]))
    assert [(x.kind, x.severity) for x in d] == [("drop_column", "breaking")]


def test_one_dropped_one_added_of_the_same_type_reads_as_a_rename():
    d = sr.classify_drift(_sig([ID, ("old", 25, -1, False)]), _sig([ID, ("new", 25, -1, False)]))
    assert [(x.kind, x.column, x.severity) for x in d] == [("rename_column", "new", "warning")]


@pytest.mark.parametrize(
    "old,new",
    [((21, -1), (23, -1)), ((23, -1), (20, -1)), ((1043, 24), (1043, 54)), ((1043, 24), (25, -1)),
     ((700, -1), (701, -1)), ((1700, 655366), (1700, 786438))],
)
def test_widenings_are_additive(old, new):
    d = sr.classify_drift(_sig([ID, ("v", *old, False)]), _sig([ID, ("v", *new, False)]))
    assert [(x.kind, x.severity) for x in d] == [("type_widen", "additive")]


@pytest.mark.parametrize("old,new", [((20, -1), (23, -1)), ((25, -1), (23, -1)), ((1043, 54), (1043, 24))])
def test_narrowings_and_type_changes_are_breaking(old, new):
    d = sr.classify_drift(_sig([ID, ("v", *old, False)]), _sig([ID, ("v", *new, False)]))
    assert [(x.kind, x.severity) for x in d] == [("type_incompatible", "breaking")]


def test_key_change_is_breaking_and_replica_identity_change_is_info():
    d = sr.classify_drift(_sig([ID], key=("id",)), _sig([ID], key=("id", "tenant"), ri="d"))
    assert {(x.kind, x.severity) for x in d} == {("key_change", "breaking"), ("replica_identity_change", "info")}


def test_no_change_produces_no_drift():
    assert sr.classify_drift(_sig([ID]), _sig([ID])) == []
