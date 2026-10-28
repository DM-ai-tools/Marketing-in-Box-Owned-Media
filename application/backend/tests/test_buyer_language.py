"""CRO's buyer-language suggestions. Never reaches DataForSEO, Anthropic or a live page: the client,
`_ask` and `scraper.read_direct` are faked."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import buyer_language as route
from app.services import buyer_language, dataforseo_client, scraper
from app.services.dataforseo_client import DfsKeyword, RankedKeyword

PAGE = (
    "Traffic Radius delivers omnichannel synergy for growth. We guarantee page one rankings in 30 days. "
    "Our SEO team helps Melbourne businesses get more customers."
)


class FakeDfs:
    calls: list[tuple[str, str]] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def keywords(self, endpoint: str, seed: str, country: str, *, limit: int = 30, language: str = "English"):
        FakeDfs.calls.append((endpoint, seed))
        return [
            DfsKeyword("how to get more customers", 1300, 20, trend=12),
            DfsKeyword("rank higher on google", 880, 30),
            DfsKeyword("seo melbourne", 2400, 40),
            DfsKeyword("seo services india", 5000, 30),
        ]

    async def ideas(self, seeds: list[str], country: str, *, limit: int = 50, language: str = "English"):
        FakeDfs.calls.append(("ideas", ",".join(seeds)))
        return [DfsKeyword("google ranking factors", 1900, 50)]

    async def ranked_keywords(self, target: str, country: str, *, limit: int = 60, language: str = "English"):
        FakeDfs.calls.append(("ranked", target))
        return [RankedKeyword("get more website traffic", 720, 25, 8, target)]

    async def overview(self, words: list[str], country: str):
        FakeDfs.calls.append(("overview", ",".join(words)))
        volumes = {"omnichannel synergy": None, "marketing across channels": 390}
        return {w: DfsKeyword(w, volumes.get(w), 20) for w in words if w in volumes}

    async def aclose(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_live_page_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """`scrape_page` falls back to Context.dev and Claude's reader, both paid. A test that forgets to
    fake it fails here instead of spending: this happened once while the service moved readers."""

    async def refuse(url: str):
        raise AssertionError(f"a test reached a live page read for {url}")

    monkeypatch.setattr(scraper, "scrape_page", refuse)
    monkeypatch.setattr(scraper, "read_direct", refuse)


@pytest.fixture
def dfs(monkeypatch: pytest.MonkeyPatch) -> list:
    FakeDfs.calls = []
    monkeypatch.setattr(dataforseo_client, "DataForSEOClient", FakeDfs)
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: True)
    return FakeDfs.calls


PROFILE = (
    "- core || seo\n"
    "- core || ai seo\n"
    "- facet || ai seo tools\n"
    "- facet || website traffic\n"  # generic: never a term
    "- facet || seo jobs\n"  # the trade, not the buyer: never a term
    "- anchor || google\n"
    "- distinct || chatgpt\n"
)


def test_clusters_are_about_the_service_and_hold_only_measured_searches(dfs: list, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    async def ask(prompt: str, _usage: Any) -> str:
        prompts.append(prompt)
        if "SEARCHES" not in prompt:
            return PROFILE
        pool = [line.split(". ", 1) for line in prompt.split("SEARCHES\n", 1)[1].splitlines()]
        number = {kw: n for n, kw in pool}
        for off in ("how to get more customers", "kling ai", "seo jobs melbourne"):
            assert off not in number, off
        return (
            f"- ai for seo || {number['how to use ai for seo']}, {number['ai seo tools']}, 999\n"
            f"- rank higher on google || {number['rank higher on google']}\n"
            "- a name with no members || 0\n"
        )

    async def keywords(self: Any, endpoint: str, seed: str, country: str, *, limit: int = 30, language: str = "English"):
        FakeDfs.calls.append((endpoint, seed))
        return [
            DfsKeyword("how to get more customers", 1300, 20, trend=12),
            DfsKeyword("rank higher on google", 880, 30),
            DfsKeyword("how to use ai for seo", 320, 30),
            DfsKeyword("ai seo tools", 140, 20),
            DfsKeyword("kling ai", 14800, 20),
            DfsKeyword("seo jobs melbourne", 590, 20),
            DfsKeyword("seo services india", 5000, 30),
        ]

    monkeypatch.setattr(FakeDfs, "keywords", keywords)
    monkeypatch.setattr(buyer_language, "_ask", ask)
    result = asyncio.run(
        buyer_language.outcome_words(
            ["AI-Optimised SEO", "Digital-Marketing - AI optimised SEO"],
            "Melbourne, Australia",
            landing_url="https://trafficradius.com.au/seo/",
            run_keywords=[{"keyword": "stop wasting ad spend", "volume": 90, "difficulty": 10}],
        )
    )
    # Searches naming the service are kept; what sets it apart (AI) leads.
    assert [(c.name, c.distinct, [p.keyword for p in c.phrases]) for c in result.clusters] == [
        ("ai for seo", True, ["how to use ai for seo", "ai seo tools"]),
        ("rank higher on google", False, ["rank higher on google"]),
    ]
    # "Digital-Marketing - AI optimised SEO" is the same service under its parent: searched once.
    # The seeds are the core names then the facets.
    assert sorted(c for c in dfs if c[0] != "ranked") == sorted(
        [
            (dataforseo_client.SUGGESTIONS, "AI-Optimised SEO"),
            (dataforseo_client.RELATED, "AI-Optimised SEO"),
            (dataforseo_client.SUGGESTIONS, "seo"),
            (dataforseo_client.SUGGESTIONS, "ai seo"),
            (dataforseo_client.SUGGESTIONS, "ai seo tools"),
            ("ideas", "seo,ai seo,ai seo tools"),
        ]
    )
    assert ("ranked", "https://trafficradius.com.au/seo/") in dfs
    judge = prompts[1]
    assert "stop wasting ad spend" not in judge  # the run's report is gated like everything else
    assert "india" not in judge  # another country's market never is
    assert "Its topics: ai seo tools" in judge
    assert any("left out for not being about AI-Optimised SEO" in n for n in result.notes)
    assert any("not in DataForSEO's results" in n for n in result.notes)
    assert result.location == "Australia"


def test_the_profile_keeps_the_services_topics_and_drops_generic_ones() -> None:
    profile = buyer_language.parse_profile(PROFILE, ["AI-Optimised SEO"])
    assert profile.core == ["seo", "ai seo"] and profile.facets == ["ai seo tools"]
    assert profile.anchors == ["seo", "ai seo", "ai seo tools", "chatgpt", "ai", "optimised", "google"]
    assert profile.distinct == ["chatgpt", "ai", "optimised"]
    assert profile.seeds == ["seo", "ai seo", "ai seo tools"]


def test_a_label_followed_by_a_plain_list_is_read_whole() -> None:
    """Measured live: the model wrote one `- facet ||` line and listed the rest bare."""
    raw = "- core || social media marketing\n- facet || instagram ads\nmeta ads\n- facebook ads, tiktok ads\n- distinct || instagram\n"
    profile = buyer_language.parse_profile(raw, ["Social Media Marketing"])
    assert profile.facets == ["instagram ads", "meta ads", "facebook ads", "tiktok ads"]
    # The name is its own core, so there is no qualifier and nothing is "specific to this service".
    assert profile.distinct == []
    assert profile.seeds[:3] == ["social media marketing", "instagram ads", "meta ads"]


def test_a_parent_service_prefix_is_dropped() -> None:
    assert buyer_language._clean_services(["Digital-Marketing - AI optimised SEO", "AI-Optimised SEO", "SEO > Local SEO"]) == [
        "AI optimised SEO",
        "Local SEO",
    ]


@pytest.mark.parametrize(
    "search,kept",
    [
        ("grow my business", False),
        ("increase online sales", False),
        ("get more website traffic", False),
        ("grow online business", False),
        ("rank higher on google", True),
        ("show up in ai overviews", True),  # plural
        ("how to rank in ChatGPT", True),
        ("chatgpt ranking factors", True),
        ("ranking factors", True),  # ranking is rank
        ("rankings", True),
        ("frank reviews", False),  # a whole word, never a part of one
        ("kling ai", False),  # a short word alone is too common: it needs a core name beside it
    ],
)
def test_the_relevance_gate_needs_an_anchor(search: str, kept: bool) -> None:
    assert buyer_language.on_service(search, ["google", "rank", "chatgpt", "ai overview", "ai"], ["seo", "ai seo"]) is kept


def test_generic_business_words_are_never_anchors() -> None:
    for term in ("website traffic", "more customers", "grow my business", "online sales", "leads"):
        assert buyer_language.is_generic(term), term
    for term in ("organic traffic", "google", "ai overview", "burst pipe"):
        assert not buyer_language.is_generic(term), term


def test_without_a_profile_the_service_words_anchor_the_searches(dfs: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ask(prompt: str, _usage: Any) -> str:
        if "SEARCHES" not in prompt:
            raise RuntimeError("overloaded")
        return "- ranked || 1\n"

    monkeypatch.setattr(buyer_language, "_ask", ask)
    assert buyer_language.fallback_anchors(["AI-Optimised SEO Services"]) == ["ai", "optimised", "seo"]
    result = asyncio.run(buyer_language.outcome_words(["SEO Services"], "Australia"))
    assert any("could not be worked out" in n for n in result.notes)
    assert all(buyer_language.on_service(p.keyword, ["seo"]) for c in result.clusters for p in c.phrases)


def test_nothing_about_the_service_returns_no_clusters_and_says_so(dfs: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def ask(prompt: str, _usage: Any) -> str:
        assert "SEARCHES" not in prompt, "the judge is never asked about off-service searches"
        return "- anchor || roof\n"

    monkeypatch.setattr(buyer_language, "_ask", ask)
    result = asyncio.run(buyer_language.outcome_words(["Roof Repairs"], "Australia"))
    assert result.clusters == []
    assert any("are about Roof Repairs's results" in n for n in result.notes)


def test_no_country_or_no_service_says_why(dfs: list) -> None:
    with pytest.raises(buyer_language.BuyerLanguageError, match="does not name a country"):
        asyncio.run(buyer_language.outcome_words(["SEO"], "Melbourne"))
    with pytest.raises(buyer_language.BuyerLanguageError, match="No service"):
        asyncio.run(buyer_language.outcome_words([], "Australia"))
    assert dfs == []


def test_words_to_avoid_keep_only_what_is_on_the_page_and_measured_jargon(dfs: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def read(_url: str):
        return scraper.ScrapedPage(
            url=_url, final_url=_url, title=None, meta_description=None, text=PAGE, word_count=30, truncated=False
        )

    async def ask(_prompt: str, _usage: Any) -> str:
        return (
            "- jargon || omnichannel synergy || marketing across channels\n"
            "- jargon || SEO || search engine optimisation\n"
            '- jargon || "holistic paradigm" || joined-up marketing\n'
            "- claim || guarantee page one rankings || Rankings cannot be guaranteed.\n"
            "- claim || #1 agency || Unsupported superlative.\n"
        )

    monkeypatch.setattr(scraper, "scrape_page", read)
    monkeypatch.setattr(buyer_language, "_ask", ask)
    result = asyncio.run(
        buyer_language.words_to_avoid(
            ["SEO"], "Melbourne, Australia", landing_url="https://x.com.au"
        )
    )
    by_kind = {(i.kind, i.term) for i in result.avoid}
    # Competitor names are never suggested (the operator asked for them off the card).
    assert not any(kind == "competitor" for kind, _ in by_kind)
    assert ("jargon", "omnichannel synergy") in by_kind
    assert ("claim", "guarantee page one rankings") in by_kind
    # Not on the page: never suggested.
    assert all(t not in {"holistic paradigm", "#1 agency"} for _, t in by_kind)
    jargon = next(i for i in result.avoid if i.term == "omnichannel synergy")
    assert jargon.alternative_volume == 390 and jargon.term_volume is None
    assert any("not on the page" in n for n in result.notes)


def test_jargon_buyers_search_more_than_the_alternative_is_not_jargon(dfs: list, monkeypatch: pytest.MonkeyPatch) -> None:
    async def overview(self: Any, words: list[str], country: str):
        return {"seo": DfsKeyword("seo", 9000, 60), "search engine optimisation": DfsKeyword("search engine optimisation", 800, 50)}

    monkeypatch.setattr(FakeDfs, "overview", overview)
    items = [buyer_language.AvoidItem("SEO", "jargon", alternative="search engine optimisation")]
    kept, _ = asyncio.run(buyer_language._measure_jargon(items, "Australia"))
    assert kept == []


def test_an_unreadable_page_says_so_and_suggests_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(_url: str):
        raise scraper.ScrapeError("blocked an automated read")

    monkeypatch.setattr(scraper, "scrape_page", fail)
    result = asyncio.run(buyer_language.words_to_avoid([], "", landing_url="https://x.com.au"))
    assert result.avoid == []
    assert any("could not be read" in n for n in result.notes)


def test_the_route_reads_the_run_and_returns_the_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def reads(run_id: str | None):
        seen["run"] = run_id
        return [{"keyword": "more leads", "volume": 100}]

    async def avoid(services, region, **kw):
        seen.update(kw)
        return buyer_language.BuyerLanguage(mode="avoid", avoid=[buyer_language.AvoidItem("synergy", "jargon")])

    async def outcome(services, region, **kw):
        raise buyer_language.BuyerLanguageError("No service is on record for this session.")

    monkeypatch.setattr(route, "_run_keywords", reads)
    monkeypatch.setattr(route.buyer_language, "words_to_avoid", avoid)
    monkeypatch.setattr(route.buyer_language, "outcome_words", outcome)
    client = TestClient(app)
    ok = client.post("/pipeline/keywords/buyer-language", json={"mode": "avoid", "run_id": "r1", "landing_url": "https://x.com.au"})
    assert ok.status_code == 200 and ok.json()["avoid"][0]["term"] == "synergy"
    # The avoid card reads nothing off the run: no keyword report, no competitor names.
    assert "run" not in seen and "competitor_names" not in seen and seen["landing_url"] == "https://x.com.au"
    bad = client.post("/pipeline/keywords/buyer-language", json={"mode": "outcome"})
    assert bad.status_code == 422 and "No service" in bad.json()["detail"]


def test_service_name_spelling_does_not_matter() -> None:
    assert buyer_language.names_a_service("ai optimized seo agency", ["AI-Optimised SEO"])
    assert buyer_language._clean_services(["AI-Optimised SEO", "ai optimized seo"]) == ["AI-Optimised SEO"]
