"""Every link on the reference page, read off its DOM, so a generated page copies hrefs instead of
guessing them.

Why this exists
---------------
`page_structure.py` gives the model the *labels* of the menus and nothing else. Its nav section
lists "Home | Services | About | Contact" with no URL behind any of them, so every header and
footer link in a generated page was a guess — `#`, `/services`, or a plausible path the client's
site does not have. It also skips any anchor with no text, which is exactly the shape of a social
icon (`<a href="https://facebook.com/acme"><svg/></a>`) and of many icon-only CTAs, so those links
were not even known to exist. A generated page with the right look and dead links is not deployable,
and nobody notices until a visitor clicks.

Same answer as the palette and the band walk: measure, then hand the model the measured value.
This walks every `<a href>` once and sorts it into the group a page-builder would put it in:

  * ``header`` — the site header's menu, with each sub-menu item's parent;
  * ``footer`` — the footer's link columns, with each column's heading;
  * ``social`` — profile links to the social platforms, icon-only ones included;
  * ``cta`` — button-styled links outside the header/footer (the page's calls to action);
  * ``contact`` — ``tel:``, ``mailto:``, ``sms:`` and WhatsApp links;
  * ``legal`` — privacy, terms, cookies and the like, wherever they sit;
  * ``body`` — other in-content links, capped, offered for reuse.

Every href is made absolute against the page's URL, because the generated page will not live at the
reference's path and a relative `/contact` would resolve against wherever it is deployed.

It is free — it runs on the HTML `design_tokens.py` already fetched (`DesignTokens.html`), like the
band walk — and it never invents: a link not on the page is not in the manifest.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services.html_dom import Node, parse_html
from app.services.page_structure import _absolute, _band_nodes, _band_role

logger = logging.getLogger(__name__)

#: The section heading in DESIGN.md. Public for the reason `STRUCTURE_HEADING` is: `generation.py`
#: decides whether to bind the model to this section by looking for the heading in the sheet it is
#: about to send, so the directive can never name a section that is not there.
LINKS_HEADING = "## Links — use these hrefs verbatim"

GROUPS: tuple[str, ...] = ("header", "footer", "social", "cta", "contact", "legal", "body")

_GROUP_PREFIX = {
    "header": "H",
    "footer": "F",
    "social": "S",
    "cta": "C",
    "contact": "T",
    "legal": "L",
    "body": "B",
}

_GROUP_TITLE = {
    "header": "Header navigation",
    "footer": "Footer links",
    "social": "Social profiles",
    "cta": "Calls to action",
    "contact": "Contact links",
    "legal": "Legal links",
    "body": "Other in-page links",
}

# Budgets. The header and footer are not capped per item — a truncated mega-menu is exactly the
# failure this module exists to remove — only the whole manifest is, so a sitemap-style footer
# cannot run the prompt fragment to tens of kilobytes.
_MAX_LINKS = 200
_MAX_BODY_LINKS = 40
_MAX_LABEL = 80

_SOCIAL_HOSTS: dict[str, str] = {
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "tiktok.com": "TikTok",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "snapchat.com": "Snapchat",
    "vimeo.com": "Vimeo",
    "behance.net": "Behance",
    "dribbble.com": "Dribbble",
    "medium.com": "Medium",
    "github.com": "GitHub",
    "tumblr.com": "Tumblr",
    "reddit.com": "Reddit",
}

# Paths on a social host that are not somebody's profile: the Meta pixel, share and intent buttons,
# Pinterest's pin creator. Linking one of these from the generated page's footer as "Facebook"
# would send a visitor to a share dialog, or nowhere.
_NOT_A_PROFILE = re.compile(
    r"^/(?:tr\b|tr/|sharer|share|dialog/|plugins/|intent/|home\?status|shareArticle|pin/create|"
    r"sharing/|watch\?)",
    re.IGNORECASE,
)

_CONTACT_SCHEMES = ("tel:", "mailto:", "sms:", "callto:")
_WHATSAPP_HOSTS = ("wa.me", "api.whatsapp.com", "whatsapp.com")

_LEGAL_WORDS = re.compile(
    r"\b(privacy|terms|conditions|cookie|cookies|disclaimer|legal|sitemap|accessibility|"
    r"gdpr|refund policy|imprint|impressum|complaints)\b",
    re.IGNORECASE,
)

_BUTTONISH = ("btn", "button", "cta", "elementor-button", "wp-block-button")

_HEADER_CLASS = re.compile(
    r"^(?:header|masthead|navbar|top-?bar)$|site-?header|main-?header|header-?(?:main|wrap|inner|container)|"
    r"location-header|page-header-nav|global-?header"
)
_FOOTER_CLASS = re.compile(r"footer")
# Footers of a card, a modal or a blog entry are not the site's footer.
_NOT_SITE_FOOTER = re.compile(r"(?:card|modal|entry|post|article|table|panel|dialog|section)[-_]?footer")

_HEADING_LIKE = ("h2", "h3", "h4", "h5", "h6")
_TITLE_CLASS_WORDS = ("title", "heading", "widget-title", "menu-title")


@dataclass(frozen=True)
class Link:
    """One link to carry over. `href` is absolute and is to be reproduced character for character."""

    link_id: str
    group: str
    label: str
    href: str
    #: Header: the parent menu item's label for a sub-menu entry. Footer: the column heading.
    parent: str = ""
    #: Where on the reference page it sits — "header", "footer", or a band role ("hero").
    where: str = ""


@dataclass
class LinkManifest:
    source_url: str
    available: bool
    reason: str | None = None
    links: tuple[Link, ...] = ()
    notes: list[str] = field(default_factory=list)

    def group(self, name: str) -> tuple[Link, ...]:
        return tuple(link for link in self.links if link.group == name)

    @property
    def hrefs(self) -> frozenset[str]:
        return frozenset(link.href for link in self.links)

    @property
    def primary_cta(self) -> Link | None:
        """The page's main call to action: its first button-styled link, else its first contact
        link. What a new CTA on the generated page should point at when the copy names no other."""
        ctas = self.group("cta")
        if ctas:
            return ctas[0]
        contacts = self.group("contact")
        return contacts[0] if contacts else None

    def to_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "available": self.available,
            "reason": self.reason,
            "links": [
                {
                    "id": link.link_id,
                    "group": link.group,
                    "label": link.label,
                    "href": link.href,
                    "parent": link.parent,
                    "where": link.where,
                }
                for link in self.links
            ],
        }

    @classmethod
    def from_dict(cls, value: dict | None) -> "LinkManifest | None":
        if not value:
            return None
        return cls(
            source_url=value.get("source_url") or "",
            available=bool(value.get("available")),
            reason=value.get("reason"),
            links=tuple(
                Link(
                    link_id=row.get("id", ""),
                    group=row.get("group", ""),
                    label=row.get("label", ""),
                    href=row.get("href", ""),
                    parent=row.get("parent", ""),
                    where=row.get("where", ""),
                )
                for row in (value.get("links") or [])
                if row.get("href")
            ),
        )


# ----------------------------------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------------------------------


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def social_platform(href: str) -> str:
    """The platform name when `href` is a social *profile* link, else "".

    Public because the page check (`asset_check`) uses the same rule to decide whether a generated
    page's footer still links the client's socials.
    """
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return ""
    host = _host(href)
    host = re.sub(r"^(?:m|web|mobile|[a-z]{2})\.", "", host)
    platform = _SOCIAL_HOSTS.get(host, "")
    if not platform:
        return ""
    path = parsed.path or "/"
    if path in {"", "/"} or _NOT_A_PROFILE.match(path + ("?" + parsed.query if parsed.query else "")):
        return ""
    return platform


def _is_contact(href: str) -> bool:
    lowered = href.lower()
    return lowered.startswith(_CONTACT_SCHEMES) or _host(href) in _WHATSAPP_HOSTS


def _is_legal(label: str, href: str) -> bool:
    if _LEGAL_WORDS.search(label):
        return True
    path = urlparse(href).path.replace("-", " ").replace("_", " ").replace("/", " ")
    return bool(_LEGAL_WORDS.search(path)) and len(label) <= 40


def _usable_href(raw: str) -> bool:
    href = raw.strip()
    if not href or href.startswith("#"):
        return False
    return not href.lower().startswith(("javascript:", "data:", "about:"))


def _label_of(anchor: Node, href: str) -> str:
    """What the link says, falling back through the accessible names an icon-only link carries.

    `page_structure._nav_groups` skips an anchor whose text is empty; this does not, because an
    icon-only link is still a link the generated page has to carry."""
    text = anchor.text_content()
    if text:
        return text[:_MAX_LABEL]
    for name in ("aria-label", "title", "data-title"):
        value = anchor.attr(name).strip()
        if value:
            return value[:_MAX_LABEL]
    for child in anchor.descendants():
        if child.tag == "img" and child.attr("alt").strip():
            return child.attr("alt").strip()[:_MAX_LABEL]
        if child.tag == "title":
            title = child.text_content()
            if title:
                return title[:_MAX_LABEL]
        if child.is_element and child.attr("aria-label").strip():
            return child.attr("aria-label").strip()[:_MAX_LABEL]
    platform = social_platform(href)
    if platform:
        return platform
    if href.lower().startswith("tel:"):
        return href[4:]
    if href.lower().startswith("mailto:"):
        return href[7:].split("?")[0]
    return "(icon link)"


def _is_buttonish(anchor: Node) -> bool:
    if anchor.attr("role").lower() == "button":
        return True
    for node in (anchor, *list(anchor.ancestors())[:2]):
        if any(word in name.lower() for name in node.classes for word in _BUTTONISH):
            return True
    return False


def _header_parent(anchor: Node, header: Node) -> str:
    """The parent menu item's label for a sub-menu entry, else ""."""
    items: list[Node] = []
    for node in anchor.ancestors():
        if node is header:
            break
        if node.tag == "li":
            items.append(node)
    # items[0] is the anchor's own <li>; items[1], when the menu nests, is the parent item's.
    if len(items) < 2:
        return ""
    parent_li = items[1]
    for child in parent_li.descendants():
        if child.tag == "a":
            return child.text_content()[:_MAX_LABEL] or child.attr("aria-label")[:_MAX_LABEL]
        if child.tag == "ul":
            break
    # A parent item with no link of its own ("Services ▾" as a <span>).
    for child in parent_li.children:
        if child.is_element and child.tag != "ul":
            text = child.text_content()
            if text:
                return text[:_MAX_LABEL]
        elif child.is_text and child.text.strip():
            return child.text.strip()[:_MAX_LABEL]
    return ""


def _is_heading_like(node: Node) -> bool:
    if node.tag in _HEADING_LIKE:
        return True
    if node.tag in {"p", "span", "strong", "div"} and not node.find("a"):
        return any(word in name.lower() for name in node.classes for word in _TITLE_CLASS_WORDS)
    return False


def _footer_column(anchor: Node, footer: Node) -> str:
    """The heading of the footer column the link sits in — "Services", "Company" — else ""."""
    for ancestor in anchor.ancestors():
        for child in ancestor.descendants():
            if child is anchor:
                break
            if _is_heading_like(child) and anchor not in list(child.descendants()):
                text = child.text_content()
                if text and len(text) <= 60:
                    return text
        if ancestor is footer:
            break
    return ""


def _region(anchor: Node, band_roles: list[tuple[Node, str]]) -> str:
    """"header", "footer", or the role of the band the anchor sits in."""
    if anchor.closest("header") is not None:
        return "header"
    if anchor.closest("footer") is not None:
        return "footer"
    # Page builders (Elementor, Divi, Webflow) build the header and footer out of `<div>`s, and a
    # slim header can be too short for the band walk to count as a band at all — so the class names
    # on the anchor's own ancestors are read first.
    for ancestor in anchor.ancestors():
        tokens = [name.lower() for name in ancestor.classes] + [ancestor.element_id.lower()]
        if any(_HEADER_CLASS.search(token) for token in tokens if token):
            return "header"
        if any(_FOOTER_CLASS.search(token) and not _NOT_SITE_FOOTER.search(token) for token in tokens if token):
            return "footer"
    for node, role in band_roles:
        if node is anchor or any(a is node for a in anchor.ancestors()):
            if role == "site header":
                return "header"
            if role == "site footer":
                return "footer"
            # A page-builder header/footer is a <div>, not a <header>; its band role says so only
            # through its class names, which `_band_role` already read.
            hay = f"{node.element_id} {' '.join(node.classes)}".lower()
            if "footer" in hay:
                return "footer"
            if "header" in hay or "masthead" in hay or "navbar" in hay:
                return "header"
            return role
    if anchor.closest("nav") is not None:
        return "header"
    return "page"


def _container_for(anchor: Node, region: str, band_roles: list[tuple[Node, str]]) -> Node | None:
    tag = "header" if region == "header" else "footer"
    found = anchor.closest(tag)
    if found is not None:
        return found
    for node, _role in band_roles:
        if any(a is node for a in anchor.ancestors()):
            return node
    return None


# ----------------------------------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------------------------------


def extract_link_manifest(html: str, base_url: str = "") -> LinkManifest:
    """Every usable link on the page, grouped. Pure and offline; never raises."""
    if not html or not html.strip():
        return LinkManifest(source_url=base_url, available=False, reason="no HTML was supplied.")
    try:
        document = parse_html(html)
    except Exception as exc:  # noqa: BLE001 — an unparseable page must not fail the capture
        return LinkManifest(source_url=base_url, available=False, reason=f"the HTML could not be parsed: {exc}")

    nodes = _band_nodes(document.body)
    band_roles = [(node, _band_role(node, index, len(nodes))) for index, node in enumerate(nodes)]

    rows: list[tuple[str, str, str, str, str]] = []  # (group, label, href, parent, where)
    seen: set[tuple[str, str, str]] = set()
    body_count = 0
    notes: list[str] = []

    for anchor in document.body.find_all("a"):
        raw = anchor.attr("href")
        if not _usable_href(raw):
            continue
        href = _absolute(raw.strip(), base_url)
        if href.startswith("//"):
            href = f"{urlparse(base_url).scheme or 'https'}:{href}"
        label = _label_of(anchor, href)
        where = _region(anchor, band_roles)
        parent = ""

        if _is_contact(href):
            group = "contact"
        elif social_platform(href):
            group = "social"
            label = social_platform(href) if label in {"(icon link)", ""} else label
        elif where != "header" and _is_legal(label, href):
            group = "legal"
        elif where == "header":
            group = "header"
            container = _container_for(anchor, where, band_roles)
            parent = _header_parent(anchor, container) if container is not None else ""
        elif where == "footer":
            group = "footer"
            container = _container_for(anchor, where, band_roles)
            parent = _footer_column(anchor, container) if container is not None else ""
        elif _is_buttonish(anchor):
            group = "cta"
        else:
            group = "body"

        # A header menu is routinely in the DOM twice (desktop and mobile); a social icon appears
        # in the header and the footer. One row per (group, label, href) keeps both facts without
        # the duplicates.
        key = (group, label.lower(), href)
        if key in seen:
            continue
        if group == "social" and any(row[0] == "social" and row[2] == href for row in rows):
            continue
        if group == "body":
            if body_count >= _MAX_BODY_LINKS:
                continue
            body_count += 1
        seen.add(key)
        rows.append((group, label, href, parent, where))
        if len(rows) >= _MAX_LINKS:
            notes.append(f"The page has more than {_MAX_LINKS} links; the first {_MAX_LINKS} are listed.")
            break

    if not rows:
        return LinkManifest(
            source_url=base_url,
            available=False,
            reason="the page has no usable links in its served HTML — it may be built by JavaScript.",
            notes=notes,
        )

    counters: dict[str, int] = {}
    links: list[Link] = []
    for group in GROUPS:
        for row in rows:
            if row[0] != group:
                continue
            counters[group] = counters.get(group, 0) + 1
            links.append(
                Link(
                    link_id=f"{_GROUP_PREFIX[group]}{counters[group]:02d}",
                    group=group,
                    label=row[1],
                    href=row[2],
                    parent=row[3],
                    where=row[4],
                )
            )

    manifest = LinkManifest(source_url=base_url, available=True, links=tuple(links), notes=notes)
    logger.info(
        "Link manifest %s: %s",
        _host(base_url) or "(fragment)",
        ", ".join(f"{group}={len(manifest.group(group))}" for group in GROUPS),
    )
    return manifest


# ----------------------------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------------------------


def _cell(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())


def links_markdown(manifest: LinkManifest | None) -> list[str]:
    """The `## Links` section of the full DESIGN.md, as lines. Empty when nothing was measured."""
    if manifest is None or not manifest.available or not manifest.links:
        return []

    out = [
        LINKS_HEADING,
        "",
        f"Read off the live DOM at <{manifest.source_url}>. These are the client's real links. Every "
        "header, footer, social and contact link in the generated page must use the href given here "
        "**character for character** — never `#`, never a guessed path, never a link that is not in "
        "this table. Labels are reproduced as written. A new call to action points at "
        + (
            f"`{manifest.primary_cta.href}` ({manifest.primary_cta.link_id})"
            if manifest.primary_cta is not None
            else "a contact link below"
        )
        + " unless the copy names another link listed here.",
        "",
    ]
    for group in GROUPS:
        links = manifest.group(group)
        if not links:
            continue
        out += [f"### {_GROUP_TITLE[group]}", ""]
        has_parent = any(link.parent for link in links)
        if has_parent:
            parent_title = "Menu parent" if group == "header" else "Column"
            out += [f"| id | {parent_title} | Label | href |", "|---|---|---|---|"]
            for link in links:
                out.append(
                    f"| {link.link_id} | {_cell(link.parent) or '—'} | {_cell(link.label)} | `{link.href}` |"
                )
        else:
            show_where = group in {"cta", "contact", "body"}
            if show_where:
                out += ["| id | Label | href | On the reference page |", "|---|---|---|---|"]
                for link in links:
                    out.append(f"| {link.link_id} | {_cell(link.label)} | `{link.href}` | {_cell(link.where)} |")
            else:
                out += ["| id | Label | href |", "|---|---|---|"]
                for link in links:
                    out.append(f"| {link.link_id} | {_cell(link.label)} | `{link.href}` |")
        out.append("")
    if manifest.group("body"):
        out += [
            "The in-page links are offered for reuse where the new copy refers to the same page; they "
            "are not required.",
            "",
        ]
    for note in manifest.notes:
        out += [f"_{note}_", ""]
    return out
