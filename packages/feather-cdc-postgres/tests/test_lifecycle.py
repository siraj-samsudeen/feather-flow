"""Lifecycle transitions are validated, not advisory (#3079 D5)."""
from __future__ import annotations

import pytest

from pg_md_cdc.lifecycle import (
    CATCHING_UP, DISCOVERED, DRIFT_REVIEW, IllegalTransition, Lifecycle, RESNAPSHOT_REQUIRED,
    RETIRED, SNAPSHOTTING, STREAMING,
)


class FakeDestination:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def read_lifecycle(self, registration):
        return dict(self.rows)

    def write_lifecycle(self, registration, schema, table, state, detail=None):
        self.rows[(schema, table)] = state
        self.writes.append((registration, schema, table, state, detail))


class FakeRun:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


@pytest.fixture
def lc():
    return Lifecycle(FakeDestination(), "reg", FakeRun())


def test_happy_path_to_streaming(lc):
    lc.transition("public", "t", SNAPSHOTTING)
    lc.transition("public", "t", CATCHING_UP)
    lc.transition("public", "t", STREAMING)
    assert lc.state_of("public", "t") == STREAMING
    units = [e["unit"] for e in lc.run.events]
    assert units == ["lifecycle"] * 3
    assert lc.run.events[-1]["extra"] == {"from": CATCHING_UP, "to": STREAMING, "detail": None}


def test_streaming_cannot_jump_back_to_snapshotting(lc):
    lc.transition("public", "t", SNAPSHOTTING)
    lc.transition("public", "t", STREAMING)
    with pytest.raises(IllegalTransition):
        lc.transition("public", "t", SNAPSHOTTING)
    lc.transition("public", "t", RESNAPSHOT_REQUIRED, "slot lost")
    lc.transition("public", "t", SNAPSHOTTING)


def test_a_new_relation_starts_discovered_snapshotting_or_adopted_as_streaming(lc):
    """`streaming` from nothing is the ADOPTION edge (runner._adopt_streaming): a table already
    in the publication was baselined by whoever ran before us. Everything else stays illegal."""
    with pytest.raises(IllegalTransition):
        lc.transition("public", "t", RESNAPSHOT_REQUIRED)
    lc.transition("public", "t", STREAMING, "adopted")
    lc.transition("a", "t", DISCOVERED)


def test_retired_is_re_admitted_through_discovered(lc):
    lc.transition("public", "t", SNAPSHOTTING)
    lc.transition("public", "t", STREAMING)
    lc.transition("public", "t", RETIRED, "narrowed selection")
    with pytest.raises(IllegalTransition):
        lc.transition("public", "t", STREAMING)
    lc.transition("public", "t", DISCOVERED, "re-widened")


def test_drift_review_has_no_edge_back_to_streaming(lc):
    """A run that merely sees no FURTHER drift must not clear a reported break. The only way
    out is `resnapshot` — resnapshot_required → snapshotting → streaming (#3079 D7)."""
    lc.transition("public", "t", SNAPSHOTTING)
    lc.transition("public", "t", STREAMING)
    lc.transition("public", "t", DRIFT_REVIEW, "column dropped")
    with pytest.raises(IllegalTransition):
        lc.transition("public", "t", STREAMING, "no further drift")
    lc.transition("public", "t", RESNAPSHOT_REQUIRED, "operator resnapshot")
    lc.transition("public", "t", SNAPSHOTTING)
    lc.transition("public", "t", STREAMING)


def test_unknown_state_is_rejected(lc):
    with pytest.raises(IllegalTransition):
        lc.transition("public", "t", "quarantined")


def test_ensure_is_idempotent(lc):
    lc.ensure("public", "t", SNAPSHOTTING)
    lc.ensure("public", "t", SNAPSHOTTING)
    assert len(lc.destination.writes) == 1


def test_two_schemas_with_the_same_table_name_have_separate_states(lc):
    lc.transition("a", "t", SNAPSHOTTING)
    lc.transition("b", "t", DISCOVERED)
    assert lc.state_of("a", "t") == SNAPSHOTTING
    assert lc.state_of("b", "t") == DISCOVERED
