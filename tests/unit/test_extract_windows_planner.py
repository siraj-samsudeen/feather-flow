"""Unit tests for the (currently single-window) extract planner.

Issue #63 will replace `plan_windows` with a multi-strategy planner; the
WindowSpec / WindowResult contract this module defines is what the
destination consumes and is locked here so #63 can extend without
churning destinations or state."""

from __future__ import annotations

from feather_etl.config import TableConfig
from feather_etl.extract_windows import WindowSpec, plan_windows


def _make_table(name: str = "sales") -> TableConfig:
    # Minimal — strategy is required; using "full" as the simplest valid value.
    return TableConfig(
        name=name,
        source_table=name,
        strategy="full",
        database="src",
        source_name="src",
    )


def test_plan_windows_returns_one_window_with_full_table_predicate() -> None:
    plans = plan_windows(_make_table("sales"))
    assert plans == [
        WindowSpec(
            window_key="all",
            window_predicate="1=1",
            window_start=None,
            window_end=None,
        )
    ]


def test_window_spec_is_frozen_dataclass() -> None:
    spec = WindowSpec(
        window_key="2026-05-12",
        window_predicate="[Invoice Date] = '2026-05-12'",
        window_start=None,
        window_end=None,
    )
    # Frozen → hashable → can live in a set (used to skip committed windows).
    assert spec in {spec}
