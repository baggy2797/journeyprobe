from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from journeyprobe.models import Persona, SiteProfile, load_persona, load_site
from journeyprobe.shop import serve_shop

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def shop_url() -> Iterator[str]:
    with serve_shop() as url:
        yield url


@pytest.fixture
def persona() -> Persona:
    return load_persona(EXAMPLES / "personas" / "maya.yaml")


@pytest.fixture
def site() -> SiteProfile:
    return load_site(EXAMPLES / "sites" / "fixture-shop.yaml")


@pytest.fixture
def recorded() -> dict[str, Any]:
    return json.loads((DATA / "recorded_plan_response.json").read_text())  # type: ignore[no-any-return]


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """A writable copy of the examples."""
    dst = tmp_path / "examples"
    shutil.copytree(EXAMPLES, dst)
    return dst
