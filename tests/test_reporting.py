from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405
from pathlib import Path

from journeyprobe.reporting import html_report, junit_xml, write_reports
from journeyprobe.runner import JourneyResult, RunResult, StepResult


def _result() -> RunResult:
    ok = JourneyResult(name="ok", goal="g", status="passed", duration_s=0.5,
                       steps=[StepResult(index=0, action="goto", status="passed")])
    bad = JourneyResult(
        name="bad <&>", goal="g", status="failed", duration_s=1.0, trace="t.zip",
        steps=[StepResult(index=0, action="goto", status="passed"),
               StepResult(index=1, action="click", status="failed", error="boom <b>",
                          screenshot="s.png"),
               StepResult(index=2, action="assert_text", status="skipped")])
    return RunResult(plan_persona="maya", site="shop", base_url="http://x", journeys=[ok, bad])


def test_junit_structure() -> None:
    root = ET.fromstring(junit_xml(_result()))  # noqa: S314
    assert root.attrib["tests"] == "2" and root.attrib["failures"] == "1"
    cases = root.findall("testcase")
    assert cases[0].find("failure") is None
    f = cases[1].find("failure")
    assert f is not None and "step 1 (click): boom" in f.attrib["message"]
    assert cases[1].attrib["classname"] == "journeyprobe.shop.maya"


def test_html_escapes_and_links() -> None:
    h = html_report(_result())
    assert "bad &lt;&amp;&gt;" in h and "boom &lt;b&gt;" in h
    assert 'href="s.png"' in h and 'href="t.zip"' in h and "1/2 journeys passed" in h


def test_write_reports(tmp_path: Path) -> None:
    paths = write_reports(_result(), tmp_path / "out")
    assert all(p.exists() for p in paths.values())
    assert RunResult.model_validate_json(paths["json"].read_text()).passed is False
