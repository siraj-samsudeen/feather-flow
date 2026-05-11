"""Unit tests for feather_etl.commands.extract private helpers."""

from __future__ import annotations

from dataclasses import dataclass

from typer.testing import CliRunner


@dataclass
class _FakeSource:
    name: str
    database: str | None = None
    databases: list[str] | None = None


@dataclass
class _FakeCfg:
    sources: list


class TestLookupSourceName:
    """``_lookup_source_name`` falls back to the raw ``source_db`` when
    ``resolve_source`` raises ValueError (commands/extract.py)."""

    def test_returns_match_when_resolvable(self):
        from feather_etl.commands.extract import _lookup_source_name

        cfg = _FakeCfg(sources=[_FakeSource(name="erp", database="erp")])
        assert _lookup_source_name(cfg, "erp") == "erp"

    def test_falls_back_to_raw_source_db_on_mismatch(self):
        """When source_db points at nothing configured, the helper returns
        the input string so the CLI has something to print."""
        from feather_etl.commands.extract import _lookup_source_name

        cfg = _FakeCfg(sources=[_FakeSource(name="erp", database="erp")])
        assert _lookup_source_name(cfg, "nonexistent_db") == "nonexistent_db"


class TestNoCacheAlias:
    """Positive proof of the hard rename: `feather cache` is gone, with no
    alias slipped in. Spec: openspec/changes/feather-transform/specs/
    extract-verb-rename/spec.md — Scenario "feather cache returns no such command".
    """

    def test_feather_cache_is_not_a_registered_command(self):
        """Invoking `feather cache` must exit non-zero with a 'No such command'
        signal from Typer/Click. This is the load-bearing assertion: it catches
        an accidentally-restored alias the moment one is introduced.
        """
        from feather_etl.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["cache", "--help"])

        assert result.exit_code != 0, (
            "feather cache must NOT be a registered command after the hard rename. "
            f"Got exit_code={result.exit_code}, output={result.output!r}"
        )
        # Click/Typer emits "No such command" for unknown sub-commands.
        combined = result.output or ""
        assert "No such command" in combined or "no such command" in combined.lower(), (
            "Expected a 'no such command' indication for `feather cache`. "
            f"Got output: {combined!r}"
        )

    def test_feather_extract_is_registered(self):
        """Companion assertion: `feather extract --help` succeeds, so the
        no-cache failure above is meaningful (the verb really did move)."""
        from feather_etl.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["extract", "--help"])
        assert result.exit_code == 0
        assert "extract" in (result.output or "").lower()

    def test_top_level_help_lists_extract_not_cache(self):
        """`feather --help` must list `extract` and NOT list `cache`."""
        from feather_etl.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        commands_text = result.output or ""
        assert "extract" in commands_text
        # Restrict the cache-absence check to Click's command-list section
        # (lines starting with two spaces but not four), so a help string
        # that legitimately mentions caching can't produce a false positive.
        lines = commands_text.splitlines()
        command_list_lines = [
            line for line in lines if line.startswith("  ") and not line.startswith("    ")
        ]
        joined = " ".join(command_list_lines)
        assert "cache" not in joined.split(), (
            f"Did not expect `cache` in the command list. Command lines:\n"
            + "\n".join(command_list_lines)
        )
