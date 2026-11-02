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
                          error_type="TimeoutError", screenshot="s.png"),
               StepResult(index=2, action="assert_text", status="skipped")])
    return RunResult(plan_persona="maya", site="shop", base_url="http://x", journeys=[ok, bad])


def test_junit_structure() -> None:
    root = ET.fromstring(junit_xml(_result()))  # noqa: S314
    assert root.attrib["tests"] == "2" and root.attrib["failures"] == "1"
    cases = root.findall("testcase")
    assert cases[0].find("failure") is None
    f = cases[1].find("failure")
    assert f is not None and "step 1 (click): boom" in f.attrib["message"]
    assert f.attrib["type"] == "TimeoutError"
    assert cases[1].attrib["classname"] == "journeyprobe.shop.maya"


def test_html_escapes_and_links() -> None:
    h = html_report(_result())
    assert "bad &lt;&amp;&gt;" in h and "boom &lt;b&gt;" in h
    assert 'href="s.png"' in h and 'href="t.zip"' in h and "1/2 journeys passed" in h


def test_write_reports(tmp_path: Path) -> None:
    paths = write_reports(_result(), tmp_path / "out")
    assert all(p.exists() for p in paths.values())
    assert RunResult.model_validate_json(paths["json"].read_text()).passed is False


def test_junit_type_falls_back_for_results_without_error_type() -> None:
    """Old results.json files have no error_type; the report must still render with the legacy value."""
    r = _result()
    r.journeys[1].steps[1].error_type = None
    f = ET.fromstring(junit_xml(r)).findall("testcase")[1].find("failure")  # noqa: S314
    assert f is not None and f.attrib["type"] == "StepFailed"


def test_error_type_of_maps_classes() -> None:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    from journeyprobe.actions import StepError
    from journeyprobe.runner import error_type_of

    assert error_type_of(PlaywrightTimeout("Timeout 1500ms exceeded.")) == "TimeoutError"
    assert error_type_of(PlaywrightError("net::ERR_FAILED")) == "PlaywrightError"
    assert error_type_of(AssertionError("Locator expected to be visible")) == "AssertionError"
    assert error_type_of(StepError("refusing to leave the site origin")) == "StepError"
    assert error_type_of(KeyError("x")) == "KeyError"


def test_error_type_round_trips_through_results_json(tmp_path: Path) -> None:
    paths = write_reports(_result(), tmp_path / "out")
    again = RunResult.model_validate_json(paths["json"].read_text())
    assert again.journeys[1].steps[1].error_type == "TimeoutError"
    assert "TimeoutError: boom" in html_report(again)
