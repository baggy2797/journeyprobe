"""LLM-backed plan authoring with schema validation and bounded repair retries."""

from __future__ import annotations

import json

from pydantic import ValidationError

from .actions import vocabulary_doc
from .llm import LLM, LLMError
from .models import (
    GeneratedBy,
    JourneyBundle,
    Persona,
    Plan,
    SiteProfile,
    check_plan_against_site,
)

SYSTEM = """You author end-to-end user-journey tests for a website as a JSON plan.
Return ONLY a JSON object matching the provided schema. Rules:
- Use only these actions (no others exist):
{vocab}
- Prefer data-testid or role+name locators; never invent locators not supported by the site profile.
- Each journey must serve one persona goal and end with at least one assertion.
- 'goto' paths stay on the site. Do not include credentials or personal data.
"""


def generate_plan(persona: Persona, site: SiteProfile, llm: LLM, max_attempts: int = 3) -> Plan:
    system = SYSTEM.format(vocab=vocabulary_doc())
    schema = JourneyBundle.model_json_schema()
    user_doc: dict[str, object] = {
        "persona": persona.model_dump(mode="json"),
        "site": site.model_dump(mode="json", exclude_none=True),
    }
    feedback = ""
    last_err = ""
    for _ in range(max_attempts):
        user = json.dumps({**user_doc, **({"previous_errors": feedback} if feedback else {})})
        raw = llm.complete_json(task="plan", system=system, user=user, schema=schema)
        try:
            bundle = JourneyBundle.model_validate(raw)
            plan = Plan(
                persona=persona.name,
                site=site.name,
                generated_by=GeneratedBy(provider=llm.provider, model=llm.model, mock=llm.mock),
                journeys=bundle.journeys,
            )
            errs = check_plan_against_site(plan, site)
        except ValidationError as e:
            errs = [str(e)]
        else:
            if not errs:
                return plan
        last_err = "; ".join(errs)
        feedback = last_err
    raise LLMError(f"model could not produce a valid plan after {max_attempts} attempts: {last_err}")
