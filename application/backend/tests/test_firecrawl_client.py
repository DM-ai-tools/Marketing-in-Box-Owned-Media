"""Tests for the Firecrawl gateway.

Nothing here touches the live API. The seam is the cached client: a fake stands in for the
`httpx.AsyncClient` and `_poll`'s sleep is patched out so the test suite does not actually wait.
"""

from __future__ import annotations

import types

import httpx
import pytest

from app.services import firecrawl_client


def test_is_configured_follows_the_env_var(monkeypatch):
    monkeypatch.delenv(firecrawl_client.API_KEY_ENV, raising=False)
    assert firecrawl_client.is_configured() is False
    monkeypatch.setenv(firecrawl_client.API_KEY_ENV, "fc-test")
    assert firecrawl_client.is_configured() is True


def test_client_refuses_to_build_without_a_key(monkeypatch):
    monkeypatch.delenv(firecrawl_client.API_KEY_ENV, raising=False)
    firecrawl_client.reset_client()
    with pytest.raises(firecrawl_client.FirecrawlNotConfigured):
        firecrawl_client._client()
    firecrawl_client.reset_client()


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.firecrawl.dev/v2/extract")
            response = httpx.Response(self.status_code, request=request, json=self._payload)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_extract_brand_tokens_polls_until_complete(monkeypatch):
    calls = {"polls": 0}

    async def post(path, json):
        assert path == "/extract"
        assert json["urls"] == ["https://client.com/"]
        return _Response({"success": True, "id": "job-1"})

    async def get(path):
        calls["polls"] += 1
        if calls["polls"] < 2:
            return _Response({"status": "processing"})
        return _Response(
            {
                "status": "completed",
                "data": {"primaryColor": "#123456", "logoUrl": "https://client.com/logo.png"},
                "tokensUsed": 42,
            }
        )

    fake = types.SimpleNamespace(post=post, get=get)
    monkeypatch.setattr(firecrawl_client, "_client", lambda: fake)
    monkeypatch.setattr(firecrawl_client, "_POLL_INTERVAL_SECONDS", 0)

    tokens = await firecrawl_client.extract_brand_tokens("https://client.com/")

    assert tokens.primary_color == "#123456"
    assert tokens.logo_url == "https://client.com/logo.png"
    assert tokens.available
    assert calls["polls"] == 2


@pytest.mark.asyncio
async def test_extract_brand_tokens_raises_on_a_failed_job(monkeypatch):
    async def post(path, json):
        return _Response({"success": True, "id": "job-2"})

    async def get(path):
        return _Response({"status": "failed"})

    fake = types.SimpleNamespace(post=post, get=get)
    monkeypatch.setattr(firecrawl_client, "_client", lambda: fake)

    with pytest.raises(firecrawl_client.FirecrawlError):
        await firecrawl_client.extract_brand_tokens("https://client.com/")


@pytest.mark.asyncio
async def test_extract_brand_tokens_raises_when_no_job_id_comes_back(monkeypatch):
    async def post(path, json):
        return _Response({"success": True})

    fake = types.SimpleNamespace(post=post, get=None)
    monkeypatch.setattr(firecrawl_client, "_client", lambda: fake)

    with pytest.raises(firecrawl_client.FirecrawlError):
        await firecrawl_client.extract_brand_tokens("https://client.com/")


@pytest.mark.asyncio
async def test_extract_reference_images_returns_hero_and_content_photos(monkeypatch):
    async def post(path, json):
        assert path == "/extract"
        assert json["schema"] is firecrawl_client._IMAGE_SCHEMA
        return _Response({"success": True, "id": "job-3"})

    async def get(path):
        return _Response(
            {
                "status": "completed",
                "data": {
                    "heroImageUrl": "https://client.com/hero.jpg",
                    "contentImageUrls": [
                        "https://client.com/team.jpg",
                        "https://client.com/office.jpg",
                        "https://client.com/hero.jpg",  # a duplicate of the hero — must not repeat
                    ],
                },
            }
        )

    fake = types.SimpleNamespace(post=post, get=get)
    monkeypatch.setattr(firecrawl_client, "_client", lambda: fake)
    monkeypatch.setattr(firecrawl_client, "_POLL_INTERVAL_SECONDS", 0)

    images = await firecrawl_client.extract_reference_images("https://client.com/")

    assert images.available
    assert images.hero_image_url == "https://client.com/hero.jpg"
    # Hero first, deduplicated against the content list — the order `image_briefs.py` hands to the
    # OpenAI edit endpoint. `content_image_urls` itself is not cross-deduped against the hero; only
    # `all_urls`, the property actually used downstream, is.
    assert images.all_urls == (
        "https://client.com/hero.jpg",
        "https://client.com/team.jpg",
        "https://client.com/office.jpg",
    )


@pytest.mark.asyncio
async def test_extract_reference_images_available_is_false_when_the_page_has_none(monkeypatch):
    async def post(path, json):
        return _Response({"success": True, "id": "job-4"})

    async def get(path):
        return _Response({"status": "completed", "data": {}})

    fake = types.SimpleNamespace(post=post, get=get)
    monkeypatch.setattr(firecrawl_client, "_client", lambda: fake)

    images = await firecrawl_client.extract_reference_images("https://client.com/")

    assert images.available is False
    assert images.all_urls == ()


# --------------------------------------------------------------------------------------
# Rate limits — one wait for the window Firecrawl names, never a stall
# --------------------------------------------------------------------------------------


def _limited(body: str = "", retry_after: str | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://api.firecrawl.dev/v2/extract")
    headers = {"retry-after": retry_after} if retry_after else {}
    return httpx.Response(429, request=request, headers=headers, text=body)


_LIVE_429 = (
    '{"success":false,"error":"Rate limit exceeded. Consumed (req/min): 3, Remaining (req/min): 0. '
    'Upgrade your plan at https://firecrawl.dev/pricing for increased rate limits or please retry after 37s"}'
)


def test_the_wait_is_read_from_the_header_or_the_body():
    assert firecrawl_client._rate_limit_wait(_limited(retry_after="12")) == 12
    assert firecrawl_client._rate_limit_wait(_limited(_LIVE_429)) == 37
    assert firecrawl_client._rate_limit_wait(_limited("slow down")) is None
    assert firecrawl_client._rate_limit_wait(_limited(retry_after="600")) is None  # longer than we wait


@pytest.mark.asyncio
async def test_a_rate_limited_submit_waits_once_then_succeeds(monkeypatch):
    posts = {"n": 0}
    slept: list[float] = []

    async def post(path, json):
        posts["n"] += 1
        return _limited(_LIVE_429) if posts["n"] == 1 else _Response({"success": True, "id": "job"})

    async def get(path):
        return _Response({"status": "completed", "data": {"instagramUrl": "https://instagram.com/acme"}})

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(firecrawl_client, "_client", lambda: types.SimpleNamespace(post=post, get=get))
    monkeypatch.setattr(firecrawl_client.asyncio, "sleep", sleep)

    links = await firecrawl_client.extract_social_links("https://acme.com")

    assert links.instagram_url == "https://instagram.com/acme"
    assert posts["n"] == 2 and slept == [37]


@pytest.mark.asyncio
async def test_a_second_rate_limit_is_reported_not_retried_again(monkeypatch):
    posts = {"n": 0}

    async def post(path, json):
        posts["n"] += 1
        return _limited(_LIVE_429)

    async def sleep(_seconds):
        return None

    monkeypatch.setattr(firecrawl_client, "_client", lambda: types.SimpleNamespace(post=post, get=None))
    monkeypatch.setattr(firecrawl_client.asyncio, "sleep", sleep)

    with pytest.raises(firecrawl_client.FirecrawlError, match="429"):
        await firecrawl_client.extract_social_links("https://acme.com")
    assert posts["n"] == 2
