"""Which services (Phase 1) or sub-services (Phase 2) a client's site offers, read off its own links.

Why this exists
----------------
The Pillar Page stage builds one combined page for the services the operator picks. Before this, the
only way to say which services the page was for was to type them, and the only copy it had was the
CRO rewrite, which is about one service. So a page meant to cover "Commercial Loans, Asset Finance
and Property Development Finance" either covered one of them or had the other two invented.

Measure, then judge, the same order as `asset_check.py` and `design_tokens.py`:

1. **Measure, free.** One direct fetch of the reference page (or the main landing page). Every
   same-site link with readable text becomes a candidate, marked with whether it sits in the
   page's navigation, where a business almost always lists what it sells.
2. **Judge, one small call.** Haiku sorts the candidates into services, or sub-services under a
   parent. It may only pick from the candidates: an item whose URL is not one of them is dropped,
   so a service the site does not link to cannot appear on the card.

The selected services' own pages are then read (`read_selected_pages`) when the stage generates,
so the sections the CRO copy lacks are written from what that page actually says.

Credits
-------
The scan is one free fetch plus one Haiku call. Reading the selected pages uses the free direct
reader only, never Context.dev or the paid fallbacks: that read loops over a list of URLs, which is
exactly what CLAUDE.md's "credits are money" rule forbids for a paid reader. A page the free reader
cannot read is reported as such, and that service's section falls back to the CRO copy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

from app.services import scraper
from app.services.claude_client import get_client
from app.services.generation import HAIKU, OnUsage
from app.services.html_dom import Node, parse_html
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

SCAN_MODEL = HAIKU
_SCAN_MAX_TOKENS = 3000
#: Candidate links handed to the model. A mega-menu site lists a few hundred; the nav ones are kept
#: first, so a cap cuts footer and body links before it cuts the menu.
_MAX_CANDIDATES = 220
_MAX_SERVICES = 40
#: Selected pages read at generation time, and the characters kept from each. Six pages of 5,000
#: characters is about 7,500 input tokens: a real cost on every run, bounded.
MAX_SOURCE_PAGES = 6
_SOURCE_CHARS = 5000

#: The sentinel written into the reference field when the operator says the page does not exist
#: and names the main landing page instead. The frontend writes it (`NO_REFERENCE_MARKER` in
#: `pipeline/pipelineData.ts`); the router reads it to take the brand design but not the markup.
NO_REFERENCE_MARKER = "NO REFERENCE PAGE"

#: The field the selection is filed under, in both phases.
SERVICES_FIELD_ID = "services_covered"

_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "sms:", "whatsapp:")
_SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".doc", ".docx", ".xls", ".xlsx", ".mp4")
_NAV_CLASS = re.compile(r"(^|[-_ ])(nav|menu|navbar|header|megamenu|mega-menu|dropdown|submenu|sub-menu)([-_ ]|$)", re.IGNORECASE)
_URL_IN_TEXT = re.compile(r"https?://[^\s<>()\"'`]+", re.IGNORECASE)


class ServiceScanError(Exception):
    """The scan could not run: the page could not be read, or the model could not be asked."""


@dataclass(frozen=True)
class LinkCandidate:
    text: str
    url: str
    in_nav: bool
    #: The menu item this link is nested under ("Business & Commercial" for "Truck Loan"), read off
    #: the menu's own `<li>` nesting. Without it the model sees a flat list and has to guess which
    #: entries are services and which are the headings over them.
    under: str = ""


@dataclass(frozen=True)
class ScannedService:
    name: str
    url: str
    #: The broader service this one sits under, when the site groups it ("Asset Finance" under
    #: "Business Loans"). Empty for a top-level service.
    parent: str = ""


@dataclass
class ServiceScan:
    source_url: str
    services: list[ScannedService] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------------------
# Measure: the links
# --------------------------------------------------------------------------------------


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _in_nav(node: Node) -> bool:
    current = node.parent
    while current is not None:
        if current.tag in {"nav", "header"}:
            return True
        if current.attr("role") == "navigation":
            return True
        if any(_NAV_CLASS.search(c) for c in current.classes):
            return True
        current = current.parent
    return False


def _clean_label(text: str) -> str:
    return " ".join(text.split()).strip(" -–—|›»>")


def _menu_parent(node: Node) -> str:
    """The label of the menu item `node` is nested under: the nearest `<li>` above this link's own
    `<li>` whose first link is a different one. Empty for a top-level item or a link not in a list."""
    own_li = node.parent
    while own_li is not None and own_li.tag != "li":
        own_li = own_li.parent
    current = own_li.parent if own_li is not None else None
    while current is not None:
        if current.tag == "li":
            heading = next((d for d in current.descendants() if d.tag == "a"), None)
            if heading is not None and heading is not node:
                return _clean_label(heading.text_content())
            # A heading with no link of its own ("Services" as a plain label).
            label = next((c for c in current.children if c.is_element and c.tag in {"span", "button", "strong"}), None)
            if label is not None:
                return _clean_label(label.text_content())
        current = current.parent
    return ""


def extract_link_candidates(html: str, base_url: str) -> list[LinkCandidate]:
    """Every same-site link with readable text, once per URL, navigation links first."""
    site = _host(base_url)
    seen: dict[str, LinkCandidate] = {}
    for node in parse_html(html).root.walk():
        if node.tag != "a":
            continue
        href = (node.attr("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
            continue
        url, _fragment = urldefrag(urljoin(base_url, href))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or _host(url) != site:
            continue
        if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        label = _clean_label(node.text_content())
        if not (2 <= len(label) <= 80):
            continue
        url = url.rstrip("/") or url
        nav = _in_nav(node)
        existing = seen.get(url)
        # The nav's wording wins over a body link's ("Learn more"), and a link is in the nav if any
        # copy of it is.
        if existing is None or (nav and not existing.in_nav):
            seen[url] = LinkCandidate(
                text=label, url=url, in_nav=nav or bool(existing and existing.in_nav), under=_menu_parent(node) if nav else ""
            )
    candidates = sorted(seen.values(), key=lambda c: not c.in_nav)
    return candidates[:_MAX_CANDIDATES]


# --------------------------------------------------------------------------------------
# Judge: which of them are services
# --------------------------------------------------------------------------------------


def _scan_prompt(candidates: list[LinkCandidate], phase: str, focus: str) -> str:
    listing = "\n".join(
        f"{i}. [{'nav' if c.in_nav else 'page'}] {c.text}{f' (in the menu under: {c.under})' if c.under else ''} — {c.url}"
        for i, c in enumerate(candidates, 1)
    )
    if phase == "phase2":
        task = (
            "List the SUB-SERVICES this business offers: the specific, individually sold offerings that "
            "sit under a broader service (e.g. \"Asset Finance\" and \"Truck Loans\" under \"Business "
            "Loans\"; \"Google Ads\" and \"Meta Ads\" under \"Paid Media\"). Set `parent` to the broader "
            "service each one sits under, as the site names it."
            + (f" The run is focused on \"{focus}\": list the sub-services under it first." if focus else "")
        )
    else:
        task = (
            "List the SERVICES this business sells: each distinct service or product line a customer "
            "can buy, as the site names it. Where the site groups services under a broader heading, "
            "set `parent` to that heading; otherwise leave it empty."
            + (f" The service most relevant to this run is \"{focus}\"." if focus else "")
        )
    return (
        "Below are the links found on a business's own website, each marked [nav] when it is in the "
        "site's navigation menu.\n\n"
        f"{task}\n\n"
        "Rules:\n"
        "- Pick ONLY from the links below, and copy each `url` exactly as listed. Never invent a URL "
        "or a service the links do not show.\n"
        "- Exclude everything that is not a service: home, about, team, contact, blog posts, news, "
        "careers, FAQs, testimonials, case studies, calculators, legal pages, locations, login.\n"
        "- `name` is the service's name as a customer would read it: tidy the link text, but do not "
        "rename the service.\n"
        "- \"(in the menu under: X)\" is the site's own grouping. Use X as `parent`.\n"
        + (
            "- A menu heading that groups other items is their `parent`, not an item itself.\n"
            if phase == "phase2"
            else "- A menu heading that links to its own page is a top-level service: list it too, with "
            "an empty `parent`, ahead of the items under it.\n"
        )
        +
        f"- At most {_MAX_SERVICES} items, in the order the site's menu lists them.\n\n"
        f"LINKS:\n{listing}\n\n"
        'Reply with ONLY a JSON object, no prose, no code fence: {"services": [{"name": "...", '
        '"url": "...", "parent": "..."}]}'
    )


def _parse_reply(raw: str, candidates: list[LinkCandidate]) -> tuple[list[ScannedService], int]:
    """The model's services, keeping only those whose URL is one of the candidates."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise ServiceScanError("The service scan's reply was not the requested JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ServiceScanError("The service scan's reply was not valid JSON.") from exc
    items = data.get("services") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ServiceScanError("The service scan's reply had no services list.")

    known = {c.url.rstrip("/"): c for c in candidates}
    services: list[ScannedService] = []
    seen: set[str] = set()
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip().rstrip("/")
        name = _clean_label(str(item.get("name") or ""))
        if url not in known or not name:
            dropped += 1
            continue
        if url in seen:
            continue
        seen.add(url)
        services.append(ScannedService(name=name, url=known[url].url, parent=_clean_label(str(item.get("parent") or ""))))
        if len(services) >= _MAX_SERVICES:
            break
    return services, dropped


async def _ask(prompt: str, on_usage: OnUsage | None) -> str:
    started = time.monotonic()
    response = await get_client().messages.create(
        model=SCAN_MODEL,
        max_tokens=_SCAN_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=SCAN_MODEL,
                                    duration_ms=int((time.monotonic() - started) * 1000))
        )
    return next((b.text for b in response.content if b.type == "text"), "")


async def scan_services(url: str, *, phase: str = "phase1", focus: str = "", on_usage: OnUsage | None = None) -> ServiceScan:
    """The services (Phase 1) or sub-services (Phase 2) the site at `url` links to.

    Raises `ServiceScanError` when the page cannot be read at all. A page that reads but lists no
    services returns an empty scan with a note: the operator can still type them.
    """
    try:
        final_url, html = await scraper._fetch_html(scraper.normalize_url(url))
    except scraper.ScrapeError as exc:
        raise ServiceScanError(f"Could not read {url}: {exc}") from exc

    scan = ServiceScan(source_url=final_url)
    candidates = extract_link_candidates(html, final_url)
    if not candidates:
        scan.notes.append(
            "The page has no readable links to this site's own pages. It may build its menu with "
            "JavaScript after loading, so type the services instead."
        )
        return scan

    try:
        raw = await _ask(_scan_prompt(candidates, phase, focus.strip()), on_usage)
    except Exception as exc:  # noqa: BLE001 — any model failure is the same outcome for the operator
        raise ServiceScanError(f"The service scan could not run: {exc}") from exc

    services, dropped = _parse_reply(raw, candidates)
    scan.services = services
    if dropped:
        logger.info("Service scan for %s dropped %d item(s) whose URL the site does not link to", url, dropped)
    if not services:
        kind = "sub-services" if phase == "phase2" else "services"
        scan.notes.append(f"No {kind} were found among this page's links, so type them instead.")
    return scan


# --------------------------------------------------------------------------------------
# The selection, as the stage receives it
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectedService:
    name: str
    url: str | None


def parse_selection(answer: str) -> list[SelectedService]:
    """The services named in a `services_covered` answer.

    Reads the shape the selection card writes ("- Commercial Loans — https://…"), one per line, and
    also a hand-typed list ("Asset Finance, Truck Loans"), since the field can be edited as text.
    """
    selected: list[SelectedService] = []
    for line in (answer or "").splitlines():
        line = line.strip().lstrip("-*•").strip()
        if not line or line.upper().startswith(("SOURCE CONTENT", "###")):
            continue
        if line.lower().startswith("other"):
            line = line.split(":", 1)[1] if ":" in line else ""
            selected.extend(SelectedService(name=part.strip(), url=None) for part in re.split(r"[,;]", line) if part.strip())
            continue
        found = _URL_IN_TEXT.search(line)
        url = found.group(0).rstrip(".,;:") if found else None
        name = _clean_label(_URL_IN_TEXT.sub("", line))
        if name or url:
            selected.append(SelectedService(name=name or url or "", url=url))
    return selected


def anchor_text(answer: str) -> str:
    """The selected services' names, for anchoring keyword suggestions: "Commercial Loans, Asset
    Finance". URLs and the card's own formatting are noise to a keyword search."""
    return ", ".join(s.name for s in parse_selection(answer) if s.name)


async def read_selected_pages(answer: str) -> tuple[str, list[dict[str, object]]]:
    """Each selected service's own page, read for free, as a block for the prompt, plus a report.

    Returns `("", [])` when no selected service has a page. Pages are read concurrently and capped
    at `MAX_SOURCE_PAGES`; one page failing costs that page alone.
    """
    targets = [s for s in parse_selection(answer) if s.url][:MAX_SOURCE_PAGES]
    if not targets:
        return "", []

    async def _one(service: SelectedService):
        try:
            return await scraper.read_direct(service.url or "")
        except scraper.ScrapeError as exc:
            return exc

    results = await asyncio.gather(*(_one(s) for s in targets))
    sections: list[str] = []
    report: list[dict[str, object]] = []
    for service, page in zip(targets, results):
        if isinstance(page, Exception) or page.low_content:
            why = str(page) if isinstance(page, Exception) else "the page came back almost empty"
            report.append({"name": service.name, "url": service.url, "ok": False, "chars": 0, "reason": why})
            continue
        text = page.text[:_SOURCE_CHARS]
        report.append({"name": service.name, "url": service.url, "ok": True, "chars": len(text)})
        sections.append(f"### {service.name} ({service.url})\n{text}")

    if not sections:
        return "", report
    block = (
        "SOURCE CONTENT FROM EACH SELECTED SERVICE'S EXISTING PAGE (read automatically from the "
        "client's own site; use it only for services the Improved Page Content does not cover):\n\n"
        + "\n\n".join(sections)
    )
    return block, report


def is_no_reference(answer: str) -> bool:
    """True when the reference field says the page does not exist and names a landing page."""
    return (answer or "").strip().upper().startswith(NO_REFERENCE_MARKER)
