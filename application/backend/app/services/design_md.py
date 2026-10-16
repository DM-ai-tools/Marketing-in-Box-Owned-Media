"""Turn the client's live page into a DESIGN.md, so a generated page can be built from measurements.

Why this exists
---------------
`design_tokens.py` already argues the case: every HTML-producing prompt in this pipeline forbids
inventing a palette, and until that module existed the prompts were unfollowable because the only
thing reaching the model was stripped text. This module is the next step of the same argument.

Two things were still missing.

**A schema.** `tokens_to_markdown` emits prose tables — readable, but every consumer re-reads them
as English and there is no name for "the brand colour" that a build can reference. DESIGN.md is a
published format (YAML token front matter + human rationale) with a token vocabulary, so a value
extracted here has a name the generated CSS can carry, and the same file drops into a linter or a
Tailwind theme without a translation pass. Spec: `.claude/skills/design-spec/SKILL.md`.

**Computed styles.** The CSS parse in `design_tokens.py` is a regex over stylesheets. It cannot
resolve the cascade, so a heading font set four sheets deep, behind a `var()` chain, or inside a
media query is invisible to it. Context.dev's `/web/styleguide` reports what the browser actually
computed. That is the difference between a plausible palette and the real one.

How the two are merged
----------------------
Context.dev is primary and the local parse fills what it has no field for:

| From `/web/styleguide` + `/web/fonts` | From the local CSS parse |
| --- | --- |
| colors (accent / background / text)   | the real logo (inline SVG or data URI) |
| the h1-h4 + p type scale              | palette *usage roles* — which hex is a border, which is a surface |
| spacing and shadow scales             | button hover states |
| button and card component styles      | the site's own `--custom-property` names |
| webfont families and their file URLs  | |

Neither is asked to cover for the other. When Context.dev is unavailable the local parse still
produces a DESIGN.md on its own (thinner, and it says so); when both fail, `available=False` and a
stated reason — never a plausible invented palette, which is the one output that looks correct to
everybody except the client.

A third tier, for named gaps only
----------------------------------
Context.dev stays primary — this is not a reordering. `_brand_gaps` checks, after the two sources
above have answered, whether colours, a type scale, or a logo are still missing (a bot-walled
page, a JS theme the local parse cannot resolve). Only for gaps that are actually still open,
`_capture_brand_fallback` tries Firecrawl's structured extraction (`app/services/firecrawl_client.py`)
and then Brandfetch's brand-by-domain lookup (`app/services/brandfetch_client.py`), each filling
only the keys the one before it left empty. Neither ever overrides a value Context.dev or the
local parse already found — see `BrandFallback` and the `fallback` argument threaded through
`_color_tokens`, `_typography_tokens` and `_logo_lines` below.

Screenshots
-----------
A token sheet describes a palette. It does not describe a layout: section order, hero composition,
how much air sits between bands. Two shots are captured (2 credits) — a full-page one for structure
and an above-the-fold one at readable resolution — and handed to the CRO rewrite as image blocks.
See `Screenshot.model_safe`: a full-page shot of a long page exceeds what Anthropic accepts, and is
then kept for the operator but withheld from the prompt.

Two views out
-------------
* `design_md` — everything. For the CRO Page Rewrite, which is reproducing the page.
* `theme_brief` — colours, type, shapes and the logo, with the layout, spacing and component blocks
  removed. For lead magnets, which must look like the same brand without being a copy of the
  client's landing page.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from app.services import brandfetch_client, context_dev, firecrawl_client
from app.services.design_tokens import (
    DesignTokens,
    extract_design_tokens,
    extract_design_tokens_from_html,
    is_icon_font,
    is_neutral,
    is_theme_bundled_asset,
    tokens_to_css,
)
from app.services.scraper import looks_like_block_page
from app.services.image_briefs import ImageBrief, build_image_briefs
from app.services.link_manifest import LinkManifest, extract_link_manifest, links_markdown
from app.services.page_structure import PageStructure, extract_page_structure, structure_markdown

logger = logging.getLogger(__name__)

# How many extra brand colours from the local palette get a token name, past the three the
# styleguide names outright. Enough to cover a secondary and a couple of section tints; past that
# it is noise a build will never reference.
_MAX_EXTRA_COLORS = 5

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_LENGTH_PX = re.compile(r"^(\d+(?:\.\d+)?)px$")
_WHITESPACE = re.compile(r"\s+")


def _plain(value: object) -> str:
    """A measured value as it should read in prose.

    Exists because the API returns font weights as floats: rendering one straight gives
    "fontWeight 500.0", and a build that copies that emits `font-weight: 500.0`, which is not a
    font weight. The YAML side already normalises through `_scalar`; this is the prose side of the
    same rule, so the two views can never disagree about a number.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _WHITESPACE.sub(" ", str(value)).strip()


# ----------------------------------------------------------------------------------------------
# YAML emission
#
# Hand-written rather than via PyYAML, for two reasons: the project does not depend on it, and the
# front matter here is a fixed, shallow shape whose quoting rules matter (a bare `#a4d36b` starts a
# YAML comment and silently becomes null). `_scalar` is where that is enforced.
# ----------------------------------------------------------------------------------------------


def _scalar(value: object) -> str:
    """One YAML scalar, quoted when it has to be.

    Colours lead with `#` and MUST be quoted or YAML reads them as a comment. Dimensions like `58px`
    are safe bare but are quoted anyway for consistency with the spec's examples. Weights are
    emitted as integers — the API returns `500.0`, and `fontWeight: 500.0` is not a font weight.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if not text:
        return '""'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_block(name: str, entries: dict[str, object], indent: str = "  ") -> list[str]:
    """A mapping of scalars, or nothing at all when there is nothing measured to put in it.

    Omitting an empty group is deliberate: `shadows: {}` in the front matter reads as "this page has
    no shadows", which is a claim. An absent key says nothing, which is the truth when a page was
    only partly readable.
    """
    if not entries:
        return []
    lines = [f"{name}:"]
    for key, value in entries.items():
        lines.append(f"{indent}{key}: {_scalar(value)}")
    return lines


def _yaml_nested(name: str, entries: dict[str, dict[str, object]], indent: str = "  ") -> list[str]:
    """A mapping of mappings — `typography:` and `components:`."""
    populated = {k: v for k, v in entries.items() if v}
    if not populated:
        return []
    lines = [f"{name}:"]
    for key, props in populated.items():
        lines.append(f"{indent}{key}:")
        for prop, value in props.items():
            lines.append(f"{indent}{indent}{prop}: {_scalar(value)}")
    return lines


# ----------------------------------------------------------------------------------------------
# Token derivation
# ----------------------------------------------------------------------------------------------


def _color_tokens(
    guide: context_dev.Styleguide | None,
    tokens: DesignTokens | None,
    fallback: "BrandFallback | None" = None,
) -> dict[str, str]:
    """The palette, under the spec's recommended names.

    The mapping is a judgement and worth stating: the styleguide's `accent` becomes `primary`
    because it is the colour a person would point at and call "theirs", `background` becomes
    `surface` and `text` becomes `on-surface`, which is the pairing the spec uses. Everything past
    those three comes from the local parse's non-neutral colours, named by the role that parse
    measured (`brand-background-1`), because a hex with no stated role cannot be applied.
    """
    colors: dict[str, str] = {}

    if guide is not None:
        for source_key, token_name in (("accent", "primary"), ("background", "surface"), ("text", "on-surface")):
            value = guide.colors.get(source_key)
            if value and _HEX.match(value):
                colors[token_name] = value

    if tokens is not None and tokens.available:
        # Fall back to the CSS parse for anything the styleguide did not name, then top up with the
        # brand colours it has no slot for at all.
        for value, token_name in ((tokens.accent, "primary"), (tokens.page_background, "surface"), (tokens.body_text, "on-surface")):
            if token_name not in colors and value:
                colors[token_name] = value

        taken = set(colors.values())
        extras = [c for c in tokens.palette if not is_neutral(c.hex) and c.hex not in taken]
        for index, color in enumerate(extras[:_MAX_EXTRA_COLORS], start=1):
            colors[f"brand-{color.role}-{index}"] = color.hex

    if fallback is not None:
        # Third tier — only the names Context.dev and the local parse left unfilled. Never
        # overrides a value found above.
        for value, token_name in (
            (fallback.primary, "primary"),
            (fallback.secondary, "secondary"),
            (fallback.background, "surface"),
            (fallback.on_surface, "on-surface"),
        ):
            if token_name not in colors and value and _HEX.match(value):
                colors[token_name] = value

    return colors


def _typography_tokens(
    guide: context_dev.Styleguide | None,
    fonts: context_dev.Fonts | None,
    tokens: DesignTokens | None,
    fallback: "BrandFallback | None" = None,
) -> dict[str, dict[str, object]]:
    """The type scale, one entry per level the page actually declares.

    Level names stay as the source's own (`h1`..`h4`, plus `body-md` for `p`) rather than being
    remapped to the spec's `headline-lg`/`body-md` vocabulary throughout. The generated artifact is
    HTML built to match a specific page, so a token named `h1` lands on the `<h1>` it was measured
    from; renaming it would add a lookup for no gain.

    `fontFamily` is emitted as the full stack including fallbacks, because a bare family name
    renders as something else entirely on a machine that does not have it.
    """
    scale: dict[str, dict[str, object]] = {}

    if guide is not None:
        for level, style in guide.typography.items():
            entry: dict[str, object] = {}
            family = style.get("fontFamily")
            fallbacks = [str(f) for f in (style.get("fontFallbacks") or []) if f]
            if family and not is_icon_font(family):
                stack = ", ".join(dict.fromkeys([str(family), *fallbacks]))
                entry["fontFamily"] = stack
            for source_key, token_key in (
                ("fontSize", "fontSize"),
                ("fontWeight", "fontWeight"),
                ("lineHeight", "lineHeight"),
                ("letterSpacing", "letterSpacing"),
            ):
                value = style.get(source_key)
                if value not in (None, ""):
                    entry[token_key] = value
            if entry:
                scale["body-md" if level == "p" else level] = entry

    if not scale and tokens is not None and tokens.available and tokens.font_families:
        # No styleguide. The CSS parse knows the families but not the sizes, so the scale carries
        # only what was measured rather than a plausible set of sizes.
        scale["body-md"] = {"fontFamily": tokens.font_families[0][0]}
        if len(tokens.font_families) > 1:
            scale["h1"] = {"fontFamily": tokens.font_families[1][0]}

    # The fonts endpoint measures which face sets most of the page's words, which the per-level
    # styleguide reading cannot see. When they disagree, the body face wins for body text.
    # The heaviest face that is a typeface: an icon font can top the count on an icon-heavy page.
    body_face = next((f for f in (fonts.fonts if fonts is not None else ()) if not is_icon_font(f.family)), None)
    if body_face is not None and "body-md" in scale:
        scale["body-md"]["fontFamily"] = body_face.stack

    if not scale and fallback is not None and (fallback.body_font or fallback.heading_font):
        # Third tier — only reached when neither the styleguide nor the local parse named a
        # single family. No sizes are known at this tier, only which face to use.
        if fallback.body_font and not is_icon_font(fallback.body_font):
            scale["body-md"] = {"fontFamily": fallback.body_font}
        if fallback.heading_font and not is_icon_font(fallback.heading_font):
            scale["h1"] = {"fontFamily": fallback.heading_font}

    return scale


def _component_tokens(guide: context_dev.Styleguide | None, tokens: DesignTokens | None) -> dict[str, dict[str, object]]:
    """Button and card styles, under the spec's component property names.

    The styleguide's `css` field is dropped here and kept for the prose section instead: the front
    matter is typed tokens, and a raw declaration string is neither typed nor referenceable.
    """
    components: dict[str, dict[str, object]] = {}

    if guide is not None:
        for name, style in guide.components.items():
            entry: dict[str, object] = {}
            for source_key, token_key in (
                ("backgroundColor", "backgroundColor"),
                ("color", "textColor"),
                ("textColor", "textColor"),
                ("borderRadius", "rounded"),
                ("padding", "padding"),
                ("minHeight", "height"),
                ("minWidth", "width"),
            ):
                value = style.get(source_key)
                if value in (None, "", "none"):
                    continue
                # A text link samples as `0px` on every box property it has none of. Emitting
                # `height: 0px` as a token would have a build render a zero-height button.
                if token_key in ("rounded", "padding", "height", "width") and str(value).strip() in ("0", "0px"):
                    continue
                entry.setdefault(token_key, value)
            if entry:
                components[name] = entry

    if not components and tokens is not None and tokens.available and tokens.buttons:
        button = tokens.buttons[0]
        entry = {
            key: value
            for key, value in (
                ("backgroundColor", button.background),
                ("textColor", button.color),
                ("rounded", button.border_radius),
                ("padding", button.padding),
            )
            if value
        }
        if entry:
            components["button-primary"] = entry

    return components


# A radius at or past this many pixels is a pill, not a step on a ladder — as are `50%` and `100%`,
# which are circles. They belong under `full`, because sorting them into the numeric scale puts a
# pill button's 30px between a card's 15px and an avatar's 50% and implies a progression that isn't
# one.
_PILL_PX = 999

# Rungs on the radius ladder, past which a build is choosing rather than matching.
_MAX_RADII = 5


def _shape_tokens(guide: context_dev.Styleguide | None, tokens: DesignTokens | None) -> dict[str, str]:
    """Corner radii, as a named scale.

    Derived from what components actually use rather than invented as a 4/8/16 ladder: the radius
    that matters is the one on the client's buttons and cards, and a scale containing values the
    page never uses invites the build to pick one of them.

    Percentage and pill radii are separated out under `full` rather than ranked, and the remaining
    px values are sorted so `sm` really is smaller than `lg`. Unsorted, the scale came out
    `md: 30px, lg: 15px, sm: 2px` — insertion order from whichever component happened to be read
    first, and actively misleading to anything that reads a name as a size.
    """
    # Two tiers, because slicing a single merged list to a cap dropped the values that matter most.
    # A component radius is one a button or card is actually built with; a parse radius is any value
    # that appears anywhere in the stylesheet, including plugin CSS. Sorting them together and
    # keeping the smallest four cut the client's 30px pill button in favour of three 2-8px values
    # off widgets nobody sees. Component radii are kept whole; parse radii only fill what's left.
    component_radii: list[str] = []
    parse_radii: list[str] = []
    full: str | None = None

    def offer(value: object, into: list[str]) -> None:
        nonlocal full
        if not isinstance(value, str):
            return
        value = value.strip()
        if value in ("", "0", "0px", "none"):
            return
        match = _LENGTH_PX.match(value)
        if value.endswith("%") or (match and float(match.group(1)) >= _PILL_PX):
            full = full or value
            return
        if value not in component_radii and value not in parse_radii:
            into.append(value)

    if guide is not None:
        for style in guide.components.values():
            offer(style.get("borderRadius"), component_radii)
    if tokens is not None and tokens.available:
        for value, _count in tokens.radii:
            offer(value, parse_radii)

    seen = component_radii + parse_radii[: max(0, _MAX_RADII - len(component_radii))]

    def sort_key(value: str) -> tuple[int, float]:
        match = _LENGTH_PX.match(value)
        # Anything not in px (rem, em, a shorthand) sorts after the px ladder rather than being
        # dropped: it is still a real measured radius, it just cannot be compared numerically.
        return (0, float(match.group(1))) if match else (1, 0.0)

    seen.sort(key=sort_key)
    names = ("sm", "md", "lg", "xl", "2xl")
    scale = {names[i] if i < len(names) else f"r{i + 1}": value for i, value in enumerate(seen[:_MAX_RADII])}
    if full:
        scale["full"] = full
    return scale


# ----------------------------------------------------------------------------------------------
# The third-tier fallback — Firecrawl, then Brandfetch, for named gaps only
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BrandFallback:
    """The handful of brand values `_capture_brand_fallback` was able to recover, merged from
    whichever of Firecrawl and Brandfetch answered. Every field here is a value Context.dev and
    the local CSS parse did not find — never a value that overrides one they did."""

    source: str
    primary: str | None = None
    secondary: str | None = None
    background: str | None = None
    on_surface: str | None = None
    heading_font: str | None = None
    body_font: str | None = None
    logo_url: str | None = None


def _brand_gaps(
    guide: context_dev.Styleguide | None, fonts: context_dev.Fonts | None, tokens: DesignTokens | None
) -> set[str]:
    """Which named fields are still missing after Context.dev and the local CSS parse have both
    had a turn. Empty when there is nothing for the third tier to do — the common case, and the
    reason it is checked before spending a Firecrawl or Brandfetch call."""
    gaps: set[str] = set()
    if not _color_tokens(guide, tokens):
        gaps.add("colors")
    if not _typography_tokens(guide, fonts, tokens):
        gaps.add("typography")
    if tokens is None or not tokens.available or tokens.logo is None:
        gaps.add("logo")
    return gaps


async def _rendered_tokens(url: str) -> tuple[DesignTokens | None, str]:
    """The page's design tokens from Context.dev's rendered HTML, for a page the free read could not
    get. Returns (tokens or None, a note for the document)."""
    try:
        page = await context_dev.scrape_html(url)
    except Exception as exc:  # noqa: BLE001 — a failed fallback is a note, never a failed capture
        return None, f"The rendered page could not be read either: {exc}"
    if not page.html.strip() or looks_like_block_page(page.html):
        return None, "The rendered page was a bot-check page too, so the page's own logo, links and images are missing."
    try:
        tokens = await extract_design_tokens_from_html(page.html, page.final_url or url)
    except Exception as exc:  # noqa: BLE001
        return None, f"The rendered page could not be parsed: {exc}"
    if not tokens.available:
        return None, f"The rendered page gave no design tokens: {tokens.reason}"
    return tokens, "The free read was blocked, so the page was read rendered via Context.dev (1 credit)."


#: A logo URL that names a bot check rather than a brand. A fallback reader that was itself served
#: a block page reports the block page's illustration as the logo (`robot-suspicion.svg`).
_NOT_A_LOGO = re.compile(r"robot|captcha|challenge|suspicion|blocked|bot[-_]?check|access[-_]?denied", re.I)


def _usable_fallback_logo(logo_url: str | None, page_url: str) -> str | None:
    """A third-tier logo URL, or None when it cannot be the client's logo: a bot-check asset, or a
    file on another site that is not a known brand-asset host."""
    if not logo_url:
        return None
    if _NOT_A_LOGO.search(logo_url) or is_theme_bundled_asset(logo_url):
        # A theme's demo mark is on the client's own host and is still not the client's logo.
        return None
    logo_host = _host(logo_url)
    site = _host(page_url)
    same_site = logo_host == site or logo_host.endswith("." + site) or site.endswith("." + logo_host)
    brand_cdn = any(logo_host.endswith(h) for h in ("brandfetch.io", "brandfetch.com"))
    return logo_url if same_site or brand_cdn else None


def _host(url: str) -> str:
    """A bare domain for Brandfetch, which looks up by host rather than by full URL."""
    return re.sub(r"^https?://", "", url.strip()).split("/")[0].removeprefix("www.")


async def _capture_brand_fallback(url: str, needs: set[str]) -> tuple[BrandFallback | None, list[str]]:
    """Try to fill `needs` and nothing else. Firecrawl first, then Brandfetch for whatever
    Firecrawl left empty — each is optional, each failure is a note rather than a raised error,
    because this whole tier is best-effort by design: Context.dev already produced a usable
    document, and this only tries to make specific named gaps in it a little smaller.
    """
    notes: list[str] = []
    primary = secondary = background = on_surface = None
    heading_font = body_font = logo_url = None
    sources: list[str] = []

    if firecrawl_client.is_configured():
        try:
            fc = await firecrawl_client.extract_brand_tokens(url)
            primary = fc.primary_color
            secondary = fc.secondary_color
            background = fc.background_color
            on_surface = fc.text_color
            heading_font = fc.heading_font
            body_font = fc.body_font
            logo_url = _usable_fallback_logo(fc.logo_url, url)
            if fc.available:
                sources.append("firecrawl")
        except firecrawl_client.FirecrawlError as exc:
            logger.info("Brand fallback: firecrawl failed for %r: %s", url, exc)
            notes.append(f"Firecrawl brand fallback could not read {url}: {exc}")

    still_needs_colors = "colors" in needs and not (primary or secondary or background or on_surface)
    still_needs_fonts = "typography" in needs and not (heading_font or body_font)
    still_needs_logo = "logo" in needs and not logo_url

    if brandfetch_client.is_configured() and (still_needs_colors or still_needs_fonts or still_needs_logo):
        try:
            bf = await brandfetch_client.retrieve_brand(_host(url))
            if still_needs_colors and bf.colors:
                primary = primary or (bf.colors[0] if len(bf.colors) > 0 else None)
                secondary = secondary or (bf.colors[1] if len(bf.colors) > 1 else None)
            if still_needs_fonts:
                heading_font = heading_font or bf.heading_font
                body_font = body_font or bf.body_font
            if still_needs_logo:
                logo_url = logo_url or _usable_fallback_logo(bf.logo_url, url)
            if bf.available:
                sources.append("brandfetch")
        except brandfetch_client.BrandfetchError as exc:
            logger.info("Brand fallback: brandfetch failed for %r: %s", url, exc)
            notes.append(f"Brandfetch brand fallback could not read {url}: {exc}")

    if not sources:
        return None, notes

    return (
        BrandFallback(
            source="+".join(sources),
            primary=primary,
            secondary=secondary,
            background=background,
            on_surface=on_surface,
            heading_font=heading_font,
            body_font=body_font,
            logo_url=logo_url,
        ),
        notes,
    )


# ----------------------------------------------------------------------------------------------
# The document
# ----------------------------------------------------------------------------------------------


@dataclass
class PageDesign:
    """One page's design system, as a DESIGN.md plus the shots that show what it looks like.

    `available=False` is a first-class outcome for the same reason it is in `design_tokens.py`: a
    generated page in a plausible but wrong palette is indistinguishable from a correct one until
    somebody who knows the brand looks at it.
    """

    source_url: str
    available: bool
    reason: str | None = None
    #: The full DESIGN.md. For stages reproducing the page.
    design_md: str = ""
    #: Colours, type, shapes and logo only. For stages that must match the brand, not the layout.
    theme_brief: str = ""
    screenshots: tuple[context_dev.Screenshot, ...] = ()
    #: The page's skeleton, read off the same HTML the CSS parse fetched. Present in `design_md`
    #: and deliberately absent from `theme_brief` — see `build_design_md`.
    structure: PageStructure | None = None
    #: Every link on the page with its real href (`link_manifest.py`). Like `structure`, rendered
    #: into `design_md` only. Kept as data too, because the page check compares a generated page's
    #: hrefs against it.
    links: LinkManifest | None = None
    #: Which of the readings actually answered, for the log and the UI.
    sources: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    image_briefs: tuple[ImageBrief, ...] = ()

    @property
    def model_screenshots(self) -> tuple[context_dev.Screenshot, ...]:
        """The shots small enough for Anthropic to accept. See `Screenshot.model_safe`."""
        return tuple(shot for shot in self.screenshots if shot.model_safe)


_SPACING_ORDER = ("xs", "sm", "md", "lg", "xl")


def _ordered_spacing(spacing: dict[str, str]) -> dict[str, str]:
    """xs..xl, not whatever order the API happened to serialise them in. Anything unrecognised keeps
    its place at the end rather than being dropped."""
    known = {k: spacing[k] for k in _SPACING_ORDER if k in spacing}
    return known | {k: v for k, v in spacing.items() if k not in known}


def _front_matter(
    name: str,
    guide: context_dev.Styleguide | None,
    fonts: context_dev.Fonts | None,
    tokens: DesignTokens | None,
    *,
    theme_only: bool,
    fallback: "BrandFallback | None" = None,
) -> list[str]:
    """The YAML token block. `theme_only` drops layout, elevation and components."""
    colors = _color_tokens(guide, tokens, fallback)
    typography = _typography_tokens(guide, fonts, tokens, fallback)
    rounded = _shape_tokens(guide, tokens)

    lines = ["---", f"name: {_scalar(name)}"]
    lines += _yaml_block("colors", dict(colors))
    lines += _yaml_nested("typography", typography)
    lines += _yaml_block("rounded", dict(rounded))

    if not theme_only:
        lines += _yaml_block("spacing", dict(_ordered_spacing(guide.spacing if guide is not None else {})))
        shadows = {k: v for k, v in (guide.shadows if guide is not None else {}).items() if v and v != "none"}
        lines += _yaml_block("shadows", dict(shadows))
        lines += _yaml_nested("components", _component_tokens(guide, tokens))

    lines.append("---")
    return lines


def _used_weights(
    typography: dict[str, dict[str, object]], components: dict[str, dict[str, object]], guide: context_dev.Styleguide | None
) -> set[str]:
    """The font weights this page's own tokens actually reference.

    Google serves nine weights per family, and listing all of them for two families put eighteen
    URLs and roughly 1,500 characters of hashed filename into every HTML stage's prompt — for a
    page that uses three. The build can only reference a weight that appears in the type scale or
    on a component, so those are the ones worth carrying. 400 is always included: it is what any
    unstyled text falls back to.
    """
    weights = {"400"}
    for style in typography.values():
        weight = style.get("fontWeight")
        if weight not in (None, ""):
            weights.add(_plain(weight))
    for style in (guide.components.values() if guide is not None else ()):
        weight = style.get("fontWeight")
        if weight not in (None, ""):
            weights.add(_plain(weight))
    for style in components.values():
        weight = style.get("fontWeight")
        if weight not in (None, ""):
            weights.add(_plain(weight))
    return weights


def _webfont_lines(
    guide: context_dev.Styleguide | None,
    fonts: context_dev.Fonts | None,
    tokens: DesignTokens | None,
    weights: set[str],
) -> list[str]:
    """The font files to link. Verbatim URLs — a family name without its file renders as whatever
    the reader's machine substitutes, which is the commonest way a generated asset stops looking
    like the client's.

    Narrowed to `weights`; a family whose listed weights miss entirely still gets its regular, so a
    family is never named without a way to load it.
    """
    links: dict[str, dict] = {}
    for source in (guide, fonts):
        for family, meta in (getattr(source, "font_links", None) or {}).items():
            links.setdefault(family, meta)
    if not links:
        return [f"- `{link}`" for link in (tokens.font_links if tokens and tokens.available else [])]

    out: list[str] = []
    for family, meta in links.items():
        files = meta.get("files") or {}
        wanted = {w: url for w, url in files.items() if w in weights}
        if not wanted and files:
            fallback = "400" if "400" in files else sorted(files)[0]
            wanted = {fallback: files[fallback]}
        kind = meta.get("type") or "custom"
        category = f", {meta['category']}" if meta.get("category") else ""
        out.append(f"- **{family}** ({kind}{category})")
        for weight in sorted(wanted, key=lambda w: (len(w), w)):
            out.append(f"    - `{weight}` \u2192 `{wanted[weight]}`")
    return out


def _logo_lines(tokens: DesignTokens | None, fallback: "BrandFallback | None" = None) -> list[str]:
    """The client's own logo, in every form that can be pasted into generated HTML.

    Only the local CSS parse finds this — `/web/styleguide` has no logo field — which is the main
    reason that parse is still run alongside Context.dev rather than retired. When the local parse
    also finds nothing (a JS theme it cannot resolve) and `fallback.logo_url` was found by
    Firecrawl or Brandfetch, that URL is offered instead — see the tail of this function.

    **Both the absolute URL and the data URI are always given when both exist**, URL first. An
    earlier version of this function offered only the data URI when one was available, and that
    silently broke logo reproduction: a data URI for a real logo runs to ~16,000 characters of
    base64, which is roughly 4,000 output tokens the model has to echo byte-perfectly in the middle
    of building a page. When it declines — or truncates, or corrupts a run of it — there was
    nothing else in the section to fall back to, so it drew a substitute wordmark instead. A
    recreated logo is the single most obvious tell that an asset was not made by the client, and it
    looks entirely plausible until somebody who knows the brand sees it.

    So the URL leads (short, always reproducible, on the client's own domain) and the data URI is
    offered second, for when a self-contained file matters more than a short one.
    """
    if tokens is None or not tokens.available or tokens.logo is None:
        if fallback is not None and fallback.logo_url:
            return [
                "## Logo — use THIS, do not recreate it",
                "",
                "Use the client's real mark exactly as given below. Do NOT redraw it as an SVG of "
                "your own, do NOT set the company name as styled text, and do NOT substitute an "
                "icon. Preserve its aspect ratio: set one dimension and leave the other `auto`.",
                "",
                f"Found via {fallback.source} — the local parse and Context.dev's styleguide "
                "found no logo, so this is the only form available:",
                "",
                "```html",
                f'<img src="{fallback.logo_url}" alt="Company logo">',
                "```",
                "",
            ]
        return []
    logo = tokens.logo

    out = [
        "## Logo — use THIS, do not recreate it",
        "",
        "Use the client's real mark exactly as given below. Do NOT redraw it as an SVG of your own, "
        "do NOT set the company name as styled text, and do NOT substitute an icon. Preserve its "
        "aspect ratio: set one dimension and leave the other `auto`.",
        "",
    ]

    if logo.svg_markup:
        out += [
            f"Found as an {logo.source}. It is inline SVG, so paste this markup directly into the "
            "generated HTML — it scales, and it can be recoloured with `currentColor` where the "
            "original does:",
            "",
            "```html",
            logo.svg_markup,
            "```",
        ]
        return out + [""]

    alt = logo.alt_text or "Company logo"
    out += ["| Field | Value |", "|---|---|", f"| Found as | {logo.source} |", f"| URL | `{logo.url}` |"]
    if logo.mime_type:
        out.append(f"| Type | {logo.mime_type} |")
    if logo.byte_size:
        out.append(f"| Size | {logo.byte_size:,} bytes |")
    if logo.alt_text:
        out.append(f"| Alt text | {logo.alt_text} |")
    if logo.width or logo.height:
        out.append(f"| Declared size | {logo.width or '?'} x {logo.height or '?'} |")

    out += [
        "",
        "**Either of these is correct — use one, verbatim.** The URL is the client's own logo on the "
        "client's own domain, and is the safer choice in a long document:",
        "",
        "```html",
        f'<img src="{logo.url}" alt="{alt}">',
        "```",
    ]

    if logo.data_uri:
        out += [
            "",
            "Or, if the generated file has to stand alone with no external asset, the same image "
            "inlined — copy the whole string, every character:",
            "",
            "```html",
            f'<img src="{logo.data_uri}" alt="{alt}">',
            "```",
        ]
    elif not logo.verified:
        out += [
            "",
            "This is the file the page itself shows; the site's bot protection refused our own "
            "check of it, so it could not be embedded. Use the URL above.",
        ]
    else:
        out += [
            "",
            "Too large to embed"
            + (f" ({logo.byte_size:,} bytes)" if logo.byte_size else "")
            + ", so the URL above is the only form. Do not attempt to inline it.",
        ]

    return out + [""]


def _site_token_lines(tokens: DesignTokens | None) -> list[str]:
    """The site's own `--custom-property` names, and a `:root` block ready to paste.

    Restored after being dropped: the binding directive in `generation.py` ends with "Declare the
    supplied CSS custom properties at the top of your <style> block and reference them throughout",
    and for a while this document supplied none — an instruction pointing at a section that was not
    there. Reusing the theme's own names is also what keeps a generated page speaking the same
    language as the real one, so a developer can diff the two.
    """
    if tokens is None or not tokens.available:
        return []

    out: list[str] = []
    if tokens.custom_properties:
        out += [
            "## The site's own design tokens",
            "",
            "Declared by the theme itself. Reuse these names wherever the generated page needs the "
            "same value.",
            "",
            "| Property | Value |",
            "|---|---|",
        ]
        out += [f"| `{name}` | `{value}` |" for name, value in list(tokens.custom_properties.items())[:30]]
        out.append("")

    css = tokens_to_css(tokens)
    if css:
        out += [
            "### Ready-to-use CSS custom properties",
            "",
            "Paste this at the top of the generated `<style>` block and reference it throughout, "
            "rather than repeating the same hex in twenty places:",
            "",
            "```css",
            css,
            "```",
            "",
        ]

    if tokens.notes:
        out += ["### Reading notes", ""] + [f"- {note}" for note in tokens.notes] + [""]

    return out


def build_design_md(
    source_url: str,
    guide: context_dev.Styleguide | None,
    fonts: context_dev.Fonts | None,
    tokens: DesignTokens | None,
    *,
    screenshots: tuple[context_dev.Screenshot, ...] = (),
    structure: PageStructure | None = None,
    links: LinkManifest | None = None,
    theme_only: bool = False,
    fallback: BrandFallback | None = None,
) -> str:
    """Render the DESIGN.md. Pure — every argument is already-measured data, so this is the part
    covered by tests without a network.

    `structure` lands only in the full view. A theme stage is told in as many words not to reproduce
    the source page's layout (`_THEME_ONLY_DIRECTIVE` in `generation.py`), and handing it a band-by-
    band description of that layout is the most effective way to get it reproduced anyway — the
    document would be arguing with the instruction, and the document is longer."""
    name = _site_name(source_url)
    colors = _color_tokens(guide, tokens, fallback)
    typography = _typography_tokens(guide, fonts, tokens, fallback)
    components = _component_tokens(guide, tokens)

    out = _front_matter(name, guide, fonts, tokens, theme_only=theme_only, fallback=fallback)
    out += ["", f"# {name}", "", "## Overview", ""]
    out += [
        f"Measured from the client's own live page at <{source_url}>. Every value in the token block "
        "above was read off that page — none of it is a recommendation, a refinement, or a default.",
        "",
    ]
    if theme_only:
        out += [
            "**This is the theme only.** Match the colours, type and shapes so the asset is "
            "recognisably the same brand. Do NOT reproduce the source page's layout, section order "
            "or components — this asset has its own structure.",
            "",
        ]
    if guide is not None and guide.mode:
        out += [f"The page is a **{guide.mode}** design. Build in that mode.", ""]

    # Ahead of the tokens, because it is the brief the tokens are used to satisfy: the skeleton says
    # what to build and the palette says what to paint it. A model that reads the colours first has
    # already started composing a page by the time it learns the page's real shape.
    if not theme_only and structure is not None:
        out += structure_markdown(structure)
    # Right after the skeleton: the header and footer it describes are made of these links, and a
    # model that reads the labels without the hrefs fills the hrefs in itself.
    if not theme_only:
        out += links_markdown(links)

    if colors:
        out += ["## Colors", ""]
        described = {
            "primary": "The brand colour. The driver for calls to action.",
            "surface": "The page background.",
            "on-surface": "Body text on that background.",
        }
        # Only the tokens the third tier actually supplied get the provenance note — most of the
        # time this dict is empty and every colour reads exactly as it did before that tier existed.
        via_fallback = (
            {
                token
                for token, val in (
                    ("primary", fallback.primary),
                    ("secondary", fallback.secondary),
                    ("surface", fallback.background),
                    ("on-surface", fallback.on_surface),
                )
                if val and colors.get(token) == val
            }
            if fallback is not None
            else set()
        )
        for token, value in colors.items():
            note = described.get(token, "A secondary brand colour measured on the page.")
            if token in via_fallback:
                note += f" (Context.dev found nothing here; via {fallback.source}.)"
            out.append(f"- **`{token}` ({value})** — {note}")
        out.append("")
        if tokens is not None and tokens.available and tokens.palette:
            out += [
                "Where each colour is actually used on the source page — a colour counted mostly as "
                "a border is not a background:",
                "",
                "| Colour | Mostly used as | background | text | border |",
                "|---|---|---|---|---|",
            ]
            for color in tokens.palette[:12]:
                out.append(
                    f"| `{color.hex}` | {color.role} | {color.background} | {color.text} | {color.border} |"
                )
            out.append("")

    if typography:
        out += ["## Typography", ""]
        for level, style in typography.items():
            bits = ", ".join(f"{k} {_plain(v)}" for k, v in style.items() if k != "fontFamily")
            out.append(f"- **`{level}`** — `{style.get('fontFamily', '?')}`{'; ' + bits if bits else ''}")
        out.append("")
        typography_from_fallback = (
            fallback is not None
            and (fallback.body_font or fallback.heading_font)
            and not (guide is not None and guide.typography)
            and not (tokens is not None and tokens.available and tokens.font_families)
        )
        if typography_from_fallback:
            out.append(
                f"Context.dev's styleguide and the local CSS parse found no type scale for this "
                f"page; the family names above are via {fallback.source} — no sizes are known at "
                "this tier."
            )
            out.append("")
        webfonts = _webfont_lines(guide, fonts, tokens, _used_weights(typography, components, guide))
        if webfonts:
            out += ["Webfonts to link in the generated `<head>`, verbatim:", "", *webfonts, ""]
        if fonts is not None and fonts.fonts:
            out += ["Share of the page's words set in each face:", ""]
            for face in fonts.fonts[:6]:
                share = f"{face.percent_words:.0f}%" if face.percent_words else "—"
                out.append(f"- `{face.family}` — {share}")
            out.append("")

    if not theme_only and guide is not None and guide.spacing:
        out += ["## Layout", "", "The spacing scale measured between elements on the page:", ""]
        out += [f"- `{k}` — {v}" for k, v in _ordered_spacing(guide.spacing).items()]
        out.append("")

    if not theme_only:
        out += _responsive_lines(tokens)

    if not theme_only:
        shadows = {k: v for k, v in (guide.shadows if guide is not None else {}).items() if v and v != "none"}
        out += ["## Elevation & Depth", ""]
        if shadows:
            out += [f"- `{k}` — `{v}`" for k, v in shadows.items()]
        else:
            out += [
                "The page uses no shadows. It is a flat design — separate surfaces with borders and "
                "background tone, not elevation. Do not add shadows it does not have.",
            ]
        out.append("")

    rounded = _shape_tokens(guide, tokens)
    if rounded:
        out += ["## Shapes", "", "Corner radii the page actually uses:", ""]
        out += [f"- `{k}` — {v}" for k, v in rounded.items()]
        out.append("")

    if not theme_only and components:
        out += ["## Components", ""]
        for name_, style in components.items():
            props = ", ".join(f"{k} `{_plain(v)}`" for k, v in style.items())
            out.append(f"- **`{name_}`** — {props}")
        out.append("")
        if guide is not None:
            for name_, style in guide.components.items():
                css = style.get("css")
                if css:
                    out += [f"`{name_}` as computed on the page:", "", "```css", _plain(css), "```", ""]
        if tokens is not None and tokens.available:
            hovers = [b for b in tokens.buttons if b.hover_background]
            if hovers:
                out += ["Hover states measured in the page's own CSS:", ""]
                # Selectors are collapsed to one line: a grouped selector carries a real newline,
                # and a newline inside backticks ends the code span and breaks the list item.
                out += [f"- `{_plain(b.selector)[:60]}` \u2192 `{b.hover_background}`" for b in hovers[:6]]
                out.append("")

    out += _site_token_lines(tokens)
    out += _logo_lines(tokens, fallback)

    if not theme_only and screenshots:
        out += ["## Reference screenshots", ""]
        for shot in screenshots:
            note = f" — {shot.aspect_note}" if shot.aspect_note else ""
            size = f"{shot.width}×{shot.height}" if shot.width and shot.height else shot.kind
            out.append(f"- **{shot.label or shot.kind}** ({size}){note}: {shot.image_url}")
        out.append("")

    out += ["## Do's and Don'ts", ""]
    out += [
        "- Do use only the colours in the token block. A colour that is not there was not on the page.",
        "- Do reproduce the font stacks with their fallbacks, and link the webfont files listed above.",
        "- Do use the client's real logo from the Logo section — the URL or the inlined copy, whichever "
        "you can reproduce exactly. Never redraw it, never set it as text.",
        "- Don't invent a radius, a shadow or a spacing step that is not in this file — derive it from "
        "one that is, and say so in a comment.",
    ]
    if theme_only:
        out.append(
            "- Don't copy the source page's layout or sections. This file is a theme, not a template."
        )
    else:
        out.append(
            "- Don't treat the screenshots as copy. They show structure and rhythm; the words come "
            "from the generated content, not from the image."
        )
    out.append("")

    return "\n".join(out)


#: Used when the page's CSS declares no width breakpoints — the common phone / tablet / desktop
#: split, stated as a fallback so the document never presents it as measured.
DEFAULT_BREAKPOINTS = (640, 1024, 1280)

RESPONSIVE_HEADING = "## Responsive"


def _responsive_lines(tokens: DesignTokens | None) -> list[str]:
    """The breakpoints the generated page switches layout at. Measured when the CSS has them."""
    measured = list(tokens.breakpoints) if tokens is not None and tokens.available else []
    out = [RESPONSIVE_HEADING, ""]
    if measured:
        widths = ", ".join(f"`{w}px`" for w in measured)
        out.append(
            f"The page's own CSS switches layout at {widths}. Use these as the generated page's "
            "media-query breakpoints, mobile-first (`min-width`)."
        )
    else:
        widths = ", ".join(f"`{w}px`" for w in DEFAULT_BREAKPOINTS)
        out.append(
            f"No width breakpoints were found in the page's CSS. Use {widths} (phone / tablet / "
            "desktop), mobile-first — these are defaults, not measurements."
        )
    out += [
        "",
        "Every band must work from 360px wide upward with no horizontal scroll: grids collapse to "
        "one column on a phone, the nav collapses behind an accessible toggle, and type scales with "
        "`clamp()` between the sizes in the Typography section — never above the largest.",
        "",
    ]
    return out


def _unavailable_md(source_url: str, reason: str) -> str:
    """What gets injected when the page could not be read.

    Deliberately still a document with a stated reason rather than an empty string: a stage that
    receives nothing invents a palette, and a stage that receives this builds in greys and flags
    them, which is recoverable.
    """
    return (
        f"---\nname: {_scalar(_site_name(source_url))}\n---\n\n"
        f"# Design system — NOT AVAILABLE\n\n"
        f"**Source**: {source_url}\n\n"
        f"The page could not be read: {reason}\n\n"
        "No colours, fonts or component styles were extracted. Do not invent a palette. Build in "
        "neutral greys, mark every colour as a placeholder needing the client's real values, and "
        "say so at the top of the output."
    )


def _site_name(url: str) -> str:
    """A readable name for the front matter, from the host. Not the company's legal name — this
    names the design system, and the host is the one identifier that is always present."""
    host = re.sub(r"^https?://", "", url.strip()).split("/")[0]
    host = re.sub(r"^www\.", "", host)
    return host or "Client site"


# ----------------------------------------------------------------------------------------------
# Capture
# ----------------------------------------------------------------------------------------------


async def capture_page_design(
    url: str, *, with_screenshots: bool = True, subject_context: str = ""
) -> PageDesign:
    """Read one page every way this backend can, and return its DESIGN.md.

    Costs, when Context.dev is configured: 10 credits (styleguide) + 5 (fonts) + 2 (screenshots) =
    **17 credits per page**. Called once per run and cached on the run by the router, not per stage.

    Every reading is issued concurrently and every one is allowed to fail on its own. A styleguide
    without fonts is still a design system; fonts without a styleguide still name the typefaces; the
    local CSS parse alone still produces something. Only when nothing answers is this unavailable.

    After those two, `_brand_gaps` checks for named holes (colours, a type scale, a logo) and only
    then — and only for those holes — tries Firecrawl and Brandfetch (`FIRECRAWL_API_KEY` /
    `BRANDFETCH_API_KEY`; either or both may be unset, in which case this tier is a no-op and costs
    nothing extra). Their cost is outside the 17-credit figure above and is not fixed, since they
    run only on a gap.

    Firecrawl is also asked for the page's own real photographs (`extract_reference_images`),
    unconditionally rather than on a gap — there is no free local reader that could ever fill this
    field, and it is what lets `image_briefs.build_image_briefs` ground a generated hero/proof/CTA
    image in the client's actual subject matter instead of inventing one. `subject_context` (a short
    description of the service this run is for, from the stage's own intake) is passed straight
    through to the same function for the same reason.
    """
    use_context_dev = context_dev.is_configured()
    use_firecrawl_images = firecrawl_client.is_configured()

    async def styleguide():
        return await context_dev.extract_styleguide(url) if use_context_dev else None

    async def fonts():
        return await context_dev.extract_fonts(url) if use_context_dev else None

    async def shots():
        if not (use_context_dev and with_screenshots):
            return ()
        return await asyncio.gather(
            context_dev.screenshot(url, full_page=True, label="full page"),
            context_dev.screenshot(url, viewport=context_dev.HERO_VIEWPORT, label="above the fold"),
            return_exceptions=True,
        )

    async def reference_images():
        if not use_firecrawl_images:
            return None
        return await firecrawl_client.extract_reference_images(url)

    guide_result, fonts_result, shots_result, tokens_result, reference_images_result = await asyncio.gather(
        styleguide(), fonts(), shots(), extract_design_tokens(url), reference_images(), return_exceptions=True
    )

    notes: list[str] = []
    sources: list[str] = []

    def _ok(result, label: str):
        if isinstance(result, BaseException):
            logger.info("Page design: %s failed for %r: %s", label, url, result)
            notes.append(f"{label} could not be read: {result}")
            return None
        if result is not None:
            sources.append(label)
        return result

    guide = _ok(guide_result, "styleguide")
    fonts_data = _ok(fonts_result, "fonts")
    tokens = _ok(tokens_result, "css parse")

    # The free read was refused (a bot wall that serves browsers but not this server). Everything the
    # page itself supplies — its logo, links, images, client logos and section order — comes from
    # `tokens.html`, so without a page there is none of it, and the brand fallback below used to
    # fill the logo from whatever its own reader was served: on a deployed run, the block page's
    # robot illustration. Read the rendered page instead (1 credit, only in this case).
    if (tokens is None or not tokens.available) and use_context_dev:
        rendered_tokens, rendered_note = await _rendered_tokens(url)
        if rendered_tokens is not None:
            tokens = rendered_tokens
            sources.append("css parse (rendered via Context.dev)")
        if rendered_note:
            notes.append(rendered_note)

    # Not read through `_ok`: `reference_images()` returns `None` (not a result) whenever Firecrawl
    # is unconfigured, and a `ReferenceImages` with no URLs is a real answer ("this page has no
    # usable photos") rather than a failure — neither should be logged as one.
    reference_images: firecrawl_client.ReferenceImages | None = None
    if isinstance(reference_images_result, BaseException):
        logger.info("Page design: reference photos failed for %r: %s", url, reference_images_result)
        notes.append(f"Reference photos could not be read: {reference_images_result}")
    elif reference_images_result is not None and reference_images_result.available:
        reference_images = reference_images_result
        sources.append("reference photos (firecrawl)")

    # Free, and from the HTML the CSS parse has already fetched — `DesignTokens.html` exists for
    # exactly this, so reading the page's skeleton costs no request and no Context.dev credit.
    structure: PageStructure | None = None
    links: LinkManifest | None = None
    if tokens is not None and tokens.html:
        structure = extract_page_structure(tokens.html, tokens.source_url or url)
        if structure.available:
            sources.append("page structure")
        else:
            notes.append(f"The page structure could not be read: {structure.reason}")
        links = extract_link_manifest(tokens.html, tokens.source_url or url)
        if links.available:
            sources.append("links")
        else:
            notes.append(f"The page's links could not be read: {links.reason}")

    if guide is not None and not guide.available:
        notes.append("The styleguide came back empty — the page may render nothing to a crawler.")
        guide = None
    if tokens is not None and not tokens.available:
        notes.append(f"The CSS parse found nothing: {tokens.reason}")

    screenshots: tuple[context_dev.Screenshot, ...] = ()
    if isinstance(shots_result, BaseException):
        notes.append(f"Screenshots could not be captured: {shots_result}")
    else:
        captured = []
        for shot in shots_result or ():
            if isinstance(shot, BaseException):
                notes.append(f"A screenshot failed: {shot}")
                continue
            captured.append(shot)
            if not shot.model_safe:
                notes.append(
                    f"The {shot.label} shot is {shot.width}×{shot.height}, past what the model API "
                    "accepts, so it is kept for review but not sent to the model."
                )
        screenshots = tuple(captured)
        if screenshots:
            sources.append("screenshots")

    # Third tier — Firecrawl, then Brandfetch, for whatever named gap Context.dev and the local
    # parse left open. Skipped entirely when there is no gap, and skipped again when neither is
    # configured, so a run with no Firecrawl/Brandfetch keys behaves exactly as it did before this
    # tier existed.
    gaps = _brand_gaps(guide, fonts_data, tokens)
    fallback: BrandFallback | None = None
    if gaps and (firecrawl_client.is_configured() or brandfetch_client.is_configured()):
        fallback, fallback_notes = await _capture_brand_fallback(url, gaps)
        notes.extend(fallback_notes)
        if fallback is not None:
            sources.append(f"brand fallback ({fallback.source})")

    readable = guide is not None or (tokens is not None and tokens.available) or fallback is not None
    if not readable:
        reason = (
            "; ".join(notes)
            if notes
            else "no reader returned a design system for this page."
        )
        logger.warning("Page design unavailable url=%r: %s", url, reason)
        unavailable = _unavailable_md(url, reason)
        # The structure still travels when one was read. A page can refuse a styleguide and hand
        # over a perfectly readable DOM (a JS-applied theme is the common case), and "no palette"
        # is not a reason to also withhold the section order — the two failures are independent, and
        # a page built in placeholder greys in the right sequence is far closer to correct than one
        # built in the right colours in an invented shape.
        document = unavailable
        if structure is not None and structure.available:
            document = unavailable + "\n\n" + "\n".join(structure_markdown(structure))
        # The links travel for the same reason: a page that refuses a palette still links to its
        # own contact page, and a placeholder-grey page with real links is still deployable.
        link_lines = links_markdown(links)
        if link_lines:
            document += "\n\n" + "\n".join(link_lines)
        return PageDesign(
            source_url=url,
            available=False,
            reason=reason,
            design_md=document,
            theme_brief=unavailable,
            screenshots=screenshots,
            structure=structure,
            links=links,
            sources=tuple(sources),
            notes=notes,
            image_briefs=(),
        )

    design_md = build_design_md(
        url,
        guide,
        fonts_data,
        tokens,
        screenshots=screenshots,
        structure=structure,
        links=links,
        fallback=fallback,
    )
    # Specs only — role, ratio, prompt. What actually reaches the model is real hosted URLs,
    # generated server-side once real ones exist (`image_briefs.generate_all`, wired from
    # `resolve_page_design` in the pipeline router). Baking an instruction to "go generate this
    # yourself" into the document itself was the bug: the model that reads DESIGN.md has no way to
    # call an image generator, so it could only fabricate a URL. See `image_briefs.py`'s docstring.
    image_briefs = build_image_briefs(
        design_md,
        subject_context=subject_context,
        reference_image_urls=reference_images.all_urls if reference_images is not None else (),
    )
    theme_brief = build_design_md(url, guide, fonts_data, tokens, theme_only=True, fallback=fallback)

    logger.info(
        "Captured page design url=%r sources=%s shots=%s model_safe_shots=%s bands=%s chars=%s",
        url,
        ",".join(sources) or "none",
        len(screenshots),
        sum(1 for s in screenshots if s.model_safe),
        structure.band_count if structure is not None and structure.available else 0,
        len(design_md),
    )
    return PageDesign(
        source_url=url,
        available=True,
        design_md=design_md,
        theme_brief=theme_brief,
        screenshots=screenshots,
        structure=structure,
        links=links,
        sources=tuple(sources),
        notes=notes,
        image_briefs=image_briefs,
    )
