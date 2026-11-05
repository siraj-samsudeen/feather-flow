"""Slot consumption in committed batches, grouped by source schema (#3079).

Yields `Batch`es the caller loads and only THEN checkpoints — the checkpoint is runtime-owned
(`_cdc_runtime.checkpoint`), never dlt resource state, so an LSN is never discarded before its
destination load commits. A transaction larger than `target_batch_size` still lands whole
(pgoutput never splits a commit). Kept from the #2851 pilot via the vendored consumer: true-PK
override, unchanged-TOAST splice, `lsn` dedup_sort desc, `deleted_ts` hard_delete. An
`append_only` relation merges on `lsn` instead (no key of its own), so a replay is exact.
Two review additions (#3079): a relation's transactions below its baseline boundary are dropped
(the baseline already holds them), and a TRUNCATE arrives on `Batch.truncates` for the runner
to apply BEFORE the batch's rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Sequence

from dlt.sources.credentials import ConnectionStringCredentials

from pg_replication.helpers import ItemGenerator, ReviewedLayout, advance_slot, get_max_lsn

Relation = tuple[str, str]


@dataclass
class Batch:
    """One drained batch: source schema -> [meta_item, marked_items, ...]."""

    items_by_namespace: dict[str, list]
    last_commit_lsn: int
    consumed_all: bool
    # TRUNCATEs in WAL order: {namespace, table, lsn, commit_lsn, commit_ts, applied}. Any rows
    # of a keyed relation that preceded its truncate in this batch are already gone from
    # `items_by_namespace`, so "delete, then load the batch" reproduces the source order.
    truncates: list = field(default_factory=list)
    # (schema, table) -> why a record's merge key could not be honoured (#4338); the runner
    # reports each as an error event.
    key_problems: dict = field(default_factory=dict)
    # (oid, schema, table, column) the decoder matched to a denied NAME (#4346); the runner
    # persists them before checkpointing so the deny follows that OID through a later rename.
    denied_seen: set = field(default_factory=set)
    # (schema, table) -> columns withheld because no reviewed signature names them (#4346).
    unreviewed: dict = field(default_factory=dict)
    # relation OID -> (schema, table, why) for each relation quarantined in this batch (#4346):
    # a change arrived under a description that did not match its reviewed layout; nothing
    # more of it loads. Keyed by OID (Codex round 17); the names are reporting metadata.
    quarantined: dict = field(default_factory=dict)

    @property
    def quarantined_names(self) -> set:
        """(schema, table) of every relation quarantined in this batch, for reports."""
        return {(s, t) for s, t, _why in self.quarantined.values()}

    @property
    def is_empty(self) -> bool:
        return not any(self.items_by_namespace.values()) and not self.truncates


def credentials(dsn: str) -> ConnectionStringCredentials:
    return ConnectionStringCredentials(dsn)


def max_lsn(dsn: str, slot: str, publication: str) -> int | None:
    """Highest LSN sitting in the slot, or None when the slot is empty. Does not consume."""
    return get_max_lsn(slot, _options(publication), credentials(dsn))


def _options(publication: str) -> dict[str, str]:
    return {"publication_names": publication, "proto_version": "1"}


def drain(
    dsn: str,
    slot: str,
    publication: str,
    start_lsn: int = 0,
    upto_lsn: int | None = None,
    *,
    target_batch_size: int = 1000,
    keys_by_relation: Mapping[Relation, Sequence[str]] | None = None,
    policies_by_relation: Mapping[Relation, str] | None = None,
    baseline_lsn_by_relation: Mapping[Relation, int] | None = None,
    deny_columns_by_relation: Mapping[Relation, Sequence[str]] | None = None,
    deny_columns_by_oid: Mapping[int, Sequence[str]] | None = None,
    approved_columns_by_relation: Mapping[Relation, Sequence[str]] | None = None,
    approved_columns_by_oid: Mapping[int, Sequence[str]] | None = None,
    deny_attnums_by_oid: Mapping[int, Sequence[int]] | None = None,
    reviewed_layout_by_oid: Mapping[int, ReviewedLayout] | None = None,
    bound_oids: Iterable[int] | None = None,
    quarantined_oids: Iterable[int] = (),
) -> Iterator[Batch]:
    """Iterate committed batches from `start_lsn` up to the slot's current max LSN.

    Does NOT advance the slot — `runner` does that after the load commits. Restarting at a
    checkpoint re-delivers the boundary commit (measured on PG 17); merge makes that harmless.
    `deny_columns_by_relation` (#4346) is dropped by name from every record and from the
    hints, so a denied column is never declared to dlt nor loaded. `deny_columns_by_oid` seeds
    the per-OID deny that follows a renamed table; it is shared by every batch of this drain.
    On a relation that carries a deny, each description must match its
    `reviewed_layout_by_oid` entry exactly (helpers `_prove_layout`); any difference, or no
    entry, quarantines it at its next change. `bound_oids` (the runner's: OIDs bound at run start or by a baseline)
    quarantines any other OID met under a denied name; None leaves that check off.
    `quarantined_oids` are skipped from the first batch on.
    """
    creds = credentials(dsn)
    options = _options(publication)
    if upto_lsn is None:
        upto_lsn = get_max_lsn(slot, options, creds)
    if upto_lsn is None:
        return
    cursor_lsn = start_lsn
    by_oid = {int(k): frozenset(v) for k, v in (deny_columns_by_oid or {}).items() if v}
    approved_by_oid = {int(k): frozenset(v) for k, v in (approved_columns_by_oid or {}).items()}
    attnums_by_oid = {int(k): frozenset(v) for k, v in (deny_attnums_by_oid or {}).items() if v}
    layouts = {int(k): v for k, v in (reviewed_layout_by_oid or {}).items()}
    bound = None if bound_oids is None else frozenset(int(o) for o in bound_oids)
    quarantined = {int(o) for o in quarantined_oids}   # shared: a quarantine is sticky
    while True:
        gen = ItemGenerator(
            credentials=creds,
            slot_name=slot,
            options=options,
            upto_lsn=upto_lsn,
            start_lsn=cursor_lsn,
            target_batch_size=target_batch_size,
            include_columns=None,
            columns=None,
            keys_by_relation=dict(keys_by_relation or {}),
            policies_by_relation=dict(policies_by_relation or {}),
            baseline_lsn_by_relation=dict(baseline_lsn_by_relation or {}),
            exclude_columns={k: tuple(v) for k, v in (deny_columns_by_relation or {}).items()},
            exclude_columns_by_relid=by_oid,
            approved_columns={k: frozenset(v)
                              for k, v in (approved_columns_by_relation or {}).items()},
            approved_by_relid=approved_by_oid,
            exclude_attnums_by_relid=attnums_by_oid,
            reviewed_layout_by_relid=layouts,
            bound_relids=bound,
            quarantined_relids=quarantined,
        )
        grouped = gen.collect()
        if gen.last_commit_lsn is None:
            # Backstop (#4345): the consumer stops only at a commit, so this cannot happen
            # today. If it ever does, the batch is a partial transaction and there is no LSN to
            # restart at — fail before the runner loads it, never loop on a `None` restart.
            raise RuntimeError(
                f"slot {slot}: replication stopped without reaching a commit (restarted at LSN "
                f"{cursor_lsn}); refusing to load a partial transaction — see issue #4345")
        yield Batch(grouped, gen.last_commit_lsn, gen.generated_all,
                    truncates=list(gen.truncates),
                    key_problems=dict(getattr(gen, "key_problems", {}) or {}),
                    denied_seen=set(getattr(gen, "denied_seen", set()) or ()),
                    unreviewed=dict(getattr(gen, "unreviewed", {}) or {}),
                    quarantined=dict(getattr(gen, "quarantined", {}) or {}))
        if gen.generated_all:
            return
        cursor_lsn = gen.last_commit_lsn


def advance(dsn: str, slot: str, upto_lsn: int) -> None:
    """Flush the slot up to an LSN whose destination load has ALREADY committed."""
    advance_slot(upto_lsn, slot, credentials(dsn))
