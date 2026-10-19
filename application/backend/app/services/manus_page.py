"""The Pillar Page, built by Manus.

Claude used to write the pillar page in one streamed call. With a Manus key set, the stage's HTML is
built by a Manus agent instead: it gets the *same* brief Claude would have got (the master prompt,
DESIGN.md, the CRO copy, the business brief, the generated image URLs and the reference
screenshots), works in its own sandbox where it can open the page it is writing, and hands back two
files. The stage document is put back together from them in the shape the prompt's OUTPUT FORMAT
already specifies, so everything downstream — the card's preview, the page check, Refine, the
Context Store — reads it exactly as it read Claude's.

- **The brief is a file, not the message.** `task.create` caps a request's text at about 5,000
  tokens and the pillar brief is several times that, so the message is a short instruction and the
  brief travels as `brief.md`. Screenshots go as URL file parts.
- **Two files back.** `page.html` is the complete page; `report.md` is every other part of the
  output format, with `PAGE_MARKER` standing where the page goes. `compose_document` puts the page
  back in a ```html fence at the marker. A missing marker puts it under its own heading; a missing
  `page.html` falls back to a fence inside `report.md`; neither is a failure.
- **No questions, no website.** `interactive_mode` is off and the instruction says not to publish.
  If the agent waits anyway it is told to proceed, at most `_MAX_NUDGES` times.
- **A dropped connection does not start a second task.** The task id is held per brief
  (`_inflight`), so the browser's automatic retry resumes polling the task already paid for.
- **Failure is reported, then Claude builds the page.** `ManusPageError` carries one sentence;
  the router shows it and falls back to the ordinary stream, so the operator still gets a page.

`MANUS_PAGE_BUILDER=off` turns this off without removing the key.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.services import manus_client
from app.services.manus_client import Attachment, ManusClient, ManusError

logger = logging.getLogger(__name__)

#: The stages Manus builds. The pillar page is the one stage whose deliverable is a whole page.
MANUS_PAGE_STAGES: frozenset[str] = frozenset({"pillar_page"})

#: Every pillar page task is created in this Manus project, and only this one. Fixed by the
#: product owner, not configurable: an env override would let a deployment file client pages under
#: some other project. Checked live with `project.detail`: it exists on the account's key, has no
#: project instruction, and its default connectors and skills apply to the task.
MANUS_PROJECT_NAME = "MIAB - Landing Page"
MANUS_PROJECT_ID = "mXepyiMV6XxnDz3oTcQ3kr"

PAGE_FILE = "page.html"
REPORT_FILE = "report.md"
BRIEF_FILE = "brief.md"
PAGE_MARKER = "{{PAGE_HTML}}"

POLL_SECONDS = 10.0
#: A pillar page is a long build; past this the task is stopped and Claude builds the page.
MAX_WAIT_SECONDS = 30 * 60
_MAX_NUDGES = 2

_INSTRUCTION = f"""You are building one web page for a marketing agency's client. The attached \
{BRIEF_FILE} is the complete brief: the master prompt, the client's measured design system \
(DESIGN.md), the approved page copy, the rules the page is checked against, and the real image URLs \
to use. Follow it exactly. Where the brief and these delivery instructions differ, these delivery \
instructions decide only how the files are delivered; the brief decides everything about the page.

Deliver exactly two files, attached to your final message:

1. `{PAGE_FILE}` — the complete page as ONE self-contained HTML document: inline <style>, any \
<script> inline, no build step, no framework, no external CSS other than web fonts the brief names. \
Use only the image URLs and links the brief supplies. Open it in your browser and check it at \
desktop and mobile widths before you deliver it.
2. `{REPORT_FILE}` — every other part of the brief's OUTPUT FORMAT, in Markdown, in the order it \
specifies. In the part that holds the designed page, write the single line {PAGE_MARKER} where the \
page goes instead of repeating the HTML.

Do not publish, deploy or host a website. Do not ask questions: when something is unclear, decide \
using the brief and note the decision in {REPORT_FILE}."""

_NUDGE = (
    f"Proceed with your best judgment and do not ask questions. Deliver {PAGE_FILE} and "
    f"{REPORT_FILE} as described, attached to your final message."
)


class ManusPageError(RuntimeError):
    """The page could not be built by Manus. One sentence, shown to the operator."""


def uses_manus(asset_id: str) -> bool:
    if asset_id not in MANUS_PAGE_STAGES:
        return False
    if os.environ.get("MANUS_PAGE_BUILDER", "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    return manus_client.is_configured()


def build_content(brief: str, screenshot_urls: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    parts = [
        manus_client.text_part(_INSTRUCTION),
        manus_client.file_part(BRIEF_FILE, brief.encode("utf-8"), "text/markdown"),
    ]
    for index, url in enumerate(screenshot_urls, start=1):
        parts.append(manus_client.url_part(url, f"reference-screenshot-{index}.png"))
    return parts


_HTML_FENCE = re.compile(r"```html\s*\n", re.IGNORECASE)


def compose_document(report: str | None, html: str | None) -> str:
    """The stage document, in the shape Claude's stream produced: the report with the page fenced
    where the marker stands."""
    report = (report or "").strip()
    if not html or not html.strip():
        if report and _HTML_FENCE.search(report):
            return report + "\n"
        raise ManusPageError("Manus finished without delivering the page (no page.html was attached).")
    fenced = "```html\n" + html.strip() + "\n```"
    if not report:
        return fenced + "\n"
    if PAGE_MARKER in report:
        return report.replace(PAGE_MARKER, fenced, 1) + "\n"
    return report + "\n\n## Full designed page\n\n" + fenced + "\n"


def _pick(attachments: list[Attachment], name: str, suffixes: tuple[str, ...]) -> Attachment | None:
    exact = [a for a in attachments if a.filename.lower().endswith(name)]
    if exact:
        return exact[-1]
    loose = [a for a in attachments if a.filename.lower().endswith(suffixes)]
    return loose[-1] if loose else None


def _delivered(events: list[manus_client.TaskEvent]) -> tuple[Attachment | None, Attachment | None, str]:
    """The page and report the agent attached (latest wins), and its last words."""
    attachments: list[Attachment] = []
    last_text = ""
    for event in events:
        if event.type == "assistant_message":
            attachments.extend(event.attachments)
            if event.content.strip():
                last_text = event.content.strip()
    page = _pick(attachments, PAGE_FILE, (".html", ".htm"))
    report = _pick(attachments, REPORT_FILE, (".md", ".markdown"))
    return page, report, last_text


def _last_error(events: list[manus_client.TaskEvent]) -> str:
    for event in reversed(events):
        if event.type == "error_message" and event.content.strip():
            return event.content.strip()
    return ""


def _latest_brief(events: list[manus_client.TaskEvent]) -> str:
    """What the agent says it is doing. Its own latest message beats the status line, which live is
    only ever "Manus is running"."""
    for event in reversed(events):
        if event.type == "assistant_message" and event.content.strip():
            line = event.content.strip().splitlines()[0]
            return line if len(line) <= 160 else line[:157].rstrip() + "…"
        if event.type == "status_update" and event.brief.strip():
            return event.brief.strip()
    return ""


@dataclass
class _Inflight:
    task_id: str
    task_url: str
    started: float


#: Brief hash → the task building it, so a reconnecting browser resumes rather than re-buys.
_inflight: dict[str, _Inflight] = {}


def _brief_key(brief: str, screenshots: tuple[str, ...]) -> str:
    return hashlib.sha256((brief + "\n".join(screenshots)).encode("utf-8")).hexdigest()


def _progress(status: str, task: _Inflight, **extra: Any) -> dict[str, Any]:
    return {
        "type": "manus",
        "status": status,
        "task_url": task.task_url,
        "elapsed": int(time.monotonic() - task.started),
        **extra,
    }


#: What a finished task is turned into: given the client and the task's events, the result fields
#: (merged into `{"type": "manus_result", ...}`). Raises `ManusPageError` when nothing usable came back.
Finisher = Callable[[ManusClient, list[manus_client.TaskEvent]], Awaitable[dict[str, Any]]]


async def run_task(
    content: list[dict[str, Any]],
    key: str,
    finish: Finisher,
    *,
    title: str = "",
    noun: str = "the page",
    client_factory: Callable[[], ManusClient] = ManusClient,
    poll_seconds: float = POLL_SECONDS,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
    nudge: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Create (or resume, by `key`) one Manus task in the MIAB project and see it through.

    Yields `{"type": "manus", ...}` progress events, then exactly one `{"type": "manus_result", ...}`
    carrying `finish`'s fields, the task link and the credits. Raises `ManusPageError` on failure.
    Shared by the Pillar Page (`build_page`) and the deployable funnel (`manus_funnel`), so both get
    the same nudging, timeout, and resume-on-reconnect rules.
    """
    try:
        client = client_factory()
    except ManusError as exc:
        raise ManusPageError(str(exc)) from exc

    try:
        task = _inflight.get(key)
        if task is not None and time.monotonic() - task.started < max_wait_seconds:
            yield _progress("resumed", task, brief="Reconnected to the build already running")
        else:
            try:
                created = await client.create_task(content, title=title, project_id=MANUS_PROJECT_ID)
            except ManusError as exc:
                raise ManusPageError(str(exc)) from exc
            task = _Inflight(created.task_id, created.task_url, time.monotonic())
            _inflight[key] = task
            logger.info("Manus task created task_id=%s for %s", task.task_id, noun)
            yield _progress("started", task, brief="Manus is reading the brief")

        nudges = 0
        last_brief = ""
        while True:
            await asyncio.sleep(poll_seconds)
            if time.monotonic() - task.started > max_wait_seconds:
                await client.stop(task.task_id)
                _inflight.pop(key, None)
                raise ManusPageError(
                    f"Manus had not finished {noun} after {int(max_wait_seconds // 60)} minutes, so the task was stopped."
                )
            try:
                detail = await client.detail(task.task_id)
                events = await client.all_messages(task.task_id)
            except ManusError as exc:
                # A rate limit or a blip on one poll is not the build failing: poll again.
                logger.warning("Manus poll failed for %s: %s", task.task_id, exc)
                continue

            brief_line = _latest_brief(events) or last_brief
            last_brief = brief_line
            yield _progress(detail.status or "running", task, brief=brief_line, credits=detail.credit_usage)

            if detail.status == "error":
                _inflight.pop(key, None)
                reason = _last_error(events) or "the task ended with an error"
                raise ManusPageError(f"Manus could not build {noun}: {reason}")
            if detail.status == "waiting":
                if nudges >= _MAX_NUDGES:
                    await client.stop(task.task_id)
                    _inflight.pop(key, None)
                    raise ManusPageError("Manus kept stopping to ask for input, so the build was stopped.")
                nudges += 1
                await client.send_message(task.task_id, nudge or _NUDGE)
                continue
            if detail.status != "stopped" or detail.background:
                continue

            try:
                result = await finish(client, events)
            except ManusError as exc:
                _inflight.pop(key, None)
                raise ManusPageError(str(exc)) from exc
            except ManusPageError:
                _inflight.pop(key, None)
                raise
            _inflight.pop(key, None)
            logger.info("Manus task done task_id=%s credits=%s", task.task_id, detail.credit_usage)
            yield {"type": "manus_result", **result, "task_url": task.task_url, "credits": detail.credit_usage}
            return
    finally:
        await client.aclose()


async def _finish_page(client: ManusClient, events: list[manus_client.TaskEvent]) -> dict[str, Any]:
    page, report, last_text = _delivered(events)
    if page is None and report is None:
        said = f' Its last message: "{last_text[:200]}"' if last_text else ""
        raise ManusPageError(f"Manus stopped without attaching the page.{said}")
    html = (await client.download(page)).decode("utf-8", errors="replace") if page else None
    report_text = (await client.download(report)).decode("utf-8", errors="replace") if report else None
    return {"document": compose_document(report_text, html)}


async def build_page(
    brief: str,
    screenshot_urls: tuple[str, ...] = (),
    *,
    title: str = "",
    client_factory: Callable[[], ManusClient] = ManusClient,
    poll_seconds: float = POLL_SECONDS,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """Run the build. Yields `{"type": "manus", ...}` progress events, then exactly one
    `{"type": "manus_result", "document": str, ...}`. Raises `ManusPageError` on failure."""
    async for event in run_task(
        build_content(brief, screenshot_urls),
        _brief_key(brief, screenshot_urls),
        _finish_page,
        title=title,
        client_factory=client_factory,
        poll_seconds=poll_seconds,
        max_wait_seconds=max_wait_seconds,
    ):
        yield event
