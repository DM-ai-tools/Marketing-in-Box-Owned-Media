"""`link_manifest.py` — every link on the reference page, with its real href.

The fixture is the same agency landing page `test_page_structure.py` reads. The synthetic pages
below each pin one shape the band walk got wrong for links: an icon-only social footer (no text, so
`_nav_groups` dropped it), a nested mega-menu (labels with no parent and no URL), and a page-builder
site whose header and footer are `<div>`s.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services.link_manifest import (
    LINKS_HEADING,
    LinkManifest,
    extract_link_manifest,
    links_markdown,
    social_platform,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_BASE = "https://www.trafficradius.com.au/seo/"


@pytest.fixture(scope="module")
def landing() -> LinkManifest:
    html = (_FIXTURES / "landing_page.html").read_text(encoding="utf-8")
    return extract_link_manifest(html, _BASE)


def _hrefs(manifest: LinkManifest, group: str) -> list[str]:
    return [link.href for link in manifest.group(group)]


def test_header_links_carry_their_real_absolute_hrefs(landing: LinkManifest) -> None:
    header = {link.label: link.href for link in landing.group("header")}
    assert header["SEO Services"] == "https://www.trafficradius.com.au/seo-services/"
    assert header["Contact"] == "https://www.trafficradius.com.au/contact/"
    assert header["Get a Free Quote"] == "https://www.trafficradius.com.au/free-quote/"


def test_footer_links_are_grouped_under_their_column_heading(landing: LinkManifest) -> None:
    columns = {link.label: link.parent for link in landing.group("footer")}
    assert columns["Google Ads"] == "Services"
    assert columns["Careers"] == "Company"


def test_legal_contact_and_cta_are_their_own_groups(landing: LinkManifest) -> None:
    assert _hrefs(landing, "legal") == ["https://www.trafficradius.com.au/privacy/"]
    assert "tel:1300049490" in _hrefs(landing, "contact")
    assert "mailto:hello@trafficradius.com.au" in _hrefs(landing, "contact")
    assert landing.primary_cta is not None
    assert landing.primary_cta.href == "https://www.trafficradius.com.au/free-quote/"


def test_placeholder_hrefs_are_never_listed(landing: LinkManifest) -> None:
    # The fixture's client logo strip links each logo to "#".
    assert all(not href.endswith("#") and href != "#" for href in landing.hrefs)


_ICON_FOOTER = """
<html><body>
<header><a href="/"><img src="/logo.svg" alt="Acme"></a>
  <nav><ul>
    <li><a href="/">Home</a></li>
    <li><span>Services</span>
      <ul>
        <li><a href="/services/seo/">SEO</a></li>
        <li><a href="/services/ppc/">PPC</a></li>
      </ul>
    </li>
    <li><a href="/about/">About</a>
      <ul><li><a href="/about/team/">Team</a></li></ul>
    </li>
  </ul></nav>
</header>
<main><section class="hero"><h1>Grow faster</h1><p>Some hero copy for the band walk to read.</p>
  <a class="elementor-button" href="/book-a-call/">Book a call</a>
  <a href="javascript:void(0)">Open menu</a>
</section>
<section><h2>More</h2><p>Body copy long enough to count as a band on its own.</p></section></main>
<footer>
  <div class="social">
    <a href="https://www.facebook.com/acmeco" aria-label="Facebook"><svg viewBox="0 0 10 10"><path d="M0 0"/></svg></a>
    <a href="https://instagram.com/acmeco/"><svg viewBox="0 0 10 10"><title>Instagram</title><path d="M0 0"/></svg></a>
    <a href="https://www.linkedin.com/company/acmeco"><i class="fa fa-linkedin"></i></a>
    <a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
    <a href="https://www.facebook.com/tr?id=1&ev=PageView"><img src="px.gif" alt=""></a>
  </div>
  <a href="/terms-and-conditions/">Terms</a>
  <a href="https://wa.me/61400000000">Chat on WhatsApp</a>
</footer>
</body></html>
"""


@pytest.fixture(scope="module")
def icon_page() -> LinkManifest:
    return extract_link_manifest(_ICON_FOOTER, "https://acme.example/landing/")


def test_icon_only_social_links_are_kept_and_named(icon_page: LinkManifest) -> None:
    social = {link.label: link.href for link in icon_page.group("social")}
    assert social == {
        "Facebook": "https://www.facebook.com/acmeco",
        "Instagram": "https://instagram.com/acmeco/",
        "LinkedIn": "https://www.linkedin.com/company/acmeco",
    }


def test_share_buttons_and_the_pixel_are_not_social_profiles(icon_page: LinkManifest) -> None:
    assert not any("sharer" in href or "/tr" in href for href in _hrefs(icon_page, "social"))
    assert social_platform("https://twitter.com/intent/tweet?text=x") == ""
    assert social_platform("https://www.youtube.com/@acme") == "YouTube"


def test_sub_menu_items_record_their_parent(icon_page: LinkManifest) -> None:
    parents = {link.label: link.parent for link in icon_page.group("header")}
    assert parents["SEO"] == "Services"
    assert parents["PPC"] == "Services"
    assert parents["Team"] == "About"
    assert parents["Home"] == ""


def test_button_classes_make_a_cta_and_javascript_links_are_dropped(icon_page: LinkManifest) -> None:
    assert _hrefs(icon_page, "cta") == ["https://acme.example/book-a-call/"]
    assert not any(href.startswith("javascript:") for href in icon_page.hrefs)


def test_whatsapp_is_contact_and_terms_is_legal(icon_page: LinkManifest) -> None:
    assert "https://wa.me/61400000000" in _hrefs(icon_page, "contact")
    assert _hrefs(icon_page, "legal") == ["https://acme.example/terms-and-conditions/"]


def test_page_builder_div_header_and_footer_are_recognised() -> None:
    html = """<html><body>
    <div class="elementor-location-header"><div class="menu">
      <a href="/a/">Alpha</a><a href="/b/">Beta</a><a href="/c/">Gamma</a></div></div>
    <div class="hero-wrap"><h1>Title here</h1><p>Hero body text, long enough.</p></div>
    <div class="content"><h2>Section</h2><p>Body text for the second band.</p></div>
    <div class="site-footer-wrap"><h4>Links</h4><a href="/d/">Delta</a><p>© Acme 2026, all rights.</p></div>
    </body></html>"""
    manifest = extract_link_manifest(html, "https://acme.example/")
    assert [link.label for link in manifest.group("header")] == ["Alpha", "Beta", "Gamma"]
    assert [link.label for link in manifest.group("footer")] == ["Delta"]


def test_a_long_mega_menu_is_not_truncated() -> None:
    items = "".join(f'<li><a href="/s/{i}/">Service {i}</a></li>' for i in range(45))
    html = f"<html><body><header><nav><ul>{items}</ul></nav></header><main><section><h1>Hi</h1><p>Body copy for the band.</p></section></main></body></html>"
    manifest = extract_link_manifest(html, "https://acme.example/")
    assert len(manifest.group("header")) == 45


def test_no_links_is_unavailable_with_a_reason() -> None:
    manifest = extract_link_manifest("<html><body><div id='root'></div></body></html>", "https://acme.example/")
    assert not manifest.available
    assert manifest.reason
    assert links_markdown(manifest) == []


def test_round_trips_through_its_stored_form(icon_page: LinkManifest) -> None:
    again = LinkManifest.from_dict(icon_page.to_dict())
    assert again is not None
    assert again.links == icon_page.links


def test_markdown_lists_every_href_verbatim_under_the_public_heading(landing: LinkManifest) -> None:
    text = "\n".join(links_markdown(landing))
    assert text.startswith(LINKS_HEADING)
    for href in landing.hrefs:
        assert f"`{href}`" in text
