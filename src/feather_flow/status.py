"""`feather status` core — orchestration without Typer."""

from __future__ import annotations

from pathlib import Path

from feather_flow.exceptions import StateDBMissingError


def load_status(state_path: Path) -> list[dict[str, object]]:
    """Return per-table last-run status rows from the state DB.

    Raises ``StateDBMissingError`` if the state DB does not exist.
    """
    if not state_path.exists():
        raise StateDBMissingError(str(state_path))

    from feather_flow.state import StateManager

    sm = StateManager(state_path)
    return sm.get_status()
