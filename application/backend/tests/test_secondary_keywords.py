"""Secondary / cluster terms for the Pillar Page, from DataForSEO.

Never reaches the live API. The filter is pinned against the rows live providers actually returned
for "social media marketing agency" in Australia; DataForSEO's client against its real nesting.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import dataforseo_client, secondary_keywords
from app.services.secondary_keywords import ProviderSuggestions, SecondaryKeyword

HEAD = "social media marketing agency"


def kw(text: str, volume: int | None = 100) -> SecondaryKeyword:
    return SecondaryKeyword(text, volume, 10)


# ---------- the shared filter ----------


def test_the_filter_drops_what_a_live_provider_actually_returned_off_topic() -> None:
    rows = [
        kw("social", 3100),
        kw("agency", 1700),
        kw("social club", 1300),
        kw("digital marketing", 11000),
        kw("social media marketing agency", 900),  # the head term itself
        kw("social media marketing agencies", 300),  # its plural
        kw("social media management", 1700),
        kw("social media advertising", 1000),
    ]
    kept = [k.keyword for k in secondary_keywords.filter_suggestions(HEAD, rows, "Australia")]
    assert kept == ["social media management", "social media advertising"]


def test_another_countrys_market_is_dropped_but_this_countrys_cities_are_kept() -> None:
    rows = [kw("social media marketing agency india"), kw("social media marketing agency sydney"), kw("social media marketing agency australia")]
    kept = [k.keyword for k in secondary_keywords.filter_suggestions(HEAD, rows, "Australia")]
    assert kept == ["social media marketing agency australia", "social media marketing agency sydney"]


def test_highest_volume_first_and_an_unmeasured_volume_stays_none() -> None:
    rows = [kw("social media strategy", None), kw("social media management", 1700), kw("social media services", 450)]
    out = secondary_keywords.filter_suggestions(HEAD, rows)
    assert [k.keyword for k in out] == ["social media management", "social media services", "social media strategy"]
    assert out[-1].volume is None


def test_the_list_is_capped() -> None:
    rows = [kw(f"social media idea {i}", 1000 - i) for i in range(40)]
    assert len(secondary_keywords.filter_suggestions(HEAD, rows)) == secondary_keywords.MAX_PER_PROVIDER


# ---------- suggest() ----------


def _patch(monkeypatch: pytest.MonkeyPatch, dfs: Any) -> list[str]:
    calls: list[str] = []

    async def fake_dfs(head: str, country: str) -> ProviderSuggestions:
        calls.append(country)
        return dfs if isinstance(dfs, ProviderSuggestions) else ProviderSuggestions("dataforseo", "error", error="DataForSEO boom")

    monkeypatch.setattr(secondary_keywords, "_from_dataforseo", fake_dfs)
    return calls


@pytest.mark.asyncio
async def test_dataforseo_is_asked_for_the_clients_country(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch, ProviderSuggestions("dataforseo", "ok", [kw("social media agency melbourne")]))
    result = await secondary_keywords.suggest(HEAD, "Melbourne, Australia")
    assert calls == ["Australia"]
    assert result.location == "Australia" and result.dataforseo.status == "ok"


@pytest.mark.asyncio
async def test_a_failure_is_a_status_and_a_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, "fail")
    result = await secondary_keywords.suggest(HEAD, "Australia")
    assert result.dataforseo.status == "error" and "boom" in result.dataforseo.error


@pytest.mark.asyncio
async def test_a_region_with_no_country_asks_nothing_and_says_how_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch, "fail")
    result = await secondary_keywords.suggest(HEAD, "Sydney NSW")
    assert calls == []
    assert result.dataforseo.status == "error" and "Melbourne, Australia" in result.dataforseo.error


@pytest.mark.asyncio
async def test_unconfigured_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*dataforseo_client.LOGIN_ENVS, *dataforseo_client.PASSWORD_ENVS):
        monkeypatch.delenv(name, raising=False)
    result = await secondary_keywords.suggest(HEAD, "Australia")
    assert result.dataforseo.status == "unconfigured"


# ---------- the DataForSEO client, against its real nesting ----------


def _dfs_transport(seen: list[dict], body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, "body": json.loads(request.content), "auth": request.headers.get("authorization")})
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _ok(items: list[dict]) -> dict:
    return {"tasks": [{"status_code": 20000, "status_message": "Ok.", "result": [{"items": items}]}]}


@pytest.mark.asyncio
async def test_suggestions_and_related_are_both_read() -> None:
    seen: list[dict] = []
    flat = {"keyword": "social media agency melbourne", "keyword_info": {"search_volume": 320, "cpc": 16.2},
            "keyword_properties": {"keyword_difficulty": 16}, "search_intent_info": {"main_intent": "commercial"}}
    nested = {"keyword_data": {"keyword": "marketing agency for social media", "keyword_info": {"search_volume": None},
                               "keyword_properties": {"keyword_difficulty": 30}}, "depth": 1}
    client = dataforseo_client.DataForSEOClient("u", "p", transport=_dfs_transport(seen, _ok([flat, nested])))
    rows = await client.keywords(dataforseo_client.RELATED, HEAD, "Australia")
    await client.aclose()
    assert rows[0] == dataforseo_client.DfsKeyword("social media agency melbourne", 320, 16, 16.2, "commercial")
    assert rows[1].keyword == "marketing agency for social media" and rows[1].volume is None
    task = seen[0]["body"][0]
    assert task["location_name"] == "Australia" and task["depth"] == 1 and seen[0]["auth"].startswith("Basic ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "words"),
    [
        (401, {}, "rejected the login"),
        (200, {"tasks": [{"status_code": 40200, "status_message": "Payment Required."}]}, "out of balance"),
        (200, {"tasks": [{"status_code": 40501, "status_message": "Invalid Field: 'location_name'."}]}, "40501"),
    ],
)
async def test_a_200_can_still_be_a_failure(status: int, body: dict, words: str) -> None:
    client = dataforseo_client.DataForSEOClient("u", "p", transport=_dfs_transport([], body, status))
    with pytest.raises(dataforseo_client.DataForSEOError, match=words):
        await client.keywords(dataforseo_client.SUGGESTIONS, HEAD, "Australia")
    await client.aclose()


@pytest.mark.asyncio
async def test_overview_and_the_broad_class_read_their_own_endpoints() -> None:
    """The keyword pipeline's two other calls: `keyword_overview` for exact keywords (a list body)
    and `keyword_ideas` for the broad class (a seed list, not a single keyword)."""
    seen: list[dict] = []
    row = {"keyword": "Social Media Agency", "keyword_info": {"search_volume": 880}, "keyword_properties": {"keyword_difficulty": 22}}
    client = dataforseo_client.DataForSEOClient("u", "p", transport=_dfs_transport(seen, _ok([row])))
    measured = await client.overview(["social media agency", "social media agency", " "], "Australia")
    broad = await client.expand(HEAD, "broad", "Australia", 40)
    phrase = await client.expand(HEAD, "phrase", "Australia", 40)
    await client.aclose()
    assert measured["social media agency"].volume == 880
    assert seen[0]["path"].endswith("keyword_overview/live") and seen[0]["body"][0]["keywords"] == ["social media agency"]
    assert seen[1]["path"].endswith("keyword_ideas/live") and seen[1]["body"][0]["keywords"] == [HEAD]
    assert seen[2]["path"].endswith("keyword_suggestions/live") and seen[2]["body"][0]["keyword"] == HEAD
    assert broad[0].volume == 880 and phrase[0].difficulty == 22


@pytest.mark.asyncio
async def test_ideas_sends_every_seed_in_one_request() -> None:
    """Buyer language's outcome searches: one `keyword_ideas` request over all the seeds, deduped,
    and nothing sent when there are none."""
    seen: list[dict] = []
    row = {"keyword": "how to rank in chatgpt", "keyword_info": {"search_volume": 320}}
    client = dataforseo_client.DataForSEOClient("u", "p", transport=_dfs_transport(seen, _ok([row])))
    rows = await client.ideas(["rank in chatgpt", "ai overviews", "rank in chatgpt", " "], "Australia", limit=50)
    assert await client.ideas([], "Australia") == []
    await client.aclose()
    assert len(seen) == 1 and seen[0]["path"].endswith("keyword_ideas/live")
    assert seen[0]["body"][0]["keywords"] == ["rank in chatgpt", "ai overviews"]
    assert seen[0]["body"][0]["limit"] == 50
    assert rows[0].keyword == "how to rank in chatgpt" and rows[0].volume == 320


@pytest.mark.asyncio
async def test_ranked_overview_reads_result_level_totals() -> None:
    """`metrics.organic.count` / `etv` are on the result, not the items. `_post` used to throw
    them away, so competitor validation could only sum the sampled rows."""
    seen: list[dict] = []
    item = {
        "keyword_data": {"keyword": "ai seo melbourne", "keyword_info": {"search_volume": 320}},
        "ranked_serp_element": {"serp_item": {"rank_absolute": 4, "url": "https://rival.com.au/ai-seo/", "etv": 90.0}},
    }
    body = {
        "tasks": [{
            "status_code": 20000,
            "result": [{
                "total_count": 4821,
                "metrics": {"organic": {"count": 4821, "etv": 15230.5}},
                "items": [item],
            }],
        }]
    }
    client = dataforseo_client.DataForSEOClient("u", "p", transport=_dfs_transport(seen, body))
    overview = await client.ranked_overview("rival.com.au", "Australia", limit=50)
    pages = await client.ranking_page_count("https://www.rival.com.au/ai-seo/", "Australia")
    await client.aclose()
    assert overview.keyword_count == 4821 and overview.traffic == 15230.5
    assert overview.keywords[0].keyword == "ai seo melbourne" and overview.keywords[0].position == 4
    assert seen[0]["path"].endswith("ranked_keywords/live")
    assert seen[1]["path"].endswith("relevant_pages/live") and seen[1]["body"][0]["target"] == "rival.com.au"
    assert seen[1]["body"][0]["limit"] == 1
    # The relevant_pages mock reuses the same body, so total_count 4821 is what comes back.
    assert pages == 4821


# ---------- the route ----------


def test_the_route_returns_the_suggestions_and_never_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, ProviderSuggestions("dataforseo", "ok", [kw("social media management", 1700)]))
    response = TestClient(app).post("/pipeline/keywords/secondary", json={"head_term": HEAD, "region": "Australia"})
    assert response.status_code == 200
    body = response.json()
    assert "ahrefs" not in body
    assert body["dataforseo"]["keywords"][0] == {"keyword": "social media management", "volume": 1700, "difficulty": 10, "cpc": None, "intent": None, "trend": None}
