"""Splitting an approved context document into sections that can be searched by meaning.

Pure text in, chunks out: no database, no API. `vector_context.py` decides what gets indexed and
when; this module only decides where a document is cut.

Cut at headings, never mid-table. A chunk records the heading chain it sits under
(`PART 2 > Pricing`), because a section's title is most of what tells a reader, or a retrieval
query, what it is about. Tables and lists stay whole up to `MAX_CHARS`, since a pricing table cut
in half answers neither half of the question.

Not everything is indexed (`indexable`):
- **HTML pages** are left out. A built page is laid out, not read, and the stages that need one take
  it whole.
- **Machine-read documents** are left out too (`SKIP_KEYS`): DESIGN.md, the replica template, the
  funnel build. Their consumers parse them, and a search hit inside one is noise.
- **Short documents** (`MIN_DOC_CHARS`) are left out. A one-line answer is already exact-matched
  by its key.

A fenced HTML block inside a prose document is replaced by a one-line marker, so a pillar page
pasted into a report doesn't turn into forty chunks of markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Aim for about 400 tokens per chunk: small enough that a hit is about one thing, large enough
#: that it carries its own context.
TARGET_CHARS = 1600
#: A single block (one table, one long list) may run to this before it is split by lines.
MAX_CHARS = 2600
#: A section shorter than this is folded into the next one rather than standing alone.
_MIN_FLUSH_CHARS = 500
MIN_DOC_CHARS = 200

SKIP_KEYS: frozenset[str] = frozenset(
    {
        "brand_design_tokens",
        "design_tokens",
        "page_replica_template",
        "page_replica_output",
        "funnel_build",
        "generated_images",
        "section_images",
        "social_post_sample",
        "industry_profile",
        "sub_service",
    }
)

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^(```|~~~)")
_HTML_DOC = re.compile(r"^\s*(?:<!doctype\s+html|<html[\s>])", re.IGNORECASE)
_HTML_TAG = re.compile(r"</?(?:div|section|body|head|html|style|script|span|a|p|img|nav|header|footer)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Chunk:
    index: int
    section_path: str
    text: str


def indexable(context_key: str, text: str) -> str | None:
    """None when this document should be indexed, otherwise why not (for the logs and the notes)."""
    if context_key in SKIP_KEYS:
        return "machine-read document"
    body = (text or "").strip()
    if len(body) < MIN_DOC_CHARS:
        return "too short to need searching"
    if _HTML_DOC.match(body) or (body.startswith("<") and len(_HTML_TAG.findall(body[:4000])) > 20):
        return "an HTML page"
    return None


def _strip_html_fences(lines: list[str]) -> list[str]:
    """Each fenced block that holds markup becomes one marker line; other fences are kept verbatim."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _FENCE.match(line.strip())
        if not match:
            out.append(line)
            i += 1
            continue
        fence = match.group(1)
        lang = line.strip()[len(fence):].strip().lower()
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith(fence):
            j += 1
        block = lines[i : j + 1]
        inner = "\n".join(block[1:-1])
        if lang in {"html", "htm", "xml", "svg"} or len(_HTML_TAG.findall(inner)) > 5:
            out.append("[HTML page omitted from search]")
        else:
            out.extend(block)
        i = j + 1
    return out


def _blocks(lines: list[str]) -> list[tuple[str, int, str]]:
    """The document as `(kind, heading level, text)` blocks: headings, and runs of non-blank lines.
    A fenced block is one block even when it holds blank lines."""
    blocks: list[tuple[str, int, str]] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            blocks.append(("text", 0, "\n".join(current).strip("\n")))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if _FENCE.match(stripped):
            in_fence = not in_fence
            current.append(line)
            continue
        if in_fence:
            current.append(line)
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            blocks.append(("heading", len(heading.group(1)), heading.group(2).strip().strip("*").strip()))
            continue
        if not stripped:
            flush()
            continue
        current.append(line.rstrip())
    flush()
    return blocks


def _split_long(text: str) -> list[str]:
    """A block longer than `MAX_CHARS`, cut at line ends, and a line longer than that at sentences."""
    pieces: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > MAX_CHARS:
            cut = line.rfind(". ", 0, MAX_CHARS)
            cut = cut + 1 if cut > MAX_CHARS // 2 else MAX_CHARS
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(line[:cut].strip())
            line = line[cut:].strip()
        if buf and len(buf) + len(line) + 1 > MAX_CHARS:
            pieces.append(buf)
            buf = ""
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p.strip()]


def chunk_document(text: str) -> list[Chunk]:
    """The document as chunks, in document order. Every word of the input outside an HTML fence
    appears in exactly one chunk; headings appear both as text and in `section_path`."""
    lines = _strip_html_fences((text or "").replace("\r\n", "\n").split("\n"))
    path: list[tuple[int, str]] = []
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_path = ""

    def path_str() -> str:
        return " > ".join(title for _, title in path)

    def flush() -> None:
        nonlocal buf, buf_path
        body = "\n\n".join(buf).strip()
        if body:
            chunks.append(Chunk(index=len(chunks), section_path=buf_path, text=body))
        buf = []
        buf_path = path_str()

    for kind, level, block in _blocks(lines):
        if kind == "heading":
            # A new section starts a new chunk unless the current one is too small to stand alone.
            if sum(len(b) for b in buf) >= _MIN_FLUSH_CHARS:
                flush()
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, block))
            if not buf:
                buf_path = path_str()
            buf.append("#" * level + " " + block)
            continue

        for piece in _split_long(block) if len(block) > MAX_CHARS else [block]:
            size = sum(len(b) + 2 for b in buf)
            if buf and size + len(piece) > TARGET_CHARS and size >= _MIN_FLUSH_CHARS:
                flush()
            if not buf:
                buf_path = path_str()
            buf.append(piece)
    flush()
    return chunks
