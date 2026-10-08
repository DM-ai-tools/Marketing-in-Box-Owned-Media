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
        '<img src="https://backend.example/media/1" alt="A consultant at work" width="1536" height="1024">'
        f"{body_extra}</main><footer></footer></body></html>\n```\n"
    )


def _issues(raw: str, manifest, design: str = _DESIGN):
    issues, _used = page_check.check_page(raw, manifest, design)
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
    facts = BusinessFacts(links=manifest, design_md=_DESIGN)
    report = await A.check_asset("pillar_page", _page(manifest, body_extra='<a href="#">x</a>'), facts)
    page = next(c for c in report.checks if c.check == "page_readiness")
    assert page.score == 85  # one must-fix, computed — the judge's 3 is ignored
    assert page.verdict.startswith("Not ready to deploy")
    assert prompts and "page_readiness" not in prompts[0]


def test_the_fix_context_carries_the_real_links(manifest) -> None:
    context = A.fix_context(BusinessFacts(links=manifest))
    assert "https://www.trafficradius.com.au/contact/" in context
