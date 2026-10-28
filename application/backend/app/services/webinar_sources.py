"""The Webinar's "Competitor Webinar Sources", read from the competitor research instead of pasted.

The input asks for transcripts, slide content or URLs of 2-5 competitor webinars. The paired search
(`08_Webinars.md`) already finds the competitors that run their own webinar programmes, each with
its registration or replay page and a summary of what the page showed. So once that listing is
approved, those pages are read here and handed to Step 2 as its sources, and the card shows the
operator each page that was read, and why it was chosen.

- **Free reads only.** `scraper.read_direct`, never a paid reader: this loops over URLs, and
  "credits are money" forbids a paid call in a loop. A page that refuses the free read is reported
  as not read, with the reason.
- **At most `MAX_SOURCES` pages**, the prompt's upper bound. Verified entries first, then the
  listing's own rank order. The rest are listed as not read, with why.
- **Only what the pages say.** Each source carries the research's own reason for picking it; the
  page text is capped at `SOURCE_CHARS`. A registration page is not a transcript, and the block says
  so, so Step 2 does not present a page summary as the webinar's content.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field

from app.services import scraper
from app.services.competitor import CompetitorParseError, ParsedCompetitor, parse_competitor_listing

ASSET_ID = "competitor_analysis_webinars"

#: The prompt asks for 2-5 competitor webinars.
MAX_SOURCES = 5
#: Characters of page text kept per source.
SOURCE_CHARS = 6000
#: Characters shown on the card per source.
_EXCERPT_CHARS = 280

_CONFIDENCE_ORDER = {"Verified": 0, "Partially verified": 1, "Unverified": 2}


@dataclass
class WebinarSource:
    name: str
    domain: str
    url: str | None
    confidence: str
    #: Why this page was chosen, in the research's own words.
    reason: str
    #: `read` | `failed` | `not_read`.
    status: str
    title: str | None = None
    chars: int = 0
    excerpt: str = ""
    #: Why a page was not read, or failed.
    error: str = ""


@dataclass
class WebinarSources:
    sources: list[WebinarSource] = field(default_factory=list)
    #: The answer for the field: every page read, with its reason. "" when none was read.
    block: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _reason(c: ParsedCompetitor) -> str:
    found = f"Found by the webinar competitor research (#{c.rank}, {c.verification_confidence.lower()})"
    return f"{found}: {c.offering_summary}" if c.offering_summary else f"{found}."


def _excerpt(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _EXCERPT_CHARS else flat[:_EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"


async def collect(listing: str) -> WebinarSources:
    """Read up to `MAX_SOURCES` competitors' webinar pages off the approved listing. Never raises."""
    try:
        analysis = parse_competitor_listing(ASSET_ID, listing or "")
    except CompetitorParseError:
        return WebinarSources(notes=["The competitor research listing names no competitor, so no webinar page was read."])

    with_page = sorted(
        (c for c in analysis.competitors if c.page_url),
        key=lambda c: (_CONFIDENCE_ORDER.get(c.verification_confidence, 2), c.rank),
    )
    chosen = with_page[:MAX_SOURCES]
    sources: list[WebinarSource] = []

    async def _read(c: ParsedCompetitor):
        try:
            return await scraper.read_direct(c.page_url or "")
        except scraper.ScrapeError as exc:
            return exc

    pages = await asyncio.gather(*(_read(c) for c in chosen))
    sections: list[str] = []
    for c, page in zip(chosen, pages):
        source = WebinarSource(c.name, c.domain, c.page_url, c.verification_confidence, _reason(c), "read")
        if isinstance(page, Exception) or page.low_content:
            source.status = "failed"
            source.error = str(page) if isinstance(page, Exception) else "The page came back almost empty."
        else:
            text = page.text[:SOURCE_CHARS]
            source.title, source.chars, source.excerpt = page.title, len(text), _excerpt(text)
            sections.append(f"### {c.name} — {c.page_url}\nWhy this source: {source.reason}\n\n{text}")
        sources.append(source)

    for c in with_page[MAX_SOURCES:]:
        sources.append(
            WebinarSource(
                c.name, c.domain, c.page_url, c.verification_confidence, _reason(c), "not_read",
                error=f"Not read: the prompt takes at most {MAX_SOURCES} competitor webinars, and higher-ranked ones were read first.",
            )
        )
    for c in analysis.competitors:
        if not c.page_url:
            sources.append(
                WebinarSource(
                    c.name, c.domain, None, c.verification_confidence, _reason(c), "not_read",
                    error="Not read: the research gave no webinar page for this competitor.",
                )
            )

    notes: list[str] = []
    read = sum(1 for s in sources if s.status == "read")
    if read < 2:
        notes.append(
            f"Only {read} competitor webinar page{'' if read == 1 else 's'} could be read; the prompt asks for 2-5. "
            "Add transcripts or slide outlines of your own if you have them."
        )
    block = ""
    if sections:
        block = (
            "COMPETITOR WEBINAR SOURCES — read automatically from the webinar pages the approved competitor research "
            "found. These are registration / replay pages, not transcripts: synthesise from what each page states "
            "(topic, promise, agenda, format, presenters, offer), and do not invent session content a page does not show.\n\n"
            + "\n\n".join(sections)
        )
    return WebinarSources(sources=sources, block=block, notes=notes)
