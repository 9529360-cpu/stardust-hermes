"""The `hermes-agent` compatibility skill is a routing hub for Stardust.

`website/` is never packaged, so an installed runtime relies on synced skill
references plus the live Stardust repository for product documentation. These
tests keep that routing honest: every local reference must be reachable and the
catch-all must point at Stardust's authoritative documentation/source rather
than the retired upstream docs domain.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "autonomous-ai-agents" / "hermes-agent"
SKILL_MD = SKILL_DIR / "SKILL.md"
GENERATOR = REPO / "website" / "scripts" / "generate-llms-txt.py"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_every_referenced_file_exists(skill_text):
    """Routing a question to a file that isn't there is a dead end."""
    targets = set(re.findall(r"`((?:references|templates)/[^`]+)`", skill_text))

    assert targets, "the skill's routing table no longer references any files"
    for target in sorted(targets):
        assert (SKILL_DIR / target).exists(), f"SKILL.md routes to missing {target}"


def test_every_reference_is_reachable_from_the_skill(skill_text):
    """An unrouted reference is one the agent will never think to open.

    This is the failure that produced the original complaint: content can exist
    and still be invisible because nothing points at it.
    """
    on_disk = {f"references/{path.name}" for path in (SKILL_DIR / "references").glob("*.md")}
    routed = set(re.findall(r"`(references/[^`]+)`", skill_text))

    assert not (on_disk - routed), (
        f"reference files no reader will ever reach: {sorted(on_disk - routed)} — "
        "add a routing-table row in SKILL.md"
    )


def test_unknown_features_route_to_stardust_docs_source(skill_text):
    """The catch-all must resolve to the current product authority."""
    docs_tree = "https://github.com/9529360-cpu/stardust-hermes/tree/main/website/docs"
    assert docs_tree in skill_text
    assert "hermes-agent.nousresearch.com/docs" not in skill_text


def test_docs_authority_matches_the_generator(skill_text):
    """The skill and llms generator must agree on the owning repository."""
    spec = importlib.util.spec_from_file_location("generate_llms_txt", GENERATOR)
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    assert gen.REPO_URL in skill_text
    assert f"{gen.REPO_URL}/tree/main/website/docs" in skill_text
