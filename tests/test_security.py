"""Regression tests for the hardening around site boundaries and the heal command."""

from __future__ import annotations

from pathlib import Path

import pytest

from journeyprobe.healer import _LOCATOR_PATH, _safe_dom_path, _verify_unique
from journeyprobe.llm import LLMError
from journeyprobe.models import Target, same_origin


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
