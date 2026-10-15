"""Pull a page's real design system out of its CSS, so generated HTML looks like the client built it.

Every HTML-producing prompt in this pipeline already forbids inventing a palette. The Pillar Page
prompt is the strictest — it asks for the primary background, the brand colours, the text colours,
the border colours, exact button padding and radius, and then states as Rule 1: "No design
invention. Every colour, button, layout, and visual element must trace back" to the reference.

It was unfollowable. `reference_design_source` reaches the model through `scraper.py`, which
extracts *readable text* — it strips every tag, stylesheet and style attribute by design, because
its job is feeding copy to a CRO audit. So the model was told to trace every colour back to a
reference that contained no colours, and did the only thing it could: invent a plausible palette
and present it as extracted. That failure is invisible in the output — a clean, professional page
in the wrong brand's colours looks exactly like a correct one.

This module closes that gap with measurement rather than instruction. It fetches the page, fetches
the stylesheets the page links, and reads the values out:

  * **CSS custom properties first.** Any modern theme declares its palette once as `--brand-primary`
    and friends. That block *is* the design system, stated by the people who built it, and it beats
    any amount of frequency analysis over the rules that consume it.
  * **Then usage.** Colours are counted per role — what appears as a background, as text, as a
    border — because the same hex means different things in different properties, and a palette
    that does not say where each colour goes cannot be applied.
  * **Then components.** Button-ish selectors are read whole, so radius, padding and weight travel
    together with the colours instead of being reassembled by guesswork.

What it will not do is fill gaps. A page it cannot fetch yields `available=False` and a stated
reason, and the prompts fall back to asking the operator for hex codes. An empty result is honest
and recoverable; a fabricated one is neither.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from urllib.parse import urljoin


from app.services.scraper import ScrapeError, _fetch_html, fetch_asset, normalize_url

logger = logging.getLogger(__name__)

# Stylesheets are fetched in parallel but bounded. A theme-heavy WordPress page links a dozen of
# them and the tail is almost always plugin CSS that says nothing about the brand, so the budget is
# spent on the first few — which is where the theme's own sheet sits.
_MAX_STYLESHEETS = 8
_MAX_CSS_BYTES = 1_500_000

_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.I | re.S)
_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*["']([^"']+)["']""", re.I)
_LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
_HREF = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.I)
_REL_STYLESHEET = re.compile(r"""\brel\s*=\s*["']?[^"'>]*stylesheet""", re.I)

_CUSTOM_PROP = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+)")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL = re.compile(r"([\w-]+)\s*:\s*([^;]+)")
# Stripped before anything else parses. A comment sitting above a rule otherwise gets swept into
# the next selector — real output from wordpress.org was a "button" whose selector was
# "/**\n * Button\n */\n[class*=wp-block]", which is neither a selector nor useful.
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_VAR_REF = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)")

_HEX = re.compile(r"#([0-9a-fA-F]{3,8})\b")
_RGB = re.compile(r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.%]+))?\s*\)", re.I)
_HSL = re.compile(r"hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%(?:[\s,/]+([\d.%]+))?\s*\)", re.I)

# Properties whose colour means "surface", "ink" and "edge" respectively. Split because a palette
# that lists six hexes without saying which is the page background is not applicable.
_BACKGROUND_PROPS = {"background", "background-color", "background-image"}
_TEXT_PROPS = {"color", "-webkit-text-fill-color"}
_BORDER_PROPS = {
    "border", "border-color", "border-top", "border-right", "border-bottom", "border-left",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "outline", "outline-color",
}

_BUTTON_SELECTOR = re.compile(
    r"(^|[\s,>])(\.?[\w-]*\b(?:btn|button|cta)\b[\w-]*|button|input\[type=[\"']?(?:submit|button))",
    re.I,
)
# A hover/focus/active rule describes a *state*, not the resting style. Kept, but separated, so the
# hover colour never gets reported as the button's own background.
_STATE_SELECTOR = re.compile(r":(hover|focus|active|visited|focus-visible)")

# Near-black and near-white dominate every page ever built and say nothing about a brand. They are
# still reported (a page's real ink and paper matter) but they are excluded when picking the
# *accent*, which is the colour anyone would call "the brand colour".
_NEUTRAL_SATURATION = 0.12
_NEUTRAL_LIGHT = 0.93
_NEUTRAL_DARK = 0.10

_NAMED_COLORS = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0",
    "navy": "#000080", "teal": "#008080", "orange": "#ffa500", "purple": "#800080",
    "yellow": "#ffff00", "maroon": "#800000", "olive": "#808000", "lime": "#00ff00",
    "aqua": "#00ffff", "fuchsia": "#ff00ff",
}

# Values that carry no design information — they defer to something else or to nothing.
_NON_COLORS = {"inherit", "initial", "unset", "revert", "transparent", "currentcolor", "none", "auto"}


class DesignTokenError(Exception):
    """The page's design could not be read. Carries a sentence written for the operator."""


# --------------------------------------------------------------------------------------
# Colour normalisation
# --------------------------------------------------------------------------------------


#: Icon fonts: they set glyphs, not words, and are never a page's typeface. A deployed pillar page
#: set its body text in "font awesome 5 pro", which renders as boxes on any machine that has it.
_ICON_FONT = re.compile(
    r"font ?awesome|fontawesome|\bfa[- ]?(?:solid|brands|regular|light)\b|eicons|elementor-icons|dashicons|icomoon"
    r"|material[- ]?(?:icons|symbols)|glyphicons|fontello|themify|ionicons|caseicon|feather|bootstrap-icons|line ?awesome"
    r"|simple-line-icons|flaticon|\bicons?\b",
    re.I,
)


def is_icon_font(family: object) -> bool:
    """Whether a font-family stack's first family is an icon font."""
    first = str(family or "").split(",")[0].strip().strip("'\"")
    return bool(first) and bool(_ICON_FONT.search(first))


def _clamp(value: float, low: float = 0.0, high: float = 255.0) -> float:
    return max(low, min(high, value))


def _rgb_to_hex(red: float, green: float, blue: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(int(round(_clamp(red))), int(round(_clamp(green))), int(round(_clamp(blue))))


def _hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    saturation /= 100.0
    lightness /= 100.0
    chroma = (1 - abs(2 * lightness - 1)) * saturation
    hue_prime = (hue % 360) / 60.0
    second = chroma * (1 - abs(hue_prime % 2 - 1))
    table = [(chroma, second, 0), (second, chroma, 0), (0, chroma, second),
             (0, second, chroma), (second, 0, chroma), (chroma, 0, second)]
    red, green, blue = table[int(hue_prime) % 6]
    match = lightness - chroma / 2
    return _rgb_to_hex((red + match) * 255, (green + match) * 255, (blue + match) * 255)


def normalize_color(value: str) -> str | None:
    """One CSS colour value as `#rrggbb`, or None when it is not a usable colour.

    Fully transparent colours return None: `rgba(0,0,0,0)` is a way of writing "no colour", and
    reporting it as black would put a colour in the palette that nobody can see on the page.
    """
    text = value.strip().lower()
    if not text or text in _NON_COLORS:
        return None

    named = _NAMED_COLORS.get(text)
    if named:
        return named

    hex_match = _HEX.search(text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits[:3])
        elif len(digits) in (6, 8):
            digits = digits[:6]
        else:
            return None
        return f"#{digits}"

    rgb_match = _RGB.search(text)
    if rgb_match:
        alpha = rgb_match.group(4)
        if alpha is not None and _alpha_value(alpha) == 0:
            return None
        return _rgb_to_hex(float(rgb_match.group(1)), float(rgb_match.group(2)), float(rgb_match.group(3)))

    hsl_match = _HSL.search(text)
    if hsl_match:
        alpha = hsl_match.group(4)
        if alpha is not None and _alpha_value(alpha) == 0:
            return None
        return _hsl_to_hex(float(hsl_match.group(1)), float(hsl_match.group(2)), float(hsl_match.group(3)))

    return None


def _alpha_value(raw: str) -> float:
    raw = raw.strip()
    if raw.endswith("%"):
        try:
            return float(raw[:-1]) / 100.0
        except ValueError:
            return 1.0
    try:
        return float(raw)
    except ValueError:
        return 1.0


def _hsl_of(hex_color: str) -> tuple[float, float, float]:
    red, green, blue = (int(hex_color[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    high, low = max(red, green, blue), min(red, green, blue)
    lightness = (high + low) / 2
    if high == low:
        return 0.0, 0.0, lightness
    delta = high - low
    saturation = delta / (2 - high - low) if lightness > 0.5 else delta / (high + low)
    if high == red:
        hue = ((green - blue) / delta) % 6
    elif high == green:
        hue = (blue - red) / delta + 2
    else:
        hue = (red - green) / delta + 4
    return hue * 60, saturation, lightness


def is_neutral(hex_color: str) -> bool:
    """Whether this is paper, ink or grey rather than a brand colour."""
    _hue, saturation, lightness = _hsl_of(hex_color)
    return saturation < _NEUTRAL_SATURATION or lightness > _NEUTRAL_LIGHT or lightness < _NEUTRAL_DARK


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance, for the contrast check below."""

    def channel(value: float) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two hexes, 1.0 (identical) to 21.0 (black on white).

    Needed because "the most frequently used text colour" is not the body text. `color: #ffffff`
    is everywhere on a light page — inside buttons, dark hero bands, footers — and picking by
    frequency alone produced white body text on a white background for a real site. A colour that
    cannot be read against the page background was never the body colour, however often it appears.
    """
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# Below this, text is unreadable on that background. Deliberately looser than WCAG AA's 4.5:1 —
# this is picking the likeliest body colour out of real data, not auditing the page's compliance,
# and a real site whose body text sits at 4:1 should still have it detected.
_MIN_TEXT_CONTRAST = 3.0


# --------------------------------------------------------------------------------------
# Gathering the CSS
# --------------------------------------------------------------------------------------


@dataclass
class _Sources:
    """Every stylesheet's text, plus the inline `style="..."` values.

    Inline attributes are kept apart because they outrank everything: they are what a page author
    wrote directly onto an element, they cannot be overridden by a later sheet, and on a
    page-builder site (Elementor, Divi) they are often where the actual section colours live while
    the linked sheets hold only the framework's defaults.
    """

    css: list[str] = field(default_factory=list)
    inline_styles: list[str] = field(default_factory=list)
    stylesheet_urls: list[str] = field(default_factory=list)
    font_links: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Kept so the logo can be located from the same fetch rather than a second one.
    html: str = ""


def _stylesheet_hrefs(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """(stylesheet URLs, webfont provider URLs), resolved against the page."""
    sheets: list[str] = []
    fonts: list[str] = []
    for tag in _LINK_TAG.findall(html):
        href_match = _HREF.search(tag)
        if not href_match:
            continue
        href = urljoin(base_url, href_match.group(1).strip())
        if "fonts.googleapis.com" in href or "use.typekit.net" in href or "fonts.bunny.net" in href:
            # Kept separately and reported verbatim: the generated HTML has to reproduce this exact
            # link to get the same typeface, and a font name alone will not do it.
            fonts.append(href)
            continue
        if _REL_STYLESHEET.search(tag):
            sheets.append(href)
    # Dedupe, order preserved — the first sheets are the theme's own, the tail is plugins.
    return list(dict.fromkeys(sheets)), list(dict.fromkeys(fonts))


async def _fetch_stylesheets(urls: list[str], base_url: str) -> tuple[list[str], list[str]]:
    """Fetch up to `_MAX_STYLESHEETS` sheets concurrently. Returns (texts, notes)."""
    notes: list[str] = []
    if not urls:
        return [], notes

    wanted = urls[:_MAX_STYLESHEETS]
    if len(urls) > _MAX_STYLESHEETS:
        notes.append(f"Read the first {_MAX_STYLESHEETS} of {len(urls)} stylesheets.")

    # Same request policy as the page itself (`scraper.fetch_asset`): agents, bot-wall retry,
    # challenge headers, timeout, redirect cap and a public-host check on every hop.
    async def one(url: str) -> str:
        try:
            sheet = await fetch_asset(url, accept="text/css,*/*;q=0.1", referer=base_url, max_bytes=_MAX_CSS_BYTES)
        except Exception as exc:  # noqa: BLE001 — one missing sheet must not lose the others
            logger.info("Could not read stylesheet %s (%s)", url, exc)
            return ""
        return sheet.content.decode(sheet.encoding or "utf-8", errors="replace")

    texts = await asyncio.gather(*(one(url) for url in wanted))

    read = [t for t in texts if t]
    if len(read) < len(wanted):
        notes.append(f"{len(wanted) - len(read)} stylesheet(s) could not be read.")
    return read, notes


async def collect_css(url: str) -> tuple[str, _Sources]:
    """Fetch the page and everything styling it. Returns (final URL, sources).

    Raises `DesignTokenError` when the page itself cannot be read — a bot wall, a login, a dead
    host. That is a real outcome with a real remedy (paste the brand values), not something to
    paper over.
    """
    try:
        final_url, html = await _fetch_html(normalize_url(url))
    except ScrapeError as exc:
        # The scraper's messages are already written for an operator ("blocked an automated read
        # … paste the copy instead"), so they are passed through rather than restated.
        raise DesignTokenError(str(exc)) from exc

    sources = _Sources()
    sources.css.extend(_CSS_COMMENT.sub(" ", block) for block in _STYLE_BLOCK.findall(html))
    sources.inline_styles.extend(_STYLE_ATTR.findall(html))

    sheet_urls, font_links = _stylesheet_hrefs(html, final_url)
    sources.stylesheet_urls = sheet_urls[:_MAX_STYLESHEETS]
    sources.font_links = font_links

    sheets, notes = await _fetch_stylesheets(sheet_urls, final_url)
    sources.css.extend(_CSS_COMMENT.sub(" ", sheet) for sheet in sheets)
    sources.notes.extend(notes)

    if not sources.css and not sources.inline_styles:
        raise DesignTokenError(
            f"{final_url} was read, but it carries no stylesheets or inline styles this reader can "
            "see — its design is likely applied by JavaScript after load."
        )

    sources.html = html
    logger.info(
        "Design tokens: %s -> %d CSS block(s), %d inline style attribute(s), %d webfont link(s)",
        final_url,
        len(sources.css),
        len(sources.inline_styles),
        len(sources.font_links),
    )
    return final_url, sources


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


_ROOT_SELECTOR = re.compile(r"(?:^|,)\s*(?:html|body|:root)\b[^,]*$", re.I)


@dataclass
class ColorUse:
    hex: str
    background: int = 0
    text: int = 0
    border: int = 0
    # Times this colour was set as a background on `html`, `body` or `:root` — i.e. as the page's
    # actual surface. Counted separately because frequency alone gets this wrong: a body background
    # is declared once and applies to the whole page, while a brand colour can appear as a
    # background on a dozen buttons and badges and out-vote it. That produced an accent-coloured
    # "page background" on a test fixture, which would have shipped a lead magnet on an orange
    # canvas.
    root_background: int = 0

    @property
    def total(self) -> int:
        return self.background + self.text + self.border

    @property
    def role(self) -> str:
        """Where this colour is mostly used. What turns a list of hexes into a usable palette."""
        counts = {"background": self.background, "text": self.text, "border": self.border}
        return max(counts, key=lambda key: counts[key])


@dataclass
class ButtonStyle:
    selector: str
    background: str | None = None
    color: str | None = None
    border_radius: str | None = None
    padding: str | None = None
    font_weight: str | None = None
    font_size: str | None = None
    border: str | None = None
    hover_background: str | None = None


def _iter_declarations(css: str):
    """(selector, property, value) for every declaration in a stylesheet.

    A deliberately shallow regex parse, not a real CSS parser. It cannot resolve the cascade — and
    it does not need to: the goal is an inventory of the values a page actually uses, and for that,
    frequency across all rules is a better signal than specificity. What it does need is to not
    choke on the minified, at-rule-heavy CSS every real site ships, which this handles by simply
    reading every `{...}` body it finds, `@media` wrappers included.
    """
    for selector, body in _RULE.findall(css):
        selector = selector.strip()
        if selector.startswith("@"):
            continue  # an at-rule's own header, not a selector; its inner rules match separately
        for prop, value in _DECL.findall(body):
            yield selector, prop.strip().lower(), value.strip()


def _collect_colors(sources: _Sources, properties: dict[str, str] | None = None) -> dict[str, ColorUse]:
    colors: dict[str, ColorUse] = {}
    properties = properties or {}

    def record(prop: str, value: str, weight: int = 1, selector: str = "") -> None:
        hex_color = normalize_color(resolve_vars(value, properties))
        if hex_color is None:
            return
        use = colors.setdefault(hex_color, ColorUse(hex_color))
        if prop in _BACKGROUND_PROPS:
            use.background += weight
            if selector and _ROOT_SELECTOR.search(selector):
                use.root_background += weight
        elif prop in _TEXT_PROPS:
            use.text += weight
        elif prop in _BORDER_PROPS:
            use.border += weight

    for css in sources.css:
        for selector, prop, value in _iter_declarations(css):
            record(prop, value, selector=selector)

    # Inline styles are what the page author put on the element itself — they win the cascade and,
    # on page-builder sites, they are where the real section colours live. Weighted accordingly.
    for style in sources.inline_styles:
        for prop, value in _DECL.findall(style):
            record(prop.strip().lower(), value.strip(), weight=3)

    return colors


def _collect_custom_properties(sources: _Sources) -> dict[str, str]:
    """The theme's own declared palette, if it has one.

    Highest-value thing in this module. A `--brand-primary: #e8590c` is the design system stated by
    whoever built the site; everything else here is inference over the rules that consume it.
    Later declarations win, matching how a browser resolves the same property redeclared.
    """
    props: dict[str, str] = {}
    for css in sources.css:
        for name, value in _CUSTOM_PROP.findall(css):
            cleaned = value.strip().rstrip(";").strip()
            # `var()`-valued properties are kept, not skipped. Themes alias heavily
            # (`--heading-font: var(--font-sans)`), and dropping the alias breaks the chain at its
            # first link — which is how a heading font came back as the literal string
            # "var(--wp--custom--heading--typography--font-family)". `resolve_vars` walks it.
            if cleaned:
                props[name.strip()] = cleaned
    return props


def _collect_values(
    sources: _Sources, prop_name: str, limit: int = 12, properties: dict[str, str] | None = None
) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    properties = properties or {}
    for css in sources.css:
        for _selector, prop, value in _iter_declarations(css):
            if prop == prop_name:
                # Whitespace collapsed, quotes left alone. These values go into generated CSS
                # verbatim, and a font stack is quoted per-family — stripping the outer quote off
                # `'EB Garamond', serif` yields `EB Garamond', serif`, which is a parse error.
                cleaned = re.sub(r"\s+", " ", resolve_vars(value, properties)).strip()
                # A value still holding an unresolved `var()` names a property this page never
                # declares. It cannot be reproduced anywhere else, so reporting it would hand the
                # build a token that renders as nothing.
                if cleaned and cleaned not in _NON_COLORS and "var(" not in cleaned:
                    counter[cleaned[:120]] += 1
    return counter.most_common(limit)


def _collect_buttons(
    sources: _Sources, limit: int = 6, properties: dict[str, str] | None = None
) -> list[ButtonStyle]:
    """Button rules read whole, so radius, padding and colour stay together.

    Resting styles and `:hover` states are gathered separately and then joined: a hover background
    reported as the button's own is how a generated page ends up with a permanently-hovered CTA.
    """
    resting: dict[str, ButtonStyle] = {}
    hovers: dict[str, str] = {}
    properties = properties or {}

    for css in sources.css:
        for selector, prop, value in _iter_declarations(css):
            if not _BUTTON_SELECTOR.search(selector):
                continue
            value = resolve_vars(value, properties)
            base = _STATE_SELECTOR.sub("", selector).strip()
            if _STATE_SELECTOR.search(selector):
                if prop in _BACKGROUND_PROPS:
                    color = normalize_color(value)
                    if color:
                        hovers.setdefault(base, color)
                continue

            style = resting.setdefault(base, ButtonStyle(selector=base))
            if prop in _BACKGROUND_PROPS and style.background is None:
                style.background = normalize_color(value)
            elif prop in _TEXT_PROPS and style.color is None:
                style.color = normalize_color(value)
            elif prop == "border-radius" and style.border_radius is None:
                style.border_radius = value[:60]
            elif prop == "padding" and style.padding is None:
                style.padding = value[:60]
            elif prop == "font-weight" and style.font_weight is None:
                style.font_weight = value[:20]
            elif prop == "font-size" and style.font_size is None:
                style.font_size = value[:20]
            elif prop == "border" and style.border is None:
                style.border = value[:60]

    for base, hover in hovers.items():
        if base in resting:
            resting[base].hover_background = hover

    # A rule that named no colour and no shape tells the build nothing.
    useful = [s for s in resting.values() if s.background or s.color or s.border_radius or s.padding]
    useful.sort(key=lambda s: -sum(1 for v in vars(s).values() if v and v != s.selector))
    return useful[:limit]


# --------------------------------------------------------------------------------------
# The token sheet
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# The logo
#
# The Lead Magnet prompt already asks for one: "Include the client's logo treatment (recreate as
# styled text/SVG if no logo file was supplied, matching the extracted brand mark style)." With no
# logo ever supplied, that fallback was the only branch that could ever run — so every generated
# asset carried a *recreated* wordmark, which is a drawing of a logo rather than the logo.
#
# Found rather than guessed, in confidence order, because pages disagree about where a logo lives:
# a header <img> whose markup says "logo" is nearly always right, an inline <svg> in the branding
# block is usually right, and a favicon is a last resort (it is the logo, cropped to 32px and often
# simplified).
#
# `og:image` is deliberately ranked last and usually rejected: it is a 1200x630 social share card,
# not a mark, and dropping one into a header renders a screenshot where a logo should be.
# --------------------------------------------------------------------------------------

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_SVG_BLOCK = re.compile(r"<svg\b[^>]*>.*?</svg>", re.I | re.S)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r"""\b([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_HEADER_BLOCK = re.compile(
    # Only genuine site chrome. The earlier version also matched any `<div class="...logo...">`,
    # which on wordpress.org matched a mid-page "sites using WordPress" strip and confidently
    # returned Rolling Stone's logo as WordPress's own.
    r"<(header|nav)\b[^>]*>.*?</\1>|<div\b[^>]*\b(?:class|id)\s*=\s*[\"'][^\"']*"
    r"(?:site-branding|site-header|site-logo|navbar-brand|header-logo)[^\"']*[\"'][^>]*>.*?</div>",
    re.I | re.S,
)

# A logo reference almost always says so somewhere in its own markup.
_LOGO_HINT = re.compile(r"\blogo\b|\bbrand(?:mark|ing)?\b|\bwordmark\b|site-?title", re.I)
# Things that look like logos and are not. The second group is the expensive one to get wrong:
# a page full of *other companies'* logos — customer strips, press mentions, integration grids,
# payment badges — is extremely common on a marketing page, and each of those images says "logo"
# in its own markup just as loudly as the real one does.
_LOGO_ANTI_HINT = re.compile(
    r"sprite|placeholder|avatar|payment|badge|award|flag|"
    r"partner|client|customer|testimonial|review|press|featured|"
    r"trusted|integration|sponsor|showcase|carousel|slider|as[-\s]seen",
    re.I,
)

# Data URIs are embedded so a generated single-file page needs no external asset. Kept small on
# purpose: base64 costs ~1.33 bytes per byte and lands in the prompt of every HTML stage, so a
# 300KB PNG would spend ~100k tokens restating an image. Above the cap the absolute URL is given
# instead, which is the client's own logo on the client's own domain — a reasonable reference.
_MAX_LOGO_EMBED_BYTES = 24_000
_MAX_LOGO_FETCH_BYTES = 400_000

_IMAGE_MIME_BY_EXT = {
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon",
}


@dataclass
class Logo:
    """The client's mark, as found on their page."""

    url: str
    # How it was identified, so the token sheet can say. A favicon-derived logo is worth flagging:
    # it is real, but it is 32px and often a simplified version of the full mark.
    source: str
    mime_type: str | None = None
    byte_size: int | None = None
    alt_text: str | None = None
    # Declared width/height from the markup, when present. Aspect ratio matters more than exact
    # pixels — a build that guesses it renders the mark stretched.
    width: str | None = None
    height: str | None = None
    # Set only when small enough to embed. `None` means "reference the URL".
    data_uri: str | None = None
    # Inline SVG markup, when the logo was an inline <svg> rather than a file. Best case: it is
    # already self-contained, scalable, and recolourable.
    svg_markup: str | None = None


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _ATTR.finditer(tag):
        value = match.group(2) or match.group(3) or match.group(4) or ""
        out[match.group(1).lower()] = value.strip()
    return out


def _best_srcset_url(srcset: str) -> str | None:
    """The largest candidate in a `srcset`. A 1x logo next to a 3x one is the wrong pick."""
    best: tuple[float, str] | None = None
    for part in srcset.split(","):
        chunk = part.strip().split()
        if not chunk:
            continue
        url = chunk[0]
        weight = 1.0
        if len(chunk) > 1:
            descriptor = chunk[1].lower()
            try:
                weight = float(descriptor.rstrip("wx"))
            except ValueError:
                weight = 1.0
        if best is None or weight > best[0]:
            best = (weight, url)
    return best[1] if best else None


_CONTAINER_OPEN = re.compile(
    r"""<(section|div|ul|aside|footer)\b[^>]*\b(?:class|id)\s*=\s*["']([^"']*)["'][^>]*>""", re.I
)


def _excluded_spans(html: str) -> list[tuple[int, int]]:
    """Character ranges holding *other companies'* logos.

    The anti-hint list only ever inspected an image's own attributes, which is not where the signal
    usually is: a customer-logo strip is marked on the container (`<section class="customer-logos">`)
    while each image inside is labelled only with the other company's name. So
    `<img alt="Rolling Stone logo">` passed every check, and if the real header logo happened to
    404, `find_logo` would have returned it — a competitor's mark on the client's lead magnet.

    The span runs from the container's opening tag to a bounded distance after it rather than to a
    matched closing tag. Nesting makes correct tag matching impossible with regex, and over-reaching
    slightly is the safe direction: the cost is skipping a logo, and the cost of under-reaching is
    shipping the wrong brand.
    """
    spans: list[tuple[int, int]] = []
    for match in _CONTAINER_OPEN.finditer(html):
        if _LOGO_ANTI_HINT.search(match.group(2)):
            spans.append((match.start(), match.start() + 8000))
    return spans


def _in_excluded_span(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _image_candidates(html: str, base_url: str) -> list[Logo]:
    """Every plausible logo in the page, best first.

    Ranked on two signals, and the second one does most of the work in practice. `score` is how
    logo-like the markup claims to be; ties break on **document position**, because a site's own
    mark sits at the very top of the page and every other company's logo — customers, press,
    integrations — appears further down. Position is the one thing those sections cannot fake.
    """
    # Only the first block of site chrome. Concatenating every match pulled mid-page sections in.
    first_header = _HEADER_BLOCK.search(html)
    header_html = first_header.group(0) if first_header else ""
    excluded = _excluded_spans(html)
    candidates: list[tuple[int, int, Logo]] = []

    def consider(tag: str, in_header: bool, position: int) -> None:
        attrs = _attrs(tag)
        haystack = " ".join(
            attrs.get(key, "") for key in ("class", "id", "alt", "src", "title", "data-src")
        )
        if _LOGO_ANTI_HINT.search(haystack):
            return
        # A logo inside a customer/press/partner block belongs to somebody else, however
        # innocent its own markup looks.
        if not in_header and _in_excluded_span(position, excluded):
            return

        src = attrs.get("src") or attrs.get("data-src") or ""
        if attrs.get("srcset"):
            src = _best_srcset_url(attrs["srcset"]) or src
        if not src or src.startswith("data:"):
            # An inline data URI is already the asset; keep it as-is rather than resolving it.
            if not src.startswith("data:"):
                return

        named = bool(_LOGO_HINT.search(haystack))
        # Header + named is the strong case. Named alone is next (footer logos are still logos).
        # Header alone is weak but better than nothing on a site whose markup says nothing.
        score = 0 if (in_header and named) else 1 if named else 3 if in_header else 9
        if score == 9:
            return

        candidates.append(
            (
                score,
                position,
                Logo(
                    url=src if src.startswith("data:") else urljoin(base_url, src),
                    source="header logo" if in_header and named else "logo image" if named else "header image",
                    alt_text=attrs.get("alt") or None,
                    width=attrs.get("width") or None,
                    height=attrs.get("height") or None,
                    data_uri=src if src.startswith("data:") else None,
                ),
            )
        )

    for match in _IMG_TAG.finditer(header_html):
        consider(match.group(0), in_header=True, position=match.start())
    for match in _IMG_TAG.finditer(html):
        if match.group(0) not in header_html:
            consider(match.group(0), in_header=False, position=match.start())

    # Inline SVG in the branding block: already self-contained and recolourable, so it outranks a
    # raster file when it is genuinely the mark.
    for match in _SVG_BLOCK.finditer(header_html):
        markup = match.group(0)
        if len(markup) > _MAX_LOGO_EMBED_BYTES or not _LOGO_HINT.search(markup[:400]):
            continue
        candidates.append(
            (0, match.start(),
             Logo(url=base_url, source="inline SVG in the site header", mime_type="image/svg+xml",
                  byte_size=len(markup), svg_markup=markup))
        )

    candidates.sort(key=lambda row: (row[0], row[1]))
    return [logo for _score, _position, logo in candidates]


def _icon_candidates(html: str, base_url: str) -> list[Logo]:
    """Favicons and touch icons, largest declared size first.

    A last resort, and labelled as one in the sheet: a touch icon is the real mark but rendered for
    a 180px square, so it is often cropped or simplified relative to the full wordmark.
    """
    found: list[tuple[int, Logo]] = []
    for tag in _LINK_TAG.findall(html):
        attrs = _attrs(tag)
        rel = attrs.get("rel", "").lower()
        if "icon" not in rel:
            continue
        href = attrs.get("href")
        if not href:
            continue
        sizes = attrs.get("sizes", "")
        try:
            largest = max(int(part.split("x")[0]) for part in sizes.lower().split() if "x" in part)
        except ValueError:
            largest = 180 if "apple-touch" in rel else 32
        found.append(
            (
                -largest,
                Logo(url=urljoin(base_url, href), source=f"site icon ({sizes or 'unspecified size'})"),
            )
        )
    found.sort(key=lambda pair: pair[0])
    return [logo for _size, logo in found]


async def _measure_logo(logo: Logo, base_url: str) -> Logo:
    """Fetch the logo to confirm it exists, learn its type and size, and embed it when small.

    A logo URL that 404s is worse than no logo: the generated page renders a broken image where the
    brand should be, and nothing in the build would have noticed.
    """
    if logo.svg_markup or (logo.data_uri and logo.url.startswith("data:")):
        return logo

    try:
        fetched = await fetch_asset(
            logo.url,
            accept="image/avif,image/webp,image/svg+xml,image/*,*/*;q=0.8",
            referer=base_url,
            max_bytes=_MAX_LOGO_FETCH_BYTES,
        )
        payload = fetched.content
    except Exception as exc:  # noqa: BLE001 — an unreachable logo is simply not a logo
        logger.info("Could not fetch logo %s (%s)", logo.url, exc)
        return replace(logo, byte_size=None, mime_type=None, url="")

    mime = fetched.content_type
    if not mime.startswith("image/"):
        suffix = "." + logo.url.rsplit(".", 1)[-1].lower().split("?")[0] if "." in logo.url else ""
        mime = _IMAGE_MIME_BY_EXT.get(suffix, "")
    if not mime.startswith("image/"):
        logger.info("Logo candidate %s is %r, not an image", logo.url, mime)
        return replace(logo, url="")

    data_uri = None
    if len(payload) <= _MAX_LOGO_EMBED_BYTES:
        data_uri = f"data:{mime};base64,{base64.b64encode(payload).decode()}"

    return replace(logo, mime_type=mime, byte_size=len(payload), data_uri=data_uri)


async def find_logo(html: str, base_url: str) -> Logo | None:
    """The client's logo, or None when the page carries nothing usable.

    Candidates are tried in confidence order and each is *verified* by fetching it, so the first
    one returned is one that actually exists and actually is an image.
    """
    candidates = _image_candidates(html, base_url) + _icon_candidates(html, base_url)
    for candidate in candidates[:6]:
        measured = await _measure_logo(candidate, base_url)
        if measured.url or measured.svg_markup:
            logger.info(
                "Logo found via %s: %s (%s, %s bytes, embedded=%s)",
                measured.source,
                measured.url[:90] or "inline",
                measured.mime_type,
                measured.byte_size,
                bool(measured.data_uri),
            )
            return measured
    return None


@dataclass
class DesignTokens:
    """One page's design system, as measured.

    `available=False` is a first-class outcome, not an error state to be smoothed over. Every field
    below is either read from the page or absent — nothing here is inferred to fill a gap, because
    the whole point is that a generated page in the wrong brand's colours is indistinguishable from
    a correct one until someone who knows the brand looks at it.
    """

    source_url: str
    available: bool
    reason: str | None = None
    palette: list[ColorUse] = field(default_factory=list)
    custom_properties: dict[str, str] = field(default_factory=dict)
    font_families: list[tuple[str, int]] = field(default_factory=list)
    font_links: list[str] = field(default_factory=list)
    radii: list[tuple[str, int]] = field(default_factory=list)
    buttons: list[ButtonStyle] = field(default_factory=list)
    stylesheet_urls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    logo: Logo | None = None
    #: The `@media` width breakpoints the page's own CSS uses, most-used first then sorted
    #: ascending, in px. What a generated page's responsive rules should switch at, rather than a
    #: framework's defaults. Empty when the CSS declares none.
    breakpoints: list[int] = field(default_factory=list)
    #: The page's HTML, as fetched. Not a token — a byproduct of the fetch, kept so
    #: `page_structure.py` can read the DOM without a second request for the same document. Excluded
    #: from `repr` because it is a whole web page and this dataclass is logged.
    html: str = field(default="", repr=False)

    @property
    def page_background(self) -> str | None:
        """The page's actual surface colour.

        Prefers what `html`/`body`/`:root` declares, and only falls back to the most-used
        background. See `ColorUse.root_background` for why the fallback alone is not enough.
        """
        roots = [c for c in self.palette if c.root_background]
        if roots:
            return max(roots, key=lambda c: c.root_background).hex
        backgrounds = [c for c in self.palette if c.background]
        return max(backgrounds, key=lambda c: c.background).hex if backgrounds else None

    @property
    def body_text(self) -> str | None:
        """The most-used text colour that is actually readable on the page background.

        The contrast filter is load-bearing, not a nicety. `color: #ffffff` is everywhere on a
        light page — inside buttons, dark hero bands, the footer — so picking purely by frequency
        returned white body text on a white background for a real site (smashingmagazine.com).
        A colour that cannot be read against the background was never the body colour.
        """
        texts = [c for c in self.palette if c.text]
        if not texts:
            return None

        background = self.page_background
        if background:
            readable = [c for c in texts if contrast_ratio(c.hex, background) >= _MIN_TEXT_CONTRAST]
            if readable:
                return max(readable, key=lambda c: c.text).hex
        return max(texts, key=lambda c: c.text).hex

    @property
    def accent(self) -> str | None:
        """The colour a person would call "the brand colour".

        Buttons first — a CTA background is the single most deliberate colour decision on a page.
        Otherwise the most-used non-neutral, since paper and ink dominate every page's counts and
        would otherwise always win.
        """
        for button in self.buttons:
            if button.background and not is_neutral(button.background):
                return button.background
        branded = [c for c in self.palette if not is_neutral(c.hex)]
        return max(branded, key=lambda c: c.total).hex if branded else None

    @property
    def heading_font(self) -> str | None:
        return self.font_families[0][0] if self.font_families else None


def resolve_vars(value: str, properties: dict[str, str], depth: int = 4) -> str:
    """Substitute `var(--x)` with what the page declares `--x` to be.

    Without this, a token sheet hands the build `border-radius: var(--wp--custom--button--radius)`
    — a name that means nothing outside the site it came from, so the generated page renders with
    no radius at all. A `var()` with a fallback (`var(--x, 4px)`) resolves to the fallback when the
    property is undeclared, exactly as a browser would.

    Bounded, because custom properties can reference each other and a malformed sheet can make that
    a cycle.
    """
    for _ in range(depth):
        if "var(" not in value:
            break

        def swap(match: re.Match[str]) -> str:
            name, fallback = match.group(1), match.group(2)
            replacement = properties.get(name)
            if replacement is not None:
                return replacement.strip()
            return fallback.strip() if fallback else match.group(0)

        resolved = _VAR_REF.sub(swap, value)
        if resolved == value:
            break  # nothing left that can be resolved
        value = resolved
    return value.strip()


# Custom-property names worth carrying into a build, when their value is usable.
_TOKEN_NAME_HINTS = ("font", "radius", "space", "spacing", "gap", "shadow", "color", "colour", "size")


def _is_useful_token(name: str, value: str) -> bool:
    """Whether a declared custom property can actually be reproduced in generated CSS.

    A theme declares hundreds of these; most are internal plumbing. Two conditions, and the second
    matters more than it looks: the *name* has to suggest something a build uses, and the *value*
    has to be a value. Name alone let `--box-shadow: hover` through — a real row from a real site,
    where the name looks like a design token and the value is a fragment of one. Handing that to a
    build produces CSS that silently does nothing.
    """
    if "var(" in value:
        return False  # an alias this page never resolves — meaningless outside it
    if not any(hint in name for hint in _TOKEN_NAME_HINTS) and not normalize_color(value):
        return False
    # A usable value is a colour, carries a number (lengths, weights, ratios), lists alternatives
    # (font stacks), or is a quoted string (font names).
    return bool(
        normalize_color(value)
        or any(char.isdigit() for char in value)
        or "," in value
        or value.startswith(("'", '"'))
    )


_MEDIA_WIDTH = re.compile(
    r"\(\s*(?:min|max)-width\s*:\s*(\d+(?:\.\d+)?)\s*(px|em|rem)\s*\)", re.IGNORECASE
)
_MEDIA_RULE = re.compile(r"@media([^{]*)\{")
_MAX_BREAKPOINTS = 4
# Below a phone or above a wide desktop, a "breakpoint" is a print or edge-case rule, not a layout.
_BREAKPOINT_RANGE = (320, 1920)


def collect_breakpoints(css_blocks: list[str]) -> list[int]:
    """The page's own responsive breakpoints, in px, ascending.

    Counted per `@media` rule rather than per stylesheet, so the widths the theme actually switches
    at outrank a one-off rule in a plugin's sheet. `em`/`rem` are taken at 16px, which is what every
    browser resolves a media-query em against regardless of the page's root font size. Width values
    within 8px of each other are one breakpoint written two ways (`max-width: 767px` and
    `min-width: 768px`) and are merged onto the more common one.
    """
    counts: dict[int, int] = {}
    for css in css_blocks:
        for query in _MEDIA_RULE.findall(css):
            for value, unit in _MEDIA_WIDTH.findall(query):
                px = float(value) * (16 if unit.lower() in {"em", "rem"} else 1)
                width = int(round(px))
                if _BREAKPOINT_RANGE[0] <= width <= _BREAKPOINT_RANGE[1]:
                    counts[width] = counts.get(width, 0) + 1
    merged: dict[int, int] = {}
    for width, count in sorted(counts.items(), key=lambda item: -item[1]):
        near = next((kept for kept in merged if abs(kept - width) <= 8), None)
        if near is None:
            merged[width] = count
        else:
            merged[near] += count
    top = sorted(merged.items(), key=lambda item: -item[1])[:_MAX_BREAKPOINTS]
    return sorted(width for width, _count in top)


def _build_tokens(source_url: str, sources: _Sources) -> DesignTokens:
    declared = _collect_custom_properties(sources)
    # Custom properties are resolved against each other first, so everything downstream — the
    # palette, the radii, the button styles — sees real values rather than names.
    declared = {name: resolve_vars(value, declared) for name, value in declared.items()}

    colors = _collect_colors(sources, declared)
    palette = sorted(colors.values(), key=lambda c: -c.total)[:24]

    return DesignTokens(
        source_url=source_url,
        available=True,
        palette=palette,
        custom_properties={
            name: value for name, value in declared.items() if _is_useful_token(name, value)
        },
        font_families=[
            row for row in _collect_values(sources, "font-family", limit=12, properties=declared) if not is_icon_font(row[0])
        ][:8],
        font_links=sources.font_links,
        radii=_collect_values(sources, "border-radius", limit=8, properties=declared),
        buttons=_collect_buttons(sources, properties=declared),
        breakpoints=collect_breakpoints(sources.css),
        stylesheet_urls=sources.stylesheet_urls,
        notes=sources.notes,
        html=sources.html,
    )


async def extract_design_tokens_from_html(html: str, base_url: str) -> DesignTokens:
    """Same extraction, from HTML the operator supplied instead of a page we fetched.

    This is the answer for a site behind a bot wall. Some hosts answer a server with a JS captcha
    while serving browsers normally — SiteGround's `sgcaptcha` is one, and no header, cookie jar or
    user-agent gets a server past it. Nothing on this side can fix that, but the operator's own
    browser already has the page: View Source, copy, paste.

    `base_url` still matters and is not cosmetic. Stylesheet and logo references on a real page are
    relative (`/wp-content/themes/x/style.css`), so without the page's own address there is nothing
    to resolve them against and the extraction yields a palette with no stylesheets behind it.
    """
    sources = _Sources()
    sources.html = html
    sources.css.extend(_CSS_COMMENT.sub(" ", block) for block in _STYLE_BLOCK.findall(html))
    sources.inline_styles.extend(_STYLE_ATTR.findall(html))

    resolved_base = normalize_url(base_url) if base_url else ""
    sheet_urls, font_links = _stylesheet_hrefs(html, resolved_base) if resolved_base else ([], [])
    sources.stylesheet_urls = sheet_urls[:_MAX_STYLESHEETS]
    sources.font_links = font_links

    # The linked stylesheets are usually still fetchable even when the page is not: a bot wall
    # guards documents, while CSS is served from the same host (or a CDN) without a challenge.
    if sheet_urls:
        sheets, notes = await _fetch_stylesheets(sheet_urls, resolved_base)
        sources.css.extend(_CSS_COMMENT.sub(" ", sheet) for sheet in sheets)
        sources.notes.extend(notes)

    if not sources.css and not sources.inline_styles:
        return DesignTokens(
            source_url=resolved_base or "(pasted HTML)",
            available=False,
            reason=(
                "The supplied HTML carries no stylesheets or inline styles. If it came from "
                '"View Source", try the browser\'s "Inspect" panel instead, or paste the '
                "stylesheet's contents as well."
            ),
        )

    sources.notes.append("Extracted from HTML supplied by the operator, not from a live fetch.")
    tokens = _build_tokens(resolved_base or "(pasted HTML)", sources)
    try:
        tokens.logo = await find_logo(html, resolved_base)
    except Exception as exc:  # noqa: BLE001
        logger.info("Logo lookup failed on pasted HTML (%s)", exc)
    logger.info(
        "Design tokens from pasted HTML (%s): %d colours, %d custom properties, logo=%s",
        resolved_base or "no base url",
        len(tokens.palette),
        len(tokens.custom_properties),
        bool(tokens.logo),
    )
    return tokens


async def extract_design_tokens(url: str) -> DesignTokens:
    """Read `url`'s design system. Never raises for an unreadable page — see `available`."""
    try:
        final_url, sources = await collect_css(url)
    except DesignTokenError as exc:
        logger.info("Design tokens unavailable for %s: %s", url, exc)
        return DesignTokens(source_url=url, available=False, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — an unreadable page must not fail the stage
        logger.warning("Design token extraction failed for %s: %s", url, exc)
        return DesignTokens(source_url=url, available=False, reason=str(exc))

    tokens = _build_tokens(final_url, sources)
    # After the tokens, because a failed logo lookup must not cost the palette.
    try:
        tokens.logo = await find_logo(sources.html, final_url)
    except Exception as exc:  # noqa: BLE001
        logger.info("Logo lookup failed for %s (%s)", final_url, exc)
    logger.info(
        "Design tokens for %s: %d colours, %d custom properties, %d fonts, %d button rules",
        final_url,
        len(tokens.palette),
        len(tokens.custom_properties),
        len(tokens.font_families),
        len(tokens.buttons),
    )
    return tokens


# --------------------------------------------------------------------------------------
# Rendering
#
# Two forms, because the prompts need two different things. The Markdown sheet is what a model
# reads to *decide* ("the CTA background is #e8590c"), and the CSS block is what it copies to
# *build*. Emitting only the prose would have every generated page re-deriving custom-property
# names, and they would differ from each other.
# --------------------------------------------------------------------------------------


def tokens_to_css(tokens: DesignTokens) -> str:
    """A `:root` block the generated HTML can paste in whole."""
    if not tokens.available:
        return ""

    lines = [":root {"]
    if tokens.page_background:
        lines.append(f"  --brand-bg: {tokens.page_background};")
    if tokens.body_text:
        lines.append(f"  --brand-text: {tokens.body_text};")
    if tokens.accent:
        lines.append(f"  --brand-accent: {tokens.accent};")
    if tokens.heading_font:
        lines.append(f"  --brand-font-heading: {tokens.heading_font};")
    if len(tokens.font_families) > 1:
        lines.append(f"  --brand-font-body: {tokens.font_families[1][0]};")
    if tokens.radii:
        lines.append(f"  --brand-radius: {tokens.radii[0][0]};")

    for index, color in enumerate([c for c in tokens.palette if not is_neutral(c.hex)][:6], start=1):
        lines.append(f"  --brand-{color.role}-{index}: {color.hex};")

    # The site's own names, verbatim. Where a theme declares its palette this way, reusing its
    # names keeps the generated page speaking the same language as the real one.
    for name, value in list(tokens.custom_properties.items())[:24]:
        lines.append(f"  {name}: {value};")

    lines.append("}")
    return "\n".join(lines)


def tokens_to_markdown(tokens: DesignTokens) -> str:
    """The human- and model-readable token sheet."""
    if not tokens.available:
        return (
            f"## Brand Design Tokens — NOT AVAILABLE\n\n"
            f"**Source**: {tokens.source_url}\n\n"
            f"The page could not be read: {tokens.reason}\n\n"
            "No colours, fonts or component styles were extracted. Do not invent a palette — ask "
            "for the brand's hex codes, font family and button style, or build in neutral greys "
            "and mark them as placeholders."
        )

    out = [
        "## Brand Design Tokens (extracted)",
        "",
        f"**Source**: {tokens.source_url}",
        "",
        "Every value below was read from that page's own CSS. These are the only colours, fonts and "
        "component styles that may appear in generated HTML.",
        "",
        "### Core",
        "",
        "| Token | Value |",
        "|---|---|",
    ]
    for label, value in (
        ("Page background", tokens.page_background),
        ("Body text", tokens.body_text),
        ("Brand / accent", tokens.accent),
        ("Heading font", tokens.heading_font),
        ("Body font", tokens.font_families[1][0] if len(tokens.font_families) > 1 else None),
        ("Default radius", tokens.radii[0][0] if tokens.radii else None),
    ):
        if value:
            out.append(f"| {label} | `{value}` |")

    out += ["", "### Palette (by where the page actually uses each colour)", "",
            "| Colour | Mostly used as | background | text | border |", "|---|---|---|---|---|"]
    for color in tokens.palette[:14]:
        out.append(f"| `{color.hex}` | {color.role} | {color.background} | {color.text} | {color.border} |")

    if tokens.custom_properties:
        out += ["", "### The site's own design tokens", "",
                "Declared by the theme itself — reuse these names where the generated page needs the "
                "same value.", "", "| Property | Value |", "|---|---|"]
        for name, value in list(tokens.custom_properties.items())[:30]:
            out.append(f"| `{name}` | `{value}` |")

    if tokens.font_families:
        out += ["", "### Type", "", "| Font stack | Rules using it |", "|---|---|"]
        for stack, count in tokens.font_families:
            out.append(f"| `{stack}` | {count} |")
    if tokens.font_links:
        out += ["", "Webfont links to reproduce exactly in the generated `<head>`:", ""]
        out += [f"- `{link}`" for link in tokens.font_links]

    if tokens.logo:
        logo = tokens.logo
        out += ["", "### Logo — use THIS, do not recreate it", ""]
        if logo.svg_markup:
            out += [
                f"Found as an {logo.source}. It is inline SVG, so paste this markup directly into "
                "the generated HTML — it scales and can be recoloured with `currentColor` where the "
                "original does:",
                "",
                "```html",
                logo.svg_markup,
                "```",
            ]
        else:
            out += ["| Field | Value |", "|---|---|",
                    f"| Found as | {logo.source} |",
                    f"| URL | `{logo.url}` |"]
            if logo.mime_type:
                out.append(f"| Type | {logo.mime_type} |")
            if logo.byte_size:
                out.append(f"| Size | {logo.byte_size:,} bytes |")
            if logo.alt_text:
                out.append(f"| Alt text | {logo.alt_text} |")
            if logo.width or logo.height:
                out.append(f"| Declared size | {logo.width or '?'} x {logo.height or '?'} |")
            if logo.data_uri:
                out += [
                    "",
                    "Small enough to embed, so the generated file needs no external asset. Use this "
                    "as the `src` verbatim:",
                    "",
                    "```html",
                    f'<img src="{logo.data_uri}" alt="{logo.alt_text or "Company logo"}">',
                    "```",
                ]
            else:
                out += [
                    "",
                    "Too large to embed"
                    + (f" ({logo.byte_size:,} bytes)" if logo.byte_size else "")
                    + ", so reference it at the absolute "
                    f"URL above — it is the client's own logo on the client's own domain. Preserve "
                    "its aspect ratio; set one dimension and leave the other `auto`.",
                ]

    if tokens.buttons:
        out += ["", "### Buttons", "",
                "| Selector | Background | Text | Radius | Padding | Weight | Hover bg |",
                "|---|---|---|---|---|---|---|"]
        for button in tokens.buttons:
            out.append(
                f"| `{button.selector[:44]}` | {button.background or '—'} | {button.color or '—'} "
                f"| {button.border_radius or '—'} | {button.padding or '—'} "
                f"| {button.font_weight or '—'} | {button.hover_background or '—'} |"
            )

    if tokens.radii:
        out += ["", "### Radii", "", ", ".join(f"`{value}` ({count})" for value, count in tokens.radii)]

    css = tokens_to_css(tokens)
    if css:
        out += ["", "### Ready-to-use CSS custom properties", "", "```css", css, "```"]

    if tokens.notes:
        out += ["", "### Reading notes", ""] + [f"- {note}" for note in tokens.notes]

    return "\n".join(out)
