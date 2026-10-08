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

Three things the live responses do that a sample has to correct for, each checked against real
payloads on 2026-10-06:

* Instagram lists **pinned posts first**, whatever their age. They are left out of the sample
  (`SocialFetchResult.pinned_excluded` counts them), and every platform's posts are sorted newest
  first before the sample is cut to `limit`.
* LinkedIn's `datePublished` is an **estimate made at scrape time** from a relative label. The real
  time is read out of the post's activity id (`_linkedin_time_from_id`).
* Facebook publishes **no share count** under any key. `videoViewCount` and Instagram's
  `play_count` become `SocialPost.view_count`; the media type becomes `SocialPost.format`; the
  LinkedIn company page's `followers` becomes `SocialFetchResult.follower_count`.

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

import datetime
import logging
import os
import re
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
    #: Plays/views where the platform publishes them (an Instagram reel, a Facebook video). `None`
    #: for a still image, which has no view count to publish — not a measured zero.
    view_count: int | None = None
    #: "reel" | "video" | "carousel" | "image" | "text", from the platform's own media type. `None`
    #: where the response does not say (LinkedIn's company feed).
    format: str | None = None


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
    #: The account's follower count where the response carries one (LinkedIn's company page), for
    #: an engagement rate. `None` when absent, never 0.
    follower_count: int | None = None
    #: Pinned posts left out of the sample. Instagram lists them first whatever their age, and
    #: counted as recent they made a year-old ad look like this week's cadence.
    pinned_excluded: int = 0


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


@dataclass(frozen=True)
class _Page:
    """One page of one platform's posts, before the pages are joined into a `SocialFetchResult`."""

    posts: list[SocialPost]
    cursor: str | None
    more_available: bool
    credits: int
    pinned_excluded: int = 0
    follower_count: int | None = None


def _instagram_format(item: dict[str, Any]) -> str | None:
    """Instagram's own media type: `product_type` "clips" is a reel; `media_type` 8 a carousel,
    2 a video, 1 a single image."""
    if item.get("product_type") == "clips":
        return "reel"
    return {8: "carousel", 2: "video", 1: "image"}.get(_as_int(item.get("media_type")))


def _is_pinned(item: dict[str, Any]) -> bool:
    """Pinned to the profile grid. The live response marks it with a non-empty
    `timeline_pinned_user_ids` (`{"0": <owner id>}`), and lists pinned posts first whatever their age."""
    return bool(item.get("timeline_pinned_user_ids"))


def _instagram_post(item: dict[str, Any]) -> SocialPost:
    # An owner can hide like counts. The field still arrives, but it is not a public measurement.
    hidden = bool(item.get("like_and_view_counts_disabled"))
    views = _as_int(item.get("play_count") or item.get("ig_play_count") or item.get("view_count"))
    return SocialPost(
        platform="instagram",
        id=str(item.get("pk") or item.get("code") or ""),
        url=f"https://www.instagram.com/p/{item['code']}/" if item.get("code") else None,
        published_at=_as_int(item.get("taken_at")),
        caption=_caption(item.get("caption")),
        like_count=None if hidden else _as_int(item.get("like_count")),
        comment_count=_as_int(item.get("comment_count")),
        share_count=None,  # Instagram's posts endpoint does not publish a share count.
        is_video=bool(item.get("video_duration") or views),
        raw=item,
        view_count=None if hidden else views,
        format=_instagram_format(item),
    )


async def _instagram_page(handle: str, *, cursor: str | None) -> _Page:
    params: dict[str, Any] = {"handle": instagram_username(handle)}
    if cursor:
        params["next_max_id"] = cursor
    payload = await _get("/scrape/instagram/posts", params)
    data = payload.get("data") or {}
    items = _records(data.get("items"))
    kept = [item for item in items if not _is_pinned(item)]
    return _Page(
        posts=[_instagram_post(item) for item in kept],
        cursor=data.get("next_max_id"),
        more_available=bool(data.get("more_available")),
        credits=int(payload.get("credits_used") or 1),
        pinned_excluded=len(items) - len(kept),
    )


def _facebook_format(item: dict[str, Any]) -> str:
    if item.get("videoDetails"):
        return "video"
    return "image" if item.get("image") or item.get("images") else "text"


async def _facebook_page(handle: str, *, cursor: str | None) -> _Page:
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
            # The live response (checked 2026-10-06) carries no share count under any key; this
            # stays None unless SociaVault adds one.
            share_count=_as_int(item.get("shareCount")),
            is_video=bool(item.get("videoDetails")),
            raw=item,
            view_count=_as_int(item.get("videoViewCount")),
            format=_facebook_format(item),
        )
        for item in _records(data.get("posts"))
    ]
    next_cursor = data.get("cursor")
    # Facebook's docs don't publish a `more_available` flag the way Instagram's do — the practical
    # signal is "did this page hand back another cursor at all".
    return _Page(posts, next_cursor, bool(next_cursor), int(payload.get("credits_used") or 1))


# LinkedIn activity ids are snowflakes: the top 41 bits are the post's creation time in epoch ms.
_LINKEDIN_ACTIVITY_ID = re.compile(r"(?:activity[:-])?(\d{15,})")
# A derived time outside this window means the number was not an activity id after all.
_EARLIEST_PLAUSIBLE_POST = 1_104_537_600  # 2005-01-01, before LinkedIn had a feed


def _linkedin_time_from_id(*candidates: Any) -> int | None:
    """The real creation time of a LinkedIn post, read out of its activity id.

    The company endpoint's `datePublished` is not that: it is LinkedIn's relative label ("2w",
    "1mo") turned into a date at scrape time, so every value carries the scrape's time of day, a
    month-old post can be 25 days out, several posts collapse onto one date, and some are null. Read
    from the id, a live sample matched the same posts' Facebook and Instagram times to the minute.
    """
    now = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
    for candidate in candidates:
        match = _LINKEDIN_ACTIVITY_ID.search(str(candidate or ""))
        if not match:
            continue
        seconds = (int(match.group(1)) >> 22) // 1000
        if _EARLIEST_PLAUSIBLE_POST <= seconds <= now + 86_400:
            return seconds
    return None


async def _linkedin_page(url: str) -> _Page:
    """LinkedIn has no separate posts endpoint — `profile` (a person) and `company` both embed a
    `posts` array directly in the one response. Neither publishes engagement counts, so
    `like_count`/`comment_count`/`share_count` are always `None` here; see the module docstring for
    why that is left `None` rather than `0`. (`/scrape/linkedin/post` does return likes and comments,
    at one credit per post; it is not used, by choice, to keep a sample's cost per account flat.)"""
    path = "/scrape/linkedin/company" if "/company/" in url else "/scrape/linkedin/profile"
    payload = await _get(path, {"url": url})
    data = payload.get("data") or {}
    posts = [
        SocialPost(
            platform="linkedin",
            id=str(item.get("id") or item.get("url") or item.get("datePublished") or index),
            url=item.get("url"),
            published_at=_linkedin_time_from_id(item.get("id"), item.get("url"))
            or _parse_iso8601(item.get("datePublished")),
            caption=item.get("text") if isinstance(item.get("text"), str) else None,
            like_count=None,
            comment_count=None,
            share_count=None,
            raw=item,
        )
        for index, item in enumerate(_records(data.get("posts")))
    ]
    return _Page(
        posts=posts,
        cursor=None,
        # Not paged here (one call per account), but a `paginationToken` means the feed goes on.
        more_available=bool(data.get("paginationToken")),
        credits=int(payload.get("credits_used") or 1),
        follower_count=_as_int(data.get("followers")),
    )


def _parse_iso8601(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _newest_first(posts: list[SocialPost]) -> list[SocialPost]:
    """Newest first, undated last. Applied before the sample is cut to `limit`, so the sample is the
    most recent posts rather than whatever order the platform happened to list them in."""
    return sorted(posts, key=lambda p: (p.published_at is None, -(p.published_at or 0)))


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
        pages = [await _linkedin_page(handle)]
    else:
        page_fn = _instagram_page if platform == "instagram" else _facebook_page
        pages = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES_PER_ACCOUNT):
            page = await page_fn(handle, cursor=cursor)
            pages.append(page)
            cursor = page.cursor
            fetched = sum(len(p.posts) for p in pages)
            # A page of nothing but pinned posts is not the end of the feed.
            if (not page.posts and not page.pinned_excluded) or fetched >= limit or not cursor or not page.more_available:
                break

    posts = _newest_first([post for page in pages for post in page.posts])
    last = pages[-1]
    note = None if posts else "SociaVault returned no posts for this account."
    return SocialFetchResult(
        platform=platform,
        handle=handle,
        posts=tuple(posts[:limit]),
        # More exists if the platform said so when paging stopped (at `limit` or at the page cap),
        # or if this page overshot `limit` and the extra posts were cut. Requiring both, as this
        # once did, reported a sample that stopped exactly at `limit` as the account's full
        # history, which the rendered document then stated as fact.
        more_available=last.more_available or len(posts) > limit,
        credits_used=sum(page.credits for page in pages),
        note=note,
        follower_count=next((p.follower_count for p in pages if p.follower_count is not None), None),
        pinned_excluded=sum(page.pinned_excluded for page in pages),
    )
