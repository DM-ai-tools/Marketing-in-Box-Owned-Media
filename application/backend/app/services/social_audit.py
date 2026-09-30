"""Turn one account's real, live social posts into the document Stage 10 asks for.

Why this exists
----------------
`social_content_strategy_audit.json`'s `raw_post_data_source` field accepts either an exported
CSV/XLSX or the instruction "research live via browser" — and the second of those is exactly the
invented-measurement failure this codebase keeps naming (`design_tokens.py` for a palette,
`image_briefs.py` for a photo): a model reading a scrape or a screenshot has no way to actually
count likes, so it guesses, and the guess is indistinguishable from a real number until someone
checks. `app/services/sociavault_client.py` is the real measurement; this module is what turns it
into the document the field's own instruction requires — real sample size and collection method
stated up front, never presented as a full archive.

What this module does not do
-----------------------------
It does not decide *what* to say about the posts — no scoring, no format/purpose classification,
no gap analysis. That is `Social-Content-Strategy-Audit-Architect-Prompt.md`'s job, working from
real data instead of an invented one. This module's only job is fetching and rendering that data.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re

from dataclasses import dataclass

from app.services import context_dev, firecrawl_client, sociavault_client
from app.services.sociavault_client import SocialFetchResult

logger = logging.getLogger(__name__)

# How the intake field's own example is formatted: "Facebook: /trafficradius, Instagram: @handle".
# One (platform, handle) pair per comma- or newline-separated segment.
_HANDLE_LINE = re.compile(r"([A-Za-z][A-Za-z/ .]*?)\s*:\s*([^,\n]+)", re.IGNORECASE)

_PLATFORM_ALIASES = {
    "facebook": "facebook",
    "fb": "facebook",
    "instagram": "instagram",
    "ig": "instagram",
    "linkedin": "linkedin",
}


@dataclass(frozen=True)
class SocialAccount:
    """One (platform, handle) pair parsed out of an intake field, resolved to a real URL/handle
    SociaVault can look up."""

    platform: str
    handle: str


def _resolve_handle(platform: str, raw: str) -> str:
    """A bare handle or path from the intake field, resolved to what `sociavault_client` expects.

    Instagram wants a bare username; Facebook and LinkedIn want a full URL (or, for Facebook, a
    numeric page id, which is passed through untouched). A `@` is stripped for Instagram since the
    field's own example writes handles that way (`@trafficradius`); a leading slash is expanded to
    the platform's own domain for Facebook and LinkedIn, matching the field's other example forms
    (`/trafficradius`, `/company/trafficradius`).
    """
    raw = raw.strip()
    if platform == "instagram":
        return raw.lstrip("@")
    if raw.startswith("http://") or raw.startswith("https://") or raw.isdigit():
        return raw
    path = raw.lstrip("/")
    domain = "www.facebook.com" if platform == "facebook" else "www.linkedin.com"
    return f"https://{domain}/{path}"


def parse_account_handles(handles_text: str) -> tuple[SocialAccount, ...]:
    """Parse `client_s_own_social_pages_handles` (or the same shape typed for a competitor) into
    `SocialAccount`s for the platforms this module can fetch live data for.

    A platform not in `sociavault_client.PLATFORMS` (YouTube, TikTok, Pinterest, ...) is silently
    skipped here — `fetch_account_snapshot`'s caller sees it is missing from the result and reports
    it as "provide manually" rather than this function raising over an unsupported platform that is
    a perfectly normal thing for the field to name.
    """
    accounts: list[SocialAccount] = []
    for match in _HANDLE_LINE.finditer(handles_text or ""):
        label, value = match.group(1).strip().lower(), match.group(2).strip()
        platform = _PLATFORM_ALIASES.get(label)
        if not platform or not value:
            continue
        accounts.append(SocialAccount(platform=platform, handle=_resolve_handle(platform, value)))
    return tuple(accounts)


def _domain_url(domain: str) -> str:
    domain = domain.strip()
    return domain if domain.startswith(("http://", "https://")) else f"https://{domain}"


async def resolve_competitor_handles(domain: str) -> tuple[SocialAccount, ...]:
    """Resolve a competitor's bare domain (from `competitor_list`'s own `domain` field) to the
    social accounts `sociavault_client` can fetch real posts from.

    `competitor_list` names a company and a domain, never a social handle — a domain is not
    something SociaVault can look up. Almost every business site links to its own social profiles
    (a header icon, a footer, "follow us"), so reading those links off the page turns the domain
    into a real, fetchable handle instead of one this module would otherwise have to leave blank.

    **Firecrawl first, Context.dev as the fallback** — the reverse of `design_md.py`'s usual
    Context.dev-primary ordering, by explicit choice for this job: this is a one-off per-competitor
    lookup rather than the once-per-run brand capture Context.dev's own credit cost is weighed
    against there, and Firecrawl's structured extraction (`extract_social_links`) is the cheaper of
    the two calls. Context.dev's `retrieve_brand` — already used elsewhere in this codebase for a
    domain's brand record, `socials` included — only runs for whichever platforms Firecrawl left
    unresolved, and only if Firecrawl is unconfigured or found nothing at all; it never overrides a
    link Firecrawl already found, the same "each tier only fills what the one before it left open"
    rule `design_md._capture_brand_fallback` already follows for brand tokens.

    Returns `()` when neither source is configured, or when neither found any social links — a
    competitor whose site links to no social profile is a real, statable finding, not an error.
    """
    url = _domain_url(domain)
    links: dict[str, str] = {}

    if firecrawl_client.is_configured():
        try:
            found = await firecrawl_client.extract_social_links(url)
            if found.facebook_url:
                links["facebook"] = found.facebook_url
            if found.instagram_url:
                links["instagram"] = found.instagram_url
            if found.linkedin_url:
                links["linkedin"] = found.linkedin_url
        except firecrawl_client.FirecrawlError as exc:
            logger.info("Competitor handle resolution: firecrawl failed for %r: %s", domain, exc)

    still_needs = [p for p in sociavault_client.PLATFORMS if p not in links]
    if still_needs and context_dev.is_configured():
        try:
            brand = await context_dev.retrieve_brand(domain)
            for platform, social_url in brand.socials:
                key = platform.strip().lower()
                if key in still_needs and key not in links:
                    links[key] = social_url
        except context_dev.ContextDevError as exc:
            logger.info("Competitor handle resolution: context.dev failed for %r: %s", domain, exc)

    return tuple(SocialAccount(platform=platform, handle=handle) for platform, handle in links.items())


async def fetch_account_snapshot(
    accounts: tuple[SocialAccount, ...], *, limit_per_platform: int = 40
) -> tuple[tuple[SocialFetchResult, ...], tuple[str, ...]]:
    """Fetch live posts for every parsed account, concurrently. Each platform is independent — one
    failing (a private account, a stale handle) must not cost the others, the same
    "one brief failing doesn't cost the rest" rule `image_briefs.generate_all` already follows.

    Returns `((), notes)` immediately when SociaVault is not configured, so a deployment with no
    key behaves exactly as it did before this module existed: the field falls back to the operator's
    own CSV or manual research.
    """
    if not accounts:
        return (), ()
    if not sociavault_client.is_configured():
        return (), (
            f"{sociavault_client.API_KEY_ENV} is not set — no live post data was fetched. Attach an "
            "exported dataset or research the accounts manually.",
        )

    async def _one(account: SocialAccount):
        try:
            return await sociavault_client.fetch_recent_posts(
                account.platform, account.handle, limit=limit_per_platform
            )
        except sociavault_client.SociaVaultError as exc:
            logger.info("Social snapshot: %s/%s failed: %s", account.platform, account.handle, exc)
            return exc

    results = await asyncio.gather(*(_one(a) for a in accounts))

    fetched: list[SocialFetchResult] = []
    notes: list[str] = []
    for account, result in zip(accounts, results):
        if isinstance(result, Exception):
            notes.append(f"{account.platform} ({account.handle}): {result}")
            continue
        fetched.append(result)
        if result.note:
            notes.append(f"{account.platform} ({account.handle}): {result.note}")

    return tuple(fetched), tuple(notes)


def _format_timestamp(unix_seconds: int | None) -> str:
    if unix_seconds is None:
        return "unknown date"
    return datetime.datetime.fromtimestamp(unix_seconds, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def _format_count(value: int | None) -> str:
    return f"{value:,}" if value is not None else "not published by this source"


def _truncate(text: str | None, limit: int = 140) -> str:
    if not text:
        return "(no caption)"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_social_data_md(label: str, results: tuple[SocialFetchResult, ...], notes: tuple[str, ...]) -> str:
    """The document `raw_post_data_source` asks for: real posts, a stated sample size, a stated
    collection method — for one account (the client, or one named competitor).

    Deliberately plain prose tables rather than a token schema like `DESIGN.md`: this is sample
    data for a prompt to read and summarise, not values a build will reference by name.
    """
    now = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## {label} — live social data (fetched via SociaVault on {now})", ""]

    if not results:
        lines.append(
            "No live data was fetched for this account. " + (" ".join(notes) if notes else "")
        )
        return "\n".join(lines).strip() + "\n"

    for result in results:
        lines += [
            f"### {result.platform.title()} — `{result.handle}`",
            "",
            f"Sample: {len(result.posts)} most recent post(s), fetched live via SociaVault. "
            + (
                "More posts exist in the account's history than were sampled here."
                if result.more_available
                else "This is the account's full recent history as returned by the API, not "
                "necessarily its full lifetime archive."
            ),
            "",
        ]
        if not result.posts:
            lines += [f"_{result.note or 'No posts were returned for this account.'}_", ""]
            continue
        lines += ["| Date | Likes | Comments | Shares | Video | Caption |", "|---|---|---|---|---|---|"]
        for post in result.posts:
            lines.append(
                f"| {_format_timestamp(post.published_at)} | {_format_count(post.like_count)} | "
                f"{_format_count(post.comment_count)} | {_format_count(post.share_count)} | "
                f"{'yes' if post.is_video else 'no'} | {_truncate(post.caption)} |"
            )
        lines.append("")

    if notes:
        lines += ["### Collection notes", ""] + [f"- {note}" for note in notes] + [""]

    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Engagement benchmarks — the real data the asset check's virality score is anchored on
# --------------------------------------------------------------------------------------

#: How many of an account's best posts are kept as the benchmark examples.
_TOP_POSTS = 5


def _engagement(post: sociavault_client.SocialPost) -> int | None:
    """Likes + comments + shares, over whichever of the three this platform publishes.

    None when it publishes none of them (LinkedIn today) — never 0, which would be a claim that the
    post got no engagement at all.
    """
    counts = [c for c in (post.like_count, post.comment_count, post.share_count) if c is not None]
    return sum(counts) if counts else None


def engagement_stats(label: str, results: tuple[SocialFetchResult, ...]) -> list[dict[str, object]]:
    """Per account and platform: sample size, median engagement, and the top posts' opening lines.

    Still no judgement — this is arithmetic over real counts. What the asset check does with it is
    compare a draft's hook against the hooks that actually travelled for this client and its
    competitors. Rows with no measurable engagement keep `median_engagement: None`.
    """
    stats: list[dict[str, object]] = []
    for result in results:
        scored = [(p, _engagement(p)) for p in result.posts]
        measured = sorted((e for _, e in scored if e is not None))
        median: float | None = None
        if measured:
            mid = len(measured) // 2
            median = float(measured[mid]) if len(measured) % 2 else (measured[mid - 1] + measured[mid]) / 2
        top = sorted((pe for pe in scored if pe[1] is not None), key=lambda pe: pe[1], reverse=True)[:_TOP_POSTS]
        stats.append(
            {
                "account": label,
                "platform": result.platform,
                "handle": result.handle,
                "sample_size": len(result.posts),
                "measured_posts": len(measured),
                "median_engagement": median,
                "top_posts": [
                    {
                        "engagement": e,
                        "is_video": p.is_video,
                        "opening": _truncate((p.caption or "").strip().splitlines()[0] if p.caption else None, 160),
                    }
                    for p, e in top
                ],
            }
        )
    return stats
