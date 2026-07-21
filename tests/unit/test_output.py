"""Tests for --json output helper (Tasks 7-8)."""

from __future__ import annotations

import json


class TestOutputHelper:
    def test_emit_single_dict_json_mode(self, capsys):
        from feather_flow.output import emit_line

        emit_line({"table": "orders", "status": "success"}, json_mode=True)
        out = capsys.readouterr().out
        parsed = json.loads(out.strip())
        assert parsed["table"] == "orders"

    def test_emit_single_dict_noop_in_normal_mode(self, capsys):
        from feather_flow.output import emit_line

        emit_line({"table": "orders"}, json_mode=False)
        assert capsys.readouterr().out == ""

    def test_emit_list_outputs_ndjson(self, capsys):
        from feather_flow.output import emit

        data = [
            {"table": "orders", "status": "success"},
            {"table": "items", "status": "failure"},
        ]
        emit(data, json_mode=True)
        lines = capsys.readouterr().out.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["table"] == "orders"
        assert json.loads(lines[1])["table"] == "items"

    def test_emit_list_noop_in_normal_mode(self, capsys):
        """emit(..., json_mode=False) is a no-op — nothing goes to stdout."""
        from feather_flow.output import emit

        emit([{"table": "orders"}, {"table": "items"}], json_mode=False)
        assert capsys.readouterr().out == ""

    def test_emit_datetime_serialized(self, capsys):
        from datetime import datetime, timezone

        from feather_flow.output import emit_line

        dt = datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc)
        emit_line({"ts": dt}, json_mode=True)
        parsed = json.loads(capsys.readouterr().out.strip())
        assert "2026-03-28" in parsed["ts"]
