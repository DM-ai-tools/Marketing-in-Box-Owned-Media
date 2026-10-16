"""`page_check.py` — is the pillar page deployable as delivered?

A clean page built from the fixture's own links passes with nothing to fix. Each broken page below
fails exactly the rule it was built to fail, so a rule that loosens or goes silent shows up here.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.services import asset_check as A
from app.services import page_check
from app.services.business_facts import BusinessFacts
from app.services.link_manifest import extract_link_manifest

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_BASE = "https://www.trafficradius.com.au/seo/"


@pytest.fixture(scope="module")
def manifest():
    html = (_FIXTURES / "landing_page.html").read_text(encoding="utf-8")
    return extract_link_manifest(html, _BASE)


#: The run's own generated image, as `GET /media/{id}` serves it. The clean page uses it, so a page
#: built the way the prompt asks passes the image-source rule.
_GENERATED = "https://backend.example/media/3f2b8c1e-9a4d-4e7b-8c1a-2d5e6f708192"

_DESIGN = '---\ncolors:\n  primary: "#e8590c"\n  surface: "#ffffff"\n  on-surface: "#1a1a1a"\n---\n'


def _page(manifest, *, body_extra: str = "", drop: str = "", head_extra: str = "", css: str = "") -> str:
    links = "".join(
        f'<a href="{link.href}">{link.label}</a>'
        for link in manifest.links
        if link.group in {"header", "footer", "social", "contact", "legal"} and link.href != drop
    )
    style = css or (
        ":root{--brand:#e8590c}body{color:#1a1a1a;background:#ffffff}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}"
        "@media (min-width:768px){.hero{padding:4rem}}"
    )
    return (
        "Notes before the page.\n\n```html\n"
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>SEO Services Melbourne</title>"
        '<meta name="description" content="SEO that brings in leads.">'
        f"{head_extra}<style>{style}</style></head><body>"
        f'<header><nav>{links}</nav></header><main><h1 id="top">SEO that works</h1>'
        '<a href="#top">Back to top</a>'
        f'<img src="{_GENERATED}" alt="A consultant at work" width="1536" height="1024">'
        f"{body_extra}</main><footer></footer></body></html>\n```\n"
    )


def _issues(raw: str, manifest, design: str = _DESIGN, generated=(_GENERATED,)):
    issues, _used = page_check.check_page(raw, manifest, design, generated_images=generated)
    return issues


def test_a_clean_page_has_nothing_to_fix(manifest) -> None:
    issues = _issues(_page(manifest), manifest)
    assert issues == []
    assert page_check.score(issues) == 100
    assert page_check.verdict(issues).startswith("Ready to deploy")


def test_a_missing_header_link_is_must_fix(manifest) -> None:
    contact = next(link for link in manifest.group("header") if link.label == "Contact")
    issues = _issues(_page(manifest, drop=contact.href), manifest)
    assert any(i.severity == "error" and contact.href in i.why for i in issues)


def test_dead_and_unlisted_links_are_flagged(manifest) -> None:
    raw = _page(
        manifest,
        body_extra=(
            '<a href="#">Learn more</a>'
            '<a href="https://www.trafficradius.com.au/made-up-page/">Made up</a>'
            '<a href="#nowhere">Jump</a>'
            '<a href="https://www.facebook.com/someoneelse">FB</a>'
        ),
    )
    issues = _issues(raw, manifest)
    whys = " ".join(i.why for i in issues)
    assert "goes nowhere" in whys
    assert "made-up-page" in whys
    assert "#nowhere" in whys
    assert "not the client's" in whys


def test_relative_and_trailing_slash_variants_count_as_the_same_link(manifest) -> None:
    raw = _page(manifest).replace(
        'href="https://www.trafficradius.com.au/contact/"', 'href="https://trafficradius.com.au/contact"'
    )
    assert _issues(raw, manifest) == []


def test_image_and_document_rules(manifest) -> None:
    raw = _page(
        manifest,
        body_extra=(
            '<img src="" data-image-prompt="a photo">'
            '<img src="https://images.unsplash.com/x.jpg" alt="stock" width="1" height="1">'
            "<h1>Second h1</h1>"
        ),
    ).replace("<title>SEO Services Melbourne</title>", "").replace(' lang="en"', "")
    whys = " ".join(i.why for i in _issues(raw, manifest))
    assert "placeholder" in whys
    assert "stock-photo" in whys
    assert "no alt attribute" in whys
    assert "no <title>" in whys
    assert "no lang" in whys
    assert "2 h1 headings" in whys


def test_responsive_and_palette_rules(manifest) -> None:
    raw = _page(manifest, css=".wrap{width:1200px;color:#123456}")
    whys = " ".join(i.why for i in _issues(raw, manifest))
    assert "no media queries" in whys
    assert "1200px" in whys
    assert "not in DESIGN.md's palette" in whys


_DROPDOWN = (
    '<nav class="main-nav"><ul><li><a href="/">Home</a></li>'
    '<li class="has-sub"{tab}><a href="/loans/">Loans</a>'
    '<ul class="sub-menu"><li><a href="/car/">Car</a></li><li><a href="/truck/">Truck</a></li></ul>'
    "</li></ul></nav>"
)
_DROPDOWN_CSS = (
    "{top}{{display:flex;gap:1rem}}"
    ".sub-menu{{display:none;position:absolute}}"
    ".has-sub:hover .sub-menu,.has-sub:focus-within .sub-menu{{display:block}}"
    "@media (min-width:768px){{.hero{{padding:4rem}}}}"
)


def test_a_descendant_menu_rule_that_forces_every_dropdown_open_is_must_fix(manifest) -> None:
    # The real ARG Finance pillar page: `nav.main-nav ul` (0,1,2) outranks `.sub-menu` (0,1,0), so
    # both dropdowns rendered open on load, laid out as wrapping flex rows.
    raw = _page(
        manifest,
        body_extra=_DROPDOWN.format(tab=' tabindex="0"'),
        css=_DROPDOWN_CSS.format(top="nav.main-nav ul"),
    )
    issues = _issues(raw, manifest)
    open_menu = [i for i in issues if "shows permanently" in i.why]
    assert open_menu and open_menu[0].severity == "error"
    assert "nav.main-nav > ul" in open_menu[0].fix
    assert any("tabindex" in i.why and i.severity == "warn" for i in issues)


def test_a_child_scoped_menu_rule_with_no_tabindex_is_clean(manifest) -> None:
    raw = _page(manifest, body_extra=_DROPDOWN.format(tab=""), css=_DROPDOWN_CSS.format(top="nav.main-nav > ul"))
    assert not [i for i in _issues(raw, manifest) if "menu" in i.why or "tabindex" in i.why]


def test_a_list_rule_outside_the_menu_is_not_a_sub_menu_problem(manifest) -> None:
    # A real Click Trends pillar page: `.toc ul` and `.footer-column ul` were reported as must-fix
    # "every sub-menu shows permanently", and "Fix with Refine" then tried to rewrite the CSS.
    toc = '<div class="toc"><ul><li><a href="#a">A</a></li></ul></div>'
    footer = '<div class="footer-column"><ul><li><a href="/x/">X</a></li></ul></div>'
    css = (
        _DROPDOWN_CSS.format(top="nav.main-nav > ul")
        + ".toc ul{display:flex;flex-wrap:wrap}.footer-column ul{display:grid}.scope-card ul{display:grid}"
    )
    raw = _page(manifest, body_extra=_DROPDOWN.format(tab="") + toc + footer, css=css)
    assert not [i for i in _issues(raw, manifest) if "shows permanently" in i.why]


def test_a_rule_through_the_menus_own_ancestors_is_still_caught(manifest) -> None:
    raw = _page(manifest, body_extra=_DROPDOWN.format(tab=""), css=_DROPDOWN_CSS.format(top=".main-nav li ul"))
    assert [i for i in _issues(raw, manifest) if "shows permanently" in i.why]


def test_without_a_manifest_the_link_rules_do_not_pretend_to_pass(manifest) -> None:
    issues, used = page_check.check_page(_page(manifest), None, _DESIGN)
    assert "the reference page's links" not in used
    assert issues == []


def test_a_draft_with_no_page_is_not_scored() -> None:
    assert page_check.check_page("Just prose, no page.", None) == ([], [])


@pytest.mark.asyncio
async def test_the_business_check_scores_page_readiness_without_asking_the_judge(manifest, monkeypatch) -> None:
    prompts: list[str] = []

    async def fake_call_judge(prompt, _on_usage):
        prompts.append(prompt)
        return json.dumps({"checks": {"page_readiness": {"score": 3, "verdict": "judge opinion", "findings": []}}})

    monkeypatch.setattr(A, "_call_judge", fake_call_judge)
    facts = BusinessFacts(links=manifest, design_md=_DESIGN, generated_image_urls=[_GENERATED])
    report = await A.check_asset("pillar_page", _page(manifest, body_extra='<a href="#">x</a>'), facts)
    page = next(c for c in report.checks if c.check == "page_readiness")
    assert page.score == 85  # one must-fix, computed — the judge's 3 is ignored
    assert page.verdict.startswith("Not ready to deploy")
    assert prompts and "page_readiness" not in prompts[0]


def test_the_fix_context_carries_the_real_links(manifest) -> None:
    context = A.fix_context(BusinessFacts(links=manifest))
    assert "https://www.trafficradius.com.au/contact/" in context


# ---------- image sources: generated, the reference page's own, or flagged ----------


def _img(src: str) -> str:
    return f'<img src="{src}" alt="x" width="10" height="10">'


def test_an_invented_image_src_is_flagged(manifest) -> None:
    issues = _issues(_page(manifest, body_extra=_img("https://cdn.trafficradius.com.au/made-up/hero.jpg")), manifest)
    flagged = [i for i in issues if "may be invented" in i.why]
    assert len(flagged) == 1 and flagged[0].severity == "warn"


def test_a_media_url_that_is_not_one_of_the_runs_images_is_flagged(manifest) -> None:
    other = "https://backend.example/media/00000000-0000-4000-8000-000000000000"
    issues = _issues(_page(manifest, body_extra=_img(other)), manifest)
    assert any("may be invented" in i.why for i in issues)


def test_a_generated_image_matches_whatever_host_it_was_written_with(manifest) -> None:
    moved = _GENERATED.replace("https://backend.example", "https://api.new-host.example")
    assert _issues(_page(manifest, body_extra=_img(moved)), manifest) == []


def test_a_localhost_image_src_is_an_error(manifest) -> None:
    for src in (
        "http://127.0.0.1:8001/media/3f2b8c1e-9a4d-4e7b-8c1a-2d5e6f708192",
        "http://localhost:8001/media/3f2b8c1e-9a4d-4e7b-8c1a-2d5e6f708192",
    ):
        issues = _issues(_page(manifest, body_extra=_img(src)), manifest)
        local = [i for i in issues if "served from this machine" in i.why]
        assert len(local) == 1 and local[0].severity == "error", src


def test_an_image_the_reference_page_serves_is_accepted(manifest) -> None:
    assert manifest.images, "the fixture page should list its own images"
    src = manifest.images[0]
    assert _issues(_page(manifest, body_extra=_img(src)), manifest) == []


def test_a_manifest_stored_before_images_were_listed_accepts_the_clients_own_host(manifest) -> None:
    from dataclasses import replace

    old = replace(manifest, images=None)
    own = _issues(_page(old, body_extra=_img("https://www.trafficradius.com.au/wp-content/x.png")), old)
    assert not any("may be invented" in i.why for i in own)
    elsewhere = _issues(_page(old, body_extra=_img("https://random-cdn.example/x.png")), old)
    assert any("may be invented" in i.why for i in elsewhere)


def test_with_no_image_facts_on_record_the_source_rule_does_not_run(manifest) -> None:
    from dataclasses import replace

    old = replace(manifest, images=None)
    raw = _page(old, body_extra=_img("https://random-cdn.example/x.png"))
    issues, _used = page_check.check_page(raw, None, _DESIGN)
    assert not any("may be invented" in i.why for i in issues)
    # Placeholder and stock findings are unchanged.
    stock = _page(old, body_extra=_img("https://images.unsplash.com/photo-1"))
    assert any("stock-photo" in i.why for i in _issues(stock, old))


def test_the_manifest_keeps_its_images_through_storage(manifest) -> None:
    from app.services.link_manifest import LinkManifest

    assert LinkManifest.from_dict(manifest.to_dict()).images == manifest.images
    legacy = manifest.to_dict()
    del legacy["images"]
    assert LinkManifest.from_dict(legacy).images is None


# ---------- unanswered [CLIENT TO CONFIRM] markers ----------

_MARKER = (
    '[CLIENT TO CONFIRM: named service beyond the consultation call — "Free development finance '
    'assessment" is not yet a confirmed offer and should not be asserted until confirmed]'
)


def test_a_visible_client_to_confirm_marker_is_must_fix(manifest) -> None:
    raw = _page(manifest, body_extra=f'<p class="note">{_MARKER}</p>')
    issues = _issues(raw, manifest)
    found = [i for i in issues if i.quote.startswith("[CLIENT TO CONFIRM")]
    assert len(found) == 1 and found[0].severity == "error"
    assert "visitors would read it" in found[0].why
    assert "never asserted" in found[0].fix
    assert page_check.verdict(issues).startswith("Not ready to deploy")


def test_every_distinct_marker_is_listed_once(manifest) -> None:
    markers = "".join(f"<p>[CLIENT TO CONFIRM: fact {n}]</p>" for n in range(9))
    raw = _page(manifest, body_extra=markers + "<p>[CLIENT TO CONFIRM: fact 0]</p><p>[TESTIMONIAL PLACEHOLDER]</p>")
    quotes = [i.quote for i in _issues(raw, manifest) if i.why.startswith("An unanswered question")]
    # Nine distinct client questions (the repeat counted once) plus the testimonial placeholder —
    # past the usual six-per-rule cap, because each one needs its own answer.
    assert len(quotes) == 10
    assert "[TESTIMONIAL PLACEHOLDER]" in quotes


def test_a_marker_a_visitor_cannot_see_is_not_flagged(manifest) -> None:
    hidden = f"<!-- {_MARKER} --><script>var note = '{_MARKER}';</script>"
    raw = _page(manifest, body_extra=hidden)
    assert not any(i.why.startswith("An unanswered question") for i in _issues(raw, manifest))


def test_ordinary_bracketed_copy_is_not_a_marker(manifest) -> None:
    raw = _page(manifest, body_extra="<p>Pricing [from $990] and [see terms].</p>")
    assert not any(i.why.startswith("An unanswered question") for i in _issues(raw, manifest))
