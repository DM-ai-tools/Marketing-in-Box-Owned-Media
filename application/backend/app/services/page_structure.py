"""Read a page's *skeleton* off its DOM, so a generated page can follow it instead of inventing one.

Why this exists
---------------
`design_tokens.py` argued that a prompt forbidding invented colours was unfollowable while the only
thing reaching the model was stripped text, and fixed it by measuring the CSS. `design_md.py` then
gave those measurements a schema. This module closes the same gap one level up, for *structure*.

The replica stages (`PAGE_REPLICA_STAGES` in `generation.py`) are told a visitor must not be able
to tell the generated page from the client's own. Until this existed, three things reached them and
none carried structure:

  * the page *text*, with every tag stripped — and `<nav>` and `<svg>` dropped outright by
    `scraper._DROP_CONTENT_TAGS`, so the menu, the logo's placement and every inline icon were
    already gone before the model saw anything;
  * `DESIGN.md`, which is a style sheet — it says buttons have a 30px radius and says nothing
    about there being nine sections in a particular order;
  * two screenshots, of which the full-page one is usually withheld, because a real landing page's
    full-height shot measured 1920x17615 and Anthropic rejects anything past 8000px
    (`context_dev.Screenshot.model_safe`). So in practice the model sees the top 900 pixels and
    guesses the rest of the page.

Asked to reproduce a page it has never seen below the fold, a model does the only thing it can: it
invents a plausible section order and presents it as a replica. That failure is invisible in the
output — a clean page in the wrong section order looks exactly like a correct one.

So: measurement again, not instruction. The DOM says how many bands there are, what order they run
in, which one holds the h1, where the logo sits relative to the nav, which images are the client
logo strip, what the phone number is and which bands repeat it, how many inline SVGs there are and
what they are, and what every heading says. All of it is free — no Context.dev credits — because
the HTML was already fetched to read the CSS.

What this is not
----------------
It is a *description*, not a template. It tells the model the sequence to follow and the details to
carry over verbatim; the model still writes the HTML, so the geometry is still generated and can
still drift. `page_replica.py` is the answer to that — it transports the real markup and swaps only
the words. This module remains useful even so, and is not scaffolding for that one:

  * it is the fallback whenever a replica capture is impossible or declined (a page too big to
    template, a reference on a domain that is not the client's);
  * it is what makes the *screenshot* and *token* views legible — a band list with roles is how the
    model knows which of the nine sections the hero shot is showing;
  * it is what an operator reads to check the capture understood the page at all.

Every field is either read off the DOM or absent. Nothing here is inferred to fill a gap, for the
reason `design_tokens.py` states about palettes and this module inherits about layout.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from app.services.design_tokens import is_theme_bundled_asset
from app.services.html_dom import Document, Node, parse_html, render

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------------------------
# Budgets
#
# This section is prepended to every replica stage's prompt, so it is sized like a prompt fragment
# rather than like a report. The caps below hold a real nine-band landing page to roughly 3-4k
# characters; an uncapped walk of the same page ran past 30k, most of it a nav's worth of `<li>`
# and forty repetitions of the same chevron icon.
# ----------------------------------------------------------------------------------------------

_MAX_BANDS = 24
_MAX_HEADINGS = 60
_HEADING_CHARS = 140
_MAX_NAV_ITEMS = 20
_MAX_STRIP_IMAGES = 12
_MAX_FORM_FIELDS = 12
_MAX_FORMS = 4

# An inline SVG is quoted in full only when it is small enough to be worth pasting. A logo or an
# illustration runs to tens of kilobytes of path data; an icon is a few hundred bytes, and those are
# the ones the model would otherwise substitute with an emoji or a Font Awesome class it cannot
# load. `page_replica.py` transports all of them regardless of size — this budget is only about
# what fits in a description.
_SVG_MARKUP_MAX = 900
_SVG_MARKUP_BUDGET = 5000
_MAX_SVG_KINDS = 10

#: Below this, a band is furniture (a spacer div, a stray `<hr>`) rather than a section.
_MIN_BAND_TEXT = 15

# Elements that occupy no space on the page and must never be reported as a band.
_NON_VISUAL = frozenset({"script", "style", "noscript", "template", "link", "meta", "br", "hr", "wbr"})

# Tags a lone child may be unwrapped through when hunting for the level the real bands live on.
# `section` is deliberately absent: a page whose body holds exactly one `<section>` has one band,
# and unwrapping it would report that section's *contents* as the page's sections.
_UNWRAP_TAGS = frozenset({"div", "main"})

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


# ----------------------------------------------------------------------------------------------
# Roles
#
# Class and id names first, because they are what the people who built the page called it, and a
# `<section class="testimonials">` is not a guess. Content shape second, for the pages whose
# classes are hashed (`css-1x2y3z`) or utility-first (`flex gap-8 py-24`) and say nothing.
# ----------------------------------------------------------------------------------------------

_ROLE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hero", ("hero", "banner", "masthead", "jumbotron", "above-fold", "abovefold", "page-title")),
    ("client logo strip", ("logo-strip", "logostrip", "client-logo", "brands", "partners", "as-seen", "trusted-by")),
    ("testimonials", ("testimonial", "review", "feedback", "client-say", "what-they-say")),
    ("FAQ", ("faq", "accordion", "questions")),
    ("pricing", ("pricing", "price-", "plans", "packages")),
    ("statistics", ("stats", "stat-", "counter", "metrics", "achievement")),
    ("process / steps", ("process", "how-it-works", "howitworks", "steps", "timeline", "roadmap")),
    ("case studies / portfolio", ("case-stud", "casestud", "portfolio", "our-work", "gallery", "projects")),
    ("team", ("team", "our-people", "staff", "leadership")),
    ("blog / resources", ("blog", "news", "insights", "resources", "articles")),
    ("services / features", ("service", "feature", "benefit", "offering", "what-we-do", "solutions", "capabilit")),
    ("about", ("about", "who-we-are", "our-story", "mission")),
    ("form / lead capture", ("contact-form", "enquiry", "inquiry", "get-a-quote", "quote-form", "booking", "signup")),
    ("call to action", ("cta", "get-started", "ready-to", "book-now")),
    ("newsletter", ("newsletter", "subscribe", "mailing")),
    ("comparison", ("compare", "comparison", "vs-")),
)

_TAG_ROLES = {
    "header": "site header",
    "footer": "site footer",
    "nav": "navigation",
    "aside": "sidebar",
    "form": "form / lead capture",
}

# A phone number as a person writes one: an optional +country, then 8-16 digits broken by spaces,
# dashes, dots or brackets. Deliberately not a strict E.164 match — the page says "1300 049 490"
# and "(02) 8005 1234", and both have to be carried over exactly as written.
_PHONE_TEXT = re.compile(r"(?:\+?\d[\d\s().\-]{7,18}\d)")
_PHONE_DIGITS = re.compile(r"\d")
_EMAIL_TEXT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Words that, immediately before a run of digits, mean it is not a phone number. Every one of these
# was hit on a real footer: a company's ABN is eleven digits grouped exactly like a landline, and
# reporting it as the client's phone number is the kind of error that reaches a printed page.
_NOT_A_PHONE_AFTER = (
    "abn", "acn", "acl", "afsl", "gst", "tfn", "vat", "ein", "invoice", "licen", "registration",
    "reg no", "company no", "sku", "ndis", "postcode", "po box",
)

# `alt` / `src` fragments that mean "this image is somebody's logo".
_LOGO_WORDS = ("logo", "brand", "client", "partner", "badge", "award", "certifi", "as-seen", "featured-in")

# Class names that identify nothing. `class="icon icon-search"` should report `icon-search`, and a
# hashed CSS-in-JS name (`css-1x2y3z`, `sc-AxjAm`) identifies nothing at all.
_GENERIC_CLASSES = frozenset({"icon", "svg", "inline", "icon-inline", "i", "image", "img", "symbol"})
_HASHED_CLASS_PREFIXES = ("css-", "sc-", "jsx-", "emotion-")


def _looks_like_phone(text: str) -> bool:
    """Whether a matched run of digits is plausibly a phone number rather than a price or a year.

    8-15 digits is the international range. The upper bound matters as much as the lower: a page's
    ABN, a 16-digit card placeholder and a run of table figures all match the character class.
    """
    return 8 <= len(_PHONE_DIGITS.findall(text)) <= 15


def _phone_candidates(text: str) -> list[str]:
    """Phone-shaped runs in `text`, with the ones a leading label disqualifies removed."""
    out: list[str] = []
    for match in _PHONE_TEXT.finditer(text):
        value = match.group(0).strip()
        if not _looks_like_phone(value):
            continue
        lead = text[max(0, match.start() - 24) : match.start()].lower()
        if any(word in lead for word in _NOT_A_PHONE_AFTER):
            continue
        out.append(value)
    return out


def _specific_class(node: Node) -> str:
    """The most identifying class on `node`, or `""`."""
    names = [
        name
        for name in node.classes
        if name and not name.lower().startswith(_HASHED_CLASS_PREFIXES)
    ]
    specific = [name for name in names if name.lower() not in _GENERIC_CLASSES]
    chosen = specific or names
    return chosen[0] if chosen else ""


# ----------------------------------------------------------------------------------------------
# The pieces
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    """One top-level horizontal section of the page — what a person means by "the bit with the
    testimonials"."""

    index: int
    role: str
    signature: str
    heading: str = ""
    heading_level: int = 0
    #: Element counts inside the band, already filtered to what a rebuild has to reproduce.
    inventory: tuple[tuple[str, int], ...] = ()
    #: `(count, child signature)` when the band is a repeated grid — three cards, six logos.
    repeat: tuple[int, str] | None = None

    @property
    def repeat_note(self) -> str:
        if not self.repeat:
            return ""
        count, signature = self.repeat
        return f"{count}x {signature}" if signature else f"{count} repeated items"


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    band_index: int


@dataclass(frozen=True)
class NavGroup:
    """One menu, with its labels in order. Reproduced verbatim: a nav with the right look and
    invented labels is worse than no nav, because it is wrong in a way nobody proofreads."""

    where: str
    items: tuple[tuple[str, str], ...]
    signature: str = ""


@dataclass(frozen=True)
class Contact:
    """A real contact detail. Carried over exactly — this is the field an operator notices."""

    kind: str
    value: str
    href: str = ""
    #: The band roles it appears in, so a rebuild puts it back in all of them.
    where: tuple[str, ...] = ()


@dataclass(frozen=True)
class LogoStrip:
    """A row of somebody else's marks — client logos, partner badges, award seals.

    Called out separately from "images in a band" because these are the one image class a rebuild
    must reference by URL rather than describe: a generated page that replaces eight real client
    logos with eight grey placeholder rectangles has lost the section's entire purpose.
    """

    band_index: int
    images: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class InlineSvg:
    """One kind of inline SVG on the page, with how many times it occurs.

    Deduped by markup, because a card grid repeats the same tick icon nine times and listing it
    nine times spends the budget on one shape.
    """

    label: str
    count: int
    byte_size: int
    markup: str = ""
    band_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormSpec:
    signature: str
    action: str = ""
    method: str = ""
    fields: tuple[tuple[str, str, bool], ...] = ()
    submit_label: str = ""
    band_index: int = 0


@dataclass
class PageStructure:
    """One page's skeleton.

    `available=False` is a first-class outcome, for the reason `DesignTokens` and `PageDesign` both
    state: a described structure that was actually guessed is indistinguishable from a measured one
    until somebody compares the two pages side by side.
    """

    source_url: str
    available: bool
    reason: str | None = None
    bands: tuple[Band, ...] = ()
    headings: tuple[Heading, ...] = ()
    navs: tuple[NavGroup, ...] = ()
    contacts: tuple[Contact, ...] = ()
    logo_strips: tuple[LogoStrip, ...] = ()
    inline_svgs: tuple[InlineSvg, ...] = ()
    forms: tuple[FormSpec, ...] = ()
    #: Where the client's own mark sits, in prose ("inside `header#masthead`, first element, ahead
    #: of the nav"). The one placement fact that is always asked about and never in a token sheet.
    logo_placement: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def band_count(self) -> int:
        return len(self.bands)


# ----------------------------------------------------------------------------------------------
# Finding the bands
# ----------------------------------------------------------------------------------------------


def _visible(nodes: list[Node]) -> list[Node]:
    return [node for node in nodes if node.tag not in _NON_VISUAL]


def _has_substance(node: Node) -> bool:
    """Whether a band is a section or just furniture."""
    if len(node.text_content()) >= _MIN_BAND_TEXT:
        return True
    return bool(node.find_all("img", "svg", "form", "input", "video", "iframe", "picture"))


def _is_page_wrapper(node: Node) -> bool:
    """Whether a `<div>` is the container the real sections live in rather than a section itself.

    The test is "does it hold several things that each look like a section", not "is it big" — a
    hero `<div>` is also big, and unwrapping that would report the hero's headline, subhead and
    button row as three page sections.
    """
    children = _visible(node.elements)
    if len(children) < 3:
        return False
    sectionish = sum(
        1
        for child in children
        if child.tag in {"section", "article", "header", "footer", "aside", "main"}
        or child.find(*_HEADING_TAGS) is not None
    )
    return sectionish >= max(2, len(children) // 2)


def _band_nodes(body: Node) -> list[Node]:
    """The page's top-level bands, in document order.

    Real pages bury them: `body > div#page > div.site-content > main > section*` is an ordinary
    WordPress shape. So this descends through lone wrappers to find the level the siblings live on,
    then expands `<main>` and any single wrapper that is holding the sections rather than being one.
    """
    level = _visible(body.elements)

    # Descend through lone wrappers. Bounded — a page builder nests these, but not forever, and an
    # unbounded loop on a malformed tree would walk to a leaf and report no bands at all.
    for _ in range(6):
        if len(level) == 1 and level[0].tag in _UNWRAP_TAGS and _visible(level[0].elements):
            level = _visible(level[0].elements)
            continue
        break

    expanded: list[Node] = []
    for node in level:
        if node.tag == "main" and len(_visible(node.elements)) >= 2:
            expanded.extend(_visible(node.elements))
        elif node.tag == "div" and _is_page_wrapper(node):
            expanded.extend(_visible(node.elements))
        else:
            expanded.append(node)

    return [node for node in expanded if _has_substance(node)]


# ----------------------------------------------------------------------------------------------
# Describing one band
# ----------------------------------------------------------------------------------------------


def _hint_haystack(node: Node) -> str:
    return f"{node.element_id} {' '.join(node.classes)}".lower()


def _repeat_group(band: Node) -> tuple[int, str] | None:
    """The band's repeated child group, if it has one — `(count, signature)`.

    "Three cards in a row" is the single most reproducible fact about a section and the one a
    rebuild most often gets wrong (four cards, or a vertical list). Found by looking for the
    container whose element children are all the same tag and share a class.

    The **shallowest** qualifying group wins, not the largest. A band's grid is its top-level
    repetition, and repetition nested inside it — six bullets inside each of three cards — is a
    detail of a card, not the band's layout. Selecting by count instead reported a services band as
    "6x li" because the bullets outnumbered the cards holding them.
    """
    best: tuple[int, int, str] | None = None  # (depth, -count, signature)
    for node in band.walk():
        if node.tag == "nav" or node.closest("nav") is not None:
            continue
        depth = 0
        walker = node
        while walker is not band and walker.parent is not None:
            walker = walker.parent
            depth += 1
        # Headings are excluded before the same-tag test, not after. A section is very often
        # `<section><h2>What We Do</h2><div class="stat">*3</div></section>` with no wrapper around
        # the grid, and requiring *every* child to match made that section's three-up layout
        # invisible while a wrapped one was found — the same page shape reported two different ways.
        children = [child for child in _visible(node.elements) if child.tag not in _HEADING_TAGS]
        if len(children) < 3:
            continue
        if len({child.tag for child in children}) != 1:
            continue
        # Enough content to be a cell rather than a spacer. Deliberately low: a stat card reads
        # "18 years in market", and an earlier 20-character floor made a real three-up stats band
        # report no layout at all because one of its three cards was eighteen characters long.
        substantial = [
            child
            for child in children
            if len(child.text_content()) >= 8 or child.find("img", "svg") is not None
        ]
        if len(substantial) < 3:
            continue
        shared = frozenset.intersection(*(frozenset(child.classes) for child in children))
        # A run of bare `<p>` is prose, not a grid. Anything else that repeats three times with a
        # shared class, or carries an image each, is a layout the rebuild has to match.
        if not shared and children[0].tag == "p":
            continue
        signature = children[0].tag + "".join(f".{name}" for name in sorted(shared)[:2])
        candidate = (depth, -len(substantial), signature)
        if best is None or candidate < best:
            best = candidate
    return (-best[1], best[2]) if best is not None else None


def _band_inventory(band: Node) -> tuple[tuple[str, int], ...]:
    """What is inside the band, as counts a rebuild can check itself against."""
    counted: list[tuple[str, int]] = []

    def add(singular: str, value: int, plural: str = "") -> None:
        if value:
            counted.append((singular if value == 1 else (plural or f"{singular}s"), value))

    add("heading", len(band.find_all(*_HEADING_TAGS)))
    add("paragraph", len(band.find_all("p")))
    add("image", len(band.find_all("img", "picture")))
    add("inline SVG", len(band.find_all("svg")))
    buttons = [
        node
        for node in band.find_all("a", "button", "input")
        if node.tag == "button"
        or (node.tag == "input" and node.attr("type").lower() in {"submit", "button"})
        or any("btn" in name or "button" in name for name in node.classes)
    ]
    add("button", len(buttons))
    add("link", len(band.find_all("a")) - len([b for b in buttons if b.tag == "a"]))
    add("list item", len(band.find_all("li")))
    add("form field", len(band.find_all("input", "textarea", "select")))
    add("video / embed", len(band.find_all("video", "iframe")), "videos / embeds")
    return tuple(counted)


def _band_role(node: Node, index: int, total: int) -> str:
    """What to call this band.

    Order is the argument: the tag is a fact, the class name is what the authors called it, and the
    content shape is a guess. A guess never overrides either of the first two.
    """
    if node.tag in _TAG_ROLES:
        return _TAG_ROLES[node.tag]

    haystack = _hint_haystack(node)
    for role, hints in _ROLE_HINTS:
        if any(hint in haystack for hint in hints):
            return role

    # Content shape, in descending confidence.
    if node.find("h1") is not None and index <= 1:
        return "hero"
    fields = node.find_all("input", "textarea", "select")
    if len(fields) >= 2:
        return "form / lead capture"
    if len(node.find_all("blockquote")) >= 2:
        return "testimonials"
    if len(node.find_all("details")) >= 2:
        return "FAQ"
    images = node.find_all("img")
    if len(images) >= 3 and node.find(*_HEADING_TAGS) is None:
        return "client logo strip"
    if node.find("table") is not None:
        return "comparison table"
    if index == total - 1 and node.find_all("a"):
        return "closing band"
    return "content band"


def _first_heading(node: Node) -> tuple[str, int]:
    for candidate in node.descendants():
        if candidate.tag in _HEADING_TAGS:
            text = candidate.text_content()
            if text:
                return text[:_HEADING_CHARS], int(candidate.tag[1])
    return "", 0


# ----------------------------------------------------------------------------------------------
# Details a rebuild has to carry over verbatim
# ----------------------------------------------------------------------------------------------


def _absolute(url: str, base_url: str) -> str:
    """Resolve a page-relative reference. Without this every asset in the description is a path
    that means nothing off the original host, which is exactly how a rebuild ends up with broken
    images and no logo."""
    if not url or url.startswith(("data:", "http://", "https://", "//", "mailto:", "tel:")):
        return url
    return urljoin(base_url, url) if base_url else url


def _image_pair(node: Node, base_url: str) -> tuple[str, str]:
    src = node.attr("src") or node.attr("data-src") or node.attr("data-lazy-src")
    if not src:
        srcset = node.attr("srcset") or node.attr("data-srcset")
        if srcset:
            src = srcset.split(",")[0].strip().split(" ")[0]
    return _absolute(src, base_url), node.attr("alt")


def _nav_groups(document: Document, bands: list[Node]) -> tuple[NavGroup, ...]:
    """Every menu on the page, labelled by where it sits.

    Falls back to a header `<ul>` of links when the page has no `<nav>` at all, which is common on
    hand-built and page-builder sites and is the case that made the nav invisible before: the old
    reader dropped `<nav>`, and a page without one had nothing to drop but nothing to report either.
    """
    groups: list[NavGroup] = []
    seen: set[int] = set()

    def where_of(node: Node) -> str:
        if node.closest("footer") is not None:
            return "footer"
        if node.closest("header") is not None:
            return "header"
        for index, band in enumerate(bands):
            if node is band or node in list(band.descendants()):
                return f"band {index + 1}"
        return "page"

    def items_of(node: Node) -> tuple[tuple[str, str], ...]:
        out: list[tuple[str, str]] = []
        for anchor in node.find_all("a"):
            label = anchor.text_content()
            if not label:
                continue
            out.append((label[:60], anchor.attr("href")))
            if len(out) >= _MAX_NAV_ITEMS:
                break
        return tuple(out)

    for nav in document.body.find_all("nav"):
        items = items_of(nav)
        if not items:
            continue
        groups.append(NavGroup(where=where_of(nav), items=items, signature=nav.signature))
        seen.add(id(nav))

    if not groups:
        header = document.body.find("header")
        if header is not None:
            for candidate in header.find_all("ul"):
                items = items_of(candidate)
                if len(items) >= 3:
                    groups.append(
                        NavGroup(where="header (no <nav> element)", items=items, signature=candidate.signature)
                    )
                    break
    return tuple(groups)


def _band_role_at(node: Node, band_roles: list[tuple[Node, str]]) -> str:
    for band, role in band_roles:
        if node is band or any(descendant is node for descendant in band.descendants()):
            return role
    return "page"


def _contacts(document: Document, band_roles: list[tuple[Node, str]]) -> tuple[Contact, ...]:
    """Phone numbers, email addresses and postal addresses, with the bands they appear in.

    Three passes, in descending order of trust, and the ordering is the whole design:

      1. **`tel:` and `mailto:` links.** Unambiguous — the page itself says what the value is and
         what to dial. Where an anchor reads "Call 1300 049 490", the phone-shaped part is taken and
         the word is left behind, so the value can be matched against page text in pass 2.
      2. **Where those known values also appear.** Each band's text is searched for the values
         already found, which is how a number that is a link in the header and plain text in the
         footer gets reported as being in both.
      3. **A pattern scan, only when a pass-1 lookup found nothing of that kind.** This is a
         fallback, not an addition, and that matters: run as an addition over a real footer it
         returned `3000 1300 049 490` (a postcode fused to the phone number) and
         `12 345 678 901` (the company's ABN) alongside the correct value, and a list of four
         numbers of which three are wrong is worse than one number — the operator cannot tell
         which to trust, and neither can the model.
    """
    found: dict[tuple[str, str], set[str]] = {}
    hrefs: dict[tuple[str, str], str] = {}

    def record(kind: str, value: str, href: str, node: Node) -> None:
        value = " ".join(value.split())
        if not value:
            return
        key = (kind, value)
        found.setdefault(key, set()).add(_band_role_at(node, band_roles))
        if href and key not in hrefs:
            hrefs[key] = href

    # 1 — the linked forms.
    for anchor in document.body.find_all("a"):
        href = anchor.attr("href").strip()
        target = href.lower()
        text = anchor.text_content()
        if target.startswith("tel:"):
            candidates = _phone_candidates(text)
            record("phone", candidates[0] if candidates else href[4:], href, anchor)
        elif target.startswith("mailto:"):
            emails = _EMAIL_TEXT.findall(text)
            record("email", emails[0] if emails else href[7:], href, anchor)

    for node in document.body.find_all("address"):
        record("address", node.text_content()[:200], "", node)

    # 2 — the same values wherever else they are printed.
    known = [(kind, value) for kind, value in found if kind in {"phone", "email"}]
    for band, role in band_roles:
        text = band.text_content()
        for kind, value in known:
            if value in text:
                found[(kind, value)].add(role)

    # 3 — the fallback, per kind.
    linked_kinds = {kind for kind, _ in found}
    for chrome in (document.body.find("header"), document.body.find("footer")):
        if chrome is None:
            continue
        text = chrome.text_content()
        if "phone" not in linked_kinds:
            for value in _phone_candidates(text):
                record("phone", value, "", chrome)
        if "email" not in linked_kinds:
            for value in _EMAIL_TEXT.findall(text):
                record("email", value, "", chrome)

    ordering = {"phone": 0, "email": 1, "address": 2}
    keys = sorted(found, key=lambda key: (ordering.get(key[0], 9), key[1]))
    return tuple(
        Contact(kind=kind, value=value, href=hrefs.get((kind, value), ""), where=tuple(sorted(found[(kind, value)])))
        for kind, value in keys
    )


def _logo_strips(bands: list[Node], base_url: str) -> tuple[LogoStrip, ...]:
    """Rows of third-party marks — client logos, partner badges, award seals.

    A strip is two or more images in one container with no heading between them, or a container
    whose class says so. `alt` text is kept: it names the client, and a rebuild that keeps the
    image but drops the alt loses the only accessible version of the section's content.
    """
    strips: list[LogoStrip] = []
    for index, band in enumerate(bands):
        haystack = _hint_haystack(band)
        declared = any(word in haystack for word in _LOGO_WORDS)
        min_count = 2 if declared else 3
        for node in band.walk():
            images = [child for child in _visible(node.elements) if child.tag in {"img", "picture"}]
            if len(images) < min_count:
                # A strip is usually `div > a > img`, so also accept a container whose children
                # each hold exactly one image.
                wrapped = [
                    child.find("img")
                    for child in _visible(node.elements)
                    if child.find("img") is not None and len(child.find_all("img")) == 1
                ]
                images = [img for img in wrapped if img is not None]
            if len(images) < min_count:
                continue
            alt_hits = sum(
                1
                for img in images
                if any(word in f"{img.attr('alt')} {img.attr('src')}".lower() for word in _LOGO_WORDS)
            )
            if not declared and alt_hits < max(1, len(images) // 3):
                continue
            pairs = tuple(_image_pair(img, base_url) for img in images[:_MAX_STRIP_IMAGES])
            strips.append(LogoStrip(band_index=index + 1, images=pairs))
            break
    return tuple(strips)


def _svg_label(node: Node) -> str:
    """What to call an inline SVG. Its own accessible name if it has one, otherwise the class of
    the thing holding it, which on a real page is `.icon-tick` or `.feature__icon` often enough to
    be worth reporting."""
    for attribute in ("aria-label", "data-name", "id"):
        value = node.attr(attribute).strip()
        if value:
            return value[:60]
    title = node.find("title")
    if title is not None and title.text_content():
        return title.text_content()[:60]
    own = _specific_class(node)
    if own:
        return own[:60]
    if node.parent is not None:
        parent = _specific_class(node.parent)
        if parent:
            return f"inside .{parent}"[:60]
    return "unnamed icon"


def _inline_svgs(bands: list[Node], band_roles: list[tuple[Node, str]]) -> tuple[InlineSvg, ...]:
    """Every inline SVG, deduped by markup and counted.

    These were invisible before — `scraper._DROP_CONTENT_TAGS` contains `svg`, so the reader threw
    away every icon on the page. A model that is not told they exist either omits them, leaving
    cards that read as bare text, or substitutes an emoji or a Font Awesome class it has no
    stylesheet for, which renders as nothing at all.
    """
    by_markup: dict[str, dict[str, object]] = {}
    for band in bands:
        role = _band_role_at(band, band_roles)
        for node in band.find_all("svg"):
            markup = render(node)
            entry = by_markup.setdefault(
                markup, {"count": 0, "label": _svg_label(node), "roles": set()}
            )
            entry["count"] = int(entry["count"]) + 1
            roles = entry["roles"]
            assert isinstance(roles, set)
            roles.add(role)

    ordered = sorted(by_markup.items(), key=lambda item: -int(item[1]["count"]))
    out: list[InlineSvg] = []
    budget = _SVG_MARKUP_BUDGET
    for markup, entry in ordered[:_MAX_SVG_KINDS]:
        quote = ""
        if len(markup) <= _SVG_MARKUP_MAX and budget - len(markup) >= 0:
            quote = markup
            budget -= len(markup)
        roles = entry["roles"]
        assert isinstance(roles, set)
        out.append(
            InlineSvg(
                label=str(entry["label"]),
                count=int(entry["count"]),
                byte_size=len(markup),
                markup=quote,
                band_roles=tuple(sorted(roles)),
            )
        )
    return tuple(out)


def _field_name(node: Node) -> str:
    for attribute in ("name", "id", "placeholder", "aria-label"):
        value = node.attr(attribute).strip()
        if value:
            return value[:40]
    return node.tag


def _forms(bands: list[Node], base_url: str) -> tuple[FormSpec, ...]:
    """Every form, with its real field list.

    A rebuilt page whose contact form has four fields where the original had seven does not
    collect the same lead, and the difference is invisible next to a screenshot of the top of the
    page.
    """
    specs: list[FormSpec] = []
    for index, band in enumerate(bands):
        for form in band.find_all("form"):
            fields: list[tuple[str, str, bool]] = []
            for node in form.find_all("input", "textarea", "select"):
                kind = node.attr("type").lower() or ("textarea" if node.tag == "textarea" else node.tag)
                if kind in {"hidden", "submit", "button"}:
                    continue
                fields.append((_field_name(node), kind, "required" in node.attrs))
                if len(fields) >= _MAX_FORM_FIELDS:
                    break
            submit = ""
            for node in form.find_all("button", "input"):
                if node.tag == "button" or node.attr("type").lower() == "submit":
                    submit = node.text_content() or node.attr("value")
                    if submit:
                        break
            specs.append(
                FormSpec(
                    signature=form.signature,
                    action=_absolute(form.attr("action"), base_url),
                    method=form.attr("method").upper(),
                    fields=tuple(fields),
                    submit_label=submit[:60],
                    band_index=index + 1,
                )
            )
            if len(specs) >= _MAX_FORMS:
                return tuple(specs)
    return tuple(specs)


def _logo_placement(document: Document, base_url: str) -> str:
    """Where the client's own mark sits, in prose.

    The one fact the operator always checks first and the token sheet has never carried: DESIGN.md's
    Logo section says what the logo *is*, and said nothing about where it goes. A page with the
    right logo centred in a stacked header instead of sitting left of the nav reads as a different
    company's site.
    """
    header = document.body.find("header")
    scope = header if header is not None else document.body

    def wrapper_says_logo(node: Node) -> bool:
        holder = node.parent
        for _ in range(3):
            if holder is None:
                return False
            label = f"{holder.attr('class')} {holder.attr('id')}".lower()
            if "logo" in label or "brand" in label:
                return True
            holder = holder.parent
        return False

    # A theme's bundled demo mark is never the client's (`design_tokens.is_theme_bundled_asset`).
    images = [
        node for node in scope.descendants()
        if node.tag in {"img", "svg"} and not is_theme_bundled_asset(node.attr("src"))
    ]
    candidate: Node | None = None
    for node in images:
        haystack = f"{node.attr('alt')} {node.attr('src')} {node.attr('class')} {node.attr('id')}".lower()
        if "logo" in haystack or "brand" in haystack or wrapper_says_logo(node):
            candidate = node
            break
    if candidate is None and images:
        candidate = images[0]
    if candidate is None:
        return ""

    link = candidate.closest("a")
    container = candidate.closest("header", "div", "nav", "section") or scope
    siblings = _visible(container.elements)
    position = "the only element"
    if len(siblings) > 1:
        holder = candidate
        while holder.parent is not None and holder.parent is not container:
            holder = holder.parent
        try:
            at = siblings.index(holder)
        except ValueError:
            at = 0
        if at == 0:
            position = "the first element"
        elif at == len(siblings) - 1:
            position = "the last element"
        else:
            position = f"element {at + 1} of {len(siblings)}"

    parts = [f"inside `{container.signature}`, as {position}"]
    if link is not None:
        parts.append(f"wrapped in a link to `{link.attr('href') or '/'}`")
    nav = document.body.find("nav")
    if nav is not None and header is not None and any(node is nav for node in header.descendants()):
        parts.append("with the nav alongside it in the same header")
    if candidate.tag == "svg":
        parts.append("and the mark itself is an inline `<svg>`, not an `<img>`")
    else:
        src = _absolute(candidate.attr("src"), base_url)
        if src:
            parts.append(f"as `<img src=\"{src}\">`")
    return ", ".join(parts) + "."


# ----------------------------------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------------------------------


def extract_page_structure(html: str, base_url: str = "") -> PageStructure:
    """Read `html`'s skeleton. Pure and offline — the HTML has already been fetched.

    Never raises. A page whose DOM yields no bands is `available=False` with a stated reason, for
    the same reason an unreadable palette is: a described structure that was guessed looks exactly
    like a measured one.
    """
    if not html or not html.strip():
        return PageStructure(source_url=base_url, available=False, reason="no HTML was supplied.")

    try:
        document = parse_html(html)
    except Exception as exc:  # noqa: BLE001 — an unparseable page must not fail the capture
        logger.warning("Page structure: %r could not be parsed (%s)", base_url, exc)
        return PageStructure(source_url=base_url, available=False, reason=f"the HTML could not be parsed: {exc}")

    notes: list[str] = []
    nodes = _band_nodes(document.body)
    if not nodes:
        return PageStructure(
            source_url=base_url,
            available=False,
            reason=(
                "the page has no readable section structure — its body is likely built by "
                "JavaScript after load, so the served HTML is an empty shell."
            ),
        )

    if len(nodes) > _MAX_BANDS:
        notes.append(f"The page has {len(nodes)} top-level bands; the first {_MAX_BANDS} are described.")
        nodes = nodes[:_MAX_BANDS]

    total = len(nodes)
    roles = [_band_role(node, index, total) for index, node in enumerate(nodes)]
    band_roles = list(zip(nodes, roles))

    bands: list[Band] = []
    for index, (node, role) in enumerate(band_roles):
        heading, level = _first_heading(node)
        bands.append(
            Band(
                index=index + 1,
                role=role,
                signature=node.signature,
                heading=heading,
                heading_level=level,
                inventory=_band_inventory(node),
                repeat=_repeat_group(node),
            )
        )

    headings: list[Heading] = []
    for index, node in enumerate(nodes):
        for candidate in node.descendants():
            if candidate.tag in _HEADING_TAGS:
                text = candidate.text_content()
                if text:
                    headings.append(
                        Heading(level=int(candidate.tag[1]), text=text[:_HEADING_CHARS], band_index=index + 1)
                    )
    if len(headings) > _MAX_HEADINGS:
        notes.append(f"The page has {len(headings)} headings; the first {_MAX_HEADINGS} are listed.")
        headings = headings[:_MAX_HEADINGS]

    host = urlparse(base_url).netloc if base_url else ""
    structure = PageStructure(
        source_url=base_url,
        available=True,
        bands=tuple(bands),
        headings=tuple(headings),
        navs=_nav_groups(document, nodes),
        contacts=_contacts(document, band_roles),
        logo_strips=_logo_strips(nodes, base_url),
        inline_svgs=_inline_svgs(nodes, band_roles),
        forms=_forms(nodes, base_url),
        logo_placement=_logo_placement(document, base_url),
        notes=notes,
    )
    logger.info(
        "Page structure %s: %d bands, %d headings, %d navs, %d contacts, %d logo strips, %d svg kinds",
        host or base_url or "(fragment)",
        len(structure.bands),
        len(structure.headings),
        len(structure.navs),
        len(structure.contacts),
        len(structure.logo_strips),
        len(structure.inline_svgs),
    )
    return structure


# ----------------------------------------------------------------------------------------------
# Rendering
#
# The document half, kept here beside the extraction for the reason `design_tokens.py` keeps
# `tokens_to_markdown` beside its parse: the two drift the moment they live apart, and a field
# added to `Band` that nothing renders is a field nobody knows is missing.
#
# `design_md.py` owns where this lands in DESIGN.md; this owns what it says.
# ----------------------------------------------------------------------------------------------

#: The section's own heading. Public because `generation.py` decides whether to append the
#: structure clause to its binding directive by looking for this string in the sheet it is about to
#: send. Deriving the flag from the document rather than from a separate boolean is deliberate: the
#: directive can then never name a section the document does not have, which is the invariant
#: `test_every_section_the_directive_names_is_present` exists to protect.
STRUCTURE_HEADING = "## Page structure — reproduce this sequence exactly"

_STRUCTURE_INTRO = (
    "Read off the live DOM at <{url}>. This is the page's real skeleton, in document order — not a "
    "summary of it and not a suggestion. The generated page must carry **the same bands in the same "
    "sequence**. Do not add a section the page does not have, do not drop one it does, and do not "
    "reorder them because a different order reads better.\n\n"
    "What changes is the **words**: the copy for every heading and paragraph comes from the "
    "generated content. What does not change is the structure, the logo and its placement, the nav "
    "labels, the client logos, the contact details, and the icons."
)


#: The same section, re-headed for a design-language stage (`generation.DESIGN_LANGUAGE_STAGES`).
#: There the bands are the site's component vocabulary rather than a sequence to clone, and a sheet
#: still saying "reproduce this sequence exactly" would argue with the directive bound to it.
COMPONENT_VOCABULARY_HEADING = "## Page structure — the reference's components (design language)"

_COMPONENT_INTRO = (
    "Read off the live DOM at <{url}>. These are the section types this site is built from, in the "
    "order the reference page happens to use them, and how each one is laid out. For this page they "
    "are a **component vocabulary, not a sequence to copy**: order the body sections by the new "
    "content, build each from the closest component listed here, repeat a component when the content "
    "has more to say, and leave one out when it has nothing for it.\n\n"
    "What does not change: the header and footer, the logo and its placement, the nav labels, the "
    "client logos, the contact details, and the icons — those are reproduced as listed."
)

_INTRO_PATTERN = re.compile(
    re.escape(_STRUCTURE_INTRO).replace(re.escape("{url}"), "(?P<url>[^>]*)")
)


def as_component_vocabulary(sheet: str) -> str:
    """Re-frame a DESIGN.md's `## Page structure` section for a design-language stage.

    The stored sheet is shared by every replica stage on the run (it is captured once), so the
    re-framing happens on the way into the prompt rather than at capture. Only the heading and its
    intro paragraph change; every measured row stays exactly as captured. A sheet without the
    section is returned unchanged.
    """
    if STRUCTURE_HEADING not in sheet:
        return sheet
    sheet = _INTRO_PATTERN.sub(lambda m: _COMPONENT_INTRO.format(url=m.group("url")), sheet, count=1)
    sheet = re.sub(r"^### The (\d+) bands, in order$", r"### The \1 bands on the reference page", sheet, flags=re.M)
    return sheet.replace(STRUCTURE_HEADING, COMPONENT_VOCABULARY_HEADING, 1)


def _escape_cell(value: str) -> str:
    """A table cell that cannot break the table. A pipe in a heading ("Fast | Cheap | Good") ends
    the cell early and shifts every column after it."""
    return value.replace("|", "\\|").replace("\n", " ")


def structure_markdown(structure: PageStructure) -> list[str]:
    """The `## Page structure` section, as lines. Empty when there is nothing measured to say."""
    if not structure.available:
        if structure.reason:
            return [
                STRUCTURE_HEADING,
                "",
                f"**NOT AVAILABLE** — {structure.reason}",
                "",
                "Build the section sequence from the reference screenshots and the generated "
                "content's own outline instead, and say at the top of the output that the source "
                "page's structure could not be read.",
                "",
            ]
        return []

    out = [STRUCTURE_HEADING, "", _STRUCTURE_INTRO.format(url=structure.source_url), ""]

    out += [
        f"### The {structure.band_count} bands, in order",
        "",
        "| # | Section | Element | Heading | Contains |",
        "|---|---|---|---|---|",
    ]
    for band in structure.bands:
        heading = f'"{_escape_cell(band.heading)}" (h{band.heading_level})' if band.heading else "—"
        contains = ", ".join(f"{count} {label}" for label, count in band.inventory) or "—"
        if band.repeat_note:
            contains += f" — laid out as {band.repeat_note}"
        out.append(
            f"| {band.index} | {_escape_cell(band.role)} | `{band.signature}` | {heading} "
            f"| {_escape_cell(contains)} |"
        )
    out.append("")

    if structure.logo_placement:
        out += [
            "### Logo placement",
            "",
            f"The client's mark sits {structure.logo_placement} Put it in the same place. The mark "
            "itself is in the Logo section of this document — use that, not a redrawn one.",
            "",
        ]

    if structure.navs:
        out += ["### Navigation — reproduce these labels verbatim", ""]
        for nav in structure.navs:
            labels = " | ".join(label for label, _ in nav.items)
            out.append(f"- **{nav.where}** (`{nav.signature}`): {_escape_cell(labels)}")
        out += [
            "",
            "These are the client's real menu labels and they are not placeholders. A nav with the "
            "right styling and invented labels is worse than no nav — nobody proofreads it.",
            "",
        ]

    if structure.contacts:
        out += [
            "### Contact details — carry these over exactly",
            "",
            "| What | Value | Link | Appears in |",
            "|---|---|---|---|",
        ]
        for contact in structure.contacts:
            href = f"`{contact.href}`" if contact.href else "not linked"
            where = ", ".join(contact.where) or "—"
            out.append(
                f"| {contact.kind} | {_escape_cell(contact.value)} | {href} | {_escape_cell(where)} |"
            )
        out += [
            "",
            "Never invent, mask or placeholder a phone number or an email address. Reproduce the "
            "digits and the `tel:`/`mailto:` href exactly as given, in every band listed above.",
            "",
        ]

    if structure.logo_strips:
        out += [
            "### Client / partner logos — reference these files, do not redraw them",
            "",
        ]
        for strip in structure.logo_strips:
            out.append(f"Band {strip.band_index} carries {len(strip.images)} marks:")
            out.append("")
            for src, alt in strip.images:
                out.append(f'- `<img src="{src}" alt="{_escape_cell(alt) or "client logo"}">`')
            out.append("")
        out += [
            "These are other companies' trademarks and they cannot be approximated. Use the URLs "
            "above verbatim, keep the `alt` text, and never substitute a placeholder rectangle or a "
            "generic icon — a strip of grey boxes loses the whole point of the section.",
            "",
        ]

    if structure.inline_svgs:
        quoted = [svg for svg in structure.inline_svgs if svg.markup]
        out += [
            "### Inline SVG icons",
            "",
            f"The page draws {sum(svg.count for svg in structure.inline_svgs)} inline `<svg>` "
            f"element(s) in {len(structure.inline_svgs)} distinct shape(s). Reproduce them as inline "
            "SVG. Do NOT substitute an emoji, and do NOT use a Font Awesome / Bootstrap Icons class "
            "— there is no icon-font stylesheet on the generated page, so a class renders as nothing "
            "at all.",
            "",
        ]
        for svg in structure.inline_svgs:
            where = ", ".join(svg.band_roles)
            out.append(f"- **{_escape_cell(svg.label)}** — {svg.count}x, {svg.byte_size:,} bytes ({where})")
        out.append("")
        if quoted:
            out += ["The small ones, verbatim — paste these:", ""]
            for svg in quoted:
                out += [f"`{_escape_cell(svg.label)}`:", "", "```html", svg.markup, "```", ""]
        withheld = [svg for svg in structure.inline_svgs if not svg.markup]
        if withheld:
            out += [
                "The rest are too large to quote here ("
                + ", ".join(f"{_escape_cell(svg.label)} at {svg.byte_size:,} bytes" for svg in withheld)
                + "). Draw a simple inline SVG of your own for those and note it in a comment.",
                "",
            ]

    if structure.forms:
        out += ["### Forms", ""]
        for form in structure.forms:
            head = f"- **Band {form.band_index}** (`{form.signature}`)"
            if form.action:
                head += f" posts {form.method or 'GET'} to `{form.action}`"
            out.append(head)
            for name, kind, required in form.fields:
                out.append(f"  - `{_escape_cell(name)}` ({kind}{', required' if required else ''})")
            if form.submit_label:
                out.append(f'  - submit button reads "{_escape_cell(form.submit_label)}"')
        out += [
            "",
            "Keep the same fields. A rebuilt form with fewer fields does not collect the same lead, "
            "and the difference is invisible next to a screenshot.",
            "",
        ]

    if structure.headings:
        out += [
            "### Heading sequence",
            "",
            "Every heading on the page, at its real level, in document order. This is the outline "
            "the new copy has to fill — same levels, same count, same order, new words:",
            "",
        ]
        out += [
            f"{'  ' * (heading.level - 1)}- `h{heading.level}` (band {heading.band_index}) — "
            f"{heading.text}"
            for heading in structure.headings
        ]
        out.append("")

    if structure.notes:
        out += ["### Reading notes", ""] + [f"- {note}" for note in structure.notes] + [""]

    return out
