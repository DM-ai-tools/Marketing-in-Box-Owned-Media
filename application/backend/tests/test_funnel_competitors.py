"""The funnel stage compares the market's real funnels.

Every funnel used to print "STEP 2 SKIPPED — no competitor analysis supplied for this stage": the
funnel was the one generation stage with a competitor step in its prompt and no competitor stage to
feed it. `competitor_analysis_funnel` maps each competitor's funnel, is reviewed before the funnel's
intake in both phases, and reaches the prompt as the Competitor Funnel Analysis input.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.seed import DAG_ROWS
from app.services import dependencies
from app.services.competitor import (
    CONFIGS_BY_PHASE,
    GATED_COMPETITOR_BY_MAIN_ASSET_BY_PHASE,
    PREPASS_BY_MAIN_ASSET_BY_PHASE,
    build_competitor_prompt,
    parse_analysis,
    to_prompt_text,
)
from app.services.generation import build_prompt

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
LISTING = json.dumps(
    {
        "competitors": [
            {
                "domain": "rival.com.au",
                "name": "Rival Digital",
                "funnel_entry_url": "https://rival.com.au/free-seo-audit/",
                "verification_confidence": "Verified",
                "funnel_type": "Free audit → strategy call",
                "starting_price": None,
                "offering_summary": "A five-field audit form; the thank-you page books a strategy call.",
                "similarity_score": 0.9,
                "avg_position": None,
                "intersections": None,
            }
        ],
        "notes": "Returned 1 of 10. Free audits dominate; nobody runs a quiz funnel.",
    }
)


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_the_funnel_has_a_reviewed_competitor_stage_in_both_phases(phase):
    cfg = CONFIGS_BY_PHASE[phase]["competitor_analysis_funnel"]
    assert cfg.paired_main_asset_id == "funnel"
    assert GATED_COMPETITOR_BY_MAIN_ASSET_BY_PHASE[phase]["funnel"] is cfg
    # Reviewed on its own card, so never also run invisibly inside generation (billed twice).
    assert "funnel" not in PREPASS_BY_MAIN_ASSET_BY_PHASE[phase]


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_the_search_is_for_this_service_s_funnels(phase):
    text = build_competitor_prompt(
        "competitor_analysis_funnel",
        {"target_url": "https://clicktrends.com.au/", "niche": "Digital marketing", "location": "Melbourne", "service": "SEO"},
        phase=phase,
    )
    assert "service: SEO" in text or "sub-service: SEO" in text
    assert "{" + "SERVICE}" not in text and "funnel_type" in text


def test_a_listing_is_parsed_into_funnel_type_and_entry_page():
    parsed = parse_analysis("competitor_analysis_funnel", LISTING)
    row = parsed.competitors[0]
    assert row.page_url == "https://rival.com.au/free-seo-audit/"
    assert row.category == "Free audit → strategy call"


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_the_approved_listing_reaches_the_funnel_prompt_and_step_2_runs(phase):
    listing = to_prompt_text(parse_analysis("competitor_analysis_funnel", LISTING), "https://clicktrends.com.au/")
    prompt = build_prompt("funnel", {"competitor_funnel_analysis": listing}, phase=phase)

    inputs = prompt.split("— END OF INPUTS —")[0]
    assert "Competitor Funnel Analysis:" in inputs and "Rival Digital (rival.com.au)" in inputs
    assert "Type / focus: Free audit → strategy call" in inputs
    # The prompt no longer tells the model a competitor analysis is never an input here.
    assert "is not an input to this stage" not in prompt
    assert "Competitor Funnel Analysis input" in prompt


def test_the_target_field_is_the_funnel_schema_s_field_and_the_catalog_s():
    cfg = CONFIGS_BY_PHASE["phase1"]["competitor_analysis_funnel"]
    schema = json.loads((BACKEND / "schemas" / "drafts" / "funnel.json").read_text(encoding="utf-8"))
    field = next(f for f in schema["fields"] if f["field_id"] == cfg.target_field_id)
    assert field["context_key"] == "competitor_analysis_funnel"
    catalog = (FRONTEND / "src" / "data" / "assetCatalog.ts").read_text(encoding="utf-8")
    assert f'ctx("{cfg.target_field_id}", "Competitor Funnel Analysis", "competitor_analysis_funnel"' in catalog


def test_the_ui_gates_it_before_the_funnel_intake_in_both_phases():
    data = (FRONTEND / "src" / "pipeline" / "pipelineData.ts").read_text(encoding="utf-8")
    for name in ("GATED_COMPETITOR_MAIN_ASSET_IDS", "PHASE2_GATED_COMPETITOR_MAIN_ASSET_IDS"):
        block = data.split(f"export const {name} = new Set<string>([", 1)[1].split("]);", 1)[0]
        assert '"funnel"' in block, name


def test_the_stage_is_seeded_and_the_funnel_depends_on_it():
    rows = {row["asset_id"]: row for row in DAG_ROWS}
    assert "competitor_analysis_funnel" in rows
    assert "competitor_analysis_funnel" in rows["funnel"]["depends_on"]
    assert dependencies.PREPASS["funnel"] == "competitor_analysis_funnel"
