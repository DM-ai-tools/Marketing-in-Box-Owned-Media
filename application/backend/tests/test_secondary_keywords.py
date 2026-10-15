"""Secondary / cluster terms for the Pillar Page, from Ahrefs and DataForSEO side by side.

Never reaches either live API. The filter is pinned against the rows the live APIs actually returned
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


def test_the_filter_drops_what_live_ahrefs_actually_returned_off_topic() -> None:
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


# ---------- suggest(): each provider on its own ----------


def _patch(monkeypatch: pytest.MonkeyPatch, ahrefs: Any, dfs: Any) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def fake_ahrefs(head: str, country: str) -> ProviderSuggestions:
        calls.append(("ahrefs", country))
        return ahrefs if isinstance(ahrefs, ProviderSuggestions) else ProviderSuggestions("ahrefs", "error", error="Ahrefs boom")

    async def fake_dfs(head: str, country: str) -> ProviderSuggestions:
        calls.append(("dataforseo", country))
        return dfs if isinstance(dfs, ProviderSuggestions) else ProviderSuggestions("dataforseo", "error", error="DataForSEO boom")

    monkeypatch.setattr(secondary_keywords, "_from_ahrefs", fake_ahrefs)
    monkeypatch.setattr(secondary_keywords, "_from_dataforseo", fake_dfs)
    return calls


@pytest.mark.asyncio
async def test_both_providers_are_asked_for_the_same_country(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = ProviderSuggestions("ahrefs", "ok", [kw("social media management")])
    calls = _patch(monkeypatch, ok, ProviderSuggestions("dataforseo", "ok", [kw("social media agency melbourne")]))
    result = await secondary_keywords.suggest(HEAD, "Melbourne, Australia")
    assert sorted(calls) == [("ahrefs", "Australia"), ("dataforseo", "Australia")]
    assert result.location == "Australia" and result.ahrefs.status == result.dataforseo.status == "ok"


@pytest.mark.asyncio
async def test_one_provider_failing_leaves_the_other_column(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, ProviderSuggestions("ahrefs", "ok", [kw("social media management")]), "fail")
    result = await secondary_keywords.suggest(HEAD, "Australia")
    assert result.ahrefs.keywords and result.dataforseo.status == "error" and "boom" in result.dataforseo.error


@pytest.mark.asyncio
async def test_a_region_with_no_country_asks_nothing_and_says_how_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(monkeypatch, "fail", "fail")
    result = await secondary_keywords.suggest(HEAD, "Sydney NSW")
    assert calls == []
    assert result.ahrefs.status == "error" and "Melbourne, Australia" in result.dataforseo.error


@pytest.mark.asyncio
async def test_unconfigured_providers_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AHREFS_API_KEY", *dataforseo_client.LOGIN_ENVS, *dataforseo_client.PASSWORD_ENVS):
        monkeypatch.delenv(name, raising=False)
    result = await secondary_keywords.suggest(HEAD, "Australia")
    assert (result.ahrefs.status, result.dataforseo.status) == ("unconfigured", "unconfigured")


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


# ---------- the route ----------


def test_the_route_returns_both_columns_and_never_errors_for_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, ProviderSuggestions("ahrefs", "ok", [kw("social media management", 1700)]), "fail")
    response = TestClient(app).post("/pipeline/keywords/secondary", json={"head_term": HEAD, "region": "Australia"})
    assert response.status_code == 200
    body = response.json()
    assert body["ahrefs"]["keywords"][0] == {"keyword": "social media management", "volume": 1700, "difficulty": 10, "cpc": None, "intent": None}
    assert body["dataforseo"]["status"] == "error"
