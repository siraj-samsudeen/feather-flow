"""Discovery adapters: source-specific POLICY that narrows the generic set of relations.

An adapter may only answer "which of the source's relations are in scope, and what does the
source call them" — it never supplies types, keys, or load behaviour. That keeps the CDC
mechanism single-implementation (#3079 D4); a fork of the mechanism per source is the failure
this design exists to prevent.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, NamedTuple


class AdapterRelation(NamedTuple):
    """(schema, table, module) — plus a description the source itself owns, when it has one."""

    schema: str
    table: str
    module: str | None = None
    description: str | None = None


AdapterFn = Callable[[str, Dict[str, Any]], List[AdapterRelation]]


def get(name: str) -> AdapterFn:
    """Resolve an adapter by manifest name. Unknown names fail loudly at validate time."""
    if name == "featherbase":
        from .featherbase import select as featherbase_select

        return featherbase_select
    raise KeyError(name)


def known() -> tuple:
    return ("featherbase",)
