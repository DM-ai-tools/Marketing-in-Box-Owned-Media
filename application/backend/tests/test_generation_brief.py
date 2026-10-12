"""The business check is told to the stage before it writes, not only after.

Before this, a draft met the check only once it was finished, so every finding cost a Fix with
Refine: a second generation to fix what the first could have got right. `asset_check.generation_brief`
states the run's facts, the rules `run_rules` enforces and the questions the judge scores, and the
stream puts it in the prompt tail. The check still runs after and still scores the draft.

No network and no model call.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.routers import pipeline as pipeline_router
from app.services import asset_check as A
from app.services.business_facts import BusinessFacts, Offer
from app.services.generation import CONFIGS_BY_PHASE, OUTPUT_STYLE_BLOCK, _prompt_parts

LADDER = [Offer(title="Content Audit", format="Audit", price="$497"), Offer(title="Growth Retainer", price="$2,500")]
REGULATED = {
    "claim_substantiation_tier": "2 PROFESSIONALLY REGULATED",
    "testimonials_before_after_permitted": "NO",
    "words_to_avoid": "cheap, synergy",
    "pricing_disclosure_mode": "B FROM-PRICE",
    "pricing_facts": "From $2,500/month",
}
RUN_ID = "11111111-1111-1111-1111-111111111111"


def _facts(**overrides: Any) -> BusinessFacts:
    base: dict[str, Any] = {"service": "Social Media Marketing", "offers": list(LADDER), "settings": dict(REGULATED)}
    base.update(overrides)
    return BusinessFacts(**base)


@pytest.mark.parametrize(
    ("phase", "asset_id"),
    [(phase, asset_id) for phase, configs in CONFIGS_BY_PHASE.items() for asset_id in configs],
)
def test_every_checked_stage_gets_a_brief(phase: str, asset_id: str) -> None:
    brief = A.generation_brief(asset_id, phase, _facts())
    if not A.check_applies(asset_id, phase):
        assert brief == ""
        return
    assert brief.startswith(f"===== BEGIN {A.BRIEF_BLOCK} =====")
    # Every check the stage is scored on is named, so the writer and the judge read the same list.
    for check in A.CHECKS_BY_ASSET[asset_id]:
        assert check.replace("_", " ").capitalize() in brief, (asset_id, check)


def test_phase2_icp_is_not_briefed() -> None:
    assert A.generation_brief("icp", "phase2", _facts()) == ""


def test_the_rules_come_from_the_facts() -> None:
    brief = A.generation_brief("lead_magnet", "phase1", _facts())
    assert "Content Audit" in brief and "Growth Retainer" in brief  # the ladder, via summary()
    assert '"cheap", "synergy"' in brief
    assert "No testimonials, star ratings" in brief and "not permitted" in brief
    assert "No absolute claims" in brief and "2 PROFESSIONALLY REGULATED" in brief
    assert "Name the offer this asset ascends to" in brief
    assert "Use only prices and values that appear on the offer ladder" in brief


def test_a_rule_that_would_not_run_is_not_stated() -> None:
    brief = A.generation_brief("blog", "phase1", BusinessFacts(service="SEO"))
    assert "Never use these words" not in brief
    assert "No testimonials" not in brief
    assert "No absolute claims" not in brief
    assert "Name the offer" not in brief


def test_forbidden_prices_replace_the_allowed_list() -> None:
    settings = {**REGULATED, "pricing_disclosure_mode": "D NO PRICING"}
    brief = A.generation_brief("cro", "phase1", _facts(settings=settings))
    assert "State no price" in brief and "D NO PRICING" in brief
    assert "Use only prices" not in brief


def test_the_brief_sits_in_the_tail_before_inputs() -> None:
    brief = A.generation_brief("funnel", "phase1", _facts())
    library, tail = _prompt_parts("funnel", {}, "phase1", business_brief=brief)
    assert A.BRIEF_BLOCK not in library  # per run: must not break the cached prefix
    assert tail.index(f"===== END {OUTPUT_STYLE_BLOCK} =====") < tail.index(f"BEGIN {A.BRIEF_BLOCK}")
    assert tail.index(f"END {A.BRIEF_BLOCK}") < tail.index("— INPUTS")


@pytest.mark.asyncio
async def test_the_stream_hands_the_brief_to_the_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def passthrough(_asset_id: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
        return answers, None

    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    async def facts(*_a: Any, **_k: Any) -> BusinessFacts:
        return _facts()

    async def fake_stream(asset_id: str, answers: dict[str, str], phase: str, **kwargs: Any):
        captured.update(kwargs)
        yield "hello"

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", nothing)
    monkeypatch.setattr(pipeline_router, "_run_industry", nothing)
    monkeypatch.setattr(pipeline_router, "_business_facts", facts)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_stream)

    events = [e async for e in pipeline_router._generation_sse_stream("funnel", {}, {}, "phase1", None, RUN_ID)]
    assert any('"delta"' in e for e in events)
    assert captured["business_brief"].startswith(f"===== BEGIN {A.BRIEF_BLOCK} =====")
    assert '"cheap", "synergy"' in captured["business_brief"]


@pytest.mark.asyncio
async def test_a_failed_facts_read_never_blocks_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def passthrough(_asset_id: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
        return answers, None

    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    async def broken(*_a: Any, **_k: Any) -> BusinessFacts:
        raise RuntimeError("database down")

    async def fake_stream(asset_id: str, answers: dict[str, str], phase: str, **kwargs: Any):
        captured.update(kwargs)
        yield "hello"

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", nothing)
    monkeypatch.setattr(pipeline_router, "_run_industry", nothing)
    monkeypatch.setattr(pipeline_router, "_business_facts", broken)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_stream)

    events = [e async for e in pipeline_router._generation_sse_stream("funnel", {}, {}, "phase1", None, RUN_ID)]
    assert any('"delta"' in e for e in events)
    assert captured["business_brief"] == ""
