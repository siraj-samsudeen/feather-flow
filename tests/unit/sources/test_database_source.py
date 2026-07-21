"""Tests for source dataclasses (StreamSchema, ChangeResult)."""

from __future__ import annotations


class TestSourceDataclasses:
    def test_stream_schema_fields(self):
        from feather_flow.sources import StreamSchema

        s = StreamSchema(
            name="test",
            columns=[("id", "BIGINT"), ("name", "VARCHAR")],
            primary_key=["id"],
            supports_incremental=True,
        )
        assert s.name == "test"
        assert len(s.columns) == 2

    def test_change_result_fields(self):
        from feather_flow.sources import ChangeResult

        r = ChangeResult(changed=True, reason="first_run", metadata={})
        assert r.changed is True
        assert r.reason == "first_run"
