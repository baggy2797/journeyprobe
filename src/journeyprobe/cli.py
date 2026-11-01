"""Typer CLI: generate | validate | run | heal | demo-shop."""

from __future__ import annotations

import functools
import http.server
import socketserver
from pathlib import Path
from typing import Annotated

import typer

from .healer import canonical_hint, propose_heals
from .llm import LLMError, get_llm
from .models import (
    check_plan_against_site,
    dump_yaml,
    load_persona,
    load_plan,
    load_site,
)
from .planner import generate_plan
from .reporting import write_reports
from .runner import RunResult, run_plan

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="LLM-authored, deterministically replayed user-journey tests.")

MockOpt = Annotated[bool, typer.Option("--mock-llm", help="Offline deterministic LLM; no API key.")]


def _fail(msg: str, code: int = 2) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(code)


@app.command()
def generate(
    persona: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    site: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    out: Annotated[Path, typer.Option(help="Plan YAML to write.")] = Path("plan.yaml"),
    mock_llm: MockOpt = False,
    model: Annotated[str | None, typer.Option(help="Model name (OpenAI).")] = None,
) -> None:
    """Have an LLM author a reviewable YAML plan for PERSONA on SITE."""
    try:
        p, s = load_persona(persona), load_site(site)
        plan = generate_plan(p, s, get_llm(mock=mock_llm, model=model))
    except (ValueError, LLMError) as e:
        raise _fail(str(e)) from e
    out.write_text(dump_yaml(plan), encoding="utf-8")
    n = sum(len(j.steps) for j in plan.journeys)
    typer.echo(f"wrote {out}: {len(plan.journeys)} journeys, {n} steps. Review it before running.")


@app.command()
def validate(
    plan: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    site: Annotated[Path | None, typer.Option(help="Also check the plan against this profile.")] = None,
) -> None:
    """Validate a plan (schema, and against a site profile when given). Exit 1 if invalid."""
    try:
        pl = load_plan(plan)
        errs = check_plan_against_site(pl, load_site(site)) if site else []
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    if errs:
        for msg in errs:
            typer.secho(f"  - {msg}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(f"{plan}: valid ({len(pl.journeys)} journeys)")


@app.command()
def run(
    plan: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    site: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    out_dir: Annotated[Path, typer.Option(help="Reports, screenshots, traces.")] = Path("jp-report"),
    base_url: Annotated[str | None, typer.Option(help="Override the profile's base_url.")] = None,
    headed: bool = False,
    timeout_ms: Annotated[int, typer.Option(min=100)] = 5000,
) -> None:
    """Replay PLAN with Playwright. No LLM involved. Exit 1 if any journey fails."""
    try:
        pl, s = load_plan(plan), load_site(site)
    except ValueError as e:
        raise _fail(str(e)) from e
    errs = check_plan_against_site(pl, s)
    if errs:
        raise _fail("plan does not match site profile:\n  - " + "\n  - ".join(errs))
    result: RunResult = run_plan(pl, s, out_dir, base_url=base_url, headed=headed,
                                 timeout_ms=timeout_ms)
    paths = write_reports(result, out_dir)
    for j in result.journeys:
        typer.echo(f"[{j.status.upper()}] {j.name} ({j.duration_s}s)")
        for st in j.steps:
            if st.status == "failed":
                typer.secho(f"    step {st.index} {st.action}: {st.error}", fg=typer.colors.RED)
    typer.echo(f"reports: {paths['junit']}, {paths['html']}")
    if not result.passed:
        raise typer.Exit(1)


@app.command()
def heal(
    plan: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    site: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    report_dir: Annotated[Path, typer.Option(exists=True, file_okay=False,
                                             help="Output dir of a failed `run`.")] = Path("jp-report"),
    out: Annotated[Path, typer.Option(help="Where to write the proposed patch.")] = Path("heal.patch"),
    mock_llm: MockOpt = False,
    model: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Propose locator fixes for failed steps as a unified diff. Never edits your files."""
    try:
        pl, s = load_plan(plan), load_site(site)
        result = RunResult.model_validate_json((report_dir / "results.json").read_text("utf-8"))
        heals = propose_heals(result, report_dir, pl, str(plan), s, str(site),
                              get_llm(mock=mock_llm, model=model))
    except (ValueError, LLMError, OSError) as e:
        raise _fail(str(e)) from e
    if not heals:
        typer.echo("nothing to heal (no failed step with a locator).")
        return
    patch = "".join(h.diff for h in heals)
    out.write_text(patch, encoding="utf-8")
    for h in heals:
        typer.echo(f"{h.locator_path}: {h.old.describe()} -> {h.new.describe()}\n  why: {h.rationale}")
    if not (canonical_hint(pl, plan) or canonical_hint(s, site)):
        typer.secho("note: files are not in canonical form; the diff may need `patch -l` or manual apply.",
                    fg=typer.colors.YELLOW, err=True)
    typer.echo(f"proposed patch written to {out} (NOT applied). Review, then: patch -p1 < {out}")


@app.command("demo-shop")
def demo_shop(port: int = 8431, host: str = "127.0.0.1") -> None:
    """Serve the bundled fixture shop (used by the examples and docker-compose)."""
    from .shop import shop_dir

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(shop_dir()))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((host, port), handler) as httpd:
        typer.echo(f"fixture shop on http://{host}:{port}/")
        httpd.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    app()
