"""Source type → destination type, and drift classification (#3079 D7).

Constraints:
  - an unknown OID becomes text but NEVER silently: every fallback emits a Problem(warning)
    naming schema.table.column and the OID.
  - the integer family compiles to plain BIGINT on both the snapshot and the CDC path — dlt
    renders precision 32 as INTEGER, and one live table already carries both spellings.
  - drift kinds are a closed set; `severity == "breaking"` is what the runtime pauses on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pg_replication.schema_types import _PG_TYPES, _to_dlt_column_type

from .discovery import SourceRelation
from .manifest import Problem

# How faithfully the destination type carries the source value.
EXACT = "exact"
WIDENED = "widened"  # a strictly larger destination type; no value is lost
EXACT_AS_TEXT = "exact_as_text"  # round-trips through its text rendering (uuid)
LOSSY_TEXT = "lossy_text"  # text rendering only; arithmetic/ordering is not preserved
TEXT_FALLBACK = "text_fallback"  # no declared mapping — reported, never silent

_MAPPING: Dict[int, str] = {
    16: EXACT, 17: EXACT, 20: EXACT, 21: WIDENED, 23: WIDENED, 25: EXACT, 26: WIDENED,
    114: EXACT, 700: WIDENED, 701: EXACT, 1042: EXACT, 1043: EXACT, 1082: EXACT,
    1083: EXACT, 1114: EXACT, 1184: EXACT, 1700: EXACT, 3802: EXACT,
    19: EXACT_AS_TEXT, 2950: EXACT_AS_TEXT,
    1186: LOSSY_TEXT, 1266: LOSSY_TEXT,
}


@dataclass(frozen=True)
class CompiledType:
    dest_type: str  # dlt data_type: bigint | text | double | decimal | date | time | timestamp | json | bool | binary
    precision: Optional[int] = None
    scale: Optional[int] = None
    mapping: str = EXACT
    timezone: Optional[bool] = None


@dataclass(frozen=True)
class CompiledColumn:
    name: str
    type_oid: int
    typmod: int
    not_null: bool
    key_role: str  # key | none
    compiled: CompiledType


@dataclass(frozen=True)
class Drift:
    kind: str
    column: Optional[str]
    detail: str
    severity: str  # additive | warning | breaking | info


def compile_column(type_oid: int, typmod: int, not_null: bool = False, is_array: bool = False) -> CompiledType:
    """One source column's destination type. `not_null` is carried for the caller's schema hint;
    it never changes the type (a NOT NULL column still lands nullable after a key-only delete)."""
    if is_array:
        # pgoutput ships arrays as their text rendering ('{a,b}'); nothing downstream parses it.
        return CompiledType(dest_type="text", mapping=TEXT_FALLBACK)
    dlt_type = _to_dlt_column_type(type_oid, typmod)
    mapping = _MAPPING.get(type_oid, TEXT_FALLBACK)
    return CompiledType(
        dest_type=str(dlt_type["data_type"]),
        precision=dlt_type.get("precision"),
        scale=dlt_type.get("scale"),
        mapping=mapping,
        timezone=dlt_type.get("timezone"),
    )


def compile_relation(
    relation: SourceRelation, key: Tuple[str, ...] = ()
) -> Tuple[List[CompiledColumn], List[Problem]]:
    """Compile every column of one relation; surface each fallback as a warning."""
    cols: List[CompiledColumn] = []
    problems: List[Problem] = []
    for c in relation.columns:
        ct = compile_column(c.type_oid, c.typmod, c.not_null, c.is_array)
        if ct.mapping == TEXT_FALLBACK:
            reason = "array type" if c.is_array else f"OID {c.type_oid} has no declared mapping"
            problems.append(
                Problem(
                    "type_fallback",
                    "warning",
                    f"{relation.schema}.{relation.table}.{c.name} ({c.type_name}): {reason} — "
                    "landing as text; add it to schema_registry._MAPPING to make the choice explicit",
                )
            )
        cols.append(
            CompiledColumn(
                name=c.name,
                type_oid=c.type_oid,
                typmod=c.typmod,
                not_null=c.not_null,
                key_role="key" if c.name in key else "none",
                compiled=ct,
            )
        )
    return cols, problems


def known_oids() -> Tuple[int, ...]:
    return tuple(sorted(_PG_TYPES))


# --------------------------------------------------------------------------- signatures


def signature(relation: SourceRelation, key: Tuple[str, ...] = ()) -> dict:
    """The inspectable shape a drift comparison runs on. Plain dicts so it can be stored in
    `_cdc_runtime.schema_registry` and diffed across runs without unpickling anything."""
    cols, _ = compile_relation(relation, key)
    return {
        "schema": relation.schema,
        "table": relation.table,
        "key": list(key),
        "replica_identity": relation.replica_identity,
        "columns": {
            c.name: {
                "type_oid": c.type_oid,
                "typmod": c.typmod,
                "not_null": c.not_null,
                "dest_type": c.compiled.dest_type,
                "precision": c.compiled.precision,
                "scale": c.compiled.scale,
                "mapping": c.compiled.mapping,
            }
            for c in cols
        },
    }


_INT_RANK = {21: 1, 23: 2, 20: 3}


def _varchar_len(oid: int, typmod: int) -> Optional[int]:
    if oid in (1043, 1042) and typmod != -1:
        return typmod - 4
    return None


def _numeric_ps(typmod: int) -> Tuple[Optional[int], Optional[int]]:
    if typmod == -1:
        return None, None
    return ((typmod - 4) >> 16) & 65535, (typmod - 4) & 65535


def _is_widen(old: dict, new: dict) -> bool:
    o, n = old["type_oid"], new["type_oid"]
    if o == n:
        if o in (1043, 1042):
            ol, nl = _varchar_len(o, old["typmod"]), _varchar_len(n, new["typmod"])
            return ol is not None and (nl is None or nl > ol)
        if o == 1700:
            op, os_ = _numeric_ps(old["typmod"])
            np, ns = _numeric_ps(new["typmod"])
            if op is None:
                return False
            return np is None or (np >= op and ns == os_ and np > op)
        return False
    if o in _INT_RANK and n in _INT_RANK:
        return _INT_RANK[n] > _INT_RANK[o]
    if o in (1043, 1042) and n == 25:  # varchar/bpchar -> text
        return True
    if o == 700 and n == 701:  # float4 -> float8
        return True
    return False


def classify_drift(old_sig: dict, new_sig: dict) -> List[Drift]:
    """Compare two signatures of the SAME relation. Ordered and deterministic."""
    out: List[Drift] = []
    old_cols: Dict[str, dict] = old_sig.get("columns", {})
    new_cols: Dict[str, dict] = new_sig.get("columns", {})
    dropped = sorted(set(old_cols) - set(new_cols))
    added = sorted(set(new_cols) - set(old_cols))

    # One dropped and one added of the same destination type is far more often a rename than a
    # coincidental pair; report it as a rename so a human sees the real event, at warning level
    # because the runtime cannot prove it (logical replication carries rows, not the DDL).
    if len(dropped) == 1 and len(added) == 1 and old_cols[dropped[0]]["dest_type"] == new_cols[added[0]]["dest_type"]:
        out.append(
            Drift(
                "rename_column",
                added[0],
                f"{dropped[0]!r} disappeared and {added[0]!r} appeared with the same type "
                f"({new_cols[added[0]]['dest_type']}) — probably a rename",
                "warning",
            )
        )
        dropped, added = [], []

    for name in added:
        if new_cols[name]["not_null"]:
            out.append(
                Drift(
                    "add_not_null_column",
                    name,
                    "added NOT NULL — rows already in bronze keep NULL there; backfill or resnapshot to align",
                    "warning",
                )
            )
        else:
            out.append(Drift("add_nullable_column", name, "added nullable column", "additive"))
    for name in dropped:
        out.append(
            Drift(
                "drop_column",
                name,
                "dropped at the source — bronze keeps the column and its history (dlt never destroys one)",
                "breaking",
            )
        )
    for name in sorted(set(old_cols) & set(new_cols)):
        o, n = old_cols[name], new_cols[name]
        if o["type_oid"] == n["type_oid"] and o["typmod"] == n["typmod"]:
            continue
        if _is_widen(o, n):
            out.append(
                Drift("type_widen", name, f"OID {o['type_oid']}→{n['type_oid']} widened, applied automatically", "additive")
            )
        else:
            out.append(
                Drift(
                    "type_incompatible",
                    name,
                    f"OID {o['type_oid']}→{n['type_oid']} is not a proven-safe widening",
                    "breaking",
                )
            )

    if list(old_sig.get("key", [])) != list(new_sig.get("key", [])):
        out.append(
            Drift(
                "key_change",
                None,
                f"merge key {old_sig.get('key')} → {new_sig.get('key')}; a live key change double-loads "
                "under dlt merge — the table is removed from the publication and needs `resnapshot`",
                "breaking",
            )
        )
    if old_sig.get("replica_identity") != new_sig.get("replica_identity"):
        out.append(
            Drift(
                "replica_identity_change",
                None,
                f"REPLICA IDENTITY {old_sig.get('replica_identity')} → {new_sig.get('replica_identity')}",
                "info",
            )
        )
    return out
