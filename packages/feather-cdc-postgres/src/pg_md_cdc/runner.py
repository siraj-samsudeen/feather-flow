"""The run/status/resnapshot engine (#3079 Phase 2).

Correctness invariants this file exists to hold:
  - the advisory owner session is asserted before EVERY mutating phase; losing it aborts;
  - the checkpoint is written only after every schema's load has committed, and the slot is
    advanced only to a checkpointed LSN — so an ambiguous destination outcome replays, never skips;
  - relation identity is (schema, table) end to end, one dlt pipeline per source schema;
  - a key-changed relation is out of the desired set for the WHOLE run (drift.scan), so the
    drain, the reconcile counts and the probe all see the same active set.
Contract with the compiler half: manifest.Manifest / selectors.Relation (plan Appendix B).
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Sequence

from control_plane import ControlPlane, status as cp_status

from . import drift, publication, snapshot, stream, telemetry
from .destination import Destination, DestinationSpec
from .lifecycle import (
    CATCHING_UP, DISCOVERED, RESNAPSHOT_REQUIRED, RETIRED, SNAPSHOTTING, STREAMING, Lifecycle,
)
from .ownership import OwnerSession, OwnershipLost


@dataclass
class RuntimeConfig:
    registration: str
    source_id: str
    source_dsn: str
    slot: str
    publication: str
    extractor: str
    destination: Destination
    schema_map: dict = field(default_factory=dict)   # source schema -> target schema
    pipeline_name: str | None = None
    target_batch_size: int = 1000
    replica_identity: str = "full"
    slot_loss_policy: str = "resnapshot"
    retirement_policy: str = "freeze"
    reconcile_threshold: int = telemetry.RECONCILE_MAX_ROWS
    manifest_path: str = "<manifest>"   # only ever interpolated into operator remedy text

    def target_schema(self, source_schema: str) -> str:
        return self.schema_map.get(source_schema, source_schema)


def _log(msg: str) -> None:
    print(f"[pg_md_cdc] {msg}", flush=True)


# --------------------------------------------------------------------------- pipelines


class _Pipelines:
    """One dlt pipeline per SOURCE schema, created lazily and reused across batches."""

    def __init__(self, cfg: RuntimeConfig, source_schemas: Sequence[str]) -> None:
        self.cfg = cfg
        self._cache: dict[str, object] = {}
        # An explicit pipeline_name pins ONE pipeline; that is only unambiguous for a
        # single-schema registration (Featherbase's `featherbase_cdc`).
        self._pin = cfg.pipeline_name if len(set(source_schemas)) <= 1 else None

    def get(self, source_schema: str):
        if source_schema not in self._cache:
            self._cache[source_schema] = self.cfg.destination.pipeline_for_schema(
                source_schema, self.cfg.target_schema(source_schema),
                self._pin, source_id=self.cfg.source_id)
        return self._cache[source_schema]


def _load_ids(info) -> list[str]:
    try:
        return [p.load_id for p in info.load_packages]
    except Exception:  # noqa: BLE001 — telemetry, never correctness
        return []


# --------------------------------------------------------------------------- phases


def _to_snapshotting(lc: Lifecycle, schema: str, table: str, detail: str) -> None:
    """A table already streaming must pass through resnapshot_required — the state history is
    the only place an operator can see WHY a baseline was retaken."""
    current = lc.state_of(schema, table)
    if current in (STREAMING, CATCHING_UP, DISCOVERED):
        lc.transition(schema, table, RESNAPSHOT_REQUIRED, detail)
    elif current == RETIRED:
        lc.transition(schema, table, DISCOVERED, detail)
    lc.ensure(schema, table, SNAPSHOTTING, detail)


def _emit_table_events(run, pipeline, unit: str, tables: set[str], extra: dict | None = None) -> int:
    counts = Destination.row_counts(pipeline)
    n = 0
    for table, rows in counts.items():
        if tables and table not in tables:
            continue
        run.event(entity=table, status=cp_status.EV_OK, unit=unit, rows=rows, extra=extra)
        n += 1
    return n


def _initialize(cfg: RuntimeConfig, relations, *, run, owner, lc: Lifecycle,
                pipelines: _Pipelines, reason: str) -> int:
    """Slot missing or lost: reconcile the publication FIRST, then create the slot with an
    exported snapshot, then stream the baseline out of that snapshot (no source-side staging)."""
    owner.assert_held()
    desired = [(r.schema, r.table) for r in relations]
    added, dropped = publication.reconcile(cfg.source_dsn, cfg.publication, desired)
    _log(f"init ({reason}): publication +{len(added)} -{len(dropped)}")
    publication.ensure_replica_identity(cfg.source_dsn, desired, cfg.replica_identity)

    for r in relations:
        _to_snapshotting(lc, r.schema, r.table, reason)

    owner.assert_held()
    handle = snapshot.create_slot_with_snapshot(cfg.source_dsn, cfg.slot)
    keys = {(r.schema, r.table): list(r.key) for r in relations}
    policies = {(r.schema, r.table): r.key_policy for r in relations}
    try:
        with snapshot.snapshot_resources(cfg.source_dsn, handle.snapshot_name, desired,
                                         keys, key_policies=policies) as by_schema:
            handle.close()  # the import already happened; the exporter is no longer needed
            for source_schema, resources in by_schema.items():
                owner.assert_held()
                pipe = pipelines.get(source_schema)
                pipe.run(resources)
                _emit_table_events(run, pipe, "snapshot",
                                   {t for s, t in desired if s == source_schema},
                                   {"reinit_reason": reason})
    finally:
        handle.close()

    cfg.destination.write_checkpoint(cfg.registration, cfg.slot, handle.consistent_point_lsn)
    for r in relations:
        lc.ensure(r.schema, r.table, CATCHING_UP, reason)
        lc.ensure(r.schema, r.table, STREAMING, reason)
    return handle.consistent_point_lsn


def _baseline(cfg: RuntimeConfig, rel, *, run, owner, lc: Lifecycle, pipelines: _Pipelines,
              detail: str) -> None:
    """Full read of a table just ADDED to the publication. The pub-add is already done, so a
    concurrent change also arrives via CDC and merge dedups it — the convergence argument."""
    owner.assert_held()
    if lc.state_of(rel.schema, rel.table) == RETIRED:
        lc.transition(rel.schema, rel.table, DISCOVERED, detail)
    lc.ensure(rel.schema, rel.table, DISCOVERED, detail)
    lc.ensure(rel.schema, rel.table, CATCHING_UP, detail)
    pipe = pipelines.get(rel.schema)
    with snapshot.baseline_resource(cfg.source_dsn, (rel.schema, rel.table), list(rel.key),
                                    key_policy=rel.key_policy) as resource:
        pipe.run(resource)
    _emit_table_events(run, pipe, "backfill", {rel.table}, {"detail": detail})
    lc.ensure(rel.schema, rel.table, STREAMING, detail)


def _reconcile_selection(cfg: RuntimeConfig, relations, *, run, owner, lc: Lifecycle,
                         pipelines: _Pipelines, blocked: set | None = None) -> tuple[list, list]:
    owner.assert_held()
    desired = [(r.schema, r.table) for r in relations]
    by_key = {(r.schema, r.table): r for r in relations}
    added, dropped = publication.reconcile(cfg.source_dsn, cfg.publication, desired)
    if added:
        publication.ensure_replica_identity(cfg.source_dsn, added, cfg.replica_identity)
        for key in added:
            _baseline(cfg, by_key[key], run=run, owner=owner, lc=lc, pipelines=pipelines,
                      detail="added to selection")
    for schema, table in dropped:
        if (schema, table) in (blocked or set()):
            continue   # out of the publication for drift, not retired from the selection
        # `freeze`: the destination copy stays as a frozen mirror — bronze never deletes history.
        lc.ensure(schema, table, RETIRED, f"dropped from selection ({cfg.retirement_policy})")
    if dropped:
        _log(f"dropped from publication: {dropped} (retirement={cfg.retirement_policy})")
    publication.ensure_replica_identity(cfg.source_dsn, desired, cfg.replica_identity)
    return added, dropped


def _drain(cfg: RuntimeConfig, relations, *, run, owner, pipelines: _Pipelines) -> dict:
    """Batch loop. The checkpoint moves only after every namespace in the batch committed;
    the slot is advanced only to a checkpointed LSN."""
    keys = {(r.schema, r.table): list(r.key) for r in relations}
    checkpoint = cfg.destination.read_checkpoint(cfg.registration)
    start_lsn = checkpoint[0] if checkpoint else 0

    probe = telemetry.slot_probe(cfg.source_dsn, cfg.slot) or {}
    confirmed = probe.get("confirmed_flush_int")
    if start_lsn and confirmed is not None and confirmed < start_lsn:
        # A crash between "load committed" and "slot advanced" leaves the slot behind the
        # checkpoint; re-assert it so the drain does not replay work already durable.
        stream.advance(cfg.source_dsn, cfg.slot, start_lsn)

    batches = rows = 0
    for batch in stream.drain(cfg.source_dsn, cfg.slot, cfg.publication, start_lsn,
                              target_batch_size=cfg.target_batch_size,
                              keys_by_relation=keys):
        owner.assert_held()
        load_ids: list[str] = []
        for source_schema, items in batch.items_by_namespace.items():
            if not items:
                continue
            pipe = pipelines.get(source_schema)
            info = Destination.load(pipe, items, resource_name=cfg.slot)
            load_ids += _load_ids(info)
            rows += sum(Destination.row_counts(pipe).values())
            _emit_table_events(run, pipe, "cdc", set())
        if batch.last_commit_lsn:
            cfg.destination.write_checkpoint(cfg.registration, cfg.slot,
                                             batch.last_commit_lsn, load_ids)
            stream.advance(cfg.source_dsn, cfg.slot, batch.last_commit_lsn)
        batches += 1
    return {"batches": batches, "rows": rows, "start_lsn": start_lsn}


# --------------------------------------------------------------------------- entry points


def run_relations(cfg: RuntimeConfig, relations, *, run, owner) -> dict:
    """One full drain cycle for an already-compiled relation set. Callers hold the owner lock."""
    t0 = time.monotonic()
    relations = list(relations)
    cfg.destination.ensure_runtime_tables()
    lc = Lifecycle(cfg.destination, cfg.registration, run)
    pipelines = _Pipelines(cfg, [r.schema for r in relations])

    slot = telemetry.slot_probe(cfg.source_dsn, cfg.slot)
    lost = slot is not None and slot.get("wal_status") == "lost"
    if (slot is None or lost) and cfg.slot_loss_policy == "pause" and slot is not None:
        for r in relations:
            lc.ensure(r.schema, r.table, RESNAPSHOT_REQUIRED, "slot lost; slot_loss_policy=pause")
        run.event(entity="_slot", status=cp_status.EV_ERROR, unit="probe",
                  error_detail="slot lost and slot_loss_policy=pause", extra=slot)
        return {"paused": True}

    blocked: set = set()
    if slot is None or lost:
        reason = "first run" if slot is None else f"slot invalidated ({slot.get('wal_status')})"
        if lost:
            for r in relations:
                lc.ensure(r.schema, r.table, RESNAPSHOT_REQUIRED, reason)
            publication.drop_publication(cfg.source_dsn, cfg.publication)
            _drop_slot(cfg.source_dsn, cfg.slot)
        _initialize(cfg, relations, run=run, owner=owner, lc=lc, pipelines=pipelines,
                    reason=reason)
        # A full re-baseline IS a resnapshot: the registry is rewritten with no diff, so a
        # key change the previous signature would have blocked on is already resolved.
        drift.record_all(cfg, relations, run=run)
        active = relations
    else:
        _adopt_streaming(lc, relations,
                         publication.published_relations(cfg.source_dsn, cfg.publication))
        outcome = drift.scan(cfg, relations, run=run, lc=lc, owner=owner,
                             manifest_path=cfg.manifest_path)
        blocked = outcome.blocked
        active = outcome.active(relations)
        _reconcile_selection(cfg, active, run=run, owner=owner, lc=lc, pipelines=pipelines,
                             blocked=blocked)

    result = _drain(cfg, active, run=run, owner=owner, pipelines=pipelines)
    result["drift_blocked"] = sorted(blocked)

    probe = telemetry.slot_probe(cfg.source_dsn, cfg.slot)
    checkpoint = cfg.destination.read_checkpoint(cfg.registration)
    published = publication.published_relations(cfg.source_dsn, cfg.publication)
    dest_tables = cfg.destination.destination_tables(
        {cfg.target_schema(r.schema) for r in active})
    telemetry.emit_probe(run, probe, {
        "selected_tables": len(active),
        "published_tables": len(published),
        "destination_tables": len(dest_tables),
        "drift_blocked_tables": len(blocked),
        "checkpoint_lsn": checkpoint[0] if checkpoint else None,
        "checkpoint_age_s": telemetry.checkpoint_age_s(_checkpoint_time(cfg)),
    }, pull_ms=int((time.monotonic() - t0) * 1000))

    telemetry.emit_reconcile(run, telemetry.reconcile_counts(
        cfg.source_dsn, cfg.destination, [(r.schema, r.table) for r in active],
        cfg.schema_map, threshold=cfg.reconcile_threshold))
    return result


def _adopt_streaming(lc: Lifecycle, relations, published: set) -> None:
    """A steady-state run over tables with no lifecycle row (a takeover from the pilot, or a
    destination rebuilt underneath us): record what is observably true. Membership of the
    publication is the evidence — a newly selected table is NOT adopted, it gets a baseline."""
    for r in relations:
        if lc.state_of(r.schema, r.table) is None and (r.schema, r.table) in published:
            lc.ensure(r.schema, r.table, STREAMING, "adopted: already in the publication")


def _checkpoint_time(cfg: RuntimeConfig):
    try:
        with cfg.destination.connect() as con:
            row = con.execute(
                'select committed_at from "_cdc_runtime".checkpoint where registration = ?',
                [cfg.registration]).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def _drop_slot(dsn: str, slot: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute("select 1 from pg_replication_slots where slot_name = %s",
                           (slot,)).fetchone()
        if row:
            conn.execute("select pg_drop_replication_slot(%s)", (slot,))


def execute(cfg: RuntimeConfig, relations, *, cp: ControlPlane) -> int:
    """Ownership + control-plane run wrapper. A second concurrent run touches NOTHING."""
    with cp.run("cdc") as run:
        owner = OwnerSession(cfg.source_dsn, cfg.slot)
        if not owner.acquire():
            _log(f"another run owns slot {cfg.slot!r} — skipping this tick")
            run.event(entity="_owner", status=cp_status.EV_ZERO_ROWS, unit="ownership",
                      extra={"slot": cfg.slot, "acquired": False})
            run.finish(cp_status.RUN_OK, stop_reason=cp_status.STOP_CLEAN, n_ok=0, n_skipped=1)
            return 0
        try:
            result = run_relations(cfg, relations, run=run, owner=owner)
        except OwnershipLost as exc:
            run.event(entity="_owner", status=cp_status.EV_ERROR, unit="ownership",
                      error_detail=str(exc))
            run.finish(cp_status.RUN_FAILED, stop_reason=cp_status.STOP_ERROR, n_err=1)
            raise
        finally:
            owner.release()
        run.finish(cp_status.RUN_OK, stop_reason=cp_status.STOP_CLEAN,
                   n_ok=len(list(relations)), n_err=0)
        _log(f"done: {result}")
    return 0


# --------------------------------------------------------------------------- manifest binding


def config_from_manifest(m, *, dsn: str, destination: Destination,
                         schemas: Sequence[str] = ()) -> RuntimeConfig:
    mapping = m.destination.schema_mapping
    schema_map = dict(mapping) if isinstance(mapping, dict) else {}
    return RuntimeConfig(
        registration=m.source_id,
        source_id=m.source_id,
        source_dsn=dsn,
        slot=m.replication.slot,
        publication=m.replication.publication,
        extractor=m.control.extractor,
        destination=destination,
        schema_map=schema_map,
        pipeline_name=m.destination.pipeline_name,
        target_batch_size=m.replication.target_batch_size,
        replica_identity=m.replication.replica_identity,
        slot_loss_policy=m.replication.slot_loss_policy,
        retirement_policy=m.destination.retirement_policy,
    )


def _compile(m):
    """Lazy import so the runtime's tests can drive `run_relations` with a literal relation
    list, and so `validate`/`plan` never pull the runtime in."""
    from . import discovery, manifest as mf
    from .adapters import get as get_adapter
    from .selectors import compile as compile_selection

    dsn = os.environ.get(m.source.dsn_env)
    if not dsn:
        sys.exit(f"FATAL: {m.source.dsn_env} is not set")
    relations = discovery.discover(dsn)
    adapter_rels = []
    if m.source.discovery is not None:
        adapter_rels = list(get_adapter(m.source.discovery.adapter)(dsn, m.source.discovery.options))
    plan = compile_selection(m, relations, adapter_rels)
    if mf.errors(list(plan.problems)):
        for p in mf.redact_problems(list(plan.problems)):
            print(p, file=sys.stderr)
        sys.exit("FATAL: the compiled plan has errors — run `validate` and fix the manifest")
    return dsn, plan


def _destination(m) -> Destination:
    """MotherDuck unless `PG_MD_CDC_DESTINATION=duckdb:<path>` overrides it.

    DEV/PROOF ONLY: it exists so `run`/`status`/`resnapshot` can be driven end to end against a
    local duckdb file (the same dlt load path minus credentials). `PG_MD_CDC_PIPELINES_DIR`
    likewise redirects dlt's pipeline state so a proof never writes into ~/.dlt. Unset, both are
    inert and the destination is MotherDuck.
    """
    override = os.environ.get("PG_MD_CDC_DESTINATION", "")
    pipelines_dir = os.environ.get("PG_MD_CDC_PIPELINES_DIR") or None
    if override:
        kind, _, path = override.partition(":")
        if kind != "duckdb" or not path:
            sys.exit("FATAL: PG_MD_CDC_DESTINATION must look like 'duckdb:<file path>'")
        return Destination(DestinationSpec(kind="duckdb", database=m.destination.database,
                                           path=path, pipelines_dir=pipelines_dir))
    return Destination(DestinationSpec(kind="motherduck", database=m.destination.database,
                                       token_env=m.destination.token_env,
                                       pipelines_dir=pipelines_dir))


def _control_plane(m, dev: bool) -> ControlPlane:
    control_dsn = os.environ.get("CONTROL_PG_DSN")
    if not control_dsn and not dev:
        sys.exit("FATAL: CONTROL_PG_DSN not set. The Postgres control plane is a HARD dependency "
                 "(ADR 0028) — set it, or pass --dev for a local WAL-only run.")
    wal_dir = m.control.wal_dir or os.environ.get("CONTROL_WAL_DIR", "control_wal")
    return ControlPlane(m.control.extractor, dsn=None if dev else control_dsn, wal_dir=wal_dir)


def run(manifest, *, dev: bool = False, manifest_path: str = "<manifest>") -> int:
    dsn, plan = _compile(manifest)
    cfg = config_from_manifest(manifest, dsn=dsn, destination=_destination(manifest))
    cfg.manifest_path = manifest_path
    if not plan.relations:
        _log("the compiled selection is empty — nothing to do")
        return 0
    return execute(cfg, plan.relations, cp=_control_plane(manifest, dev))


def status(manifest) -> int:
    """Read-only: slot probe, checkpoint, lifecycle table."""
    dsn = os.environ.get(manifest.source.dsn_env)
    if not dsn:
        sys.exit(f"FATAL: {manifest.source.dsn_env} is not set")
    dest = _destination(manifest)
    probe = telemetry.slot_probe(dsn, manifest.replication.slot)
    print(f"slot {manifest.replication.slot}: " + (str(probe) if probe else "ABSENT"))
    checkpoint = dest.read_checkpoint(manifest.source_id)
    print(f"checkpoint: {checkpoint[0] if checkpoint else 'none'}")
    states = dest.read_lifecycle(manifest.source_id)
    for (schema, table), state in sorted(states.items()):
        print(f"  {schema}.{table:<40} {state}")
    return 0


def resnapshot(manifest, *, tables: Sequence[str] | None = None, yes: bool = False,
               dev: bool = False) -> int:
    """Drop the destination table(s) and retake a baseline. DESTRUCTIVE — requires `yes`
    (D6's "separately approved drop"); the retirement policy never drops on its own."""
    if not yes:
        sys.exit("resnapshot DROPS destination tables — re-run with --yes to confirm")
    dsn, plan = _compile(manifest)
    wanted = set(tables or [])
    targets = [r for r in plan.relations if not wanted or r.qualified in wanted]
    if not targets:
        sys.exit(f"no selected relation matches {sorted(wanted)}")
    cfg = config_from_manifest(manifest, dsn=dsn, destination=_destination(manifest))
    cp = _control_plane(manifest, dev=dev)
    with cp.run("resnapshot") as run_ctx:
        owner = OwnerSession(cfg.source_dsn, cfg.slot)
        if not owner.acquire():
            run_ctx.finish(cp_status.RUN_OK, stop_reason=cp_status.STOP_CLEAN, n_skipped=1)
            sys.exit("another run owns this registration — try again after its tick")
        try:
            cfg.destination.ensure_runtime_tables()
            lc = Lifecycle(cfg.destination, cfg.registration, run_ctx)
            pipelines = _Pipelines(cfg, [r.schema for r in plan.relations])
            for rel in targets:
                owner.assert_held()
                lc.ensure(rel.schema, rel.table, RESNAPSHOT_REQUIRED, "operator resnapshot")
                publication.reconcile(
                    cfg.source_dsn, cfg.publication,
                    [(r.schema, r.table) for r in plan.relations
                     if (r.schema, r.table) != (rel.schema, rel.table)])
                dropped = cfg.destination.drop_table(cfg.target_schema(rel.schema), rel.table)
                _log(f"resnapshot {rel.qualified}: dropped {dropped}")
                publication.reconcile(cfg.source_dsn, cfg.publication,
                                      [(r.schema, r.table) for r in plan.relations])
                lc.ensure(rel.schema, rel.table, SNAPSHOTTING, "operator resnapshot")
                pipe = pipelines.get(rel.schema)
                with snapshot.baseline_resource(cfg.source_dsn, (rel.schema, rel.table),
                                                list(rel.key), key_policy=rel.key_policy) as res:
                    pipe.run(res)
                _emit_table_events(run_ctx, pipe, "snapshot", {rel.table},
                                   {"reason": "operator resnapshot"})
                # The refreshed signature is what clears a key_change block: without it the
                # next run would diff against the pre-change key and pull the table again.
                drift.record_signature(cfg, rel)
                lc.ensure(rel.schema, rel.table, STREAMING, "operator resnapshot")
        finally:
            owner.release()
        run_ctx.finish(cp_status.RUN_OK, stop_reason=cp_status.STOP_CLEAN, n_ok=len(targets))
    return 0
