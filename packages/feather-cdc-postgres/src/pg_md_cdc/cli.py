"""`python -m pg_md_cdc <command> <manifest>` (#3079 D1).

Constraints:
  - `validate` and `plan` MUTATE NOTHING — they open read-only connections and print. Anything
    that would change either database belongs to `run`.
  - no environment VALUE is ever printed; only the NAME of the variable is echoed.
  - `run`/`status`/`resnapshot` import the runtime lazily, so the compiler half stays usable
    (and testable) without dlt credentials or a destination.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Dict, List, Optional, Tuple

from . import discovery, generate_dbt, manifest as mf
from .adapters import AdapterRelation, get as get_adapter, known as known_adapters
from .selectors import CompiledPlan, compile as compile_selection

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _env(name: str) -> Optional[str]:
    val = os.environ.get(name)
    return val or None


def _resolve(m: mf.Manifest) -> Tuple[Optional[str], List[mf.Problem]]:
    """The source DSN, or a problem naming the variable that is unset — never its value."""
    dsn = _env(m.source.dsn_env)
    if not dsn:
        return None, [mf.Problem("env_not_set", "error", f"environment variable {m.source.dsn_env} is not set")]
    return dsn, []


def _adapter_relations(m: mf.Manifest, dsn: str) -> Tuple[List[AdapterRelation], List[mf.Problem]]:
    if m.source.discovery is None:
        return [], []
    try:
        fn = get_adapter(m.source.discovery.adapter)
    except KeyError:
        return [], [
            mf.Problem(
                "adapter_unknown",
                "error",
                f"discovery adapter {m.source.discovery.adapter!r} is unknown (known: {', '.join(known_adapters())})",
            )
        ]
    return list(fn(dsn, m.source.discovery.options)), []


def _compile(m: mf.Manifest, *, with_publication: bool) -> Tuple[Optional[CompiledPlan], List[mf.Problem], Dict]:
    problems = list(mf.validate_static(m))
    dsn, dsn_problems = _resolve(m)
    problems += dsn_problems
    if dsn is None:
        return None, problems, {}
    relations = discovery.discover(dsn)
    adapter_rels, adapter_problems = _adapter_relations(m, dsn)
    problems += adapter_problems
    published = discovery.publication_relations(dsn, m.replication.publication) if with_publication else None
    plan = compile_selection(m, relations, adapter_rels, published)
    extra: Dict = {}
    if with_publication:
        extra["publication_exists"] = published is not None
        extra["slot"] = discovery.slot_state(dsn, m.replication.slot)
    return plan, problems + list(plan.problems), extra


def _emit(payload: Dict, lines: List[str], as_json: bool) -> None:
    text = json.dumps(payload, indent=2, default=str) if as_json else "\n".join(lines)
    print(mf.redact(text))


def _problem_lines(problems: List[mf.Problem]) -> List[str]:
    if not problems:
        return ["problems: none"]
    return ["problems:"] + [f"  {p}" for p in problems]


def _relation_lines(plan: CompiledPlan) -> List[str]:
    width = max((len(r.qualified) for r in plan.relations), default=10)
    out = [f"relations ({len(plan.relations)}):"]
    for r in plan.relations:
        key = ",".join(r.key) or "-"
        idx = f" via {r.key_index}" if r.key_index else ""
        out.append(
            f"  {r.qualified:<{width}}  -> {r.target:<{width}}  {r.key_policy:<12} key={key}{idx}  "
            f"class={r.table_class}"
        )
    return out


def cmd_validate(args) -> int:
    m = mf.load(args.manifest)
    plan, problems, _ = _compile(m, with_publication=False)
    lines = [f"manifest: {args.manifest}", f"source_id: {m.source_id}", f"dsn from: ${m.source.dsn_env}"]
    payload: Dict = {"source_id": m.source_id, "problems": [p.__dict__ for p in problems]}
    if plan is not None:
        lines += _relation_lines(plan) + [f"selection_hash: {plan.selection_hash}"]
        payload["relations"] = [r.__dict__ for r in plan.relations]
        payload["selection_hash"] = plan.selection_hash
    lines += _problem_lines(problems)
    _emit(payload, lines, args.json)
    return 1 if mf.errors(problems) else 0


def cmd_plan(args) -> int:
    m = mf.load(args.manifest)
    plan, problems, extra = _compile(m, with_publication=True)
    lines = [f"manifest: {args.manifest}", f"source_id: {m.source_id}", "PLAN — nothing is mutated by this command"]
    payload: Dict = {"source_id": m.source_id, "problems": [p.__dict__ for p in problems], **extra}
    if plan is not None:
        lines += _relation_lines(plan)
        lines.append(f"publication {m.replication.publication}: " + ("exists" if extra.get("publication_exists") else "MISSING — would be created"))
        lines.append(f"  add ({len(plan.publication_add)}): " + (", ".join(f"{s}.{t}" for s, t in plan.publication_add) or "-"))
        lines.append(f"  drop ({len(plan.publication_drop)}): " + (", ".join(f"{s}.{t}" for s, t in plan.publication_drop) or "-"))
        lines.append(
            f"replica identity ALTERs ({len(plan.replica_identity_alters)}): "
            + (", ".join(f"{s}.{t}" for s, t in plan.replica_identity_alters) or "-")
        )
        lines.append("denied hits: " + (", ".join(plan.denied_hits) or "-"))
        lines.append(f"selection_hash: {plan.selection_hash}")
        slot = extra.get("slot") or {}
        lines.append(
            f"slot {m.replication.slot}: "
            + ("absent — would be created" if not slot.get("exists") else
               f"wal_status={slot.get('wal_status')} lag_bytes={slot.get('lag_bytes')} active={slot.get('active')}")
            + f" · server max_slot_wal_keep_size={slot.get('max_slot_wal_keep_size_bytes') or 'unlimited'}"
        )
        payload.update(
            relations=[r.__dict__ for r in plan.relations],
            publication_add=plan.publication_add,
            publication_drop=plan.publication_drop,
            replica_identity_alters=plan.replica_identity_alters,
            denied_hits=plan.denied_hits,
            selection_hash=plan.selection_hash,
        )
    lines += _problem_lines(problems)
    _emit(payload, lines, args.json)
    return 1 if mf.errors(problems) else 0


def cmd_generate_dbt(args) -> int:
    m = mf.load(args.manifest)
    plan, problems, _ = _compile(m, with_publication=False)
    if plan is None or mf.errors(problems):
        _emit({"problems": [p.__dict__ for p in problems]}, _problem_lines(problems), args.json)
        return 1
    out = pathlib.Path(args.out) if args.out else generate_dbt.default_out_path(m, REPO_ROOT)
    generate_dbt.write(m, plan, out)
    _emit(
        {"out": str(out), "tables": len(plan.relations)},
        [f"wrote {out} — {len(plan.relations)} tables + _dlt_loads"],
        args.json,
    )
    return 0


def _runner():
    """The runtime half (#3079 Phase 2). Contract expected here:

        runner.run(manifest: Manifest, *, dev: bool) -> int
        runner.status(manifest: Manifest) -> int
        runner.resnapshot(manifest: Manifest, *, tables: list[str] | None, yes: bool) -> int
    """
    from . import runner  # noqa: PLC0415  # TODO(runner): owned by the runtime half

    return runner


def cmd_run(args) -> int:
    return int(_runner().run(mf.load(args.manifest), dev=args.dev) or 0)


def cmd_status(args) -> int:
    return int(_runner().status(mf.load(args.manifest)) or 0)


def cmd_resnapshot(args) -> int:
    return int(_runner().resnapshot(mf.load(args.manifest), tables=args.table or None, yes=args.yes) or 0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pg_md_cdc", description="Declarative PostgreSQL → MotherDuck CDC runtime")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    for name, fn, helptext in (
        ("validate", cmd_validate, "static + read-only checks; prints the compiled relation set"),
        ("plan", cmd_plan, "validate + every change a run would make; mutates nothing"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("manifest")
        s.set_defaults(func=fn)

    s = sub.add_parser("generate-dbt", help="write dbt source declarations for this registration")
    s.add_argument("manifest")
    s.add_argument("--out", help="output path (default dbt_runner/dbt/models/<source_id>/_<source_id>_sources.yml)")
    s.set_defaults(func=cmd_generate_dbt)

    s = sub.add_parser("run", help="reconcile, snapshot and drain one tick")
    s.add_argument("manifest")
    s.add_argument("--dev", action="store_true", help="local destination, no control-plane writes")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="lifecycle, checkpoint and slot health")
    s.add_argument("manifest")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("resnapshot", help="re-baseline selected tables")
    s.add_argument("manifest")
    s.add_argument("--table", action="append", help="schema.table (repeatable); default every relation")
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    s.set_defaults(func=cmd_resnapshot)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except mf.ManifestError as exc:
        print(f"[error] {mf.redact(str(exc))}", file=sys.stderr)
        return 2
