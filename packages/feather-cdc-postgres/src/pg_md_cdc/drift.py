"""Per-run schema registry + drift policy (#3079 D7, plan Deviation D-3).

Constraints:
  - a key_change deliberately does NOT refresh the stored signature, so every later run
    re-detects it and keeps the table out of the publication until `resnapshot` refreshes it;
  - breaking non-key drift never stops the drain — one ordered slot cannot skip one relation,
    and dlt's evolution is additive, so continuing is non-destructive;
  - a `text_fallback` mapping is always an event, never a silent cast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from control_plane import status as cp_status

from . import discovery, publication
from .lifecycle import (
    DISCOVERED, DRIFT_REVIEW, RESNAPSHOT_REQUIRED, RETIRED, SNAPSHOTTING, Lifecycle,
)
from .schema_registry import (
    TEXT_FALLBACK, classify_drift, compile_relation, project, project_signature, signature,
)

KEY_CHANGE = "key_change"
# Reported and marked, but the table keeps loading (Appendix C: dlt never destroys a column).
BREAKING_KINDS = frozenset({"drop_column", "rename_column", "type_incompatible"})
# A column the reviewed signature does not name (#4346): on a table with denied columns it is
# withheld until an operator decides — it may be the secret renamed.
NEW_COLUMN_KINDS = frozenset({"add_nullable_column", "add_not_null_column", "rename_column"})
# States whose next step this tick is a full baseline (runner._adopt_streaming routes a
# re-selected RETIRED or DISCOVERED relation to SNAPSHOTTING; an unpublished one is added and
# baselined).
BASELINE_PENDING = frozenset({SNAPSHOTTING, RETIRED, DISCOVERED})


def remedy(manifest_path: str, qualified: str) -> str:
    return f"python -m pg_md_cdc resnapshot {manifest_path} --tables {qualified} --yes"


@dataclass
class DriftOutcome:
    """`blocked` is out of the publication for this run and every run until a resnapshot."""

    blocked: set = field(default_factory=set)
    review: set = field(default_factory=set)
    drifts: dict = field(default_factory=dict)

    def active(self, relations: Sequence) -> list:
        return [r for r in relations if (r.schema, r.table) not in self.blocked]


def _sources(cfg) -> dict:
    return discovery.relation_map(discovery.discover(cfg.source_dsn))


def _compile_all(cfg, relations, *, run, sources) -> tuple[list[dict], dict]:
    """Registry rows + fresh signature per relation; every text_fallback column is an event."""
    rows: list[dict] = []
    sigs: dict = {}
    for rel in relations:
        src = sources.get(rel.qualified)
        if src is None:
            run.event(entity=rel.qualified, status=cp_status.EV_ERROR, unit="schema_drift",
                      error_detail="selected relation does not exist at the source")
            continue
        key = tuple(rel.key)
        denied = _denied(cfg, rel, src)
        src = project(src, denied)   # #4346: a denied column is not part of what lands
        cols, _problems = compile_relation(src, key)
        rows += [{"registration": cfg.registration, "schema": rel.schema, "table": rel.table,
                  "column": c.name, "type_oid": c.type_oid, "typmod": c.typmod,
                  "not_null": c.not_null, "key_role": c.key_role,
                  "dest_type": c.compiled.dest_type, "mapping": c.compiled.mapping}
                 for c in cols]
        fallback = [c.name for c in cols if c.compiled.mapping == TEXT_FALLBACK]
        if fallback:
            run.event(entity=rel.qualified, status=cp_status.EV_OK, unit="schema_registry",
                      extra={"text_fallback_columns": fallback,
                             "detail": "no declared OID mapping — landing as text"})
        sigs[(rel.schema, rel.table)] = signature(src, key)
    return rows, sigs


def _denied(cfg, rel, src=None) -> tuple:
    """The relation's column deny-list (#4346): the compiled Relation's plus the manifest map
    the runtime carries (both come from `source.deny.columns`; a relation built outside the
    compiler carries none of its own) — plus, given the catalogue relation `src`, every column
    whose attnum is a denied one: a denied column renamed after the run resolved its names
    must not be recorded as a reviewed, approved column (Codex round 12)."""
    own = set(getattr(rel, "deny_columns", ()) or ())
    own |= set((getattr(cfg, "deny_columns", None) or {}).get((rel.schema, rel.table), ()))
    if src is not None:
        attnums = set((getattr(cfg, "deny_attnums", None) or {}).get(src.oid, ()))
        own |= {c.name for c in src.columns if c.attnum in attnums}
    return tuple(sorted(own))


def record_all(cfg, relations, *, run) -> dict:
    """Registry + signatures with NO diff — what a full (re-)snapshot leaves behind."""
    cfg.destination.ensure_runtime_tables()
    rows, sigs = _compile_all(cfg, relations, run=run, sources=_sources(cfg))
    cfg.destination.write_registry_rows(rows)
    for (schema, table), sig in sigs.items():
        cfg.destination.write_signature(cfg.registration, schema, table, sig)
    return sigs


def record_signature(cfg, rel, *, run=None) -> dict | None:
    """Refresh ONE relation's signature — what `resnapshot` calls to clear a key_change."""
    src = _sources(cfg).get(rel.qualified)
    if src is None:
        return None
    sig = signature(src, tuple(rel.key), _denied(cfg, rel, src))
    cfg.destination.write_signature(cfg.registration, rel.schema, rel.table, sig)
    return sig


def scan(cfg, relations, *, run, lc: Lifecycle, owner,
         manifest_path: str = "<manifest>") -> DriftOutcome:
    """Compile every desired relation, persist the registry, and act on the diff vs last run."""
    cfg.destination.ensure_runtime_tables()
    stored = cfg.destination.read_signatures(cfg.registration)
    sources = _sources(cfg)
    rows, sigs = _compile_all(cfg, relations, run=run, sources=sources)
    cfg.destination.write_registry_rows(rows)
    out = DriftOutcome()

    for rel in relations:
        pair = (rel.schema, rel.table)
        new_sig = sigs.get(pair)
        if new_sig is None:
            continue
        # Projected like the new one: a signature stored before a deny was added still names the
        # column, and must not read as a breaking `drop_column` (#4346).
        old_sig = project_signature(stored.get(pair),
                                    _denied(cfg, rel, sources.get(rel.qualified)))
        drifts = classify_drift(old_sig, new_sig) if old_sig is not None else []
        if not drifts:
            # Identical → nothing to write: each write is a MotherDuck round trip, and fifty of
            # them every tick for a schema that did not move is the cost of an idle registration.
            if stored.get(pair) != new_sig:
                cfg.destination.write_signature(cfg.registration, rel.schema, rel.table, new_sig)
            continue

        out.drifts[pair] = drifts
        kinds = {d.kind for d in drifts}
        payload = [{"kind": d.kind, "column": d.column, "severity": d.severity,
                    "detail": d.detail} for d in drifts]

        if lc.state_of(rel.schema, rel.table) in BASELINE_PENDING:
            # A full baseline is already due this tick (#4338: a re-selected retired table, a
            # table renamed at the source, an interrupted snapshot): it rebuilds the copy in the
            # new shape, which IS the remedy. Blocking or reviewing it would also need a
            # lifecycle edge `snapshotting` does not have, and abort the whole tick.
            cfg.destination.write_signature(cfg.registration, rel.schema, rel.table, new_sig)
            run.event(entity=rel.qualified, status=cp_status.EV_OK, unit="schema_drift",
                      extra={"drifts": payload, "action": "superseded_by_baseline"})
            continue

        if KEY_CHANGE in kinds:
            # The OLD signature stands: the block has to survive a restart, and the only thing
            # that clears it is a resnapshot rebuilding the table under the new key.
            out.blocked.add(pair)
            run.event(entity=rel.qualified, status=cp_status.EV_ERROR, unit="schema_drift",
                      error_detail="merge key changed — removed from the publication",
                      extra={"drifts": payload, "action": "publication_removed",
                             "remedy": remedy(manifest_path, rel.qualified)})
            lc.ensure(rel.schema, rel.table, RESNAPSHOT_REQUIRED, "key change")
            continue

        added = kinds & NEW_COLUMN_KINDS
        if added and _denied(cfg, rel, sources.get(rel.qualified)):
            # Fail closed on a table that carries a deny (#4346, Sonnet F1): a new or renamed
            # column may be the secret under another name. The OLD signature stands — it is the
            # reviewed shape the decoder withholds everything else against — until an operator
            # denies the column or resnapshots to approve it.
            new_cols = sorted(d.column for d in drifts if d.kind in NEW_COLUMN_KINDS and d.column)
            out.review.add(pair)
            # unit `deny_columns`, every tick until resolved: the daily-loading privacy alarm
            # reads that unit, and must stay red while the review is pending (Codex round 12).
            run.event(entity=rel.qualified, status=cp_status.EV_ERROR, unit="deny_columns",
                      error_detail=(f"new or renamed column(s) {', '.join(new_cols)} on a table "
                                    "with denied columns — withheld until reviewed"),
                      extra={"drifts": payload, "action": "withheld_pending_review",
                             "remedy": "add the column(s) to source.deny.columns, or "
                                       + remedy(manifest_path, rel.qualified) + " to approve"})
            lc.ensure(rel.schema, rel.table, DRIFT_REVIEW, "new column on a denied table")
            continue
        cfg.destination.write_signature(cfg.registration, rel.schema, rel.table, new_sig)
        breaking = kinds & BREAKING_KINDS
        if breaking:
            out.review.add(pair)
            run.event(entity=rel.qualified, status=cp_status.EV_ERROR, unit="schema_drift",
                      error_detail=f"breaking drift: {', '.join(sorted(breaking))}",
                      extra={"drifts": payload, "action": "kept_loading"})
            lc.ensure(rel.schema, rel.table, DRIFT_REVIEW, ", ".join(sorted(breaking)))
        else:
            run.event(entity=rel.qualified, status=cp_status.EV_OK, unit="schema_drift",
                      extra={"drifts": payload, "action": "auto_evolved"})

    if out.blocked:
        owner.assert_held()
        publication.reconcile(cfg.source_dsn, cfg.publication,
                              [(r.schema, r.table) for r in relations
                               if (r.schema, r.table) not in out.blocked])
    return out
