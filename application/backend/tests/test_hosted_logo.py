"""The logo is served by this backend, never hotlinked from the client's site.

A Click Trends pillar page shipped with no logo. `find_logo` had the right file
(`/wp-content/uploads/2025/05/image-1-2.png`, inside `div.pxl-logo`) and DESIGN.md handed the model
its URL, which the model used verbatim. SiteGround serves that file to a visitor on
clicktrends.com.au and refuses it to the same browser on any other site, so the preview, the HTML
report and the deployed page all showed a broken image. Any client's host can start doing that, so
the capture copies the bytes into `media_assets` and the document offers only that copy.
"""

from __future__ import annotations

import uuid

import pytest

from app.routers import pipeline
from app.services import brandfetch_client, context_dev, design_md, firecrawl_client, media_storage
from app.services.design_tokens import ColorUse, DesignTokens, Logo
from app.services.scraper import FetchedAsset, ScrapeError

URL = "https://clicktrends.com.au/seo/"
PAGE_LOGO = "https://clicktrends.com.au/wp-content/uploads/2025/05/image-1-2.png"
HOSTED = "https://api.example.app/media/0b5c3c9e-6f0e-4d38-9d4a-1f6f0c2a7e11"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _tokens(logo: Logo) -> DesignTokens:
    return DesignTokens(
        source_url=URL,
        available=True,
        palette=[ColorUse("#ffffff", background=230, text=297), ColorUse("#ff602f", background=126, text=182)],
        font_families=[("Oswald, sans-serif", 400)],
        logo=logo,
        html=f'<header><div class="pxl-logo"><img src="{PAGE_LOGO}" alt="Mobile-friendly Magento store preview"></div></header>',
    )


def _fetched_logo() -> Logo:
    return Logo(
        url=PAGE_LOGO,
        source="header logo",
        mime_type="image/png",
        byte_size=len(PNG),
        alt_text="Mobile-friendly Magento store preview",
        content=PNG,
    )


def _walled_logo() -> Logo:
    return Logo(url=PAGE_LOGO, source="header logo", alt_text="Click Trends logo", verified=False)


class _Host:
    """Stands in for the router's `host_image`: records what it was given, returns a URL."""

    def __init__(self, url: str | None = HOSTED, error: Exception | None = None):
        self.url, self.error, self.calls = url, error, []

    async def __call__(self, data: bytes, mime_type: str) -> str | None:
        self.calls.append((data, mime_type))
        if self.error:
            raise self.error
        return self.url


def _quiet(monkeypatch, tokens: DesignTokens, *, context_dev_on: bool = False, brandfetch_on: bool = False):
    async def local(url):
        return tokens

    monkeypatch.setattr(design_md, "extract_design_tokens", local)
    monkeypatch.setattr(context_dev, "is_configured", lambda: context_dev_on)
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: brandfetch_on)
    if context_dev_on:
        async def none(url, **_):
            return None

        monkeypatch.setattr(context_dev, "extract_styleguide", none)
        monkeypatch.setattr(context_dev, "extract_fonts", none)


@pytest.mark.asyncio
async def test_a_fetched_logo_is_hosted_and_the_document_offers_only_that_url(monkeypatch):
    _quiet(monkeypatch, _tokens(_fetched_logo()))
    host = _Host()

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=host)

    assert host.calls == [(PNG, "image/png")]
    assert design.hosted_logo is not None and design.hosted_logo.url == HOSTED
    assert design.hosted_logo.via == "the page itself"
    for document in (design.design_md, design.theme_brief):
        assert f'<img src="{HOSTED}"' in document
        # Not as a snippet, not in a table, not in the structure walk: one URL for the logo.
        assert PAGE_LOGO not in document
        assert "data:image" not in document


@pytest.mark.asyncio
async def test_the_wrong_alt_text_of_a_theme_slot_is_not_copied_onto_the_logo(monkeypatch):
    _quiet(monkeypatch, _tokens(_fetched_logo()))

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=_Host())

    assert "store preview" not in design.design_md.split("## Logo")[1]
    assert 'alt="Company logo"' in design.design_md
    assert design_md._logo_alt("Click Trends logo") == "Click Trends logo"


@pytest.mark.asyncio
async def test_a_walled_logo_is_copied_from_brandfetch(monkeypatch):
    _quiet(monkeypatch, _tokens(_walled_logo()), brandfetch_on=True)
    cdn = "https://cdn.brandfetch.io/clicktrends.com.au/logo.png"

    async def brand(domain):
        assert domain == "clicktrends.com.au"
        return brandfetch_client.BrandRecord(domain=domain, logo_url=cdn)

    async def fetch(url, **_):
        if url == cdn:
            return FetchedAsset(url, PNG, "image/png", None)
        raise ScrapeError("challenged")

    monkeypatch.setattr(brandfetch_client, "retrieve_brand", brand)
    monkeypatch.setattr(design_md, "fetch_asset", fetch)
    host = _Host()

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=host)

    assert design.hosted_logo is not None and design.hosted_logo.via == "brandfetch"
    assert design.hosted_logo.origin_url == cdn
    assert f'<img src="{HOSTED}"' in design.design_md
    assert PAGE_LOGO not in design.design_md


@pytest.mark.asyncio
async def test_context_dev_brand_record_is_the_last_resort(monkeypatch):
    _quiet(monkeypatch, _tokens(_walled_logo()), context_dev_on=True)
    asked = []

    async def brand(domain):
        asked.append(domain)
        return context_dev.BrandRecord(
            domain=domain, title=None, description=None, slogan=None,
            logos=(("icon", "https://media.brand.dev/icon.png"), ("logo", "https://media.brand.dev/logo.png")),
        )

    async def fetch(url, **_):
        return FetchedAsset(url, PNG, "image/png", None)

    async def rendered(url):
        return None, ""

    monkeypatch.setattr(context_dev, "retrieve_brand", brand)
    monkeypatch.setattr(design_md, "fetch_asset", fetch)
    monkeypatch.setattr(design_md, "_rendered_tokens", rendered)

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=_Host())

    assert asked == ["clicktrends.com.au"]
    # The full logo outranks the icon.
    assert design.hosted_logo is not None and design.hosted_logo.origin_url == "https://media.brand.dev/logo.png"


@pytest.mark.asyncio
async def test_no_paid_lookup_when_the_page_logo_was_already_fetched(monkeypatch):
    _quiet(monkeypatch, _tokens(_fetched_logo()), context_dev_on=True, brandfetch_on=True)

    async def must_not(*_a, **_k):
        raise AssertionError("a brand lookup was paid for a logo the page already supplied")

    monkeypatch.setattr(context_dev, "retrieve_brand", must_not)
    monkeypatch.setattr(brandfetch_client, "retrieve_brand", must_not)
    monkeypatch.setattr(design_md, "fetch_asset", must_not)

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=_Host())

    assert design.hosted_logo is not None


@pytest.mark.asyncio
async def test_the_preview_route_hosts_nothing_and_links_the_client_file(monkeypatch):
    """No `host_image`: the preview stores nothing, so it spends nothing on a logo copy either."""
    _quiet(monkeypatch, _tokens(_walled_logo()), brandfetch_on=True)

    async def must_not(*_a, **_k):
        raise AssertionError("the preview looked for a logo copy")

    monkeypatch.setattr(brandfetch_client, "retrieve_brand", must_not)
    monkeypatch.setattr(design_md, "fetch_asset", must_not)

    design = await design_md.capture_page_design(URL, with_screenshots=False)

    assert design.hosted_logo is None
    assert f'<img src="{PAGE_LOGO}"' in design.design_md


@pytest.mark.asyncio
async def test_a_logo_nothing_could_copy_is_linked_and_the_document_says_why(monkeypatch):
    _quiet(monkeypatch, _tokens(_walled_logo()))
    host = _Host()

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=host)

    assert host.calls == []
    assert design.hosted_logo is None
    assert f'<img src="{PAGE_LOGO}"' in design.design_md
    assert any("could not be copied" in note for note in design.notes)


@pytest.mark.asyncio
async def test_a_storage_failure_falls_back_to_the_client_file(monkeypatch):
    _quiet(monkeypatch, _tokens(_fetched_logo()))

    design = await design_md.capture_page_design(
        URL, with_screenshots=False, host_image=_Host(error=RuntimeError("db down"))
    )

    assert design.hosted_logo is None
    assert f'<img src="{PAGE_LOGO}"' in design.design_md
    assert any("could not be stored" in note for note in design.notes)


@pytest.mark.asyncio
async def test_an_inline_svg_logo_needs_no_copy(monkeypatch):
    svg = '<svg class="logo" viewBox="0 0 10 10"><title>logo</title></svg>'
    logo = Logo(url=URL, source="inline SVG in the site header", mime_type="image/svg+xml", svg_markup=svg)
    _quiet(monkeypatch, _tokens(logo))
    host = _Host()

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=host)

    assert host.calls == []
    assert design.hosted_logo is None


def test_a_stored_sheet_on_the_loopback_is_read_back_on_the_public_host(monkeypatch):
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://api.example.app")
    asset = uuid.uuid4()
    sheet = f'## Logo\n\n<img src="http://127.0.0.1:8001/media/{asset}" alt="Company logo">'

    design = pipeline._page_design_input({"design_md": sheet, "theme_brief": sheet})

    assert design is not None
    assert f"https://api.example.app/media/{asset}" in design.design_md
    assert "127.0.0.1" not in design.theme_brief
    # A real public host is never moved.
    assert media_storage.current_media_urls_in(HOSTED) == HOSTED


def test_existing_runs_recapture_to_pick_up_the_hosted_logo():
    reuse, _why = pipeline._reuse_stored_design({"source_url": URL, "capture_version": 6}, URL)
    assert not reuse


@pytest.mark.asyncio
async def test_every_other_page_image_the_sheet_offers_is_copied_too(monkeypatch):
    """The client-logo strip is offered as `<img src>` tags to copy, and is hotlinked the same way."""
    strip = "https://clicktrends.com.au/wp-content/uploads/2025/06/partner-a.png"
    walled = "https://clicktrends.com.au/wp-content/uploads/2025/06/partner-b.png"
    _quiet(monkeypatch, _tokens(_fetched_logo()))

    async def fetch(url, **_):
        if url == strip:
            return FetchedAsset(url, PNG, "image/png", None)
        raise ScrapeError("challenged")

    monkeypatch.setattr(design_md, "fetch_asset", fetch)
    copies = iter([HOSTED, "https://api.example.app/media/11111111-2222-3333-4444-555555555555"])

    async def host(data, mime):
        return next(copies)

    real_build = design_md.build_design_md

    def with_strip(*args, **kwargs):
        document = real_build(*args, **kwargs)
        return document + f'\n- `<img src="{strip}" alt="Partner A">`\n- `<img src="{walled}" alt="Partner B">`\n'

    monkeypatch.setattr(design_md, "build_design_md", with_strip)

    design = await design_md.capture_page_design(URL, with_screenshots=False, host_image=host)

    copied = "https://api.example.app/media/11111111-2222-3333-4444-555555555555"
    assert design.hosted_images == {strip: copied}
    assert strip not in design.design_md and copied in design.design_md
    # One that could not be read is linked, and said so.
    assert walled in design.design_md
    assert any("could not be copied" in note for note in design.notes)
