"""Helpers for the bundled static fixture shop."""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path


def shop_dir() -> Path:
    return Path(str(resources.files("journeyprobe") / "fixture_shop"))


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def make_server(
    host: str, port: int, directory: Path | None = None, *, quiet: bool = False
) -> http.server.ThreadingHTTPServer:
    """A threaded server for the shop.

    It must be threaded: Chromium opens speculative idle connections, and a single-threaded server blocks
    on one of them until it times out, so the next real request stalls for ~30 seconds.
    """
    handler = functools.partial(_Quiet if quiet else http.server.SimpleHTTPRequestHandler,
                                directory=str(directory or shop_dir()))
    httpd = http.server.ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


@contextmanager
def serve_shop(directory: Path | None = None) -> Iterator[str]:
    """Serve the shop on a free localhost port; yields the base URL."""
    httpd = make_server("127.0.0.1", 0, directory, quiet=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
