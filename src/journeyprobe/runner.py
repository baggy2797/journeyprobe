"""Deterministic Playwright replay. No LLM is imported or called here."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Literal

from playwright.sync_api import sync_playwright
from pydantic import BaseModel

from .actions import REGISTRY, Ctx
from .models import Plan, SiteProfile, Target

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
        browser = pw.chromium.launch(headless=not headed)
        for ji, journey in enumerate(plan.journeys):
            jslug = f"{ji}-{_slug(journey.name)}"
            context = browser.new_context(viewport={"width": 1280, "height": 800})
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
