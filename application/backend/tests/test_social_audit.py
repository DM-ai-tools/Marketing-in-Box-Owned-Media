"""Tests for `app/services/social_audit.py`.

The regression this file exists to prevent: `raw_post_data_source`'s own instruction requires every
report to disclose its real sample size and collection method, never presenting a sample as a full
archive. `test_render_states_the_sample_size_and_more_available_flag` pins that the rendered
document actually says so.
"""

from __future__ import annotations

import pytest

from app.services import context_dev, firecrawl_client, scraper, social_audit, sociavault_client
from app.services.sociavault_client import SocialFetchResult, SocialPost


def test_parse_account_handles_matches_the_field_s_own_example_format():
    text = "Facebook: /trafficradius, Instagram: @trafficradius, LinkedIn: /company/trafficradius, YouTube: @trafficradius"
    accounts = social_audit.parse_account_handles(text)

    # YouTube is not a platform this module covers — silently skipped, not an error.
    assert accounts == (
        social_audit.SocialAccount(platform="facebook", handle="https://www.facebook.com/trafficradius"),
        social_audit.SocialAccount(platform="instagram", handle="trafficradius"),
        social_audit.SocialAccount(platform="linkedin", handle="https://www.linkedin.com/company/trafficradius"),
    )


def test_parse_account_handles_accepts_full_urls_and_numeric_facebook_ids():
    text = "Facebook: 100063669491743, Instagram: https://instagram.com/acme"
    accounts = social_audit.parse_account_handles(text)

    assert accounts == (
        social_audit.SocialAccount(platform="facebook", handle="100063669491743"),
        # A pasted profile URL is reduced to the username: the Instagram endpoint 400s on a URL.
        social_audit.SocialAccount(platform="instagram", handle="acme"),
    )


def test_parse_account_handles_returns_empty_for_unrecognisable_text():
    assert social_audit.parse_account_handles("N/A") == ()
    assert social_audit.parse_account_handles("") == ()


@pytest.mark.asyncio
async def test_fetch_account_snapshot_is_a_no_op_when_sociavault_is_not_configured(monkeypatch):
    monkeypatch.setattr(sociavault_client, "is_configured", lambda: False)
    accounts = (social_audit.SocialAccount(platform="instagram", handle="acme"),)

    results, notes = await social_audit.fetch_account_snapshot(accounts)

    assert results == ()
    assert len(notes) == 1
    assert sociavault_client.API_KEY_ENV in notes[0]


@pytest.mark.asyncio
async def test_fetch_account_snapshot_degrades_one_platform_at_a_time(monkeypatch):
    """Instagram failing must not cost LinkedIn — each account/platform pair is independent."""
    monkeypatch.setattr(sociavault_client, "is_configured", lambda: True)

    async def fake_fetch(platform, handle, *, limit):
        if platform == "instagram":
            raise sociavault_client.SociaVaultError("account not found")
        return SocialFetchResult(platform=platform, handle=handle, posts=(), more_available=False, credits_used=1)

    monkeypatch.setattr(sociavault_client, "fetch_recent_posts", fake_fetch)
    accounts = (
        social_audit.SocialAccount(platform="instagram", handle="ghost"),
        social_audit.SocialAccount(platform="linkedin", handle="https://linkedin.com/company/acme"),
    )

    results, notes = await social_audit.fetch_account_snapshot(accounts)

    assert len(results) == 1
    assert results[0].platform == "linkedin"
    assert any("instagram" in note and "account not found" in note for note in notes)


def test_render_states_the_sample_size_and_more_available_flag():
    result = SocialFetchResult(
        platform="instagram",
        handle="acme",
        posts=(
            SocialPost(
                platform="instagram",
                id="1",
                url="https://instagram.com/p/1",
                published_at=1700000000,
                caption="a great post about roofing",
                like_count=120,
                comment_count=8,
                share_count=None,
                is_video=False,
            ),
        ),
        more_available=True,
        credits_used=1,
    )

    markdown = social_audit.render_social_data_md("Client", (result,), ())

    assert "1 most recent post" in markdown
    assert "more posts exist" in markdown.lower()
    assert "120" in markdown
    assert "not published by this source" in markdown  # share_count is None, not 0


def test_render_never_prints_none_engagement_as_zero():
    """LinkedIn posts have no engagement counts — the document must say so explicitly rather than
    printing a `0` that would claim a real measurement of no engagement."""
    result = SocialFetchResult(
        platform="linkedin",
        handle="https://linkedin.com/company/acme",
        posts=(
            SocialPost(
                platform="linkedin",
                id="1",
                url="https://linkedin.com/post/1",
                published_at=1700000000,
                caption="hiring announcement",
                like_count=None,
                comment_count=None,
                share_count=None,
            ),
        ),
        more_available=False,
        credits_used=1,
    )

    markdown = social_audit.render_social_data_md("Client", (result,), ())

    assert "| 0 |" not in markdown
    assert markdown.count("not published by this source") == 3  # likes, comments, shares


@pytest.fixture(autouse=True)
def _no_live_page_reads(monkeypatch):
    """The resolver's first tier is a real HTTP read of the competitor's page. No test may make one:
    each starts with that read finding nothing, and a test about the read swaps in its own page."""

    async def nothing(_url):
        return ()

    monkeypatch.setattr(social_audit, "_read_site_links", nothing)


# Shaped like entourage.com.au's footer on 2026-10-06: every profile is an icon with no text, so it
# never reaches a page's markdown — which is why the Firecrawl `/extract` this replaced came back
# empty on a page that linked all three. Plus everything that is *not* the business's profile.
_ICON_FOOTER_HTML = """
<html><head>
<noscript><img src="https://www.facebook.com/tr?id=111504249562459&ev=PageView&noscript=1"></noscript>
</head><body>
<header><a href="https://www.facebook.com/entouragefinance"><svg viewBox="0 0 24 24"><path d="M0"/></svg></a></header>
<main>
  <p>Read <a href="https://www.linkedin.com/posts/someone-else_activity-7492396789455638528-GdZ1">this post</a>
  and <a href="https://www.instagram.com/p/DPxyz123/">our feature</a>.</p>
  <a href="https://www.facebook.com/sharer/sharer.php?u=https://entourage.com.au">Share</a>
  <a href="https://www.linkedin.com/shareArticle?mini=true&url=x">Share</a>
  <a href="https://twitter.com/intent/tweet?url=x">Tweet</a>
</main>
<footer>
  <a href="https://www.facebook.com/entouragefinance/" aria-label="Facebook"><i class="icon-fb"></i></a>
  <a href="https://www.instagram.com/entourage_au/"><i class="icon-ig"></i></a>
  <a href="https://au.linkedin.com/company/entourage-finance?trk=footer"><i class="icon-li"></i></a>
</footer>
</body></html>
"""


@pytest.mark.asyncio
async def test_icon_only_footer_links_are_found_by_the_free_read(monkeypatch):
    """Tier 1, through the real reader path: the page's own hrefs, with no paid call at all."""
    monkeypatch.undo()  # drop the autouse stub; this test exercises `_read_site_links` itself
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_fetch_html(url):
        assert url.startswith("https://entourage.com.au")
        return "https://entourage.com.au/", _ICON_FOOTER_HTML

    async def must_not_be_called(*_args, **_kwargs):
        raise AssertionError("a paid tier ran although the free read found every platform")

    monkeypatch.setattr(scraper, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(firecrawl_client, "scrape_links", must_not_be_called)
    monkeypatch.setattr(context_dev, "retrieve_brand", must_not_be_called)

    accounts = await social_audit.resolve_competitor_handles("entourage.com.au")

    assert {a.platform: a.handle for a in accounts} == {
        "facebook": "https://www.facebook.com/entouragefinance",
        "instagram": "entourage_au",
        "linkedin": "https://www.linkedin.com/company/entourage-finance",
    }


@pytest.mark.parametrize(
    "link",
    [
        "https://www.facebook.com/tr?id=277483536540840&ev=PageView&noscript=1",
        "https://www.facebook.com/sharer/sharer.php?u=x",
        "https://www.facebook.com/entouragefinance/posts/pfbid0Zx3",
        "https://www.instagram.com/p/DPxyz123/",
        "https://www.instagram.com/reel/DPxyz123/",
        "https://www.linkedin.com/posts/arg-finance-pty-ltd_rba-activity-7510565171300511744-refm",
        "https://www.linkedin.com/shareArticle?mini=true&url=x",
        "https://twitter.com/argfinance",
        "mailto:hello@argfinance.com.au",
        "/contact",
    ],
)
def test_pixels_share_buttons_and_single_posts_are_not_profiles(link):
    assert social_audit.social_profile_links([link]) == {}


def test_the_profile_linked_most_often_wins_and_tracking_is_dropped():
    links = [
        "https://www.linkedin.com/company/a-client-we-mention",
        "https://www.linkedin.com/company/arg-finance-pty-ltd/",
        "https://www.linkedin.com/company/arg-finance-pty-ltd/?originalSubdomain=au",
        "https://m.facebook.com/argfinance?ref=footer",
    ]
    assert social_audit.social_profile_links(links) == {
        "linkedin": "https://www.linkedin.com/company/arg-finance-pty-ltd",
        "facebook": "https://www.facebook.com/argfinance",
    }


@pytest.mark.asyncio
async def test_firecrawl_links_fill_only_what_the_free_read_left_open(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def free_read(_url):
        return ("https://www.facebook.com/acme",)

    async def fake_scrape_links(url):
        assert url == "https://acme.com"
        return (
            "https://www.facebook.com/someone-else",  # must not override the free read's find
            "https://www.facebook.com/tr?id=1",
            "https://www.instagram.com/acme/",
            "https://www.linkedin.com/company/acme",
        )

    async def fail_if_called(domain):
        raise AssertionError("Context.dev should not run when the earlier tiers found every platform")

    monkeypatch.setattr(social_audit, "_read_site_links", free_read)
    monkeypatch.setattr(firecrawl_client, "scrape_links", fake_scrape_links)
    monkeypatch.setattr(context_dev, "retrieve_brand", fail_if_called)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    assert {a.platform: a.handle for a in accounts} == {
        "facebook": "https://www.facebook.com/acme",
        "instagram": "acme",
        "linkedin": "https://www.linkedin.com/company/acme",
    }


@pytest.mark.asyncio
async def test_resolve_competitor_handles_falls_back_to_context_dev_for_the_gap(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_scrape_links(url):
        return ("https://facebook.com/acme",)

    class _Brand:
        socials = (
            ("facebook", "https://facebook.com/not-this-one"),
            ("linkedin", "https://linkedin.com/company/acme"),
            ("instagram", "https://instagram.com/acme"),
        )

    async def fake_retrieve_brand(domain):
        assert domain == "acme.com"
        return _Brand()

    monkeypatch.setattr(firecrawl_client, "scrape_links", fake_scrape_links)
    monkeypatch.setattr(context_dev, "retrieve_brand", fake_retrieve_brand)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    by_platform = {a.platform: a.handle for a in accounts}
    # Facebook is Firecrawl's own find and must not be overridden by Context.dev's.
    assert by_platform["facebook"] == "https://www.facebook.com/acme"
    assert by_platform["linkedin"] == "https://linkedin.com/company/acme"
    # Reduced to the bare username: the Instagram endpoint 400s on a profile URL.
    assert by_platform["instagram"] == "acme"


@pytest.mark.asyncio
async def test_resolve_competitor_handles_uses_context_dev_when_firecrawl_is_unconfigured(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    class _Brand:
        socials = (("facebook", "https://facebook.com/acme"),)

    async def fake_retrieve_brand(domain):
        return _Brand()

    monkeypatch.setattr(context_dev, "retrieve_brand", fake_retrieve_brand)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    assert accounts == (social_audit.SocialAccount(platform="facebook", handle="https://facebook.com/acme"),)


@pytest.mark.asyncio
async def test_resolve_competitor_handles_returns_empty_when_nothing_finds_a_profile(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)

    assert await social_audit.resolve_competitor_handles("acme.com") == ()


@pytest.mark.asyncio
async def test_resolve_competitor_handles_degrades_when_firecrawl_errors(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_scrape_links(url):
        raise firecrawl_client.FirecrawlError("timed out")

    class _Brand:
        socials = (("facebook", "https://facebook.com/acme"),)

    async def fake_retrieve_brand(domain):
        return _Brand()

    monkeypatch.setattr(firecrawl_client, "scrape_links", fake_scrape_links)
    monkeypatch.setattr(context_dev, "retrieve_brand", fake_retrieve_brand)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    assert accounts == (social_audit.SocialAccount(platform="facebook", handle="https://facebook.com/acme"),)


@pytest.mark.asyncio
async def test_a_page_the_free_read_cannot_fetch_falls_through_to_firecrawl(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)

    async def refused(url):
        raise scraper.ScrapeError("blocked an automated read (HTTP 403)")

    async def fake_scrape_links(url):
        return ("https://www.instagram.com/acme",)

    monkeypatch.setattr(scraper, "_fetch_html", refused)
    monkeypatch.setattr(firecrawl_client, "scrape_links", fake_scrape_links)

    assert await social_audit.resolve_competitor_handles("acme.com") == (
        social_audit.SocialAccount(platform="instagram", handle="acme"),
    )


def test_render_states_format_views_followers_and_pinned_posts_left_out():
    result = SocialFetchResult(
        platform="instagram",
        handle="prospaau",
        posts=(
            SocialPost("instagram", "1", None, 1790894186, "carousel post", 91, 3, None, format="carousel"),
            SocialPost("instagram", "2", None, 1783559453, "a reel", 4, 0, None, is_video=True, view_count=352, format="reel"),
        ),
        more_available=True,
        credits_used=1,
        follower_count=12_400,
        pinned_excluded=3,
    )

    markdown = social_audit.render_social_data_md("Prospa", (result,), ())

    assert "| Format |" in markdown and "| Views |" in markdown
    assert "| reel |" in markdown and "| 352 |" in markdown
    assert "Followers: 12,400" in markdown
    assert "3 pinned post(s) were left out" in markdown
    # The carousel has no view count: marked as absent, not printed as a measured zero.
    assert "| — | carousel post |" in markdown

    stats = social_audit.engagement_stats("Prospa", (result,))[0]
    assert stats["follower_count"] == 12_400
    assert stats["formats"] == {"carousel": 1, "reel": 1}
    assert stats["median_engagement_rate_pct"] is not None
    assert stats["top_posts"][0]["format"] == "carousel"
    assert stats["top_posts"][1]["views"] == 352


def test_engagement_rate_is_none_without_a_follower_count():
    result = SocialFetchResult("facebook", "x", (SocialPost("facebook", "1", None, 1, "x", 5, 1, None),), False, 1)
    assert social_audit.engagement_stats("X", (result,))[0]["median_engagement_rate_pct"] is None


def test_render_reports_when_no_live_data_was_fetched():
    markdown = social_audit.render_social_data_md(
        "Client", (), (f"{sociavault_client.API_KEY_ENV} is not set — no live post data was fetched.",)
    )
    assert "No live data was fetched" in markdown
    assert sociavault_client.API_KEY_ENV in markdown


@pytest.mark.asyncio
async def test_resolve_competitor_handles_names_each_reader_that_found_a_profile(monkeypatch):
    """The prepass states it in the transcript when a paid fallback answered, so it needs to know."""
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_scrape_links(url):
        return ("https://facebook.com/acme",)

    class _Brand:
        socials = (("linkedin", "https://linkedin.com/company/acme"),)

    async def fake_retrieve_brand(domain):
        return _Brand()

    monkeypatch.setattr(firecrawl_client, "scrape_links", fake_scrape_links)
    monkeypatch.setattr(context_dev, "retrieve_brand", fake_retrieve_brand)

    readers: list[str] = []
    await social_audit.resolve_competitor_handles("acme.com", readers=readers)
    assert readers == ["Firecrawl", "Context.dev"]
