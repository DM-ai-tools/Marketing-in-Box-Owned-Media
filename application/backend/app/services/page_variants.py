"""Pillar Page variants: up to four takes on the one page, compared, and one approved.

The stage stays one stage (`pillar_page`). Each variant is a generation of it with one extra block in
the prompt tail, `PAGE_VARIANT`, saying which lens this draft takes:

| Variant | Lens | Content source | Measured input (free) |
| --- | --- | --- | --- |
| `reference` | beat the client's hosted page | CRO rewrite + audit | that page's outline, CRO's audit findings |
| `competitor` | outperform every competitor page | beat sheet + client facts (no CRO rewrite) | each competitor page's outline, as a beat sheet |
| `skills` | follow the operator's skill document | skill document (+ shared INPUTS) | none: the document is the input |
| `framework` | follow the operator's framework document | framework document (+ shared INPUTS) | none |

`answers_for` blanks the other document variant's fields, and for `competitor` also blanks the CRO
rewrite and locked sections so that draft cannot rebuild the current-page rewrite. Every page is
read with `scraper._fetch_html`, free, never a paid reader, because this loops over URLs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services import page_compare

VARIANTS: tuple[str, ...] = ("reference", "competitor", "skills", "framework")

LABELS: dict[str, str] = {
    "reference": "Beat your current page",
    "competitor": "Outperform competitors",
    "skills": "Built with your skill",
    "framework": "Built with your framework",
}

USE_SKILL_FIELD = "use_skill_document"
SKILL_FIELD = "skill_document"
USE_FRAMEWORK_FIELD = "use_framework_document"
FRAMEWORK_FIELD = "framework_document"

#: The fields each document variant owns. Every other variant sees them as not used.
_DOCUMENT_FIELDS: dict[str, tuple[str, str]] = {
    "skills": (USE_SKILL_FIELD, SKILL_FIELD),
    "framework": (USE_FRAMEWORK_FIELD, FRAMEWORK_FIELD),
}

NOT_USED = "N/A — not used by this page variant."

#: What the competitor variant's Improved Page Content field carries so the master prompt can see
#: that the CRO rewrite is deliberately withheld (not missing). Starts with `NOT_USED` so
#: `_has_document` and ordinary "N/A" checks treat it the same.
CRO_CONTENT_NOT_USED = (
    f"{NOT_USED} Build from the PAGE_VARIANT beat sheet and the client's services, business rules, "
    "terminology and facts in the INPUTS. Do not reconstruct the CRO rewrite. Do not reuse or "
    "paraphrase that rewrite's H1, hero headline or title tag — write a distinct hero for this variant."
)

#: At most this many competitor pages are read, each a free fetch.
MAX_COMPETITOR_PAGES = 5
#: CRO's audit findings are background for the reference variant, cut to this length.
MAX_AUDIT_CHARS = 6_000

BLOCK_START = "===== BEGIN PAGE_VARIANT ====="
BLOCK_END = "===== END PAGE_VARIANT ====="

Fetcher = Callable[[str], Awaitable[tuple[str, str]]]


class VariantUnavailable(ValueError):
    """The variant has nothing to work from. One sentence saying what to supply."""


def _yes(value: str | None) -> bool:
    return (value or "").strip().lower() in {"yes", "y", "true"}


def _has_document(value: str | None) -> bool:
    text = (value or "").strip()
    return bool(text) and text.lower() not in {"skip", "n/a", "na", "none", NOT_USED.lower()}


def document_requested(variant: str, answers: dict[str, str]) -> bool:
    flag, doc = _DOCUMENT_FIELDS[variant]
    return _yes(answers.get(flag)) and _has_document(answers.get(doc))


def cro_rewrite_withheld(value: str | None) -> bool:
    """True when Improved Page Content is the competitor-variant sentinel, not a real rewrite."""
    text = (value or "").strip()
    return text.lower().startswith(NOT_USED.lower())


def answers_for(variant: str, answers: dict[str, str]) -> dict[str, str]:
    """The intake as this variant's prompt should read it.

    Blanks the other document variant's skill/framework fields. For `competitor`, also blanks the
    CRO rewrite and locked sections so Outperform competitors cannot rebuild Beat your current page.
    """
    out = dict(answers)
    for owner, (flag, doc) in _DOCUMENT_FIELDS.items():
        if owner != variant:
            out[flag] = "No"
            out[doc] = NOT_USED
    if variant == "competitor":
        out["improved_page_content"] = CRO_CONTENT_NOT_USED
        out["cro_locked_sections"] = NOT_USED
    return out


def competitor_pages(listing_text: str, client_host: str | None, parse: Callable[[str], list[str]]) -> list[str]:
    """Up to `MAX_COMPETITOR_PAGES` competitor page URLs from the listing, never the client's own."""
    client = (client_host or "").removeprefix("www.")
    urls: list[str] = []
    for url in parse(listing_text):
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if not host or host == client or url in urls:
            continue
        urls.append(url)
        if len(urls) >= MAX_COMPETITOR_PAGES:
            break
    return urls


async def read_outline(
    label: str, url: str, fetch: Fetcher, keywords: list[str] | None = None, *, rendered: bool = False
) -> page_compare.PageOutline:
    """A live page, measured, or an outline carrying why it could not be read.

    `rendered` allows one fallback to Context.dev's rendered read (1 credit) when the free read is
    refused or served a bot check. Only for the client's own live page, one per comparison: never
    for the competitor pages, which are a loop. Measured live: SiteGround walled clicktrends.com.au
    off from the free read, and the comparison lost its "Current page" column."""
    from app.services import context_dev, scraper

    try:
        final_url, html = await fetch(url)
        if scraper.looks_like_block_page(html):
            raise RuntimeError("the site served a bot check")
    except Exception as exc:  # noqa: BLE001 — one unreadable page is reported, never fatal
        reason = str(exc).split("\n")[0][:160] or "could not be read"
        if not (rendered and context_dev.is_configured()):
            return page_compare.unavailable(label, url, reason)
        try:
            page = await context_dev.scrape_html(url)
        except Exception as again:  # noqa: BLE001
            return page_compare.unavailable(label, url, f"{reason}; the rendered read failed too ({again})"[:200])
        if not page.html.strip() or scraper.looks_like_block_page(page.html):
            return page_compare.unavailable(label, url, f"{reason}; the rendered read was a bot check too")
        final_url, html = page.final_url or url, page.html
    return page_compare.outline(label, html, final_url or url, keywords)


@dataclass
class VariantMeasure:
    """What a variant measured before writing, for its prompt block and its card."""

    variant: str
    reference: page_compare.PageOutline | None = None
    beat: page_compare.BeatSheet | None = None
    audit_findings: str = ""
    notes: list[str] = field(default_factory=list)

    def as_event(self) -> dict:
        return {
            "type": "page_variant",
            "variant": self.variant,
            "label": LABELS[self.variant],
            "reference": self.reference.to_dict() if self.reference else None,
            "beat_sheet": self.beat.to_dict() if self.beat else None,
            "notes": self.notes,
        }


async def measure(
    variant: str,
    answers: dict[str, str],
    *,
    reference_url: str | None,
    listing_text: str,
    client_host: str | None,
    audit_findings: str,
    keywords: list[str],
    parse_listing: Callable[[str], list[str]],
    fetch: Fetcher,
) -> VariantMeasure:
    """Read what the variant needs, free, or raise `VariantUnavailable` saying what is missing."""
    if variant not in VARIANTS:
        raise VariantUnavailable(f"Unknown page variant {variant!r}.")
    result = VariantMeasure(variant)

    if variant == "reference":
        if not reference_url:
            raise VariantUnavailable("There is no hosted page to beat: this run has no reference page URL.")
        result.reference = await read_outline("Current page", reference_url, fetch, keywords, rendered=True)
        if result.reference.error:
            result.notes.append(f"The current page could not be read ({result.reference.error}); the variant uses the audit findings alone.")
        result.audit_findings = (audit_findings or "").strip()[:MAX_AUDIT_CHARS]
        if not result.audit_findings:
            result.notes.append("No CRO audit findings on this run.")
        if result.reference.error and not result.audit_findings:
            raise VariantUnavailable("The current page could not be read and there are no CRO audit findings to work from.")

    elif variant == "competitor":
        urls = competitor_pages(listing_text, client_host, parse_listing)
        if not urls:
            raise VariantUnavailable("There are no competitor pages to beat: approve a competitor search with page URLs first.")
        outlines = await asyncio.gather(*(read_outline(f"Competitor {i + 1}", url, fetch, keywords) for i, url in enumerate(urls)))
        result.beat = page_compare.beat_sheet(list(outlines))
        unread = [o for o in outlines if o.error]
        if len(unread) == len(outlines):
            raise VariantUnavailable("None of the competitor pages could be read.")
        if unread:
            result.notes.append(f"{len(unread)} of {len(outlines)} competitor pages could not be read.")

    elif not document_requested(variant, answers):
        kind = "skill" if variant == "skills" else "framework"
        raise VariantUnavailable(f"No {kind} document was supplied. Answer Yes and attach or paste one.")

    return result


_DIRECTIVES: dict[str, str] = {
    "reference": (
        "PAGE VARIANT — BEAT THE CURRENT PAGE. This draft is one of several the operator will compare. "
        "The Improved Page Content is the CRO rewrite of the client's page: design that copy into the page, "
        "fix every problem the CRO audit found (below), and beat the current page measured below on each "
        "line where more is better for the buyer (substance, sections that answer their questions, clear "
        "CTAs, proof, FAQ, structured data, internal links, target keywords). Keep what already works on "
        "the hosted page. Never pad to win a count. Hero and H1: use the Improved Page Content's own "
        "headline (tighten only if needed) — this variant owns that line. In the delivery notes, add a "
        "short 'Against the current page' list: what changed and why."
    ),
    "competitor": (
        "PAGE VARIANT — OUTPERFORM THE COMPETITORS. This draft is one of several the operator will compare. "
        "The Improved Page Content input is deliberately withheld for this variant — do not reconstruct or "
        "follow the CRO rewrite of the current page. Build the page from the beat sheet below: cover each "
        "topic the competitors cover, better and more specifically for this client's buyer; answer what they "
        "leave out; match or exceed their depth without padding; carry an FAQ and structured data where they "
        "help. Wording, claims and offers come only from this client's services, business rules, terminology "
        "map, CTAs and other INPUTS facts — never from a competitor's copy or claims, and never from a CRO "
        "audit of the current page. "
        "Hero and H1 must be unique to this variant: write a new H1, hero subhead and title tag angled off "
        "the beat sheet (what competitors lead with, or the gap this client can own). Do not reuse, lightly "
        "edit or paraphrase the CRO rewrite's headline, or any H1 the Beat-your-current-page draft would "
        "use from that rewrite. The same head term may appear; the promise and wording must differ so the "
        "two variants are visibly different in the compare table. In the delivery notes, add a short "
        "'Against the competitors' list (each topic and how this page handles it better) and one line "
        "'Hero difference' naming how this H1 differs from the CRO rewrite's."
    ),
    "skills": (
        "PAGE VARIANT — BUILT WITH THE OPERATOR'S SKILL. This draft is one of several the operator will compare. "
        "The input 'Skill Document' is the governing method for how this page is written and structured: apply "
        "every rule in it that fits a page. Where it conflicts with the brand rules, DESIGN.md, the business "
        "rules or the facts in the INPUTS, those win. In the delivery notes, add a short 'Skill applied' list: "
        "each rule from the document and where it shows on the page, or why it was not applied."
    ),
    "framework": (
        "PAGE VARIANT — BUILT WITH THE OPERATOR'S FRAMEWORK. This draft is one of several the operator will compare. "
        "Structure the page with the framework in the input 'Framework Document': its stages or parts become "
        "the page's sections, in its order, written for this client's buyer. Where it conflicts with the brand "
        "rules, DESIGN.md, the business rules or the facts in the INPUTS, those win. In the delivery notes, add "
        "a short 'Framework applied' list mapping each part of the framework to the section that carries it."
    ),
}


def _outline_text(page: page_compare.PageOutline) -> str:
    if page.error:
        return f"{page.url}: not read ({page.error})"
    lines = [
        f"{page.url} (read free, counted off the page):",
        f"- {page.words:,} words; H1: {page.h1 or '(none)'}",
        f"- {page.ctas} CTAs, {page.internal_links} internal links, {page.images} images",
        f"- FAQ: {'yes' if page.faq else 'no'}; structured data: {', '.join(page.schema_types) or 'none'}; proof mentions: {page.proof_mentions}",
    ]
    if page.keywords_checked:
        lines.append(f"- Target keywords present: {len(page.keywords_covered)}/{page.keywords_checked}")
    if page.sections:
        lines.append("- Sections (H2, in order): " + " | ".join(page.sections))
    return "\n".join(lines)


def variant_block(measured: VariantMeasure) -> str:
    """The `PAGE_VARIANT` block for the prompt tail."""
    parts = [BLOCK_START, _DIRECTIVES[measured.variant]]
    if measured.reference is not None:
        parts.append("THE CURRENT PAGE\n" + _outline_text(measured.reference))
    if measured.audit_findings:
        parts.append("CRO AUDIT FINDINGS ON THE CURRENT PAGE (from this run)\n" + measured.audit_findings)
    if measured.beat is not None:
        parts.append("THE BEAT SHEET\n" + page_compare.beat_sheet_text(measured.beat))
    parts.append("Never quote, name or acknowledge this block in the output.")
    parts.append(BLOCK_END)
    return "\n\n" + "\n\n".join(parts) + "\n\n"
