"""The deployable funnel's pages, built by Manus.

Claude, handed DESIGN.md, builds a page in the client's design *language*: the right palette and
type, laid out its own way, with no motion. The deployable funnel has to be the client's site — same
look, same layout system, same animations — so its pages are built by a Manus agent, which works in
a sandbox with a browser: it opens the reference page itself, screenshots it at desktop and mobile,
reads the computed styles and the animation code, and reproduces them around the funnel's approved
copy (`Funnel_Manus_Fidelity.md`). The Pillar Page uses the same agent for the same reason.

- **One task for every page.** The pages are built side by side from one reading of the reference,
  so they share one design rather than drifting per page. A single page is rebuilt as its own task.
- **The brief is a file** (`brief.md`): the fidelity rules, the page build rules, every page's copy,
  the funnel map, the client's forms, the hub routing, DESIGN.md, the images. The reference
  screenshots travel as URL parts. `task.create` caps a message at ~5,000 tokens.
- **Files back by name.** One `<key>.html` per page and `build-notes.md` with a `## <key>` section
  each. A page Manus did not deliver is reported on its row; the others still ship.
- **Emails are not Manus's.** An HTML email is table layout with inline styles and no motion, so
  there is no reference fidelity to gain, and Claude builds them alongside (`funnel_build`).
- **Failure falls back to Claude** (the router), like the Pillar Page: the operator still gets pages.

`MANUS_FUNNEL_BUILDER=off` turns this off without removing the key.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from app.services import manus_client, manus_page
from app.services.funnel_build import (
    PAGE_PROMPT,
    BuildInputs,
    BuiltItem,
    check_built_page,
    funnel_map,
    _this_file,
)
from app.services.funnel_plan import FunnelFile
from app.services.generation import PAGE_BUILD_STANDARDS, _brand_token_block, _generated_images_block
from app.services.manus_client import ManusClient
from app.services.reference_forms import forms_markdown

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "Prompts"
FIDELITY_PROMPT = "Funnel_Manus_Fidelity.md"
BRIEF_FILE = "brief.md"
NOTES_FILE = "build-notes.md"
#: Several pages, each measured against the live reference: longer than one pillar page.
MAX_WAIT_SECONDS = 45 * 60

_NOTES_SECTION = re.compile(r"^##\s+`?([a-z0-9][a-z0-9\-]*)(?:\.html)?`?\s*$", re.MULTILINE | re.IGNORECASE)


def uses_manus() -> bool:
    if os.environ.get("MANUS_FUNNEL_BUILDER", "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    return manus_client.is_configured()


def _page_rules() -> str:
    """The page build rules, without the Claude prompt's OUTPUT FORMAT: Manus delivers files."""
    text = (_PROMPTS_DIR / PAGE_PROMPT).read_text(encoding="utf-8").strip()
    return text.split("\nOUTPUT FORMAT", 1)[0].strip()


def build_brief(inputs: BuildInputs, files: list[FunnelFile]) -> str:
    design = inputs.design
    parts = [
        "# Deployable funnel — build brief\n",
        f"## REFERENCE PAGE URL\n\n{inputs.reference_url or 'not on record — follow DESIGN.md and the attached screenshots'}\n",
        (_PROMPTS_DIR / FIDELITY_PROMPT).read_text(encoding="utf-8").strip() + "\n",
        "# PAGE BUILD RULES\n\n" + _page_rules() + "\n",
        "# FUNNEL STRATEGY\n\n" + "\n\n".join(p for p in (inputs.plan.preamble, inputs.plan.strategy) if p) + "\n",
        "# HUB ROUTING\n\n" + (inputs.hub_text or "No Funnel Hub & Media document on this run.") + "\n",
        "# CLIENT FORMS\n\n"
        + (forms_markdown(inputs.forms) or "No form could be read off the client's site. Follow Rule 4's no-form case.")
        + "\n",
        "# FILES TO BUILD\n",
    ]
    for current in files:
        parts.append(
            f"## File `{current.key}.html` — {current.title}\n\n"
            f"### FUNNEL MAP (links from this file)\n\n{funnel_map(inputs.plan, current, inputs.site_origin)}\n\n"
            f"### THIS FILE\n\n{_this_file(current, inputs.site_origin)}\n"
        )
    if design is not None:
        parts.append(_brand_token_block(design.design_md or design.theme_brief, has_screenshots=True, design_language=True))
        if design.generated_images:
            parts.append(_generated_images_block(design.generated_images))
    parts.append("\n# PAGE BUILD STANDARDS\n\n" + (_PROMPTS_DIR / PAGE_BUILD_STANDARDS).read_text(encoding="utf-8").strip())
    return "\n".join(parts)


def _instruction(files: list[FunnelFile]) -> str:
    names = ", ".join(f"`{f.key}.html`" for f in files)
    return (
        "You are building the pages of a lead-generation funnel for a marketing agency's client, as a "
        f"pixel-faithful extension of the client's own website. The attached {BRIEF_FILE} is the complete "
        "brief: the design-fidelity rules, the page build rules, every page's approved copy, the links, "
        "forms and images to use, and the client's measured design system. Open the REFERENCE PAGE URL in "
        "your browser and study it — screenshots at desktop and mobile, computed styles, and every "
        "animation and scroll effect — before writing any HTML. The attached reference screenshots show "
        "the same page. Do not invent a design: reproduce the reference.\n\n"
        f"Deliver these files, attached to your final message: {names} — each ONE complete, "
        "self-contained HTML document (inline <style>, at most one inline <script>, no frameworks, no "
        f"external CSS or JS other than the web fonts the brief names) — and `{NOTES_FILE}`, with one "
        "`## <file name without .html>` section per page holding that page's build notes as the brief's "
        "rules describe them, plus a **Design measured** list.\n\n"
        "Check every page in your browser at 1440px and 390px against the reference before you deliver. "
        "Do not publish, deploy or host a website. Do not ask questions: when something is unclear, decide "
        f"using the brief and the reference page, and note the decision in {NOTES_FILE}."
    )


def build_content(inputs: BuildInputs, files: list[FunnelFile], brief: str) -> list[dict[str, Any]]:
    parts = [
        manus_client.text_part(_instruction(files)),
        manus_client.file_part(BRIEF_FILE, brief.encode("utf-8"), "text/markdown"),
    ]
    for index, url in enumerate(inputs.reference_screenshots, start=1):
        parts.append(manus_client.url_part(url, f"reference-screenshot-{index}.png"))
    return parts


def split_notes(notes: str) -> dict[str, str]:
    """`build-notes.md` → {key: that page's section}. Text before the first page section (a shared
    "Design measured" list, say) is added to every page's notes."""
    matches = list(_NOTES_SECTION.finditer(notes or ""))
    shared = (notes[: matches[0].start()] if matches else notes or "").strip()
    out: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(notes)
        body = notes[match.end():end].strip()
        out[match.group(1).lower()] = "\n\n".join(p for p in (body, shared) if p)
    if not matches and shared:
        out["*"] = shared
    return out


def _finisher(inputs: BuildInputs, files: list[FunnelFile]) -> manus_page.Finisher:
    async def finish(client: ManusClient, events: list[manus_client.TaskEvent]) -> dict[str, Any]:
        attachments = [a for e in events if e.type == "assistant_message" for a in e.attachments]
        if not attachments:
            raise manus_page.ManusPageError("Manus stopped without attaching any of the funnel's pages.")

        def pick(name: str):
            found = [a for a in attachments if a.filename.lower().endswith(name.lower())]
            return found[-1] if found else None

        notes_file = pick(NOTES_FILE)
        notes = (await client.download(notes_file)).decode("utf-8", errors="replace") if notes_file else ""
        sections = split_notes(notes)
        items: list[dict] = []
        for current in files:
            item = BuiltItem(
                key=current.key, kind="page", title=current.title, file_path=current.file_path, url_path=current.url_path
            )
            found = pick(f"{current.key}.html")
            if found is None and len(files) == 1:
                html_files = [a for a in attachments if a.filename.lower().endswith((".html", ".htm"))]
                found = html_files[-1] if html_files else None
            if found is None:
                item.error = "Manus did not deliver this page. Rebuild it."
            else:
                item.html = (await client.download(found)).decode("utf-8", errors="replace").strip()
                item.notes = sections.get(current.key.lower()) or sections.get("*", "")
                check_built_page(item, inputs)
            items.append(item.to_dict())
        if all(i["error"] for i in items):
            raise manus_page.ManusPageError("Manus finished without delivering any of the funnel's pages.")
        return {"items": items}

    return finish


async def build_pages(
    inputs: BuildInputs,
    files: list[FunnelFile],
    *,
    run_id: str,
    client_factory: Callable[[], ManusClient] = ManusClient,
    poll_seconds: float = manus_page.POLL_SECONDS,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """Build `files` in one Manus task. Yields `{"type": "manus", ...}` progress, then one
    `{"type": "manus_result", "items": [...]}`. Raises `manus_page.ManusPageError` on failure.

    A reconnecting browser resumes the task already running for the same run and brief (`run_task`'s
    in-flight map), so a dropped stream never buys a second build."""
    brief = build_brief(inputs, files)
    key = "funnel:" + run_id + ":" + hashlib.sha256((brief + "|".join(inputs.reference_screenshots)).encode("utf-8")).hexdigest()
    async for event in manus_page.run_task(
        build_content(inputs, files, brief),
        key,
        _finisher(inputs, files),
        title=f"Funnel pages — {inputs.plan.title}"[:120],
        noun="the funnel's pages",
        client_factory=client_factory,
        poll_seconds=poll_seconds,
        max_wait_seconds=max_wait_seconds,
        nudge=(
            "Proceed with your best judgment and do not ask questions. Deliver every page's HTML file and "
            f"{NOTES_FILE}, attached to your final message."
        ),
    ):
        yield event
