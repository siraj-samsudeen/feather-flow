"""Workflow stage 16: feather view.

Scenarios exercise `feather view` — inspecting the destination DB through
a local HTTP viewer. Heavy monkeypatching of serve_and_open /
sync_viewer_html prevents real server / browser launches.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _forbid_sync(*args, **kwargs):
    raise AssertionError("view should not call sync_viewer_html directly")


def test_uses_current_directory_by_default(
    cli, project, monkeypatch: pytest.MonkeyPatch
):
    from feather_flow import viewer_server

    seen: dict[str, object] = {}

    def fake_serve_and_open(
        target_dir: Path, preferred_port: int = viewer_server.DEFAULT_PORT
    ):
        seen["serve_target_dir"] = target_dir
        seen["preferred_port"] = preferred_port

    monkeypatch.chdir(project.root)
    monkeypatch.setattr(viewer_server, "sync_viewer_html", _forbid_sync)
    monkeypatch.setattr(viewer_server, "serve_and_open", fake_serve_and_open)

    result = cli("view", config=False)

    assert result.exit_code == 0, result.output
    assert seen["serve_target_dir"] == project.root
    assert seen["preferred_port"] == viewer_server.DEFAULT_PORT


def test_uses_path_and_port_options(cli, project, monkeypatch: pytest.MonkeyPatch):
    from feather_flow import viewer_server

    target_dir = project.root / "viewer"
    target_dir.mkdir()
    seen: dict[str, object] = {}

    def fake_serve_and_open(
        path: Path, preferred_port: int = viewer_server.DEFAULT_PORT
    ):
        seen["serve_target_dir"] = path
        seen["preferred_port"] = preferred_port

    monkeypatch.setattr(viewer_server, "sync_viewer_html", _forbid_sync)
    monkeypatch.setattr(viewer_server, "serve_and_open", fake_serve_and_open)

    result = cli("view", str(target_dir), "--port", "8123", config=False)

    assert result.exit_code == 0, result.output
    assert seen["serve_target_dir"] == target_dir.resolve()
    assert seen["preferred_port"] == 8123


def test_invalid_path_fails(cli, project):
    result = cli("view", str(project.root / "missing"), config=False)

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_help_mentions_existing_directory(cli):
    result = cli("view", "--help", config=False)

    assert result.exit_code == 0, result.output
    assert "Existing directory to serve the schema viewer from." in result.output
