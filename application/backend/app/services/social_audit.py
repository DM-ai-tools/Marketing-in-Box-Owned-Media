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
from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from app.services import context_dev, firecrawl_client, scraper, sociavault_client
from app.services.html_dom import parse_html
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
        return sociavault_client.instagram_username(raw)
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


# Which host is which platform, after `www.` / `m.` / `web.` / a LinkedIn country prefix is dropped.
_PROFILE_HOSTS = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
}
_HOST_PREFIX = re.compile(r"^(?:www\.|m\.|web\.|[a-z]{2}\.(?=linkedin\.com$))")
_CANONICAL_HOST = {"facebook": "www.facebook.com", "instagram": "www.instagram.com", "linkedin": "www.linkedin.com"}

# First path segments that are never a business's own profile: the Meta pixel (`/tr`), share and
# intent buttons, and individual posts — a post linked from a page is often somebody else's.
_NOT_A_PROFILE = {
    "facebook": frozenset({
        "tr", "sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "login", "watch",
        "events", "photo", "photo.php", "permalink.php", "story.php", "hashtag", "help", "policies",
        "privacy", "business", "ads", "groups",
    }),
    "instagram": frozenset({"p", "reel", "reels", "tv", "explore", "stories", "accounts", "share"}),
}
# LinkedIn profiles always sit under one of these; everything else (`/posts/…`, `/shareArticle`,
# `/feed`, `/pulse`) is not one.
_LINKEDIN_PROFILE_KINDS = frozenset({"company", "in", "school", "showcase"})


def _profile_url(link: str) -> tuple[str, str] | None:
    """`(platform, canonical profile URL)` when `link` is a Facebook/Instagram/LinkedIn profile, else
    None. The canonical form drops tracking query strings and any trailing feed path, so the same
    profile linked from the header and the footer counts once."""
    parts = urlsplit(link.strip())
    if parts.scheme not in ("http", "https", ""):
        return None
    host = _HOST_PREFIX.sub("", parts.netloc.lower().split(":", 1)[0])
    platform = _PROFILE_HOSTS.get(host)
    segments = [s for s in parts.path.split("/") if s]
    if not platform or not segments:
        return None

    first = segments[0].lower()
    if platform == "linkedin":
        if first not in _LINKEDIN_PROFILE_KINDS or len(segments) < 2:
            return None
        return platform, f"https://www.linkedin.com/{first}/{segments[1]}"
    if first in _NOT_A_PROFILE[platform]:
        return None
    if platform == "facebook" and first == "profile.php":
        page_id = (parse_qs(parts.query).get("id") or [""])[0]
        return (platform, f"https://www.facebook.com/profile.php?id={page_id}") if page_id.isdigit() else None
    # `/name/posts/123`, `/name/videos/…`: a single post, not the page.
    if len(segments) > 1 and not (platform == "facebook" and first == "pages"):
        return None
    if platform == "facebook" and first == "pages":
        return platform, "https://www.facebook.com/" + "/".join(segments)
    return platform, f"https://{_CANONICAL_HOST[platform]}/{segments[0]}"


def social_profile_links(links: tuple[str, ...] | list[str]) -> dict[str, str]:
    """The one profile per platform a page links to, out of every href on it.

    When a page links two profiles on one platform, the one it links most often wins — a business
    puts its own in the header *and* the footer, and a partner's or a client's once in the copy. A
    tie keeps the first in document order."""
    counts: dict[str, Counter[str]] = {}
    for link in links:
        found = _profile_url(link)
        if found:
            counts.setdefault(found[0], Counter())[found[1]] += 1
    return {platform: tally.most_common(1)[0][0] for platform, tally in counts.items()}


async def _read_site_links(url: str) -> tuple[str, ...]:
    """Every `<a href>` on the page, through the free direct reader — no credits. `()` when the page
    could not be read; the paid tiers are there for that case."""
    try:
        final_url, html = await scraper._fetch_html(scraper.normalize_url(url))
    except scraper.ScrapeError as exc:
        logger.info("Competitor handle resolution: direct read failed for %r: %s", url, exc)
        return ()
    anchors = parse_html(html).root.find_all("a")
    return tuple(urljoin(final_url, a.attr("href")) for a in anchors if a.attr("href").strip())


def _fill_gaps(links: dict[str, str], found: dict[str, str]) -> None:
    """Each tier fills only the platforms the tiers before it left open — never an override."""
    for platform, url in found.items():
        if platform in sociavault_client.PLATFORMS and platform not in links:
            links[platform] = url


async def resolve_competitor_handles(domain: str) -> tuple[SocialAccount, ...]:
    """Resolve a competitor's bare domain (from `competitor_list`'s own `domain` field) to the
    social accounts `sociavault_client` can fetch real posts from.

    `competitor_list` names a company and a domain, never a social handle — a domain is not
    something SociaVault can look up. Almost every business site links to its own social profiles
    (a header icon, a footer, "follow us"), so reading those links off the page turns the domain
    into a real, fetchable handle instead of one this module would otherwise have to leave blank.

    Three tiers, cheapest first, each filling only the platforms the one before it left open:

    1. **The free direct read** (`scraper._fetch_html`), with the page's hrefs classified by
       `social_profile_links`. Most sites serve their footer icons in the HTML, so this usually
       answers on its own at no cost.
    2. **Firecrawl's links scrape** (`firecrawl_client.scrape_links`, 1 credit), for a page whose
       served HTML is a JS shell or refused the direct read. Classified by the same function, so a
       tracking pixel or share button is rejected identically whichever tier read the page.
    3. **Context.dev's `retrieve_brand`** (10 credits), whose brand record carries `socials`.

    This is the reverse of `design_md.py`'s Context.dev-primary ordering by explicit choice: a
    one-off per-competitor lookup, where the free and cheap reads answer most sites.

    Returns `()` when nothing found any social links — a competitor whose site links to no social
    profile is a real, statable finding, not an error.
    """
    url = _domain_url(domain)
    links: dict[str, str] = {}

    _fill_gaps(links, social_profile_links(await _read_site_links(url)))

    if len(links) < len(sociavault_client.PLATFORMS) and firecrawl_client.is_configured():
        try:
            _fill_gaps(links, social_profile_links(await firecrawl_client.scrape_links(url)))
        except firecrawl_client.FirecrawlError as exc:
            logger.info("Competitor handle resolution: firecrawl failed for %r: %s", domain, exc)

    if len(links) < len(sociavault_client.PLATFORMS) and context_dev.is_configured():
        try:
            brand = await context_dev.retrieve_brand(domain)
            found: dict[str, str] = {}
            for platform, social_url in brand.socials:
                found.setdefault(platform.strip().lower(), social_url)
            _fill_gaps(links, found)
        except context_dev.ContextDevError as exc:
            logger.info("Competitor handle resolution: context.dev failed for %r: %s", domain, exc)

    # Read off the competitor's own site, so these are profile URLs. Instagram's endpoint wants a
    # bare username and 400s on a URL; Facebook and LinkedIn take the URL as it is.
    return tuple(SocialAccount(platform=platform, handle=_resolve_handle(platform, handle)) for platform, handle in links.items())


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


_PLATFORM_NAMES = {"facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn"}
# Views are absent far more often than they are zero (every still image), so the table marks the
# absence briefly and says once, under it, what the mark means.
_NO_VIEWS = "—"


def _format_of(post: sociavault_client.SocialPost) -> str:
    if post.format:
        return post.format
    return "video" if post.is_video else "not stated"


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
        lines += [f"### {_PLATFORM_NAMES.get(result.platform, result.platform.title())} — `{result.handle}`", ""]
        if result.follower_count is not None:
            lines += [f"Followers: {result.follower_count:,}", ""]
        lines += [
            f"Sample: {len(result.posts)} most recent post(s), newest first, fetched live via SociaVault. "
            + (
                "More posts exist in the account's history than were sampled here."
                if result.more_available
                else "This is the account's full recent history as returned by the API, not "
                "necessarily its full lifetime archive."
            )
            + (
                f" {result.pinned_excluded} pinned post(s) were left out: a pinned post stays at the "
                "top of the profile whatever its age, so it says nothing about current cadence."
                if result.pinned_excluded
                else ""
            ),
            "",
        ]
        if not result.posts:
            lines += [f"_{result.note or 'No posts were returned for this account.'}_", ""]
            continue
        lines += [
            "| Date | Format | Likes | Comments | Shares | Views | Caption |",
            "|---|---|---|---|---|---|---|",
        ]
        for post in result.posts:
            lines.append(
                f"| {_format_timestamp(post.published_at)} | {_format_of(post)} | {_format_count(post.like_count)} | "
                f"{_format_count(post.comment_count)} | {_format_count(post.share_count)} | "
                f"{_NO_VIEWS if post.view_count is None else f'{post.view_count:,}'} | {_truncate(post.caption)} |"
            )
        lines += ["", f"_{_NO_VIEWS} under Views: no view count — a still image has none, and not every platform publishes one._", ""]

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
        followers = result.follower_count
        stats.append(
            {
                "account": label,
                "platform": result.platform,
                "handle": result.handle,
                "sample_size": len(result.posts),
                "measured_posts": len(measured),
                "median_engagement": median,
                "follower_count": followers,
                # Median engagement per 100 followers. Only where both halves are real measurements.
                "median_engagement_rate_pct": round(median / followers * 100, 3) if median is not None and followers else None,
                "formats": dict(Counter(_format_of(p) for p in result.posts)),
                "top_posts": [
                    {
                        "engagement": e,
                        "is_video": p.is_video,
                        "format": _format_of(p),
                        "views": p.view_count,
                        "opening": _truncate((p.caption or "").strip().splitlines()[0] if p.caption else None, 160),
                    }
                    for p, e in top
                ],
            }
        )
    return stats
