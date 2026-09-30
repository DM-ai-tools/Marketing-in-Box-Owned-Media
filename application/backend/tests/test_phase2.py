"""Tests for the Phase 2 pipeline's prompt wiring.

Phase 2 is expressed as a *delta* over Phase 1 rather than as a second set of prompt-and-schema
tables (see the Phase 2 section of `app/services/generation.py`). That is the right trade — one
field registry per asset cannot drift from itself — but it moves the risk somewhere new: a delta is
silent when it is wrong. A dropped field that no longer exists, a relabel whose field_id was renamed,
or a prompt file moved on disk all still *build a prompt*; they just build the wrong one, and the
first sign of it is a finished deliverable that answered the wrong question.

So what is pinned here is the delta actually landing: every Phase 2 stage resolves to a real file,
renders an INPUTS block, drops exactly what its prompt file has no input for, and relabels exactly
what its prompt file words differently. No network — nothing here calls Anthropic.
"""

from __future__ import annotations

import pytest

from app.services.competitor import (
    CONFIGS_BY_PHASE as COMPETITOR_CONFIGS_BY_PHASE,
    GATED_COMPETITOR_BY_MAIN_ASSET_BY_PHASE,
    PHASE2_COMPETITOR_CONFIGS,
    PREPASS_BY_MAIN_ASSET_BY_PHASE,
    build_competitor_prompt,
    resolve_inputs,
)
from app.services.competitor import _config as competitor_config
from app.services.generation import (
    PHASE2_OVERRIDES,
    PHASE2_STAGE_CONFIGS,
    STAGE_CONFIGS,
    UnknownStageError,
    _load_schema_fields,
    build_prompt,
    has_stage,
)
from app.services.generation import _config as stage_config

# The Phase 2 running order, as the frontend's `pipeline/pipelineData.ts` declares it. Duplicated
# here rather than imported (it lives in TypeScript) — which is exactly why it is worth asserting:
# the two must agree or a stage the UI runs has no prompt behind it.
PHASE2_ASSET_IDS = [
    "icp",
    "cro",
    "pillar_page",
    "funnel",
    "lead_magnet",
    "blog",
    "sms_sequence",
    "content_marketing_strategy",
    "funnel_hub_media",
]


def _inputs_block(prompt: str) -> str:
    """Just the reproduced INPUTS block, so a label assertion cannot pass on a match in the body."""
    head, _, _ = prompt.partition("— END OF INPUTS —")
    return head


# --------------------------------------------------------------------------------------
# Stage coverage
# --------------------------------------------------------------------------------------


def test_phase2_covers_exactly_the_ui_stage_list():
    assert set(PHASE2_STAGE_CONFIGS) == set(PHASE2_ASSET_IDS)


def test_every_phase2_stage_is_also_a_phase1_stage():
    """Phase 2 overrides Phase 1 configs, so an id Phase 1 doesn't have cannot resolve at all."""
    assert set(PHASE2_OVERRIDES) <= set(STAGE_CONFIGS)


def test_has_stage_is_phase_specific():
    assert has_stage("blog", "phase2")
    assert has_stage("icp", "phase1")
    # Phase 2 builds its own ICP for the sub-service (see tests/test_phase2_icp.py).
    assert has_stage("icp", "phase2")
    assert not has_stage("offers", "phase2")


def test_unknown_phase2_stage_names_the_phase():
    with pytest.raises(UnknownStageError) as excinfo:
        stage_config("offers", "phase2")
    assert "phase2" in str(excinfo.value)


# --------------------------------------------------------------------------------------
# Prompt files and rendered INPUTS
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("asset_id", PHASE2_ASSET_IDS)
def test_phase2_prompt_builds_from_its_own_file(asset_id):
    cfg = stage_config(asset_id, "phase2")
    assert cfg.prompt_file.startswith("Phase2/")

    prompt = build_prompt(asset_id, {}, "phase2")
    assert "— INPUTS (fill in before submitting) —" in prompt
    assert "— END OF INPUTS —" in prompt
    # The body has to survive the marker splice, not just the INPUTS block get built.
    assert len(prompt.split("— END OF INPUTS —")[1].strip()) > 1000


@pytest.mark.parametrize("asset_id", PHASE2_ASSET_IDS)
def test_phase1_and_phase2_read_different_prompt_files(asset_id):
    assert stage_config(asset_id, "phase1").prompt_file != stage_config(asset_id, "phase2").prompt_file


@pytest.mark.parametrize("asset_id", PHASE2_ASSET_IDS)
def test_every_answer_reaches_the_rendered_inputs(asset_id):
    """Every field Phase 2 asks for must appear in the block, or the answer is collected and lost."""
    fields = _load_schema_fields(stage_config(asset_id, "phase2"))
    answers = {field_id: f"value-for-{field_id}" for field_id, _ in fields}

    block = _inputs_block(build_prompt(asset_id, answers, "phase2"))
    for field_id, label in fields:
        assert f"value-for-{field_id}" in block, f"{asset_id}.{field_id} never reached the prompt"
        assert label in block


# --------------------------------------------------------------------------------------
# The deltas themselves
# --------------------------------------------------------------------------------------


def test_pillar_page_drops_the_search_and_benchmark_block():
    """Phase 2's pillar page prompt is the standalone v1.0 design file: no keyword or competitor
    inputs exist in it, so asking for them would collect answers with nowhere to go."""
    dropped = {
        "primary_keyword_head_term",
        "secondary_cluster_terms_optional",
        "internal_cluster_pages_to_link_optional",
        "competitor_analysis_pillar_page",
        "cro_locked_sections",
    }
    phase1 = {field_id for field_id, _ in _load_schema_fields(stage_config("pillar_page", "phase1"))}
    phase2 = {field_id for field_id, _ in _load_schema_fields(stage_config("pillar_page", "phase2"))}

    assert dropped <= phase1
    assert phase1 - phase2 == dropped


@pytest.mark.parametrize("asset_id", PHASE2_ASSET_IDS)
def test_dropped_fields_exist_in_the_registry(asset_id):
    """A drop naming a field_id that no longer exists is a no-op that reads as deliberate."""
    override = PHASE2_OVERRIDES[asset_id]
    registry = {field_id for field_id, _ in _load_schema_fields(stage_config(asset_id, "phase1"))}
    assert override.drop_fields <= registry


@pytest.mark.parametrize("asset_id", PHASE2_ASSET_IDS)
def test_relabelled_fields_exist_and_are_rendered(asset_id):
    override = PHASE2_OVERRIDES[asset_id]
    if not override.label_overrides:
        pytest.skip(f"{asset_id} has no Phase 2 relabels")

    rendered = dict(_load_schema_fields(stage_config(asset_id, "phase2")))
    block = _inputs_block(build_prompt(asset_id, {}, "phase2"))

    for field_id, label in override.label_overrides:
        assert field_id in rendered, f"{asset_id}: relabelled a field it does not have"
        assert rendered[field_id] == label
        assert label in block


@pytest.mark.parametrize(
    ("asset_id", "field_id", "expected"),
    [
        ("funnel", "target_service_if_different_from_pillar_page", "Target Sub-Service (if different from pillar page)"),
        (
            "content_marketing_strategy",
            "primary_service_pillar_page_being_supported",
            "Primary Sub-Service / Pillar Page Being Supported",
        ),
        (
            "funnel_hub_media",
            "service_or_product_line_being_funnel_mapped",
            "sub-service or product line being funnel-mapped",
        ),
    ],
)
def test_relabels_match_the_prompt_files_own_wording(asset_id, field_id, expected):
    """The label is the variable name the master prompt body refers to. Handing a Phase 2 file
    Phase 1's wording is how a prompt ends up designing for the headline service."""
    rendered = dict(_load_schema_fields(stage_config(asset_id, "phase2")))
    assert rendered[field_id] == expected
    # And the Phase 2 file really does word it that way.
    from app.services.generation import _PROMPTS_DIR

    source = (_PROMPTS_DIR / stage_config(asset_id, "phase2").prompt_file).read_text(encoding="utf-8")
    assert expected in source


def test_phase1_configs_carry_no_delta():
    """The delta fields exist for Phase 2. A Phase 1 config with one set would silently change the
    prompt that has been in production."""
    for cfg in STAGE_CONFIGS.values():
        assert cfg.drop_fields == frozenset()
        assert cfg.label_overrides == ()


# --------------------------------------------------------------------------------------
# Competitor stages
# --------------------------------------------------------------------------------------


def test_phase2_has_no_invisible_competitor_prepass():
    """All three Phase 2 competitor stages are reviewed before the stage they feed, so folding one
    into a generation call as well would run — and bill — the same search twice."""
    assert PREPASS_BY_MAIN_ASSET_BY_PHASE["phase2"] == {}
    assert set(GATED_COMPETITOR_BY_MAIN_ASSET_BY_PHASE["phase2"]) == {
        "cro",
        "lead_magnet",
        "blog",
        "content_marketing_strategy",
    }


@pytest.mark.parametrize("asset_id", sorted(PHASE2_COMPETITOR_CONFIGS))
def test_phase2_competitor_prompt_substitutes_every_placeholder(asset_id):
    cfg = competitor_config(asset_id, "phase2")
    inputs = resolve_inputs(
        cfg,
        main_answers={},
        client_profile={
            "website_url": "https://example.com/",
            "industry": "Digital Marketing",
            "region": "Melbourne VIC Australia",
            "sub_service": "Google Ads",
        },
    )
    text = build_competitor_prompt(asset_id, inputs, phase="phase2")

    for placeholder in ("{TARGET_URL}", "{NICHE}", "{LOCATION}", "{SERVICE}"):
        assert placeholder not in text
    assert "Google Ads" in text
    assert "https://example.com/" in text


@pytest.mark.parametrize("asset_id", sorted(PHASE2_COMPETITOR_CONFIGS))
def test_phase2_competitor_service_comes_from_the_sub_service(asset_id):
    """The whole point of the phase split: the search is run on the sub-service, not on the client's
    headline service. No Phase 2 stage's intake asks for it, so it must resolve from the profile."""
    cfg = competitor_config(asset_id, "phase2")
    assert cfg.source_fields["service"] == ()

    inputs = resolve_inputs(cfg, main_answers={}, client_profile={"sub_service": "LinkedIn"})
    assert inputs["service"] == "LinkedIn"


@pytest.mark.parametrize("asset_id", sorted(PHASE2_COMPETITOR_CONFIGS))
def test_phase2_competitor_reads_its_own_file(asset_id):
    phase1 = competitor_config(asset_id, "phase1").prompt_file
    phase2 = competitor_config(asset_id, "phase2").prompt_file
    assert phase1 != phase2
    assert "phase2" in phase2


def test_phase2_competitor_stages_reuse_the_phase1_output_contract():
    """Same schema file, so the same parser and the same review card serve both phases."""
    for asset_id, cfg in PHASE2_COMPETITOR_CONFIGS.items():
        assert cfg.schema_file == COMPETITOR_CONFIGS_BY_PHASE["phase1"][asset_id].schema_file
        assert cfg.target_field_id == COMPETITOR_CONFIGS_BY_PHASE["phase1"][asset_id].target_field_id


def test_competitor_target_fields_are_real_phase2_inputs():
    """Each competitor stage writes its output into a named field of the paired stage's INPUTS. If
    that field is one Phase 2 dropped, the analysis is produced, approved and then discarded."""
    for cfg in PHASE2_COMPETITOR_CONFIGS.values():
        fields = {f for f, _ in _load_schema_fields(stage_config(cfg.paired_main_asset_id, "phase2"))}
        assert cfg.target_field_id in fields, f"{cfg.asset_id} writes into a field Phase 2 does not ask"


# --------------------------------------------------------------------------------------
# Competitor briefings
# --------------------------------------------------------------------------------------


def test_briefings_exist_for_exactly_the_two_stages_that_need_one():
    """Mirrors `COMPETITOR_BRIEFING_STAGES` in the frontend's `pipeline/pipelineData.ts`. A stage
    listed there and not here asks for a briefing and gets a 404 where the operator expects one."""
    from app.services import insights

    assert insights.has_briefing("blog")
    assert insights.has_briefing("content_marketing_strategy")
    for asset_id in ("pillar_page", "funnel", "lead_magnet", "sms_sequence", "funnel_hub_media", "icp"):
        assert not insights.has_briefing(asset_id)


@pytest.mark.parametrize("asset_id", ["blog", "content_marketing_strategy"])
def test_briefing_prompt_carries_the_listing_and_the_honesty_rules(asset_id):
    from app.services import insights

    listing = '{"competitors": [{"domain": "example.com.au"}], "notes": "Returned 1 of 10."}'
    prompt = insights.build_briefing_prompt(asset_id, listing, "Google Ads")

    # The listing has to arrive whole — a briefing over a truncated one invents the rest.
    assert listing in prompt
    assert "Google Ads" in prompt
    # The rules that keep an inference from being read as a measurement.
    assert "inference" in prompt
    assert "search volume" in prompt


def test_briefing_prompt_survives_a_missing_sub_service():
    """A standalone Phase 2 run has no parent to name the sub-service, and the operator may still
    have skipped it. The briefing is still worth producing."""
    from app.services import insights

    prompt = insights.build_briefing_prompt("blog", "{}", "")
    assert "for the sub-service" not in prompt
