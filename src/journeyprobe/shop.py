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


@contextmanager
def serve_shop(directory: Path | None = None) -> Iterator[str]:
    """Serve the shop on a free localhost port; yields the base URL."""
    handler = functools.partial(_Quiet, directory=str(directory or shop_dir()))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
