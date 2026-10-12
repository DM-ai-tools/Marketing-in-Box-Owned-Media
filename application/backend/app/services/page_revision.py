"""Revising a draft by exact edits, never by retyping it — and, for a business fix, never its layout.

Why this exists
---------------
"Refine" and "Fix with Refine" used to send the previous draft and the request, and ask for "the
FULL revised document". On a built page that re-flowed everything: sections moved, copy was
reworded, components changed, and the page no longer traced back to the CRO rewrite it came from.

The first fix stopped the retyping: the model returns exact `find` / `replace` edits and
`apply_edits` places them on the previous draft. Every byte no edit names stays as it was.

That was not enough, and it broke a second time. The business check's findings themselves ask for
layout changes: a fixed width, a sub-menu rule, an off-palette colour, an image source that is not
on record. The model obeyed, one placed edit at a time, and each edit was placed exactly where it
said. The CSS moved the logo and the icons, swapped image sources moved pictures between sections,
and enough medium-sized edits added up to a different page. An edit can land in the right place and
still do the wrong thing.

So a business fix is **layout-locked**, and the lock is measured, not requested:
  - `layout_fingerprint` reads the skeleton of every HTML page in the draft — every non-text
    element in order with its classes, ids, styles and image sources, plus the `<style>` and
    `<script>` text. For a Markdown document it is the heading outline.
  - An edit whose result changes that fingerprint is rejected and reported, whatever the model
    meant by it. Copy, link targets, alt text and the removal of a paragraph or list item stay
    possible: those live in text-level elements, which the fingerprint looks through.
  - All edits together may not change more than `MAX_TOTAL_SHARE` of the draft.
  - The model is told the rules, and lists the findings it left alone in `skipped`, which are
    reported too.

An ordinary Refine of a page is still edit-only, but not layout-locked: the operator may be asking
for a layout change in their own words. An ordinary Refine of a prose document is a full revision,
as before (`build_revision_prompt`); rewriting prose is what that request is for.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.services.html_dom import Node, parse_html
from app.services.industry_voice import IndustryProfile, voice_block

logger = logging.getLogger(__name__)

#: A single edit may not replace more than this share of the draft. Past it, the "edit" is a
#: rewrite, which is what this module exists to prevent; re-running the stage is how a page is
#: rebuilt.
MAX_EDIT_SHARE = 0.35
#: All edits together may not replace more than this share of the draft.
MAX_TOTAL_SHARE = 0.5
#: The CRO copy and DESIGN.md are references for the edits, not documents to reproduce.
_SOURCE_COPY_CHARS = 60_000
_DESIGN_MD_CHARS = 16_000

_FENCED_HTML = re.compile(r"```html[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")

#: Elements that carry copy, not layout. The fingerprint looks through them, so their text, their
#: link targets and whether a paragraph or list item exists can change under a business fix.
#: Anything inside them that *is* layout — an image in a link, an icon in a list item — is still
#: in the fingerprint.
_TEXT_LEVEL = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del", "dfn", "em", "i",
        "ins", "kbd", "li", "mark", "p", "q", "s", "samp", "small", "span", "strong", "sub",
        "sup", "time", "u", "var", "wbr", "title", "meta",
    }
)
#: Attributes a business fix may change on any element: what a link points to, what an image
#: says, the page's meta description.
_FREE_ATTRS = frozenset({"href", "alt", "title", "aria-label", "content", "datetime", "target", "rel"})


def has_html_page(draft: str) -> bool:
    lowered = draft.lower()
    return "```html" in lowered or "<html" in lowered or "<!doctype html" in lowered


def uses_edit_revision(asset_id: str, previous_draft: str, *, business_fix: bool) -> bool:
    """Edits, not a retyped document: every business fix, and every Refine of a draft with a page."""
    return business_fix or has_html_page(previous_draft)


# ----------------------------------------------------------------------------------------------
# The layout fingerprint
# ----------------------------------------------------------------------------------------------


def _is_icon(node: Node) -> bool:
    """A text-level element used as an icon: classed or styled, with no text of its own
    (`<i class="fa fa-phone"></i>`). Swapping one changes what the page shows."""
    if not (node.attr("class") or node.attr("style")):
        return False
    return not any(child.is_text and child.text.strip() for child in node.walk())


def _html_fingerprint(html: str) -> tuple:
    try:
        document = parse_html(html)
    except Exception:  # noqa: BLE001 — an unparseable page compares by its text
        return ("unparsed", html)
    out: list[tuple] = []

    def walk(node: Node, depth: int) -> None:
        for child in node.children:
            if child.is_text:
                if child.raw:  # <style> / <script> bodies are layout and behaviour, never copy
                    out.append(("raw", depth, child.text.strip()))
                continue
            if not child.is_element:
                continue
            if child.tag in _TEXT_LEVEL and not _is_icon(child):
                walk(child, depth)
                continue
            attrs = tuple(sorted((k, v or "") for k, v in child.attrs.items() if k not in _FREE_ATTRS))
            out.append((depth, child.tag, attrs))
            walk(child, depth + 1)

    walk(document.root, 0)
    return tuple(out)


def _markdown_outline(text: str) -> tuple[str, ...]:
    """The heading lines outside fenced blocks: a prose document's structure."""
    outline: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        mark = _FENCE.match(line)
        if mark:
            fence = None if fence == mark.group(1)[0] else (fence or mark.group(1)[0])
            continue
        if fence is None and _MD_HEADING.match(line):
            outline.append(" ".join(line.split()))
    return tuple(outline)


def layout_fingerprint(text: str) -> tuple:
    """What a business fix must leave alone: the heading outline, and every HTML page's skeleton."""
    pages = _FENCED_HTML.findall(text)
    if not pages and has_html_page(text):
        pages = [text]
    return (_markdown_outline(text), tuple(_html_fingerprint(page) for page in pages))


# ----------------------------------------------------------------------------------------------
# Edits
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    find: str
    replace: str
    why: str = ""


@dataclass
class EditReport:
    applied: int = 0
    #: (start of the `find` text, why it was not applied)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    #: (finding, why) — what the model left alone on purpose, by its own account.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: The reply held no readable edit list at all.
    unreadable: bool = False
    #: Whether the layout was locked for this revision (every business fix).
    layout_locked: bool = False

    def as_event(self) -> dict:
        return {
            "type": "revision_edits",
            "applied": self.applied,
            "rejected": [list(row) for row in self.rejected],
            "skipped": [list(row) for row in self.skipped],
            "unreadable": self.unreadable,
            "layout_locked": self.layout_locked,
        }


def build_edit_prompt(
    previous_draft: str,
    note: str,
    *,
    voice: IndustryProfile | None = None,
    business_facts: str | None = None,
    source_copy: str | None = None,
    design_md: str | None = None,
    lock_layout: bool = False,
) -> str:
    sources = ""
    if source_copy and source_copy.strip():
        sources += (
            "----- THE PAGE'S COPY (the approved CRO page rewrite this page was built from) -----\n"
            f"{source_copy.strip()[:_SOURCE_COPY_CHARS]}\n"
            "----- END THE PAGE'S COPY -----\n\n"
        )
    if design_md and design_md.strip():
        sources += (
            "----- DESIGN.md (the client's measured palette, type and components) -----\n"
            f"{design_md.strip()[:_DESIGN_MD_CHARS]}\n"
            "----- END DESIGN.md -----\n\n"
        )
    facts = (
        "----- THE CLIENT'S BUSINESS (the only source of truth for offers, prices and claims) -----\n"
        f"{business_facts.strip()}\n"
        "----- END THE CLIENT'S BUSINESS -----\n\n"
        if business_facts and business_facts.strip()
        else ""
    )
    layout_rules = (
        "- THE LAYOUT IS LOCKED. Change only wording, link targets (href), alt text and the meta "
        "description, and remove a paragraph or list item where a claim must go. Do not add, move, "
        "remove or re-tag any section, container, heading, image, icon, logo, button or form. Do "
        "not change any class, id, style, CSS rule, script or image source. Code checks every edit "
        "and rejects one that changes the layout, so such an edit is wasted.\n"
        "- A finding that can only be fixed by changing the layout (a CSS rule, a width, a colour, "
        "an image, the menu's structure) is left alone: put it in `skipped` with the reason.\n"
        if lock_layout
        else "- Do not move, reorder, merge or restyle sections, and do not change layout the request "
        "does not ask about.\n"
    )
    return voice_block(voice) + (
        "You are revising a document by returning EDITS to it. You do not rewrite or re-emit the "
        "document: the edits are applied to the exact text below by code, and everything they do "
        "not name stays exactly as it is.\n\n"
        "----- PREVIOUS DRAFT -----\n"
        f"{previous_draft}\n"
        "----- END PREVIOUS DRAFT -----\n\n"
        f"{sources}{facts}"
        "The operator reviewed this draft and requested the following change:\n"
        f'"{note.strip()}"\n\n'
        "RULES\n"
        "- Make only the changes the request needs. Do not reword anything the request does not "
        "mention.\n"
        f"{layout_rules}"
        "- Wording you add or change comes from THE PAGE'S COPY or THE CLIENT'S BUSINESS above where "
        "they are given. Do not invent offers, prices, results or claims. Where something needed is "
        "not on record, remove or soften the statement instead of inventing a replacement.\n"
        "- A [CLIENT TO CONFIRM: ...] marker the request gives no answer for is removed together "
        "with the claim it guards. Never assert the unconfirmed claim.\n"
        "- Links use the hrefs listed in the client's business (the Links section), never '#' or a "
        "guessed URL.\n"
        "- Each `find` is copied character for character from PREVIOUS DRAFT, whitespace included, "
        "and is long enough to occur exactly once. Keep it as short as uniqueness allows. Never put "
        "a whole section or the whole document in one `find`.\n"
        "- When the same wording appears in more than one place (notes and page), edit each.\n\n"
        "OUTPUT\n"
        "Return only this JSON object, with no commentary before or after it:\n"
        '{"edits": [{"find": "<exact text from the draft>", "replace": "<what replaces it>", '
        '"why": "<which part of the request this serves>"}], '
        '"skipped": [{"finding": "<the request item left alone>", "why": "<why>"}]}\n'
        'Return {"edits": [], "skipped": []} when nothing needs to change.'
    )


def _load(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.info("Revision reply could not be parsed as JSON: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def parse_edits(raw: str) -> list[Edit] | None:
    """The edit list out of a reply, or None when there is no readable list at all.

    Tolerant of a fenced block, preamble and trailing commentary. An item missing `find` is
    skipped; one missing `replace` is a deletion only if it says so with an empty string.
    """
    data = _load(raw)
    items = data.get("edits") if data else None
    if not isinstance(items, list):
        return None
    edits: list[Edit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        find, replace = item.get("find"), item.get("replace")
        if not isinstance(find, str) or not find or not isinstance(replace, str):
            continue
        edits.append(Edit(find=find, replace=replace, why=str(item.get("why") or "")))
    return edits


def parse_skipped(raw: str) -> list[tuple[str, str]]:
    data = _load(raw)
    items = data.get("skipped") if data else None
    out: list[tuple[str, str]] = []
    for item in items if isinstance(items, list) else ():
        if isinstance(item, dict) and str(item.get("finding") or "").strip():
            out.append((str(item["finding"]).strip()[:200], str(item.get("why") or "").strip()[:300]))
    return out


def _locate(text: str, find: str) -> tuple[int, int] | str:
    """The one span `find` occupies in `text`, or why there is not exactly one.

    Exact first. Then whitespace-tolerant, because a model copying a long indented block reliably
    gets the line breaks and indentation slightly wrong while getting every word right; the words
    still have to match in order and occur once.
    """
    count = text.count(find)
    if count == 1:
        start = text.index(find)
        return start, start + len(find)
    if count > 1:
        return f"appears {count} times in the draft — it needs more surrounding text to be unique"
    words = find.split()
    if not words:
        return "is empty"
    # `\s*`, not `\s+`: a model often adds a line break between two tags the draft wrote touching.
    pattern = re.compile(r"\s*".join(re.escape(word) for word in words))
    matches = list(pattern.finditer(text))
    if len(matches) == 1:
        return matches[0].span()
    if len(matches) > 1:
        return f"appears {len(matches)} times in the draft — it needs more surrounding text to be unique"
    return "is not in the draft"


def apply_edits(draft: str, edits: list[Edit], *, lock_layout: bool = False) -> tuple[str, EditReport]:
    """Apply `edits` in order, each against the text the edits before it produced.

    With `lock_layout`, an edit whose result changes `layout_fingerprint` is rejected — the check
    is on the outcome, so it holds however the edit was phrased.
    """
    report = EditReport(layout_locked=lock_layout)
    text = draft
    per_edit = max(1, int(len(draft) * MAX_EDIT_SHARE))
    budget = max(1, int(len(draft) * MAX_TOTAL_SHARE))
    changed = 0
    locked = layout_fingerprint(draft) if lock_layout else None
    for edit in edits:
        excerpt = " ".join(edit.find.split())[:120]
        if edit.find == edit.replace:
            continue
        located = _locate(text, edit.find)
        if isinstance(located, str):
            report.rejected.append((excerpt, f"Not applied: the text to change {located}."))
            continue
        start, end = located
        size = max(end - start, len(edit.replace))
        if end - start > per_edit:
            report.rejected.append(
                (excerpt, "Not applied: it would replace too much in one edit. Re-run the stage to rebuild it.")
            )
            continue
        if changed + size > budget:
            report.rejected.append(
                (excerpt, "Not applied: together the edits would change too much of the document. Re-run the stage instead.")
            )
            continue
        candidate = text[:start] + edit.replace + text[end:]
        if locked is not None and layout_fingerprint(candidate) != locked:
            report.rejected.append(
                (
                    excerpt,
                    "Not applied: it would change the page's layout (a section, image, icon, logo, "
                    "class, style or the CSS). Fix with Refine changes copy and links only; change "
                    "layout with an ordinary Refine that asks for it, or re-run the stage.",
                )
            )
            continue
        text = candidate
        changed += size
        report.applied += 1
    return text, report
