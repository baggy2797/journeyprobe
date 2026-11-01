from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from journeyprobe.cli import app

runner = CliRunner()


def test_generate_then_validate(workdir: Path) -> None:
    out = workdir / "new.plan.yaml"
    r = runner.invoke(app, ["generate", "--persona", str(workdir / "personas/maya.yaml"),
                            "--site", str(workdir / "sites/fixture-shop.yaml"),
                            "--out", str(out), "--mock-llm"])
    assert r.exit_code == 0, r.output
    assert out.read_text() == (workdir / "plans/maya-fixture-shop.plan.yaml").read_text()
    v = runner.invoke(app, ["validate", str(out), "--site", str(workdir / "sites/fixture-shop.yaml")])
    assert v.exit_code == 0 and "valid" in v.output


def test_validate_rejects_bad_plan(tmp_path: Path, workdir: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\npersona: x\n")
    assert runner.invoke(app, ["validate", str(bad)]).exit_code == 1
    plan = (workdir / "plans/maya-fixture-shop.plan.yaml").read_text().replace(
        "site: fixture-shop", "site: other")
    bad.write_text(plan)
    r = runner.invoke(app, ["validate", str(bad), "--site", str(workdir / "sites/fixture-shop.yaml")])
    assert r.exit_code == 1


def test_generate_without_key_fails_cleanly(workdir: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = runner.invoke(app, ["generate", "--persona", str(workdir / "personas/maya.yaml"),
                            "--site", str(workdir / "sites/fixture-shop.yaml"),
                            "--out", str(workdir / "x.yaml")])
    assert r.exit_code == 2 and "OPENAI_API_KEY" in r.output
