"""The one place this backend talks to SociaVault.

Why this exists
----------------
Stage 10 (`social_content_strategy_audit`) benchmarks the client's own social accounts against
competitors', and its own schema says what that requires: real posts, real timestamps, real
engagement counts (`raw_post_data_source`'s `raw_explanation` — "every report must disclose the
actual sample size and collection method used, never presented as a full historical archive unless
it demonstrably is one"). Before this module, the only path to that field with no exported CSV was
"research live via browser" — the model reading a screenshot or a scrape and guessing at like
counts, exactly the invented-measurement failure `design_tokens.py` documents for a palette, one
platform over. SociaVault is a paid, structured API over Facebook, Instagram and LinkedIn's public
data, so this module exists to fetch real numbers instead.

What this module returns
-------------------------
Three platforms' raw shapes are all different — Instagram nests posts under `data.items` with a
`next_max_id` cursor, Facebook returns three posts per call under `data.posts` with an opaque
`cursor`, LinkedIn embeds posts directly in the profile/company payload with no pagination and no
engagement counts published at all. `fetch_recent_posts` is the one function callers actually use:
it dispatches to the right endpoint, pages until `limit` is reached or the platform runs out, and
returns the same `SocialPost` shape regardless of platform. A field a platform genuinely does not
expose (LinkedIn's like/comment counts) is left `None`, never fabricated — `social_audit.py`'s
renderer states that gap in the document rather than printing a `0` or an invented figure.

Credits
-------
Every call costs a credit, and Facebook is the expensive one: 3 posts per call versus Instagram's
~12, so a same-sized sample costs roughly 4x as many credits. `_MAX_PAGES_PER_ACCOUNT` bounds the
worst case regardless of platform or the caller's requested `limit` — nothing here loops unbounded
over pages the way `CLAUDE.md`'s "credits are money" rule forbids looping over URLs.

Testing
-------
Tests must not reach the live API. Patch the functions in this module — never `_client()`. See
`tests/test_sociavault_client.py`.

Docs
----
* Overview             https://docs.sociavault.com/
* Instagram posts      https://docs.sociavault.com/api-reference/instagram/posts
* Facebook profile posts  https://docs.sociavault.com/api-reference/facebook/profile-posts
                           (the docs page slug is `profile-posts`; the endpoint is `/profile/posts`)
* LinkedIn profile     https://docs.sociavault.com/api-reference/linkedin/profile
* LinkedIn company     https://docs.sociavault.com/api-reference/linkedin/company
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Matches the variable name already set in the backend .env — see CLAUDE.md.
API_KEY_ENV = "SOCIO_VAULT"
_BASE_URL = "https://api.sociavault.com/v1"
_TIMEOUT_SECONDS = 30.0

PLATFORMS = ("facebook", "instagram", "linkedin")

# Bounds worst-case spend per account regardless of `limit` or how cheap/expensive the platform's
# per-page yield is. Facebook returns 3 posts/page, so 8 pages is 24 posts for 8 credits; Instagram
# returns ~12/page, so 8 pages is closer to 96 posts for the same 8 credits. Either way, a caller
# cannot accidentally page forever against an account with a very long history.
_MAX_PAGES_PER_ACCOUNT = 8


class SociaVaultError(Exception):
    """A SociaVault call did not produce a usable answer."""


class SociaVaultNotConfigured(SociaVaultError):
    """`SOCIO_VAULT` is not set. Callers treat this as "skip me", not a failure."""


class UnsupportedPlatform(SociaVaultError):
    """Asked for a platform this module has no endpoint for (e.g. TikTok, YouTube)."""


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


@lru_cache(maxsize=1)
def _client() -> httpx.AsyncClient:
    """The shared async client. Cached so the connection pool is reused across requests."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise SociaVaultNotConfigured(
            f"{API_KEY_ENV} is not set, so live social post data is unavailable. Add it to the "
            "backend .env."
        )
    return httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"X-API-Key": key},
        timeout=_TIMEOUT_SECONDS,
    )


def reset_client() -> None:
    """Drop the cached client. For tests and for a key rotated without a restart."""
    _client.cache_clear()


def _fail(what: str, exc: Exception) -> SociaVaultError:
    if isinstance(exc, httpx.TimeoutException):
        detail = f"SociaVault did not answer within {int(_TIMEOUT_SECONDS)}s."
    elif isinstance(exc, httpx.ConnectError):
        detail = "Could not reach SociaVault from this server."
    elif isinstance(exc, httpx.HTTPStatusError):
        detail = f"SociaVault answered HTTP {exc.response.status_code}: {_status_body(exc)}"
    else:
        detail = str(exc) or exc.__class__.__name__
    return SociaVaultError(f"{what}: {detail}")


def _status_body(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
    except ValueError:
        return exc.response.text[:300]
    if isinstance(body, dict):
        message = body.get("message") or body.get("error")
        if message:
            return str(message)[:300]
    return str(body)[:300]


@dataclass(frozen=True)
class SocialPost:
    """One post, in the shape every platform's response is normalised into.

    `like_count` / `comment_count` / `share_count` are `None`, not `0`, when the platform's own
    response has no such field — LinkedIn publishes neither today. A renderer that prints `None` as
    "not published by this source" keeps that distinction; printing `0` would claim a real
    measurement of zero engagement, which is a different, false claim.
    """

    platform: str
    id: str
    url: str | None
    published_at: int | None  # unix seconds
    caption: str | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    is_video: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SocialFetchResult:
    """One account, one platform, fetched. `more_available` says whether the real history is
    longer than what was sampled — the field this feeds (`raw_post_data_source`) requires the
    report to disclose that rather than imply a full archive."""

    platform: str
    handle: str
    posts: tuple[SocialPost, ...]
    more_available: bool
    credits_used: int
    #: Set when a request succeeded but returned zero posts, or the account could not be resolved.
    note: str | None = None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    logger.info("SociaVault API executing operation=get path=%r params=%s", path, params)
    try:
        response = await _client().get(path, params=params)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("SociaVault API error path=%r params=%s error=%s", path, params, exc)
        raise _fail(f"Could not call SociaVault {path}", exc) from exc

    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("success", True):
        raise SociaVaultError(f"SociaVault {path} returned an unsuccessful response.")
    return payload


def _records(value: Any) -> list[dict[str, Any]]:
    """A post collection as a list, whichever shape it arrives in.

    The live API returns `items` / `posts` as an object keyed `"0"`, `"1"`, ... rather than as the
    array the docs' prose suggests. Iterated directly, that object yields its keys, every one of
    which the `isinstance(item, dict)` filter then dropped, so every account came back with zero
    posts and still cost its credits. A list is still accepted, in case the API settles on one."""
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        def order(key: str) -> tuple[int, int | str]:
            return (0, int(key)) if str(key).isdigit() else (1, str(key))

        items = [value[k] for k in sorted(value, key=order)]
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def _caption(value: Any) -> str | None:
    """Instagram sends `caption` as an object (`{"text": ...}`); a plain string is accepted too."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


def instagram_username(handle: str) -> str:
    """A bare Instagram username from a handle, `@handle` or profile URL.

    The posts endpoint rejects a URL with a 400 ("You must provide a handle, not a url"), and a
    competitor's handle arrives as the profile URL read off their site, so it is reduced here
    rather than trusted to every caller."""
    raw = handle.strip()
    if "instagram.com/" in raw:
        raw = raw.split("instagram.com/", 1)[1]
        raw = raw.split("?", 1)[0].split("#", 1)[0].strip("/").split("/", 1)[0]
    return raw.lstrip("@").strip("/")


async def _instagram_page(handle: str, *, next_max_id: str | None) -> tuple[list[SocialPost], str | None, bool, int]:
    params: dict[str, Any] = {"handle": instagram_username(handle)}
    if next_max_id:
        params["next_max_id"] = next_max_id
    payload = await _get("/scrape/instagram/posts", params)
    data = payload.get("data") or {}
    posts = [
        SocialPost(
            platform="instagram",
            id=str(item.get("pk") or item.get("code") or ""),
            url=f"https://www.instagram.com/p/{item['code']}/" if item.get("code") else None,
            published_at=_as_int(item.get("taken_at")),
            caption=_caption(item.get("caption")),
            like_count=_as_int(item.get("like_count")),
            comment_count=_as_int(item.get("comment_count")),
            share_count=None,  # Instagram's posts endpoint does not publish a share count.
            is_video=bool(item.get("video_duration") or item.get("play_count")),
            raw=item,
        )
        for item in _records(data.get("items"))
    ]
    return posts, data.get("next_max_id"), bool(data.get("more_available")), int(payload.get("credits_used") or 1)


async def _facebook_page(handle: str, *, cursor: str | None) -> tuple[list[SocialPost], str | None, bool, int]:
    # `handle` doubles as either a profile URL or a bare numeric page id — SociaVault accepts
    # either under different query params, so a purely numeric handle is sent as `pageId`.
    params: dict[str, Any] = {"pageId": handle} if handle.strip().isdigit() else {"url": handle}
    if cursor:
        params["cursor"] = cursor
    payload = await _get("/scrape/facebook/profile/posts", params)
    data = payload.get("data") or {}
    posts = [
        SocialPost(
            platform="facebook",
            id=str(item.get("id") or ""),
            url=item.get("url"),
            published_at=_as_int(item.get("publishTime")),
            caption=item.get("text") if isinstance(item.get("text"), str) else None,
            like_count=_as_int(item.get("reactionCount")),
            comment_count=_as_int(item.get("commentCount")),
            share_count=_as_int(item.get("shareCount")),
            is_video=bool(item.get("videoDetails")),
            raw=item,
        )
        for item in _records(data.get("posts"))
    ]
    next_cursor = data.get("cursor")
    # Facebook's docs don't publish a `more_available` flag the way Instagram's do — the practical
    # signal is "did this page hand back another cursor at all".
    return posts, next_cursor, bool(next_cursor), int(payload.get("credits_used") or 1)


async def _linkedin_posts(url: str) -> tuple[list[SocialPost], int]:
    """LinkedIn has no separate posts endpoint or pagination — `profile` (a person) and `company`
    both embed a `posts` array directly in the one response. Neither publishes engagement counts as
    of this module's docs pass, so `like_count`/`comment_count`/`share_count` are always `None`
    here; see the module docstring for why that is left `None` rather than `0`."""
    path = "/scrape/linkedin/company" if "/company/" in url else "/scrape/linkedin/profile"
    payload = await _get(path, {"url": url})
    data = payload.get("data") or {}
    posts = [
        SocialPost(
            platform="linkedin",
            id=str(item.get("url") or item.get("datePublished") or index),
            url=item.get("url"),
            published_at=_parse_iso8601(item.get("datePublished")),
            caption=item.get("text") if isinstance(item.get("text"), str) else None,
            like_count=None,
            comment_count=None,
            share_count=None,
            raw=item,
        )
        for index, item in enumerate(_records(data.get("posts")))
    ]
    return posts, int(payload.get("credits_used") or 1)


def _parse_iso8601(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    import datetime

    try:
        return int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


async def fetch_recent_posts(platform: str, handle: str, *, limit: int = 40) -> SocialFetchResult:
    """Fetch up to `limit` recent posts for one account on one platform.

    Raises `UnsupportedPlatform` for anything outside `PLATFORMS` (TikTok, YouTube, Pinterest, ...)
    — those are real gaps in what this module covers, not failures to hide behind a generic error.
    Raises `SociaVaultError` for a request failure; callers (`social_audit.fetch_social_snapshot`)
    treat that as "no live data for this account" and say so in the rendered document, the same
    "state it, don't invent a substitute" rule as every other optional source in this codebase.
    """
    platform = platform.strip().lower()
    if platform not in PLATFORMS:
        raise UnsupportedPlatform(
            f"SociaVault wiring in this app covers {', '.join(PLATFORMS)} — not {platform!r}. "
            "Provide posts for this platform manually."
        )

    if platform == "linkedin":
        posts, credits = await _linkedin_posts(handle)
        return SocialFetchResult(
            platform=platform, handle=handle, posts=tuple(posts[:limit]), more_available=False, credits_used=credits
        )

    page_fn = _instagram_page if platform == "instagram" else _facebook_page
    posts: list[SocialPost] = []
    cursor: str | None = None
    more_available = False
    credits_used = 0
    for _ in range(_MAX_PAGES_PER_ACCOUNT):
        page_posts, cursor, more_available, page_credits = await page_fn(handle, **({"next_max_id": cursor} if platform == "instagram" else {"cursor": cursor}))
        credits_used += page_credits
        posts.extend(page_posts)
        if not page_posts or len(posts) >= limit or not cursor or not more_available:
            break

    note = None if posts else "SociaVault returned no posts for this account."
    return SocialFetchResult(
        platform=platform,
        handle=handle,
        posts=tuple(posts[:limit]),
        # More exists if the platform said so when paging stopped (at `limit` or at the page cap),
        # or if this page overshot `limit` and the extra posts were cut. Requiring both, as this
        # once did, reported a sample that stopped exactly at `limit` as the account's full
        # history, which the rendered document then stated as fact.
        more_available=more_available or len(posts) > limit,
        credits_used=credits_used,
        note=note,
    )
