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
from typing import Iterator, Mapping, Sequence

from dlt.sources.credentials import ConnectionStringCredentials

from pg_replication.helpers import ItemGenerator, advance_slot, get_max_lsn

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
) -> Iterator[Batch]:
    """Iterate committed batches from `start_lsn` up to the slot's current max LSN.

    Does NOT advance the slot — `runner` does that after the load commits. Restarting at a
    checkpoint re-delivers the boundary commit (measured on PG 17); merge makes that harmless.
    """
    creds = credentials(dsn)
    options = _options(publication)
    if upto_lsn is None:
        upto_lsn = get_max_lsn(slot, options, creds)
    if upto_lsn is None:
        return
    cursor_lsn = start_lsn
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
        )
        grouped = gen.collect()
        yield Batch(grouped, gen.last_commit_lsn or cursor_lsn, gen.generated_all,
                    truncates=list(gen.truncates),
                    key_problems=dict(getattr(gen, "key_problems", {}) or {}))
        if gen.generated_all:
            return
        cursor_lsn = gen.last_commit_lsn


def advance(dsn: str, slot: str, upto_lsn: int) -> None:
    """Flush the slot up to an LSN whose destination load has ALREADY committed."""
    advance_slot(upto_lsn, slot, credentials(dsn))
