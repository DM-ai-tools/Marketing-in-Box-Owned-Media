"""The Funnel stage's document, read as a build plan: which HTML files to make, with which copy.

`Funnel_Prompt.md` fixes the output format — `PART 3: [Page 1 name] — Full Copy` … `PART 7:
[Booking page] — Full Copy`, each page opening with a header block (`Page type`, `URL`, goals), and
`PART 6` holding the three follow-up emails as `## Email N — …`. Parsed, never summarised by a
model: the copy is already approved, and a second model pass over it is a second place for a word
to change.

Two shapes, both real (`manual_execution/`):

- **One page per URL.** `Funnel-Document_Social-Media-Marketing.md`: entry, gate, thank-you and
  booking pages at four URLs → four files.
- **Several pages at one URL.** `Partner-Voice-Audit-Funnel_LinkedIn-Marketing-Ads.md`: pages 1-3 are
  screens of one quiz ("same tool instance (no separate URL …)") → one file whose screens switch in
  place. A page whose URL line names no path joins the file before it.

Each file gets its path from the funnel's own URL (`/social-media-audit/request/` →
`social-media-audit/request/index.html`), so the bundle deploys at the client's site root with the
URLs the funnel was designed with, and the pages link each other by relative path.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

#: `# PART 3 — PAGE 1: FREE SOCIAL MEDIA AUDIT LANDING PAGE — FULL COPY`, at any heading level, with
#: or without bold, with `—`, `–`, `-` or `:` as the separator.
_PART_HEADING = re.compile(
    r"^\s*#{1,4}\s*\**\s*PART\s+(\d+)\s*[—–:\-]+\s*(.+?)\s*\**\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_PAGE_TITLE = re.compile(r"^(?:PAGE\s*\d+\s*[:—–\-]\s*)?(.+?)(?:\s*[—–\-]\s*FULL\s+COPY)?\s*$", re.IGNORECASE)
_IS_PAGE = re.compile(r"\bFULL\s+COPY\b|^\s*PAGE\s*\d+\b", re.IGNORECASE)
_IS_EMAILS = re.compile(r"\bEMAIL\b.*\bSEQUENCE\b|\bFOLLOW[- ]UP\b", re.IGNORECASE)
_IS_NOTES = re.compile(r"\bIMPLEMENTATION\b|\bCRO\s+MAPPING\b", re.IGNORECASE)

_FIELD = r"^\s*[-*]?\s*\**\s*{label}\s*:?\s*\**\s*:?\s*(.+?)\s*$"
_URL_LINE = re.compile(_FIELD.format(label=r"URL(?:\s*slug)?"), re.IGNORECASE | re.MULTILINE)
_TYPE_LINE = re.compile(_FIELD.format(label=r"Page\s*type"), re.IGNORECASE | re.MULTILINE)
_PATH = re.compile(r"`?(/[A-Za-z0-9._~\-/]*)`?")

_EMAIL_HEADING = re.compile(r"^\s*#{2,4}\s*\**\s*(Email\s*\d+\b.*?)\s*\**\s*$", re.IGNORECASE | re.MULTILINE)
_SUBJECT_LINE = re.compile(_FIELD.format(label=r"Subject(?:\s*line)?"), re.IGNORECASE | re.MULTILINE)

_SLUG_JUNK = re.compile(r"[^a-z0-9]+")


@dataclass
class FunnelScreen:
    """One `PART n — PAGE k` block: a page of the funnel as its copy describes it."""

    part: int
    title: str
    page_type: str
    url_line: str
    copy: str


@dataclass
class FunnelFile:
    """One HTML file to build. Several screens when the funnel runs them at one URL."""

    key: str
    url_path: str
    screens: list[FunnelScreen] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.screens[0].title if self.screens else self.key

    @property
    def file_path(self) -> str:
        """Where it sits in the bundle: `/a/b/` → `a/b/index.html`, `/` → `index.html`."""
        path = self.url_path.strip("/")
        return f"{path}/index.html" if path else "index.html"

    @property
    def is_multi_screen(self) -> bool:
        return len(self.screens) > 1

    def href_to(self, other: "FunnelFile") -> str:
        """A relative link from this file to `other`'s directory, so the bundle works wherever the
        site root is. Ends in `/`: the deployed URL the funnel was designed with."""
        here = posixpath.dirname(self.file_path) or "."
        there = posixpath.dirname(other.file_path) or "."
        rel = posixpath.relpath(there, here)
        return "./" if rel == "." else rel.rstrip("/") + "/"


@dataclass
class FunnelEmail:
    key: str
    title: str
    subject: str
    copy: str


@dataclass
class FunnelPlan:
    title: str
    #: The document's opening block (client, ICP, tone, conversion goal) — context for every page.
    preamble: str
    #: PART 1 — the flow and the CTA structure, so each page knows where it sits.
    strategy: str
    files: list[FunnelFile]
    emails: list[FunnelEmail]
    #: PART 8 — integrations, tracking, KPIs. For the README, not for a page.
    notes: str

    @property
    def available(self) -> bool:
        return bool(self.files)


def _slug(text: str, fallback: str) -> str:
    return _SLUG_JUNK.sub("-", text.lower()).strip("-")[:60] or fallback


def _url_path(url_line: str) -> str | None:
    """The path a page's URL line names, or None when it names none ("same tool instance …").
    A full URL keeps only its path; the bundle deploys at the client's root."""
    line = url_line.strip()
    full = re.search(r"https?://[^/\s`]+(/[^\s`]*)?", line)
    if full:
        path = posixpath.normpath(full.group(1) or "/")
        return "/" if path in (".", "/") else path.rstrip("/") + "/"
    if re.match(r"^`?/", line):
        found = _PATH.search(line)
        if found:
            path = posixpath.normpath(found.group(1))
            return "/" if path in (".", "/") else path.rstrip("/") + "/"
    return None


#: A backticked site path in a page's header lines, and wording that says the page has no URL of its
#: own. Read only when the page has no URL line.
_HEADER_PATH = re.compile(r"`(/[A-Za-z0-9._~\-/]*)`")
_SAME_PAGE = re.compile(r"\bsame\s+(?:url|page|tool)\b|\bin-page\b|\bno separate url\b", re.IGNORECASE)


def _header_block(body: str) -> str:
    """The page's header lines: everything before its first section heading or rule."""
    return re.split(r"\n\s*(?:#{2,4}\s|---)", body, maxsplit=1)[0]


def _sections(text: str) -> list[tuple[int, str, str]]:
    """(part number, heading, body) for each PART block, in document order."""
    matches = list(_PART_HEADING.finditer(text))
    out: list[tuple[int, str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((int(match.group(1)), match.group(2).strip(" *"), text[match.end():end].strip()))
    return out


def _emails(body: str) -> list[FunnelEmail]:
    matches = list(_EMAIL_HEADING.finditer(body))
    emails: list[FunnelEmail] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        copy = body[match.end():end].strip().rstrip("-").strip()
        subject = _SUBJECT_LINE.search(copy)
        title = match.group(1).strip(" *")
        emails.append(
            FunnelEmail(
                key=f"email-{i + 1}",
                title=title,
                subject=subject.group(1).strip("`* ") if subject else "",
                copy=copy,
            )
        )
    return emails


def parse_funnel_document(text: str) -> FunnelPlan:
    """The build plan for one approved Funnel document. `available` is False when no page copy
    could be found — a document written in some other shape is reported, never guessed at."""
    text = (text or "").replace("\r\n", "\n")
    parts = _sections(text)
    first = _PART_HEADING.search(text)
    preamble = text[: first.start()].strip() if first else ""
    title_match = re.search(r"^\s*#\s+(.+)$", preamble, re.MULTILINE)

    strategy = ""
    notes = ""
    emails: list[FunnelEmail] = []
    files: list[FunnelFile] = []
    used_keys: set[str] = set()

    for number, heading, body in parts:
        if number == 1 and not _IS_PAGE.search(heading):
            strategy = body
            continue
        if _IS_EMAILS.search(heading) and not _IS_PAGE.search(heading):
            emails = _emails(body)
            continue
        if _IS_NOTES.search(heading) and not _IS_PAGE.search(heading):
            notes = body
            continue
        if not _IS_PAGE.search(heading):
            continue  # the competitor analysis, or a part this build has no use for

        title = _PAGE_TITLE.match(heading).group(1).strip(" *—–-")  # type: ignore[union-attr]
        url_match = _URL_LINE.search(body)
        type_match = _TYPE_LINE.search(body)
        url_line = url_match.group(1) if url_match else ""
        screen = FunnelScreen(
            part=number,
            title=title,
            page_type=type_match.group(1).strip("`* ") if type_match else "",
            url_line=url_line.strip("`* "),
            copy=body,
        )
        path = _url_path(url_line) if url_line else None
        if path is None and not url_line:
            # No URL line. The header may still place the page: "Interactive scorecard (in-page,
            # same URL as entry: `/ai-seo-scorecard/`)" is a screen at that path.
            header = _header_block(body)
            named = _HEADER_PATH.search(header)
            if named:
                path = _url_path(named.group(1))
            elif _SAME_PAGE.search(header) and files:
                files[-1].screens.append(screen)
                continue
        if path is None and url_line and files:
            # "same tool instance (no separate URL …)": a screen of the file before it.
            files[-1].screens.append(screen)
            continue
        if path is None:
            path = f"/{_slug(title, f'page-{len(files) + 1}')}/"
        existing = next((f for f in files if f.url_path == path), None)
        if existing is not None:
            existing.screens.append(screen)
            continue
        key = _slug(path.strip("/").split("/")[-1] or "index", f"page-{len(files) + 1}")
        while key in used_keys:
            key += "-2"
        used_keys.add(key)
        files.append(FunnelFile(key=key, url_path=path, screens=[screen]))

    return FunnelPlan(
        title=title_match.group(1).strip(" *") if title_match else "Funnel",
        preamble=preamble,
        strategy=strategy,
        files=files,
        emails=emails,
        notes=notes,
    )


#: The hub document's own HTML (the mindmap and the ladder) is a visual for the operator; the page
#: builder needs the words. Fenced code is dropped, and the rest is capped.
_FENCE = re.compile(r"```.*?```", re.DOTALL)
HUB_CHARS = 14_000


def hub_routing_text(hub_document: str) -> str:
    """Funnel Hub & Media's selection, hub model and commitment ladder, as prose for the page
    builder: which offer every road converges on, and what cascades after it."""
    text = _FENCE.sub("", (hub_document or "").replace("\r\n", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > HUB_CHARS:
        text = text[:HUB_CHARS].rsplit("\n", 1)[0] + "\n\n[… the rest of the hub document is omitted]"
    return text
