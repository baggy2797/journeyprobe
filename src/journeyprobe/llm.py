"""Provider-agnostic LLM layer. Every call is JSON-schema constrained; callers validate with pydantic."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any, Protocol

from .models import JourneyBundle, Persona, SiteProfile


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    provider: str
    model: str
    mock: bool

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a JSON object. ``user`` is itself a JSON document. ``task`` is 'plan' or 'heal'."""
        ...


class OpenAILLM:
    provider = "openai"
    mock = False

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY is not set (use --mock-llm to run without a key)")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover - depends on extras
            raise LLMError("install the extra: pip install 'journeyprobe[openai]'") from e
        self.model = model
        self._client = OpenAI(api_key=key)

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": task, "schema": schema, "strict": False},
            },
        )
        content = resp.choices[0].message.content
        if not content:
            raise LLMError("empty response from model")
        try:
            out = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned non-JSON: {e}") from e
        if not isinstance(out, dict):
            raise LLMError("model returned a JSON value that is not an object")
        return out


class RecordedLLM:
    """Replays canned responses in order (used for fixtures and retry tests)."""

    provider = "recorded"
    model = "recorded"
    mock = True

    def __init__(self, responses: Sequence[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, str]] = []

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"task": task, "system": system, "user": user})
        if not self._responses:
            raise LLMError("RecordedLLM exhausted")
        return self._responses.pop(0)


def _tokens(s: str) -> set[str]:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in s).split() if len(t) > 2}


class MockLLM:
    """Deterministic offline stand-in. Rule-based, no network. Not 'smart' - it exists so the whole
    pipeline (generate -> validate -> run -> heal) is testable without a key."""

    provider = "mock"
    model = "mock-1"
    mock = True

    def complete_json(
        self, *, task: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        payload = json.loads(user)
        if task == "plan":
            return self._plan(payload)
        if task == "heal":
            return self._heal(payload)
        raise LLMError(f"unknown task {task!r}")

    def _plan(self, p: dict[str, Any]) -> dict[str, Any]:
        persona = Persona.model_validate(p["persona"])
        site = SiteProfile.model_validate(p["site"])
        journeys: list[dict[str, Any]] = []
        for goal in persona.goals:
            gt = _tokens(goal)
            scored = sorted(
                ((len(gt & _tokens(i)), i) for i in site.known_items), key=lambda x: (-x[0], x[1])
            )
            if not scored or scored[0][0] == 0:
                continue
            item = scored[0][1]
            journeys.append(
                {
                    "name": f"{persona.name}: {goal}",
                    "goal": goal,
                    "steps": [
                        {"action": "goto", "path": "/", "note": "start on the home page"},
                        {"action": "search", "query": item.split()[-1]},
                        {"action": "click", "target": {"role": "link", "name": item}},
                        {"action": "assert_text", "text": item},
                        {"action": "add_to_cart"},
                        {"action": "assert_text", "text": "Added to cart"},
                        {"action": "click", "target": {"testid": "cart-link"}},
                        {"action": "assert_url", "contains": "cart"},
                        {"action": "assert_text", "text": item, "target": {"testid": "cart-items"}},
                    ],
                }
            )
        if not journeys:
            journeys.append(
                {
                    "name": f"{persona.name}: browse",
                    "goal": "browse the site",
                    "steps": [
                        {"action": "goto", "path": "/"},
                        {"action": "assert_visible", "target": {"testid": "search-input"}},
                    ],
                }
            )
        return JourneyBundle.model_validate({"journeys": journeys}).model_dump(
            mode="json", exclude_none=True
        )

    def _heal(self, p: dict[str, Any]) -> dict[str, Any]:
        failed = p["failed_target"]
        cands = _dom_candidates(p["dom"])
        want = failed.get("testid") or failed.get("name") or failed.get("text") or failed.get("label")
        want = want or failed.get("css") or ""
        import difflib

        best: tuple[float, dict[str, str]] | None = None
        for c in cands:
            keys = [c.get("testid", ""), c.get("text", ""), c.get("label", "")]
            r = max(difflib.SequenceMatcher(None, want.lower(), k.lower()).ratio() for k in keys if k) \
                if any(keys) else 0.0
            if best is None or r > best[0]:
                best = (r, c)
        if best is None or best[0] < 0.4:
            raise LLMError("mock healer found no plausible replacement")
        c = best[1]
        if c.get("testid"):
            target: dict[str, Any] = {"testid": c["testid"]}
        elif c.get("text"):
            target = {"text": c["text"]}
        else:
            target = {"css": c["tag"]}
        return {
            "target": target,
            "rationale": f"closest element to {want!r} (similarity {best[0]:.2f}) in the snapshot",
        }


class _Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, str]] = []
        self._stack: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        item = {"tag": tag, "testid": a.get("data-testid", ""), "label": a.get("aria-label", ""),
                "text": ""}
        self.items.append(item)
        self._stack.append(item)

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._stack and data.strip():
            self._stack[-1]["text"] = (self._stack[-1]["text"] + " " + data.strip()).strip()


def _dom_candidates(dom: str) -> list[dict[str, str]]:
    p = _Collect()
    p.feed(dom)
    return [i for i in p.items if i["testid"] or (i["text"] and i["tag"] in {"a", "button", "label"})]


def get_llm(*, mock: bool, provider: str = "openai", model: str | None = None) -> LLM:
    if mock:
        return MockLLM()
    if provider == "openai":
        return OpenAILLM(model=model or "gpt-4o-mini")
    raise LLMError(f"unknown provider {provider!r}")
