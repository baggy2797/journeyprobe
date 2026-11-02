"""Deterministic Playwright replay. No LLM is imported or called here."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

from playwright.sync_api import BrowserContext, Route, sync_playwright
from pydantic import BaseModel

from .actions import REGISTRY, Ctx, StepError
from .models import Plan, SiteProfile, Target, same_origin

DOM_LIMIT = 30_000


class StepResult(BaseModel):
    index: int
    action: str
    note: str | None = None
    status: Literal["passed", "failed", "skipped"]
    duration_s: float = 0.0
    error: str | None = None
    locator_path: str | None = None  # e.g. "journeys[0].steps[2].target" or "selectors.search_input"
    locator: Target | None = None
    screenshot: str | None = None
    dom: str | None = None


class JourneyResult(BaseModel):
    name: str
    goal: str
    status: Literal["passed", "failed"]
    duration_s: float
    steps: list[StepResult]
    trace: str | None = None


class RunResult(BaseModel):
    plan_persona: str
    site: str
    base_url: str
    journeys: list[JourneyResult]

    @property
    def passed(self) -> bool:
        return all(j.status == "passed" for j in self.journeys)


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:60] or "x"


def _trim_dom(html: str) -> str:
    html = re.sub(r"<(script|style|svg)\b.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return re.sub(r"\s+", " ", html)[:DOM_LIMIT]


MAX_REDIRECTS = 10

# The navigation guard fulfils documents itself, and Chromium then classes them as "public", so its
# Private Network Access checks block the page's own scripts when the site under test lives on a private or
# loopback address (a Docker service, a staging box, localhost). Those checks are therefore turned off, but
# ONLY when the target itself is on such an address (see _is_private_target). For a public target they stay
# on, so a page on someone else's site cannot probe your localhost or internal network through the browser.
# Unknown feature names are ignored, so this survives Chromium upgrades.
_PNA_OFF = [
    "--disable-features=BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessSendPreflights,"
    "PrivateNetworkAccessRespectPreflightResults,PrivateNetworkAccessForNavigations,"
    "PrivateNetworkAccessForWorkers,LocalNetworkAccessChecks,LocalNetworkAccessChecksWebSockets",
]


def _is_private_target(base_url: str) -> bool:
    """True when every address the host resolves to is private, loopback or link-local. Fails safe (False)."""
    host = urlparse(base_url).hostname
    if not host:
        return False
    try:
        addrs = {ipaddress.ip_address(str(i[4][0]).split("%")[0]) for i in socket.getaddrinfo(host, None)}
    except (OSError, ValueError):
        return False
    return bool(addrs) and all(a.is_private or a.is_loopback or a.is_link_local for a in addrs)


def _confine_navigation(context: BrowserContext, base_url: str) -> None:
    """Abort every off-origin page navigation: direct ones, link clicks and every redirect hop.

    A redirect that the handler hands back to the browser is followed inside the browser's network stack
    and is NOT routed again, so a chain such as /a -> /b -> other-origin would escape a first-hop-only check.
    The handler therefore follows the whole chain itself (redirects off, one fetch per hop), vets every
    Location, and aborts if any hop leaves the origin. If the chain ends on a same-origin page, the browser
    gets one redirect straight to that validated URL, so page.url and relative links stay correct.
    Sub-resources (images, scripts, fonts) are left alone: the point is that a plan can never end up driving
    a different site, not that a page may not load a CDN asset.
    """
    def guard(route: Route) -> None:
        req = route.request
        if not req.is_navigation_request():
            route.continue_()
            return
        if not same_origin(req.url, base_url):
            route.abort("blockedbyclient")
            return
        try:
            cur = req.url
            resp = route.fetch(max_redirects=0)
            for _ in range(MAX_REDIRECTS):
                if not 300 <= resp.status < 400:
                    break
                loc = resp.headers.get("location")
                nxt = urljoin(cur, loc) if loc else ""
                if not nxt or not same_origin(nxt, base_url):
                    route.abort("blockedbyclient")
                    return
                cur = nxt
                resp = route.fetch(url=cur, method="GET", post_data=b"", max_redirects=0)
            else:
                route.abort("failed")  # redirect loop or an absurdly long chain
                return
            if cur == req.url:
                route.fulfill(response=resp)
            else:
                route.fulfill(status=302, headers={"location": cur})
        except Exception:  # noqa: BLE001 - fail closed: a navigation we could not vet does not happen
            route.abort("failed")

    context.route("**/*", guard)


def run_plan(
    plan: Plan,
    site: SiteProfile,
    out_dir: Path,
    *,
    base_url: str | None = None,
    headed: bool = False,
    timeout_ms: int = 5000,
) -> RunResult:
    base = base_url or site.base_url
    if base_url:
        site = site.model_copy(update={"base_url": base_url})
    out_dir.mkdir(parents=True, exist_ok=True)
    journeys: list[JourneyResult] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, args=_PNA_OFF if _is_private_target(base) else [])
        for ji, journey in enumerate(plan.journeys):
            jslug = f"{ji}-{_slug(journey.name)}"
            # Service workers can answer navigations without the request ever reaching the route guard.
            context = browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
            _confine_navigation(context, base)
            context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            ctx = Ctx(page=page, site=site, base_url=base, timeout_ms=timeout_ms)
            results: list[StepResult] = []
            failed = False
            t_journey = time.perf_counter()
            for si, step in enumerate(journey.steps):
                if failed:
                    results.append(StepResult(index=si, action=step.action, note=step.note,
                                              status="skipped"))
                    continue
                ctx.locator_path = ctx.locator = None
                t0 = time.perf_counter()
                try:
                    REGISTRY[step.action].handler(step, ctx)
                    # Defence in depth: whatever the step did (click, search, goto), we must still be on the site.
                    if page.url != "about:blank" and not same_origin(page.url, base):
                        raise StepError(f"the browser ended up outside the site origin: {page.url}")
                    results.append(StepResult(index=si, action=step.action, note=step.note,
                                              status="passed",
                                              duration_s=round(time.perf_counter() - t0, 3)))
                except Exception as e:  # noqa: BLE001 - any failure is a test failure
                    failed = True
                    shot = out_dir / f"{jslug}-step{si}.png"
                    dom_file = out_dir / f"{jslug}-step{si}.dom.html"
                    try:
                        page.screenshot(path=shot, full_page=True)
                        dom_file.write_text(_trim_dom(page.content()), encoding="utf-8")
                    except Exception:  # noqa: BLE001  # pragma: no cover - page may be gone
                        pass
                    path = ctx.locator_path
                    if path == "target":
                        path = f"journeys[{ji}].steps[{si}].target"
                    results.append(StepResult(
                        index=si, action=step.action, note=step.note, status="failed",
                        duration_s=round(time.perf_counter() - t0, 3),
                        error=str(e).strip().splitlines()[0][:500] if str(e).strip() else repr(e),
                        locator_path=path, locator=ctx.locator,
                        screenshot=shot.name if shot.exists() else None,
                        dom=dom_file.name if dom_file.exists() else None,
                    ))
            trace_name: str | None = None
            if failed:
                trace_name = f"{jslug}.trace.zip"
                context.tracing.stop(path=out_dir / trace_name)
            else:
                context.tracing.stop()
            context.close()
            journeys.append(JourneyResult(
                name=journey.name, goal=journey.goal,
                status="failed" if failed else "passed",
                duration_s=round(time.perf_counter() - t_journey, 3),
                steps=results, trace=trace_name,
            ))
        browser.close()
    return RunResult(plan_persona=plan.persona, site=plan.site, base_url=base, journeys=journeys)
