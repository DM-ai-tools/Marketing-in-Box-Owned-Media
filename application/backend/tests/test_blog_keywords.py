"""Better keywords for a blog post, measured against the client's page and the competing pages.

No live calls: a fake DataForSEO client return rows shaped like the live responses read
on 2026-10-20 (`ranked_keywords` rows with a position and URL, organic SERP results). The real run
that shaped these rules: "ai seo services" for ClickTrends, Australia.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import blog_keywords, dataforseo_client
from app.services.dataforseo_client import DfsKeyword, RankedKeyword, SerpResult

REF = "https://clicktrends.com.au/seo/"


def rk(keyword, volume, kd, position, url, intent="commercial"):
    return RankedKeyword(keyword, volume, kd, position, url, None, intent, None)


RANKED = {
    "https://clicktrends.com.au/seo/": [rk("wordpress web designer", 1000, 0, 1, REF), rk("seo services melbourne", 880, 30, 34, REF)],
    "https://studiohawk.com.au/seo-services/ai-seo/": [
        rk("ai seo agency", 390, 0, 2, "https://studiohawk.com.au/seo-services/ai-seo/"),
        rk("ai seo services", 320, 0, 6, "https://studiohawk.com.au/seo-services/ai-seo/"),
    ],
    "https://www.apwebsolutions.com.au/seo-services/ai-seo/": [
        rk("ai seo", 1000, 41, 17, "https://www.apwebsolutions.com.au/seo-services/ai-seo/", "informational"),
        rk("ai seo agency", 390, 0, 33, "https://www.apwebsolutions.com.au/seo-services/ai-seo/"),
    ],
}


class FakeDfs:
    instances: list["FakeDfs"] = []

    def __init__(self, *a, **k):
        self.ranked_targets: list[str] = []
        self.serp_calls = 0
        FakeDfs.instances.append(self)

    async def serp(self, keyword, location):
        self.serp_calls += 1
        return [
            SerpResult(1, "clicktrends.com.au", "https://clicktrends.com.au/ai-seo/"),
            SerpResult(2, "www.apwebsolutions.com.au", "https://www.apwebsolutions.com.au/seo-services/ai-seo/"),
            SerpResult(3, "studiohawk.com.au", "https://studiohawk.com.au/seo-services/ai-seo/"),
        ]

    async def ranked_keywords(self, target, location, limit=60):
        self.ranked_targets.append(target)
        return RANKED.get(target, [])

    async def keywords(self, endpoint, seed, location, limit=30):
        return [
            DfsKeyword("ai seo services melbourne", 20, None, None, "commercial"),
            DfsKeyword("seo services in usa", 30, None, None, "commercial"),
            DfsKeyword("chatgpt", 90000, 80, None, "informational"),
        ]

    async def overview(self, keywords, location):
        return {k.lower(): DfsKeyword(k, 320, 5) for k in keywords}

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeDfs.instances.clear()
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: True)
    monkeypatch.setattr(dataforseo_client, "DataForSEOClient", FakeDfs)


async def _suggest(**kw):
    return await blog_keywords.suggest("ai seo services", "Melbourne, Australia", reference_url=REF, current_keyword="ai seo services", **kw)


@pytest.mark.asyncio
async def test_competitors_are_the_pages_ranking_for_the_topic_never_the_client():
    r = await _suggest()
    assert r.competitor_source == "serp"
    assert [p.domain for p in r.compared] == ["clicktrends.com.au", "apwebsolutions.com.au", "studiohawk.com.au"]
    assert r.compared[0].role == "reference"
    assert "https://clicktrends.com.au/ai-seo/" not in FakeDfs.instances[0].ranked_targets


@pytest.mark.asyncio
async def test_the_approved_competitor_listing_is_preferred_over_a_search():
    r = await _suggest(competitor_urls=["https://studiohawk.com.au/seo-services/ai-seo/"])
    assert r.competitor_source == "listing" and FakeDfs.instances[0].serp_calls == 0
    assert [p.domain for p in r.compared if p.role == "competitor"] == ["studiohawk.com.au"]


@pytest.mark.asyncio
async def test_a_keyword_competitors_rank_for_and_the_client_does_not_is_a_gap_and_ranks_first():
    r = await _suggest()
    by = {k.keyword: k for k in r.primary}
    assert by["ai seo agency"].status == "gap"
    assert {c.domain for c in by["ai seo agency"].competitors} == {"studiohawk.com.au", "apwebsolutions.com.au"}
    assert r.primary[0].keyword in {"ai seo", "ai seo agency"}
    # The current keyword is measured the same way, for the comparison — and never suggested over itself.
    assert r.current is not None and r.current.keyword == "ai seo services"
    assert "ai seo services" not in by


@pytest.mark.asyncio
async def test_the_client_s_own_rankings_set_improve_and_owned():
    r = await _suggest()
    all_rows = {k.keyword: k for k in [*r.primary, *r.secondary]}
    assert all_rows["seo services melbourne"].status == "improve" and all_rows["seo services melbourne"].reference_position == 34
    # "wordpress web designer" is off-topic for an AI SEO blog, whatever it earns the client.
    assert "wordpress web designer" not in all_rows


@pytest.mark.asyncio
async def test_off_topic_and_other_country_terms_are_dropped():
    r = await _suggest()
    keywords = {k.keyword for k in [*r.primary, *r.secondary]}
    assert "chatgpt" not in keywords
    assert "seo services in usa" not in keywords  # measured live: an abbreviation the full-name filter missed
    assert "ai seo services melbourne" in keywords


@pytest.mark.asyncio
async def test_the_current_keyword_is_measured_when_no_read_reached_it():
    r = await _suggest()
    assert r.current is not None and r.current.dataforseo_volume == 320


@pytest.mark.asyncio
async def test_a_reference_page_ranking_for_nothing_on_topic_says_so():
    r = await blog_keywords.suggest("ai seo services", "Australia", reference_url="https://clicktrends.com.au/nothing/")
    assert any("ranks for nothing on this topic" in n for n in r.notes)


@pytest.mark.asyncio
async def test_no_country_requests_nothing():
    r = await blog_keywords.suggest("ai seo services", "Melbourne", reference_url=REF)
    assert r.dataforseo.status == "error" and "country" in r.dataforseo.error and not FakeDfs.instances


def test_the_route_reads_the_reference_and_listing_from_the_run(monkeypatch):
    from app.routers import pipeline

    seen = {}

    async def sources(run_id, website_url):
        seen["args"] = (run_id, website_url)
        return REF, ["https://studiohawk.com.au/seo-services/ai-seo/"]

    monkeypatch.setattr(pipeline, "_blog_keyword_sources", sources)
    from app.main import app

    body = TestClient(app).post(
        "/pipeline/keywords/blog",
        json={"seed": "ai seo services", "region": "Melbourne, Australia", "current_keyword": "ai seo services", "run_id": "r1", "website_url": "https://clicktrends.com.au/"},
    ).json()
    assert seen["args"] == ("r1", "https://clicktrends.com.au/")
    assert body["competitor_source"] == "listing"
    assert body["primary"][0]["volume"] is not None and "status" in body["primary"][0]


@pytest.mark.asyncio
async def test_keywords_for_another_city_are_dropped(monkeypatch):
    """Measured live: StudioHawk's /seo-sydney/ ranked top 10 for "ai seo services" in Australia, and
    its Sydney keywords led a Melbourne client's suggestions."""
    sydney = [rk("ai seo services sydney", 880, 14, 4, "https://studiohawk.com.au/seo-sydney/"),
              rk("ai seo services melbourne", 90, 5, 9, "https://studiohawk.com.au/seo-sydney/")]
    monkeypatch.setitem(RANKED, "https://studiohawk.com.au/seo-services/ai-seo/", sydney)
    r = await _suggest()
    keywords = {k.keyword for k in [*r.primary, *r.secondary]}
    assert "ai seo services sydney" not in keywords and "ai seo services melbourne" in keywords


@pytest.mark.asyncio
async def test_the_current_keyword_reads_as_the_operator_has_it(monkeypatch):
    async def singular(self, endpoint, seed, location, limit=30):
        return [DfsKeyword("ai seo service", 320, 0, None, "commercial")]

    monkeypatch.setattr(FakeDfs, "keywords", singular)
    r = await _suggest()
    assert r.current.keyword == "ai seo services"


@pytest.mark.asyncio
async def test_a_competitor_home_page_is_read_as_that_page_never_its_whole_site():
    await _suggest(competitor_urls=["https://studiohawk.com.au/"])
    targets = FakeDfs.instances[0].ranked_targets
    assert "https://studiohawk.com.au/" in targets and "studiohawk.com.au" not in targets
    # The client's own home page is read site-wide, so a keyword it ranks for elsewhere counts.
    await blog_keywords.suggest("ai seo services", "Australia", reference_url="https://clicktrends.com.au/")
    assert "clicktrends.com.au" in FakeDfs.instances[-1].ranked_targets
