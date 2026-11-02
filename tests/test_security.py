"""Regression tests for the hardening around site boundaries and the heal command."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from journeyprobe.healer import _LOCATOR_PATH, _safe_dom_path, _verify_unique
from journeyprobe.llm import LLMError
from journeyprobe.models import Plan, SiteProfile, Target, same_origin
from journeyprobe.runner import run_plan


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8431/cart",
    "http://127.0.0.1:8431",
])
def test_same_origin_accepts_same_scheme_host_port(url: str) -> None:
    assert same_origin(url, "http://127.0.0.1:8431/")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8431/",        # scheme differs
    "http://127.0.0.1:9999/",         # port differs
    "http://evil.example/",           # host differs
    "http://127.0.0.1.evil.example/", # look-alike host
    "http://127.0.0.1:8431@evil.example/",  # credentials trick
    "//evil.example/",                # scheme-relative
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,hi",
    "http://127.0.0.1:8431\\@evil.example/",  # backslash: browsers treat it as a slash
    "http://127.0.0.1:8431 @evil.example/",   # whitespace
    "http://127.0.0.1:8431/\n@evil.example/",  # control character
    "http://user:pw@127.0.0.1:8431/",         # embedded credentials
])
def test_same_origin_rejects_everything_else(url: str) -> None:
    assert not same_origin(url, "http://127.0.0.1:8431/")


def test_default_ports_are_equal() -> None:
    assert same_origin("https://shop.test/x", "https://shop.test:443/")
    assert not same_origin("http://shop.test/x", "https://shop.test/")


@pytest.mark.parametrize("name", ["../secret.dom.html", "/etc/passwd", "a/b.dom.html", "x.txt", "", ".."])
def test_dom_snapshot_name_must_be_a_bare_dom_html_file(tmp_path: Path, name: str) -> None:
    with pytest.raises(LLMError):
        _safe_dom_path(tmp_path, name)


def test_dom_snapshot_symlink_escape_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    results = tmp_path / "results"
    results.mkdir()
    (results / "x.dom.html").symlink_to(outside)
    with pytest.raises(LLMError):
        _safe_dom_path(results, "x.dom.html")


def test_dom_snapshot_good_name_resolves(tmp_path: Path) -> None:
    (tmp_path / "ok.dom.html").write_text("<p>hi</p>")
    assert _safe_dom_path(tmp_path, "ok.dom.html").name == "ok.dom.html"


@pytest.mark.parametrize("path,ok", [
    ("selectors.search_input", True),
    ("journeys[0].steps[2].target", True),
    ("journeys[0].steps[2].target.name", False),
    ("../../etc", False),
    ("selectors.", False),
    ("journeys[x].steps[1].target", False),
])
def test_locator_path_shapes(path: str, ok: bool) -> None:
    assert bool(_LOCATOR_PATH.fullmatch(path)) is ok


def test_heal_verifies_in_an_inert_page_without_running_scripts() -> None:
    # If the script ran, it would remove the only button and the count would be 0.
    dom = '<button id="a">Buy</button><script>document.getElementById("a").remove()</script>'
    assert _verify_unique(dom, Target(css="#a")) == 1


def _server(handler_cls: type) -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.mark.integration
def test_redirect_and_link_to_another_origin_are_blocked_at_the_network_layer(tmp_path: Path) -> None:
    hits: list[str] = []

    class Other(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"other")

        def log_message(self, *a: object) -> None: ...

    other, other_url = _server(Other)

    class Shop(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", other_url + "/via-redirect")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f'<a data-testid="out" href="{other_url}/via-link">out</a>'.encode())

        def log_message(self, *a: object) -> None: ...

    shop, shop_url = _server(Shop)
    site = SiteProfile.model_validate({"name": "t", "base_url": shop_url, "selectors": {}})
    try:
        redirect = Plan.model_validate({
            "persona": "p", "site": "t", "generated_by": {"provider": "t", "model": "t"},
            "journeys": [{"name": "r", "goal": "g", "steps": [{"action": "goto", "path": "/redirect"}]}]})
        res = run_plan(redirect, site, tmp_path / "a", timeout_ms=1500)
        assert not res.passed
        link = Plan.model_validate({
            "persona": "p", "site": "t", "generated_by": {"provider": "t", "model": "t"},
            "journeys": [{"name": "l", "goal": "g", "steps": [
                {"action": "goto", "path": "/"},
                {"action": "click", "target": {"testid": "out"}},
                {"action": "assert_text", "text": "other"}]}]})
        res = run_plan(link, site, tmp_path / "b", timeout_ms=1500)
        assert not res.passed
    finally:
        shop.shutdown()
        other.shutdown()
    assert hits == [], "the other origin must never receive a request"


@pytest.mark.integration
@pytest.mark.parametrize("hops", [
    ["{other}/x"],                                   # plain absolute
    ["//127.0.0.1:{port}/x"],                        # scheme-relative
    ["/\\\\127.0.0.1:{port}/x"],                      # backslash: browsers read it as a slash
    ["\\\\127.0.0.1:{port}/x"],
    ["/hop1", "/hop2", "{other}/x"],                 # a chain: every hop must be vetted, not just the first
])
def test_tricky_and_multi_hop_redirects_never_reach_another_origin(tmp_path: Path, hops: list[str]) -> None:
    hits: list[str] = []

    class Other(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a: object) -> None: ...

    other, other_url = _server(Other)
    port = other.server_address[1]
    chain = [h.format(other=other_url, port=port) for h in hops]

    class Shop(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            order = ["/start", *[c for c in chain if c.startswith("/hop")]]
            idx = order.index(self.path) if self.path in order else -1
            if idx >= 0:
                nxt = chain[idx] if idx < len(chain) else chain[-1]
                self.send_response(302)
                self.send_header("Location", nxt)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a: object) -> None: ...

    shop, shop_url = _server(Shop)
    site = SiteProfile.model_validate({"name": "t", "base_url": shop_url, "selectors": {}})
    plan = Plan.model_validate({
        "persona": "p", "site": "t", "generated_by": {"provider": "t", "model": "t"},
        "journeys": [{"name": "j", "goal": "g", "steps": [{"action": "goto", "path": "/start"}]}]})
    try:
        res = run_plan(plan, site, tmp_path, timeout_ms=1500)
    finally:
        shop.shutdown()
        other.shutdown()
    assert not res.passed
    assert hits == [], f"the other origin received {hits}"


@pytest.mark.integration
def test_run_fails_if_the_browser_ends_up_off_origin_even_when_a_server_changes_its_answer(tmp_path: Path) -> None:
    """A same-origin server that answers the guard's check differently from the browser's own request.

    The guard cannot stop the browser's second request (documented limitation), but the run must fail.
    """
    class Other(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a: object) -> None: ...

    other, other_url = _server(Other)
    landing = {"n": 0}

    class Shop(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/flip":  # same-origin redirect: the guard follows it itself
                self.send_response(302)
                self.send_header("Location", "/landing")
            elif self.path == "/landing":
                landing["n"] += 1
                if landing["n"] > 1:  # the guard's check saw 200; the browser's own request gets this
                    self.send_response(302)
                    self.send_header("Location", other_url + "/x")
                else:
                    self.send_response(200)
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, *a: object) -> None: ...

    shop, shop_url = _server(Shop)
    site = SiteProfile.model_validate({"name": "t", "base_url": shop_url, "selectors": {}})
    plan = Plan.model_validate({
        "persona": "p", "site": "t", "generated_by": {"provider": "t", "model": "t"},
        "journeys": [{"name": "j", "goal": "g", "steps": [{"action": "goto", "path": "/flip"}]}]})
    try:
        res = run_plan(plan, site, tmp_path, timeout_ms=1500)
    finally:
        shop.shutdown()
        other.shutdown()
    assert not res.passed


def test_shop_server_is_not_blocked_by_an_idle_connection() -> None:
    """Regression: demo-shop was single-threaded, so Chromium's idle speculative connection stalled it."""
    import socket
    import urllib.request

    from journeyprobe.shop import make_server

    httpd = make_server("127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    idle = socket.create_connection(("127.0.0.1", port))  # connects, sends nothing, like a preconnect
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:  # noqa: S310
            assert r.status == 200
    finally:
        idle.close()
        httpd.shutdown()
        httpd.server_close()


def _private_lan_ip() -> str | None:
    """This machine's private, non-loopback IPv4 address (no traffic is sent), or None."""
    import ipaddress
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("10.255.255.255", 1))
            ip = str(sock.getsockname()[0])
        except OSError:
            return None
    return ip if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback else None


@pytest.mark.integration
def test_example_plan_runs_against_a_private_non_loopback_address(tmp_path: Path) -> None:
    """Regression: in Docker (host 'shop' on a private IP) every page script was blocked by Private Network Access.

    The tests used 127.0.0.1, which Chromium treats as secure, so this only showed up in the container.
    """
    from journeyprobe.models import load_plan, load_site
    from journeyprobe.shop import make_server

    ip = _private_lan_ip()
    if ip is None:
        pytest.skip("no private LAN address on this machine")
    httpd = make_server("0.0.0.0", 0)  # noqa: S104 - reachable on the LAN address on purpose
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    examples = Path(__file__).resolve().parents[1] / "examples"
    try:
        res = run_plan(load_plan(examples / "plans/maya-fixture-shop.plan.yaml"),
                       load_site(examples / "sites/fixture-shop.yaml"), tmp_path,
                       base_url=f"http://{ip}:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert res.passed, res.model_dump_json(indent=1)


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8431", True),
    ("http://localhost:8431", True),
    ("http://10.1.2.3/", True),
    ("http://192.168.1.5/", True),
    ("http://172.18.0.2:8431", True),
    ("http://169.254.1.1/", True),
    ("http://8.8.8.8/", False),                 # public
    ("https://93.184.216.34/", False),          # public
    ("http://no-such-host.invalid/", False),    # does not resolve: fail safe, keep the checks on
    ("not a url", False),
    ("", False),
])
def test_private_network_checks_are_only_disabled_for_private_targets(url: str, expected: bool) -> None:
    from journeyprobe.runner import _is_private_target

    assert _is_private_target(url) is expected
