"""Tests for `app/services/design_md.py` and the stage routing that consumes it.

No live API. `build_design_md` is pure — it takes already-measured data — so the document tests
feed it fixtures shaped like real Context.dev payloads (the fixtures below are trimmed from an
actual `/web/styleguide` response for trafficradius.com.au). `capture_page_design` is tested by
patching the wrapper functions in `context_dev`, which is the seam that exists for it.
"""

from __future__ import annotations

import pytest
import yaml

from app.services import brandfetch_client, context_dev, design_md, firecrawl_client
from app.services.design_tokens import ButtonStyle, ColorUse, DesignTokens, Logo
from app.services.generation import (
    BRAND_THEME_STAGES,
    PAGE_REPLICA_STAGES,
    SCREENSHOT_STAGES,
    PageDesignInput,
    build_stage_request,
)

URL = "https://trafficradius.com.au/"


# ----------------------------------------------------------------------------------------------
# Fixtures shaped like the real payloads
# ----------------------------------------------------------------------------------------------


def _guide(**overrides) -> context_dev.Styleguide:
    base = dict(
        source_url=URL,
        mode="light",
        colors={"accent": "#a4d36b", "background": "#ffffff", "text": "#3d3d3d"},
        typography={
            "h1": {
                "fontFamily": "Montserrat",
                "fontFallbacks": ["Montserrat", "sans-serif"],
                "fontSize": "58px",
                "fontWeight": 500.0,
                "lineHeight": "70px",
                "letterSpacing": "0px",
            },
            "p": {
                "fontFamily": "Montserrat",
                "fontFallbacks": ["Montserrat", "sans-serif"],
                "fontSize": "17px",
                "fontWeight": 300.0,
                "lineHeight": "27px",
                "letterSpacing": "0px",
            },
        },
        spacing={"lg": "40px", "md": "24px", "sm": "14px", "xl": "90px", "xs": "6px"},
        shadows={"sm": "rgba(2, 2, 2, 0.23) 1px 1px 3px 0px", "md": "none", "inner": "none"},
        components={
            "button-primary": {
                "backgroundColor": "#a4d36b",
                "color": "#121212",
                "borderRadius": "30px",
                "padding": "13px 23px",
                "minHeight": "46px",
                "minWidth": "184px",
                "fontWeight": 500.0,
                "css": "background-color: #a4d36b;\n  border-radius: 30px;",
            },
            "button-link": {
                "color": "#3d3d3d",
                "borderRadius": "0px",
                "padding": "0px",
                "minHeight": "0px",
                "minWidth": "0px",
            },
            "card": {"backgroundColor": "#ffffff", "textColor": "#3d3d3d", "borderRadius": "15px"},
        },
        font_links={
            "Montserrat": {
                "type": "google",
                "category": "sans-serif",
                "files": {str(w): f"https://fonts.gstatic.com/montserrat-{w}.ttf" for w in range(100, 1000, 100)},
            }
        },
        raw={},
    )
    return context_dev.Styleguide(**{**base, **overrides})


def _fonts() -> context_dev.Fonts:
    return context_dev.Fonts(
        source_url=URL,
        fonts=(
            context_dev.FontUsage("Montserrat", ("sans-serif",), 95.0, 83.0, ("body",)),
            context_dev.FontUsage("Kanit", ("sans-serif",), 4.0, 13.0, ("a",)),
        ),
        font_links={"Montserrat": {"type": "google", "files": {"400": "https://x/m400.ttf"}}},
    )


def _tokens() -> DesignTokens:
    return DesignTokens(
        source_url=URL,
        available=True,
        palette=[
            ColorUse("#ffffff", background=230, text=297, border=46, root_background=3),
            ColorUse("#a4d36b", background=126, text=182, border=98),
            ColorUse("#072032", background=28, text=68, border=4),
        ],
        font_families=[("Montserrat, sans-serif", 400)],
        radii=[("4px", 30), ("8px", 12)],
        buttons=[ButtonStyle(selector=".cta,\n.cta-alt", background="#a4d36b", hover_background="#8fc05a")],
        logo=Logo(url="https://trafficradius.com.au/logo.svg", source="header img", alt_text="Traffic Radius"),
    )


def _front_matter(document: str) -> dict:
    return yaml.safe_load(document.split("---")[1])


# ----------------------------------------------------------------------------------------------
# The document
# ----------------------------------------------------------------------------------------------


def test_front_matter_is_valid_yaml_and_colours_survive_as_strings():
    """A bare `#a4d36b` opens a YAML comment. If the quoting is ever dropped the palette silently
    becomes null, and a build reading this file gets no colours while the file still looks right."""
    fm = _front_matter(design_md.build_design_md(URL, _guide(), _fonts(), _tokens()))

    assert fm["colors"]["primary"] == "#a4d36b"
    assert fm["colors"]["surface"] == "#ffffff"
    assert fm["colors"]["on-surface"] == "#3d3d3d"


def test_font_weights_are_integers_in_both_views():
    """The API returns 500.0. `font-weight: 500.0` is not a font weight."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _tokens())

    assert _front_matter(document)["typography"]["h1"]["fontWeight"] == 500
    assert "500.0" not in document


def test_the_type_scale_carries_fallbacks_not_a_bare_family():
    """A bare family name renders as whatever the reader's machine substitutes."""
    fm = _front_matter(design_md.build_design_md(URL, _guide(), _fonts(), _tokens()))

    assert fm["typography"]["h1"]["fontFamily"] == "Montserrat, sans-serif"


def test_the_body_face_comes_from_the_fonts_endpoint():
    """The styleguide reads `p` in isolation; the fonts endpoint measures which face sets 95% of
    the page's words. When they disagree, the measured one is the body face."""
    fonts = context_dev.Fonts(
        source_url=URL,
        fonts=(context_dev.FontUsage("Inter", ("system-ui",), 96.0),),
    )
    fm = _front_matter(design_md.build_design_md(URL, _guide(), fonts, _tokens()))

    assert fm["typography"]["body-md"]["fontFamily"] == "Inter, system-ui"


def test_the_radius_ladder_is_sorted_and_keeps_the_component_radius():
    """Sorting a merged list and slicing it dropped the client's 30px pill button in favour of
    small values off plugin widgets. Component radii are kept whole."""
    fm = _front_matter(design_md.build_design_md(URL, _guide(), _fonts(), _tokens()))
    rounded = fm["rounded"]

    assert "30px" in rounded.values(), rounded
    assert "15px" in rounded.values(), rounded
    pixels = [int(v.removesuffix("px")) for v in rounded.values() if v.endswith("px")]
    assert pixels == sorted(pixels), f"the ladder must ascend: {rounded}"


def test_percentage_radii_land_under_full_not_in_the_ladder():
    guide = _guide(components={"avatar": {"borderRadius": "50%"}, "card": {"borderRadius": "15px"}})
    rounded = _front_matter(design_md.build_design_md(URL, guide, None, None))["rounded"]

    assert rounded["full"] == "50%"
    assert "50%" not in [v for k, v in rounded.items() if k != "full"]


def test_zero_dimensions_are_not_emitted_as_component_tokens():
    """A text link samples 0px on every box property it has none of. `height: 0px` as a token
    would have a build render a zero-height button."""
    components = _front_matter(design_md.build_design_md(URL, _guide(), _fonts(), _tokens()))["components"]

    assert "height" not in components["button-link"]
    assert "rounded" not in components["button-link"]
    assert components["button-primary"]["height"] == "46px"


def test_only_the_weights_the_page_uses_are_listed():
    """Google serves nine weights per family. Listing all of them put ~1,500 characters of hashed
    filename into every HTML stage's prompt for a page that uses three."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _tokens())

    assert "montserrat-500.ttf" in document  # h1 and the button
    assert "montserrat-400.ttf" in document  # always, as the unstyled fallback
    assert "montserrat-300.ttf" in document  # body
    assert "montserrat-900.ttf" not in document
    assert "montserrat-100.ttf" not in document


def test_a_grouped_selector_does_not_break_out_of_its_code_span():
    """A grouped CSS selector carries a real newline, and a newline inside backticks ends the code
    span and breaks the markdown list item it sits in."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _tokens())

    assert "`.cta, .cta-alt`" in document
    for line in document.splitlines():
        if line.startswith("```"):  # a fence, not a code span
            continue
        assert line.count("`") % 2 == 0, f"unbalanced backticks: {line!r}"

    # The same collapsing applies to a computed `css` blob, which also arrives with newlines in it.
    assert "background-color: #a4d36b; border-radius: 30px;" in document


def test_the_logo_comes_from_the_css_parse_which_the_styleguide_cannot_give():
    """The one thing `/web/styleguide` has no field for, and the reason the local parse is still
    run alongside Context.dev rather than retired."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _tokens())

    assert "## Logo" in document
    assert "https://trafficradius.com.au/logo.svg" in document
    assert "do not recreate" in document.lower()


def test_palette_usage_roles_come_from_the_css_parse():
    """The styleguide names three colours. Which hex is a border and which is a surface is only
    measurable by counting where the page uses each one."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _tokens())

    assert "Mostly used as" in document
    assert "#072032" in document


# ----------------------------------------------------------------------------------------------
# The two views
# ----------------------------------------------------------------------------------------------


def test_the_theme_brief_drops_layout_elevation_and_components():
    theme = design_md.build_design_md(URL, _guide(), _fonts(), _tokens(), theme_only=True)
    fm = _front_matter(theme)

    assert set(fm) <= {"name", "colors", "typography", "rounded"}
    assert "spacing" not in fm
    assert "components" not in fm
    assert "## Layout" not in theme
    assert "## Elevation & Depth" not in theme
    assert "## Components" not in theme


def test_the_theme_brief_keeps_colours_type_shapes_and_the_logo():
    """What a lead magnet needs to be recognisably the same brand."""
    theme = design_md.build_design_md(URL, _guide(), _fonts(), _tokens(), theme_only=True)
    fm = _front_matter(theme)

    assert fm["colors"]["primary"] == "#a4d36b"
    assert fm["typography"]["h1"]["fontFamily"] == "Montserrat, sans-serif"
    assert fm["rounded"]
    assert "## Logo" in theme


def test_the_theme_brief_forbids_copying_the_source_layout():
    """Without this a lead magnet handed a landing page's design system builds a landing page."""
    theme = design_md.build_design_md(URL, _guide(), _fonts(), _tokens(), theme_only=True)

    assert "theme only" in theme.lower()
    assert "do not reproduce the source page's layout" in theme.lower()


def test_screenshots_are_listed_only_in_the_full_view():
    shots = (context_dev.Screenshot(URL, "https://img/full.png", "fullPage", 1937, 15550, "full page"),)
    full = design_md.build_design_md(URL, _guide(), _fonts(), _tokens(), screenshots=shots)
    theme = design_md.build_design_md(URL, _guide(), _fonts(), _tokens(), screenshots=shots, theme_only=True)

    assert "https://img/full.png" in full
    assert "https://img/full.png" not in theme


def test_a_flat_page_is_told_it_is_flat_rather_than_left_silent():
    """All-`none` shadows is a design decision. Left unsaid, a build adds the shadows it expects."""
    document = design_md.build_design_md(URL, _guide(shadows={"sm": "none", "md": "none"}), None, None)

    assert "uses no shadows" in document
    assert "Do not add shadows it does not have." in document


# ----------------------------------------------------------------------------------------------
# Degradation
# ----------------------------------------------------------------------------------------------


def test_the_css_parse_alone_still_produces_a_document():
    """When Context.dev is unconfigured or fails, the free local parse still carries a palette."""
    fm = _front_matter(design_md.build_design_md(URL, None, None, _tokens()))

    assert fm["colors"]["surface"] == "#ffffff"
    assert fm["colors"]


def test_an_unavailable_page_states_the_reason_and_forbids_invention():
    document = design_md._unavailable_md(URL, "the site refuses server-side reads")

    assert "NOT AVAILABLE" in document
    assert "the site refuses server-side reads" in document
    assert "Do not invent a palette" in document
    # Still a document, not an empty string: a stage handed nothing invents a palette.
    assert _front_matter(document)["name"] == "trafficradius.com.au"


# ----------------------------------------------------------------------------------------------
# capture_page_design
# ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_survives_one_reader_failing(monkeypatch):
    """A styleguide without fonts is still a design system. Each reading fails on its own."""
    async def ok_guide(url, **_):
        return _guide()

    async def boom(url, **_):
        raise context_dev.ContextDevError("fonts endpoint said 402")

    async def a_shot(url, **kwargs):
        return context_dev.Screenshot(url, "https://img/x.png", "viewport", 1440, 900, kwargs.get("label", ""))

    async def local_tokens(url):
        return _tokens()

    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "extract_styleguide", ok_guide)
    monkeypatch.setattr(context_dev, "extract_fonts", boom)
    monkeypatch.setattr(context_dev, "screenshot", a_shot)
    monkeypatch.setattr(design_md, "extract_design_tokens", local_tokens)
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: False)

    design = await design_md.capture_page_design(URL)

    assert design.available
    assert "styleguide" in design.sources
    assert "fonts" not in design.sources
    assert any("fonts" in note for note in design.notes)
    assert design.design_md and design.theme_brief


@pytest.mark.asyncio
async def test_an_oversized_screenshot_is_kept_but_withheld_from_the_model(monkeypatch):
    """Anthropic rejects an image past 8000px on either axis outright — it does not resize it."""
    async def tall_or_hero(url, **kwargs):
        if kwargs.get("full_page"):
            return context_dev.Screenshot(url, "https://img/full.png", "fullPage", 1937, 15550, "full page")
        return context_dev.Screenshot(url, "https://img/hero.png", "viewport", 1440, 900, "above the fold")

    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "extract_styleguide", lambda url, **_: _async(_guide()))
    monkeypatch.setattr(context_dev, "extract_fonts", lambda url, **_: _async(_fonts()))
    monkeypatch.setattr(context_dev, "screenshot", tall_or_hero)
    monkeypatch.setattr(design_md, "extract_design_tokens", lambda url: _async(_tokens()))
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: False)

    design = await design_md.capture_page_design(URL)

    assert len(design.screenshots) == 2
    assert [s.image_url for s in design.model_screenshots] == ["https://img/hero.png"]
    assert any("not sent to the model" in note for note in design.notes)


@pytest.mark.asyncio
async def test_no_key_means_the_local_parse_carries_it_alone(monkeypatch):
    """A deployment without a Context.dev key degrades to the old behaviour rather than failing."""
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)
    monkeypatch.setattr(design_md, "extract_design_tokens", lambda url: _async(_tokens()))
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: False)

    design = await design_md.capture_page_design(URL)

    assert design.available
    assert design.sources == ("css parse",)
    assert design.screenshots == ()


@pytest.mark.asyncio
async def test_everything_failing_is_unavailable_not_an_invented_palette(monkeypatch):
    """No Context.dev, no local parse, and no third-tier keys either — genuinely unavailable."""
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)
    monkeypatch.setattr(
        design_md,
        "extract_design_tokens",
        lambda url: _async(DesignTokens(source_url=url, available=False, reason="bot wall")),
    )
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: False)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: False)

    design = await design_md.capture_page_design(URL)

    assert not design.available
    assert "bot wall" in (design.reason or "")
    assert "Do not invent a palette" in design.design_md


async def _async(value):
    """Wrap a value as an already-resolved coroutine, for monkeypatching an async function."""
    return value


# ----------------------------------------------------------------------------------------------
# Third-tier fallback — Firecrawl, then Brandfetch, for named gaps only
# ----------------------------------------------------------------------------------------------


def test_fallback_never_overrides_a_value_context_dev_already_found():
    """The whole point of this tier: it plugs holes, it does not compete with Context.dev."""
    fallback = design_md.BrandFallback(source="firecrawl", primary="#000000", logo_url="https://evil/logo.png")
    document = design_md.build_design_md(URL, _guide(), _fonts(), _tokens(), fallback=fallback)
    fm = _front_matter(document)

    assert fm["colors"]["primary"] == "#a4d36b"  # the guide's colour, not the fallback's
    assert "https://trafficradius.com.au/logo.svg" in document  # the local parse's logo
    assert "https://evil/logo.png" not in document


def test_fallback_fills_a_named_gap_only():
    """With no guide and no local parse, the fallback's colour and font are all there is."""
    fallback = design_md.BrandFallback(source="firecrawl", primary="#123456", body_font="Inter")
    document = design_md.build_design_md(URL, None, None, None, fallback=fallback)
    fm = _front_matter(document)

    assert fm["colors"]["primary"] == "#123456"
    assert fm["typography"]["body-md"]["fontFamily"] == "Inter"
    assert "via firecrawl" in document


def test_fallback_logo_used_only_when_the_local_parse_has_none():
    fallback = design_md.BrandFallback(source="brandfetch", logo_url="https://client.com/logo.png")

    without_local_logo = design_md.build_design_md(URL, None, None, None, fallback=fallback)
    assert "https://client.com/logo.png" in without_local_logo
    assert "found via brandfetch" in without_local_logo.lower()

    with_local_logo = design_md.build_design_md(URL, None, None, _tokens(), fallback=fallback)
    assert "https://client.com/logo.png" not in with_local_logo
    assert "https://trafficradius.com.au/logo.svg" in with_local_logo


def test_brand_gaps_reports_only_what_is_actually_missing():
    assert design_md._brand_gaps(_guide(), _fonts(), _tokens()) == set()
    assert design_md._brand_gaps(None, None, None) == {"colors", "typography", "logo"}
    assert design_md._brand_gaps(None, None, _tokens()) == set()  # the CSS parse alone covers all three


@pytest.mark.asyncio
async def test_fallback_is_never_called_when_context_dev_left_no_gap(monkeypatch):
    """Spending a Firecrawl or Brandfetch call when nothing is missing would be the exact mistake
    the "credits are money" rule elsewhere in this codebase warns against."""
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "extract_styleguide", lambda url, **_: _async(_guide()))
    monkeypatch.setattr(context_dev, "extract_fonts", lambda url, **_: _async(_fonts()))
    monkeypatch.setattr(context_dev, "screenshot", lambda url, **kw: _async(
        context_dev.Screenshot(url, "https://img/x.png", "viewport", 100, 100, kw.get("label", ""))
    ))
    monkeypatch.setattr(design_md, "extract_design_tokens", lambda url: _async(_tokens()))
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: True)

    async def must_not_be_called(*_a, **_kw):
        raise AssertionError("firecrawl_client.extract_brand_tokens should not run with no gap")

    monkeypatch.setattr(firecrawl_client, "extract_brand_tokens", must_not_be_called)

    design = await design_md.capture_page_design(URL)

    assert design.available
    assert not any("brand fallback" in s for s in design.sources)


@pytest.mark.asyncio
async def test_brandfetch_fills_what_firecrawl_left_open(monkeypatch):
    """Firecrawl runs first; Brandfetch only fills what Firecrawl's own answer left empty."""
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)
    monkeypatch.setattr(
        design_md,
        "extract_design_tokens",
        lambda url: _async(DesignTokens(source_url=url, available=False, reason="bot wall")),
    )
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: True)

    async def fc_extract(url):
        return firecrawl_client.BrandTokens(source_url=url, primary_color="#123456")  # no logo

    async def bf_retrieve(domain):
        return brandfetch_client.BrandRecord(domain=domain, logo_url="https://cdn.brandfetch.io/trafficradius.com.au/logo.png")

    monkeypatch.setattr(firecrawl_client, "extract_brand_tokens", fc_extract)
    monkeypatch.setattr(brandfetch_client, "retrieve_brand", bf_retrieve)

    design = await design_md.capture_page_design(URL)

    assert design.available
    assert "brand fallback (firecrawl+brandfetch)" in design.sources
    assert "#123456" in design.design_md
    assert "https://cdn.brandfetch.io/trafficradius.com.au/logo.png" in design.design_md


@pytest.mark.asyncio
async def test_a_failed_fallback_call_is_a_note_not_a_crash(monkeypatch):
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)
    monkeypatch.setattr(
        design_md,
        "extract_design_tokens",
        lambda url: _async(DesignTokens(source_url=url, available=False, reason="bot wall")),
    )
    monkeypatch.setattr(firecrawl_client, "is_configured", lambda: True)
    monkeypatch.setattr(brandfetch_client, "is_configured", lambda: False)

    async def fc_boom(url):
        raise firecrawl_client.FirecrawlError("job failed")

    monkeypatch.setattr(firecrawl_client, "extract_brand_tokens", fc_boom)

    design = await design_md.capture_page_design(URL)

    assert not design.available  # nothing else answered either
    assert any("Firecrawl brand fallback" in note for note in design.notes)


# ----------------------------------------------------------------------------------------------
# Stage routing — which view each stage receives
# ----------------------------------------------------------------------------------------------


def test_the_replica_and_theme_stage_sets_do_not_overlap():
    assert PAGE_REPLICA_STAGES & BRAND_THEME_STAGES == frozenset()
    assert "cro" in PAGE_REPLICA_STAGES
    assert "lead_magnet" in BRAND_THEME_STAGES
    assert SCREENSHOT_STAGES <= PAGE_REPLICA_STAGES


def test_cro_gets_the_full_design_md_and_lead_magnet_gets_the_theme():
    design = PageDesignInput(design_md="FULL-SHEET-MARKER", theme_brief="THEME-SHEET-MARKER")

    _, cro = build_stage_request("cro", {"client_name": "Acme"}, "phase1", design)
    _, magnet = build_stage_request("lead_magnet", {"client_name": "Acme"}, "phase1", design)

    assert "FULL-SHEET-MARKER" in cro
    assert "THEME-SHEET-MARKER" not in cro
    assert "THEME-SHEET-MARKER" in magnet
    assert "FULL-SHEET-MARKER" not in magnet


def test_a_stage_with_only_one_view_still_gets_a_sheet():
    """Entries stored before DESIGN.md existed hold one sheet. They are read, not discarded."""
    legacy = PageDesignInput(design_md="LEGACY", theme_brief="LEGACY")

    _, magnet = build_stage_request("lead_magnet", {"client_name": "Acme"}, "phase1", legacy)
    assert "LEGACY" in magnet


def test_screenshots_reach_cro_as_image_blocks_ahead_of_the_text():
    """Images lead: the text ends with the master prompt's instruction to proceed, and an image
    appended after that sits between the instruction and the response."""
    design = PageDesignInput(
        design_md="FULL", theme_brief="THEME", screenshot_urls=("https://img/hero.png",)
    )

    _, content = build_stage_request("cro", {"client_name": "Acme"}, "phase1", design)

    assert isinstance(content, list)
    assert content[0] == {"type": "image", "source": {"type": "url", "url": "https://img/hero.png"}}
    assert content[-1]["type"] == "text"
    assert "FULL" in content[-1]["text"]


def test_no_screenshots_leaves_the_user_half_a_plain_string():
    """Only the stage that needs images pays the shape change; everything else stays a string."""
    design = PageDesignInput(design_md="FULL", theme_brief="THEME", screenshot_urls=("https://img/hero.png",))

    _, magnet = build_stage_request("lead_magnet", {"client_name": "Acme"}, "phase1", design)

    assert isinstance(magnet, str)
    assert "https://img/hero.png" not in magnet


def test_the_screenshot_directive_forbids_lifting_copy_off_the_image():
    """The old page's wording is exactly what a CRO rewrite is replacing."""
    design = PageDesignInput(design_md="FULL", screenshot_urls=("https://img/hero.png",))

    _, content = build_stage_request("cro", {"client_name": "Acme"}, "phase1", design)
    text = content[-1]["text"]

    assert "do not copy any words out of the screenshots" in text.lower()
    assert "do not eyedrop them off the image" in text.lower()


def test_a_stage_outside_the_brand_set_gets_nothing():
    """ICP writes structure and copy. It has no palette to get wrong."""
    design = PageDesignInput(design_md="FULL", theme_brief="THEME")

    _, icp = build_stage_request("icp", {"client_name": "Acme"}, "phase1", design)

    assert "FULL" not in icp
    assert "THEME" not in icp
    assert "BRAND_DESIGN_TOKENS" not in icp


def test_the_cached_prefix_never_carries_the_design_sheet():
    """The system half is the cache breakpoint. A per-client value there pins the cache to one
    client and shows up only as a permanent cache_read_input_tokens=0."""
    design = PageDesignInput(design_md="FULL-SHEET-MARKER", theme_brief="THEME-SHEET-MARKER")

    for asset_id in ("cro", "lead_magnet"):
        system_blocks, _ = build_stage_request(asset_id, {"client_name": "Acme"}, "phase1", design)
        if system_blocks is None:
            continue
        assert "SHEET-MARKER" not in system_blocks[0]["text"], asset_id
        assert "Acme" not in system_blocks[0]["text"], asset_id


# ----------------------------------------------------------------------------------------------
# Logo reproduction
#
# Regression cover. An earlier version of `_logo_lines` offered only the data URI when one existed
# and dropped the absolute URL. A real logo's data URI runs to ~16,000 characters of base64 — about
# 4,000 output tokens the model has to echo byte-perfectly while building a page — and when it
# declined, there was nothing else in the section to fall back to, so it drew a substitute
# wordmark. That looks entirely plausible until somebody who knows the brand sees it.
# ----------------------------------------------------------------------------------------------


def _logo_tokens(**overrides) -> DesignTokens:
    logo = Logo(
        url="https://trafficradius.com.au/logo.png",
        source="header logo",
        alt_text="Traffic Radius",
        mime_type="image/png",
        byte_size=11_662,
        data_uri="data:image/png;base64," + "A" * 15_000,
        **overrides,
    )
    return DesignTokens(source_url=URL, available=True, palette=[ColorUse("#ffffff", background=9)], logo=logo)


def test_the_absolute_logo_url_survives_alongside_the_data_uri():
    """Both forms, always. The URL is short and always reproducible; the data URI is not."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _logo_tokens())

    assert '<img src="https://trafficradius.com.au/logo.png"' in document
    assert '<img src="data:image/png;base64,' in document


def test_the_logo_url_is_offered_before_the_data_uri():
    """Order is the fix. Whichever form the model reaches first is the one it uses, and a 15k
    base64 blob is the one it is most likely to mangle."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _logo_tokens())

    assert document.index("logo.png") < document.index("base64,")


def test_a_logo_too_large_to_embed_still_gives_the_url():
    tokens = DesignTokens(
        source_url=URL,
        available=True,
        logo=Logo(url="https://x.test/big.png", source="header logo", byte_size=900_000),
    )
    document = design_md.build_design_md(URL, _guide(), _fonts(), tokens)

    assert '<img src="https://x.test/big.png"' in document
    assert "Do not attempt to inline it." in document
    assert "base64" not in document


def test_an_inline_svg_logo_is_pasted_whole():
    tokens = DesignTokens(
        source_url=URL,
        available=True,
        logo=Logo(url="", source="header svg", svg_markup='<svg viewBox="0 0 10 10"><path d="M0 0"/></svg>'),
    )
    document = design_md.build_design_md(URL, _guide(), _fonts(), tokens)

    assert '<svg viewBox="0 0 10 10">' in document


def test_the_logo_reaches_the_theme_stages_too():
    """A lead magnet without the client's logo is a worse lead magnet. The theme brief strips
    layout, not identity."""
    theme = design_md.build_design_md(URL, _guide(), _fonts(), _logo_tokens(), theme_only=True)

    assert "## Logo" in theme
    assert "https://trafficradius.com.au/logo.png" in theme


def test_the_dos_and_donts_repeat_the_logo_rule():
    """The tail of a 25,000-character document is where a model looks last, so the rule is there
    as well as in its own section."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _logo_tokens())
    tail = document.split("## Do's and Don'ts")[1]

    assert "logo" in tail.lower()
    assert "never redraw it" in tail.lower()


# ----------------------------------------------------------------------------------------------
# The sections the binding directive points at
# ----------------------------------------------------------------------------------------------


def _custom_prop_tokens() -> DesignTokens:
    return DesignTokens(
        source_url=URL,
        available=True,
        palette=[ColorUse("#a4d36b", background=40), ColorUse("#ffffff", background=90, root_background=4)],
        custom_properties={"--brand-primary": "#a4d36b", "--brand-radius": "30px"},
        font_families=[("Montserrat, sans-serif", 400)],
        notes=["Two stylesheets were skipped as plugin CSS."],
    )


def test_the_supplied_css_custom_properties_actually_exist():
    """`generation._BRAND_TOKEN_DIRECTIVE` ends with "Declare the supplied CSS custom properties at
    the top of your <style> block". For a while this document supplied none — an instruction
    pointing at a section that was not there."""
    from app.services.generation import _BRAND_TOKEN_DIRECTIVE

    assert "supplied CSS custom properties" in _BRAND_TOKEN_DIRECTIVE

    document = design_md.build_design_md(URL, _guide(), _fonts(), _custom_prop_tokens())
    assert "## The site's own design tokens" in document
    assert "`--brand-primary`" in document
    assert "### Ready-to-use CSS custom properties" in document
    assert ":root {" in document


def test_every_section_the_directive_names_is_present():
    """One test that fails the moment the directive and the document drift apart again."""
    document = design_md.build_design_md(URL, _guide(), _fonts(), _logo_tokens())

    assert "## Logo" in document            # "as supplied in the Logo section"
    assert "## Colors" in document          # "Use ONLY colours from the palette below"
    assert "## Typography" in document      # "Reproduce the font stacks verbatim"
    assert "## Components" in document      # "Match the button styles as given"


def test_reading_notes_are_carried_through():
    document = design_md.build_design_md(URL, _guide(), _fonts(), _custom_prop_tokens())

    assert "Two stylesheets were skipped as plugin CSS." in document
