"""Measured outlines of a page, for comparing pillar page variants with the client's hosted page and
with competitors' pages. Rules only, never a model: every number is counted off the page.

`outline` reads HTML (a live page, or the ```html fence of a generated draft) or, failing that,
Markdown. `beat_sheet` turns competitor outlines into the targets the competitor variant must
beat. `comparison` lays several outlines side by side for the comparison card.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from statistics import median
from urllib.parse import urlparse

from app.services import html_dom

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’&-]*")
_HTML_FENCE = re.compile(r"```html\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_MD_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CTA_WORDS = re.compile(
    r"\b(book|call|contact|get|start|request|schedule|claim|download|talk|speak|enquire|enquiry|quote|apply|"
    r"buy|shop|order|sign up|subscribe|try|join|register|free)\b",
    re.IGNORECASE,
)
_CTA_CLASS = re.compile(r"\b(btn|button|cta)\b", re.IGNORECASE)
_FAQ = re.compile(r"\b(faq|frequently asked|common questions|questions? (?:we|people) (?:get|ask))\b", re.IGNORECASE)
_PROOF = re.compile(
    r"\b(testimonial|review|case stud(?:y|ies)|client(?:s)? say|rated|award|certified|accredited|trusted by|"
    r"\d+\+?\s*(?:years|clients|customers|projects|reviews))\b",
    re.IGNORECASE,
)
_DROP = {"script", "style", "noscript", "template", "svg"}


@dataclass
class PageOutline:
    """One page, measured."""

    label: str
    url: str | None = None
    words: int = 0
    h1: str = ""
    sections: list[str] = field(default_factory=list)
    ctas: int = 0
    internal_links: int = 0
    images: int = 0
    faq: bool = False
    schema_types: list[str] = field(default_factory=list)
    proof_mentions: int = 0
    keywords_covered: list[str] = field(default_factory=list)
    keywords_checked: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _count_words(text: str) -> int:
    return len(_WORD.findall(text))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def page_html(document: str) -> str | None:
    """The page inside a generated draft (its largest ```html fence), the document itself when it
    is an HTML page, or None for prose."""
    fences = _HTML_FENCE.findall(document or "")
    if fences:
        return max(fences, key=len)
    head = (document or "").lstrip()[:200].lower()
    if head.startswith("<!doctype html") or head.startswith("<html") or "<body" in (document or "")[:5000].lower():
        return document
    return None


def _visible_text(node: html_dom.Node) -> str:
    parts: list[str] = []

    def walk(current: html_dom.Node) -> None:
        if current.is_text:
            parts.append(current.text)
            return
        if current.tag in _DROP:
            return
        for child in current.children:
            walk(child)
        if not current.is_inline:
            parts.append(" ")

    walk(node)
    return _clean(" ".join(parts))


def _schema_types(document: html_dom.Document) -> list[str]:
    found: list[str] = []
    for script in document.root.find_all("script"):
        if "ld+json" not in script.attr("type").lower():
            continue
        try:
            data = json.loads("".join(child.text for child in script.children if child.is_text))
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                kind = item.get("@type")
                for name in kind if isinstance(kind, list) else [kind]:
                    if isinstance(name, str) and name not in found:
                        found.append(name)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return found


def _outline_html(label: str, html: str, url: str | None) -> PageOutline:
    document = html_dom.parse_html(html)
    body = document.body
    text = _visible_text(body)
    host = (urlparse(url).hostname or "").removeprefix("www.") if url else ""
    out = PageOutline(label=label, url=url, words=_count_words(text))
    h1 = body.find("h1")
    out.h1 = _clean(h1.text_content()) if h1 else ""
    out.sections = [s for s in (_clean(h.text_content()) for h in body.find_all("h2")) if s][:40]
    for link in body.find_all("a", "button"):
        words = _clean(link.text_content())
        if link.tag == "button" or _CTA_CLASS.search(" ".join(link.classes)) or (words and len(words.split()) <= 6 and _CTA_WORDS.search(words)):
            if words or link.tag == "button":
                out.ctas += 1
        if link.tag == "a":
            href = link.attr("href").strip()
            target = (urlparse(href).hostname or "").removeprefix("www.")
            if href.startswith("/") and not href.startswith("//") or (host and target == host):
                out.internal_links += 1
    out.images = len(body.find_all("img"))
    out.schema_types = _schema_types(document)
    headings = " ".join(_clean(h.text_content()) for h in body.find_all("h2", "h3"))
    out.faq = bool(_FAQ.search(headings)) or "FAQPage" in out.schema_types or len(body.find_all("details")) >= 3
    out.proof_mentions = len(_PROOF.findall(text))
    out._text = text  # type: ignore[attr-defined]
    return out


def _outline_markdown(label: str, text: str, url: str | None) -> PageOutline:
    plain = _MD_LINK.sub(r"\1", text)
    out = PageOutline(label=label, url=url, words=_count_words(re.sub(r"[#*_>`|-]", " ", plain)))
    for level, heading in _MD_HEADING.findall(text):
        heading = _clean(heading.replace("*", ""))
        if level == "#" and not out.h1:
            out.h1 = heading
        elif level == "##":
            out.sections.append(heading)
    out.sections = out.sections[:40]
    out.ctas = sum(1 for anchor, _ in _MD_LINK.findall(text) if _CTA_WORDS.search(anchor))
    out.internal_links = sum(1 for _, href in _MD_LINK.findall(text) if href.startswith("/"))
    out.faq = bool(_FAQ.search(" ".join(out.sections)))
    out.proof_mentions = len(_PROOF.findall(plain))
    out._text = plain  # type: ignore[attr-defined]
    return out


def outline(label: str, document: str, url: str | None = None, keywords: list[str] | None = None) -> PageOutline:
    """Measure a page: a live page's HTML, a draft holding an ```html page, or Markdown prose."""
    html = page_html(document)
    out = _outline_html(label, html, url) if html else _outline_markdown(label, document or "", url)
    if keywords:
        text = " " + re.sub(r"[^a-z0-9]+", " ", getattr(out, "_text", "").lower()) + " "
        out.keywords_checked = len(keywords)
        out.keywords_covered = [k for k in keywords if f" {re.sub(r'[^a-z0-9]+', ' ', k.lower()).strip()} " in text]
    return out


def unavailable(label: str, url: str | None, reason: str) -> PageOutline:
    return PageOutline(label=label, url=url, error=reason)


# ---------------------------------------------------------------------------------------- beat sheet


_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "your", "our", "you", "we", "is", "are", "how", "what", "why"}


def _topic_key(heading: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", heading.lower()) if w not in _STOP and len(w) > 2)


@dataclass
class BeatSheet:
    competitors: list[PageOutline]
    topics: list[tuple[str, list[str]]]
    target_words: int | None
    max_words: int | None
    median_words: int | None
    faq_share: str
    schema_seen: list[str]

    def to_dict(self) -> dict:
        return {
            "competitors": [c.to_dict() for c in self.competitors],
            "topics": [{"topic": t, "covered_by": by} for t, by in self.topics],
            "target_words": self.target_words,
            "max_words": self.max_words,
            "median_words": self.median_words,
            "faq_share": self.faq_share,
            "schema_seen": self.schema_seen,
        }


def beat_sheet(competitors: list[PageOutline]) -> BeatSheet:
    """What the competitor variant must beat: the topics competitors cover (merged when their
    headings share their content words), their length and their FAQ and schema use."""
    read = [c for c in competitors if not c.error and c.words]
    topics: list[tuple[frozenset[str], str, list[str]]] = []
    for page in read:
        name = urlparse(page.url or "").hostname or page.label
        for heading in page.sections:
            key = _topic_key(heading)
            if not key:
                continue
            for i, (other, label, by) in enumerate(topics):
                if len(key & other) >= max(1, min(len(key), len(other)) // 2 + (1 if min(len(key), len(other)) > 2 else 0)):
                    if name not in by:
                        by.append(name)
                    break
            else:
                topics.append((key, heading, [name]))
    topics.sort(key=lambda t: -len(t[2]))
    words = [c.words for c in read]
    schema: list[str] = []
    for page in read:
        schema.extend(s for s in page.schema_types if s not in schema)
    most = max(words) if words else None
    return BeatSheet(
        competitors=competitors,
        topics=[(label, by) for _, label, by in topics[:30]],
        target_words=int(most * 1.1) if most else None,
        max_words=most,
        median_words=int(median(words)) if words else None,
        faq_share=f"{sum(1 for c in read if c.faq)}/{len(read)}",
        schema_seen=schema,
    )


def beat_sheet_text(sheet: BeatSheet) -> str:
    """The beat sheet as prompt text: measured, with its source on every line."""
    lines = ["Competitor pages read (free direct reads; numbers counted off each page):"]
    for page in sheet.competitors:
        if page.error:
            lines.append(f"- {page.url or page.label}: not read ({page.error})")
        else:
            lines.append(
                f"- {page.url or page.label}: {page.words:,} words, {len(page.sections)} sections, {page.ctas} CTAs, "
                f"FAQ {'yes' if page.faq else 'no'}, schema {', '.join(page.schema_types) or 'none'}"
            )
    if sheet.max_words:
        lines.append(f"\nLength to beat: longest competitor page {sheet.max_words:,} words (median {sheet.median_words:,}). Aim for about {sheet.target_words:,} words of substance, never padding.")
    lines.append(f"FAQ sections: {sheet.faq_share} competitor pages have one. Structured data seen: {', '.join(sheet.schema_seen) or 'none'}.")
    if sheet.topics:
        lines.append("\nTopics competitors cover (their own H2 wording, most common first). Cover each one better, or say in the delivery notes why it does not apply:")
        lines.extend(f"- {topic} — {', '.join(by)}" for topic, by in sheet.topics)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------- comparison


def topic_coverage(page: PageOutline, topics: list[str]) -> tuple[int, int] | None:
    """How many of the competitors' topics (the beat sheet's) this page's sections cover."""
    if not topics:
        return None
    mine = [_topic_key(s) for s in page.sections]
    covered = 0
    for topic in topics:
        key = _topic_key(topic)
        if any(len(key & m) >= max(1, min(len(key), len(m)) // 2) for m in mine if m):
            covered += 1
    return covered, len(topics)
