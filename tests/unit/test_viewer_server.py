"""Tests for feather_flow.viewer_server sync and launch behavior."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def _source_viewer_bytes() -> bytes:
    return Path("scripts/schema_viewer.html").read_bytes()


class TestSyncViewerHtml:
    def test_creates_viewer_when_missing(self, tmp_path: Path):
        from feather_flow.viewer_server import VIEWER_FILENAME, sync_viewer_html

        target = tmp_path
        viewer_path = target / VIEWER_FILENAME

        result = sync_viewer_html(target)

        assert result.path == viewer_path
        assert result.status == "created"
        assert viewer_path.read_bytes() == _source_viewer_bytes()

    def test_does_not_rewrite_unchanged_file(self, tmp_path: Path):
        from feather_flow.viewer_server import VIEWER_FILENAME, sync_viewer_html

        viewer_path = tmp_path / VIEWER_FILENAME
        viewer_path.write_bytes(_source_viewer_bytes())
        os.utime(viewer_path, (1_700_000_000, 1_700_000_000))
        before = viewer_path.stat().st_mtime_ns

        result = sync_viewer_html(tmp_path)

        after = viewer_path.stat().st_mtime_ns
        assert result.path == viewer_path
        assert result.status == "unchanged"
        assert after == before
        assert viewer_path.read_bytes() == _source_viewer_bytes()

    def test_updates_different_file(self, tmp_path: Path):
        from feather_flow.viewer_server import VIEWER_FILENAME, sync_viewer_html

        viewer_path = tmp_path / VIEWER_FILENAME
        viewer_path.write_text("stale viewer", encoding="utf-8")
        os.utime(viewer_path, (1_700_000_000, 1_700_000_000))
        before = viewer_path.stat().st_mtime_ns

        result = sync_viewer_html(tmp_path)

        after = viewer_path.stat().st_mtime_ns
        assert result.path == viewer_path
        assert result.status == "updated"
        assert after != before
        assert viewer_path.read_bytes() == _source_viewer_bytes()


class TestSyncStatusMessage:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("created", "Schema viewer created."),
            ("updated", "Schema viewer updated."),
            ("unchanged", None),
        ],
    )
    def test_messages(self, status: str, expected: str | None):
        from feather_flow.viewer_server import sync_status_message

        message = sync_status_message(status)

        if expected is None:
            assert message is None
        else:
            assert message == expected


class TestCanBind:
    def test_free_port_is_bindable(self):
        """Binding to an ephemeral port succeeds."""
        from feather_flow import viewer_server

        # Port 0 → kernel assigns a free port for the probe.
        free_port = viewer_server._free_port(viewer_server.DEFAULT_HOST)
        assert viewer_server._can_bind(viewer_server.DEFAULT_HOST, free_port) is True

    def test_busy_port_is_not_bindable(self):
        """When another process holds the port, _can_bind returns False."""
        import socket

        from feather_flow import viewer_server

        # Hold a port open for the duration of the probe.
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            holder.bind((viewer_server.DEFAULT_HOST, 0))
            holder.listen(1)
            busy_port = holder.getsockname()[1]
            assert (
                viewer_server._can_bind(viewer_server.DEFAULT_HOST, busy_port) is False
            )
        finally:
            holder.close()


class TestFreePort:
    def test_returns_nonzero_port(self):
        from feather_flow import viewer_server

        port = viewer_server._free_port(viewer_server.DEFAULT_HOST)
        assert isinstance(port, int)
        assert port > 0


class TestSyncStatusMessageFallback:
    def test_unknown_status_returns_none(self):
        """Defensive branch: an unexpected status string falls through all
        the explicit ``if`` branches and returns None."""
        from feather_flow.viewer_server import sync_status_message

        # Intentionally outside the Literal type — exercises the final return
        assert sync_status_message("weird") is None  # type: ignore[arg-type]


class TestChoosePort:
    def test_returns_preferred_when_available(self, monkeypatch: pytest.MonkeyPatch):
        from feather_flow import viewer_server

        monkeypatch.setattr(viewer_server, "_can_bind", lambda host, port: port == 8123)
        monkeypatch.setattr(viewer_server, "_free_port", lambda host: 9999)

        port = viewer_server.choose_port(8123)

        assert port == 8123

    def test_falls_back_when_preferred_is_busy(self, monkeypatch: pytest.MonkeyPatch):
        from feather_flow import viewer_server

        monkeypatch.setattr(viewer_server, "_can_bind", lambda host, port: False)
        monkeypatch.setattr(viewer_server, "_free_port", lambda host: 8765)

        port = viewer_server.choose_port(8123)

        assert port == 8765


class TestServeAndOpen:
    def test_prints_url_once_and_manual_hint_on_browser_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        from feather_flow import viewer_server

        server = SimpleNamespace(
            server_address=(viewer_server.DEFAULT_HOST, 8123),
            serve_forever=Mock(side_effect=KeyboardInterrupt),
            server_close=Mock(),
        )

        monkeypatch.setattr(
            viewer_server,
            "sync_viewer_html",
            lambda target_dir: viewer_server.ViewerSyncResult(
                path=target_dir / viewer_server.VIEWER_FILENAME,
                status="unchanged",
            ),
        )
        monkeypatch.setattr(
            viewer_server,
            "choose_port",
            lambda preferred_port=viewer_server.DEFAULT_PORT: 8123,
        )
        monkeypatch.setattr(viewer_server.webbrowser, "open", lambda url: False)
        monkeypatch.setattr(
            viewer_server, "HTTPServer", lambda address, handler: server
        )

        viewer_server.serve_and_open(tmp_path)

        output = capsys.readouterr().out.strip().splitlines()

        assert output == [
            "Serving schema viewer at http://127.0.0.1:8123/schema_viewer.html",
            "Browser launch failed. Open the URL above manually.",
            "Press Ctrl+C to stop.",
        ]
        assert server.serve_forever.call_count == 1
        assert server.server_close.call_count == 1

    def test_cleans_up_when_browser_open_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        from feather_flow import viewer_server

        server = SimpleNamespace(
            server_address=(viewer_server.DEFAULT_HOST, 8123),
            serve_forever=Mock(side_effect=KeyboardInterrupt),
            server_close=Mock(),
        )

        monkeypatch.setattr(
            viewer_server,
            "sync_viewer_html",
            lambda target_dir: viewer_server.ViewerSyncResult(
                path=target_dir / viewer_server.VIEWER_FILENAME,
                status="unchanged",
            ),
        )
        monkeypatch.setattr(
            viewer_server,
            "choose_port",
            lambda preferred_port=viewer_server.DEFAULT_PORT: 8123,
        )
        monkeypatch.setattr(
            viewer_server.webbrowser, "open", Mock(side_effect=RuntimeError("boom"))
        )
        monkeypatch.setattr(
            viewer_server, "HTTPServer", lambda address, handler: server
        )

        viewer_server.serve_and_open(tmp_path)

        output = capsys.readouterr().out.strip().splitlines()

        assert output == [
            "Serving schema viewer at http://127.0.0.1:8123/schema_viewer.html",
            "Browser launch failed. Open the URL above manually.",
            "Press Ctrl+C to stop.",
        ]
        assert server.serve_forever.call_count == 1
        assert server.server_close.call_count == 1

    def test_prints_sync_status_message_when_viewer_is_created(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        """If sync_viewer_html returns a non-'unchanged' status,
        serve_and_open prints the friendly message before serving."""
        from feather_flow import viewer_server

        server = SimpleNamespace(
            server_address=(viewer_server.DEFAULT_HOST, 8123),
            serve_forever=Mock(side_effect=KeyboardInterrupt),
            server_close=Mock(),
        )

        monkeypatch.setattr(
            viewer_server,
            "sync_viewer_html",
            lambda target_dir: viewer_server.ViewerSyncResult(
                path=target_dir / viewer_server.VIEWER_FILENAME,
                status="created",
            ),
        )
        monkeypatch.setattr(
            viewer_server,
            "choose_port",
            lambda preferred_port=viewer_server.DEFAULT_PORT: 8123,
        )
        monkeypatch.setattr(viewer_server.webbrowser, "open", lambda url: True)
        monkeypatch.setattr(
            viewer_server, "HTTPServer", lambda address, handler: server
        )

        viewer_server.serve_and_open(tmp_path)

        output = capsys.readouterr().out.strip().splitlines()
        assert output[0] == "Schema viewer created."
        assert any(line.startswith("Serving schema viewer at") for line in output)

    def test_falls_back_to_ephemeral_port_when_bind_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        from feather_flow import viewer_server

        fallback_server = SimpleNamespace(
            server_address=(viewer_server.DEFAULT_HOST, 9234),
            serve_forever=Mock(side_effect=KeyboardInterrupt),
            server_close=Mock(),
        )

        monkeypatch.setattr(
            viewer_server,
            "sync_viewer_html",
            lambda target_dir: viewer_server.ViewerSyncResult(
                path=target_dir / viewer_server.VIEWER_FILENAME,
                status="unchanged",
            ),
        )
        monkeypatch.setattr(
            viewer_server,
            "choose_port",
            lambda preferred_port=viewer_server.DEFAULT_PORT: 8123,
        )
        monkeypatch.setattr(viewer_server.webbrowser, "open", lambda url: True)

        def fake_http_server(address, handler):
            if address[1] == 8123:
                raise OSError("port busy")
            assert address[1] == 0
            return fallback_server

        monkeypatch.setattr(viewer_server, "HTTPServer", fake_http_server)

        viewer_server.serve_and_open(tmp_path)

        output = capsys.readouterr().out.strip().splitlines()

        assert output == [
            "Serving schema viewer at http://127.0.0.1:9234/schema_viewer.html",
            "Press Ctrl+C to stop.",
        ]
        assert sum(line.startswith("Serving schema viewer at ") for line in output) == 1
        assert fallback_server.serve_forever.call_count == 1
        assert fallback_server.server_close.call_count == 1
