"""Competitor validation: proof, fit and feasibility for this client, measured then judged.

Never reaches DataForSEO, a competitor's site or the model: all three are patched.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import competitor_validation as cv
from app.services import dataforseo_client, scraper
from app.services.business_facts import BusinessFacts
from app.services.competitor import ParsedCompetitor
from app.services.dataforseo_client import RankedKeyword


def comp(rank: int, name: str, domain: str, category: str, summary: str, confidence: str = "Verified", price: str | None = None) -> ParsedCompetitor:
    return ParsedCompetitor(
        rank=rank, domain=domain, name=name, page_url=f"https://{domain}/offer/", verification_confidence=confidence,
        offering_summary=summary, starting_price=price, category=category,
        similarity_score=None, avg_position=None, intersections=None,
    )


def rk(keyword: str, position: int, traffic: float) -> RankedKeyword:
    return RankedKeyword(keyword=keyword, volume=500, difficulty=20, position=position, url="", traffic=traffic)


RANKED: dict[str, list[RankedKeyword]] = {
    "client.com.au": [rk("social media marketing melbourne", 14, 40.0)],
    "alpha.com.au": [
        rk("social media marketing audit", 3, 900.0),
        rk("social media marketing agency melbourne", 6, 600.0),
        rk("free marketing template", 12, 20.0),
    ],
    "beta.com.au": [rk("social media marketing audit", 9, 15.0), rk("plumbing tips", 2, 300.0)],
    "gamma.com.au": [],
}
PAGES_COUNT = {"client.com.au": 6, "alpha.com.au": 42, "beta.com.au": 12}
PAGES = {
    "https://alpha.com.au/offer/": "Free social media audit for Melbourne retailers. Updated 2026.",
    "https://beta.com.au/offer/": "See our client results and 5-star reviews before you book.",
}

FACTS = BusinessFacts(
    service="Social Media Marketing",
    icp="Owners of Melbourne retail stores who post inconsistently and cannot tell which posts sell.",
    settings={"testimonials_before_after_permitted": "NO"},
    keywords=[{"keyword": "social media marketing audit", "volume": 500}],
)


class FakeDfs:
    def __init__(self, *a: Any, **k: Any) -> None:
        self.targets: list[str] = []

    async def ranked_keywords(self, target: str, location: str, *, limit: int = 60, language: str = "English") -> list[RankedKeyword]:
        return list((await self.ranked_overview(target, location, limit=limit, language=language)).keywords)

    async def ranked_overview(self, target: str, location: str, *, limit: int = 60, language: str = "English") -> dataforseo_client.RankedOverview:
        if target == "gamma.com.au":
            raise dataforseo_client.DataForSEOError("DataForSEO rate-limiting this account (429)")
        kws = RANKED.get(target, [])
        traffic = round(sum(k.traffic for k in kws if k.traffic is not None), 1) if kws else 0.0
        return dataforseo_client.RankedOverview(keywords=tuple(kws), keyword_count=len(kws), traffic=traffic)

    async def ranking_page_count(self, target: str, location: str, *, language: str = "English") -> int | None:
        return PAGES_COUNT.get(target)

    async def aclose(self) -> None:
        pass


def _judge_reply(**over: Any) -> str:
    data = {
        "competitors": [
            {"rank": 1, "buyer_fit": 9, "buyer_fit_reason": "Speaks to the retail owner.", "buyer_fit_quote": "Owners of Melbourne retail stores",
             "blockers": [], "what_to_take": "A free 10-point audit", "how_to_prove": "Opt-in rate ≥ 25% within 30 days"},
            {"rank": 2, "buyer_fit": 8, "buyer_fit_reason": "Invented.", "buyer_fit_quote": "words that appear nowhere at all",
             "blockers": [{"reason": "Needs a studio", "quote": "also not anywhere"}]},
            {"rank": 3, "buyer_fit": 3, "buyer_fit_reason": "Different buyer.", "buyer_fit_quote": "post inconsistently"},
        ],
        "market_gaps": ["No competitor offers a post-by-post sales report."],
    }
    data.update(over)
    return json.dumps(data)


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"judge": _judge_reply(), "prompts": []}
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: True)
    monkeypatch.setattr(dataforseo_client, "DataForSEOClient", FakeDfs)

    async def read(url: str) -> scraper.ScrapedPage:
        if url not in PAGES:
            raise scraper.ScrapeError(f"{url} returned HTTP 404")
        text = PAGES[url]
        return scraper.ScrapedPage(url=url, final_url=url, title="T", meta_description=None, text=text * 40,
                                   word_count=400, truncated=False)

    async def judge(prompt: str, on_usage: Any) -> str:
        state["prompts"].append(prompt)
        if isinstance(state["judge"], Exception):
            raise state["judge"]
        return state["judge"]

    monkeypatch.setattr(scraper, "read_direct", read)
    monkeypatch.setattr(cv, "_call_judge", judge)
    return state


LISTING = [
    comp(1, "Alpha", "alpha.com.au", "Free audit", "A free social media audit for retailers."),
    comp(2, "Beta", "beta.com.au", "Case study library", "Gated case studies and client reviews.", "Partially verified"),
    comp(3, "Gamma", "gamma.com.au", "Free audit", "A free audit.", "Unverified"),
]


def _run(**kw: Any) -> cv.ValidationReport:
    return asyncio.run(cv.validate("competitor_analysis_lead_magnet", LISTING, FACTS, client_url="https://www.client.com.au/", region="Melbourne, Australia", **kw))


def test_proof_is_measured_traffic_rankings_and_liveness_only(patched: dict[str, Any]) -> None:
    report = _run()
    alpha = next(s for s in report.competitors if s.name == "Alpha")
    m = {x.key: x for x in alpha.metrics}
    assert m["traffic"].value == "1,520" and m["traffic"].source == "measured"
    assert "client's 40" in m["traffic"].detail
    assert m["top10"].value == "2"
    assert m["live"].value == "Live" and "2026" in m["live"].detail
    assert "prevalence" not in m and "verified" not in m
    assert m["level"].value == "Above this client" and m["level"].source == "measured"
    assert m["pages"].value == "42" and m["keywords"].value == "3"
    assert report.client_traffic == 40.0
    assert report.client_page_count == 6
    assert alpha.business_level == "above" and alpha.page_count == 42
    assert any("DataForSEO ranked keywords and ranking pages (Google, Australia)" in s for s in report.sources)


def test_overlap_counts_only_the_clients_own_keywords(patched: dict[str, Any]) -> None:
    report = _run()
    beta = next(s for s in report.competitors if s.name == "Beta")
    overlap = next(x for x in beta.metrics if x.key == "overlap")
    assert overlap.value == "1 of 2" and "social media marketing audit" in overlap.detail


def test_a_failed_measurement_is_unavailable_never_zero(patched: dict[str, Any]) -> None:
    report = _run()
    gamma = next(s for s in report.competitors if s.name == "Gamma")
    traffic = next(x for x in gamma.metrics if x.key == "traffic")
    assert traffic.score is None and traffic.source == "unavailable" and traffic.value == "—"
    assert any("could not measure Gamma" in n for n in report.notes)


def test_judge_reasons_must_quote_their_source(patched: dict[str, Any]) -> None:
    report = _run()
    beta = next(s for s in report.competitors if s.name == "Beta")
    buyer = next(x for x in beta.metrics if x.key == "buyer")
    assert buyer.score is None  # its quote is nowhere
    assert not any(b.source == "judged" for b in beta.blockers)
    assert report.dropped_quotes == 2
    alpha = next(s for s in report.competitors if s.name == "Alpha")
    assert next(x for x in alpha.metrics if x.key == "buyer").score == 90


def test_the_clients_own_settings_block_a_play(patched: dict[str, Any]) -> None:
    report = _run()
    beta = next(s for s in report.competitors if s.name == "Beta")
    assert beta.verdict == "avoid"
    assert any("testimonials are NO" in b.reason for b in beta.blockers)


def test_ranked_by_verdict_with_what_to_take_and_how_to_prove(patched: dict[str, Any]) -> None:
    report = _run()
    assert report.competitors[0].name == "Alpha" and report.competitors[0].verdict == "implement_now"
    assert report.competitors[0].how_to_prove.startswith("Opt-in rate")
    assert [s.verdict for s in report.competitors][1:] == ["avoid", "not_a_fit"]
    audit = next(p for p in report.plays if p.play == "Free audit")
    assert audit.count == 2 and audit.verdict == "implement_now"
    assert report.market_gaps == ["No competitor offers a post-by-post sales report."]
    md = report.markdown
    assert "## Competitor validation — ranked for this client" in md
    assert "| 1 | Alpha | Free audit | Above this client | 42 | 3 | Implement now |" in md
    assert "Beta — blocker:" in md


def test_no_country_measures_nothing_and_says_so(patched: dict[str, Any]) -> None:
    report = asyncio.run(cv.validate("competitor_analysis_lead_magnet", LISTING, FACTS, client_url="https://client.com.au", region="Melbourne"))
    assert all(next(x for x in s.metrics if x.key == "traffic").score is None for s in report.competitors)
    assert any("names no country" in n for n in report.notes)


def test_a_judge_failure_keeps_the_measured_scores(patched: dict[str, Any]) -> None:
    patched["judge"] = RuntimeError("overloaded")
    report = _run()
    assert report.judge == "unavailable"
    alpha = next(s for s in report.competitors if s.name == "Alpha")
    assert alpha.proof is not None and alpha.fit is not None
    assert any("reviewer was unavailable" in n for n in report.notes)


def test_business_level_is_a_ratio_not_a_guess() -> None:
    assert cv.business_level(1520, 40) == ("above", "38.0× the client's organic visits")
    assert cv.business_level(50, 40)[0] == "peer"
    assert cv.business_level(10, 40)[0] == "below"
    assert cv.business_level(None, 40) == (None, "")
    assert cv.business_level(100, None) == (None, "")
    assert cv.business_level(80, 0)[0] == "above"


def test_the_route_scores_any_competitor_stage(patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "phase": "phase1",
        "target_url": "https://client.com.au/",
        "location": "Melbourne, Australia",
        "service": "Social Media Marketing",
        "competitors": [
            {"rank": c.rank, "domain": c.domain, "name": c.name, "page_url": c.page_url,
             "verification_confidence": c.verification_confidence, "offering_summary": c.offering_summary, "category": c.category}
            for c in LISTING
        ],
    }
    with TestClient(app) as client:
        for asset in ("competitor_analysis_lead_magnet", "competitor_analysis_funnel", "competitor_analysis_webinars", "competitor_analysis_blog"):
            res = client.post(f"/pipeline/competitor/{asset}/validate", json=body)
            assert res.status_code == 200, res.text
            assert len(res.json()["competitors"]) == 3
        assert client.post("/pipeline/competitor/nope/validate", json=body).status_code == 404


def test_every_scanned_keyword_reaches_the_hover(patched: dict[str, Any]) -> None:
    report = _run()
    alpha = next(s for s in report.competitors if s.name == "Alpha")
    assert alpha.scanned_keywords and len(alpha.scanned_keywords) >= len(alpha.top_keywords)
    assert set(alpha.scanned_keywords[0]) == {"keyword", "position", "volume", "url"}
    assert report.scanned_limit == cv._RANKED_LIMIT
    assert isinstance(report.client_keywords, list)
