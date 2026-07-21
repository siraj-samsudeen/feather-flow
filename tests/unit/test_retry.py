"""Tests for retry + backoff state management (V14)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class TestIncrementRetry:
    def test_first_failure_sets_retry_count_1(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        sm.increment_retry("orders")
        wm = sm.read_watermark("orders")
        assert wm["retry_count"] == 1

    def test_first_failure_sets_retry_after_15_min(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        before = datetime.now(timezone.utc).replace(tzinfo=None)
        sm.increment_retry("orders")
        wm = sm.read_watermark("orders")

        retry_after = wm["retry_after"]
        assert retry_after is not None
        expected_min = before + timedelta(minutes=14)
        expected_max = before + timedelta(minutes=16)
        assert expected_min <= retry_after <= expected_max

    def test_two_failures_30_min_backoff(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        sm.increment_retry("orders")
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        sm.increment_retry("orders")
        wm = sm.read_watermark("orders")

        assert wm["retry_count"] == 2
        retry_after = wm["retry_after"]
        expected_min = before + timedelta(minutes=29)
        expected_max = before + timedelta(minutes=31)
        assert expected_min <= retry_after <= expected_max

    def test_ten_failures_capped_at_120_min(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        for _ in range(9):
            sm.increment_retry("orders")

        before = datetime.now(timezone.utc).replace(tzinfo=None)
        sm.increment_retry("orders")
        wm = sm.read_watermark("orders")

        assert wm["retry_count"] == 10
        retry_after = wm["retry_after"]
        expected_min = before + timedelta(minutes=119)
        expected_max = before + timedelta(minutes=121)
        assert expected_min <= retry_after <= expected_max

    def test_increment_creates_watermark_if_missing(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.increment_retry("new_table")
        wm = sm.read_watermark("new_table")
        assert wm is not None
        assert wm["retry_count"] == 1


class TestResetRetry:
    def test_reset_clears_retry_state(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        sm.increment_retry("orders")
        sm.increment_retry("orders")
        sm.reset_retry("orders")

        wm = sm.read_watermark("orders")
        assert wm["retry_count"] == 0
        assert wm["retry_after"] is None

    def test_reset_noop_on_clean_table(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        sm.reset_retry("orders")
        wm = sm.read_watermark("orders")
        assert wm["retry_count"] == 0


class TestShouldSkipRetry:
    def test_no_backoff_returns_false(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        skip, error = sm.should_skip_retry("orders")
        assert skip is False
        assert error is None

    def test_in_backoff_returns_true_with_error(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")

        now = datetime.now(timezone.utc)
        sm.record_run(
            run_id="orders_fail",
            table_name="orders",
            started_at=now,
            ended_at=now,
            status="failure",
            error_message="Connection refused",
        )
        sm.increment_retry("orders")

        skip, error = sm.should_skip_retry("orders")
        assert skip is True
        assert error == "Connection refused"

    def test_past_backoff_returns_false(self, tmp_path: Path):
        import duckdb

        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_watermark("orders", strategy="full")
        sm.increment_retry("orders")

        con = duckdb.connect(str(sm.path))
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        con.execute(
            "UPDATE _watermarks SET retry_after = ? WHERE table_name = 'orders'",
            [past],
        )
        con.close()

        skip, _ = sm.should_skip_retry("orders")
        assert skip is False

    def test_nonexistent_table_returns_false(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        skip, error = sm.should_skip_retry("nonexistent")
        assert skip is False
        assert error is None


class TestGetLastFailureMessage:
    def test_returns_error_from_last_failure(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        now = datetime.now(timezone.utc)
        sm.record_run(
            run_id="orders_1",
            table_name="orders",
            started_at=now,
            ended_at=now,
            status="failure",
            error_message="First error",
        )
        sm.record_run(
            run_id="orders_2",
            table_name="orders",
            started_at=now + timedelta(seconds=1),
            ended_at=now + timedelta(seconds=1),
            status="failure",
            error_message="Second error",
        )

        msg = sm.get_last_failure_message("orders")
        assert msg == "Second error"

    def test_returns_none_when_no_failures(self, tmp_path: Path):
        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        msg = sm.get_last_failure_message("orders")
        assert msg is None


class TestConnectionCleanupRetry:
    def test_increment_retry_closes_on_error(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        mock_con = MagicMock()
        mock_con.execute.side_effect = RuntimeError("db error")
        sm._connect = lambda: mock_con

        with pytest.raises(RuntimeError, match="db error"):
            sm.increment_retry("test")

        mock_con.close.assert_called_once()

    def test_should_skip_retry_closes_on_error(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from feather_flow.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        mock_con = MagicMock()
        mock_con.execute.side_effect = RuntimeError("db error")
        sm._connect = lambda: mock_con

        with pytest.raises(RuntimeError, match="db error"):
            sm.should_skip_retry("test")

        mock_con.close.assert_called_once()
