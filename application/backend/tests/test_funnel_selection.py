"""Funnel Hub & Media selects its funnel types; it does not map all eighteen.

Both prompts used to call the eighteen names a "fixed taxonomy" and say "all 18" in every phase, so
a regulated lender got a Cart Funnel, a Free-Plus-Shipping book and a Summit written for it. The
names are now a menu: Step 0 keeps the types that fit how this business sells this service (or
sub-service), the ICP's buyer can enter, and the reference material can ground — and publishes
the rest as "Not used". Phases 1-3 then run only on the kept set.

There is no byte-for-byte test tying the two files together, so this one reads both. It also reads
the body `build_prompt` actually sends: the stage's marker strips everything above it, and Step 0
sitting above the marker would reach no model at all.

No network and no model call: the prompts are read off disk.
"""

from __future__ import annotations

import re

import pytest

from app.services.generation import _PROMPTS_DIR, build_prompt
from app.services.generation import _config as stage_config

PHASES = ("phase1", "phase2")

MENU = (
    "Lead Magnet / Squeeze Page", "Reverse Squeeze Page", "Survey Funnel", "Tripwire / SLO Funnel",
    "Free-Plus-Shipping / Book Funnel", "Cart Funnel", "VSL / Sales Letter Funnel",
    "Product Launch Funnel", "Challenge Funnel", "Webinar Funnel", "Summit Funnel",
    "Application Funnel", "Membership Funnel", "High Ticket Funnel", "Consultation Funnel",
    "Free Trial Funnel", "Evergreen Funnel", "Quiz Funnel",
)

#: Wording that makes the full menu a required output. Any of these back means a business is once
#: again handed funnels it cannot sell through.
_ALL_EIGHTEEN = re.compile(
    r"all\s+(?:18|eighteen)\b"
    r"|every\s+funnel\s+type"
    r"|same\s+(?:18|eighteen)\b"
    r"|(?:18|eighteen)\s+canonical"
    r"|fixed\s+taxonomy",
    re.IGNORECASE,
)


def _prompt(phase: str) -> str:
    return (_PROMPTS_DIR / stage_config("funnel_hub_media", phase).prompt_file).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


@pytest.mark.parametrize("phase", PHASES)
def test_no_phase_requires_all_eighteen(phase):
    hits = _ALL_EIGHTEEN.findall(_prompt(phase))
    assert not hits, f"{phase} still requires the full menu: {hits}"


@pytest.mark.parametrize("phase", PHASES)
def test_selection_step_precedes_phase_1_and_carries_the_menu(phase):
    text = _prompt(phase)
    assert "## STEP 0 — FUNNEL SELECTION" in text
    assert text.index("## STEP 0 — FUNNEL SELECTION") < text.index("## PHASE 1 —")
    step0 = _section(text, "## STEP 0 — FUNNEL SELECTION")
    assert "menu, not a required output" in step0
    for name in MENU:
        assert name in step0, f"{phase}: {name!r} missing from the menu"
    # The three keep tests, and the inputs they are judged against.
    for needle in ("ICP", "Existing Funnel Document", "offer ladder", "industry voice", "reference material"):
        assert needle in step0, f"{phase}: Step 0 no longer reads {needle!r}"
    assert "Do not refill the set" in step0


@pytest.mark.parametrize("phase", PHASES)
def test_kept_and_not_used_lists_are_required(phase):
    step0 = _section(_prompt(phase), "## STEP 0 — FUNNEL SELECTION")
    assert "**Kept:**" in step0
    assert "**Not used:**" in step0
    assert "names the input" in step0 or "naming the input" in step0


@pytest.mark.parametrize("phase", PHASES)
def test_every_phase_runs_on_the_kept_set(phase):
    text = _prompt(phase)
    for heading in ("## PHASE 1 —", "## PHASE 2 —", "## PHASE 3 —"):
        assert "every funnel kept in the selection" in _section(text, heading), heading
    assert "omitted" in _section(text, "## PHASE 1 —")  # empty media categories


@pytest.mark.parametrize("phase", PHASES)
def test_no_subset_is_hardcoded_per_industry(phase):
    # The prompt may name industries as examples of *different* subsets, never assign one.
    step0 = _section(_prompt(phase), "## STEP 0 — FUNNEL SELECTION")
    assert "never from a default list" in step0


@pytest.mark.parametrize("phase", PHASES)
def test_the_selection_step_reaches_the_model(phase):
    # `marker_override` strips everything above it. Step 0 and its menu must survive that.
    prompt = build_prompt("funnel_hub_media", {}, phase)
    assert "STEP 0 — FUNNEL SELECTION" in prompt
    assert "Quiz Funnel" in prompt
    assert "**Not used:**" in prompt
    assert prompt.index("STEP 0 — FUNNEL SELECTION") < prompt.index("PHASE 1 —")


def test_phase2_says_sub_service_where_phase1_says_service():
    p1, p2 = _prompt("phase1"), _prompt("phase2")
    pairs = (
        ("how this business sells this service —", "how this business sells this sub-service —"),
        ("the service or product line being funnel-mapped,", "the sub-service or product line being funnel-mapped,"),
        ("offer for [Service / product line]", "offer for [sub-service / product line]"),
        ("**Service or product line being funnel-mapped:**", "**sub-service or product line being funnel-mapped:**"),
    )
    for phase1_text, phase2_text in pairs:
        assert phase1_text in p1 and phase1_text not in p2, phase1_text
        assert phase2_text in p2, phase2_text
    # Otherwise the two files carry the same rule.
    restored = p2
    for phase1_text, phase2_text in pairs:
        restored = restored.replace(phase2_text, phase1_text)
    restored = (
        restored.replace("client's sub-service line", "client's service line")
        .replace("vertical, or sub-service, and", "vertical, or service, and")
        .replace("(for example the sub-service,", "(for example the service,")
    )
    assert restored == p1
