"""The exact-description rule of the column deny-list (#4346, Codex round 17), without a
database: Relation messages are built by hand, so a PG 17 sequence (a generated column whose
expression is dropped) can be exercised on any server version."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from feather_cdc_postgres.destination import Destination, DestinationSpec
from pg_replication.decoders import ColumnType
from pg_replication.helpers import MessageConsumer, ReviewedLayout

INT4, TEXT = 23, 25
OID = 4242


def _relation(*cols, relid=OID, name="person"):
    """A decoded pgoutput Relation message: (name, type oid) per column, every column flagged
    (REPLICA IDENTITY FULL)."""
    return SimpleNamespace(relation_id=relid, namespace="public", relation_name=name,
                           columns=[ColumnType(1, n, t, -1) for n, t in cols])


def _consumer(layout, current, **kw):
    """A consumer whose catalogue reads return `current` (a ReviewedLayout)."""
    c = MessageConsumer(upto_lsn=10**12, pub_ops={"insert": True, "update": True,
                                                  "delete": True, "truncate": True},
                        keys_by_relation={("public", "person"): ["id"]},
                        exclude_columns={("public", "person"): ["password_hash"]},
                        exclude_attnums_by_relid={OID: frozenset({4})},
                        approved_by_relid={OID: frozenset({"id", "safe", "email"})},
                        reviewed_layout_by_relid={OID: layout}, bound_relids={OID}, **kw)
    c._current_shape = lambda relid: (current, True)
    return c


def _cols(*spec):
    return tuple((a, n, t, -1, 1) for a, n, t in spec)


# PG 17 baseline: id(1), safe(2), gen(3, generated: not published), password_hash(4), email(5).
REVIEWED = ReviewedLayout(_cols((1, "id", INT4), (2, "safe", TEXT), (4, "password_hash", TEXT),
                                (5, "email", TEXT)), frozenset({3}))


def _judge(consumer, msg):
    consumer._withhold_unreviewed(msg, msg.relation_name)
    return consumer._unproven.get(msg.relation_id)


def test_the_reviewed_description_is_proven_and_the_denied_attnum_withheld():
    c = _consumer(REVIEWED, REVIEWED)
    msg = _relation(("id", INT4), ("safe", TEXT), ("password_hash", TEXT), ("email", TEXT))
    assert _judge(c, msg) is None
    assert "password_hash" in c.exclude_by_relid[OID] and "email" not in c.exclude_by_relid[OID]


def test_r17_drop_expression_then_renames_is_quarantined():
    """Codex round 17 finding 1: DROP EXPRESSION publishes `gen` (attnum 3) below reviewed
    attnums; then gen -> password_hash, password_hash -> email, email -> tail. Position 4 of
    the message is the secret (attnum 4) under the name `email`."""
    after = ReviewedLayout(_cols((1, "id", INT4), (2, "safe", TEXT), (3, "password_hash", TEXT),
                                 (4, "email", TEXT), (5, "tail", TEXT)))
    c = _consumer(REVIEWED, after)
    msg = _relation(("id", INT4), ("safe", TEXT), ("password_hash", TEXT), ("email", TEXT),
                    ("tail", TEXT))
    why = _judge(c, msg)
    assert why and "differs from the reviewed layout" in why


def test_r17_a_hidden_column_that_is_published_now_is_quarantined():
    """The catalogue half on its own: a description that reads like the review while a column
    the review saw hidden (generated) is published now cannot be trusted."""
    after = ReviewedLayout(REVIEWED.columns + ((3, "gen", TEXT, -1, 1),))
    c = _consumer(REVIEWED, after)
    msg = _relation(("id", INT4), ("safe", TEXT), ("password_hash", TEXT), ("email", TEXT))
    why = _judge(c, msg)
    assert why and "hidden at the review" in why


@pytest.mark.parametrize("cols", [
    [("id", INT4), ("safe", TEXT), ("password_hash", TEXT), ("email", TEXT), ("new", TEXT)],
    [("id", INT4), ("safe", TEXT), ("password_hash", TEXT)],               # a prefix
    [("id", INT4), ("safe", TEXT), ("email", TEXT), ("password_hash", TEXT)],
    [("id", INT4), ("safe", INT4), ("password_hash", TEXT), ("email", TEXT)],
])
def test_any_difference_from_the_reviewed_description_is_unproven(cols):
    """Added (even last), missing, reordered or retyped: all quarantine (Codex round 17)."""
    c = _consumer(REVIEWED, REVIEWED)
    assert _judge(c, _relation(*cols))


def test_a_key_flag_difference_is_unproven():
    c = _consumer(REVIEWED, REVIEWED)
    msg = _relation(("id", INT4), ("safe", TEXT), ("password_hash", TEXT), ("email", TEXT))
    msg.columns[1] = ColumnType(0, "safe", TEXT, -1)
    assert _judge(c, msg)


def test_a_layout_row_in_the_older_format_reads_as_no_layout():
    assert ReviewedLayout.from_json([[1, "id"], [2, "email"]]) is None
    assert ReviewedLayout.from_json(REVIEWED.to_json()) == REVIEWED


@pytest.fixture
def dest(tmp_path):
    return Destination(DestinationSpec(kind="duckdb", database="test",
                                       path=str(tmp_path / "d.duckdb"),
                                       pipelines_dir=str(tmp_path / "dlt")))


def test_r17_a_second_table_under_a_quarantined_name_gets_its_own_mark(dest):
    """Codex round 17 finding 2: marks were de-duplicated by NAME, so OID B taking the name of
    quarantined OID A got no mark and resumed without a resnapshot."""
    dest.record_quarantine("reg", {1001: ("public", "person", "first")})
    dest.record_quarantine("reg", {1002: ("public", "person", "second")})
    assert dest.read_quarantined_oids("reg") == {1001, 1002}
    dest.record_quarantine("reg", {1001: ("public", "member", "again")})   # same OID: kept once
    assert dest.read_quarantine("reg")[1001][:3] == ("public", "person", "first")


def test_r17_clearing_is_by_oid_not_by_name(dest):
    """Codex round 17 finding 3 (and its reverse): clearing B leaves A's mark under the same
    name; clearing A clears it under whatever name it was recorded."""
    dest.record_quarantine("reg", {1001: ("public", "person", "a"),
                                   1002: ("public", "person", "b")})
    dest.clear_quarantine("reg", [1002])
    assert dest.read_quarantined_oids("reg") == {1001}
    dest.clear_quarantine("reg", [1001])
    assert dest.read_quarantine("reg") == {}
