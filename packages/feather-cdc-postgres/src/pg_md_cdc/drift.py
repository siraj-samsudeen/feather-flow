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
from .lifecycle import DRIFT_REVIEW, RESNAPSHOT_REQUIRED, Lifecycle
from .schema_registry import TEXT_FALLBACK, classify_drift, compile_relation, signature

KEY_CHANGE = "key_change"
# Reported and marked, but the table keeps loading (Appendix C: dlt never destroys a column).
BREAKING_KINDS = frozenset({"drop_column", "rename_column", "type_incompatible"})


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
    sig = signature(src, tuple(rel.key))
    cfg.destination.write_signature(cfg.registration, rel.schema, rel.table, sig)
    return sig


def scan(cfg, relations, *, run, lc: Lifecycle, owner,
         manifest_path: str = "<manifest>") -> DriftOutcome:
    """Compile every desired relation, persist the registry, and act on the diff vs last run."""
    cfg.destination.ensure_runtime_tables()
    stored = cfg.destination.read_signatures(cfg.registration)
    rows, sigs = _compile_all(cfg, relations, run=run, sources=_sources(cfg))
    cfg.destination.write_registry_rows(rows)
    out = DriftOutcome()

    for rel in relations:
        pair = (rel.schema, rel.table)
        new_sig = sigs.get(pair)
        if new_sig is None:
            continue
        old_sig = stored.get(pair)
        drifts = classify_drift(old_sig, new_sig) if old_sig is not None else []
        if not drifts:
            # Identical → nothing to write: each write is a MotherDuck round trip, and fifty of
            # them every tick for a schema that did not move is the cost of an idle registration.
            if old_sig != new_sig:
                cfg.destination.write_signature(cfg.registration, rel.schema, rel.table, new_sig)
            continue

        out.drifts[pair] = drifts
        kinds = {d.kind for d in drifts}
        payload = [{"kind": d.kind, "column": d.column, "severity": d.severity,
                    "detail": d.detail} for d in drifts]

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
