"""`page_revision.py` — a pillar page is refined by exact edits, never retyped.

The bug this pins: "Fix with Refine" on a pillar page asked for the FULL revised document, and the
model re-emitted the page from a copy with none of its sources — sections moved, copy reworded, the
CRO rewrite lost. Now the model returns `find`/`replace` edits and the code applies them, so every
byte no edit names is unchanged. No test here calls Anthropic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import pipeline as pipeline_router
from app.services import page_revision as R

PAGE = """PART 1 — DELIVERY NOTES
- Hero headline: "Development finance that keeps your build moving"

```html
<!doctype html><html lang="en"><head><title>Development Finance</title></head><body>
<header><nav><a href="https://argfinance.com.au/contact/">Contact</a></nav></header>
<main>
  <section class="hero"><h1>Development finance that keeps your build moving</h1>
    <p>[CLIENT TO CONFIRM: named service beyond the consultation call]</p></section>
  <section class="process"><h2>How it works</h2><p>Three steps from feasibility to drawdown.</p></section>
  <section class="proof"><h2>Why developers choose ARG</h2><p>Panel access beyond the majors.</p></section>
</main></body></html>
```
"""


def test_every_business_fix_and_every_page_refine_is_made_of_edits() -> None:
    # Any draft holding a page, whatever the stage.
    assert R.uses_edit_revision("pillar_page", PAGE, business_fix=False)
    assert R.uses_edit_revision("cro", PAGE, business_fix=False)
    # Every business fix, page or prose.
    assert R.uses_edit_revision("blog", "## Intro\nProse.", business_fix=True)
    # Only an ordinary Refine of prose is still a full revision.
    assert not R.uses_edit_revision("blog", "## Intro\nProse.", business_fix=False)


def test_an_edit_changes_only_what_it_names() -> None:
    edits = [R.Edit("<p>[CLIENT TO CONFIRM: named service beyond the consultation call]</p>", "")]
    revised, report = R.apply_edits(PAGE, edits)
    assert report.applied == 1 and not report.rejected
    assert "CLIENT TO CONFIRM" not in revised
    # Everything else is byte-for-byte the previous draft: the sections are in the same order.
    assert revised == PAGE.replace("<p>[CLIENT TO CONFIRM: named service beyond the consultation call]</p>", "")
    assert revised.index('class="hero"') < revised.index('class="process"') < revised.index('class="proof"')


def test_a_find_with_the_wrong_whitespace_still_lands_once() -> None:
    sloppy = '<section class="process"><h2>How it works</h2>\n<p>Three steps from feasibility to drawdown.</p>'
    revised, report = R.apply_edits(PAGE, [R.Edit(sloppy, '<section class="process"><h2>How it works</h2><p>Three steps.</p>')])
    assert report.applied == 1
    assert "Three steps.</p>" in revised and "feasibility" not in revised


def test_an_edit_that_cannot_be_placed_is_reported_not_guessed() -> None:
    edits = [
        R.Edit("Development finance that keeps your build moving", "X"),  # in the notes and the H1
        R.Edit("A sentence the draft does not contain.", "Y"),
    ]
    revised, report = R.apply_edits(PAGE, edits)
    assert revised == PAGE and report.applied == 0
    assert "appears 2 times" in report.rejected[0][1]
    assert "is not in the draft" in report.rejected[1][1]


def test_one_edit_may_not_rewrite_the_page() -> None:
    body = PAGE[PAGE.index("<main>") : PAGE.index("</main>") + len("</main>")]
    revised, report = R.apply_edits(PAGE, [R.Edit(body, "<main><p>A different page.</p></main>")])
    assert revised == PAGE and report.applied == 0
    assert "too much in one edit" in report.rejected[0][1]


def test_edits_are_read_from_a_fenced_or_chatty_reply() -> None:
    reply = 'Here are the edits:\n```json\n{"edits": [{"find": "a", "replace": "b", "why": "fix"}, {"replace": "x"}]}\n```\nDone.'
    assert R.parse_edits(reply) == [R.Edit("a", "b", "fix")]
    assert R.parse_edits('{"edits": []}') == []
    assert R.parse_edits("I rewrote the page for you: <html>...</html>") is None


def test_the_prompt_carries_the_cro_copy_and_design_and_asks_for_edits_only() -> None:
    prompt = R.build_edit_prompt(PAGE, "fix it", source_copy="THE APPROVED CRO COPY", design_md="---\ncolors: {}\n---")
    assert "THE APPROVED CRO COPY" in prompt and "DESIGN.md" in prompt
    assert "do not rewrite or re-emit the document" in prompt.lower()
    assert "Do not move, reorder" in prompt
    locked = R.build_edit_prompt(PAGE, "fix it", lock_layout=True)
    assert "THE LAYOUT IS LOCKED" in locked and '"skipped"' in locked
    assert prompt.index("END PREVIOUS DRAFT") < prompt.index("THE PAGE'S COPY") < prompt.index("fix it")


# ---------- the refine route ----------


def _events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


def test_refining_a_pillar_page_applies_edits_and_never_retypes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    async def fake_edits(_asset: str, prompt: str, _phase: str = "phase1", on_usage: Any = None):
        prompts.append(prompt)
        return json.dumps({"edits": [{"find": "<p>Panel access beyond the majors.</p>", "replace": "<p>Panel access.</p>"}]}), "end_turn"

    async def never(*_a: Any, **_k: Any):
        raise AssertionError("a pillar page must not be retyped by the full-document revision")
        yield ""  # pragma: no cover

    monkeypatch.setattr(pipeline_router, "generate_page_edits", fake_edits)
    monkeypatch.setattr(pipeline_router, "generate_revision_stream", never)
    res = TestClient(app).post(
        "/pipeline/refine/pillar_page/stream",
        json={"previous_draft": PAGE, "note": "shorten the proof line", "source_copy": "CRO COPY FROM THE CARD"},
    )
    assert res.status_code == 200, res.text
    events = _events(res.text)
    report = next(e for e in events if e["type"] == "revision_edits")
    assert report["applied"] == 1 and report["rejected"] == []
    revised = "".join(e["text"] for e in events if e["type"] == "delta")
    # The route strips the draft it receives (`NonBlankStr`), so the trailing newline is not part of it.
    assert revised == PAGE.strip().replace("<p>Panel access beyond the majors.</p>", "<p>Panel access.</p>")
    done = events[-1]
    assert done["type"] == "done" and done["truncated"] is False
    # The card's own copy is what the edits are written against.
    assert "CRO COPY FROM THE CARD" in prompts[0]


def test_an_unreadable_reply_returns_the_page_unchanged_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_edits(*_a: Any, **_k: Any):
        return "<html>a whole new page</html>", "end_turn"

    monkeypatch.setattr(pipeline_router, "generate_page_edits", fake_edits)
    res = TestClient(app).post("/pipeline/refine/pillar_page/stream", json={"previous_draft": PAGE, "note": "fix it"})
    events = _events(res.text)
    report = next(e for e in events if e["type"] == "revision_edits")
    assert report["unreadable"] is True and "No change was made" in report["rejected"][0][1]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == PAGE.strip()


def test_other_stages_still_get_the_full_document_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    async def revise(*_a: Any, **_k: Any):
        yield "revised blog"

    async def never(*_a: Any, **_k: Any):
        raise AssertionError("edits are for built pages only")

    monkeypatch.setattr(pipeline_router, "generate_revision_stream", revise)
    monkeypatch.setattr(pipeline_router, "generate_page_edits", never)
    res = TestClient(app).post("/pipeline/refine/blog/stream", json={"previous_draft": "draft", "note": "fix it"})
    assert "revised blog" in res.text and "revision_edits" not in res.text


# ---------- a business fix may not change the layout ----------

STYLED = """Notes.

```html
<!doctype html><html lang="en"><head><title>Development Finance</title>
<style>.hero{display:flex}.logo{width:160px}</style></head><body>
<header><a class="logo" href="/"><img src="https://argfinance.com.au/logo.svg" alt="ARG"></a>
<nav><ul><li><a href="#">Contact</a></li></ul></nav></header>
<main>
<section class="hero"><img class="hero-img" src="https://api.example/media/hero" alt="A builder on site">
<h1>Development finance that keeps your build moving</h1>
<p>[CLIENT TO CONFIRM: free development finance assessment]</p>
<a class="btn" href="#">Book a call</a><i class="icon icon-phone"></i></section>
<section class="proof"><img src="https://api.example/media/proof" alt="Proof"><p>Panel access beyond the majors.</p></section>
</main></body></html>
```
"""


def _one(find: str, replace: str):
    return R.apply_edits(STYLED, [R.Edit(find, replace)], lock_layout=True)


def test_copy_links_alt_and_a_claim_paragraph_can_change_under_the_lock() -> None:
    edits = [
        R.Edit("Panel access beyond the majors.", "Access to non-bank lenders."),
        R.Edit('<li><a href="#">Contact</a></li>', '<li><a href="https://argfinance.com.au/contact/">Contact</a></li>'),
        R.Edit('alt="A builder on site"', 'alt="A developer reviewing plans on site"'),
        R.Edit("<p>[CLIENT TO CONFIRM: free development finance assessment]</p>\n", ""),
    ]
    revised, report = R.apply_edits(STYLED, edits, lock_layout=True)
    assert report.applied == 4 and not report.rejected, report.rejected
    assert "CLIENT TO CONFIRM" not in revised and "non-bank lenders" in revised


@pytest.mark.parametrize(
    ("find", "replace", "what"),
    [
        (".logo{width:160px}", ".logo{width:120px}", "a CSS rule"),
        ('class="hero"', 'class="hero hero--centered"', "a class"),
        ("https://api.example/media/hero", "https://api.example/media/proof", "an image source"),
        ('<i class="icon icon-phone"></i>', '<i class="icon icon-mail"></i>', "an icon"),
        (
            '<section class="proof"><img src="https://api.example/media/proof" alt="Proof"><p>Panel access beyond the majors.</p></section>',
            "",
            "a whole section",
        ),
        ('<a class="logo" href="/"><img src="https://argfinance.com.au/logo.svg" alt="ARG"></a>\n', "", "the logo"),
        ("<h1>Development finance", "<h2>Development finance", "a heading's level"),
    ],
)
def test_a_layout_change_is_rejected_whatever_the_edit_says(find: str, replace: str, what: str) -> None:
    revised, report = _one(find, replace)
    assert revised == STYLED, what
    assert report.applied == 0 and "layout" in report.rejected[0][1], (what, report.rejected)


def test_moving_a_section_is_a_layout_change() -> None:
    hero_start = STYLED.index('<section class="hero">')
    proof_start = STYLED.index('<section class="proof">')
    main_end = STYLED.index("</main>")
    hero, proof = STYLED[hero_start:proof_start], STYLED[proof_start:main_end]
    revised, report = _one(hero + proof, proof + hero)
    assert revised == STYLED and report.applied == 0


def test_an_ordinary_refine_may_change_layout_when_asked() -> None:
    revised, report = R.apply_edits(STYLED, [R.Edit(".logo{width:160px}", ".logo{width:120px}")])
    assert report.applied == 1 and ".logo{width:120px}" in revised


def test_a_prose_business_fix_keeps_its_outline() -> None:
    doc = "# Funnel\n\n## Stage 1\nAwareness copy.\n\n## Stage 2\nConsideration copy.\n"
    ok_text, ok_report = R.apply_edits(doc, [R.Edit("Awareness copy.", "Sharper awareness copy.")], lock_layout=True)
    assert ok_report.applied == 1
    moved, moved_report = R.apply_edits(doc, [R.Edit("## Stage 2", "## Stage Two")], lock_layout=True)
    assert moved == doc and moved_report.applied == 0


def test_many_small_edits_cannot_add_up_to_a_rewrite() -> None:
    doc = "".join(f"<p>Line {n} of the page copy.</p>\n" for n in range(40))
    edits = [R.Edit(f"<p>Line {n} of the page copy.</p>", f"<p>Completely new line number {n}.</p>") for n in range(40)]
    _revised, report = R.apply_edits(doc, edits)
    assert 0 < report.applied < 40
    assert any("too much of the document" in why for _f, why in report.rejected)


def test_the_models_own_skipped_list_is_read() -> None:
    reply = '{"edits": [], "skipped": [{"finding": "Fixed width 1200px", "why": "a CSS change"}]}'
    assert R.parse_skipped(reply) == [("Fixed width 1200px", "a CSS change")]
    assert R.parse_skipped("not json") == []


def test_a_business_fix_on_a_page_is_locked_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_facts(*_a: Any, **_k: Any):
        from app.services.business_facts import BusinessFacts

        return BusinessFacts()

    async def fake_edits(_asset: str, prompt: str, _phase: str = "phase1", on_usage: Any = None):
        assert "THE LAYOUT IS LOCKED" in prompt
        return json.dumps(
            {
                "edits": [
                    {"find": "Panel access beyond the majors.", "replace": "Access to non-bank lenders."},
                    {"find": ".logo{width:160px}", "replace": ".logo{width:100%}"},
                ],
                "skipped": [{"finding": "Fixed width", "why": "needs a CSS change"}],
            }
        ), "end_turn"

    monkeypatch.setattr(pipeline_router, "_business_facts", fake_facts)
    monkeypatch.setattr(pipeline_router, "generate_page_edits", fake_edits)
    res = TestClient(app).post(
        "/pipeline/refine/pillar_page/stream",
        json={"previous_draft": STYLED, "note": "Fix these", "business_fix": True},
    )
    events = _events(res.text)
    report = next(e for e in events if e["type"] == "revision_edits")
    assert report["layout_locked"] is True and report["applied"] == 1
    assert "layout" in report["rejected"][0][1]
    assert report["skipped"] == [["Fixed width", "needs a CSS change"]]
    revised = "".join(e["text"] for e in events if e["type"] == "delta")
    assert ".logo{width:160px}" in revised and "non-bank lenders" in revised


_FILLER = "".join(f"<p>Section {i} copy that stays exactly as it is.</p>" for i in range(60))


def test_a_quote_with_a_plain_ampersand_finds_the_entity_in_the_page() -> None:
    # A real "Fix with Refine": the finding quoted the heading as it reads, the page holds the
    # entity, and the fix was rejected as "the text to change is not in the draft".
    page = "<section><h3>Trade &amp; Distribution Business — Melbourne, VIC</h3><p>Copy.</p></section>" + _FILLER
    revised, report = R.apply_edits(page, [R.Edit("<h3>Trade & Distribution Business — Melbourne, VIC</h3>", "<h3>Trade &amp; Distribution</h3>")])
    assert report.applied == 1 and not report.rejected
    assert "<h3>Trade &amp; Distribution</h3>" in revised


def test_curly_and_straight_quotes_match_either_way() -> None:
    page = "<p>We&rsquo;re based in Melbourne.</p>" + _FILLER
    revised, report = R.apply_edits(page, [R.Edit("<p>We're based in Melbourne.</p>", "<p>Melbourne-based.</p>")])
    assert report.applied == 1 and revised == "<p>Melbourne-based.</p>" + _FILLER


_SERVICES_PAGE = """```html
<html><body><header><img class="logo" src="https://x.com/logo.png" alt="X"></header>
<main><section class="services"><h2>Services</h2>
<div class="card"><h3>AI SEO</h3><p>We do AI SEO.</p></div>
<div class="card"><h3>Local SEO</h3><p>We do local SEO.</p></div>
<div class="card"><h3>Off-Page SEO</h3><p>We do off-page SEO.</p></div>
</section></main><footer><p>X Pty Ltd</p></footer></body></html>
```"""


def test_a_business_fix_may_remove_a_whole_card_for_a_service_the_client_does_not_sell() -> None:
    edits = [R.Edit(find='<div class="card"><h3>Local SEO</h3><p>We do local SEO.</p></div>', replace="")]
    revised, report = R.apply_edits(_SERVICES_PAGE, edits, lock_layout=True)
    assert report.applied == 1 and not report.rejected
    assert "Local SEO" not in revised and "AI SEO" in revised and "Off-Page SEO" in revised


def test_a_business_fix_may_remove_a_markdown_section() -> None:
    body = "We review every page against what buyers search for and what the client can prove. " * 12
    draft = f"# Page\n\n## AI SEO\n{body}\n\n### Local SEO\nWe do local SEO.\n\n## Contact\n{body}\n"
    revised, report = R.apply_edits(draft, [R.Edit(find="### Local SEO\nWe do local SEO.\n\n", replace="")], lock_layout=True)
    assert report.applied == 1 and "Local SEO" not in revised


@pytest.mark.parametrize(
    "find",
    [
        '<header><img class="logo" src="https://x.com/logo.png" alt="X"></header>',
        "<footer><p>X Pty Ltd</p></footer>",
    ],
)
def test_removal_never_takes_the_logo_or_the_frame(find: str) -> None:
    _, report = R.apply_edits(_SERVICES_PAGE, [R.Edit(find=find, replace="")], lock_layout=True)
    assert report.applied == 0 and report.rejected


def test_removal_does_not_open_the_door_to_moving_a_section() -> None:
    card = '<div class="card"><h3>Local SEO</h3><p>We do local SEO.</p></div>'
    moved = _SERVICES_PAGE.replace(card, "").replace("<h2>Services</h2>", "<h2>Services</h2>" + card)
    start = _SERVICES_PAGE.index("<h2>Services</h2>")
    end = _SERVICES_PAGE.index("</section>")
    _, report = R.apply_edits(_SERVICES_PAGE, [R.Edit(find=_SERVICES_PAGE[start:end], replace=moved[start:moved.index("</section>")])], lock_layout=True)
    assert report.applied == 0 and report.rejected
