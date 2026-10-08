"""The Plan of Action runs at the end of Phase 2 and covers every sub-service.

Pinned here:
  - the three engagement-wide documents render what the plan needs and leave out what it must not,
  - the server's composed keys and the frontend's are the same list,
  - Phase 2's prompt is Phase 1's plus exactly the declared edits, so the two cannot drift,
  - the inputs Phase 2 drops on the server are the ones the frontend drops.

No network and no database: prompts and TypeScript sources are read off disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services import plan_context as pc
from app.services.generation import PHASE2_OVERRIDES, build_prompt
from app.services.generation import _config as stage_config

_ROOT = Path(__file__).resolve().parents[2]
_PROMPTS = _ROOT / "backend" / "assets" / "Prompts"
_CATALOG = (_ROOT / "frontend" / "src" / "data" / "assetCatalog.ts").read_text(encoding="utf-8")
_PHASE2_CATALOG = (_ROOT / "frontend" / "src" / "data" / "phase2Catalog.ts").read_text(encoding="utf-8")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def test_phase1_assets_lists_what_exists_and_names_it_done() -> None:
    page = "<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>Rewritten</h1> copy</body></html>"
    text = pc.render_phase1_assets({"pillar_page": page, "blog": "A blog.", "podcast": "  "})
    assert "treat as done" in text
    assert "## Pillar page" in text and "## Blog" in text
    assert "Podcast" not in text, "a blank document is not a built asset"
    assert "Rewritten" in text
    assert "<h1>" not in text and "color:red" not in text, "an HTML deliverable is reduced to its words"


def test_phase1_assets_is_empty_when_nothing_was_built() -> None:
    assert pc.render_phase1_assets({}) == ""


def test_the_value_ladder_is_looked_up_under_both_keys() -> None:
    assert pc.keys_for("offers") == ("offers", "offer_ladder")
    assert pc.keys_for("blog") == ("blog",)


def test_every_sub_service_is_named_and_what_is_missing_is_listed() -> None:
    meta = pc.SubServiceTrack("Meta Ads", {"icp": "Buyer.", "cro": "Copy.", "pillar_page": "Page."})
    google = pc.SubServiceTrack("Google Ads", {"icp": "Other buyer."})
    text = pc.render_phase2_assets([meta, google])

    assert "(2): Meta Ads, Google Ads" in text
    assert "## Meta Ads" in text and "## Google Ads" in text
    assert "Built and approved: ICP, CRO page copy, Pillar page." in text
    assert "Not yet built: Funnel, Lead magnet" in text


def test_a_sub_service_with_nothing_approved_is_not_reported_as_built() -> None:
    text = pc.render_phase2_assets([pc.SubServiceTrack("Meta Ads", {"icp": "Buyer."}), pc.SubServiceTrack("TikTok")])
    assert "TikTok" not in text
    assert pc.render_phase2_assets([pc.SubServiceTrack("TikTok")]) == ""


def test_excerpts_are_cut_and_say_so() -> None:
    long = "A sentence about the service. " * 200
    cut = pc.excerpt(long, 300)
    assert len(cut) < 400
    assert cut.endswith(pc._EXCERPT_NOTE)
    assert pc.excerpt("Short.", 300) == "Short."


def test_the_phase2_documents_are_bounded_by_the_number_of_sub_services() -> None:
    """Ten sub-services at nine documents each is the largest block in the plan's prompt; keep it
    inside a budget a stage can carry."""
    docs = {a: "Word. " * 500 for a in pc.PHASE2_ASSET_IDS}
    text = pc.render_phase2_assets([pc.SubServiceTrack(f"Sub {i}", docs) for i in range(10)])
    assert len(text) < 90_000


# --------------------------------------------------------------------------------------
# The two sides agree
# --------------------------------------------------------------------------------------


def test_composed_keys_match_the_frontend() -> None:
    block = _CATALOG.split("export const PLAN_COMPOSED_KEYS", 1)[1].split("};", 1)[0]
    assert set(re.findall(r"^\s+(plan_\w+):", block, re.MULTILINE)) == set(pc.COMPOSED_KEYS)


def test_composed_keys_are_declared_server_composed_in_the_frontend() -> None:
    block = _CATALOG.split("export const SERVER_COMPOSED_CONTEXT_KEYS", 1)[1].split("]);", 1)[0]
    assert set(pc.COMPOSED_KEYS) <= set(re.findall(r'"(\w+)"', block))


def test_the_frontend_re_points_the_plan_fields_at_the_composed_keys() -> None:
    delta = _PHASE2_CATALOG.split("  plan_of_action: {", 1)[1].split("\n  funnel_hub_media:", 1)[0]
    pointed = set(re.findall(r'context_key: "(plan_\w+)"', delta))
    assert pointed == set(pc.COMPOSED_KEYS)


def test_phase2_drops_the_same_inputs_on_both_sides() -> None:
    delta = _PHASE2_CATALOG.split("  plan_of_action: {", 1)[1].split("fromComposed", 1)[0]
    frontend = set(re.findall(r'"(\w+)"', delta.split("dropFields:", 1)[1]))
    assert frontend == set(PHASE2_OVERRIDES["plan_of_action"].drop_fields)


def test_asset_ids_the_plan_reads_are_real_stages() -> None:
    from app.services.generation import STAGE_CONFIGS

    assert set(pc.PHASE1_ASSET_IDS) <= set(STAGE_CONFIGS)
    assert set(pc.PHASE2_ASSET_IDS) <= set(STAGE_CONFIGS)
    assert set(pc.PHASE1_ASSET_IDS) | set(pc.PHASE2_ASSET_IDS) <= set(pc.LABELS)


# --------------------------------------------------------------------------------------
# The Phase 2 prompt
# --------------------------------------------------------------------------------------

_BUILT_INPUT = (
    "Phase 2 Sub-Services Already Built (optional): [YOUR ANSWER or leave blank — every sub-service whose "
    "pillar page, funnel, lead magnet or other assets are already built, with what each has, so Phase 2 "
    'marks it complete rather than re-briefing it. If none are built yet, say "none — Phase 2 not started."]\n'
)
_DROPPED_INPUTS = (
    "CRO / Messaging Framework (optional): [YOUR ANSWER or leave blank — paste, attach, or reference the CRO "
    "audit / psychological-layer framework already established, so tone and locked terminology carry through "
    "every asset in this plan.]\n"
    "Existing Funnels / Lead Magnets (optional): [YOUR ANSWER or leave blank — reference any funnel document or "
    "lead magnet already established, so this plan is additive rather than duplicative.]\n"
    "Existing Value Ladder / Offers (optional): [YOUR ANSWER or leave blank — reference any value ladder already "
    "established, so funnel conversion events point at real, already-defined next steps (consultation, audit, "
    "retainer tier) rather than inventing new ones.]\n"
)
_B2_START = "B2) Existing Phase 2 Work"
_B2_END = "C) ICP and CRO Framework"
_STEP3_START = "A sub-service listed under Phase 2 Sub-Services Already Built"
_STEP3_END = "Every sub-service must feel distinct"
_STEP8_ADDED = (
    " Mark every Phase 2 asset listed as already\nbuilt as done in the same way, and schedule only what is still to build."
)


def test_phase2_prompt_is_phase1s_plus_exactly_the_declared_edits() -> None:
    """Reverse the edits and the result must be the Phase 1 prompt byte for byte. An improvement made
    to `Plan-of-Action-Architect-Prompt.md` and not carried across fails here."""
    text = _read(_PROMPTS / "Phase2" / "Plan-of-Action-Architect-Prompt_phase2.md")

    text = text.replace(_BUILT_INPUT, "", 1)
    anchor = "Existing Phase 1 Assets (optional):"
    assert anchor in text
    # The dropped inputs sat between Brand-adjacent lines: put them back in front of the brand input.
    text = text.replace("Brand Design Reference (optional):", _DROPPED_INPUTS + "Brand Design Reference (optional):", 1)

    b2_start = text.index(_B2_START)
    text = text[:b2_start] + text[text.index(_B2_END) :]
    s3_start = text.index(_STEP3_START)
    text = text[:s3_start] + text[text.index(_STEP3_END) :]
    assert _STEP8_ADDED in text
    text = text.replace(_STEP8_ADDED, "", 1)

    assert text == _read(_PROMPTS / "Plan-of-Action-Architect-Prompt.md")


def test_phase2_plan_builds_from_its_own_file_and_renders_the_new_input() -> None:
    cfg = stage_config("plan_of_action", "phase2")
    assert cfg.prompt_file == "Phase2/Plan-of-Action-Architect-Prompt_phase2.md"

    prompt = build_prompt(
        "plan_of_action",
        {"phase_2_sub_services_already_built_optional": "Meta Ads: pillar page built"},
        "phase2",
    )
    inputs = prompt.split("— END OF INPUTS —")[0]
    assert "Phase 2 Sub-Services Already Built (optional)" in inputs
    assert "Meta Ads: pillar page built" in inputs
    for label in ("CRO / Messaging Framework", "Existing Funnels / Lead Magnets", "Existing Value Ladder"):
        assert label not in inputs, "a Phase 2 plan must not ask for one sub-service's document as the client's"


def test_phase1_plan_is_unchanged_in_the_server() -> None:
    """Chats that already hold a Phase 1 plan keep resolving it."""
    inputs = build_prompt("plan_of_action", {}, "phase1").split("— END OF INPUTS —")[0]
    assert "CRO / Messaging Framework" in inputs
