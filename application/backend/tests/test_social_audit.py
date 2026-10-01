"""Tests for `app/services/social_audit.py`.

The regression this file exists to prevent: `raw_post_data_source`'s own instruction requires every
report to disclose its real sample size and collection method, never presenting a sample as a full
archive. `test_render_states_the_sample_size_and_more_available_flag` pins that the rendered
document actually says so.
"""

from __future__ import annotations

import pytest

from app.services import context_dev, firecrawl_client, social_audit, sociavault_client
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


@pytest.mark.asyncio
async def test_resolve_competitor_handles_prefers_firecrawl(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_extract(url):
        assert url == "https://acme.com"
        return firecrawl_client.SocialLinks(
            source_url=url,
            facebook_url="https://facebook.com/acme",
            instagram_url="https://instagram.com/acme",
            linkedin_url="https://linkedin.com/company/acme",
        )

    async def fail_if_called(domain):
        raise AssertionError("Context.dev should not run when Firecrawl already found every platform")

    monkeypatch.setattr(firecrawl_client, "extract_social_links", fake_extract)
    monkeypatch.setattr(context_dev, "retrieve_brand", fail_if_called)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    # Context.dev is configured but never called — it is a fallback for what Firecrawl leaves
    # open, not a second full lookup run on top of a complete Firecrawl answer.
    assert set(a.platform for a in accounts) == {"facebook", "instagram", "linkedin"}


@pytest.mark.asyncio
async def test_resolve_competitor_handles_falls_back_to_context_dev_for_the_gap(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_extract(url):
        return firecrawl_client.SocialLinks(source_url=url, facebook_url="https://facebook.com/acme")

    class _Brand:
        socials = (("linkedin", "https://linkedin.com/company/acme"), ("instagram", "https://instagram.com/acme"))

    async def fake_retrieve_brand(domain):
        assert domain == "acme.com"
        return _Brand()

    monkeypatch.setattr(firecrawl_client, "extract_social_links", fake_extract)
    monkeypatch.setattr(context_dev, "retrieve_brand", fake_retrieve_brand)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    by_platform = {a.platform: a.handle for a in accounts}
    # Facebook is Firecrawl's own find and must not be overridden by Context.dev's.
    assert by_platform["facebook"] == "https://facebook.com/acme"
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
async def test_resolve_competitor_handles_returns_empty_when_neither_source_is_configured(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)

    assert await social_audit.resolve_competitor_handles("acme.com") == ()


@pytest.mark.asyncio
async def test_resolve_competitor_handles_degrades_when_firecrawl_errors(monkeypatch):
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)

    async def fake_extract(url):
        raise firecrawl_client.FirecrawlError("timed out")

    class _Brand:
        socials = (("facebook", "https://facebook.com/acme"),)

    async def fake_retrieve_brand(domain):
        return _Brand()

    monkeypatch.setattr(firecrawl_client, "extract_social_links", fake_extract)
    monkeypatch.setattr(context_dev, "retrieve_brand", fake_retrieve_brand)

    accounts = await social_audit.resolve_competitor_handles("acme.com")

    assert accounts == (social_audit.SocialAccount(platform="facebook", handle="https://facebook.com/acme"),)


def test_render_reports_when_no_live_data_was_fetched():
    markdown = social_audit.render_social_data_md(
        "Client", (), (f"{sociavault_client.API_KEY_ENV} is not set — no live post data was fetched.",)
    )
    assert "No live data was fetched" in markdown
    assert sociavault_client.API_KEY_ENV in markdown
