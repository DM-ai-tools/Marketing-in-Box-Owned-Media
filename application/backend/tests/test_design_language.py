"""Design-language mode — the pillar page follows the client's style, links and breakpoints, not
the reference page's band sequence.

Pins three agreements that otherwise drift silently:

* the directive bound to the sheet only names sections the sheet actually has (the bug this
  pipeline keeps having — `test_every_section_the_directive_names_is_present` is the original);
* the sheet a design-language stage reads no longer says "reproduce this sequence exactly", which
  would argue with the directive;
* both pillar page master prompts carry the same new rules.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services import generation
from app.services.design_md import RESPONSIVE_HEADING, build_design_md
from app.services.design_tokens import DesignTokens
from app.services.generation import (
    DESIGN_LANGUAGE_STAGES,
    PAGE_BUILD_STANDARDS,
    PageDesignInput,
    _brand_token_block,
    build_prompt,
)
from app.services.link_manifest import LINKS_HEADING, extract_link_manifest
from app.services.page_structure import (
    COMPONENT_VOCABULARY_HEADING,
    STRUCTURE_HEADING,
    as_component_vocabulary,
    extract_page_structure,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_PROMPTS = pathlib.Path(__file__).resolve().parents[1] / "assets" / "Prompts"
_URL = "https://www.trafficradius.com.au/seo/"


@pytest.fixture(scope="module")
def sheet() -> str:
    html = (_FIXTURES / "landing_page.html").read_text(encoding="utf-8")
    tokens = DesignTokens(source_url=_URL, available=True, breakpoints=[768, 1024], html=html)
    return build_design_md(
        _URL,
        None,
        None,
        tokens,
        structure=extract_page_structure(html, _URL),
        links=extract_link_manifest(html, _URL),
    )


def test_the_full_sheet_carries_links_and_measured_breakpoints(sheet: str) -> None:
    assert LINKS_HEADING in sheet
    assert RESPONSIVE_HEADING in sheet
    assert "`768px`, `1024px`" in sheet
    assert "`https://www.trafficradius.com.au/contact/`" in sheet


def test_the_theme_brief_carries_neither(sheet: str) -> None:
    html = (_FIXTURES / "landing_page.html").read_text(encoding="utf-8")
    tokens = DesignTokens(source_url=_URL, available=True, html=html)
    theme = build_design_md(
        _URL, None, None, tokens, links=extract_link_manifest(html, _URL), theme_only=True
    )
    assert LINKS_HEADING not in theme
    assert RESPONSIVE_HEADING not in theme


def test_unmeasured_breakpoints_are_stated_as_defaults() -> None:
    tokens = DesignTokens(source_url=_URL, available=True)
    text = build_design_md(_URL, None, None, tokens)
    assert "defaults, not measurements" in text


def test_component_vocabulary_reframes_the_heading_and_keeps_every_row(sheet: str) -> None:
    reframed = as_component_vocabulary(sheet)
    assert STRUCTURE_HEADING not in reframed
    assert "reproduce this sequence exactly" not in reframed
    assert "same bands in the same" not in reframed
    assert COMPONENT_VOCABULARY_HEADING in reframed
    assert _URL in reframed
    # Every measured table row survives untouched.
    rows = [line for line in sheet.splitlines() if line.startswith("| ")]
    assert rows and all(row in reframed for row in rows)


def test_a_sheet_without_the_section_is_unchanged() -> None:
    assert as_component_vocabulary("# tokens only") == "# tokens only"


def test_design_language_block_binds_links_responsive_and_deploy_and_never_the_clone_rule(sheet: str) -> None:
    block = _brand_token_block(sheet, design_language=True)
    assert generation._DESIGN_LANGUAGE_DIRECTIVE in block
    assert generation._COMPONENT_VOCABULARY_DIRECTIVE in block
    assert generation._LINKS_DIRECTIVE in block
    assert generation._RESPONSIVE_DIRECTIVE in block
    assert generation._DEPLOY_READY_DIRECTIVE in block
    assert generation._PAGE_STRUCTURE_DIRECTIVE not in block
    assert STRUCTURE_HEADING not in block


def test_the_clone_stages_keep_the_sequence_rule(sheet: str) -> None:
    block = _brand_token_block(sheet)
    assert generation._PAGE_STRUCTURE_DIRECTIVE in block
    assert generation._DESIGN_LANGUAGE_DIRECTIVE not in block
    # Links are real on any page; the clone stage is bound to them too.
    assert generation._LINKS_DIRECTIVE in block


def test_directives_never_name_a_section_the_sheet_lacks() -> None:
    bare = "---\nname: x\n---\n# x\n## Colors\n"
    block = _brand_token_block(bare, design_language=True)
    assert generation._LINKS_DIRECTIVE not in block
    assert generation._RESPONSIVE_DIRECTIVE not in block
    assert generation._COMPONENT_VOCABULARY_DIRECTIVE not in block


def test_theme_stages_never_get_design_language_clauses(sheet: str) -> None:
    block = _brand_token_block(sheet, theme_only=True, design_language=True)
    assert generation._DESIGN_LANGUAGE_DIRECTIVE not in block
    assert generation._LINKS_DIRECTIVE not in block


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_the_pillar_page_prompt_carries_the_standards_and_the_reframed_sheet(sheet: str, phase: str) -> None:
    prompt = build_prompt("pillar_page", {}, phase, page_design=PageDesignInput(design_md=sheet, theme_brief="t"))
    assert f"BEGIN assets/Prompts/{PAGE_BUILD_STANDARDS}" in prompt
    assert COMPONENT_VOCABULARY_HEADING in prompt
    assert STRUCTURE_HEADING not in prompt


def test_only_design_language_stages_get_the_standards(sheet: str) -> None:
    prompt = build_prompt("cro", {}, "phase1", page_design=PageDesignInput(design_md=sheet, theme_brief="t"))
    assert PAGE_BUILD_STANDARDS not in prompt
    assert DESIGN_LANGUAGE_STAGES == frozenset({"pillar_page"})


def test_the_standards_document_sets_no_aesthetic_of_its_own() -> None:
    text = (_PROMPTS / PAGE_BUILD_STANDARDS).read_text(encoding="utf-8")
    assert "DESIGN.md wins" in text
    assert "#" + "fff" not in text.lower()  # no hard-coded colour


@pytest.mark.parametrize(
    "path",
    [
        _PROMPTS / "Master_Prompt_Universal_Page_Design_v1.md",
        _PROMPTS / "Phase2" / "Master_Prompt_Universal_Page_Design_v1_phase2.md",
    ],
)
def test_both_pillar_prompts_carry_the_design_language_rules(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "design-language fidelity" in text
    assert "Real links only" in text
    assert "Deploy-ready" in text
    assert "Links section" in text
    assert "Responsive section" in text
    # The old fixed breakpoints contradicted the measured ones.
    assert "Breakpoints: 768px (tablet), 480px (mobile)" not in text
    assert "responsive breakpoints at 768px and 480px" not in text
