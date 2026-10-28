"""Content Marketing Strategy's keyword data, measured by DataForSEO instead of asked for.

Never reaches the live API: `DataForSEOClient` is replaced with a fake returning `DfsKeyword` rows.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.routers import pipeline
from app.services import content_keywords, dataforseo_client
from app.services.dataforseo_client import DfsKeyword

CATALOG = Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "assetCatalog.ts"


class FakeClient:
    calls: list[tuple[str, str, str]] = []

    def __init__(self, *_a, **_k) -> None:
        pass

    async def keywords(self, endpoint: str, seed: str, location_name: str, *, limit: int = 30, language: str = "English"):
        FakeClient.calls.append((endpoint, seed, location_name))
        if endpoint == dataforseo_client.SUGGESTIONS:
            return [
                DfsKeyword("social media marketing", 9900, 55, 12.5, "commercial"),
                DfsKeyword("social media marketing strategy", 1600, 40, 8.0, "informational"),
                DfsKeyword("social media marketing india", 5000, 30, 1.0, "commercial"),
            ]
        return [DfsKeyword("social media marketing tips", None, None, None, None), DfsKeyword("facebook", 99000, 90)]

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_dfs(monkeypatch: pytest.MonkeyPatch) -> list:
    FakeClient.calls = []
    monkeypatch.setattr(dataforseo_client, "DataForSEOClient", FakeClient)
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: True)
    return FakeClient.calls


@pytest.mark.parametrize(
    ("answer", "seed"),
    [
        ("Social Media Marketing — /social-media-marketing", "Social Media Marketing"),
        ('"Google Ads Management," trafficradius.com.au/google-ads-management', "Google Ads Management"),
        ("SEO (https://example.com/seo)", "SEO"),
        ("Meta Ads /meta-ads", "Meta Ads"),
    ],
)
def test_the_seed_is_the_service_without_its_page(answer: str, seed: str) -> None:
    assert content_keywords.seed_from_service(answer) == seed


@pytest.mark.parametrize("answer", [None, "", "N/A", "skip", "[[context: x]]"])
def test_blank_or_skipped_is_filled(answer: str | None) -> None:
    assert content_keywords.wants_fill(answer)


def test_a_supplied_export_is_kept() -> None:
    assert not content_keywords.wants_fill("keyword,volume\nseo melbourne,880")


def test_two_requests_filtered_and_measured_only(fake_dfs: list) -> None:
    data = asyncio.run(content_keywords.fetch("Social Media Marketing — /smm", "Melbourne, Australia"))
    assert [c[0] for c in fake_dfs] == [dataforseo_client.SUGGESTIONS, dataforseo_client.RELATED]
    assert {c[2] for c in fake_dfs} == {"Australia"}
    assert data.head is not None and data.head.volume == 9900
    kept = [r.keyword for r in data.rows]
    assert kept == ["social media marketing strategy", "social media marketing tips"]
    md = content_keywords.render_md(data)
    assert "DataForSEO (Google, Australia" in md
    assert "| social media marketing tips | — | — | — | — |" in md
    assert "| 0 |" not in md


def test_no_country_requests_nothing(fake_dfs: list) -> None:
    with pytest.raises(content_keywords.ContentKeywordError, match="does not name a country"):
        asyncio.run(content_keywords.fetch("Social Media Marketing", "Melbourne"))
    assert fake_dfs == []


def test_the_prepass_fills_the_field_and_reports_it(fake_dfs: list) -> None:
    answers = {
        content_keywords.SERVICE_FIELD_ID: "Social Media Marketing",
        content_keywords.REGION_FIELD_ID: "Australia",
        content_keywords.FIELD_ID: "N/A",
    }
    out, event = asyncio.run(pipeline._run_content_keyword_prepass(content_keywords.ASSET_ID, answers, {}))
    assert event is not None and event["type"] == "keyword_prepass" and not event["skipped"]
    assert out[content_keywords.FIELD_ID].startswith("Keyword data — DataForSEO")


def test_the_prepass_leaves_an_operator_export_and_other_stages_alone(fake_dfs: list) -> None:
    supplied = {content_keywords.FIELD_ID: "my keyword export", content_keywords.SERVICE_FIELD_ID: "SEO"}
    assert asyncio.run(pipeline._run_content_keyword_prepass(content_keywords.ASSET_ID, supplied, {})) == (supplied, None)
    blank = {content_keywords.FIELD_ID: "", content_keywords.SERVICE_FIELD_ID: "SEO"}
    assert asyncio.run(pipeline._run_content_keyword_prepass("plan_of_action", blank, {})) == (blank, None)
    assert fake_dfs == []


def test_a_failure_leaves_the_field_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: False)
    answers = {content_keywords.SERVICE_FIELD_ID: "SEO", content_keywords.REGION_FIELD_ID: "Australia"}
    out, event = asyncio.run(pipeline._run_content_keyword_prepass(content_keywords.ASSET_ID, answers, {}))
    assert out == answers
    assert event is not None and event["skipped"] and "not configured" in event["error"]


def test_the_catalog_never_asks_the_question() -> None:
    text = CATALOG.read_text(encoding="utf-8")
    start = text.index('asset_id: "content_marketing_strategy"')
    block = text[start : text.index("asset_id:", start + 10)]
    field = block[block.index('"keyword_search_volume_data_source_optional"') :][:600]
    assert 'source: "auto_from_context"' in field
    assert 'fallback: "treat_as_empty_if_missing"' in field
