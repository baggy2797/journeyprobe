"""Pydantic models: personas, site profiles and the journey plan (the reviewable artifact)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA_VERSION = 1


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(_Strict):
    """A locator. Exactly one strategy must be set (``name`` may accompany ``role``)."""

    testid: str | None = None
    role: str | None = None
    name: str | None = None
    text: str | None = None
    label: str | None = None
    css: str | None = None
    nth: int | None = Field(default=None, ge=0, description="Pick the nth match (0-based).")

    @model_validator(mode="after")
    def _exactly_one_strategy(self) -> Target:
        used = [k for k in ("testid", "role", "text", "label", "css") if getattr(self, k)]
        if len(used) != 1:
            raise ValueError(f"exactly one of testid/role/text/label/css required, got {used}")
        if self.name and not self.role:
            raise ValueError("'name' is only valid together with 'role'")
        return self

    def describe(self) -> str:
        parts = [f"{k}={getattr(self, k)!r}" for k in ("testid", "role", "name", "text", "label", "css")
                 if getattr(self, k)]
        return ", ".join(parts) + (f" [nth={self.nth}]" if self.nth is not None else "")


class Persona(_Strict):
    name: str
    description: str
    goals: list[str] = Field(min_length=1)
    traits: list[str] = Field(default_factory=list)


class SiteProfile(_Strict):
    name: str
    base_url: str
    description: str = ""
    pages: dict[str, str] = Field(
        default_factory=dict, description="Path -> what the page is for."
    )
    known_items: list[str] = Field(default_factory=list, description="Product/entity names.")
    selectors: dict[str, Target] = Field(
        default_factory=dict,
        description="Semantic locators: search_input (required for 'search'), "
        "search_submit (optional), add_to_cart (required for 'add_to_cart').",
    )

    @model_validator(mode="after")
    def _http_url(self) -> SiteProfile:
        if urlparse(self.base_url).scheme not in ("http", "https"):
            raise ValueError("base_url must be http(s)")
        return self

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc


class _Step(_Strict):
    note: str | None = Field(default=None, description="Human-readable intent of this step.")


class Goto(_Step):
    action: Literal["goto"] = "goto"
    path: str = Field(description="Path on the site (e.g. '/cart.html') or absolute URL on the same host.")


class Search(_Step):
    action: Literal["search"] = "search"
    query: str


class Click(_Step):
    action: Literal["click"] = "click"
    target: Target


class Fill(_Step):
    action: Literal["fill"] = "fill"
    target: Target
    value: str


class AddToCart(_Step):
    action: Literal["add_to_cart"] = "add_to_cart"
    target: Target | None = Field(
        default=None, description="Override; defaults to the profile's add_to_cart selector."
    )


class AssertText(_Step):
    action: Literal["assert_text"] = "assert_text"
    text: str
    target: Target | None = Field(default=None, description="Scope; defaults to the whole page.")


class AssertVisible(_Step):
    action: Literal["assert_visible"] = "assert_visible"
    target: Target


class AssertUrl(_Step):
    action: Literal["assert_url"] = "assert_url"
    contains: str


Step = Annotated[
    Union[Goto, Search, Click, Fill, AddToCart, AssertText, AssertVisible, AssertUrl],  # noqa: UP007
    Field(discriminator="action"),
]
STEP_MODELS: tuple[type[_Step], ...] = (
    Goto, Search, Click, Fill, AddToCart, AssertText, AssertVisible, AssertUrl,
)


class Journey(_Strict):
    name: str
    goal: str
    steps: list[Step] = Field(min_length=1)


class JourneyBundle(_Strict):
    """What the LLM must return (the plan body)."""

    journeys: list[Journey] = Field(min_length=1)


class GeneratedBy(_Strict):
    provider: str
    model: str
    mock: bool = False


class Plan(_Strict):
    version: Literal[1] = SCHEMA_VERSION  # type: ignore[assignment]
    persona: str
    site: str
    generated_by: GeneratedBy
    journeys: list[Journey] = Field(min_length=1)


class HealProposal(_Strict):
    target: Target
    rationale: str


def check_plan_against_site(plan: Plan, site: SiteProfile) -> list[str]:
    """Semantic checks that JSON-schema validation cannot express."""
    errs: list[str] = []
    if plan.site != site.name:
        errs.append(f"plan targets site {plan.site!r} but profile is {site.name!r}")
    for ji, j in enumerate(plan.journeys):
        for si, s in enumerate(j.steps):
            where = f"journeys[{ji}].steps[{si}]"
            if isinstance(s, Goto):
                u = urlparse(s.path)
                if u.netloc and u.netloc != site.host:
                    errs.append(f"{where}: goto leaves site host {site.host!r}: {s.path}")
            if isinstance(s, Search) and "search_input" not in site.selectors:
                errs.append(f"{where}: 'search' needs selectors.search_input in the site profile")
            if isinstance(s, AddToCart) and s.target is None and "add_to_cart" not in site.selectors:
                errs.append(f"{where}: 'add_to_cart' needs selectors.add_to_cart or a target")
    return errs


def dump_yaml(model: BaseModel) -> str:
    return yaml.safe_dump(
        model.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True
    )


def _load(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML: {e}") from e


def _load_model(path: Path, cls: type[Any]) -> Any:
    data = _load(path)
    try:
        return cls.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"{path}: {e}") from e


def load_persona(path: Path) -> Persona:
    return _load_model(path, Persona)  # type: ignore[no-any-return]


def load_site(path: Path) -> SiteProfile:
    return _load_model(path, SiteProfile)  # type: ignore[no-any-return]


def load_plan(path: Path) -> Plan:
    return _load_model(path, Plan)  # type: ignore[no-any-return]
