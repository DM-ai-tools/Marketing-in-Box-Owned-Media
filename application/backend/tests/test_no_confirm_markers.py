"""A pillar page never carries `[CLIENT TO CONFIRM: …]` or a `[… PLACEHOLDER]`.

Both pillar prompts used to tell the model to put the marker in the built page where a fact was
missing, and PAGE_BUILD_STANDARDS said to keep it "visible and styled as notes". So a production page
shipped sentences like "[CLIENT TO CONFIRM: on-page display of 27+ industry awards … to be confirmed
with compliance]". A held-back claim is now left out of the page and listed under "Confirm before
publishing", outside the HTML. `page_check` still flags any marker as must-fix — the backstop.

No network and no model call: the prompts are read off disk.
"""

from __future__ import annotations

import re

import pytest

from app.services.generation import _PROMPTS_DIR, build_prompt

PILLAR = {
    "phase1": "Master_Prompt_Universal_Page_Design_v1.md",
    "phase2": "Phase2/Master_Prompt_Universal_Page_Design_v1_phase2.md",
}

#: Wording that puts a marker *into* the page. Any of these back and the markers come back with it.
_MARKER_IN_PAGE = re.compile(
    r"with `\[CLIENT TO CONFIRM[^`]*`\] where"
    r"|marker in the built page where"
    r"|carry a `\[CLIENT TO CONFIRM\]` gap"
    r"|mark clearly: `\[TESTIMONIAL PLACEHOLDER"
    r"|no placeholders except"
    r"|stay visible\s+and styled as notes",
    re.IGNORECASE,
)


def _read(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", list(PILLAR))
def test_no_instruction_puts_a_marker_in_the_page(phase: str) -> None:
    hits = _MARKER_IN_PAGE.findall(_read(PILLAR[phase]))
    assert not hits, f"{phase}: {hits}"


def test_build_standards_keep_markers_out() -> None:
    text = _read("PAGE_BUILD_STANDARDS.md")
    assert not _MARKER_IN_PAGE.search(text)
    assert "never reach\n  the page" in text


@pytest.mark.parametrize("phase", list(PILLAR))
def test_the_rule_and_the_list_reach_the_model(phase: str) -> None:
    prompt = build_prompt("pillar_page", {}, phase)
    assert "**No confirmation markers in the page.**" in prompt
    assert "is left out of the page" in prompt
    assert "Never replace it with an invented or softened version" in prompt
    # The questions have somewhere to go that is not the page.
    assert prompt.count("Confirm before publishing") + prompt.count("CONFIRM BEFORE PUBLISHING") >= 3
    assert "leave the testimonial section out of the page" in prompt
