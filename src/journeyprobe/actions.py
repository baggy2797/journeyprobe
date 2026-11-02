"""The fixed action vocabulary and its deterministic Playwright implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Locator, Page
from playwright.sync_api import expect as pw_expect

from .models import (
    STEP_MODELS,
    AddToCart,
    AssertText,
    AssertUrl,
    AssertVisible,
    Click,
    Fill,
    Goto,
    Search,
    SiteProfile,
    Target,
    same_origin,
)


class StepError(Exception):
    pass


def locate(page: Page, t: Target) -> Locator:
    if t.testid:
        loc = page.get_by_test_id(t.testid)
    elif t.role:
        loc = page.get_by_role(t.role, name=t.name) if t.name else page.get_by_role(t.role)  # type: ignore[arg-type]
    elif t.text:
        loc = page.get_by_text(t.text)
    elif t.label:
        loc = page.get_by_label(t.label)
    else:
        assert t.css
        loc = page.locator(t.css)
    return loc.nth(t.nth) if t.nth is not None else loc


@dataclass
class Ctx:
    page: Page
    site: SiteProfile
    base_url: str
    timeout_ms: int = 5000
    #: Where the locator used by the current step lives (plan or profile); read by the runner/healer.
    locator_path: str | None = None
    locator: Target | None = None

    def resolve(self, target: Target, path: str) -> Locator:
        self.locator_path = path
        self.locator = target
        return locate(self.page, target)


Handler = Callable[[Any, Ctx], None]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    handler: Handler


REGISTRY: dict[str, ActionSpec] = {}


def action(model: type, description: str) -> Callable[[Handler], Handler]:
    name = model.model_fields["action"].default  # type: ignore[attr-defined]

    def deco(fn: Handler) -> Handler:
        REGISTRY[name] = ActionSpec(name, description, fn)
        return fn

    return deco


def _selector(ctx: Ctx, key: str) -> Target:
    t = ctx.site.selectors.get(key)
    if t is None:
        raise StepError(f"site profile has no selectors.{key}")
    return t


@action(Goto, "goto(path): navigate to a path on the site")
def _goto(s: Goto, ctx: Ctx) -> None:
    parsed = urlparse(s.path)
    url = s.path if (parsed.scheme or parsed.netloc) \
        else urljoin(ctx.base_url.rstrip("/") + "/", s.path.lstrip("/"))
    if not same_origin(url, ctx.base_url):
        raise StepError(f"refusing to leave the site origin: {url}")
    ctx.page.goto(url, timeout=ctx.timeout_ms)
    # Redirects are followed by the browser, so check where we actually ended up.
    if not same_origin(ctx.page.url, ctx.base_url):
        ctx.page.goto("about:blank")
        raise StepError("refusing to stay on a page outside the site origin (redirect)")


@action(Search, "search(query): type into the site's search box and submit")
def _search(s: Search, ctx: Ctx) -> None:
    box = ctx.resolve(_selector(ctx, "search_input"), "selectors.search_input")
    box.fill(s.query, timeout=ctx.timeout_ms)
    if "search_submit" in ctx.site.selectors:
        ctx.resolve(_selector(ctx, "search_submit"), "selectors.search_submit").click(
            timeout=ctx.timeout_ms
        )
    else:
        box.press("Enter", timeout=ctx.timeout_ms)


@action(Click, "click(target): click one element")
def _click(s: Click, ctx: Ctx) -> None:
    ctx.resolve(s.target, "target").click(timeout=ctx.timeout_ms)


@action(Fill, "fill(target, value): type a value into an input")
def _fill(s: Fill, ctx: Ctx) -> None:
    ctx.resolve(s.target, "target").fill(s.value, timeout=ctx.timeout_ms)


@action(AddToCart, "add_to_cart(target?): click the add-to-cart control (profile default)")
def _add_to_cart(s: AddToCart, ctx: Ctx) -> None:
    if s.target is not None:
        loc = ctx.resolve(s.target, "target")
    else:
        loc = ctx.resolve(_selector(ctx, "add_to_cart"), "selectors.add_to_cart")
    loc.click(timeout=ctx.timeout_ms)


@action(AssertText, "assert_text(text, target?): text is visible on the page or within target")
def _assert_text(s: AssertText, ctx: Ctx) -> None:
    scope: Locator = ctx.resolve(s.target, "target") if s.target else ctx.page.locator("body")
    pw_expect(scope).to_contain_text(s.text, timeout=ctx.timeout_ms)


@action(AssertVisible, "assert_visible(target): element is visible")
def _assert_visible(s: AssertVisible, ctx: Ctx) -> None:
    pw_expect(ctx.resolve(s.target, "target")).to_be_visible(timeout=ctx.timeout_ms)


@action(AssertUrl, "assert_url(contains): current URL contains a substring")
def _assert_url(s: AssertUrl, ctx: Ctx) -> None:
    ctx.page.wait_for_url(lambda u: s.contains in u, timeout=ctx.timeout_ms)


def vocabulary_doc() -> str:
    return "\n".join(f"  - {spec.description}" for spec in REGISTRY.values())


def registered_names() -> set[str]:
    return set(REGISTRY)


def model_names() -> set[str]:
    return {m.model_fields["action"].default for m in STEP_MODELS}
