"""JUnit XML, HTML and JSON reports."""

from __future__ import annotations

import html
import xml.etree.ElementTree as ET  # noqa: S405 - used for writing only
from pathlib import Path

from .runner import RunResult


def junit_xml(result: RunResult) -> str:
    """One <testsuite> per run; one <testcase> per journey (stable names so flaky-test tools can key on them)."""
    failures = sum(j.status == "failed" for j in result.journeys)
    suite = ET.Element("testsuite", {
        "name": f"journeyprobe.{result.site}", "tests": str(len(result.journeys)),
        "failures": str(failures), "errors": "0", "skipped": "0",
        "time": f"{sum(j.duration_s for j in result.journeys):.3f}",
    })
    for j in result.journeys:
        tc = ET.SubElement(suite, "testcase", {
            "classname": f"journeyprobe.{result.site}.{result.plan_persona}",
            "name": j.name, "time": f"{j.duration_s:.3f}",
        })
        lines = [f"{s.index}. {s.action} [{s.status}]" for s in j.steps]
        if j.status == "failed":
            bad = next(s for s in j.steps if s.status == "failed")
            fl = ET.SubElement(tc, "failure", {
                "type": bad.error_type or "StepFailed",  # StepFailed only for results written before error_type existed
                "message": f"step {bad.index} ({bad.action}): {bad.error}",
            })
            if bad.screenshot:
                lines.append(f"[[ATTACHMENT|{bad.screenshot}]]")
            fl.text = "\n".join(lines)
        ET.SubElement(tc, "system-out").text = "\n".join(lines)
    ET.indent(suite)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suite, encoding="unicode")


def html_report(result: RunResult) -> str:
    e = html.escape
    rows: list[str] = []
    for j in result.journeys:
        steps = []
        for s in j.steps:
            kind = f"{s.error_type}: " if s.error_type else ""
            err = f'<div class="err">{e(kind)}{e(s.error or "")}</div>' if s.error else ""
            shot = f'<a href="{e(s.screenshot)}"><img src="{e(s.screenshot)}" alt="failure"></a>' \
                if s.screenshot else ""
            steps.append(f'<li class="{s.status}"><b>{e(s.action)}</b> {e(s.note or "")} '
                         f"<small>{s.status} {s.duration_s}s</small>{err}{shot}</li>")
        trace = f' <a href="{e(j.trace)}">trace</a>' if j.trace else ""
        rows.append(f'<section class="{j.status}"><h2>{e(j.name)} - {j.status}{trace}</h2>'
                    f"<p>{e(j.goal)}</p><ol>{''.join(steps)}</ol></section>")
    ok = sum(j.status == "passed" for j in result.journeys)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>journeyprobe report</title>
<style>body{{font:15px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}}
section{{border-left:4px solid #999;padding:.2rem 1rem;margin:1rem 0}}
section.passed{{border-color:#2a9d4a}}section.failed{{border-color:#d33}}
li.passed b{{color:#2a9d4a}}li.failed b{{color:#d33}}li.skipped{{color:#888}}
.err{{color:#d33;font-family:monospace}}img{{max-width:420px;border:1px solid #ccc;display:block}}</style>
<h1>journeyprobe: {e(result.site)} / {e(result.plan_persona)}</h1>
<p>{ok}/{len(result.journeys)} journeys passed against {e(result.base_url)}</p>
{''.join(rows)}</html>"""


def write_reports(result: RunResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "junit": out_dir / "junit.xml",
        "html": out_dir / "report.html",
        "json": out_dir / "results.json",
    }
    paths["junit"].write_text(junit_xml(result), encoding="utf-8")
    paths["html"].write_text(html_report(result), encoding="utf-8")
    paths["json"].write_text(result.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    return paths
