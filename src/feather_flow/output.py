"""JSON output helpers for feather-flow --json mode."""

from __future__ import annotations

import json
import sys


def emit_line(data: dict, json_mode: bool) -> None:
    """Emit a single dict as a JSON line. No-op if json_mode is False."""
    if not json_mode:
        return
    print(json.dumps(data, default=str), file=sys.stdout)


def emit(data: list[dict], json_mode: bool) -> None:
    """Emit a list of dicts as NDJSON (one JSON line per dict)."""
    if not json_mode:
        return
    for item in data:
        print(json.dumps(item, default=str), file=sys.stdout)
