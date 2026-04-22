"""Smoke test for the empty Typer app entrypoint."""

from __future__ import annotations

import typer


def test_app_is_a_typer_instance() -> None:
    from feather_etl.cli import app

    assert isinstance(app, typer.Typer)
