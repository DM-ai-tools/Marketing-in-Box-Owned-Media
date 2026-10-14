"""CRO writes copy, never a page, in both phases.

Handed the page design, CRO built a page. A real Phase 2 run got another page's DESIGN.md and
structure brief, wrote a full ```html page into its copy, and had its copy assembled into the other
service's markup. Phase 1 runs with a real existing page did the same, and the card rendered the
page. The Pillar Page is the stage that lays the page out; these tests pin that CRO stays copy.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.routers import pipeline as pipeline_router

RUN_ID = "11111111-1111-1111-1111-111111111111"
REPO = Path(__file__).resolve().parents[3]
PIPELINE_DATA = REPO / "application" / "frontend" / "src" / "pipeline" / "pipelineData.ts"


def _new_page_url_answer() -> str:
    """The exact answer the NEW PAGE button writes, read from the UI rather than restated here."""
    match = re.search(r'urlAnswer:\s*"([^"]+)"', PIPELINE_DATA.read_text(encoding="utf-8"))
    assert match, "NEW_PAGE_OPTIONS.urlAnswer not found in pipelineData.ts"
    return match.group(1)


@pytest.mark.parametrize(
    ("asset_id", "phase", "answers", "expected"),
    [
        ("cro", "phase2", {}, True),
        ("cro", "phase2", {"existing_page_url": "https://client.example/page"}, True),
        ("cro", "phase1", {"existing_page_url": "https://client.example/page"}, True),
        ("cro", "phase1", {}, True),
        ("pillar_page", "phase2", {}, False),
        ("pillar_page", "phase1", {"existing_page_url": "NEW PAGE — no existing URL"}, False),
        ("lead_magnet", "phase2", {}, False),
    ],
)
def test_which_cro_runs_are_copy_only(asset_id: str, phase: str, answers: dict[str, str], expected: bool) -> None:
    assert pipeline_router._cro_writes_copy_only(asset_id, phase, answers) is expected


def test_the_new_page_buttons_answer_is_recognised() -> None:
    answer = _new_page_url_answer()
    assert pipeline_router._cro_writes_copy_only("cro", "phase1", {"existing_page_url": answer})
    # Typed by hand, in any case, with stray whitespace.
    assert pipeline_router._cro_writes_copy_only("cro", "phase1", {"existing_page_url": f"  {answer.lower()} "})


async def _run_stream(monkeypatch: pytest.MonkeyPatch, asset_id: str, phase: str, answers: dict[str, str]) -> dict[str, Any]:
    calls: dict[str, Any] = {"design": 0, "template": 0, "events": []}

    async def passthrough(_asset_id: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
        return answers, None

    async def design(*_a: Any, **_k: Any) -> None:
        calls["design"] += 1
        return None

    async def template(*_a: Any, **_k: Any) -> None:
        calls["template"] += 1
        return None

    async def no_voice(_run_id: str | None) -> None:
        return None

    async def fake_stream(_asset_id: str, _answers: dict[str, str], _phase: str, **kwargs: Any):
        calls["page_design"] = kwargs.get("page_design", "missing")
        yield "copy"

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_read_selected_service_pages", passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", design)
    monkeypatch.setattr(pipeline_router, "resolve_page_template", template)
    monkeypatch.setattr(pipeline_router, "_run_industry", no_voice)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_stream)

    calls["events"] = [e async for e in pipeline_router._generation_sse_stream(asset_id, answers, {}, phase, None, RUN_ID)]
    return calls


@pytest.mark.asyncio
async def test_phase2_cro_gets_no_design_sheet_and_no_page_template(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _run_stream(monkeypatch, "cro", "phase2", {"existing_page_url": _new_page_url_answer()})
    assert calls["design"] == 0, "Phase 2 CRO captured a design sheet it would build a page against"
    assert calls["template"] == 0, "Phase 2 CRO captured a page to pour its copy into"
    assert calls["page_design"] is None
    assert not any("replica" in e for e in calls["events"])


@pytest.mark.asyncio
async def test_phase1_new_page_cro_is_copy_only_too(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _run_stream(monkeypatch, "cro", "phase1", {"existing_page_url": _new_page_url_answer()})
    assert (calls["design"], calls["template"]) == (0, 0)


@pytest.mark.asyncio
async def test_phase1_cro_with_a_real_page_still_writes_copy_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _run_stream(monkeypatch, "cro", "phase1", {"existing_page_url": "https://client.example/page"})
    assert (calls["design"], calls["template"]) == (0, 0), "Phase 1 CRO was handed a page to build"
    assert calls["page_design"] is None
    assert not any("replica" in e for e in calls["events"])


def test_both_cro_prompts_ask_for_copy_not_markup() -> None:
    prompts = REPO / "application" / "backend" / "assets" / "Prompts"
    for path in (prompts / "Master_Prompt_Universal_Page_Rewrite_v1.md", prompts / "Phase2" / "Master_Prompt_Universal_Page_Rewrite_v1_phase2.md"):
        text = path.read_text(encoding="utf-8")
        part2 = text[text.index("**PART 2 — REWRITTEN PAGE**") : text.index("**PART 3")]
        assert "no HTML, CSS or code blocks" in part2, path.name


@pytest.mark.asyncio
async def test_phase2_pillar_page_still_builds_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # The design is captured; the reference's markup is not. The pillar page is a design-language
    # stage (`DESIGN_LANGUAGE_STAGES`): a new page in the client's style, laid out for its own copy.
    calls = await _run_stream(monkeypatch, "pillar_page", "phase2", {})
    assert (calls["design"], calls["template"]) == (1, 0)


@pytest.mark.asyncio
async def test_phase1_pillar_page_is_never_poured_into_the_reference_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _run_stream(monkeypatch, "pillar_page", "phase1", {})
    assert (calls["design"], calls["template"]) == (1, 0)
