"""Destination connectors for feather-flow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Protocol

import pyarrow as pa

if TYPE_CHECKING:
    from feather_flow.extract_windows import WindowResult, WindowSpec
    from feather_flow.state import StateManager


class Destination(Protocol):
    """Target for loaded data."""

    def load_full(self, table: str, data: pa.Table, run_id: str) -> int: ...

    def load_incremental(
        self, table: str, data: pa.Table, run_id: str, timestamp_column: str
    ) -> int: ...

    def load_batched_append(
        self,
        table: str,
        window: "WindowSpec",
        batches: "Iterator[pa.RecordBatch]",
        run_id: str,
        state: "StateManager",
        transport_used: str,
    ) -> "WindowResult": ...

    def execute_sql(self, sql: str) -> None: ...

    def sync_to_remote(
        self, tables: list[str], remote_config: dict[str, object]
    ) -> None: ...
