"""Desired-state compiler: manifest + catalog + adapter → one deterministic relation set.

Constraints:
  - deny is evaluated on the COMPILED set, not on the include list — an adapter or a wildcard
    must not be able to smuggle a denied relation in (#3079 D12).
  - ordering is sorted everywhere, so `selection_hash` is a function of the selection alone.
  - a relation is always (schema, table); the target schema is derived per TABLE
    (`destination.table_schemas`, then `schema_mapping`), never assumed equal (D-13).
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .adapters import USER_CREATED, AdapterRelation
from .discovery import SourceRelation
from .manifest import Manifest, Problem, Selection

# Key policies, in the order the compiler prefers them.
PRIMARY_KEY = "primary_key"
UNIQUE_INDEX = "unique_index"
APPEND_ONLY = "append_only"
# Rejected, never compiled: an all-column merge key cannot mirror an UPDATE — pgoutput emits
# only the new tuple, so the old row is never matched and both versions stay (#3079 review).
FULL_ROW = "full_row"

# Featherbase's own platform tables. Physical-schema-faithful bronze keeps them where they are;
# the class is what lets a consumer filter them out (#3079 D3).
_SOURCE_SYSTEM = frozenset(
    {
        "table_def", "column_def", "person", "identifier_system", "role", "has_role",
        "permission", "data_scope",
        "share", "user_event", "access_log", "activity_log", "version", "migration", "site",
    }
)
_SOURCE_OPERATIONAL = frozenset({"background_job", "job_execution"})
# A selected column whose NAME looks secret-bearing and is not in `deny.columns` (#4346). A
# warning, never an error: the name is a hint, and `validate` must stay green on a false hit.
_SECRET_LIKE = re.compile(r"password|_hash$|token|secret", re.IGNORECASE)


@dataclass(frozen=True)
class Relation:
    schema: str
    table: str
    key: Tuple[str, ...]
    key_policy: str
    table_class: str
    module: Optional[str] = None
    target_schema: str = ""
    key_index: Optional[str] = None  # the unique index a UNIQUE_INDEX key came from
    description: Optional[str] = None
    origin: Optional[str] = None  # adapters.USER_CREATED | NAMED_SYSTEM | None
    # Columns that never leave the CDC process (`source.deny.columns`, #4346), sorted.
    deny_columns: Tuple[str, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def target(self) -> str:
        return f"{self.target_schema or self.schema}.{self.table}"


@dataclass(frozen=True)
class CompiledPlan:
    relations: List[Relation] = field(default_factory=list)
    selection_hash: str = ""
    publication_add: List[Tuple[str, str]] = field(default_factory=list)
    publication_drop: List[Tuple[str, str]] = field(default_factory=list)
    replica_identity_alters: List[Tuple[str, str]] = field(default_factory=list)
    denied_hits: List[str] = field(default_factory=list)
    problems: List[Problem] = field(default_factory=list)


def _matches(qualified: str, schema: str, sel: Selection) -> bool:
    if any(fnmatch.fnmatchcase(schema, pat) for pat in sel.schemas):
        return True
    return any(fnmatch.fnmatchcase(qualified, pat) for pat in sel.tables)


def _included(qualified: str, schema: str, sel: Selection) -> bool:
    """Include is a conjunction: the schema must be allowed AND, when table patterns are given,
    one must match. Table patterns alone (empty `schemas`) select by table pattern only."""
    schema_ok = any(fnmatch.fnmatchcase(schema, pat) for pat in sel.schemas)
    if not sel.tables:
        return schema_ok
    table_ok = any(fnmatch.fnmatchcase(qualified, pat) for pat in sel.tables)
    return table_ok if not sel.schemas else (schema_ok and table_ok)


def table_class(m: Manifest, schema: str, table: str, origin: Optional[str] = None) -> str:
    """First matching manifest rule wins; otherwise the defaults below. Loader-internal is
    checked first so a broad application rule cannot claim `_dlt_loads`. A relation the
    adapter offered as USER_CREATED is application data whatever it is called — a user's
    table named `share` is not the platform's `share`."""
    qualified = f"{schema}.{table}"
    for rule in m.classification.rules:
        if fnmatch.fnmatchcase(qualified, rule.match):
            return rule.table_class
    if table.startswith("_dlt_") or schema == "_cdc_runtime":
        return "loader_internal"
    if origin == USER_CREATED:
        return "application"
    if m.source_id == "control_plane" and schema == "control":
        return "warehouse_control"
    if table in _SOURCE_OPERATIONAL:
        return "source_operational"
    if table in _SOURCE_SYSTEM:
        return "source_system"
    return "application"


def _key_for(
    m: Manifest, rel: SourceRelation, problems: List[Problem]
) -> Tuple[Tuple[str, ...], str, Optional[str], bool]:
    """(key, policy, index_name, keep) per D6."""
    override = m.replication.key_overrides.get(rel.qualified)
    if override == APPEND_ONLY:
        return (), APPEND_ONLY, None, True
    if override == FULL_ROW:
        problems.append(
            Problem(
                "full_row_unsupported",
                "error",
                f"{rel.qualified}: full_row cannot mirror an UPDATE (the old row is never matched, "
                "so both versions would stay) — declare append_only for a history, or add a key",
            )
        )
        return (), FULL_ROW, None, False

    if rel.primary_key:
        return tuple(rel.primary_key), PRIMARY_KEY, None, True

    # A unique index is a merge key only when it is also NOT NULL: dlt joins the destination to
    # the staging table on key equality, and NULL = NULL is never true, so a nullable key row
    # would insert a duplicate on every load instead of updating.
    notnull = {c.name for c in rel.columns if c.not_null}
    for idx in rel.unique_indexes:
        if all(c in notnull for c in idx.columns):
            if m.replication.replica_identity != "full" and rel.replica_identity not in ("f", "i"):
                problems.append(
                    Problem(
                        "unique_key_needs_identity",
                        "warning",
                        f"{rel.qualified}: merging on unique index {idx.name!r}, but REPLICA IDENTITY is "
                        f"{rel.replica_identity!r} — DELETE will not carry those columns; set "
                        "replica_identity: full or ALTER ... REPLICA IDENTITY USING INDEX",
                    )
                )
            return tuple(idx.columns), UNIQUE_INDEX, idx.name, True

    if m.replication.tables_without_key == APPEND_ONLY:
        problems.append(
            Problem(
                "no_key_append_only",
                "warning",
                f"{rel.qualified}: no primary key and no NOT NULL unique index — loading append-only; "
                "updates and deletes are not promised at the destination",
            )
        )
        return (), APPEND_ONLY, None, True

    problems.append(
        Problem(
            "no_key_rejected",
            "error",
            f"{rel.qualified}: no primary key and no NOT NULL unique index; set "
            "replication.tables_without_key: append_only or add key_overrides for this table",
        )
    )
    return (), APPEND_ONLY, None, False


def compile(
    m: Manifest,
    discovered: Sequence[SourceRelation],
    adapter_relations: Optional[Sequence[AdapterRelation]] = None,
    published: Optional[Set[Tuple[str, str]]] = None,
) -> CompiledPlan:
    """Compile the manifest against a discovered catalog. Pure — no connection, no mutation."""
    problems: List[Problem] = []
    by_qualified = {r.qualified: r for r in discovered}

    adapter_allowed: Optional[Set[str]] = None
    adapter_meta: Dict[str, AdapterRelation] = {}
    if m.source.discovery is not None:
        adapter_meta = {f"{a.schema}.{a.table}": a for a in (adapter_relations or [])}
        adapter_allowed = set(adapter_meta)
        for q in sorted(adapter_allowed - set(by_qualified)):
            problems.append(
                Problem("adapter_phantom", "warning", f"{q}: selected by the adapter but not physically present — skipped")
            )

    denied: List[str] = []
    candidates: List[SourceRelation] = []
    leaves_of: Dict[str, List[str]] = {}
    for rel in discovered:
        if rel.parent:
            leaves_of.setdefault(rel.parent, []).append(rel.qualified)
    for rel in sorted(discovered, key=lambda r: (r.schema, r.table)):
        q = rel.qualified
        if not _included(q, rel.schema, m.source.include):
            continue
        if _matches(q, rel.schema, m.source.exclude):
            continue
        if adapter_allowed is not None and q not in adapter_allowed:
            continue
        if rel.kind == "p":
            # Leaves only: `pg_publication_tables` expands a parent to its leaves (so a parent
            # in the desired set is "added" again every tick), and pgoutput emits leaf
            # identity by default (so a parent's destination copy would stop receiving rows).
            # The leaves are selected by the same globs; the parent is named so the operator
            # can see what was skipped (#3079 review).
            problems.append(
                Problem(
                    "partitioned_parent_skipped",
                    "warning",
                    f"{q}: partitioned parent — not replicated as one relation; its partitions "
                    f"{', '.join(sorted(leaves_of.get(q, []))) or '(none yet)'} are selected "
                    "individually when the include globs match them",
                )
            )
            continue
        # Deny last, on what selection actually produced: a deny rule is a tripwire, so it must
        # fire only when something really would have been replicated (D12).
        if _matches(q, rel.schema, m.source.deny):
            denied.append(q)
            continue
        candidates.append(rel)

    for q in denied:
        problems.append(
            Problem("deny_hit", "error", f"{q} matches a deny rule but was selected — refusing to replicate it")
        )

    relations: List[Relation] = []
    for rel in candidates:
        key, policy, idx_name, keep = _key_for(m, rel, problems)
        if not keep:
            continue
        meta = adapter_meta.get(rel.qualified)
        deny_cols = _deny_columns_for(m, rel, key, problems)
        if deny_cols is None:
            continue
        relations.append(
            Relation(
                schema=rel.schema,
                table=rel.table,
                key=key,
                key_policy=policy,
                table_class=table_class(m, rel.schema, rel.table, meta.origin if meta else None),
                module=meta.module if meta else None,
                target_schema=m.destination.target_schema(rel.schema, rel.table),
                key_index=idx_name,
                description=meta.description if meta else None,
                origin=meta.origin if meta else None,
                deny_columns=deny_cols,
            )
        )

    seen_target: Dict[str, str] = {}
    for r in relations:
        prior = seen_target.get(r.target)
        if prior:
            problems.append(
                Problem(
                    "target_collision",
                    "error",
                    f"{prior} and {r.qualified} both map to {m.destination.database}.{r.target}",
                )
            )
        seen_target[r.target] = r.qualified

    desired = {(r.schema, r.table) for r in relations}
    add, drop = diff_publication(desired, published)
    alters = (
        sorted((r.schema, r.table) for r in relations if by_qualified[r.qualified].replica_identity != "f")
        if m.replication.replica_identity == "full"
        else []
    )

    return CompiledPlan(
        relations=relations,
        selection_hash=selection_hash(relations),
        publication_add=add,
        publication_drop=drop,
        replica_identity_alters=alters,
        denied_hits=denied,
        problems=problems,
    )


def _deny_columns_for(
    m: Manifest, rel: SourceRelation, key: Tuple[str, ...], problems: List[Problem]
) -> Optional[Tuple[str, ...]]:
    """The relation's column projection (#4346), or None when it cannot be honoured: a denied
    merge-key column would leave the mirror unable to merge, so the relation is refused. A name
    the catalogue does not carry is a warning (it may be dropped, or not yet added) and is still
    withheld, so a column added later can never flow before the manifest is re-read."""
    denied = sorted(set(m.source.deny_columns.get(rel.qualified, [])))
    present = {c.name for c in rel.columns}
    for col in denied:
        if col in key:
            problems.append(Problem(
                "deny_column_is_key", "error",
                f"{rel.qualified}.{col} is part of the merge key and cannot be withheld — "
                "the mirror could not merge without it"))
            return None
        if col not in present:
            problems.append(Problem(
                "deny_column_absent", "warning",
                f"{rel.qualified}.{col} is denied but not in the source catalogue — still withheld "
                "if it appears"))
    for col in sorted(present - set(denied)):
        if _SECRET_LIKE.search(col):
            problems.append(Problem(
                "secret_like_column", "warning",
                f"{rel.qualified}.{col} looks secret-bearing and is replicated — add it to "
                "source.deny.columns if it should never leave the source"))
    return tuple(denied)


def selection_hash(relations: Iterable[Relation]) -> str:
    """Stable over the selection AND its key policy — a table that silently changed from
    merge to append-only must produce a different hash in the control plane. A column
    projection (#4346) is part of the selection; it is appended only when present, so a
    registration with no column deny keeps the hash it always had."""
    lines = sorted(
        f"{r.schema}.{r.table}|{','.join(r.key)}|{r.key_policy}"
        + (f"|deny={','.join(r.deny_columns)}" if r.deny_columns else "")
        for r in relations)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def diff_publication(
    desired: Set[Tuple[str, str]], published: Optional[Set[Tuple[str, str]]]
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """(add, drop). `published is None` means the publication does not exist yet — everything
    is an add and nothing can be dropped."""
    if published is None:
        return sorted(desired), []
    return sorted(desired - published), sorted(published - desired)
