"""Every stage is told to write bullets, not paragraphs — except copy the client's customer reads.

`generation._OUTPUT_STYLE` is one block composed in one place, the way the industry voice block is,
so no prompt file carries its own copy to drift. These tests pin where it goes and what it exempts.

No network and no model call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.generation import (
    _OUTPUT_STYLE,
    CONFIGS_BY_PHASE,
    OUTPUT_STYLE_BLOCK,
    _prompt_parts,
    build_revision_prompt,
)
from app.services.industry_voice import IndustryProfile

ALL_STAGES = [(phase, asset_id) for phase, configs in CONFIGS_BY_PHASE.items() for asset_id in configs]
FINANCE = IndustryProfile(bucket="regulated_finance", label="Mortgage broking", source="operator_typed")
_PROMPT_ECHO = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "promptEcho.ts"


@pytest.mark.parametrize(("phase", "asset_id"), ALL_STAGES)
def test_every_stage_gets_the_block_before_inputs_and_outside_the_cache(phase: str, asset_id: str) -> None:
    library, tail = _prompt_parts(asset_id, {}, phase)
    assert OUTPUT_STYLE_BLOCK not in library
    assert tail.startswith(_OUTPUT_STYLE)
    assert tail.index(f"===== END {OUTPUT_STYLE_BLOCK} =====") < tail.index("— INPUTS")


@pytest.mark.parametrize(("phase", "asset_id"), ALL_STAGES)
def test_the_industry_voice_still_leads(phase: str, asset_id: str) -> None:
    _, tail = _prompt_parts(asset_id, {}, phase, None, FINANCE)
    assert tail.startswith("===== BEGIN INDUSTRY_VOICE =====")
    assert tail.index("===== END INDUSTRY_VOICE =====") < tail.index(f"===== BEGIN {OUTPUT_STYLE_BLOCK} =====")


def test_the_block_asks_for_bullets_and_exempts_customer_copy() -> None:
    assert "bullet points instead of paragraphs" in _OUTPUT_STYLE
    # Parsers read the master prompts' item formats; shortening must never cost a section or a fact.
    assert "item format the master prompt specifies" in _OUTPUT_STYLE
    assert "never means fewer facts" in _OUTPUT_STYLE
    for copy in ("page copy", "HTML", "article", "emails", "SMS", "social captions", "scripts"):
        assert copy in _OUTPUT_STYLE, copy
    assert "Do not quote, name or acknowledge this block" in _OUTPUT_STYLE


def test_refine_is_not_restyled() -> None:
    # A one-line change note must not reformat the whole draft; Refine keeps the draft's own format.
    assert OUTPUT_STYLE_BLOCK not in build_revision_prompt("draft", "fix the price")


def test_an_echo_of_the_block_is_stripped_in_the_ui() -> None:
    assert OUTPUT_STYLE_BLOCK in _PROMPT_ECHO.read_text(encoding="utf-8")
