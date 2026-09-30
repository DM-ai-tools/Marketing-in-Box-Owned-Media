"""Phase 2 builds its own ICP, for the sub-service, and never reads the parent run's.

Phase 1's ICP profiles the buyer of the client's headline service ("Social Media Marketing"). The
buyer of one sub-service ("Meta Ads") is often a different person, with a different trigger, budget
line and set of alternatives. So Phase 2 runs an ICP stage of its own at stage 01, and the parent's
ICP and offer ladder stop crossing `source_run_id`.

Three things are pinned here:
  - the Phase 2 prompt is `ICP.md` plus exactly two edits, so it cannot drift from Phase 1's,
  - the sub-service reaches the prompt's INPUTS under a label that says so,
  - `icp`, `offers` and `offer_ladder` never inherit, while client-level keys still do.

No network and no database: prompts are read off disk and the session is faked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.routers import pipeline as pipeline_router
from app.services.dependencies import dependencies_for, transitive_producers
from app.services.generation import CONFIGS_BY_PHASE, build_prompt

_PROMPTS = Path(__file__).resolve().parents[1] / "assets" / "Prompts"

OLD_INPUT = 'Service/Product + Price/Terms: [YOUR ANSWER — or write "not specified (quote required)"]'
NEW_INPUT = 'Sub-Service + Price/Terms: [YOUR ANSWER — or write "not specified (quote required)"]'
SCOPE_HEADING = "SUB-SERVICE SCOPE (Phase 2 — read before anything else below)"
NEXT_HEADING = "CRITICAL CONTEXT RULES — Company Type + Audience Type (ICP Orientation)"


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _phase2() -> str:
    return _read(_PROMPTS / "Phase2" / "ICP_phase2.md")


# --------------------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------------------


def test_phase2_prompt_is_phase1s_plus_exactly_two_edits() -> None:
    """Reverse the two edits and the result must be `ICP.md` byte for byte.

    This is what stops the two files drifting. An improvement made to `ICP.md` and not carried into
    `Phase2/ICP_phase2.md` fails here, and so does any edit to the Phase 2 file outside those two.
    """
    text = _phase2()
    start = text.index(SCOPE_HEADING)
    end = text.index(NEXT_HEADING)
    reverted = (text[:start] + text[end:]).replace(NEW_INPUT, OLD_INPUT)
    assert reverted == _read(_PROMPTS / "ICP.md")


def test_scope_section_sits_before_the_rules_it_governs() -> None:
    text = _phase2()
    assert text.index("— MASTER PROMPT") < text.index(SCOPE_HEADING) < text.index(NEXT_HEADING)


def test_scope_section_says_the_icp_is_built_from_scratch_for_the_sub_service() -> None:
    scope = _phase2().split(SCOPE_HEADING, 1)[1].split(NEXT_HEADING, 1)[0]
    assert "ONE SUB-SERVICE" in scope
    assert "No earlier ICP is an input to it" in scope
    assert "Do not drift up to the headline service" in scope


def test_the_sub_service_reaches_inputs_under_its_own_label() -> None:
    prompt = build_prompt("icp", {"service_product_price_terms": "Meta Ads"}, "phase2")
    inputs = prompt.partition("— END OF INPUTS —")[0]
    assert "Sub-Service + Price/Terms: Meta Ads" in inputs
    assert "Service/Product + Price/Terms" not in inputs


def test_phase1_icp_is_untouched() -> None:
    prompt = build_prompt("icp", {"service_product_price_terms": "Social Media Marketing"}, "phase1")
    assert "Service/Product + Price/Terms: Social Media Marketing" in prompt
    assert SCOPE_HEADING not in prompt


# --------------------------------------------------------------------------------------
# The dependency graph
# --------------------------------------------------------------------------------------


def test_icp_is_phase2s_first_stage_and_its_own_producer() -> None:
    assert "icp" in CONFIGS_BY_PHASE["phase2"]
    for asset_id in ("cro", "funnel", "lead_magnet", "blog", "sms_sequence", "content_marketing_strategy"):
        assert "icp" in transitive_producers(asset_id, "phase2"), asset_id


def test_no_phase2_stage_depends_on_a_stage_phase2_does_not_run() -> None:
    """Everything a Phase 2 stage requires is produced inside Phase 2. Offer Ladder is the one
    optional exception, and it no longer inherits either — those fields ask or go without."""
    stages = set(CONFIGS_BY_PHASE["phase2"])
    for asset_id in stages:
        for dep in dependencies_for(asset_id, "phase2").dependencies:
            if dep.required and dep.producer:
                assert dep.producer in stages, f"{asset_id}.{dep.field_id} requires {dep.producer}"


# --------------------------------------------------------------------------------------
# Inheritance
# --------------------------------------------------------------------------------------

CHILD = uuid.UUID("11111111-1111-1111-1111-111111111111")
PARENT = uuid.UUID("22222222-2222-2222-2222-222222222222")


@dataclass
class _Run:
    source_run_id: uuid.UUID | None


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _Session:
    """The parent holds every key; the child holds none. So anything found was inherited."""

    def __init__(self) -> None:
        self.current: uuid.UUID | None = None

    async def execute(self, stmt: Any) -> _Result:
        run_id = stmt.compile().params["run_id_1"]
        return _Result(object() if run_id == PARENT else None)

    async def get(self, _model: Any, ident: uuid.UUID) -> _Run:
        return _Run(source_run_id=PARENT if ident == CHILD else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["icp", "offers", "offer_ladder"])
async def test_the_parents_audience_documents_never_reach_a_phase2_run(key: str) -> None:
    assert await pipeline_router._latest_context_entry(_Session(), CHILD, key) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["brand_design_tokens", "cro_client_settings", "industry_profile"])
async def test_client_level_documents_still_inherit(key: str) -> None:
    found = await pipeline_router._latest_context_entry(_Session(), CHILD, key)
    assert found is not None and found[1] == PARENT
