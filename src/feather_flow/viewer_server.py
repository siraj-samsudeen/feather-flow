"""Sync the packaged schema viewer into a local project directory."""

from __future__ import annotations

import socket
import webbrowser
from dataclasses import dataclass
from functools import lru_cache
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from importlib.resources import files
from pathlib import Path
from typing import Literal

VIEWER_FILENAME = "schema_viewer.html"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

ViewerSyncStatus = Literal["created", "updated", "unchanged"]


@dataclass(frozen=True, slots=True)
class ViewerSyncResult:
    path: Path
    status: ViewerSyncStatus


@lru_cache(maxsize=1)
def _packaged_viewer_bytes() -> bytes:
    return files("feather_flow.resources").joinpath(VIEWER_FILENAME).read_bytes()


def sync_viewer_html(target_dir: Path) -> ViewerSyncResult:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / VIEWER_FILENAME
    packaged = _packaged_viewer_bytes()

    if not target_path.exists():
        target_path.write_bytes(packaged)
        return ViewerSyncResult(path=target_path, status="created")

    current = target_path.read_bytes()
    if current == packaged:
        return ViewerSyncResult(path=target_path, status="unchanged")

    target_path.write_bytes(packaged)
    return ViewerSyncResult(path=target_path, status="updated")


def sync_status_message(status: ViewerSyncStatus) -> str | None:
    if status == "unchanged":
        return None
    if status == "created":
        return "Schema viewer created."
    if status == "updated":
        return "Schema viewer updated."
    return None


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def choose_port(preferred_port: int = DEFAULT_PORT) -> int:
    if _can_bind(DEFAULT_HOST, preferred_port):
        return preferred_port
    return _free_port(DEFAULT_HOST)


def _viewer_url(port: int) -> str:
    return f"http://{DEFAULT_HOST}:{port}/{VIEWER_FILENAME}"


def serve_and_open(target_dir: Path, preferred_port: int = DEFAULT_PORT) -> None:
    result = sync_viewer_html(target_dir)
    message = sync_status_message(result.status)
    if message:
        print(message)

    handler = partial(SimpleHTTPRequestHandler, directory=str(target_dir))
    selected_port = choose_port(preferred_port)
    server = None
    try:
        try:
            server = HTTPServer((DEFAULT_HOST, selected_port), handler)
        except OSError:
            server = HTTPServer((DEFAULT_HOST, 0), handler)

        url = _viewer_url(server.server_address[1])
        print(f"Serving schema viewer at {url}")
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            print("Browser launch failed. Open the URL above manually.")
        print("Press Ctrl+C to stop.")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        if server is not None:
            server.server_close()
