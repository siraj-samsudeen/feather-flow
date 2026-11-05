"""Per-relation lifecycle state machine (#3079 D5).

States are the ONLY record of why a table is not streaming, so every move is validated and
mirrored to `control.event` (unit='lifecycle'). Slot loss is never a silent empty stream: it
lands as `resnapshot_required` before any re-snapshot begins.
`drift_review` has NO edge back to `streaming`: a run that merely sees no further drift must
not clear a reported break, so the only way out is `resnapshot` (which passes through
resnapshot_required → snapshotting → streaming) or retirement. The state machine enforces
that rather than a convention (#3079 D7).
"""
from __future__ import annotations

from control_plane import status as cp_status

DISCOVERED = "discovered"
SNAPSHOTTING = "snapshotting"
CATCHING_UP = "catching_up"
STREAMING = "streaming"
DRIFT_REVIEW = "drift_review"
RESNAPSHOT_REQUIRED = "resnapshot_required"
RETIRED = "retired"

STATES = frozenset({DISCOVERED, SNAPSHOTTING, CATCHING_UP, STREAMING,
                    DRIFT_REVIEW, RESNAPSHOT_REQUIRED, RETIRED})

ALLOWED: dict[str | None, frozenset[str]] = {
    # STREAMING from nothing is ADOPTION only (runner._adopt_streaming): a slot that already
    # exists is proof a previous drainer baselined the table; inventing a snapshot it never ran
    # would be the bigger lie.
    None: frozenset({DISCOVERED, SNAPSHOTTING, STREAMING}),
    DISCOVERED: frozenset({SNAPSHOTTING, CATCHING_UP, RETIRED}),
    SNAPSHOTTING: frozenset({CATCHING_UP, STREAMING, RESNAPSHOT_REQUIRED, RETIRED}),
    CATCHING_UP: frozenset({STREAMING, DRIFT_REVIEW, RESNAPSHOT_REQUIRED, RETIRED}),
    STREAMING: frozenset({CATCHING_UP, DRIFT_REVIEW, RESNAPSHOT_REQUIRED, RETIRED}),
    DRIFT_REVIEW: frozenset({RESNAPSHOT_REQUIRED, RETIRED}),
    RESNAPSHOT_REQUIRED: frozenset({SNAPSHOTTING, RETIRED}),
    RETIRED: frozenset({DISCOVERED}),   # re-widening the selection re-admits the table
}


class IllegalTransition(ValueError):
    pass


class Lifecycle:
    def __init__(self, destination, registration: str, run=None) -> None:
        self.destination = destination
        self.registration = registration
        self.run = run
        self._states: dict[tuple[str, str], str] = destination.read_lifecycle(registration)

    def state_of(self, schema: str, table: str) -> str | None:
        return self._states.get((schema, table))

    def known(self) -> dict[tuple[str, str], str]:
        """Every relation this registration has a row for, with its state (a copy)."""
        return dict(self._states)

    def transition(self, schema: str, table: str, new_state: str,
                   detail: str | None = None) -> str:
        if new_state not in STATES:
            raise IllegalTransition(f"unknown lifecycle state {new_state!r}")
        current = self._states.get((schema, table))
        if new_state not in ALLOWED[current]:
            raise IllegalTransition(
                f"{schema}.{table}: {current or 'new'} → {new_state} is not an allowed transition"
            )
        self.destination.write_lifecycle(self.registration, schema, table, new_state, detail)
        self._states[(schema, table)] = new_state
        if self.run is not None:
            self.run.event(entity=f"{schema}.{table}", status=cp_status.EV_OK, unit="lifecycle",
                           extra={"from": current, "to": new_state, "detail": detail})
        return new_state

    def ensure(self, schema: str, table: str, new_state: str, detail: str | None = None) -> str:
        """Idempotent: already in `new_state` is a no-op, not an error."""
        if self._states.get((schema, table)) == new_state:
            return new_state
        return self.transition(schema, table, new_state, detail)
