from __future__ import annotations

import json
from typing import Any

import pytest

from journeyprobe.llm import LLMError, MockLLM, OpenAILLM, RecordedLLM, get_llm
from journeyprobe.models import Persona, SiteProfile
from journeyprobe.planner import generate_plan

from .conftest import DATA


def test_recorded_response_becomes_validated_plan(
    persona: Persona, site: SiteProfile, recorded: dict[str, Any]
) -> None:
    llm = RecordedLLM([recorded])
    plan = generate_plan(persona, site, llm)
    assert plan.generated_by.provider == "recorded" and plan.generated_by.mock
    assert plan.journeys[0].steps[1].action == "search"
    call = llm.calls[0]
    assert call["task"] == "plan"
    assert "add_to_cart" in call["system"]  # vocabulary is part of the prompt
    assert json.loads(call["user"])["persona"]["name"] == "maya"


def test_invalid_output_is_repaired_with_feedback(
    persona: Persona, site: SiteProfile, recorded: dict[str, Any]
) -> None:
    invalid = json.loads((DATA / "invalid_plan_response.json").read_text())
    llm = RecordedLLM([invalid, recorded])
    plan = generate_plan(persona, site, llm)
    assert len(plan.journeys) == 1
    assert "previous_errors" in json.loads(llm.calls[1]["user"])
    assert "previous_errors" not in json.loads(llm.calls[0]["user"])


def test_semantic_violation_triggers_retry_then_gives_up(persona: Persona, site: SiteProfile) -> None:
    offsite = {"journeys": [{"name": "j", "goal": "g",
                             "steps": [{"action": "goto", "path": "https://evil.example/"}]}]}
    with pytest.raises(LLMError, match="after 2 attempts"):
        generate_plan(persona, site, RecordedLLM([offsite, offsite]), max_attempts=2)


def test_recorded_llm_exhaustion() -> None:
    with pytest.raises(LLMError):
        RecordedLLM([]).complete_json(task="plan", system="", user="{}", schema={})


def test_mock_llm_plan_matches_goals(persona: Persona, site: SiteProfile) -> None:
    plan = generate_plan(persona, site, MockLLM())
    assert [j.goal for j in plan.journeys] == persona.goals
    first = plan.journeys[0].steps
    assert first[2].model_dump()["target"]["name"] == "Ceramic Mug"
    assert plan.generated_by.mock


def test_mock_llm_falls_back_to_browse(site: SiteProfile) -> None:
    p = Persona(name="z", description="d", goals=["do something unrelated"])
    plan = generate_plan(p, site, MockLLM())
    assert plan.journeys[0].goal == "browse the site"


def test_mock_llm_unknown_task() -> None:
    with pytest.raises(LLMError):
        MockLLM().complete_json(task="nope", system="", user="{}", schema={})


def test_get_llm_and_openai_key_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_llm(mock=True), MockLLM)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        get_llm(mock=False)
    with pytest.raises(LLMError, match="provider"):
        get_llm(mock=False, provider="nope")


class _Msg:
    def __init__(self, content: str | None) -> None:
        self.message = type("M", (), {"content": content})()


class _FakeClient:
    def __init__(self, content: str | None) -> None:
        self.kwargs: dict[str, Any] = {}
        outer = self

        class _C:
            def create(self, **kw: Any) -> Any:
                outer.kwargs = kw
                return type("R", (), {"choices": [_Msg(content)]})()

        self.chat = type("Chat", (), {"completions": _C()})()


def _openai_with(content: str | None) -> tuple[OpenAILLM, _FakeClient]:
    llm = OpenAILLM.__new__(OpenAILLM)
    llm.model = "m"
    fc = _FakeClient(content)
    llm._client = fc  # type: ignore[assignment]
    return llm, fc


def test_openai_sends_json_schema_and_parses() -> None:
    llm, fc = _openai_with('{"a": 1}')
    assert llm.complete_json(task="plan", system="s", user="u", schema={"type": "object"}) == {"a": 1}
    assert fc.kwargs["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize("content", [None, "not json", "[1]"])
def test_openai_bad_responses(content: str | None) -> None:
    llm, _ = _openai_with(content)
    with pytest.raises(LLMError):
        llm.complete_json(task="plan", system="s", user="u", schema={})
