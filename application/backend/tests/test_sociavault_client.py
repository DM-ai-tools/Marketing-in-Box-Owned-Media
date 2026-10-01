"""Tests for the SociaVault gateway.

Nothing here touches the live API. The seam is the cached client: a fake stands in for the
`httpx.AsyncClient`, same pattern as `test_firecrawl_client.py` and `test_brandfetch_client.py`.
"""

from __future__ import annotations

import types

import httpx
import pytest

from app.services import sociavault_client


def test_is_configured_follows_the_env_var(monkeypatch):
    monkeypatch.delenv(sociavault_client.API_KEY_ENV, raising=False)
    assert sociavault_client.is_configured() is False
    monkeypatch.setenv(sociavault_client.API_KEY_ENV, "sk_live_test")
    assert sociavault_client.is_configured() is True


def test_client_refuses_to_build_without_a_key(monkeypatch):
    monkeypatch.delenv(sociavault_client.API_KEY_ENV, raising=False)
    sociavault_client.reset_client()
    with pytest.raises(sociavault_client.SociaVaultNotConfigured):
        sociavault_client._client()
    sociavault_client.reset_client()


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.sociavault.com/v1/scrape/instagram/posts")
            response = httpx.Response(self.status_code, request=request, json=self._payload)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_fetch_recent_posts_rejects_an_unsupported_platform():
    with pytest.raises(sociavault_client.UnsupportedPlatform):
        await sociavault_client.fetch_recent_posts("tiktok", "someone", limit=10)


@pytest.mark.asyncio
async def test_instagram_posts_are_normalised_and_paginated(monkeypatch):
    calls = {"n": 0}

    async def get(path, params):
        calls["n"] += 1
        assert path == "/scrape/instagram/posts"
        assert params["handle"] == "trafficradius"
        if calls["n"] == 1:
            assert "next_max_id" not in params
            return _Response(
                {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "pk": "1",
                                "code": "AAA",
                                "taken_at": 1700000000,
                                "like_count": 10,
                                "comment_count": 2,
                            }
                        ],
                        "next_max_id": "cursor-1",
                        "more_available": True,
                    },
                    "credits_used": 1,
                }
            )
        assert params["next_max_id"] == "cursor-1"
        return _Response(
            {
                "success": True,
                "data": {
                    "items": [
                        {"pk": "2", "code": "BBB", "taken_at": 1700003600, "like_count": 5, "comment_count": 1}
                    ],
                    "next_max_id": None,
                    "more_available": False,
                },
                "credits_used": 1,
            }
        )

    fake = types.SimpleNamespace(get=get)
    monkeypatch.setattr(sociavault_client, "_client", lambda: fake)

    # `sociavault_client` passes the handle through untouched — stripping the leading `@` is
    # `social_audit._resolve_handle`'s job, since it also has to expand Facebook/LinkedIn paths.
    result = await sociavault_client.fetch_recent_posts("instagram", "trafficradius", limit=40)

    assert calls["n"] == 2
    assert result.credits_used == 2
    assert len(result.posts) == 2
    assert result.posts[0].like_count == 10
    assert result.posts[0].url == "https://www.instagram.com/p/AAA/"
    assert result.more_available is False


@pytest.mark.asyncio
async def test_pagination_stops_at_the_page_cap_even_if_more_is_available(monkeypatch):
    calls = {"n": 0}

    async def get(path, params):
        calls["n"] += 1
        return _Response(
            {
                "success": True,
                "data": {
                    "items": [{"pk": str(calls["n"]), "code": f"c{calls['n']}", "taken_at": 1, "like_count": 1, "comment_count": 1}],
                    "next_max_id": f"cursor-{calls['n']}",
                    "more_available": True,
                },
                "credits_used": 1,
            }
        )

    fake = types.SimpleNamespace(get=get)
    monkeypatch.setattr(sociavault_client, "_client", lambda: fake)

    result = await sociavault_client.fetch_recent_posts("instagram", "someone", limit=1000)

    assert calls["n"] == sociavault_client._MAX_PAGES_PER_ACCOUNT
    assert result.credits_used == sociavault_client._MAX_PAGES_PER_ACCOUNT


@pytest.mark.asyncio
async def test_facebook_engagement_fields_are_normalised(monkeypatch):
    async def get(path, params):
        assert path == "/scrape/facebook/profile/posts"
        assert params["url"] == "https://www.facebook.com/trafficradius"
        return _Response(
            {
                "success": True,
                "data": {
                    "posts": [
                        {
                            "id": "p1",
                            "url": "https://www.facebook.com/reel/p1",
                            "text": "hello world",
                            "publishTime": 1700000000,
                            "reactionCount": 42,
                            "commentCount": 3,
                            "videoDetails": {"sdUrl": "x"},
                        }
                    ],
                    "cursor": None,
                },
                "credits_used": 1,
            }
        )

    fake = types.SimpleNamespace(get=get)
    monkeypatch.setattr(sociavault_client, "_client", lambda: fake)

    result = await sociavault_client.fetch_recent_posts("facebook", "https://www.facebook.com/trafficradius", limit=10)

    assert len(result.posts) == 1
    post = result.posts[0]
    assert post.like_count == 42
    assert post.comment_count == 3
    assert post.is_video is True
    assert result.more_available is False


@pytest.mark.asyncio
async def test_linkedin_engagement_counts_are_none_not_zero(monkeypatch):
    """LinkedIn's documented response has no engagement fields at all — a `None` here is the
    correct 'not measured', and printing `0` would be a false claim of zero engagement."""

    async def get(path, params):
        assert path == "/scrape/linkedin/company"
        return _Response(
            {
                "success": True,
                "data": {
                    "posts": [
                        {"url": "https://linkedin.com/post/1", "datePublished": "2024-01-01T00:00:00Z", "text": "hi"}
                    ]
                },
                "credits_used": 1,
            }
        )

    fake = types.SimpleNamespace(get=get)
    monkeypatch.setattr(sociavault_client, "_client", lambda: fake)

    result = await sociavault_client.fetch_recent_posts("linkedin", "https://linkedin.com/company/acme", limit=10)

    assert len(result.posts) == 1
    post = result.posts[0]
    assert post.like_count is None
    assert post.comment_count is None
    assert post.published_at == 1704067200


@pytest.mark.asyncio
async def test_a_failed_request_raises_a_sociavault_error(monkeypatch):
    async def get(path, params):
        return _Response({"error": "not found"}, status_code=404)

    fake = types.SimpleNamespace(get=get)
    monkeypatch.setattr(sociavault_client, "_client", lambda: fake)

    with pytest.raises(sociavault_client.SociaVaultError):
        await sociavault_client.fetch_recent_posts("instagram", "ghost", limit=10)


# --------------------------------------------------------------------------------------
# The live API's real shapes — taken from real responses on 2026-10-01, not from the docs' prose
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "handle", "data"),
    [
        (
            "instagram",
            "someone",
            {"items": {"0": {"pk": "a", "code": "A", "taken_at": 2, "like_count": 8, "comment_count": 2,
                             "caption": {"text": "Choosing a broker is high-stakes."}},
                       "1": {"pk": "b", "code": "B", "taken_at": 1, "like_count": 20, "comment_count": 0,
                             "caption": {"text": "Find your niche."}}},
             "next_max_id": None, "more_available": False},
        ),
        (
            "facebook",
            "https://www.facebook.com/someone/",
            {"posts": {"0": {"id": "a", "text": "Choosing a broker is high-stakes.", "publishTime": 2, "reactionCount": 0, "commentCount": 0},
                       "1": {"id": "b", "text": "Find your niche.", "publishTime": 1, "reactionCount": 1, "commentCount": 0}},
             "cursor": None},
        ),
        (
            "linkedin",
            "https://www.linkedin.com/company/someone/",
            {"name": "Someone", "posts": {"0": {"url": "u0", "datePublished": "2026-07-01T09:34:03.192Z", "text": "Choosing a broker is high-stakes."},
                                          "1": {"url": "u1", "datePublished": "2026-06-01T09:34:03.192Z", "text": "Find your niche."}}},
        ),
    ],
)
async def test_posts_keyed_by_index_are_read_on_every_platform(monkeypatch, platform, handle, data):
    """The live API sends `items` / `posts` as `{"0": {...}, "1": {...}}`. Iterated as a list, that
    is its keys, and every account came back with zero posts while still costing its credits."""

    async def get(path, params):
        return _Response({"success": True, "data": data, "credits_used": 1})

    monkeypatch.setattr(sociavault_client, "_client", lambda: types.SimpleNamespace(get=get))
    result = await sociavault_client.fetch_recent_posts(platform, handle, limit=10)

    assert [p.caption for p in result.posts] == ["Choosing a broker is high-stakes.", "Find your niche."]
    assert result.note is None


@pytest.mark.parametrize(
    "raw",
    ["socialbroker.com.au", "@socialbroker.com.au", "https://www.instagram.com/socialbroker.com.au/",
     "instagram.com/socialbroker.com.au?hl=en", "https://instagram.com/socialbroker.com.au/reels/"],
)
def test_instagram_handles_are_reduced_to_the_bare_username(raw):
    assert sociavault_client.instagram_username(raw) == "socialbroker.com.au"


@pytest.mark.asyncio
async def test_an_instagram_profile_url_is_sent_as_a_handle(monkeypatch):
    """The endpoint answers a URL with a 400: "You must provide a handle, not a url"."""
    sent = []

    async def get(path, params):
        sent.append(params["handle"])
        return _Response({"success": True, "data": {"items": {}, "more_available": False}, "credits_used": 1})

    monkeypatch.setattr(sociavault_client, "_client", lambda: types.SimpleNamespace(get=get))
    await sociavault_client.fetch_recent_posts("instagram", "https://www.instagram.com/socialbroker.com.au/", limit=5)
    assert sent == ["socialbroker.com.au"]


@pytest.mark.asyncio
async def test_a_sample_that_stops_at_the_limit_is_not_reported_as_the_full_history(monkeypatch):
    """Facebook pages three posts at a time; at limit=15 paging stops on exactly 15 with the API
    still offering a cursor. That used to come back `more_available=False`, and the document said
    "This is the account's full recent history"."""
    calls = {"n": 0}

    async def get(path, params):
        calls["n"] += 1
        posts = {str(i): {"id": f"{calls['n']}-{i}", "text": "x", "publishTime": 1} for i in range(3)}
        return _Response({"success": True, "data": {"posts": posts, "cursor": f"c{calls['n']}"}, "credits_used": 1})

    monkeypatch.setattr(sociavault_client, "_client", lambda: types.SimpleNamespace(get=get))
    result = await sociavault_client.fetch_recent_posts("facebook", "https://www.facebook.com/someone", limit=15)

    assert len(result.posts) == 15 and calls["n"] == 5
    assert result.more_available is True
