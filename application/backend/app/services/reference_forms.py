"""The client's real forms, read off their own pages, for a generated page to post to.

A deployable funnel page needs its form to go somewhere real. The client's site already has forms
that work — a contact form, a booking form behind the main call to action — so the funnel's forms
post to one of those: the same `action`, `method` and hidden inputs (a WordPress Contact Form 7 form
will not accept a submission without its `_wpcf7*` fields), and the same field `name`s wherever the
funnel asks for the same thing. Nothing here invents an endpoint; with no form found, the page sends
the visitor to the client's call-to-action link instead, and the build says so.

Free reads first (`scraper._fetch_html`), at most `MAX_PAGES` of them: the reference page and the
page its primary call to action points at, which is usually where the booking form lives. A page
whose free read is bot-walled (SiteGround's sg-captcha walls some IPs and not others) is read
rendered through `context_dev.scrape_html` — 1 credit, only in that case, at most `MAX_PAGES` — the
way `design_md._rendered_tokens` and `service_scan._read_rendered` read the same walls.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from app.services import context_dev
from app.services.html_dom import Node, parse_html
from app.services.scraper import _fetch_html, looks_like_block_page

logger = logging.getLogger(__name__)

MAX_PAGES = 3
#: `scraper`'s wording for a wall that admits browsers but not this server. A 404 or a bad URL is not
#: one, and spends nothing.
_BOT_WALLED = re.compile(r"challenged|bot protection|blocked an automated|captcha", re.IGNORECASE)
_SKIP_INPUT_TYPES = {"submit", "button", "reset", "image"}
#: A site-search box is a form too, and posting a lead to it loses the lead.
_SEARCH_HINTS = ("search", "s")


@dataclass
class FormField:
    name: str
    kind: str  # input type, "textarea" or "select"
    label: str = ""
    required: bool = False


@dataclass
class ReferenceForm:
    page_url: str
    action: str
    method: str
    fields: list[FormField] = field(default_factory=list)
    hidden: list[tuple[str, str]] = field(default_factory=list)
    submit_label: str = ""

    @property
    def captures_email(self) -> bool:
        return any(f.kind == "email" or "email" in f.name.lower() for f in self.fields)

    def to_dict(self) -> dict:
        return {
            "page_url": self.page_url,
            "action": self.action,
            "method": self.method,
            "fields": [vars(f) for f in self.fields],
            "hidden": [list(pair) for pair in self.hidden],
            "submit_label": self.submit_label,
        }


def _label_for(form: Node, control: Node) -> str:
    control_id = control.element_id
    if control_id:
        for label in form.find_all("label"):
            if label.attr("for") == control_id:
                return label.text_content().strip()
    wrapping = control.closest("label")
    if wrapping is not None:
        return wrapping.text_content().strip()
    return control.attr("placeholder") or control.attr("aria-label")


def extract_forms(html: str, page_url: str) -> list[ReferenceForm]:
    """Every lead-capturing form on the page, in page order. A search box is left out."""
    try:
        document = parse_html(html)
    except Exception:  # noqa: BLE001 — an unparseable page simply offers no form
        return []
    forms: list[ReferenceForm] = []
    for form in document.root.find_all("form"):
        method = (form.attr("method") or "get").lower()
        fields: list[FormField] = []
        hidden: list[tuple[str, str]] = []
        submit_label = ""
        for control in form.find_all("input", "textarea", "select", "button"):
            name = control.attr("name").strip()
            kind = control.tag if control.tag in ("textarea", "select") else (control.attr("type") or "text").lower()
            if control.tag == "button" or kind in _SKIP_INPUT_TYPES:
                submit_label = submit_label or control.text_content().strip() or control.attr("value")
                continue
            if not name:
                continue
            if kind == "hidden":
                hidden.append((name, control.attr("value")))
                continue
            label = " ".join(_label_for(form, control).split())[:80]
            required = "required" in control.attrs or control.attr("aria-required") == "true"
            if not any(f.name == name for f in fields):
                fields.append(FormField(name=name, kind=kind, label=label, required=required))
        if not fields:
            continue
        if method == "get" and all(f.name.lower() in _SEARCH_HINTS for f in fields):
            continue
        action = urljoin(page_url, form.attr("action") or page_url)
        forms.append(
            ReferenceForm(
                page_url=page_url, action=action, method=method, fields=fields, hidden=hidden, submit_label=submit_label
            )
        )
    return forms


def _same_site(url: str, site: str) -> bool:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return bool(host) and host == urlparse(site).netloc.lower().removeprefix("www.")


async def read_forms(urls: list[str]) -> tuple[list[ReferenceForm], list[str]]:
    """The forms on each page, read for free. Returns (forms, notes); a page that cannot be read is a
    note, never a failure."""
    wanted = [u for u in dict.fromkeys(urls) if u and u.startswith("http")][:MAX_PAGES]
    site = wanted[0] if wanted else ""
    wanted = [u for u in wanted if _same_site(u, site)]

    async def rendered(url: str, why: str) -> tuple[str, str | None, str]:
        if not context_dev.is_configured():
            return url, None, why
        try:
            page = await context_dev.scrape_html(url)
        except Exception as exc:  # noqa: BLE001
            return url, None, f"{why}; the rendered read failed too: {exc}"
        if not page.html.strip() or looks_like_block_page(page.html):
            return url, None, f"{why}; the rendered page was a bot-check page too"
        notes.append(f"Read {url} rendered via Context.dev (1 credit): the free read was bot-walled.")
        return page.final_url or url, page.html, ""

    async def one(url: str) -> tuple[str, str | None, str]:
        try:
            final_url, html = await _fetch_html(url)
        except Exception as exc:  # noqa: BLE001
            if _BOT_WALLED.search(str(exc)):
                return await rendered(url, str(exc))
            return url, None, str(exc)
        if looks_like_block_page(html):
            return await rendered(url, "the site served a bot-check page")
        return final_url or url, html, ""

    forms: list[ReferenceForm] = []
    notes: list[str] = []
    for url, html, why in await asyncio.gather(*(one(u) for u in wanted)):
        if html is None:
            notes.append(f"Could not read {url} for its forms: {why}.")
            continue
        found = extract_forms(html, url)
        forms.extend(f for f in found if f.action not in {g.action for g in forms})
    logger.info("Reference forms: %s form(s) from %s page(s)", len(forms), len(wanted))
    return forms, notes


def forms_markdown(forms: list[ReferenceForm]) -> str:
    """The forms as the page builder reads them."""
    if not forms:
        return ""
    out: list[str] = []
    for i, form in enumerate(forms, 1):
        out.append(f"### Form {i} — on {form.page_url}")
        out.append(f"- action: `{form.action}`")
        out.append(f"- method: `{form.method}`")
        if form.hidden:
            out.append("- hidden inputs (copy every one verbatim, with its value):")
            out += [f'    - `<input type="hidden" name="{name}" value="{value}">`' for name, value in form.hidden]
        out.append("- fields:")
        for f in form.fields:
            req = ", required" if f.required else ""
            out.append(f"    - `{f.name}` ({f.kind}{req}){' — ' + f.label if f.label else ''}")
        if form.submit_label:
            out.append(f"- submit button on the client's page: \"{form.submit_label}\"")
        out.append("")
    return "\n".join(out)
