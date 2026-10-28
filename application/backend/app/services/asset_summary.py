"""The reader's Summary tab: what a deliverable contains, in a handful of points.

The Visual and Text views are the document itself, re-laid out or verbatim, and never summarise it.
This is the one place that does, so it is held to the same rule as the business check's judge: the
model may only describe what is there, and code checks what it can.

- **One Haiku call per document**, `low` effort, on the text with any built HTML page replaced by a
  marker (a page's markup is not its content, and it would crowd out the rest).
- **Points, not prose**: `MIN_POINTS`-`MAX_POINTS`, one `- point || heading` line each. Not JSON:
  these documents are full of quoted phrases, and one unescaped quote made the first real ICP's
  JSON reply unreadable. Asked for one fact in `_POINT_WORDS_ASKED` words; longer than
  `_MAX_POINT_WORDS` is dropped, and more than `MAX_POINTS` is cut.
- **Checked in code** (`_verified_points`):
  - a point that states a number the document does not contain is dropped, so a summary cannot
    invent a price, a count or a score;
  - a point's `section` must be one of the document's own headings, or it is cleared rather than
    pointing the reader at a section that does not exist.
- **Never stored server-side.** The browser keeps the result on the message, keyed by the text it
  was made from, so reopening a deliverable costs nothing and an edited one is summarised again.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field

from app.services.claude_client import get_client
from app.services.generation import HAIKU, OnUsage
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

MODEL = HAIKU
MIN_POINTS = 4
MAX_POINTS = 8
#: Asked for; a point up to `_MAX_POINT_WORDS` is still kept, since one a few words over is not wrong.
_POINT_WORDS_ASKED = 25
_MAX_POINT_WORDS = 40
_MAX_TOKENS = 1000
#: The document shown to the model. Long enough for every stage's text once pages are taken out.
_MAX_INPUT_CHARS = 60_000

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HTML_FENCE = re.compile(r"```(?:html|htm)?\s*\n\s*(?:<!doctype|<html|<head|<body|<div|<section)[\s\S]*?```", re.IGNORECASE)
_HTML_DOC = re.compile(r"<!doctype html[\s\S]*?</html>", re.IGNORECASE)
#: A number as written: 4,500 · 58/100 · 41% · 3.5 · $1,500. Years and list ordinals included —
#: they must be in the document too.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class SummaryError(RuntimeError):
    """The summary could not be made. One sentence for the reader."""


@dataclass(frozen=True)
class SummaryPoint:
    text: str
    #: One of the document's own headings, or "" when the point spans the document.
    section: str = ""


@dataclass
class DocumentSummary:
    points: list[SummaryPoint] = field(default_factory=list)
    #: Points the model gave that failed a check, for the logs and the tests.
    dropped: int = 0

    def to_dict(self) -> dict:
        return {"points": [asdict(p) for p in self.points], "dropped": self.dropped}


def _clean_heading(text: str) -> str:
    return re.sub(r"[*_`]", "", text).strip()


def headings(text: str) -> list[str]:
    out: list[str] = []
    for match in _HEADING.finditer(text or ""):
        title = _clean_heading(match.group(1))
        if title and title not in out:
            out.append(title)
    return out


def readable(text: str) -> str:
    """The document as the model reads it: built pages replaced by a one-line marker, then capped."""
    body = _HTML_FENCE.sub("[A built HTML page is here.]", text or "")
    body = _HTML_DOC.sub("[A built HTML page is here.]", body)
    if len(body) > _MAX_INPUT_CHARS:
        body = body[:_MAX_INPUT_CHARS] + "\n\n[Document continues; summarise what is shown.]"
    return body


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUMBER.finditer(text or "")}


def build_prompt(asset_label: str, text: str) -> str:
    sections = headings(text)
    listed = "\n".join(f"- {h}" for h in sections) or "(the document has no headings)"
    return (
        f"Below is a finished marketing deliverable: {asset_label}. Summarise what it contains for an "
        "operator deciding what is in it before reading it.\n\n"
        "Rules:\n"
        f"- Between {MIN_POINTS} and {MAX_POINTS} points in total, never more. This is not one point per "
        "section: combine related sections into one point and leave minor ones out.\n"
        f"- One fact per point, at most {_POINT_WORDS_ASKED} words, in the order the document covers them.\n"
        "- Spread the points across the whole document, beginning to end. The later sections matter as "
        "much as the first ones.\n"
        "- Be specific: name the offers, prices, channels, audiences, scores, steps and sections the "
        "document actually contains.\n"
        "- Describe only what is in the document. No praise, no advice, no opinion of its quality, nothing "
        "it does not say. Every number you write must appear in the document exactly.\n"
        "- After each point, name the heading it comes from, copied exactly from the HEADINGS list, or "
        "write - when it spans the document.\n\n"
        f"HEADINGS\n{listed}\n\n"
        "Reply with only the points, one per line, in exactly this form and nothing else:\n"
        "- <the point> || <heading or ->\n\n"
        f"DOCUMENT\n{readable(text)}"
    )


def parse_points(raw: str) -> list[tuple[str, str]]:
    """`(point, heading)` per `- point || heading` line. One line per point rather than JSON: these
    documents are full of quoted buyer phrases, and one unescaped quote made a JSON reply unreadable
    on the first real ICP tried. Lines without the marker are taken as a point with no heading."""
    out: list[tuple[str, str]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not re.match(r"^[-•*]\s+", line):
            continue
        body = re.sub(r"^[-•*]\s+", "", line)
        point, _, heading = body.partition("||")
        heading = heading.strip()
        out.append((point.strip(), "" if heading in {"", "-", "—"} else heading))
    return out


def _verified_points(raw: str, text: str) -> DocumentSummary:
    items = parse_points(raw)
    if not items:
        raise SummaryError("The summary came back in a form that could not be read.")

    in_document = _numbers(text)
    by_lower = {h.lower(): h for h in headings(text)}
    summary = DocumentSummary()
    for point_text, heading in items:
        point = re.sub(r"\s+", " ", point_text).strip()
        if not point or len(point.split()) > _MAX_POINT_WORDS:
            summary.dropped += 1
            continue
        # A number the document does not contain is an invented fact: the whole point goes.
        if not _numbers(point) <= in_document:
            summary.dropped += 1
            continue
        section = by_lower.get(_clean_heading(heading).lower(), "")
        summary.points.append(SummaryPoint(point, section))
    if not summary.points:
        raise SummaryError("No point in the summary could be checked against the document.")
    summary.points = _spread(summary.points, MAX_POINTS)
    return summary


def _spread(points: list[SummaryPoint], limit: int) -> list[SummaryPoint]:
    """At most `limit` points, evenly spaced and in order, first and last always kept. The model
    often returns a few too many; cutting at the limit dropped every later section (a 16-section ICP
    summarised as sections 1-8)."""
    if len(points) <= limit:
        return points
    picks = sorted({round(i * (len(points) - 1) / (limit - 1)) for i in range(limit)})
    return [points[i] for i in picks]


async def _ask(prompt: str, on_usage: OnUsage | None) -> str:
    started = time.monotonic()
    response = await get_client().messages.create(
        model=MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=MODEL, duration_ms=int((time.monotonic() - started) * 1000))
        )
    return next((b.text for b in response.content if b.type == "text"), "")


async def summarise(asset_label: str, text: str, on_usage: OnUsage | None = None) -> DocumentSummary:
    """Raises `SummaryError` with one sentence on any failure."""
    if len((text or "").strip()) < 40:
        raise SummaryError("There is not enough in this document to summarise.")
    try:
        raw = await _ask(build_prompt(asset_label, text), on_usage)
    except Exception as exc:  # noqa: BLE001 — any model failure is the same outcome for the reader
        logger.warning("Summary for %s failed: %s", asset_label, exc)
        raise SummaryError(f"The summary could not be made: {exc}") from exc
    summary = _verified_points(raw, text)
    logger.info("Summary for %s: %d points, %d dropped", asset_label, len(summary.points), summary.dropped)
    return summary
