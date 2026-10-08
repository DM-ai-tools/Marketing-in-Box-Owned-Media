"""Is a generated page ready to deploy? Free, deterministic rules over the HTML a stage produced.

The business check (`asset_check.py`) asks whether a draft is *true* for the client. This asks
whether the page it contains is *publishable*: every link the client's own site has is carried with
its real href, nothing points at `#`, every image has a source and alt text, the page has the
document furniture a browser and a search engine need, and it will not break on a phone.

Every rule here reads the HTML; none calls a model. The link rules compare against the run's link
manifest (`link_manifest.py`) — the hrefs measured off the reference page — so "is this a real
link" is answered by the client's site rather than by a guess. With no manifest on record the link
rules do not run, and the report says so instead of passing them.

Findings are `PageIssue`s, converted to `asset_check.Finding` by the caller, so this module does not
import the checker (which imports it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.services.html_dom import Node, parse_html, render
from app.services.link_manifest import LinkManifest, social_platform

CHECK = "page_readiness"

_FENCE = re.compile(r"```html\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_MAX_PER_RULE = 6
_QUOTE_CHARS = 200

#: A missing link in one of these groups is a must-fix: a header or social icon pointing nowhere is
#: the first thing a visitor clicks. Footer columns and legal links are worth fixing, not blocking.
_MUST_KEEP_GROUPS = ("header", "social", "contact")
_SHOULD_KEEP_GROUPS = ("footer", "legal")

_PLACEHOLDER_SRC = re.compile(
    r"^(?:#|about:blank|data:,?$)|placeholder|via\.placeholder|placehold\.|dummyimage|"
    r"example\.(?:com|org)|your-?image|image\.(?:jpg|png)$",
    re.IGNORECASE,
)
_STOCK_SRC = re.compile(r"unsplash\.com|pexels\.com|pixabay\.com|shutterstock\.com|istockphoto\.com|gettyimages\.", re.I)
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RULE_BODY = re.compile(r"([^{}]+)\{([^{}]*)\}")
_FIXED_WIDTH = re.compile(r"(?<![\w-])width\s*:\s*(\d{3,4})px", re.IGNORECASE)
_NEUTRAL_EXTREMES = {"#ffffff", "#000000"}

#: Penalty per finding when scoring. A deterministic score, so it moves only when the page does.
_PENALTY = {"error": 15, "warn": 5, "info": 1}


@dataclass(frozen=True)
class PageIssue:
    severity: str  # "error" | "warn" | "info"
    quote: str
    why: str
    fix: str


def extract_page_html(raw: str) -> str:
    """The page inside a draft: the largest ```html fence, else the draft itself when it is a whole
    HTML document, else ""."""
    fences = _FENCE.findall(raw or "")
    if fences:
        return max(fences, key=len)
    if re.search(r"<(?:html|body)\b", raw or "", re.IGNORECASE):
        return raw
    return ""


def _norm_url(href: str) -> str:
    """Comparison form: scheme-less, `www.`-less, no fragment, no trailing slash, case-folded host."""
    parsed = urlparse(href.strip())
    if parsed.scheme in {"tel", "mailto", "sms", "callto"}:
        return f"{parsed.scheme}:{re.sub(r'[^0-9a-z@.+_-]', '', parsed.path.lower())}"
    host = parsed.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = parsed.path.rstrip("/") or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}"


def _quote(node: Node) -> str:
    text = " ".join(render(node).split())
    return text if len(text) <= _QUOTE_CHARS else text[: _QUOTE_CHARS - 1] + "…"


def _palette(design_md: str) -> set[str]:
    """Every hex in the DESIGN.md (front matter and tables), lower-cased and expanded to 6 digits."""
    return {_expand(h) for h in _HEX.findall(design_md or "")}


def _expand(hex_value: str) -> str:
    value = hex_value.lower()
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value


def check_page(raw: str, manifest: LinkManifest | None, design_md: str = "") -> tuple[list[PageIssue], list[str]]:
    """The page's issues, and which facts they were checked against. `([], [])` when the draft holds
    no HTML page — a pillar page draft always should, and the caller reports that."""
    html = extract_page_html(raw)
    if not html.strip():
        return [], []
    try:
        document = parse_html(html)
    except Exception:  # noqa: BLE001 — an unparseable page is itself the finding
        return [PageIssue("error", "", "The page's HTML could not be parsed.", "Regenerate the page.")], []

    issues: list[PageIssue] = []
    used: list[str] = []
    counts: dict[str, int] = {}

    def add(key: str, severity: str, quote: str, why: str, fix: str) -> None:
        if counts.get(key, 0) >= _MAX_PER_RULE:
            return
        counts[key] = counts.get(key, 0) + 1
        issues.append(PageIssue(severity, quote, why, fix))

    nodes: list[Node] = list(document.root.walk())

    anchors = [n for n in nodes if n.tag == "a"]
    ids = {n.element_id for n in nodes if n.is_element and n.element_id}

    # -- Links ---------------------------------------------------------------------------------
    for anchor in anchors:
        href = anchor.attr("href").strip()
        if "href" not in anchor.attrs or not href or href == "#" or href.lower().startswith("javascript:"):
            # A `#` that opens a modal through `onclick` does something with scripts on, and nothing
            # without them or when the page is pasted into a CMS that strips handlers.
            scripted = bool(anchor.attr("onclick").strip())
            add(
                "dead-link",
                "warn" if scripted else "error",
                _quote(anchor),
                f'A link that goes nowhere (href="{href}")'
                + (" unless its script runs." if scripted else "."),
                "Point it at a real link from DESIGN.md's Links section (a contact page or the "
                "primary call to action), or make it a <button>.",
            )
        elif href.startswith("#") and href[1:] not in ids:
            add(
                "dead-anchor",
                "warn",
                _quote(anchor),
                f"Jumps to {href}, but no element on the page has that id.",
                "Give the target section that id, or fix the link.",
            )

    if manifest is not None and manifest.available and manifest.links:
        used.append("the reference page's links")
        page_hrefs = {_norm_url(a.attr("href")) for a in anchors if a.attr("href").strip()}
        for group in (*_MUST_KEEP_GROUPS, *_SHOULD_KEEP_GROUPS):
            severity = "error" if group in _MUST_KEEP_GROUPS else "warn"
            for link in manifest.group(group):
                if _norm_url(link.href) in page_hrefs:
                    continue
                add(
                    f"missing-{group}",
                    severity,
                    "",
                    f'The {group} link "{link.label}" ({link.href}) from the client\'s site is missing.',
                    f'Add it with href="{link.href}", where the reference page has it.',
                )
        known = {_norm_url(link.href) for link in manifest.links}
        client_host = urlparse(manifest.source_url).netloc.lower().removeprefix("www.")
        for anchor in anchors:
            href = anchor.attr("href").strip()
            parsed = urlparse(href)
            host = parsed.netloc.lower().removeprefix("www.")
            if not client_host or host != client_host or _norm_url(href) in known:
                continue
            if parsed.path.rstrip("/") in {"", "/"}:
                continue
            add(
                "unlisted-link",
                "warn",
                _quote(anchor),
                f"Links to {href}, a page on the client's site that is not among the reference page's links.",
                "Use a listed link, or confirm this page exists before publishing.",
            )
        listed_social = {_norm_url(link.href) for link in manifest.group("social")}
        for anchor in anchors:
            href = anchor.attr("href").strip()
            if social_platform(href) and _norm_url(href) not in listed_social:
                # A guessed profile is wrong either way; it is a must-fix when the client's real
                # ones are on record to use instead.
                add(
                    "unlisted-social",
                    "error" if listed_social else "warn",
                    _quote(anchor),
                    f"Links a social profile ({href}) that is not the client's"
                    + ("." if listed_social else " — the reference page links no social profiles."),
                    "Use the client's own profile from the Links section."
                    if listed_social
                    else "Remove it, or confirm the client owns this profile.",
                )

    # -- Images --------------------------------------------------------------------------------
    for img in (n for n in nodes if n.tag == "img"):
        src = (img.attr("src") or img.attr("data-src")).strip()
        if not src or _PLACEHOLDER_SRC.search(src):
            prompt = img.attr("data-image-prompt")
            add(
                "img-placeholder",
                "warn" if prompt else "error",
                _quote(img),
                "An image placeholder with no real image behind it."
                if prompt
                else f'An image with no usable source (src="{src}").',
                "Supply the image, or remove the placeholder before publishing.",
            )
        elif _STOCK_SRC.search(src):
            add(
                "img-stock",
                "warn",
                _quote(img),
                "Uses a stock-photo URL rather than the client's own or a generated image.",
                "Replace it with a supplied image URL.",
            )
        if "alt" not in img.attrs:
            add("img-alt", "warn", _quote(img), "An image with no alt attribute.", 'Add descriptive alt text (or alt="" if decorative).')
        if not (img.attr("width") and img.attr("height")) and "aspect-ratio" not in img.attr("style"):
            add(
                "img-size",
                "info",
                _quote(img),
                "An image with no width/height, so the page shifts as it loads.",
                "Add width and height attributes.",
            )

    # -- Document ------------------------------------------------------------------------------
    def first(tag: str) -> Node | None:
        return next((n for n in nodes if n.tag == tag), None)

    title = first("title")
    if title is None or not title.text_content():
        add("doc-title", "error", "", "The page has no <title>.", "Add a title of 60 characters or fewer.")
    metas = [n for n in nodes if n.tag == "meta"]
    if not any(m.attr("name").lower() == "viewport" for m in metas):
        add("doc-viewport", "error", "", "No viewport meta tag, so phones render it as a shrunken desktop page.",
            'Add <meta name="viewport" content="width=device-width, initial-scale=1">.')
    if not any(m.attr("name").lower() == "description" and m.attr("content").strip() for m in metas):
        add("doc-description", "warn", "", "No meta description.", "Add one of 155 characters or fewer.")
    html_el = document.html_element
    if html_el is not None and not html_el.attr("lang").strip():
        add("doc-lang", "warn", "", "The <html> element has no lang attribute.", 'Add lang="en" (or the page\'s language).')
    h1s = [n for n in nodes if n.tag == "h1"]
    if len(h1s) != 1:
        add(
            "doc-h1",
            "error" if not h1s else "warn",
            _quote(h1s[1]) if len(h1s) > 1 else "",
            f"The page has {len(h1s)} h1 headings; it needs exactly one.",
            "Keep one h1 (the hero headline) and make the others h2.",
        )
    if first("main") is None:
        add("doc-main", "warn", "", "No <main> landmark.", "Wrap the page body in <main>.")

    # -- Responsive ----------------------------------------------------------------------------
    css = "\n".join(_raw_text(n) for n in nodes if n.tag == "style")
    if css.strip():
        if "@media" not in css and "clamp(" not in css:
            add("css-responsive", "warn", "", "The CSS has no media queries and no clamp(), so nothing adapts to a phone.",
                "Add mobile-first media queries at the DESIGN.md breakpoints.")
        for selector, declarations in _RULE_BODY.findall(css):
            if selector.strip().startswith("@"):
                continue
            for match in _FIXED_WIDTH.finditer(declarations):
                if int(match.group(1)) > 480 and "max-width" not in declarations:
                    add(
                        "css-fixed-width",
                        "warn",
                        f"{' '.join(selector.split())[:80]} {{ {match.group(0)} }}",
                        f"A fixed {match.group(1)}px width will overflow a phone screen.",
                        "Use max-width (or a percentage) instead.",
                    )

        palette = _palette(design_md)
        if palette:
            used.append("the DESIGN.md palette")
            off_palette = sorted(
                {_expand(h) for h in _HEX.findall(css)} - palette - _NEUTRAL_EXTREMES
            )
            if off_palette:
                add(
                    "css-palette",
                    "info",
                    ", ".join(off_palette[:6]),
                    f"{len(off_palette)} colour(s) in the CSS are not in DESIGN.md's palette.",
                    "Use palette colours, or note in a comment which palette colour each is derived from.",
                )

    # -- Navigation ----------------------------------------------------------------------------
    sub_menus = [
        n for n in nodes
        if n.tag in {"ul", "ol"} and n.closest("li") is not None and n.closest("nav", "header") is not None
    ]
    if sub_menus and css.strip():
        for quote, why, fix in _sub_menu_always_open(css, sub_menus):
            add("nav-submenu-open", "error", quote, why, fix)
        if ":focus-within" in css:
            for item in (n for n in nodes if n.tag == "li" and "tabindex" in n.attrs and n.closest("nav") is not None):
                add(
                    "nav-sticky-focus",
                    "warn",
                    _quote(item)[:80],
                    "A menu item made focusable with tabindex keeps its sub-menu open after a click, "
                    "because :focus-within holds until focus moves somewhere else.",
                    "Remove tabindex from the <li>; the link inside is already focusable. A parent "
                    "with no page of its own should be a <button aria-expanded>.",
                )

    return issues, used


#: A bare list at the end of a selector reached by the descendant combinator. Run on a selector with
#: `>`/`+`/`~` already squeezed (`nav>ul`), so a child-scoped rule — the correct one — never matches.
_LIST_TAIL = re.compile(r"(?:^|\s)(ul|ol)$", re.IGNORECASE)
_DISPLAY = re.compile(r"(?<![\w-])display\s*:\s*([\w-]+)", re.IGNORECASE)
_OPEN_STATE = re.compile(r":hover|:focus|\.open\b|\.is-open|\.active|\[aria-expanded", re.IGNORECASE)


def _specificity(selector: str) -> tuple[int, int, int]:
    """CSS specificity, close enough to rank two rules: (ids, classes/attrs/pseudo-classes, types)."""
    s = re.sub(r"::[\w-]+", "", selector)
    ids = len(re.findall(r"#[\w-]+", s))
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|(?<!:):[\w-]+", s))
    types = len(re.findall(r"(?:^|[\s>+~(])([a-zA-Z][\w-]*)", s))
    return ids, classes, types


def _sub_menu_always_open(css: str, sub_menus: list[Node]) -> list[tuple[str, str, str]]:
    """A rule like `nav ul{display:flex}` that also matches the nested sub-menu lists and outranks
    the rule hiding them, so every sub-menu is permanently open.

    The model writes this one reliably: the top-level menu needs `display:flex`, the descendant
    selector is the obvious way to say it, and `.sub-menu{display:none}` reads as if it wins. It
    does not — (0,1,2) beats (0,1,0) — and the page renders with every dropdown showing.
    """
    menu_classes = {cls for menu in sub_menus for cls in menu.classes}
    show_all: list[tuple[int, str, tuple[int, int, int]]] = []
    hide: list[tuple[int, tuple[int, int, int]]] = []
    for order, (selectors, declarations) in enumerate(_RULE_BODY.findall(css)):
        displays = [d.lower() for d in _DISPLAY.findall(declarations)]
        if not displays:
            continue
        for selector in (re.sub(r"\s*([>+~])\s*", r"\1", " ".join(s.split())) for s in selectors.split(",")):
            if not selector or selector.startswith("@"):
                continue
            if displays[-1] != "none" and _LIST_TAIL.search(selector) and not _OPEN_STATE.search(selector):
                show_all.append((order, selector, _specificity(selector)))
            elif displays[-1] == "none" and not _OPEN_STATE.search(selector):
                last = re.split(r"[\s>+~]+", selector)[-1]
                if any(f".{cls}" in last for cls in menu_classes):
                    hide.append((order, _specificity(selector)))
    if not hide:
        return []
    found = []
    for order, selector, spec in show_all:
        if all(spec > h_spec or (spec == h_spec and order > h_order) for h_order, h_spec in hide):
            head, _, tail = selector.rpartition(" ")
            scoped = f"{head} > {tail}" if head else f"nav > {tail}"
            found.append((
                f"{selector} {{ display: … }}",
                f'"{selector}" also matches the nested sub-menu lists and outranks the rule that '
                "hides them, so every sub-menu shows permanently instead of opening on hover.",
                f'Scope it to the top-level list ("{scoped}"), or hide the sub-menu with a selector '
                "at least as specific.",
            ))
    return found


def _raw_text(node: Node) -> str:
    """A `<style>` element's text, raw nodes included — `text_content` deliberately skips them."""
    return "".join(child.text for child in node.walk() if child.is_text)


def score(issues: list[PageIssue]) -> int:
    return max(0, 100 - sum(_PENALTY.get(issue.severity, 0) for issue in issues))


def verdict(issues: list[PageIssue]) -> str:
    errors = sum(1 for issue in issues if issue.severity == "error")
    warns = sum(1 for issue in issues if issue.severity == "warn")
    if errors:
        return f"Not ready to deploy: {errors} must-fix issue(s)" + (f" and {warns} to review." if warns else ".")
    if warns:
        return f"Deployable after reviewing {warns} issue(s)."
    return "Ready to deploy: links, images, document tags and responsive rules all check out."
