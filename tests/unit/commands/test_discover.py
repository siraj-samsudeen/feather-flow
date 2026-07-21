"""Unit tests for feather_flow.commands.discover private helpers."""

from __future__ import annotations

from unittest.mock import patch


class TestResolveRenameDecisionTtyConfirm:
    """Covers the interactive ``sys.stdin.isatty() → typer.confirm`` branch
    in ``_resolve_rename_decision``."""

    def test_tty_confirm_accepts_proposals(self, capsys):
        """When stdin is a TTY and the user confirms, all proposals are
        accepted and echoed back (commands/discover.py:48-50)."""
        from feather_flow.commands import discover as discover_cmd

        proposals = [("old_a", "new_a"), ("old_b", "new_b")]
        with (
            patch.object(discover_cmd.sys.stdin, "isatty", return_value=True),
            patch.object(discover_cmd.typer, "confirm", return_value=True),
        ):
            accepted, rejected = discover_cmd._resolve_rename_decision(
                proposals, yes=False, no_renames=False
            )

        assert accepted == proposals
        assert rejected == []

    def test_tty_confirm_rejects_and_lists_orphans(self, capsys):
        """When the user declines, every proposal becomes orphaned and
        ``treating … as new`` is printed per pair (commands/discover.py:51-52)."""
        from feather_flow.commands import discover as discover_cmd

        proposals = [("old_a", "new_a"), ("old_b", "new_b")]
        with (
            patch.object(discover_cmd.sys.stdin, "isatty", return_value=True),
            patch.object(discover_cmd.typer, "confirm", return_value=False),
        ):
            accepted, rejected = discover_cmd._resolve_rename_decision(
                proposals, yes=False, no_renames=False
            )

        assert accepted == []
        assert rejected == proposals

        out = capsys.readouterr().out
        assert "Kept old_a orphaned" in out
        assert "treating new_a as new" in out
        assert "Kept old_b orphaned" in out
