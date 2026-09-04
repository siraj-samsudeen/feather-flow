"""The run/status/resnapshot engine (#3079 Phase 2).

Correctness invariants this file exists to hold:
  - the advisory owner session is asserted before EVERY mutating phase AND again after every
    blocking destination load, before the checkpoint/slot move; losing it aborts with the load
    left replayable;
  - the checkpoint is written only after every schema's load has committed, and the slot is
    advanced only to a checkpointed LSN — so an ambiguous destination outcome replays, never skips;
  - every baseline records the consistent point it was taken at; the drain drops a relation's
    transactions below that boundary (they are in the baseline), so no row lands twice;
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
    CATCHING_UP, DISCOVERED, DRIFT_REVIEW, RESNAPSHOT_REQUIRED, RETIRED, SNAPSHOTTING, STREAMING,
    Lifecycle,
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
    reconcile_exclude: list = field(default_factory=list)
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


def _baseline_kwargs(rel) -> dict:
    """An append_only relation has no key a baseline could dedup on — its CDC key is `lsn`,
    which a snapshot row does not carry — so re-reading it on top of the old copy DOUBLES it.
    Re-baselining one is a replace: dlt's `drop_resources` drops the destination table and
    forgets it, which a bare `drop table` does not (dlt then skips the CREATE and the load
    dies "table does not exist")."""
    from .selectors import APPEND_ONLY
    return {"refresh": "drop_resources"} if rel.key_policy == APPEND_ONLY else {}


def _mark_resnapshot_required(lc: Lifecycle, schema: str, table: str, detail: str) -> None:
    """The state a resnapshot leaves a table in when it fails partway — and the only state it
    may be in while the destination copy is being dropped."""
    if lc.state_of(schema, table) in (STREAMING, CATCHING_UP, DRIFT_REVIEW, SNAPSHOTTING):
        lc.transition(schema, table, RESNAPSHOT_REQUIRED, detail)


def _to_snapshotting(lc: Lifecycle, schema: str, table: str, detail: str) -> None:
    """Route ANY current state to `snapshotting` on a legal path — a vanished slot re-baselines
    every selected relation whatever state it was in, and an IllegalTransition here would abort
    the whole tick. A table that was streaming passes through resnapshot_required (the state
    history is the only place an operator sees WHY a baseline was retaken); one that never got a
    baseline (`discovered`, or no row at all) goes straight there, which is what happened."""
    current = lc.state_of(schema, table)
    if current == RETIRED:
        lc.transition(schema, table, DISCOVERED, detail)
        current = DISCOVERED
    if current in (STREAMING, CATCHING_UP, DRIFT_REVIEW):
        lc.transition(schema, table, RESNAPSHOT_REQUIRED, detail)
    lc.ensure(schema, table, SNAPSHOTTING, detail)


def _emit_table_events(run, pipeline, unit: str, source_schema: str, tables: set[str],
                       extra: dict | None = None) -> int:
    """One event per loaded table, entity `schema.table` — two source schemas may hold the same
    table name, and lifecycle/reconcile already key on the qualified name (#3079 review)."""
    counts = Destination.row_counts(pipeline)
    n = 0
    for table, rows in counts.items():
        if tables and table not in tables:
            continue
        run.event(entity=f"{source_schema}.{table}", status=cp_status.EV_OK, unit=unit,
                  rows=rows, extra=extra)
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
            by_key = {(r.schema, r.table): r for r in relations}
            for source_schema, resources in by_schema.items():
                owner.assert_held()
                pipe = pipelines.get(source_schema)
                # append_only relations re-baseline as a REPLACE, so they load in their own
                # call: `drop_resources` would otherwise drop the keyed tables beside them.
                plain = [x for x in resources
                         if not _baseline_kwargs(by_key[(source_schema, x.name)])]
                replaced = [x for x in resources
                            if _baseline_kwargs(by_key[(source_schema, x.name)])]
                if plain:
                    pipe.run(plain)
                if replaced:
                    pipe.run(replaced, refresh="drop_resources")
                owner.assert_held()   # the load blocked for a while; the lock may be gone
                _emit_table_events(run, pipe, "snapshot", source_schema,
                                   {t for s, t in desired if s == source_schema},
                                   {"reinit_reason": reason})
    finally:
        handle.close()

    owner.assert_held()
    # The exported snapshot IS the consistent point: nothing below it is ever streamed, so the
    # boundary is recorded for uniformity (every baseline has one), not because it filters.
    cfg.destination.write_baseline_lsns(
        cfg.registration, {(r.schema, r.table): handle.consistent_point_lsn for r in relations})
    cfg.destination.write_checkpoint(cfg.registration, cfg.slot, handle.consistent_point_lsn)
    for r in relations:
        lc.ensure(r.schema, r.table, CATCHING_UP, reason)
        lc.ensure(r.schema, r.table, STREAMING, reason)
    return handle.consistent_point_lsn


def _take_baseline(cfg: RuntimeConfig, rel, *, run, owner, pipelines: _Pipelines, unit: str,
                   extra: dict) -> int:
    """One consistent baseline of a table the publication ALREADY carries. Returns the boundary
    LSN, persisted before the lifecycle moves on: a baseline whose boundary was not recorded
    would let the drain re-land what it just read."""
    pipe = pipelines.get(rel.schema)
    with snapshot.consistent_baseline(cfg.source_dsn, cfg.slot, (rel.schema, rel.table),
                                      list(rel.key), key_policy=rel.key_policy) as (resource,
                                                                                    boundary):
        pipe.run(resource, **_baseline_kwargs(rel))
    owner.assert_held()   # the load blocked for a while; the lock may be gone
    cfg.destination.write_baseline_lsns(cfg.registration, {(rel.schema, rel.table): boundary})
    _emit_table_events(run, pipe, unit, rel.schema, {rel.table},
                       {**extra, "boundary_lsn": boundary})
    return boundary


def _baseline(cfg: RuntimeConfig, rel, *, run, owner, lc: Lifecycle, pipelines: _Pipelines,
              detail: str) -> None:
    """Full read of a table just ADDED to the publication, at a consistent point — the drain
    drops this relation's transactions below it, so the overlap with the WAL lands once."""
    owner.assert_held()
    # `snapshotting`, not `catching_up`: a crash during this load must leave the ONE state
    # _resume_interrupted_snapshots retakes a baseline from (idempotent — the caller already
    # marked it before the publication add).
    _to_snapshotting(lc, rel.schema, rel.table, detail)
    _take_baseline(cfg, rel, run=run, owner=owner, pipelines=pipelines, unit="backfill",
                   extra={"detail": detail})
    lc.ensure(rel.schema, rel.table, CATCHING_UP, detail)
    lc.ensure(rel.schema, rel.table, STREAMING, detail)


def _resume_interrupted_snapshots(cfg: RuntimeConfig, relations, *, run, owner,
                                  lc: Lifecycle, pipelines: _Pipelines) -> list:
    """A run killed between `create_slot_with_snapshot` and the snapshot load leaves relations
    in `snapshotting` — and because the SLOT now exists, the next run goes straight to steady
    state and would never take that baseline: the pre-slot rows are lost silently (the CDC
    stream only carries what happened after the consistent point). Retake the baseline here,
    before the drain, so a concurrent change also arrives via CDC and merge dedups it."""
    resumed = []
    for r in relations:
        if lc.state_of(r.schema, r.table) != SNAPSHOTTING:
            continue
        owner.assert_held()
        _take_baseline(cfg, r, run=run, owner=owner, pipelines=pipelines, unit="snapshot",
                       extra={"detail": "resumed an interrupted snapshot"})
        lc.ensure(r.schema, r.table, CATCHING_UP, "resumed an interrupted snapshot")
        lc.ensure(r.schema, r.table, STREAMING, "resumed an interrupted snapshot")
        resumed.append((r.schema, r.table))
    if resumed:
        _log(f"resumed interrupted snapshots: {resumed}")
    return resumed


def _reconcile_selection(cfg: RuntimeConfig, relations, *, run, owner, lc: Lifecycle,
                         pipelines: _Pipelines, blocked: set | None = None) -> tuple[list, list]:
    owner.assert_held()
    desired = [(r.schema, r.table) for r in relations]
    by_key = {(r.schema, r.table): r for r in relations}
    # Mark the newcomers BEFORE the ALTER PUBLICATION. A crash in between leaves a published
    # table with no lifecycle row, which _adopt_streaming then reads as "already streaming" —
    # and nothing would ever baseline it, so its pre-add rows (in no WAL record) are lost
    # silently and forever. `snapshotting` written first is what _resume_interrupted_snapshots
    # picks up on the next tick.
    planned_add, _ = publication.reconcile(cfg.source_dsn, cfg.publication, desired,
                                           plan_only=True)
    for schema, table in planned_add:
        _to_snapshotting(lc, schema, table, "added to selection")
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
    from .selectors import APPEND_ONLY
    keys = {(r.schema, r.table): list(r.key) for r in relations}
    policies = {(r.schema, r.table): r.key_policy for r in relations}
    baselines = cfg.destination.read_baseline_lsns(cfg.registration)
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
                              keys_by_relation=keys,
                              policies_by_relation=policies,
                              baseline_lsn_by_relation=baselines):
        owner.assert_held()
        # TRUNCATEs first: the consumer already discarded a keyed relation's earlier rows of
        # this batch, so "empty, then load what followed" is the source's own order. A replay
        # after a crash deletes again and re-merges — idempotent.
        for t in batch.truncates:
            keyed = policies.get((t["namespace"], t["table"])) != APPEND_ONLY
            applied = False
            if keyed:
                applied = cfg.destination.delete_all_rows(cfg.target_schema(t["namespace"]),
                                                          t["table"])
            run.event(entity=f"{t['namespace']}.{t['table']}", status=cp_status.EV_OK,
                      unit="truncate",
                      extra={"lsn": t["lsn"], "commit_lsn": t["commit_lsn"],
                             "applied": "delete_all" if keyed else "recorded_only",
                             "destination_table_existed": applied})
        load_ids: list[str] = []
        for source_schema, items in batch.items_by_namespace.items():
            if not items:
                continue
            pipe = pipelines.get(source_schema)
            info = Destination.load(pipe, items, resource_name=cfg.slot)
            # The load is the one long blocking call per batch: Postgres may have reaped the
            # owner backend meanwhile, freeing the lock to a peer. Checkpointing or advancing
            # the slot without the lock would hand that peer a hole; re-assert first, and leave
            # the committed load replayable (merge / lsn-merge make the replay exact).
            owner.assert_held()
            load_ids += _load_ids(info)
            rows += sum(Destination.row_counts(pipe).values())
            _emit_table_events(run, pipe, "cdc", source_schema, set())
        if batch.last_commit_lsn:
            owner.assert_held()
            cfg.destination.write_checkpoint(cfg.registration, cfg.slot,
                                             batch.last_commit_lsn, load_ids)
            stream.advance(cfg.source_dsn, cfg.slot, batch.last_commit_lsn)
        batches += 1
    return {"batches": batches, "rows": rows, "start_lsn": start_lsn}


# --------------------------------------------------------------------------- entry points


class _Tally:
    """The run handle every phase writes through, counting EV_ERROR entities on the way so the
    control.run row can be finished honestly: `ok` means every unit succeeded (control_plane
    status.py), and a drift-blocked table, a reconcile mismatch or a paused slot is not that."""

    def __init__(self, run) -> None:
        self._run = run
        self.errors: set[str] = set()

    def event(self, *, entity: str, status: str, **kw) -> None:
        if status == cp_status.EV_ERROR:
            self.errors.add(entity)
        return self._run.event(entity=entity, status=status, **kw)

    def __getattr__(self, name):
        return getattr(self._run, name)


def _outcome(relations, active, *, errors: set, paused: bool) -> dict:
    selected = [r.qualified for r in relations]
    active_names = [r.qualified for r in active]
    skipped = len(selected) if paused else len(selected) - len(active_names)
    failed_active = [q for q in active_names if q in errors]
    n_ok = 0 if paused else len(active_names) - len(failed_active)
    if paused:
        status, stop = cp_status.RUN_FAILED, cp_status.STOP_ERROR
    elif errors or skipped:
        status, stop = cp_status.RUN_PARTIAL, cp_status.STOP_CLEAN
    else:
        status, stop = cp_status.RUN_OK, cp_status.STOP_CLEAN
    return {"status": status, "stop_reason": stop, "n_ok": n_ok, "n_err": len(errors),
            "n_skipped": skipped, "errors": sorted(errors)}


def run_relations(cfg: RuntimeConfig, relations, *, run, owner) -> dict:
    """One full drain cycle for an already-compiled relation set. Callers hold the owner lock.
    The result carries `outcome` — status + n_ok/n_err/n_skipped — for `execute` to finish
    the control.run row with."""
    t0 = time.monotonic()
    relations = list(relations)
    run = _Tally(run)
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
        return {"paused": True,
                "outcome": _outcome(relations, [], errors=run.errors, paused=True)}

    blocked: set = set()
    if slot is None or lost:
        reason = "first run" if slot is None else f"slot invalidated ({slot.get('wal_status')})"
        if lost:
            # Dropping the publication and the slot is the most destructive thing this runtime
            # does — a peer that acquired the freed lock could already be re-initialising. Prove
            # ownership BEFORE the drop, not at the top of _initialize which runs after it.
            owner.assert_held()
            for r in relations:
                lc.ensure(r.schema, r.table, RESNAPSHOT_REQUIRED, reason)
            publication.drop_publication(cfg.source_dsn, cfg.publication)
            snapshot.drop_slot_if_exists(cfg.source_dsn, cfg.slot)
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
        _resume_interrupted_snapshots(cfg, active, run=run, owner=owner, lc=lc,
                                      pipelines=pipelines)

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

    from .selectors import APPEND_ONLY
    telemetry.emit_reconcile(run, telemetry.reconcile_counts(
        cfg.source_dsn, cfg.destination, [(r.schema, r.table) for r in active],
        cfg.schema_map, threshold=cfg.reconcile_threshold,
        exclude=list(cfg.reconcile_exclude),
        append_only={(r.schema, r.table) for r in active
                     if r.key_policy == APPEND_ONLY}))
    result["outcome"] = _outcome(relations, active, errors=run.errors, paused=False)
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
            # The ONE event whose text is an arbitrary driver message: psycopg wraps whatever
            # killed the owner connection. control.event is read by everyone with the plane, so
            # it goes through the same redaction as stdout.
            from .manifest import redact
            run.event(entity="_owner", status=cp_status.EV_ERROR, unit="ownership",
                      error_detail=redact(str(exc)))
            run.finish(cp_status.RUN_FAILED, stop_reason=cp_status.STOP_ERROR, n_err=1)
            raise
        finally:
            owner.release()
        outcome = result["outcome"]
        run.finish(outcome["status"], stop_reason=outcome["stop_reason"],
                   n_ok=outcome["n_ok"], n_err=outcome["n_err"], n_skipped=outcome["n_skipped"])
        _log(f"done: {result}")
    # A paused registration drained nothing: exit non-zero so the cron surface is red too.
    return 0 if outcome["status"] != cp_status.RUN_FAILED else 1


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
        reconcile_exclude=list(m.control.reconcile_exclude),
    )


def _compile(m):
    """Lazy import so the runtime's tests can drive `run_relations` with a literal relation
    list, and so `validate`/`plan` never pull the runtime in."""
    from . import discovery, manifest as mf
    from .adapters import get as get_adapter
    from .selectors import compile as compile_selection

    _refuse_static_errors(m)
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


def _refuse_static_errors(m) -> None:
    """The manifest contract (unknown api_version rejected, enums exact) is only a contract if
    the MUTATING entry points hold it too — `validate` alone would let `run` execute a v999
    manifest best-effort (#3079 review). Runs before any connection is opened."""
    from . import manifest as mf
    problems = mf.errors(list(mf.validate_static(m)))
    if problems:
        for prob in mf.redact_problems(problems):
            print(prob, file=sys.stderr)
        sys.exit("FATAL: the manifest fails static validation — run `validate` and fix it")


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
        _refuse_empty_selection(cfg, cp=_control_plane(manifest, dev))
    return execute(cfg, plan.relations, cp=_control_plane(manifest, dev))


def _refuse_empty_selection(cfg: RuntimeConfig, *, cp: ControlPlane) -> None:
    """A declaration that compiles to nothing is a mistake or a decommission — never "keep the
    publication and the slot but stop consuming": a slot nobody drains retains WAL until the
    fuse, and exiting 0 with no control.run row hides it (#3079 review). Fail closed, say so
    in control.run, touch nothing; §i of the runbook is the explicit way out."""
    published = (publication.published_relations(cfg.source_dsn, cfg.publication)
                 if publication.publication_exists(cfg.source_dsn, cfg.publication) else None)
    slot = telemetry.slot_probe(cfg.source_dsn, cfg.slot)
    detail = (f"the compiled selection is empty; publication {cfg.publication!r} "
              + (f"still carries {len(published)} table(s)" if published is not None
                 else "does not exist")
              + f", slot {cfg.slot!r} " + ("exists" if slot else "does not exist"))
    with cp.run("cdc") as run:
        run.event(entity="_selection", status=cp_status.EV_ERROR, unit="selection",
                  error_detail=detail,
                  extra={"published_tables": len(published or ()), "slot_exists": bool(slot)})
        run.finish(cp_status.RUN_FAILED, stop_reason=cp_status.STOP_ERROR, n_ok=0, n_err=1,
                   n_skipped=len(published or ()))
    sys.exit(f"FATAL: {detail}. Fix the manifest's selection, or decommission the registration "
             "explicitly (docs/agents/pg-md-cdc-runbook.md §i) — a run never retires a whole "
             "registration on its own.")


def status(manifest) -> int:
    """Read-only: slot probe, checkpoint, lifecycle table."""
    _refuse_static_errors(manifest)
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
                # Drop FIRST, in `resnapshot_required`. A drop that fails must leave the
                # table exactly where a failed resnapshot belongs — not `snapshotting`, which
                # the next run would resume by merging a fresh baseline INTO the undropped
                # copy, resurrecting rows the source deleted (#3079 review). The publication
                # is only touched once the destination copy is really gone.
                _mark_resnapshot_required(lc, rel.schema, rel.table, "operator resnapshot")
                dropped = cfg.destination.drop_table(cfg.target_schema(rel.schema), rel.table)
                _log(f"resnapshot {rel.qualified}: dropped {dropped}")
                _to_snapshotting(lc, rel.schema, rel.table, "operator resnapshot")
                # Re-admits a key-changed table drift.scan removed; a no-op otherwise.
                publication.reconcile(cfg.source_dsn, cfg.publication,
                                      [(r.schema, r.table) for r in plan.relations])
                pipe = pipelines.get(rel.schema)
                with snapshot.consistent_baseline(
                        cfg.source_dsn, cfg.slot, (rel.schema, rel.table), list(rel.key),
                        key_policy=rel.key_policy) as (res, boundary):
                    # Always `drop_resources`, even for a keyed relation: the drop above went
                    # through OUR duckdb connection, and MotherDuck's catalog can still serve
                    # the dropped table to dlt's own connection for tens of seconds (gotcha
                    # `motherduck-catalog-lags-a-write`) — dlt would then skip the CREATE and
                    # the load would die. Doing the drop inside the same dlt client removes the
                    # cross-session read entirely.
                    pipe.run(res, refresh="drop_resources")
                owner.assert_held()
                cfg.destination.write_baseline_lsns(cfg.registration,
                                                    {(rel.schema, rel.table): boundary})
                _emit_table_events(run_ctx, pipe, "snapshot", rel.schema, {rel.table},
                                   {"reason": "operator resnapshot", "boundary_lsn": boundary})
                # The refreshed signature is what clears a key_change block: without it the
                # next run would diff against the pre-change key and pull the table again.
                drift.record_signature(cfg, rel)
                lc.ensure(rel.schema, rel.table, STREAMING, "operator resnapshot")
        except Exception as exc:  # noqa: BLE001 — finish the run row honestly, then re-raise
            from .manifest import redact
            run_ctx.event(entity="_resnapshot", status=cp_status.EV_ERROR, unit="resnapshot",
                          error_detail=redact(f"{type(exc).__name__}: {exc}"))
            run_ctx.finish(cp_status.RUN_FAILED, stop_reason=cp_status.STOP_ERROR, n_err=1)
            raise
        finally:
            owner.release()
        run_ctx.finish(cp_status.RUN_OK, stop_reason=cp_status.STOP_CLEAN, n_ok=len(targets))
    return 0
