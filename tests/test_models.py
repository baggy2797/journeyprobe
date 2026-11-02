from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from journeyprobe.models import (
    AddToCart,
    Journey,
    Plan,
    SiteProfile,
    Target,
    check_plan_against_site,
    dump_yaml,
    load_plan,
    load_site,
)

from .conftest import EXAMPLES


def test_target_requires_exactly_one_strategy() -> None:
    with pytest.raises(ValidationError):
        Target()
    with pytest.raises(ValidationError):
        Target(testid="a", css="b")
    with pytest.raises(ValidationError):
        Target(name="x")
    assert "role='link'" in Target(role="link", name="Home").describe()
    assert "nth=1" in Target(css="li", nth=1).describe()


def test_example_plan_roundtrips_canonically() -> None:
    p = EXAMPLES / "plans" / "maya-fixture-shop.plan.yaml"
    plan = load_plan(p)
    assert dump_yaml(plan) == p.read_text()


def test_unknown_action_rejected() -> None:
    with pytest.raises(ValidationError):
        Journey.model_validate({"name": "n", "goal": "g", "steps": [{"action": "teleport"}]})


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Journey.model_validate(
            {"name": "n", "goal": "g", "steps": [{"action": "goto", "path": "/", "oops": 1}]}
        )


def test_site_requires_http() -> None:
    with pytest.raises(ValidationError):
        SiteProfile(name="x", base_url="file:///etc/passwd")


def test_load_errors_are_readable(tmp_path: Path) -> None:
    bad = tmp_path / "p.yaml"
    bad.write_text("name: [unclosed")
    with pytest.raises(ValueError, match="invalid YAML"):
        load_plan(bad)
    bad.write_text("name: x")
    with pytest.raises(ValueError, match="base_url"):
        load_site(bad)


def _plan(steps: list[dict[str, object]], site: str = "fixture-shop") -> Plan:
    return Plan.model_validate({
        "persona": "p", "site": site,
        "generated_by": {"provider": "t", "model": "t"},
        "journeys": [{"name": "j", "goal": "g", "steps": steps}],
    })


def test_semantic_checks(site: SiteProfile) -> None:
    ok = _plan([{"action": "goto", "path": "/"}, {"action": "search", "query": "x"}])
    assert check_plan_against_site(ok, site) == []
    bad = _plan([{"action": "goto", "path": "https://evil.example/x"}], site="other")
    errs = check_plan_against_site(bad, site)
    assert any("other" in e for e in errs) and any("leaves the site origin" in e for e in errs)
    bare = site.model_copy(update={"selectors": {}})
    errs = check_plan_against_site(_plan([{"action": "search", "query": "x"}, {"action": "add_to_cart"}]), bare)
    assert len(errs) == 2
    assert isinstance(_plan([{"action": "add_to_cart"}]).journeys[0].steps[0], AddToCart)
