"""The Plan of Action is Phase 1's last stage: a delivery record of Phase 1, and a roadmap.

Pinned here:
  - the Phase 1 document reports each deliverable as measured facts plus an excerpt, never invents
    one, and carries the keyword clusters' measured volumes,
  - the server's composed keys and the frontend's are the same list,
  - there is no Phase 2 plan stage on either side any more,
  - the plan's inputs are wired the way the prompt describes them (composed Phase 1 document,
    competitor listings gathered from the run, regulated flag from CRO's settings).

No network and no database: prompts and TypeScript sources are read off disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services import plan_context as pc
from app.services.generation import PHASE2_OVERRIDES, PHASE2_STAGE_CONFIGS, build_prompt

_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS = _ROOT / "backend" / "assets" / "Prompts"
_CATALOG = (_ROOT / "frontend" / "src" / "data" / "assetCatalog.ts").read_text(encoding="utf-8")
_PHASE2_CATALOG = (_ROOT / "frontend" / "src" / "data" / "phase2Catalog.ts").read_text(encoding="utf-8")
_PIPELINE_DATA = (_ROOT / "frontend" / "src" / "pipeline" / "pipelineData.ts").read_text(encoding="utf-8")


def _plan_block() -> str:
    return _CATALOG.split('asset_id: "plan_of_action"', 1)[1].split("// ----------------", 1)[0]


# --------------------------------------------------------------------------------------
# The Phase 1 document
# --------------------------------------------------------------------------------------


def test_phase1_assets_lists_what_exists_and_names_it_done() -> None:
    page = "<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>Rewritten</h1> copy</body></html>"
    text = pc.render_phase1_assets({"pillar_page": page, "blog": "A blog.", "podcast": "  "})
    assert "treat as done" in text
    assert "## Pillar Page Design" in text and "## Blog Post" in text
    assert "## Podcast" not in text, "a blank document is not a built asset"
    assert "Not built in Phase 1:" in text and "Podcast Episode" in text.split("Not built in Phase 1:", 1)[1]
    assert "Rewritten" in text
    assert "<h1>" not in text and "color:red" not in text, "an HTML deliverable is reduced to its words"


def test_phase1_assets_is_empty_when_nothing_was_built() -> None:
    assert pc.render_phase1_assets({}) == ""


def test_each_deliverable_leads_with_its_measured_facts() -> None:
    blog = "# How Much Should You Spend on Social Ads?\n\n## Budgets\n\nText.\n\n## Benchmarks\n\nMore text here."
    cro = "# CRO Audit\n\n**Score: 58/100**\n\n## Layer 1\n\nx"
    sms = "## Message 1\nHi\n## Message 2\nHi\n## Message 6\nBye"
    text = pc.render_phase1_assets({"blog": blog, "cro": cro, "sms_sequence": sms})
    assert "- Title: How Much Should You Spend on Social Ads?" in text
    assert "- Sections: Budgets | Benchmarks" in text
    assert "- Page score stated in the audit: 58/100" in text
    assert "- Messages in the sequence: 6" in text
    assert "- Length: about" in text


def test_the_value_ladder_reports_its_offers_from_the_parser() -> None:
    ladder = (
        "## Rung 1 — Entry\n\n"
        '1. **[Audit] "Free Social Audit"**\n   - Price: $0\n   - Estimated Value: $500\n   - Ascends To: Sprint\n\n'
        '2. **[Sprint] "30-Day Sprint"**\n   - Price: $1,500\n   - Estimated Value: $3,000\n   - Ascends To: Retainer\n'
    )
    facts = pc.asset_facts("offers", ladder)
    offers = next((f for f in facts if f.startswith("Offers on the ladder")), "")
    assert "Free Social Audit" in offers and "30-Day Sprint" in offers


def test_no_fact_is_invented_for_a_document_that_does_not_state_it() -> None:
    facts = pc.asset_facts("cro", "# Audit\n\nNo score here.")
    assert not any("score" in f.lower() for f in facts)
    assert not any("Messages" in f for f in pc.asset_facts("sms_sequence", "Just text."))


def test_keyword_clusters_close_the_document_with_measured_volumes() -> None:
    rows = [
        {"keyword": "social media agency", "volume": 2400},
        {"keyword": "meta ads agency", "volume": 880},
        {"keyword": "unmeasured", "volume": None},
    ]
    text = pc.render_phase1_assets({"blog": "# A blog\n\ntext"}, rows)
    block = text.split("## Keyword Clusters (measured search volume)", 1)[1]
    assert "- social media agency — 2,400/mo" in block
    assert block.index("social media agency") < block.index("meta ads agency")
    assert "unmeasured" not in block, "a keyword with no volume is not reported as measured"


def test_the_value_ladder_is_looked_up_under_both_keys() -> None:
    assert pc.keys_for("offers") == ("offers", "offer_ladder")
    assert pc.keys_for("blog") == ("blog",)


def test_excerpts_are_cut_and_say_so() -> None:
    long = "A sentence about the service. " * 200
    cut = pc.excerpt(long, 300)
    assert len(cut) < 400
    assert cut.endswith(pc._EXCERPT_NOTE)
    assert pc.excerpt("Short.", 300) == "Short."


def test_the_phase1_document_is_bounded() -> None:
    docs = {a: "# Title\n\n## Section\n\n" + "Word. " * 5000 for a in pc.PHASE1_ASSET_IDS}
    assert len(pc.render_phase1_assets(docs)) < 40_000


# --------------------------------------------------------------------------------------
# The two sides agree
# --------------------------------------------------------------------------------------


def test_composed_keys_match_the_frontend() -> None:
    block = _CATALOG.split("export const PLAN_COMPOSED_KEYS", 1)[1].split("};", 1)[0]
    assert set(re.findall(r"^\s+(plan_\w+):", block, re.MULTILINE)) == set(pc.COMPOSED_KEYS)


def test_composed_keys_are_declared_server_composed_in_the_frontend() -> None:
    block = _CATALOG.split("export const SERVER_COMPOSED_CONTEXT_KEYS", 1)[1].split("]);", 1)[0]
    assert set(pc.COMPOSED_KEYS) <= set(re.findall(r'"(\w+)"', block))


def test_the_phase1_plan_reads_the_composed_document() -> None:
    block = _plan_block()
    phase1 = block.split('"existing_phase_1_assets_optional"', 1)[1].split("},", 1)[0]
    assert 'context_key: "plan_phase1_assets"' in phase1


def test_the_competitor_input_gathers_the_listings_discovery_reads() -> None:
    from app.services.sub_service_discovery import COMPETITOR_LISTING_KEYS

    keys = _CATALOG.split("const PLAN_COMPETITOR_KEYS = [", 1)[1].split("];", 1)[0]
    assert tuple(re.findall(r'"(\w+)"', keys)) == COMPETITOR_LISTING_KEYS
    competitor = _plan_block().split('"competitor_list"', 1)[1].split("},", 2)
    assert "context_keys: PLAN_COMPETITOR_KEYS" in "".join(competitor[:2])


def test_the_regulated_flag_is_read_from_cros_settings() -> None:
    block = _plan_block().split('bool("regulated_field_flag"', 1)[1].split("},", 1)[0]
    assert 'context_key: "cro_client_settings"' in block
    assert 'sub_key: "regulated_field_flag"' in block


def test_asset_ids_the_plan_reads_are_real_stages() -> None:
    from app.services.generation import STAGE_CONFIGS

    assert set(pc.PHASE1_ASSET_IDS) <= set(STAGE_CONFIGS)
    assert set(pc.PHASE2_ASSET_IDS) <= set(STAGE_CONFIGS)
    assert set(pc.PHASE1_ASSET_IDS) | set(pc.PHASE2_ASSET_IDS) <= set(pc.LABELS)


# --------------------------------------------------------------------------------------
# There is one plan, and it is Phase 1's
# --------------------------------------------------------------------------------------


def test_there_is_no_phase2_plan_on_the_server() -> None:
    assert "plan_of_action" not in PHASE2_OVERRIDES
    assert "plan_of_action" not in PHASE2_STAGE_CONFIGS
    assert not (_PROMPTS / "Phase2" / "Plan-of-Action-Architect-Prompt_phase2.md").exists()


def test_there_is_no_phase2_plan_in_the_frontend() -> None:
    phase2_ids = _PIPELINE_DATA.split("const PHASE2_ASSET_IDS = [", 1)[1].split("] as const;", 1)[0]
    assert '"plan_of_action"' not in phase2_ids
    assert "DEFERRED_TO_PHASE2" not in _PIPELINE_DATA
    assert "plan_of_action:" not in _PHASE2_CATALOG
    assert "plan_phase2_assets" not in _CATALOG


def test_the_plan_inputs_render_in_phase1() -> None:
    inputs = build_prompt("plan_of_action", {}, "phase1").split("— END OF INPUTS —")[0]
    for label in ("Existing Phase 1 Assets", "Sub-Services to Expand Into (Phase 2)", "CRO / Messaging Framework"):
        assert label in inputs
    assert "Phase 2 Sub-Services Already Built" not in inputs


def test_the_prompt_asks_for_status_url_priority_and_month_per_sub_service() -> None:
    text = (_PROMPTS / "Plan-of-Action-Architect-Prompt.md").read_text(encoding="utf-8")
    step3 = text.split("# STEP 3 —", 1)[1].split("# STEP 4 —", 1)[0]
    for line in ("Status: EXISTING PAGE — ENHANCE", "Proposed URL:", "Priority: [rank from Step 7] | Month:"):
        assert line in step3, line
    assert "Email is\nnot a separate asset" in step3, "email folds into Funnel and Lead Magnet"
    step7 = text.split("# STEP 7 —", 1)[1].split("# STEP 8 —", 1)[0]
    assert "never scheduled after" in step7, "the ranking sets the build order"
