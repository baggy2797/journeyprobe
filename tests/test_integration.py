"""Real Playwright against the local fixture shop. No network, no LLM key."""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET  # noqa: S405
from pathlib import Path

import pytest
from typer.testing import CliRunner

from journeyprobe.cli import app
from journeyprobe.healer import propose_heals
from journeyprobe.llm import LLMError, MockLLM, RecordedLLM
from journeyprobe.models import SiteProfile, load_plan, load_site
from journeyprobe.reporting import write_reports
from journeyprobe.runner import run_plan

pytestmark = pytest.mark.integration
runner = CliRunner()


def test_example_plan_replays_green(shop_url: str, site: SiteProfile, workdir: Path, tmp_path: Path) -> None:
    plan = load_plan(workdir / "plans/maya-fixture-shop.plan.yaml")
    res = run_plan(plan, site, tmp_path, base_url=shop_url)
    assert res.passed, res.model_dump_json(indent=1)
    assert all(s.status == "passed" for j in res.journeys for s in j.steps)
    assert not list(tmp_path.glob("*.png"))  # no failure artifacts on a green run


def _break_site(workdir: Path) -> Path:
    p = workdir / "sites/broken.yaml"
    p.write_text((workdir / "sites/fixture-shop.yaml").read_text().replace(
        "testid: search-input", "testid: search-box"))
    return p


def test_failure_produces_artifacts_and_junit(shop_url: str, workdir: Path, tmp_path: Path) -> None:
    site = load_site(_break_site(workdir))
    plan = load_plan(workdir / "plans/maya-fixture-shop.plan.yaml")
    res = run_plan(plan, site, tmp_path, base_url=shop_url, timeout_ms=1000)
    assert not res.passed
    bad = res.journeys[0].steps[1]
    assert bad.status == "failed" and bad.locator_path == "selectors.search_input"
    assert res.journeys[0].steps[2].status == "skipped"
    assert bad.screenshot and bad.dom and res.journeys[0].trace
    assert (tmp_path / bad.screenshot).exists() and (tmp_path / bad.dom).exists()
    assert (tmp_path / res.journeys[0].trace).exists()
    paths = write_reports(res, tmp_path)
    assert ET.parse(paths["junit"]).getroot().attrib["failures"] == "2"  # noqa: S314


def test_step_failure_in_plan_target_and_assertions(shop_url: str, site: SiteProfile, tmp_path: Path) -> None:
    from journeyprobe.models import Plan

    plan = Plan.model_validate({
        "persona": "p", "site": "fixture-shop", "generated_by": {"provider": "t", "model": "t"},
        "journeys": [{"name": "j", "goal": "g", "steps": [
            {"action": "goto", "path": "/"},
            {"action": "fill", "target": {"testid": "search-input"}, "value": "lamp"},
            {"action": "click", "target": {"testid": "search-submit"}},
            {"action": "assert_visible", "target": {"role": "link", "name": "Desk Lamp"}},
            {"action": "assert_url", "contains": "q=lamp"},
            {"action": "click", "target": {"css": "li[data-testid=result] a", "nth": 0}},
            {"action": "assert_text", "text": "$35.00", "target": {"testid": "price"}},
            {"action": "click", "target": {"testid": "add-to-cart-btn"}},
        ]}],
    })
    res = run_plan(plan, site, tmp_path, base_url=shop_url, timeout_ms=800)
    steps = res.journeys[0].steps
    assert [s.status for s in steps[:7]] == ["passed"] * 7
    assert steps[7].status == "failed" and steps[7].locator_path == "journeys[0].steps[7].target"


def test_goto_cannot_leave_site(shop_url: str, site: SiteProfile, tmp_path: Path) -> None:
    from journeyprobe.models import Plan

    plan = Plan.model_validate({
        "persona": "p", "site": "fixture-shop", "generated_by": {"provider": "t", "model": "t"},
        "journeys": [{"name": "j", "goal": "g", "steps": [{"action": "goto", "path": "http://example.org/"}]}]})
    res = run_plan(plan, site, tmp_path, base_url=shop_url)  # bypasses check_plan_against_site on purpose
    assert res.journeys[0].steps[0].error and "refusing to leave" in res.journeys[0].steps[0].error


def test_heal_proposes_verified_diff_and_never_applies(shop_url: str, workdir: Path, tmp_path: Path) -> None:
    broken = _break_site(workdir)
    plan_path = workdir / "plans/maya-fixture-shop.plan.yaml"
    before = broken.read_text()
    site = load_site(broken)
    plan = load_plan(plan_path)
    res = run_plan(plan, site, tmp_path, base_url=shop_url, timeout_ms=800)
    heals = propose_heals(res, tmp_path, plan, str(plan_path), site, "sites/broken.yaml", MockLLM())
    assert len(heals) == 2
    assert broken.read_text() == before  # never applied
    h = heals[0]
    assert h.locator_path == "selectors.search_input" and h.new.testid == "search-input"
    assert "-    testid: search-box" in h.diff and "+    testid: search-input" in h.diff

    # Applying the patch by hand makes the suite green again.
    if shutil.which("patch"):
        (workdir / "heal.patch").write_text(heals[0].diff)
        subprocess.run(["patch", "-p1", "-d", str(workdir), "-i", "heal.patch"], check=True,
                       capture_output=True)
        fixed = run_plan(plan, load_site(broken), tmp_path / "again", base_url=shop_url)
        assert fixed.passed


def test_heal_rejects_ambiguous_proposal(shop_url: str, workdir: Path, tmp_path: Path) -> None:
    broken = _break_site(workdir)
    site = load_site(broken)
    plan = load_plan(workdir / "plans/maya-fixture-shop.plan.yaml")
    res = run_plan(plan, site, tmp_path, base_url=shop_url, timeout_ms=800)
    llm = RecordedLLM([{"target": {"css": "body *"}, "rationale": "lazy"}])
    with pytest.raises(LLMError, match="exactly 1"):
        propose_heals(res, tmp_path, plan, "p", site, "s", llm)


def test_cli_run_and_heal_end_to_end(shop_url: str, workdir: Path, tmp_path: Path) -> None:
    ex = workdir
    out = tmp_path / "rep"
    good = runner.invoke(app, ["run", str(ex / "plans/maya-fixture-shop.plan.yaml"), "--site",
                               str(ex / "sites/fixture-shop.yaml"), "--base-url", shop_url,
                               "--out-dir", str(out)])
    assert good.exit_code == 0, good.output
    assert (out / "junit.xml").exists() and (out / "report.html").exists()

    broken = _break_site(ex)
    bad = runner.invoke(app, ["run", str(ex / "plans/maya-fixture-shop.plan.yaml"), "--site",
                              str(broken), "--base-url", shop_url, "--out-dir", str(out),
                              "--timeout-ms", "800"])
    assert bad.exit_code == 1 and "FAILED" in bad.output

    patch = tmp_path / "heal.patch"
    h = runner.invoke(app, ["heal", str(ex / "plans/maya-fixture-shop.plan.yaml"), "--site",
                            str(broken), "--report-dir", str(out), "--out", str(patch), "--mock-llm"])
    assert h.exit_code == 0, h.output
    assert "NOT applied" in h.output and "search-input" in patch.read_text()

    ok_dir = tmp_path / "okrep"
    runner.invoke(app, ["run", str(ex / "plans/maya-fixture-shop.plan.yaml"), "--site",
                        str(ex / "sites/fixture-shop.yaml"), "--base-url", shop_url,
                        "--out-dir", str(ok_dir)])
    nothing = runner.invoke(app, ["heal", str(ex / "plans/maya-fixture-shop.plan.yaml"), "--site",
                                  str(ex / "sites/fixture-shop.yaml"), "--report-dir", str(ok_dir),
                                  "--mock-llm"])
    assert "nothing to heal" in nothing.output


def test_cli_run_rejects_mismatched_plan(workdir: Path, tmp_path: Path) -> None:
    plan = workdir / "plans/x.yaml"
    plan.write_text((workdir / "plans/maya-fixture-shop.plan.yaml").read_text().replace(
        "site: fixture-shop", "site: nope"))
    r = runner.invoke(app, ["run", str(plan), "--site", str(workdir / "sites/fixture-shop.yaml"),
                            "--out-dir", str(tmp_path)])
    assert r.exit_code == 2
