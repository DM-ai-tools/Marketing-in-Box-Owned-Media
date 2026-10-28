"""Pillar Page variants: what each one measures, which inputs it sees, the prompt block it gets, and
the comparison route. No live page reads: every fetch here is faked."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import pipeline as pipeline_router
from app.services import page_compare
from app.services import page_variants as V
from app.services.generation import _prompt_parts

RUN_ID = "11111111-1111-1111-1111-111111111111"

CLIENT_PAGE = """<!doctype html><html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization"}</script>
</head><body>
<h1>SEO Services Melbourne</h1>
<p>We help local businesses rank. 12 years of results.</p>
<h2>Our process</h2><p>Audit, plan, build.</p>
<h2>Pricing</h2><p>From $1,500 a month.</p>
<a class="btn" href="/contact">Book a call</a>
<a href="/seo/local">Local SEO</a>
<img src="/a.png">
<script>var notCopy = "ignore these words entirely";</script>
</body></html>"""

COMPETITOR_PAGE = """<html><body><h1>SEO Agency</h1>
<h2>What is technical SEO</h2><p>{body}</p>
<h2>Our SEO process</h2><p>Discovery.</p>
<h2>Frequently asked questions</h2>
<details><summary>Cost?</summary>Depends.</details>
<script type="application/ld+json">{{"@type":"FAQPage","mainEntity":[]}}</script>
<a href="https://rival.com/contact">Get a quote</a>
</body></html>"""

DRAFT = """# Pillar page

## PART 1 — the page

```html
<!doctype html><html><body>
<h1>SEO Services Melbourne</h1>
<h2>Technical SEO explained</h2><p>local seo for melbourne businesses</p>
<h2>How our SEO process works</h2>
<h2>Frequently asked questions</h2>
<a class="cta" href="/contact">Book a strategy call</a>
</body></html>
```

## Delivery notes
- done
"""


def fetcher(pages: dict[str, str]):
    async def fetch(url: str) -> tuple[str, str]:
        if url not in pages:
            raise RuntimeError("404 Not Found")
        return url, pages[url]

    return fetch


def listing(text: str) -> list[str]:
    return [line.split()[-1] for line in text.splitlines() if line.strip()]


# ------------------------------------------------------------------------------------- page_compare


def test_an_html_page_is_counted_off_its_visible_text():
    page = page_compare.outline("Current page", CLIENT_PAGE, "https://client.com.au/seo", ["seo services", "local seo", "ppc"])
    assert page.h1 == "SEO Services Melbourne"
    assert page.sections == ["Our process", "Pricing"]
    assert page.ctas == 1
    assert page.internal_links == 2
    assert page.images == 1
    assert page.schema_types == ["Organization"]
    assert page.proof_mentions == 1  # "12 years"
    assert "ignore" not in getattr(page, "_text")
    assert page.keywords_covered == ["seo services", "local seo"]
    assert page.keywords_checked == 3
    assert not page.faq


def test_a_draft_is_measured_on_its_page_not_its_notes():
    page = page_compare.outline("Variant", DRAFT)
    assert page.h1 == "SEO Services Melbourne"
    assert page.sections == ["Technical SEO explained", "How our SEO process works", "Frequently asked questions"]
    assert page.faq and page.ctas == 1
    assert page_compare.page_html(DRAFT) is not None


def test_prose_falls_back_to_markdown_headings():
    page = page_compare.outline("Prose", "# Title\n\n## One\ntext [Book a call](/book)\n\n## FAQ\n")
    assert page.h1 == "Title" and page.sections == ["One", "FAQ"]
    assert page.ctas == 1 and page.internal_links == 1 and page.faq


def test_the_beat_sheet_merges_topics_and_sets_a_length_to_beat():
    a = page_compare.outline("A", COMPETITOR_PAGE.format(body="word " * 400), "https://rival.com/seo")
    b = page_compare.outline("B", COMPETITOR_PAGE.format(body="word " * 100), "https://other.com/seo")
    gone = page_compare.unavailable("C", "https://walled.com/seo", "the site served a bot check")
    sheet = page_compare.beat_sheet([a, b, gone])
    topics = dict(sheet.topics)
    assert topics["What is technical SEO"] == ["rival.com", "other.com"]
    assert sheet.max_words == a.words and sheet.target_words == int(a.words * 1.1)
    assert sheet.faq_share == "2/2"
    assert sheet.schema_seen == ["FAQPage"]
    text = page_compare.beat_sheet_text(sheet)
    assert "https://walled.com/seo: not read (the site served a bot check)" in text
    assert "What is technical SEO — rival.com, other.com" in text


def test_topic_coverage_counts_the_drafts_sections_against_the_topics():
    page = page_compare.outline("Variant", DRAFT)
    assert page_compare.topic_coverage(page, ["What is technical SEO", "Our SEO process", "Pricing and packages"]) == (2, 3)
    assert page_compare.topic_coverage(page, []) is None


# ------------------------------------------------------------------------------------- page_variants


ANSWERS = {
    "use_skill_document": "Yes",
    "skill_document": "Attached file: skill.md\n\nLead with the outcome.",
    "use_framework_document": "Yes",
    "framework_document": "AIDA: attention, interest, desire, action",
}


def test_each_variant_sees_only_its_own_document():
    skills = V.answers_for("skills", ANSWERS)
    assert skills["skill_document"] == ANSWERS["skill_document"]
    assert skills["framework_document"] == V.NOT_USED and skills["use_framework_document"] == "No"
    reference = V.answers_for("reference", ANSWERS)
    assert reference["skill_document"] == V.NOT_USED and reference["framework_document"] == V.NOT_USED
    assert ANSWERS["skill_document"].startswith("Attached")  # the caller's answers are untouched


@pytest.mark.asyncio
async def test_the_reference_variant_reads_the_hosted_page_and_the_audit():
    measured = await V.measure(
        "reference",
        {},
        reference_url="https://client.com.au/seo",
        listing_text="",
        client_host="client.com.au",
        audit_findings="Layer 2 — Oxytocin: 4/10. No proof above the fold.",
        keywords=["seo services"],
        parse_listing=listing,
        fetch=fetcher({"https://client.com.au/seo": CLIENT_PAGE}),
    )
    block = V.variant_block(measured)
    assert block.strip().startswith(V.BLOCK_START) and block.strip().endswith(V.BLOCK_END)
    assert "BEAT THE CURRENT PAGE" in block
    assert "Sections (H2, in order): Our process | Pricing" in block
    assert "No proof above the fold." in block
    assert "Target keywords present: 1/1" in block
    event = measured.as_event()
    assert event["type"] == "page_variant" and event["reference"]["words"] > 0


@pytest.mark.asyncio
async def test_the_competitor_variant_reads_competitor_pages_never_the_clients():
    pages = {
        "https://rival.com/seo": COMPETITOR_PAGE.format(body="word " * 300),
        "https://other.com/seo": COMPETITOR_PAGE.format(body="word " * 50),
    }
    measured = await V.measure(
        "competitor",
        {},
        reference_url=None,
        listing_text="Client https://www.client.com.au/seo\nRival https://rival.com/seo\nOther https://other.com/seo\nGone https://gone.com/seo",
        client_host="client.com.au",
        audit_findings="",
        keywords=[],
        parse_listing=listing,
        fetch=fetcher(pages),
    )
    assert [c.url for c in measured.beat.competitors] == ["https://rival.com/seo", "https://other.com/seo", "https://gone.com/seo"]
    assert measured.notes == ["1 of 3 competitor pages could not be read."]
    assert "THE BEAT SHEET" in V.variant_block(measured)


@pytest.mark.parametrize(
    "variant,kwargs,needle",
    [
        ("reference", {"reference_url": None}, "no hosted page"),
        ("competitor", {"listing_text": ""}, "approve a competitor search"),
        ("skills", {}, "No skill document"),
        ("framework", {}, "No framework document"),
        ("nonsense", {}, "Unknown page variant"),
    ],
)
@pytest.mark.asyncio
async def test_a_variant_with_nothing_to_work_from_says_what_to_supply(variant, kwargs, needle):
    base: dict[str, Any] = {
        "reference_url": None,
        "listing_text": "",
        "client_host": None,
        "audit_findings": "",
        "keywords": [],
        "parse_listing": listing,
        "fetch": fetcher({}),
    }
    base.update(kwargs)
    answers = {"use_skill_document": "No", "skill_document": "x", "use_framework_document": "Yes", "framework_document": "skip"}
    with pytest.raises(V.VariantUnavailable, match=needle):
        await V.measure(variant, answers, **base)


@pytest.mark.asyncio
async def test_every_competitor_page_unreadable_is_unavailable():
    with pytest.raises(V.VariantUnavailable, match="None of the competitor pages"):
        await V.measure(
            "competitor", {}, reference_url=None, listing_text="A https://a.com/x", client_host=None,
            audit_findings="", keywords=[], parse_listing=listing, fetch=fetcher({}),
        )


@pytest.mark.asyncio
async def test_a_bot_wall_is_not_measured_as_the_page():
    wall = "<html><body><h1>Checking your browser</h1><p>we suspect you are a robot</p></body></html>"
    outline = await V.read_outline("Current page", "https://x.com", fetcher({"https://x.com": wall}))
    assert outline.error == "the site served a bot check"


def test_the_variant_block_sits_in_the_tail_and_the_new_fields_reach_inputs():
    answers = V.answers_for("framework", ANSWERS)
    library, tail = _prompt_parts("pillar_page", answers, "phase1", business_brief=f"\n\n{V.BLOCK_START}\nX\n{V.BLOCK_END}\n\n")
    assert V.BLOCK_START not in library
    assert tail.index(V.BLOCK_END) < tail.index("— INPUTS")
    assert "Framework Document: AIDA: attention, interest, desire, action" in tail
    assert f"Skill Document: {V.NOT_USED}" in tail
    _, phase2_tail = _prompt_parts("pillar_page", answers, "phase2")
    assert "Framework Document: AIDA" in phase2_tail


def test_the_reference_url_follows_the_runs_answers():
    assert pipeline_router._variant_reference_url({"reference_design_source": "https://c.com/seo"}) == "https://c.com/seo"
    assert pipeline_router._variant_reference_url({"reference_design_source": "NO REFERENCE PAGE — https://c.com"}) is None
    assert (
        pipeline_router._variant_reference_url(
            {"reference_design_source": "NO REFERENCE PAGE — https://c.com", "parent_pillar_page_url": "https://c.com/social"}
        )
        == "https://c.com/social"
    )


# ------------------------------------------------------------------------------------- the stream


async def _passthrough(_asset_id: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
    return answers, None


async def _nothing(*_a: Any, **_k: Any) -> None:
    return None


def _quiet_stream(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    async def fake_stream(asset_id: str, answers: dict[str, str], phase: str, **kwargs: Any):
        captured["answers"] = answers
        captured.update(kwargs)
        yield "draft"

    async def no_facts(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no database here")

    for name in ("_run_competitor_prepass", "_run_social_data_prepass", "_run_content_keyword_prepass", "_read_selected_service_pages"):
        monkeypatch.setattr(pipeline_router, name, _passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", _nothing)
    monkeypatch.setattr(pipeline_router, "_run_industry", _nothing)
    monkeypatch.setattr(pipeline_router, "_keyword_targets", _nothing)
    monkeypatch.setattr(pipeline_router, "_business_facts", no_facts)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_stream)


@pytest.mark.asyncio
async def test_a_variant_stream_carries_its_block_and_only_its_document(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    _quiet_stream(monkeypatch, captured)

    async def measured(variant: str, *_a: Any, **_k: Any) -> V.VariantMeasure:
        return V.VariantMeasure(variant)

    monkeypatch.setattr(pipeline_router, "_measure_page_variant", measured)
    events = [
        e async for e in pipeline_router._generation_sse_stream("pillar_page", dict(ANSWERS), {}, "phase1", None, RUN_ID, variant="skills")
    ]
    assert any('"page_variant"' in e for e in events)
    assert "BUILT WITH THE OPERATOR'S SKILL" in captured["business_brief"]
    assert captured["answers"]["framework_document"] == V.NOT_USED
    assert captured["answers"]["skill_document"] == ANSWERS["skill_document"]


@pytest.mark.asyncio
async def test_an_unavailable_variant_stops_before_any_model_call(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    _quiet_stream(monkeypatch, captured)

    async def unavailable(*_a: Any, **_k: Any) -> V.VariantMeasure:
        raise V.VariantUnavailable("There is no hosted page to beat.")

    monkeypatch.setattr(pipeline_router, "_measure_page_variant", unavailable)
    events = [e async for e in pipeline_router._generation_sse_stream("pillar_page", {}, {}, "phase1", None, RUN_ID, variant="reference")]
    error = json.loads(events[-1].removeprefix("data: ").strip())
    assert error["type"] == "error" and error["code"] == "variant_unavailable"
    assert error["message"] == "There is no hosted page to beat." and error["retryable"] is False
    assert "business_brief" not in captured


@pytest.mark.asyncio
async def test_without_a_variant_the_stage_is_unchanged(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    _quiet_stream(monkeypatch, captured)
    events = [e async for e in pipeline_router._generation_sse_stream("pillar_page", dict(ANSWERS), {}, "phase1", None, RUN_ID)]
    assert not any('"page_variant"' in e for e in events)
    assert V.BLOCK_START not in captured["business_brief"]
    assert captured["answers"]["framework_document"] == ANSWERS["framework_document"]


def test_two_variants_hold_separate_stream_locks():
    first = pipeline_router.GenerateStageRequest(run_id=RUN_ID, variant="reference")
    second = pipeline_router.GenerateStageRequest(run_id=RUN_ID, variant="competitor")
    a = pipeline_router._claim_stream("generate", "pillar_page", first)
    b = pipeline_router._claim_stream("generate", "pillar_page", second)
    try:
        assert a != b
        with pytest.raises(Exception) as busy:
            pipeline_router._claim_stream("generate", "pillar_page", first)
        assert getattr(busy.value, "status_code", None) == 409
    finally:
        from app.services import stream_locks

        for key in (a, b):
            if key:
                stream_locks.release(key)


# ------------------------------------------------------------------------------------- the route


def test_the_comparison_route_measures_every_draft_alike():
    client = TestClient(app)
    response = client.post(
        "/pipeline/pillar/variants/compare",
        json={
            "drafts": [
                {"variant": "reference", "label": "Beat your current page", "text": DRAFT},
                {"variant": "skills", "text": "# Prose only\n\n## One\n"},
            ],
            "topics": ["What is technical SEO", "Pricing"],
            "keywords": ["local seo", "seo audit"],
        },
    )
    assert response.status_code == 200
    first, second = response.json()["pages"]
    assert first["variant"] == "reference" and first["has_page"] is True
    assert first["topics_covered"] == [1, 2]
    assert first["keywords_covered"] == ["local seo"] and first["keywords_checked"] == 2
    assert second["has_page"] is False and second["sections"] == ["One"]


def test_the_comparison_reads_the_live_page_when_no_outline_was_sent(monkeypatch):
    from app.services import scraper

    async def fetch(url):
        return url, "<html><body><h1>SEO Melbourne</h1><h2>Pricing</h2><a href='/contact' class='btn'>Book a call</a></body></html>"

    monkeypatch.setattr(scraper, "_fetch_html", fetch)
    client = TestClient(app)
    body = client.post(
        "/pipeline/pillar/variants/compare",
        json={
            "drafts": [{"variant": "reference", "text": DRAFT}],
            "topics": ["Pricing"],
            "answers": {"reference_design_source": "https://client.com.au/seo"},
        },
    ).json()
    current = body["current"]
    assert current["variant"] == "current" and current["label"] == "Current page"
    assert current["url"] == "https://client.com.au/seo" and current["h1"] == "SEO Melbourne"
    assert current["topics_covered"] == [1, 1] and current["error"] is None


def test_a_measured_outline_is_reused_and_no_page_is_read(monkeypatch):
    from app.services import scraper

    async def refuse(url):
        raise AssertionError("the live page was read again")

    monkeypatch.setattr(scraper, "_fetch_html", refuse)
    client = TestClient(app)
    body = client.post(
        "/pipeline/pillar/variants/compare",
        json={
            "drafts": [{"variant": "reference", "text": DRAFT}],
            "current": {"label": "Current page", "url": "https://client.com.au/seo", "words": 900, "sections": ["Pricing"]},
            "answers": {"reference_design_source": "https://client.com.au/seo"},
        },
    ).json()
    assert body["current"]["words"] == 900 and body["current"]["label"] == "Current page"


def test_no_reference_page_means_no_current_column():
    client = TestClient(app)
    body = client.post(
        "/pipeline/pillar/variants/compare",
        json={"drafts": [{"variant": "competitor", "text": DRAFT}], "answers": {"reference_design_source": "NO REFERENCE PAGE — https://client.com.au"}},
    ).json()
    assert body["current"] is None


def test_a_walled_live_page_is_read_rendered_once(monkeypatch):
    from app.services import context_dev

    async def walled(url):
        raise RuntimeError("blocked an automated read")

    calls = []

    async def rendered(url, **_k):
        calls.append(url)
        return context_dev.HTMLPage(url=url, final_url=url, title=None, html="<html><body><h1>Real page</h1></body></html>")

    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "scrape_html", rendered)
    page = asyncio.run(V.read_outline("Current page", "https://client.com.au", walled, rendered=True))
    assert page.error is None and page.h1 == "Real page" and calls == ["https://client.com.au"]
    # Competitor pages never get the paid fallback.
    page = asyncio.run(V.read_outline("Competitor 1", "https://rival.com.au", walled))
    assert page.error and calls == ["https://client.com.au"]
