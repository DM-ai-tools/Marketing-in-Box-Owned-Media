"""Every generation is held to the SEO, GEO and AEO standard.

`generation.CONTENT_OPTIMIZATION` is one block composed in one place, the way the output-style
block is, so no prompt file carries its own copy to drift. It follows the business brief — the
facts, keywords and link map are already stated — and it does not ride the cached reference
library. Refine edits a finished draft, so it does not get the block: adding an FAQ or a
schema there is a new document, which a full re-run is for.

No network and no model call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.funnel_build import BuildInputs, build_email_request, build_page_request
from app.services.funnel_plan import FunnelEmail, FunnelFile, FunnelPlan, FunnelScreen
from app.services.generation import (
    BRAND_DESIGN_TOKENS,
    CONFIGS_BY_PHASE,
    CONTENT_OPTIMIZATION,
    CONTENT_OPTIMIZATION_BLOCK,
    OUTPUT_STYLE_BLOCK,
    PageDesignInput,
    _prompt_parts,
    build_revision_prompt,
)
from app.services.manus_funnel import build_brief

ALL_STAGES = [(phase, asset_id) for phase, configs in CONFIGS_BY_PHASE.items() for asset_id in configs]
BRIEF = "===== BEGIN BUSINESS_CHECK =====\nThe client's offers.\n===== END BUSINESS_CHECK =====\n\n"
_PROMPT_ECHO = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "promptEcho.ts"


def _plan() -> tuple[FunnelPlan, FunnelFile, FunnelEmail]:
    screen = FunnelScreen(part=3, title="Audit", page_type="landing", url_line="/audit/", copy="Book a call.")
    page = FunnelFile(key="audit", url_path="/audit/", screens=[screen])
    email = FunnelEmail(key="e1", title="Email 1", subject="Hello", copy="Thanks for asking.")
    plan = FunnelPlan(title="Funnel", preamble="", strategy="", files=[page], emails=[email], notes="")
    return plan, page, email


@pytest.mark.parametrize(("phase", "asset_id"), ALL_STAGES)
def test_every_stage_gets_the_block_after_the_brief_and_outside_the_cache(phase: str, asset_id: str) -> None:
    library, tail = _prompt_parts(asset_id, {}, phase, business_brief=BRIEF)
    assert CONTENT_OPTIMIZATION_BLOCK not in library
    assert tail.index(f"===== END {OUTPUT_STYLE_BLOCK} =====") < tail.index("===== END BUSINESS_CHECK =====")
    assert tail.index("===== END BUSINESS_CHECK =====") < tail.index(f"===== BEGIN {CONTENT_OPTIMIZATION_BLOCK} =====")
    assert tail.index(f"===== END {CONTENT_OPTIMIZATION_BLOCK} =====") < tail.index("— INPUTS")
    assert tail.count(CONTENT_OPTIMIZATION) == 1


def test_the_block_precedes_brand_tokens() -> None:
    _, tail = _prompt_parts("pillar_page", {}, page_design=PageDesignInput(design_md="# Sheet\n\nColours."))
    assert tail.index(f"===== END {CONTENT_OPTIMIZATION_BLOCK} =====") < tail.index(
        f"===== BEGIN {BRAND_DESIGN_TOKENS} ====="
    )


def test_the_block_binds_search_optimisation_without_inventing_facts() -> None:
    for phrase in (
        "SEO",
        "GEO",
        "AEO",
        "search intent",
        "One H1",
        "FAQ",
        "schema",
        "Never invent",
        "Never stuff",
        "KEYWORD_TARGETS",
        "SEO_ARCHITECTURE",
        "master prompt's section list",
        "PAGE_BUILD_STANDARDS",
        "ChatGPT, Gemini and Perplexity",
        "[CLIENT TO CONFIRM]",
        "Do not quote, name or acknowledge this block",
        "60 characters",
        "155",
    ):
        assert phrase in CONTENT_OPTIMIZATION, phrase


def test_refine_is_not_reoptimised() -> None:
    # A one-line change note must not grow an FAQ or a schema block. A full re-run picks the rules up.
    assert CONTENT_OPTIMIZATION_BLOCK not in build_revision_prompt("draft", "fix the price")


def test_funnel_pages_emails_and_the_manus_brief_carry_the_same_block() -> None:
    plan, page, email = _plan()
    inputs = BuildInputs(
        plan=plan, design=None, manifest=None, forms=[], hub_text="", site_origin="https://example.com"
    )
    page_text = build_page_request(inputs, page)[-1]["text"]
    assert CONTENT_OPTIMIZATION in page_text
    assert page_text.index("PAGE_BUILD_STANDARDS") < page_text.index(f"BEGIN {CONTENT_OPTIMIZATION_BLOCK}")
    assert CONTENT_OPTIMIZATION in build_email_request(inputs, email)
    brief = build_brief(inputs, [page])
    assert CONTENT_OPTIMIZATION in brief
    assert brief.index("PAGE BUILD STANDARDS") < brief.index(f"BEGIN {CONTENT_OPTIMIZATION_BLOCK}")


def test_an_echo_of_the_block_is_stripped_in_the_ui() -> None:
    assert CONTENT_OPTIMIZATION_BLOCK in _PROMPT_ECHO.read_text(encoding="utf-8")
