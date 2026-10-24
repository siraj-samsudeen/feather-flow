"""The run/status/resnapshot engine (#3079 Phase 2).

Correctness invariants this file exists to hold:
  - the advisory owner session is asserted before EVERY mutating phase AND again after every
    blocking destination load, before the checkpoint/slot move; losing it aborts with the load
    left replayable;
  - the checkpoint is written only after every schema's load has committed, and the slot is
    advanced only to a checkpointed LSN — so an ambiguous destination outcome replays, never skips;
  - every baseline records the consistent point it was taken at; the drain drops a relation's
    transactions below that boundary (they are in the baseline), so no row lands twice;
  - relation identity is (schema, table) end to end; the destination side is the relation's
    compiled `target_schema` (D-13), one dlt pipeline per TARGET schema, never the source's;
  - a key-changed relation is out of the desired set for the WHOLE run (drift.scan), so the
    drain, the reconcile counts and the probe all see the same active set;
  - a table RENAMED at the source stays in the publication under its new name (membership is
    by OID): a published relation with no lifecycle row and no destination copy is baselined,
    not adopted, and
    a known relation that left the selection is retired whether or not the publication still
    named it (#4338, Featherbase `user` → `person`).
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
    table_schemas: list = field(default_factory=list)  # [("schema.table" glob, target schema)]
    pipeline_name: str | None = None
    target_batch_size: int = 1000
    replica_identity: str = "full"
    slot_loss_policy: str = "resnapshot"
    retirement_policy: str = "freeze"
    reconcile_threshold: int = telemetry.RECONCILE_MAX_ROWS
    reconcile_exclude: list = field(default_factory=list)
    manifest_path: str = "<manifest>"   # only ever interpolated into operator remedy text
    # Bounds CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT (snapshot.create_slot_with_snapshot):
    # a long-running source transaction blocks it, and statement_timeout cannot cancel that
    # command (see the docstring there) — so the client enforces this deadline itself.
    baseline_wait_s: float = snapshot.BASELINE_WAIT_S_DEFAULT
    # `source.deny.columns` (#4346): (schema, table) -> columns that never leave the process.
    # The WHOLE manifest map, not just the compiled set: a retired relation's queued WAL is
    # projected too, and the destination tripwire watches its frozen copy.
    deny_columns: dict = field(default_factory=dict)
    # Relation OID -> denied columns, resolved per run by `_with_inherited_denies` (#4346).
    deny_oids: dict = field(default_factory=dict)
    # Relation OID -> denied column attnums: a column's identity, so the deny follows a column
    # RENAME (the baseline resolves them inside its own snapshot).
    deny_attnums: dict = field(default_factory=dict)
    # "schema.table.column" denies that bound to no attnum this run (#4346): the run refuses.
    deny_unbound: tuple = ()

    def target_schema(self, source_schema: str, table: str | None = None) -> str:
        """Manifest rules only — the runtime prefers a compiled Relation's own `target_schema`
        (`_Targets`); this is the fallback for a relation outside the current set."""
        if table is not None:
            import fnmatch
            qualified = f"{source_schema}.{table}"
            for pattern, target in self.table_schemas:
                if fnmatch.fnmatchcase(qualified, pattern):
                    return target
        return self.schema_map.get(source_schema, source_schema)


def _log(msg: str) -> None:
    print(f"[pg_md_cdc] {msg}", flush=True)


def _denied(cfg: RuntimeConfig, relations=()) -> dict:
    """(schema, table) -> sorted denied columns: the manifest map plus each compiled Relation's
    own (#4346). One answer for the baseline, the drain and the tripwire."""
    out = {k: set(v) for k, v in (cfg.deny_columns or {}).items()}
    for r in relations:
        if getattr(r, "deny_columns", ()):
            out.setdefault((r.schema, r.table), set()).update(r.deny_columns)
    return {k: tuple(sorted(v)) for k, v in out.items() if v}


def _denied_oids(cfg: RuntimeConfig, denied: dict, unbound: list | None = None) -> dict:
    """Relation OID -> denied columns (#4346), so the deny follows a table through a RENAME:
    pgoutput names a relation by its CURRENT description, and a renamed `person` would
    otherwise arrive under a name with no deny entry. Every OID a manifest-denied name has ever
    resolved to is PERSISTED (`_cdc_runtime.denied_oid`, with the origin name and column), never
    re-derived from a name: a new table re-using the old name must not erase the old OID's deny.
    A persisted row applies only while its origin name still denies that column, so removing a
    deny from the manifest does remove it.

    Each denied (table, column) must BIND to an attnum (Codex round 16): by name, in the same
    catalogue statement that finds the table's OID (so a rename cannot slip between two
    lookups), or through an earlier binding to an attnum. One that binds neither
    way — renamed before it was ever bound, or not there at all — is appended to `unbound`,
    and the run refuses: its identity is unknown, so no column of that table is safe to read."""
    if not denied:
        return {}
    import psycopg
    found, missing = [], []
    with psycopg.connect(cfg.source_dsn, autocommit=True, connect_timeout=10) as c:
        for (schema, table), cols in sorted(denied.items()):
            rows = c.execute(
                "select c.oid, a.attname, a.attnum from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "left join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 "
                "and not a.attisdropped and a.attname = any(%s) "
                "where n.nspname = %s and c.relname = %s",
                (list(cols), schema, table)).fetchall()
            if not rows:
                continue   # no such table at the source (a frozen copy, say): nothing to bind
            oid = int(rows[0][0])
            got = {n: int(a) for _o, n, a in rows if n is not None}
            found += [(oid, schema, table, col, got[col]) for col in cols if col in got]
            missing += [(oid, schema, table, col) for col in cols if col not in got]
    _record_denied(cfg, found)
    live = _live_denied_columns(cfg, denied)
    if unbound is not None:
        # Any earlier binding covers it, a dropped one too: its identity is known, the value is
        # gone from the table, and the decoder's layout proof quarantines WAL that may carry it.
        covered = {(int(o), col) for o, _s, _t, col, a in
                   cfg.destination.read_denied_oids(cfg.registration) if a is not None}
        unbound += sorted(f"{s}.{t}.{col}" for oid, s, t, col in missing
                          if (oid, col) not in covered)
    out: dict = {}
    for oid, attnum, name, _origin in live:
        out.setdefault(oid, set()).add(name)
    for oid, schema, table, col, _attnum in cfg.destination.read_denied_oids(cfg.registration):
        if col in denied.get((schema, table), ()):
            out.setdefault(int(oid), set()).add(col)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _record_denied(cfg: RuntimeConfig, rows) -> None:
    """Persist (oid, schema, table, column, attnum) deny bindings. Every caller resolved the
    attnum at a trusted moment — one catalogue statement at run start, under a baseline's
    locks, or from a proven decoder position — never by a later name lookup (Codex round 16)."""
    rows = sorted({(int(o), s, t, c, int(a)) for o, s, t, c, a in rows if a is not None})
    if not rows:
        return
    cfg.destination.record_denied_oids(cfg.registration, rows)
    # ...and into this run's live attnum map (the decoder and later baselines read it).
    for o, _s, _t, _c, a in rows:
        cfg.deny_attnums[o] = tuple(sorted(set(cfg.deny_attnums.get(o, ())) | {a}))


def _live_denied_columns(cfg: RuntimeConfig, denied: dict) -> list:
    """(oid, attnum, CURRENT column name, origin column) for every persisted, still-applicable
    deny binding: a denied column renamed at the source (`password_hash` -> `pw`) keeps its
    attnum, so its new name is denied too — the column's identity, not its spelling (Sonnet
    review F1). A dropped column (`attisdropped`) is gone (the decoder's layout proof then
    quarantines any WAL that may carry it); a re-added one of the same name binds anew."""
    bindings: dict = {}   # (oid, attnum) -> origin columns (two origin names may share one)
    for o, s, t, col, a in cfg.destination.read_denied_oids(cfg.registration):
        if a is not None and col in denied.get((s, t), ()):
            bindings.setdefault((int(o), int(a)), set()).add(col)
    if not bindings:
        return []
    pairs = sorted(bindings)
    import psycopg
    with psycopg.connect(cfg.source_dsn, autocommit=True, connect_timeout=10) as c:
        rows = c.execute(
            "select attrelid::bigint, attnum, attname from pg_attribute "
            "where not attisdropped and (attrelid, attnum) in "
            "(select unnest(%s::oid[]), unnest(%s::int2[]))",
            ([p[0] for p in pairs], [p[1] for p in pairs])).fetchall()
    return [(int(o), int(a), n, origin) for o, a, n in rows
            for origin in sorted(bindings[(int(o), int(a))])]


def _denied_attnums(cfg: RuntimeConfig, denied: dict) -> dict:
    """Relation OID -> denied attnums, for the baseline to resolve inside its snapshot."""
    out: dict = {}
    for o, s, t, col, a in cfg.destination.read_denied_oids(cfg.registration):
        if a is not None and col in denied.get((s, t), ()):
            out.setdefault(int(o), set()).add(int(a))
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _baseline_capture() -> dict:
    """What a baseline reports from under its locks (snapshot_resources' *_out arguments):
    each relation's OID, its reviewed layout, and each denied name's attnum."""
    return {"oids_out": {}, "layouts_out": {}, "bound_out": {}}


def _persist_bound(cfg: RuntimeConfig, bound: dict) -> None:
    """Persist what the baseline read under its locks (#4346), before its load: the OID each
    denied relation had INSIDE the snapshot, bound to its deny and to the attnum each denied
    name had there (so a replacement-then-rename between lookups cannot leave an OID with rows
    in bronze but no recorded deny), and each relation's reviewed layout, which later
    descriptions are proven against."""
    oids, attnums = bound.get("oids_out", {}), bound.get("bound_out", {})
    rows = [(oid, s, t, c) for (s, t), oid in oids.items()
            for c in cfg.deny_columns.get((s, t), ())]
    _record_denied(cfg, [(oid, s, t, c, dict(attnums.get((s, t), ())).get(c))
                         for oid, s, t, c in rows])
    # ...and into THIS run's live map, which the drain hands the decoder: a rename between the
    # baseline and the drain must not find the bound OID missing (Codex round 6).
    for oid, _s, _t, col in rows:
        cfg.deny_oids[oid] = tuple(sorted(set(cfg.deny_oids.get(oid, ())) | {col}))
    if bound.get("layouts_out"):   # every baseline: a deny added later has its reference
        cfg.destination.write_reviewed_layouts(cfg.registration, bound["layouts_out"])


def _clear_rebuilt_quarantine(cfg: RuntimeConfig, bound: dict, relations) -> None:
    """Clear the quarantine of each OID this baseline read (Codex round 17): by OID, never by
    name, so a quarantined table renamed and then resnapshotted under its new name is cleared,
    and another OID's mark under the rebuilt name is not. A mark whose OID no longer exists at
    the source and whose recorded name was rebuilt is cleared too: that table can emit nothing
    more, and the copy it fed was just replaced."""
    oids = set((bound.get("oids_out") or {}).values())
    names = {(r.schema, r.table) for r in relations}
    marks = cfg.destination.read_quarantine(cfg.registration)
    orphans = [o for o, (s, t, _w, _since) in marks.items() if (s, t) in names and o not in oids]
    if orphans:
        import psycopg
        with psycopg.connect(cfg.source_dsn, autocommit=True, connect_timeout=10) as c:
            live = {int(r[0]) for r in c.execute(
                "select oid::bigint from pg_class where oid = any(%s::oid[])",
                (orphans,)).fetchall()}
        oids |= set(orphans) - live
    cfg.destination.clear_quarantine(cfg.registration, oids)


def _refuse_denied_keys(cfg: RuntimeConfig, relations) -> list:
    """Relations whose merge key now intersects the EFFECTIVE deny map — possible only through
    a deny inherited by OID onto a renamed table (the compiler refuses an explicit one,
    `deny_column_is_key`). Withholding a key column breaks every merge, so the run refuses,
    exactly as a compile error would."""
    return sorted(f"{r.qualified}: {', '.join(sorted(set(r.key) & set(cfg.deny_columns.get((r.schema, r.table), ()))))}"
                  for r in relations
                  if set(r.key) & set(cfg.deny_columns.get((r.schema, r.table), ())))


def _forms(name: str) -> set:
    """A source identifier and its dlt-normalised spelling (`password$hash` lands
    `password_hash`): every comparison against a dlt schema or the destination uses both."""
    from dlt.common.normalizers.naming.snake_case import NamingConvention
    return {name, NamingConvention().normalize_identifier(name)}


def _pending_safe(cfg: RuntimeConfig, pipe, target: str) -> bool:
    """Settle a package a crashed run left pending in `pipe` (#4346, Codex). If the pipeline's
    schema already names a denied column of a table in this dataset, the package may carry the
    value (extracted before the deny existed): it is DISCARDED, never loaded. That is replay-safe —
    the checkpoint never moved past a load that did not commit, so the WAL (or the baseline)
    comes again, through the projection. Otherwise it is finished as #4345 requires."""
    if not pipe.has_pending_data:
        return False
    tables: dict = {}   # unioned: two denied relations may route to one destination table
    for (s, t), cols in cfg.deny_columns.items():
        if cfg.target_schema(s, t) == target:
            tables.setdefault(t, set()).update(cols)
    try:
        schema_tables = pipe.default_schema.tables if pipe.default_schema_name else {}
    except Exception:  # noqa: BLE001 — no schema yet: nothing loaded can name a denied column
        schema_tables = {}
    unsafe = sorted(f"{st}.{sc}" for t, cols in tables.items() for st in _forms(t)
                    if st in schema_tables
                    for c in cols for sc in _forms(c)
                    if sc in schema_tables[st].get("columns", {}))
    if unsafe:
        _log(f"discarding a pending dlt package in {target}: its schema names denied "
             f"column(s) {unsafe} — the load replays through the projection")
        pipe.drop_pending_packages()   # deletes only; abort_packages would also run a load
        return False
    return Destination.finish_pending(pipe)


def _dlt_schema_hits(pipelines, wanted: dict, selected_targets: set) -> list:
    """Denied columns in the dlt schema of each SELECTED table this run's pipelines hold — the
    second, lag-free signal (a fresh MotherDuck connection can miss a column for ~25 s after a
    write, gotcha `motherduck-catalog-lags-a-write`). A selected table's dlt schema is what its
    next merge re-creates from, so a denied column there is a finding in its own right. Frozen
    copies are not checked here: after their purge (a plain DROP COLUMN) dlt's schema still
    names the column, and nothing writes them again."""
    hits = []
    for (target, table), cols in wanted.items():
        if (target, table) not in selected_targets:
            continue
        try:
            pipe = pipelines.get(target)
            if not pipe.default_schema_name:
                continue
            present = {t: set(pipe.default_schema.get_table_columns(t))
                       for t in _forms(table) if t in pipe.default_schema.tables}
        except Exception:  # noqa: BLE001 — no schema in this process: the catalogue decides
            continue
        hits += [(target, t, c) for t, cols_there in present.items()
                 for col in cols for c in _forms(col) if c in cols_there]
    return hits


def _with_inherited_denies(cfg: RuntimeConfig, relations) -> RuntimeConfig:
    """The run's EFFECTIVE deny map (#4346): the manifest's, plus every table that now carries
    a denied OID under another name — a denied table renamed between runs, whose new name is
    selected, would otherwise be baselined (and watched) with no deny at all. Resolved once per
    run, so the baseline, the drain, drift and the tripwire all see the same projection."""
    import dataclasses
    base = _denied(cfg, relations)
    unbound: list = []
    by_oid = _denied_oids(cfg, base, unbound=unbound)
    attnums = _denied_attnums(cfg, base) if base else {}
    if not by_oid:
        return dataclasses.replace(cfg, deny_columns=base, deny_oids={}, deny_attnums=attnums,
                                   deny_unbound=tuple(unbound))
    import psycopg
    with psycopg.connect(cfg.source_dsn, autocommit=True, connect_timeout=10) as c:
        rows = c.execute("select c.oid, n.nspname, c.relname from pg_class c "
                         "join pg_namespace n on n.oid = c.relnamespace where c.oid = any(%s)",
                         (list(by_oid),)).fetchall()
    merged = {k: set(v) for k, v in base.items()}
    for oid, schema, table in rows:
        merged.setdefault((schema, table), set()).update(by_oid[int(oid)])
    inherited = sorted(k for k in merged if k not in base)
    if inherited:
        _log(f"deny columns inherited through a rename: {inherited}")
    return dataclasses.replace(
        cfg, deny_columns={k: tuple(sorted(v)) for k, v in merged.items() if v},
        deny_oids=by_oid, deny_attnums=attnums, deny_unbound=tuple(unbound))


def _check_denied_columns(cfg: RuntimeConfig, relations, *, run, targets,
                          pipelines=None) -> int:
    """Destination tripwire (#4346): a denied column present in a mirror or its merge-staging
    twin is a leak — it was loaded before the deny existed, or something bypassed the
    projection. Each hit is an EV_ERROR naming table and column, never a value; the run is then
    `partial`. Remedy: `resnapshot` a selected table; drop the column from a frozen copy."""
    denied = _denied(cfg, relations)
    if not denied:
        return 0
    # Unioned per DESTINATION table: a retired and a selected relation may route to the same
    # one, and a plain dict comprehension would let the later deny-list replace the earlier.
    wanted: dict = {}
    for (schema, table), cols in denied.items():
        wanted.setdefault((targets(schema, table), table), set()).update(cols)
    selected = {(r.schema, r.table) for r in relations}
    found = set(cfg.destination.denied_columns_present(wanted))
    if pipelines is not None:
        found |= set(_dlt_schema_hits(
            pipelines, wanted, {(targets(s, t), t) for s, t in selected}))
    hits = 0
    for (schema, table), cols in sorted(denied.items()):
        target = targets(schema, table)
        names = _forms(table)
        where = sorted(f"{s}.{t}.{c}" for s, t, c in found
                       if t in names and s in (target, f"{target}_staging"))
        if not where:
            continue
        hits += len(where)
        remedy = (drift.remedy(cfg.manifest_path, f"{schema}.{table}") if (schema, table) in selected
                  else "frozen copy: drop the column at the destination (runbook, #4346)")
        run.event(entity=f"{schema}.{table}", status=cp_status.EV_ERROR, unit="deny_columns",
                  error_detail=f"denied column(s) present at the destination: {', '.join(where)}",
                  extra={"columns": where, "remedy": remedy})
    return hits


# --------------------------------------------------------------------------- pipelines


class _Targets:
    """(source schema, table) -> target schema for one run's relation set. The compiled
    Relation carries the answer; anything outside the set (a truncate for a table that was
    narrowed out mid-batch, say) falls back to the manifest rules."""

    def __init__(self, cfg: RuntimeConfig, relations) -> None:
        self.cfg = cfg
        self.of: dict[tuple[str, str], str] = {
            (r.schema, r.table): (r.target_schema or cfg.target_schema(r.schema, r.table))
            for r in relations}

    def __call__(self, schema: str, table: str) -> str:
        return self.of.get((schema, table)) or self.cfg.target_schema(schema, table)

    def all(self) -> set[str]:
        return set(self.of.values())

    def source_of(self, target: str) -> dict[str, str]:
        """table -> source schema within one target dataset (table names are unique there:
        selectors.compile refuses a (target_schema, table) collision)."""
        return {t: s for (s, t), tgt in self.of.items() if tgt == target}


class _Pipelines:
    """One dlt pipeline per TARGET schema (= dlt dataset), created lazily, reused across batches."""

    def __init__(self, cfg: RuntimeConfig, target_schemas) -> None:
        self.cfg = cfg
        self._cache: dict[str, object] = {}
        # An explicit pipeline_name pins ONE pipeline; that is only unambiguous for a
        # single-dataset registration.
        self._pin = cfg.pipeline_name if len(set(target_schemas)) <= 1 else None

    def get(self, target_schema: str):
        if target_schema not in self._cache:
            self._cache[target_schema] = self.cfg.destination.pipeline_for_target(
                target_schema, self._pin, source_id=self.cfg.source_id)
        return self._cache[target_schema]


def _load_ids(info) -> list[str]:
    try:
        return [p.load_id for p in info.load_packages]
    except Exception:  # noqa: BLE001 — telemetry, never correctness
        return []


# --------------------------------------------------------------------------- phases


# Every baseline REPLACES the destination copy — keyed relations too. A merge on top of an
# older copy keeps every row the source deleted in between (a resumed snapshot, a slot-loss
# re-init, a table re-widened after `retired`), and the boundary skip then drops the very
# DELETE that used to heal it; an append_only copy has no key at all and simply doubles.
# dlt's `drop_resources` drops the table AND forgets it, which a bare `drop table` does not
# (dlt then skips the CREATE and the load dies "table does not exist"); it finds the table by
# the schema's `resource` attribute, which Destination.load keeps equal to the table name.
BASELINE_KWARGS = {"refresh": "drop_resources"}


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


def _emit_table_events(run, pipeline, unit: str, source_of: dict[str, str], tables: set[str],
                       extra: dict | None = None) -> int:
    """One event per loaded table, entity `<source schema>.table` — the SOURCE identity, which
    lifecycle/reconcile key on; `source_of` maps the dataset's table names back to it, since
    one target dataset may carry tables from several source schemas (#3079 review, D-13)."""
    counts = Destination.row_counts(pipeline)
    n = 0
    for table, rows in counts.items():
        if tables and table not in tables:
            continue
        entity = f"{source_of[table]}.{table}" if table in source_of else table
        run.event(entity=entity, status=cp_status.EV_OK, unit=unit, rows=rows, extra=extra)
        n += 1
    return n


def _initialize(cfg: RuntimeConfig, relations, *, run, owner, lc: Lifecycle,
                pipelines: _Pipelines, targets: _Targets, reason: str) -> int:
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
    handle = snapshot.create_slot_with_snapshot(cfg.source_dsn, cfg.slot,
                                                baseline_wait_s=cfg.baseline_wait_s)
    keys = {(r.schema, r.table): list(r.key) for r in relations}
    policies = {(r.schema, r.table): r.key_policy for r in relations}
    bound = _baseline_capture()
    try:
        with snapshot.snapshot_resources(cfg.source_dsn, handle.snapshot_name, desired,
                                         keys, key_policies=policies,
                                         deny_columns=_denied(cfg, relations),
                                         **bound,
                                         deny_by_oid=cfg.deny_oids, deny_attnums_by_oid=cfg.deny_attnums) as by_schema:
            handle.close()  # the import already happened; the exporter is no longer needed
            # Before any load: an interrupted load must not lose an OID first met here.
            _persist_bound(cfg, bound)
            by_target: dict[str, list] = {}   # a snapshot resource is named after its table
            for source_schema, resources in by_schema.items():
                for res in resources:
                    by_target.setdefault(targets(source_schema, res.name), []).append(res)
            for target, resources in sorted(by_target.items()):
                owner.assert_held()
                pipe = pipelines.get(target)
                _pending_safe(cfg, pipe, target)   # never let `run` load a stale package instead
                pipe.run(resources, **BASELINE_KWARGS)
                owner.assert_held()   # the load blocked for a while; the lock may be gone
                _emit_table_events(run, pipe, "snapshot", targets.source_of(target),
                                   {t for s, t in desired if targets(s, t) == target},
                                   {"reinit_reason": reason})
    finally:
        handle.close()

    owner.assert_held()
    # The exported snapshot IS the consistent point: nothing below it is ever streamed, so the
    # boundary is recorded for uniformity (every baseline has one), not because it filters.
    cfg.destination.write_baseline_lsns(
        cfg.registration, {(r.schema, r.table): handle.consistent_point_lsn for r in relations})
    cfg.destination.write_checkpoint(cfg.registration, cfg.slot, handle.consistent_point_lsn)
    # A full re-baseline IS a resnapshot: the registry is rewritten with no diff, so a key
    # change the previous signature would have blocked on is already resolved. Written BEFORE
    # any relation is marked streaming, so a streaming relation always has a recorded key — a
    # later rename's queued WAL is merged on it (`_retired_keys`, #4338).
    drift.record_all(cfg, relations, run=run)
    # A full re-baseline rebuilt every copy: nothing it read stays quarantined (#4346).
    _clear_rebuilt_quarantine(cfg, bound, relations)
    for r in relations:
        lc.ensure(r.schema, r.table, CATCHING_UP, reason)
        lc.ensure(r.schema, r.table, STREAMING, reason)
    return handle.consistent_point_lsn


def _take_baseline(cfg: RuntimeConfig, rel, *, run, owner, pipelines: _Pipelines,
                   targets: _Targets, unit: str, extra: dict) -> int:
    """One consistent baseline of a table the publication ALREADY carries. Returns the boundary
    LSN, persisted before the lifecycle moves on: a baseline whose boundary was not recorded
    would let the drain re-land what it just read."""
    pipe = pipelines.get(targets(rel.schema, rel.table))
    bound = _baseline_capture()
    with snapshot.consistent_baseline(cfg.source_dsn, cfg.slot, (rel.schema, rel.table),
                                      list(rel.key), key_policy=rel.key_policy,
                                      baseline_wait_s=cfg.baseline_wait_s,
                                      deny_columns=_denied(cfg, [rel]).get(
                                          (rel.schema, rel.table), ()),
                                      **bound,
                                      deny_by_oid=cfg.deny_oids, deny_attnums_by_oid=cfg.deny_attnums) as (resource, boundary):
        _persist_bound(cfg, bound)   # before the load: an interruption must not lose it
        _pending_safe(cfg, pipe, targets(rel.schema, rel.table))
        pipe.run(resource, **BASELINE_KWARGS)
    owner.assert_held()   # the load blocked for a while; the lock may be gone
    cfg.destination.write_baseline_lsns(cfg.registration, {(rel.schema, rel.table): boundary})
    _clear_rebuilt_quarantine(cfg, bound, [rel])   # rebuilt
    _emit_table_events(run, pipe, unit, {rel.table: rel.schema}, {rel.table},
                       {**extra, "boundary_lsn": boundary})
    return boundary


def _baseline(cfg: RuntimeConfig, rel, *, run, owner, lc: Lifecycle, pipelines: _Pipelines,
              targets: _Targets, detail: str) -> None:
    """Full read of a table just ADDED to the publication, at a consistent point — the drain
    drops this relation's transactions below it, so the overlap with the WAL lands once."""
    owner.assert_held()
    # `snapshotting`, not `catching_up`: a crash during this load must leave the ONE state
    # _resume_interrupted_snapshots retakes a baseline from (idempotent — the caller already
    # marked it before the publication add).
    _to_snapshotting(lc, rel.schema, rel.table, detail)
    _take_baseline(cfg, rel, run=run, owner=owner, pipelines=pipelines, targets=targets,
                   unit="backfill", extra={"detail": detail})
    lc.ensure(rel.schema, rel.table, CATCHING_UP, detail)
    lc.ensure(rel.schema, rel.table, STREAMING, detail)


def _resume_interrupted_snapshots(cfg: RuntimeConfig, relations, *, run, owner,
                                  lc: Lifecycle, pipelines: _Pipelines,
                                  targets: _Targets) -> list:
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
        _take_baseline(cfg, r, run=run, owner=owner, pipelines=pipelines, targets=targets,
                       unit="snapshot", extra={"detail": "resumed an interrupted snapshot"})
        lc.ensure(r.schema, r.table, CATCHING_UP, "resumed an interrupted snapshot")
        lc.ensure(r.schema, r.table, STREAMING, "resumed an interrupted snapshot")
        resumed.append((r.schema, r.table))
    if resumed:
        _log(f"resumed interrupted snapshots: {resumed}")
    return resumed


def _reconcile_selection(cfg: RuntimeConfig, relations, *, run, owner, lc: Lifecycle,
                         pipelines: _Pipelines, targets: _Targets,
                         blocked: set | None = None) -> tuple[list, list]:
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
                      targets=targets, detail="added to selection")
    for schema, table in dropped:
        if (schema, table) in (blocked or set()):
            continue   # out of the publication for drift, not retired from the selection
        if lc.state_of(schema, table) is None:
            # Published by an earlier drainer (the pilot's 50) and never selected here: its
            # membership of the publication is the same evidence _adopt_streaming reads, and a
            # `retired` row with no history would say the table was never live at all.
            lc.transition(schema, table, STREAMING, "adopted: already in the publication")
        # `freeze`: the destination copy stays as a frozen mirror — bronze never deletes history.
        lc.ensure(schema, table, RETIRED, f"dropped from selection ({cfg.retirement_policy})")
    if dropped:
        _log(f"dropped from publication: {dropped} (retirement={cfg.retirement_policy})")
    _retire_unselected(cfg, lc, desired, skip=blocked or set())
    publication.ensure_replica_identity(cfg.source_dsn, desired, cfg.replica_identity)
    return added, dropped


def _retire_unselected(cfg: RuntimeConfig, lc: Lifecycle, desired, *, skip=frozenset()) -> list:
    """A relation the lifecycle knows that is no longer selected is retired even when the
    publication no longer names it — a source-side DROP or RENAME takes it out of the
    publication by itself, so `publication.reconcile` never reports it as dropped, and its row
    would otherwise say `streaming` for ever. Drift-blocked relations (`skip`) are out of the
    selection for drift, not retired."""
    wanted = set(desired) | set(skip)
    retired = []
    for (schema, table), state in sorted(lc.known().items()):
        if state == RETIRED or (schema, table) in wanted:
            continue
        lc.ensure(schema, table, RETIRED,
                  f"no longer selected and gone from the publication ({cfg.retirement_policy})")
        retired.append((schema, table))
    if retired:
        _log(f"retired (no longer selected): {retired} (retirement={cfg.retirement_policy})")
    return retired


def _retired_keys(cfg: RuntimeConfig, lc: Lifecycle, active) -> dict:
    """Merge keys for RETIRED relations, from their last recorded signature. WAL written before
    a rename still names the old relation; the stream would otherwise look its key up in the
    source catalog, find nothing (the name is gone) and fall back to REPLICA IDENTITY FULL's
    every-column key — an UPDATE would then land as a second row in the frozen copy."""
    active_keys = {(r.schema, r.table) for r in active}
    retired = [k for k, s in lc.known().items() if s == RETIRED and k not in active_keys]
    if not retired:
        return {}
    # One with no signature (the pilot's adopted-then-retired tables) falls back to the source
    # catalog in the stream; only a record whose key resolves NOWHERE is reported, by the
    # drain (`Batch.key_problems`), so an idle retired table never raises anything.
    signatures = cfg.destination.read_signatures(cfg.registration)
    return {k: tuple(signatures[k].get("key") or ()) for k in retired if k in signatures}


def _drain(cfg: RuntimeConfig, relations, *, run, owner, pipelines: _Pipelines,
           targets: _Targets, retired_keys: dict | None = None) -> dict:
    """Batch loop. The slot hands over items grouped by SOURCE schema; they are regrouped by
    TARGET schema here (one dlt pipeline per dataset). The checkpoint moves only after every
    dataset in the batch committed; the slot is advanced only to a checkpointed LSN."""
    from .selectors import APPEND_ONLY, PRIMARY_KEY
    keys = {(r.schema, r.table): list(r.key) for r in relations}
    policies = {(r.schema, r.table): r.key_policy for r in relations}
    for rel, key in (retired_keys or {}).items():
        if rel not in keys:
            keys[rel] = list(key)
            policies[rel] = PRIMARY_KEY if key else APPEND_ONLY
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
    reported_key_problems: set = set()
    reported_unreviewed: set = set()
    quarantined_now: set = set()
    # The reviewed shape of each relation that carries a deny (#4346, Sonnet F1): its stored
    # signature's columns. Anything else the decoder meets there is withheld until reviewed.
    signatures = cfg.destination.read_signatures(cfg.registration) if cfg.deny_columns else {}
    # Keyed by the relation's OID where the signature records one (it follows a table rename);
    # by name only for a signature written before the OID was recorded.
    approved_by_oid = {int(sig["oid"]): list(sig.get("columns", {}))
                       for sig in signatures.values() if sig and sig.get("oid")}
    approved = {k: list((sig or {}).get("columns", {})) for k, sig in signatures.items()
                if sig and not sig.get("oid")}
    # The proof inputs (#4346, Codex round 16): each relation's reviewed layout (its baseline),
    # the OIDs whose deny was bound at a trusted moment (this run's start, or a baseline), and
    # the relations already quarantined, whose changes stay skipped until a resnapshot.
    deny_on = bool(cfg.deny_columns)
    layouts = cfg.destination.read_reviewed_layouts(cfg.registration) if deny_on else {}
    held = cfg.destination.read_quarantined_oids(cfg.registration) if deny_on else set()
    for batch in stream.drain(cfg.source_dsn, cfg.slot, cfg.publication, start_lsn,
                              target_batch_size=cfg.target_batch_size,
                              keys_by_relation=keys,
                              policies_by_relation=policies,
                              baseline_lsn_by_relation=baselines,
                              deny_columns_by_relation=_denied(cfg, relations),
                              deny_columns_by_oid=cfg.deny_oids,
                              approved_columns_by_relation=approved,
                              approved_columns_by_oid=approved_by_oid,
                              deny_attnums_by_oid=cfg.deny_attnums,
                              reviewed_layout_by_oid=layouts,
                              bound_oids=(set(cfg.deny_oids) | set(cfg.deny_attnums)
                                          if deny_on else None),
                              quarantined_oids=held):
        owner.assert_held()
        for oid, (schema, table, why) in sorted(batch.quarantined.items()):
            run.event(entity=f"{schema}.{table}", status=cp_status.EV_ERROR, unit="deny_columns",
                      error_detail=(f"changes NOT loaded: {why}. The table carries denied "
                                    "columns, so nothing of it loads until a resnapshot "
                                    "re-reviews it; bronze is stale for it until then"),
                      extra={"action": "quarantined", "oid": oid,
                             "remedy": drift.remedy(cfg.manifest_path, f"{schema}.{table}")})
        for (schema, table), cols in sorted(batch.unreviewed.items()):
            if (schema, table) not in reported_unreviewed:
                reported_unreviewed.add((schema, table))
                run.event(entity=f"{schema}.{table}", status=cp_status.EV_ERROR,
                          unit="deny_columns",
                          error_detail=(f"column(s) {', '.join(cols)} on a table with denied "
                                        "columns are not in its reviewed signature and were "
                                        "WITHHELD: add them to source.deny.columns, or "
                                        "resnapshot the table to approve"),
                          extra={"withheld_columns": cols,
                                 "remedy": drift.remedy(cfg.manifest_path, f"{schema}.{table}")})
        if batch.denied_seen:
            # FIRST, before any destination change: an OID the decoder matched to a denied name
            # keeps its deny even if this batch's load fails and the name moves before the
            # retry (#4346, Codex round 10).
            _record_denied(cfg, batch.denied_seen)
        if batch.quarantined:
            # Before the checkpoint passes the skipped changes: the relation stays marked
            # (and reported every tick) until a resnapshot rebuilds it (Codex round 14).
            cfg.destination.record_quarantine(cfg.registration, batch.quarantined)
            quarantined_now |= set(batch.quarantined)   # OIDs
        # TRUNCATEs first: the consumer already discarded a keyed relation's earlier rows of
        # this batch, so "empty, then load what followed" is the source's own order. A replay
        # after a crash deletes again and re-merges — idempotent.
        for (schema, table), why in sorted(batch.key_problems.items()):
            if (schema, table) not in reported_key_problems:
                reported_key_problems.add((schema, table))
                run.event(entity=f"{schema}.{table}", status=cp_status.EV_ERROR,
                          unit="merge_key", error_detail=why)
        load_ids: list[str] = []
        by_target: dict[str, list] = {}
        for source_schema, items in batch.items_by_namespace.items():
            for item in items:
                table = Destination.table_of(item)
                target = (targets(source_schema, table) if table
                          else cfg.target_schema(source_schema))
                by_target.setdefault(target, []).append(item)
        # A replay after a crash may find a pipeline with a package left pending (#4345 Codex
        # adversarial: a later load round normalized, never loaded). Finish it BEFORE the
        # truncates below, which then remove it again — after them it would resurrect rows.
        for target in sorted(set(by_target) | {targets(t["namespace"], t["table"])
                                               for t in batch.truncates}):
            if _pending_safe(cfg, pipelines.get(target), target):
                owner.assert_held()
        for t in batch.truncates:
            keyed = policies.get((t["namespace"], t["table"])) != APPEND_ONLY
            applied = False
            if keyed:
                applied = cfg.destination.delete_all_rows(targets(t["namespace"], t["table"]),
                                                          t["table"])
            run.event(entity=f"{t['namespace']}.{t['table']}", status=cp_status.EV_OK,
                      unit="truncate",
                      extra={"lsn": t["lsn"], "commit_lsn": t["commit_lsn"],
                             "applied": "delete_all" if keyed else "recorded_only",
                             "destination_table_existed": applied})
        for target, items in sorted(by_target.items()):
            pipe = pipelines.get(target)
            # Usually one round; more only when a table changed description mid-batch (#4345).
            for round_items in Destination.rounds(items):
                info = Destination.load(pipe, round_items, resource_name=cfg.slot)
                # The load is the one long blocking call per batch: Postgres may have reaped
                # the owner backend meanwhile, freeing the lock to a peer. Checkpointing or
                # advancing the slot without the lock would hand that peer a hole; re-assert
                # first, and leave the committed load replayable (merge / lsn-merge make the
                # replay exact).
                owner.assert_held()
                load_ids += _load_ids(info)
                rows += sum(Destination.row_counts(pipe).values())
                _emit_table_events(run, pipe, "cdc", targets.source_of(target), set())
        if batch.last_commit_lsn:
            owner.assert_held()
            cfg.destination.write_checkpoint(cfg.registration, cfg.slot,
                                             batch.last_commit_lsn, load_ids)
            stream.advance(cfg.source_dsn, cfg.slot, batch.last_commit_lsn)
        batches += 1
    return {"batches": batches, "rows": rows, "start_lsn": start_lsn,
            "quarantined": quarantined_now}


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
    cfg = _with_inherited_denies(cfg, relations)
    refused = _refuse_denied_keys(cfg, relations)
    if refused:
        run.event(entity="_deny_columns", status=cp_status.EV_ERROR, unit="deny_columns",
                  error_detail="a merge key includes a denied column (inherited through a "
                               f"rename): {'; '.join(refused)} — nothing was replicated",
                  extra={"refused": refused})
        return {"paused": True,
                "outcome": _outcome(relations, [], errors=run.errors, paused=True)}
    if cfg.deny_unbound:
        # Fail closed (Codex round 16): a denied column that binds to no attnum may have been
        # renamed before it was ever bound — any column of its table could be the secret.
        run.event(entity="_deny_columns", status=cp_status.EV_ERROR, unit="deny_columns",
                  error_detail=("denied column(s) bound to no column of their table: "
                                f"{', '.join(cfg.deny_unbound)} — nothing was replicated. "
                                "If the column was renamed, add the new name to "
                                "source.deny.columns; if it does not exist, remove the entry"),
                  extra={"unbound": list(cfg.deny_unbound)})
        return {"paused": True,
                "outcome": _outcome(relations, [], errors=run.errors, paused=True)}
    lc = Lifecycle(cfg.destination, cfg.registration, run)
    targets = _Targets(cfg, relations)
    pipelines = _Pipelines(cfg, targets.all())

    slot = telemetry.slot_probe(cfg.source_dsn, cfg.slot)
    lost = slot is not None and slot.get("wal_status") == "lost"
    if (slot is None or lost) and cfg.slot_loss_policy == "pause" and slot is not None:
        for r in relations:
            # State-aware: a relation selected since the last tick has no row (and a re-selected
            # one is `retired`); neither has a legal edge to resnapshot_required (#4338).
            _mark_resnapshot_required(lc, r.schema, r.table, "slot lost; slot_loss_policy=pause")
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
                # State-aware (#4338): a newly selected or re-selected relation is routed
                # straight to `snapshotting` by _initialize instead of aborting the tick here.
                _mark_resnapshot_required(lc, r.schema, r.table, reason)
            publication.drop_publication(cfg.source_dsn, cfg.publication)
            snapshot.drop_slot_if_exists(cfg.source_dsn, cfg.slot)
        _initialize(cfg, relations, run=run, owner=owner, lc=lc, pipelines=pipelines,
                    targets=targets, reason=reason)
        # A rebuilt source (slot and publication gone) may no longer carry a table the
        # lifecycle still calls live; nothing else would ever retire it.
        _retire_unselected(cfg, lc, [(r.schema, r.table) for r in relations])
        active = relations
    else:
        _adopt_streaming(lc, relations,
                         publication.published_relations(cfg.source_dsn, cfg.publication),
                         existing=cfg.destination.destination_tables(targets.all()),
                         targets=targets)
        outcome = drift.scan(cfg, relations, run=run, lc=lc, owner=owner,
                             manifest_path=cfg.manifest_path)
        blocked = outcome.blocked
        active = outcome.active(relations)
        _reconcile_selection(cfg, active, run=run, owner=owner, lc=lc, pipelines=pipelines,
                             targets=targets, blocked=blocked)
        _resume_interrupted_snapshots(cfg, active, run=run, owner=owner, lc=lc,
                                      pipelines=pipelines, targets=targets)

    result = _drain(cfg, active, run=run, owner=owner, pipelines=pipelines, targets=targets,
                    retired_keys=_retired_keys(cfg, lc, active))
    result["drift_blocked"] = sorted(blocked)
    # Re-resolved: OIDs bound during this run may now sit under a name the startup map lacks.
    cfg = _with_inherited_denies(cfg, relations)
    for oid, (schema, table, why, since) in sorted(
            cfg.destination.read_quarantine(cfg.registration).items()):
        if oid in result["quarantined"]:
            continue   # already reported by this run's drain
        run.event(entity=f"{schema}.{table}", status=cp_status.EV_ERROR, unit="deny_columns",
                  error_detail=(f"changes were skipped since {since} ({why}); bronze is stale "
                                "for this table until it is resnapshotted"),
                  extra={"action": "quarantine_pending", "oid": oid,
                         "remedy": drift.remedy(cfg.manifest_path, f"{schema}.{table}")})
    result["denied_columns_at_destination"] = _check_denied_columns(
        cfg, relations, run=run, targets=targets, pipelines=pipelines)

    probe = telemetry.slot_probe(cfg.source_dsn, cfg.slot)
    checkpoint = cfg.destination.read_checkpoint(cfg.registration)
    published = publication.published_relations(cfg.source_dsn, cfg.publication)
    dest_tables = cfg.destination.destination_tables(
        {targets(r.schema, r.table) for r in active})
    telemetry.emit_probe(run, probe, {
        "selected_tables": len(active),
        "published_tables": len(published),
        "destination_tables": len(dest_tables),
        "drift_blocked_tables": len(blocked),
        "denied_columns_at_destination": result["denied_columns_at_destination"],
        "checkpoint_lsn": checkpoint[0] if checkpoint else None,
        "checkpoint_age_s": telemetry.checkpoint_age_s(_checkpoint_time(cfg)),
    }, pull_ms=int((time.monotonic() - t0) * 1000))

    from .selectors import APPEND_ONLY
    telemetry.emit_reconcile(run, telemetry.reconcile_counts(
        cfg.source_dsn, cfg.destination, [(r.schema, r.table) for r in active],
        targets.of, threshold=cfg.reconcile_threshold,
        exclude=list(cfg.reconcile_exclude),
        append_only={(r.schema, r.table) for r in active
                     if r.key_policy == APPEND_ONLY}))
    result["outcome"] = _outcome(relations, active, errors=run.errors, paused=False)
    return result


def _adopt_streaming(lc: Lifecycle, relations, published: set, *, existing: set,
                     targets) -> None:
    """A steady-state run over tables with no lifecycle row (a takeover from the pilot, or a
    destination rebuilt underneath us): record what is observably true. Membership of the
    publication is the evidence — a newly selected table is NOT adopted, it gets a baseline.

    Adoption also needs the DESTINATION table to exist: a table RENAMED at the source stays in
    the publication under its new name (membership is by OID), and its earlier rows are in no
    WAL record under that name, so with no destination copy it is routed to `snapshotting` and
    baselined this tick (#4338). Both signals are re-read on every tick, so a takeover that
    crashed half-way decides the same way on retry — an adopted table is never re-baselined
    (which would replace an append_only history with the source's current rows)."""
    for r in relations:
        key = (r.schema, r.table)
        if key not in published:
            continue
        state = lc.state_of(r.schema, r.table)
        if state in (RETIRED, DISCOVERED):
            # Re-selected while already published (a rename undone, a restored source whose
            # publication still names it): `publication.reconcile` will not report it as
            # added, so nothing else would re-baseline its frozen copy. DISCOVERED is the same
            # case interrupted between `_to_snapshotting`'s two writes.
            _to_snapshotting(lc, r.schema, r.table, "re-selected after retirement — baselining")
        elif state is None and (targets(r.schema, r.table), r.table) in existing:
            lc.ensure(r.schema, r.table, STREAMING, "adopted: already in the publication")
        elif state is None:
            _to_snapshotting(lc, r.schema, r.table,
                             "published, but no lifecycle row and no destination copy "
                             "(renamed at the source?) — baselining")


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
        except snapshot.BaselineBlocked as exc:
            # The relation(s) mid-baseline stay `snapshotting` (never advanced past it) — the
            # next tick's _resume_interrupted_snapshots retakes them once the blocker clears.
            from .manifest import redact
            run.event(entity="_snapshot", status=cp_status.EV_ERROR, unit="snapshot",
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
        table_schemas=[(r.match, r.schema) for r in m.destination.table_schemas],
        pipeline_name=m.destination.pipeline_name,
        target_batch_size=m.replication.target_batch_size,
        replica_identity=m.replication.replica_identity,
        slot_loss_policy=m.replication.slot_loss_policy,
        retirement_policy=m.destination.retirement_policy,
        reconcile_exclude=list(m.control.reconcile_exclude),
        deny_columns={tuple(q.split(".", 1)): tuple(sorted(set(cols)))
                      for q, cols in m.source.deny_columns.items() if cols},
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


def _run_log_dsn() -> str | None:
    # #4078: the CDC logs to its own CDC log DB. CONTROL_PG_DSN is accepted for one release, and
    # must lose: for the control-plane CDC it is ALSO the source being mirrored.
    return os.environ.get("CDC_LOG_PG_DSN") or os.environ.get("CONTROL_PG_DSN")


def _control_plane(m, dev: bool) -> ControlPlane:
    control_dsn = _run_log_dsn()
    if not control_dsn and not dev:
        sys.exit("FATAL: CDC_LOG_PG_DSN not set (CONTROL_PG_DSN is accepted for one release). "
                 "The CDC log DB is a HARD dependency (#4078) — set it, or pass --dev for a "
                 "local WAL-only run.")
    wal_dir = m.control.wal_dir or os.environ.get("CONTROL_WAL_DIR", "control_wal")
    return ControlPlane(m.control.extractor, dsn=None if dev else control_dsn, wal_dir=wal_dir)


def run(manifest, *, dev: bool = False, manifest_path: str = "<manifest>") -> int:
    cp = _control_plane(manifest, dev)
    try:
        dsn, plan = _compile(manifest)
    except SystemExit:
        raise   # a manifest refusal already explained itself; nothing ran
    except Exception as exc:
        # Discovery runs BEFORE the control-plane run opens, so a source outage here used to
        # exit 1 with no control.run row at all — which the runbook's first trust step reads as
        # "the cron never fired". Record it as the failed tick it is (#3079 final review).
        _record_failed_run(cp, "_source", "discovery", exc)
        raise
    cfg = config_from_manifest(manifest, dsn=dsn, destination=_destination(manifest))
    cfg.manifest_path = manifest_path
    if not plan.relations:
        _refuse_empty_selection(cfg, cp=cp)
    return execute(cfg, plan.relations, cp=cp)


def _record_failed_run(cp: ControlPlane, entity: str, unit: str, exc: BaseException) -> None:
    """One failed control.run row + one redacted error event for a tick that died before
    `execute` could own it. Best effort: an unreachable control plane must not mask the cause."""
    from .manifest import redact
    try:
        with cp.run("cdc") as run:
            run.event(entity=entity, status=cp_status.EV_ERROR, unit=unit,
                      error_detail=redact(f"{type(exc).__name__}: {exc}"))
            run.finish(cp_status.RUN_FAILED, stop_reason=cp_status.STOP_ERROR, n_err=1)
    except Exception as cp_exc:  # noqa: BLE001
        _log(f"could not record the failed run: {redact(f'{type(cp_exc).__name__}: {cp_exc}')}")


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
        target = manifest.destination.target_schema(schema, table)
        print(f"  {schema}.{table:<40} -> {target}.{table:<40} {state}")
    return 0


def resnapshot(manifest, *, tables: Sequence[str] | None = None, yes: bool = False,
               dev: bool = False) -> int:
    """Drop the destination table(s) and retake a baseline. DESTRUCTIVE — requires `yes`
    (D6's "separately approved drop"); the retirement policy never drops on its own."""
    if not yes:
        sys.exit("resnapshot DROPS destination tables — re-run with --yes to confirm")
    dsn, plan = _compile(manifest)
    wanted = set(tables or [])
    wanted_relations = [r for r in plan.relations if not wanted or r.qualified in wanted]
    if not wanted_relations:
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
            cfg = _with_inherited_denies(cfg, plan.relations)
            refused = _refuse_denied_keys(cfg, plan.relations)
            if refused:
                raise RuntimeError("a merge key includes a denied column (inherited through a "
                                   f"rename): {'; '.join(refused)}")
            if cfg.deny_unbound:
                raise RuntimeError("denied column(s) bound to no column of their table: "
                                   f"{', '.join(cfg.deny_unbound)} (#4346)")
            lc = Lifecycle(cfg.destination, cfg.registration, run_ctx)
            targets = _Targets(cfg, plan.relations)
            pipelines = _Pipelines(cfg, targets.all())
            for rel in wanted_relations:
                owner.assert_held()
                # Drop FIRST, in `resnapshot_required`. A drop that fails must leave the
                # table exactly where a failed resnapshot belongs — not `snapshotting`, which
                # the next run would resume by merging a fresh baseline INTO the undropped
                # copy, resurrecting rows the source deleted (#3079 review). The publication
                # is only touched once the destination copy is really gone.
                _mark_resnapshot_required(lc, rel.schema, rel.table, "operator resnapshot")
                dropped = cfg.destination.drop_table(targets(rel.schema, rel.table), rel.table)
                _log(f"resnapshot {rel.qualified}: dropped {dropped}")
                _to_snapshotting(lc, rel.schema, rel.table, "operator resnapshot")
                # Re-admits THIS key-changed table drift.scan removed; a no-op otherwise. Any
                # other relation still `resnapshot_required` stays out: re-admitted for even
                # one tick, its changes would stream under the new key into the old-keyed
                # copy — the double-load the block exists to prevent (#3079 final review).
                publication.reconcile(cfg.source_dsn, cfg.publication, [
                    (r.schema, r.table) for r in plan.relations
                    if r in wanted_relations
                    or lc.state_of(r.schema, r.table) != RESNAPSHOT_REQUIRED])
                pipe = pipelines.get(targets(rel.schema, rel.table))
                bound = _baseline_capture()
                with snapshot.consistent_baseline(
                        cfg.source_dsn, cfg.slot, (rel.schema, rel.table), list(rel.key),
                        key_policy=rel.key_policy,
                        baseline_wait_s=cfg.baseline_wait_s,
                        deny_columns=_denied(cfg, [rel]).get(
                            (rel.schema, rel.table), ()),
                        **bound,
                        deny_by_oid=cfg.deny_oids, deny_attnums_by_oid=cfg.deny_attnums) as (res, boundary):
                    # `drop_resources` again here: the drop above went through OUR duckdb
                    # connection, and MotherDuck's catalog can still serve the dropped table
                    # to dlt's own connection for tens of seconds (gotcha
                    # `motherduck-catalog-lags-a-write`) — dlt would then skip the CREATE and
                    # the load would die. Doing the drop inside the same dlt client removes the
                    # cross-session read entirely.
                    _persist_bound(cfg, bound)   # before the load (see _take_baseline)
                    _pending_safe(cfg, pipe, targets(rel.schema, rel.table))
                    pipe.run(res, **BASELINE_KWARGS)
                owner.assert_held()
                cfg.destination.write_baseline_lsns(cfg.registration,
                                                    {(rel.schema, rel.table): boundary})
                _clear_rebuilt_quarantine(cfg, bound, [rel])
                _emit_table_events(run_ctx, pipe, "snapshot", {rel.table: rel.schema}, {rel.table},
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
        run_ctx.finish(cp_status.RUN_OK, stop_reason=cp_status.STOP_CLEAN, n_ok=len(wanted_relations))
    return 0
