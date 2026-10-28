"""The Webinar's competitor sources, read from the approved research instead of pasted.

Never reads a live page: `scraper.read_direct` is replaced. The listing is fed in the prose shape the
context store actually holds (`to_prompt_text`), not the search's raw JSON.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scraper, webinar_sources
from app.services.competitor import ParsedAnalysis, ParsedCompetitor, parse_competitor_listing, to_prompt_text

CATALOG = Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "assetCatalog.ts"


def _competitor(rank: int, domain: str, confidence: str = "Verified", url: str | None = "default") -> ParsedCompetitor:
    return ParsedCompetitor(
        rank=rank,
        domain=domain,
        name=domain.split(".")[0].title(),
        page_url=f"https://{domain}/webinars/" if url == "default" else url,
        verification_confidence=confidence,
        offering_summary=f"Monthly live webinar run by {domain}, gated by email, closing on a free audit.",
        starting_price=None,
        category=None,
        similarity_score=0.9,
        avg_position=None,
        intersections=None,
    )


def _listing(*competitors: ParsedCompetitor) -> str:
    return to_prompt_text(ParsedAnalysis(list(competitors), "Returned a few.", ""), "https://client.com.au")


def _page(url: str, words: int = 400) -> scraper.ScrapedPage:
    text = ("Join our free webinar on paid social strategy. " * (words // 8))[: words * 7]
    return scraper.ScrapedPage(url, url, f"Webinar at {url}", None, text, words, False)


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def fake(url: str) -> scraper.ScrapedPage:
        seen.append(url)
        if "blocked" in url:
            raise scraper.ScrapeError("The site blocked an automated read (403).")
        return _page(url)

    monkeypatch.setattr(scraper, "read_direct", fake)
    return seen


def test_the_prose_listing_keeps_each_offering() -> None:
    parsed = parse_competitor_listing("competitor_analysis_webinars", _listing(_competitor(1, "alpha.com.au")))
    assert parsed.competitors[0].page_url == "https://alpha.com.au/webinars/"
    assert parsed.competitors[0].offering_summary and "Monthly live webinar" in parsed.competitors[0].offering_summary


def test_pages_are_read_and_each_says_why(reads: list[str]) -> None:
    out = asyncio.run(webinar_sources.collect(_listing(_competitor(1, "alpha.com.au"), _competitor(2, "beta.com.au"))))
    assert [s.status for s in out.sources] == ["read", "read"]
    assert all("Found by the webinar competitor research" in s.reason and "Monthly live webinar" in s.reason for s in out.sources)
    assert out.block.startswith("COMPETITOR WEBINAR SOURCES")
    assert "not transcripts" in out.block
    assert "Why this source:" in out.block and "https://beta.com.au/webinars/" in out.block
    assert out.notes == []


def test_verified_first_capped_and_the_rest_explained(reads: list[str]) -> None:
    listing = _listing(
        _competitor(1, "u1.com.au", "Unverified"),
        *[_competitor(i, f"v{i}.com.au") for i in range(2, 8)],
        _competitor(8, "nopage.com.au", url=None),
    )
    out = asyncio.run(webinar_sources.collect(listing))
    assert len(reads) == webinar_sources.MAX_SOURCES
    assert "https://u1.com.au/webinars/" not in reads  # unverified loses to five verified
    statuses = {s.domain: s.status for s in out.sources}
    assert statuses["u1.com.au"] == "not_read" and statuses["nopage.com.au"] == "not_read"
    assert all(s.error for s in out.sources if s.status == "not_read")


def test_a_blocked_page_is_reported_not_hidden(reads: list[str]) -> None:
    out = asyncio.run(webinar_sources.collect(_listing(_competitor(1, "blocked.com.au"), _competitor(2, "beta.com.au"))))
    failed = next(s for s in out.sources if s.domain == "blocked.com.au")
    assert failed.status == "failed" and "403" in failed.error
    assert "blocked.com.au" not in out.block
    assert out.notes and "Only 1" in out.notes[0]


def test_no_listing_reads_nothing(reads: list[str]) -> None:
    out = asyncio.run(webinar_sources.collect("no competitors here"))
    assert reads == [] and out.sources == [] and out.block == "" and out.notes


def test_the_route_is_always_a_200(reads: list[str]) -> None:
    response = TestClient(app).post("/pipeline/webinar/sources", json={"listing": _listing(_competitor(1, "alpha.com.au"))})
    assert response.status_code == 200
    assert response.json()["sources"][0]["status"] == "read"


def test_the_research_is_asked_before_the_sources() -> None:
    text = CATALOG.read_text(encoding="utf-8")
    start = text.index('asset_id: "webinar"')
    block = text[start : text.index("asset_id:", start + 10)]
    assert block.index('ctx("competitor_analysis_webinars"') < block.index('file("competitor_webinar_sources"')
