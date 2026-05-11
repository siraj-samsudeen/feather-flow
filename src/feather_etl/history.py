"""`feather history` core — orchestration without Typer."""

from __future__ import annotations

from pathlib import Path

from feather_etl.exceptions import StateDBMissingError


def load_history(
    state_path: Path,
    *,
    table: str | None = None,
    limit: int = 20,
    trigger: str | None = None,
) -> list[dict]:
    """Return recent run history rows from the state DB.

    Raises ``StateDBMissingError`` if the state DB does not exist.

    When ``trigger`` is set, only rows whose ``_runs.trigger`` equals the
    given value are returned (legacy NULL-trigger rows are excluded).
    """
    if not state_path.exists():
        raise StateDBMissingError(str(state_path))

    from feather_etl.state import StateManager

    sm = StateManager(state_path)
    return sm.get_history(table_name=table, limit=limit, trigger=trigger)
