"""The one place this backend talks to Firecrawl.

Why this exists
----------------
Context.dev is, and stays, the primary source for `DESIGN.md` (see `app/services/design_md.py`
and the "One API and one key for web data" rule in `CLAUDE.md`). This module has two jobs, on two
different triggers.

**`extract_brand_tokens`** is the narrow, secondary one: when Context.dev's `/web/styleguide` and
`/web/fonts` and the free local CSS parse in `design_tokens.py` *all* leave a field unfilled — a
bot-walled page, a JS-heavy theme the local parse cannot resolve — Firecrawl's structured extraction
is tried before giving up on that field. It is never called unless `design_md._brand_gaps` finds
something missing, and it never overrides a value Context.dev or the local parse already found. See
`design_md._capture_brand_fallback`.

**`extract_reference_images`** has no equivalent gap to wait for: there is no free local reader for
"what does this business actually look like", and a generated image with nothing to go on but brand
colours is exactly how this pipeline used to produce photorealistic images of the wrong subject
(see its own docstring, and `image_briefs.py`). It runs once per run, alongside the brand-token
capture, whenever Firecrawl is configured and image generation is about to happen.

What this module returns
-------------------------
`extract_brand_tokens` asks Firecrawl's `/extract` for a fixed JSON Schema (colors, fonts, logo,
button radius) rather than scraping HTML and re-parsing it here — that would just be a second,
worse copy of `design_tokens.py`. The values come back as raw strings; validation (is this really
a hex colour?) happens in `design_md.py`'s merge, the same place that already validates
Context.dev's own colours, so there is exactly one place a bad value gets caught.

`/extract` is asynchronous: POST returns a job id, and the result is polled from
`GET /extract/{id}`. `_poll` bounds that with `_MAX_POLL_SECONDS` rather than waiting forever on a
job that never finishes.

Testing
-------
Tests must not reach the live API. Patch the functions in this module — never `_client()`.

Docs
----
* Extract   https://docs.firecrawl.dev/api-reference/endpoint/extract
* Poll      https://docs.firecrawl.dev/api-reference/endpoint/extract-get
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_KEY_ENV = "FIRECRAWL_API_KEY"
_BASE_URL = "https://api.firecrawl.dev/v2"

_TIMEOUT_SECONDS = 30.0
# Bounds the poll loop in `extract_brand_tokens`. A brand-token extraction is a handful of fields
# off one page, not a crawl — if it has not finished in this long it is not going to.
_MAX_POLL_SECONDS = 45.0
_POLL_INTERVAL_SECONDS = 2.0
# The longest this module will wait out a 429 before trying once more. Firecrawl's lower plans are
# rate-limited per minute (a live run hit "Consumed (req/min): 3, Remaining: 0 ... retry after 37s"
# on its third competitor), and the reply says exactly when the window resets. Waiting once for it
# costs seconds; giving up drops that competitor from the audit. A wait longer than this, or a
# second 429, is reported as the failure it is rather than stalling a stage.
_MAX_RATE_LIMIT_WAIT_SECONDS = 65.0
_RETRY_AFTER_IN_BODY = re.compile(r"retry after (\d+(?:\.\d+)?)\s*s", re.IGNORECASE)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primaryColor": {"type": "string", "description": "The site's main brand hex color, e.g. #1a73e8"},
        "secondaryColor": {"type": "string", "description": "A secondary brand hex color, if distinct from the primary"},
        "backgroundColor": {"type": "string", "description": "The page's main background hex color"},
        "textColor": {"type": "string", "description": "The body text hex color"},
        "headingFont": {"type": "string", "description": "The font family used for headings"},
        "bodyFont": {"type": "string", "description": "The font family used for body text"},
        "logoUrl": {"type": "string", "description": "Absolute URL of the site's primary logo image"},
        "buttonBorderRadius": {"type": "string", "description": "CSS border-radius of the primary button, e.g. 8px"},
    },
}

# What `extract_reference_images` asks for — the page's own real photos, not its brand tokens.
# Separate from `_SCHEMA` because it is asked for on a different trigger (see that function's
# docstring): brand tokens are asked for only on a named gap, reference photos are asked for
# whenever image generation is about to run, since there is no "gap" for a subject a text-only
# prompt could otherwise only guess at.
_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "heroImageUrl": {
            "type": "string",
            "description": "Absolute URL of the page's main hero/banner photograph — the large image "
            "at the top of the page, if it is a real photo rather than an icon or illustration",
        },
        "contentImageUrls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Absolute URLs of up to 4 other real photographs used further down the page "
            "(people, premises, product, work in progress). Exclude logos, icons, decorative SVGs and "
            "stock illustration.",
        },
    },
}

# What `extract_social_links` asks for — resolving a competitor's *domain* to the social accounts
# `sociavault_client` can then fetch real posts from. Stage 10 (`social_content_strategy_audit`)
# has a real handle for the client (`client_s_own_social_pages_handles`) but only a domain for each
# competitor (`competitor_list`'s own output), and a domain is not a handle SociaVault can look up.
_SOCIAL_LINKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facebookUrl": {"type": "string", "description": "Absolute URL of this business's Facebook page, if one is linked anywhere on the site (header, footer, or a social icon)"},
        "instagramUrl": {"type": "string", "description": "Absolute URL of this business's Instagram profile, if linked"},
        "linkedinUrl": {"type": "string", "description": "Absolute URL of this business's LinkedIn company page, if linked"},
    },
}


class FirecrawlError(Exception):
    """A Firecrawl call did not produce a usable answer."""


class FirecrawlNotConfigured(FirecrawlError):
    """`FIRECRAWL_API_KEY` is not set. Callers treat this as "skip me", not a failure."""


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV, "").strip())


@lru_cache(maxsize=1)
def _client() -> httpx.AsyncClient:
    """The shared async client. Cached so the connection pool is reused across requests."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise FirecrawlNotConfigured(
            f"{API_KEY_ENV} is not set, so Firecrawl is unavailable. Add it to the backend .env."
        )
    return httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=_TIMEOUT_SECONDS,
    )


def reset_client() -> None:
    """Drop the cached client. For tests and for a key rotated without a restart."""
    _client.cache_clear()


def _rate_limit_wait(response: httpx.Response) -> float | None:
    """Seconds until a 429's window resets, from `Retry-After` or the "retry after Ns" in the body,
    or None when it is not a 429, says nothing, or asks for longer than this module will wait."""
    if response.status_code != 429:
        return None
    wait: float | None = None
    header = response.headers.get("retry-after", "").strip()
    if header:
        try:
            wait = float(header)
        except ValueError:
            wait = None
    if wait is None:
        match = _RETRY_AFTER_IN_BODY.search(response.text or "")
        wait = float(match.group(1)) if match else None
    if wait is None or wait > _MAX_RATE_LIMIT_WAIT_SECONDS:
        return None
    return max(wait, 1.0)


async def _post_extract(client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    """Submit an extract job, waiting out one rate-limit window if Firecrawl names one."""
    response = await client.post("/extract", json=body)
    wait = _rate_limit_wait(response)
    if wait is not None:
        logger.info("Firecrawl rate limit hit; waiting %.0fs for the window to reset", wait)
        await asyncio.sleep(wait)
        response = await client.post("/extract", json=body)
    response.raise_for_status()
    return response


def _fail(what: str, exc: Exception) -> FirecrawlError:
    if isinstance(exc, httpx.TimeoutException):
        detail = f"Firecrawl did not answer within {int(_TIMEOUT_SECONDS)}s."
    elif isinstance(exc, httpx.ConnectError):
        detail = "Could not reach Firecrawl from this server."
    elif isinstance(exc, httpx.HTTPStatusError):
        detail = f"Firecrawl answered HTTP {exc.response.status_code}: {_status_body(exc)}"
    else:
        detail = str(exc) or exc.__class__.__name__
    return FirecrawlError(f"{what}: {detail}")


def _status_body(exc: httpx.HTTPStatusError) -> str:
    try:
        body = exc.response.json()
    except ValueError:
        return exc.response.text[:300]
    if isinstance(body, dict):
        message = body.get("error") or body.get("message")
        if message:
            return str(message)[:300]
    return str(body)[:300]


@dataclass(frozen=True)
class BrandTokens:
    """The handful of brand fields this app asks Firecrawl for. Raw strings — `design_md.py`
    validates and merges them, the same place Context.dev's own values are validated."""

    source_url: str
    primary_color: str | None = None
    secondary_color: str | None = None
    background_color: str | None = None
    text_color: str | None = None
    heading_font: str | None = None
    body_font: str | None = None
    logo_url: str | None = None
    button_border_radius: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def available(self) -> bool:
        return any(
            (
                self.primary_color,
                self.secondary_color,
                self.background_color,
                self.text_color,
                self.heading_font,
                self.body_font,
                self.logo_url,
            )
        )


async def extract_brand_tokens(url: str) -> BrandTokens:
    """Ask Firecrawl's structured extraction for this page's brand basics.

    Only ever called as a fallback — see the module docstring. Raises `FirecrawlError` on failure
    or timeout; callers treat that as "this source had nothing" and carry on with what they have.
    """
    logger.info("Firecrawl API executing operation=extract_brand_tokens url=%r", url)
    client = _client()
    try:
        submit = await _post_extract(client, {"urls": [url], "schema": _SCHEMA})
    except httpx.HTTPError as exc:
        logger.error("Firecrawl API error operation=extract_brand_tokens url=%r error=%s", url, exc)
        raise _fail(f"Could not submit a Firecrawl extract for {url}", exc) from exc

    payload = submit.json()
    job_id = payload.get("id")
    if not job_id:
        raise FirecrawlError(f"Firecrawl did not return a job id for {url}.")

    try:
        data = await _poll(client, job_id, url)
    except FirecrawlError as exc:
        logger.error("Firecrawl API error operation=extract_brand_tokens url=%r error=%s", url, exc)
        raise

    return BrandTokens(
        source_url=url,
        primary_color=data.get("primaryColor") or None,
        secondary_color=data.get("secondaryColor") or None,
        background_color=data.get("backgroundColor") or None,
        text_color=data.get("textColor") or None,
        heading_font=data.get("headingFont") or None,
        body_font=data.get("bodyFont") or None,
        logo_url=data.get("logoUrl") or None,
        button_border_radius=data.get("buttonBorderRadius") or None,
        raw=data,
    )


@dataclass(frozen=True)
class ReferenceImages:
    """The page's own real photographs, for grounding a generated image in the client's actual
    business rather than in brand colours alone. See `extract_reference_images`."""

    source_url: str
    hero_image_url: str | None = None
    content_image_urls: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.hero_image_url or self.content_image_urls)

    @property
    def all_urls(self) -> tuple[str, ...]:
        """Hero first, deduplicated — the order `image_briefs.py` hands to OpenAI's edit endpoint,
        since the hero shot is the most representative single reference when only one is used."""
        ordered = [self.hero_image_url, *self.content_image_urls]
        return tuple(dict.fromkeys(u for u in ordered if u))


async def extract_reference_images(url: str) -> ReferenceImages:
    """Ask Firecrawl for the page's own real photos.

    Why this exists
    ----------------
    A generated hero/proof/CTA image used to be briefed on brand colours and typography alone
    (`image_briefs.build_image_briefs`) — nothing told the model what the business actually *is*,
    so it filled that gap with a plausible but arbitrary scene: the "random character images" that
    do not reflect the client's real product, people or premises. Handing the model real photographs
    from the client's own page (via `openai_image_client.edit_image`) grounds the generated image in
    the actual subject matter instead of an invented one — the same fix `DESIGN.md`'s palette
    measurement is for colour, one layer up.

    Unlike `extract_brand_tokens`, this is not gated on a "gap" — there is no free local reader for
    "what does this business look like", so it is called whenever image generation is about to run
    and Firecrawl is configured. Raises `FirecrawlError` on failure or timeout; callers treat that as
    "no reference available" and fall back to a text-only prompt, never a fabricated one.
    """
    logger.info("Firecrawl API executing operation=extract_reference_images url=%r", url)
    client = _client()
    try:
        submit = await _post_extract(client, {"urls": [url], "schema": _IMAGE_SCHEMA})
    except httpx.HTTPError as exc:
        logger.error("Firecrawl API error operation=extract_reference_images url=%r error=%s", url, exc)
        raise _fail(f"Could not submit a Firecrawl image extract for {url}", exc) from exc

    payload = submit.json()
    job_id = payload.get("id")
    if not job_id:
        raise FirecrawlError(f"Firecrawl did not return a job id for {url}.")

    try:
        data = await _poll(client, job_id, url)
    except FirecrawlError as exc:
        logger.error("Firecrawl API error operation=extract_reference_images url=%r error=%s", url, exc)
        raise

    content_urls = tuple(
        dict.fromkeys(u.strip() for u in (data.get("contentImageUrls") or []) if isinstance(u, str) and u.strip())
    )[:4]
    return ReferenceImages(
        source_url=url,
        hero_image_url=(data.get("heroImageUrl") or "").strip() or None,
        content_image_urls=content_urls,
    )


@dataclass(frozen=True)
class SocialLinks:
    """A domain's own social profile URLs — not the client's, a *competitor's*. See
    `extract_social_links` and `social_audit.resolve_competitor_handles`, the module that turns this
    into something `sociavault_client.fetch_recent_posts` can actually look up."""

    source_url: str
    facebook_url: str | None = None
    instagram_url: str | None = None
    linkedin_url: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.facebook_url or self.instagram_url or self.linkedin_url)


async def extract_social_links(url: str) -> SocialLinks:
    """Ask Firecrawl which Facebook/Instagram/LinkedIn URLs a business's own site links to.

    Why this exists
    ----------------
    Stage 10 (`social_content_strategy_audit`) has a real, ready-to-fetch handle for the client
    (`client_s_own_social_pages_handles`), but for a competitor it only has a domain
    (`competitor_list`'s own output names a company and a URL, never a social handle) — and a
    domain is not something `sociavault_client` can look up directly. This is the missing step: a
    business's own site almost always links to its own social profiles (header icons, footer,
    "follow us"), so reading those links off the page turns a domain into a real, fetchable handle,
    per `social_audit.resolve_competitor_handles` — Firecrawl first, Context.dev's `retrieve_brand`
    as the fallback when Firecrawl is unconfigured or finds nothing, per that function's own
    docstring for why the order is inverted from `design_md.py`'s usual Context.dev-primary rule.

    Raises `FirecrawlError` on failure or timeout; callers treat that as "no links found here" and
    fall through to the next source, never a fabricated handle.
    """
    logger.info("Firecrawl API executing operation=extract_social_links url=%r", url)
    client = _client()
    try:
        submit = await _post_extract(client, {"urls": [url], "schema": _SOCIAL_LINKS_SCHEMA})
    except httpx.HTTPError as exc:
        logger.error("Firecrawl API error operation=extract_social_links url=%r error=%s", url, exc)
        raise _fail(f"Could not submit a Firecrawl social-links extract for {url}", exc) from exc

    payload = submit.json()
    job_id = payload.get("id")
    if not job_id:
        raise FirecrawlError(f"Firecrawl did not return a job id for {url}.")

    try:
        data = await _poll(client, job_id, url)
    except FirecrawlError as exc:
        logger.error("Firecrawl API error operation=extract_social_links url=%r error=%s", url, exc)
        raise

    return SocialLinks(
        source_url=url,
        facebook_url=(data.get("facebookUrl") or "").strip() or None,
        instagram_url=(data.get("instagramUrl") or "").strip() or None,
        linkedin_url=(data.get("linkedinUrl") or "").strip() or None,
    )


async def _poll(client: httpx.AsyncClient, job_id: str, url: str) -> dict[str, Any]:
    elapsed = 0.0
    while elapsed <= _MAX_POLL_SECONDS:
        try:
            response = await client.get(f"/extract/{job_id}")
            wait = _rate_limit_wait(response)
            if wait is not None and elapsed + wait <= _MAX_POLL_SECONDS + _MAX_RATE_LIMIT_WAIT_SECONDS:
                # A status poll counts against the same per-minute limit as the submit.
                await asyncio.sleep(wait)
                elapsed += wait
                continue
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise _fail(f"Could not poll the Firecrawl extract job for {url}", exc) from exc

        payload = response.json()
        status = payload.get("status")
        if status == "completed":
            logger.info("Firecrawl extract completed url=%r tokens_used=%s", url, payload.get("tokensUsed"))
            return payload.get("data") or {}
        if status in ("failed", "cancelled"):
            raise FirecrawlError(f"Firecrawl extract for {url} {status}.")

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

    raise FirecrawlError(f"Firecrawl extract for {url} did not finish within {int(_MAX_POLL_SECONDS)}s.")
