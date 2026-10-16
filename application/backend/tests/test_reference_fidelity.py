"""A generated page must carry the client's real logo, type, images and social marks.

Each test is one cause of a deployed Click Trends pillar page that came out wrong:

- the site served the deployed server a "we suspect you are a robot" page as an ordinary 200, and
  the brand fallback took that page's illustration (`robot-suspicion.svg`) as the client's logo;
- with the free read blocked, nothing read the page another way, so the logo, links, images,
  client logos and section order the page supplies were all missing;
- the body text was set in an icon font ("font awesome 5 pro");
- social links were plain words because the brief had no icons to draw;
- every image pointed at 127.0.0.1 because `BACKEND_PUBLIC_URL` was unset on Railway;
- the logo was the WordPress theme's demo mark ("Aimo", `themes/aimo/assets/img/logo.png`, left in
  the hidden mobile-header slot with `alt="Click Trends"`), while the real one — an upload inside
  `div.pxl-logo` whose alt text says "preview" — was never even a candidate.
"""
from __future__ import annotations

import pytest

from app.services import context_dev, design_md, firecrawl_client, brandfetch_client, media_storage
from app.services.design_tokens import DesignTokens, is_icon_font
from app.services.link_manifest import extract_link_manifest, links_markdown
from app.services.scraper import looks_like_block_page

URL = "https://clicktrends.com.au/seo/"

ROBOT_PAGE = """<!doctype html><html><head><title>Security check</title></head><body>
<img src="https://d1rozh26tys225.cloudfront.net/robot-suspicion.svg" alt="">
<h1>We suspect you are a robot</h1><p>Please confirm you are human to continue.</p></body></html>"""

REAL_PAGE = (
    "<!doctype html><html><head><title>AI-Powered SEO Services in Melbourne | Click Trends</title>"
    '<script src="https://www.google.com/recaptcha/api.js"></script></head><body>'
    '<header><div class="pxl-logo"> <a href="https://clicktrends.com.au/"> <img width="856" height="376" '
    'src="https://clicktrends.com.au/wp-content/uploads/2025/05/image-1-2.png" class="attachment-full" '
    'alt="Mobile-friendly Magento store preview"> </a></div>'
    '<div class="pxl-logo-mobile pxl-hide-xl"><a class="pxl-logo--dark" href="https://clicktrends.com.au/" '
    'title="Click Trends" rel="home"><img src="https://clicktrends.com.au/wp-content/themes/aimo/assets/img/logo.png" '
    'alt="Click Trends"></a></div>'
    '<nav><a href="/about-us/">About Us</a><a href="/seo/">SEO</a></nav></header><main><h1>Sick Of SEO</h1>'
    + "<p>" + " ".join(["Real copy about search engine optimisation for Melbourne businesses."] * 60) + "</p>"
    + "</main><footer>"
    '<a href="https://www.facebook.com/ClickTrendsAu/"><i class="fab fa-facebook-f"></i></a>'
    '<a href="https://www.instagram.com/clicktrendsau/"><i class="fab fa-instagram"></i></a>'
    "</footer></body></html>"
)


def _async(value):
    async def inner():
        return value

    return inner()


# ---------- block pages ----------


def test_a_robot_check_page_is_recognised() -> None:
    assert looks_like_block_page(ROBOT_PAGE)
    assert looks_like_block_page('<html><body><p>Verify you are human by completing the action below.</p></body></html>')


def test_a_real_page_that_loads_recaptcha_is_not_a_block_page() -> None:
    assert not looks_like_block_page(REAL_PAGE)


# ---------- logos from the fallback tier ----------


@pytest.mark.parametrize(
    ("logo", "kept"),
    [
        ("https://d1rozh26tys225.cloudfront.net/robot-suspicion.svg", False),
        ("https://clicktrends.com.au/wp-content/themes/aimo/assets/img/logo.png", False),
        ("https://clicktrends.com.au/wp-content/uploads/2025/05/image-1-2.png", True),
        ("https://www.clicktrends.com.au/logo.svg", True),
        ("https://cdn.brandfetch.io/clicktrends.com.au/logo.png", True),
        ("https://some-other-site.com/logo.png", False),
        ("https://clicktrends.com.au/captcha/logo.png", False),
    ],
)
def test_a_fallback_logo_must_be_the_clients(logo: str, kept: bool) -> None:
    assert (design_md._usable_fallback_logo(logo, URL) == logo) is kept


# ---------- the page's own logo, never the theme's ----------

REAL_LOGO = "https://clicktrends.com.au/wp-content/uploads/2025/05/image-1-2.png"


def test_the_header_logo_is_the_upload_not_the_themes_demo_mark() -> None:
    from app.services.design_tokens import _image_candidates

    candidates = _image_candidates(REAL_PAGE, URL)
    assert candidates and candidates[0].url == REAL_LOGO
    assert not any("/themes/" in c.url for c in candidates)


def test_the_logo_placement_names_the_upload_too() -> None:
    from app.services.page_structure import extract_page_structure

    placement = extract_page_structure(REAL_PAGE, URL).logo_placement
    assert REAL_LOGO in placement and "aimo" not in placement


@pytest.mark.asyncio
async def test_a_bot_walled_logo_check_keeps_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import design_tokens
    from app.services.scraper import ScrapeError

    async def challenged(url, **_k):
        raise ScrapeError(f"{url} challenged the request instead of serving the file.")

    monkeypatch.setattr(design_tokens, "fetch_asset", challenged)
    logo = await design_tokens.find_logo(REAL_PAGE, URL)
    assert logo is not None and logo.url == REAL_LOGO and not logo.verified
    sheet = " ".join(design_md._logo_lines(DesignTokens(source_url=URL, available=True, logo=logo)))
    assert "bot protection refused" in sheet


def test_a_rate_limited_capture_is_re_read() -> None:
    from app.routers.pipeline import DESIGN_CAPTURE_VERSION, _reuse_stored_design

    stored = {"source_url": URL, "capture_version": DESIGN_CAPTURE_VERSION, "notes": []}
    assert _reuse_stored_design(stored, URL)[0]
    stored["notes"] = ["A screenshot failed: Context.dev is rate-limiting this key; the retries were used up."]
    assert not _reuse_stored_design(stored, URL)[0]


# ---------- type ----------


@pytest.mark.parametrize(
    ("family", "icon"),
    [
        ('"font awesome 5 pro"', True),
        ("Font Awesome 6 Free, sans-serif", True),
        ("eicons", True),
        ("Material Symbols Outlined", True),
        ('"Rubik", Sans-serif', False),
        ("Oswald, sans-serif", False),
        ("Montserrat", False),
    ],
)
def test_icon_fonts_are_recognised(family: str, icon: bool) -> None:
    assert is_icon_font(family) is icon


def test_an_icon_font_never_becomes_the_body_face() -> None:
    fonts = context_dev.Fonts(
        source_url=URL,
        fonts=(
            context_dev.FontUsage(family="font awesome 5 pro", percent_words=40),
            context_dev.FontUsage(family="Rubik", fallbacks=("sans-serif",), percent_words=35),
        ),
    )
    tokens = DesignTokens(source_url=URL, available=True, font_families=[("Rubik", 10), ("Oswald", 8)])
    scale = design_md._typography_tokens(None, fonts, tokens)
    assert scale["body-md"]["fontFamily"] == "Rubik, sans-serif"


# ---------- reading a blocked page another way ----------


@pytest.mark.asyncio
async def test_a_blocked_free_read_is_replaced_by_the_rendered_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: False)
    monkeypatch.setattr(
        design_md, "extract_design_tokens", lambda url: _async(DesignTokens(source_url=url, available=False, reason="bot wall"))
    )

    async def fail(*_a, **_k):
        raise context_dev.ContextDevError("rate-limited") if hasattr(context_dev, "ContextDevError") else RuntimeError("rate-limited")

    monkeypatch.setattr(context_dev, "extract_styleguide", fail)
    monkeypatch.setattr(context_dev, "extract_fonts", fail)
    monkeypatch.setattr(context_dev, "screenshot", fail)

    async def rendered(url, **_k):
        calls.append(url)
        return context_dev.HTMLPage(url=url, final_url=url, title="Click Trends", html=REAL_PAGE)

    async def from_html(html, base_url):
        tokens = DesignTokens(source_url=base_url, available=True, font_families=[("Rubik", 10)])
        tokens.html = html
        return tokens

    monkeypatch.setattr(context_dev, "scrape_html", rendered)
    monkeypatch.setattr(design_md, "extract_design_tokens_from_html", from_html)

    design = await design_md.capture_page_design(URL, with_screenshots=False)
    assert calls == [URL], "the rendered page was not read once"
    assert "css parse (rendered via Context.dev)" in design.sources
    assert "links" in design.sources, "the page's own links were not read from the rendered page"
    assert any("read rendered via Context.dev" in n for n in design.notes)


@pytest.mark.asyncio
async def test_a_rendered_block_page_is_not_used_either(monkeypatch: pytest.MonkeyPatch) -> None:
    async def rendered(url, **_k):
        return context_dev.HTMLPage(url=url, final_url=url, title="Security check", html=ROBOT_PAGE)

    monkeypatch.setattr(context_dev, "scrape_html", rendered)
    tokens, note = await design_md._rendered_tokens(URL)
    assert tokens is None and "bot-check page too" in note


# ---------- social icons ----------


def test_social_links_come_with_their_icons() -> None:
    manifest = extract_link_manifest(REAL_PAGE, URL)
    section = "\n".join(links_markdown(manifest))
    assert "Draw each social link as its platform's icon" in section
    assert "- Facebook: `<svg" in section and "- Instagram: `<svg" in section
    assert "LinkedIn: `<svg" not in section, "only the platforms the page links are emitted"


# ---------- image links ----------


def test_railways_own_public_domain_is_the_media_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(media_storage.PUBLIC_BASE_URL_ENV, raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "backend-production.up.railway.app")
    url = media_storage.media_url("a729ee1b-6d78-4427-b064-8cef874233e2")
    assert url == "https://backend-production.up.railway.app/media/a729ee1b-6d78-4427-b064-8cef874233e2"
    # A link stored while the base was missing is rewritten on read.
    stored = "http://127.0.0.1:8001/media/a729ee1b-6d78-4427-b064-8cef874233e2"
    assert media_storage.current_media_url(stored) == url


def test_an_explicit_public_url_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "backend-production.up.railway.app")
    assert media_storage.media_url("x").startswith("https://api.example.com/media/")


def test_a_railway_deploy_with_no_public_address_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(media_storage.PUBLIC_BASE_URL_ENV, raising=False)
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert media_storage.public_base_url_problem()
