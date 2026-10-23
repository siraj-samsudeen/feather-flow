"""The key-only delete a primary-key-changing UPDATE emits (#4338, pg_replication patch)."""
from __future__ import annotations

from types import SimpleNamespace

import pendulum

from pg_replication.decoders import ColumnData, TupleData
from pg_replication.helpers import MessageConsumer

COLUMNS = {
    "id": {"name": "id", "data_type": "text", "nullable": False, "primary_key": True},
    "name": {"name": "name", "data_type": "text", "nullable": True},
    "secret": {"name": "secret", "data_type": "text", "nullable": True},
}


def _consumer(policy="primary_key"):
    c = object.__new__(MessageConsumer)
    c.last_table_schema = {1: {"name": "person", "namespace": "featherbase", "columns": COLUMNS}}
    c.policies_by_relation = {("featherbase", "person"): policy}
    c.last_commit_ts = pendulum.datetime(2026, 10, 23, tz="UTC")
    c.key_problems = {}
    return c


def _update(old_id, identifier="O"):
    old = TupleData(3, [ColumnData("t", len(old_id), old_id), ColumnData("t", 4, "Asha"),
                        ColumnData("t", 6, "s3cr3t")])
    return SimpleNamespace(relation_id=1, optional_tuple_identifier=identifier, old_tuple=old)


def test_a_key_change_emits_a_tombstone_carrying_only_the_old_key_and_delete_metadata():
    new_item = {"id": "psn_a", "name": "Asha", "secret": "s3cr3t", "lsn": 10}
    tomb = _consumer()._old_key_delete(_update("E001"), new_item, 10)
    assert tomb == {"id": "E001", "lsn": 10, "deleted_ts": pendulum.datetime(2026, 10, 23, tz="UTC")}
    assert "secret" not in tomb and "name" not in tomb


def test_no_tombstone_when_the_key_is_unchanged_or_the_relation_is_append_only():
    new_item = {"id": "E001", "name": "Asha K", "secret": "s3cr3t", "lsn": 10}
    assert _consumer()._old_key_delete(_update("E001"), new_item, 10) is None
    moved = {"id": "psn_a", "name": "Asha", "secret": "s3cr3t", "lsn": 10}
    assert _consumer("append_only")._old_key_delete(_update("E001"), moved, 10) is None
    assert _consumer()._old_key_delete(_update("E001", identifier=None), moved, 10) is None


def test_a_key_tuple_without_the_merge_key_never_becomes_a_dummy_tombstone():
    """Codex adversarial round 4: a 'K' old tuple carries the REPLICA IDENTITY columns, which
    can be a secondary unique index — the merge key arrives as null ('n'). Converting that
    would tombstone a dummy key and could delete an unrelated row: skip it and report."""
    old = TupleData(3, [ColumnData("n"), ColumnData("t", 4, "Asha"), ColumnData("n")])
    msg = SimpleNamespace(relation_id=1, optional_tuple_identifier="K", old_tuple=old)
    c = _consumer()
    moved = {"id": "psn_a", "name": "Asha", "secret": None, "lsn": 10}
    assert c._old_key_delete(msg, moved, 10) is None
    assert ("featherbase", "person") in c.key_problems
