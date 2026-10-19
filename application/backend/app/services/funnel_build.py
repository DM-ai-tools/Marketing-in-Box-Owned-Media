"""The deployable funnel: the Funnel stage's approved pages and emails, built as files.

The Funnel stage writes final copy for every page of one funnel and its follow-up emails; Funnel Hub
& Media decides which funnels the client runs and where they all converge. Neither produces a file
anyone can publish. This builds them, once both are approved and the operator asks:

1. **Plan** (`funnel_plan.parse_funnel_document`) — one HTML file per URL the funnel names, several
   screens in one file where the funnel runs them at one URL, and the emails.
2. **Forms** (`reference_forms.read_forms`) — the working forms on the client's own reference page
   and on the page its main call to action leads to, so every funnel form posts somewhere real.
3. **Build** — one Claude call per file and per email, run concurrently (`MAX_PARALLEL`). Each page
   gets the same design-language inputs as the Pillar Page — the full DESIGN.md under its binding
   directive, the page-build standards, the run's hosted images, the reference screenshots — plus
   its own copy, the funnel map and the hub routing. Each email gets the theme brief.
4. **Check** (`page_check.check_page`) — every page, against the reference page's links.
5. **Bundle** (`bundle_zip`) — every page at its funnel URL path, images written in
   (`standalone_html`), the emails, and a README.

A file that fails to build is reported on its own; the others still ship. Nothing here writes copy:
the prompts carry the approved words verbatim and say what to do with a placeholder (leave it out,
list it).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.services import page_check
from app.services.claude_client import get_client
from app.services.funnel_plan import FunnelEmail, FunnelFile, FunnelPlan
from app.services.generation import (
    DEFAULT_EFFORT,
    EFFORT_CAPABLE_MODELS,
    PAGE_BUILD_STANDARDS,
    SONNET,
    PageDesignInput,
    _brand_token_block,
    _generated_images_block,
)
from app.services.link_manifest import LinkManifest
from app.services.reference_forms import ReferenceForm, forms_markdown
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "Prompts"
PAGE_PROMPT = "Funnel_Page_Build_Prompt.md"
EMAIL_PROMPT = "Funnel_Email_Build_Prompt.md"

MODEL = SONNET
#: A funnel page is a whole page, like the Pillar Page's, and thinking bills against the same ceiling.
PAGE_MAX_TOKENS = 64_000
EMAIL_MAX_TOKENS = 16_000
MAX_PARALLEL = 4

#: The build's own asset id: what the usage rows and the stored context entry are filed under.
ASSET_ID = "funnel_build"
CONTEXT_KEY = "funnel_build"

OnUsage = Callable[[CallUsage], Awaitable[None]]
OnEvent = Callable[[dict], Awaitable[None]]

_FENCE = re.compile(r"```html\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class BuildInputs:
    """Everything a build reads, resolved by the router from the run."""

    plan: FunnelPlan
    design: PageDesignInput | None
    manifest: LinkManifest | None
    forms: list[ReferenceForm]
    hub_text: str
    #: The client's site origin (`https://clicktrends.com.au`), for canonical URLs and email links.
    site_origin: str
    #: The run's own images — generated, the hosted logo, copied page images — for the page check.
    known_images: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The page the design is read from, for a builder that can open it (`manus_funnel`).
    reference_url: str = ""
    #: Every captured screenshot of it, the full-page one included (Manus is not bound by the model
    #: API's 8000px limit that `PageDesignInput.screenshot_urls` is filtered for).
    reference_screenshots: tuple[str, ...] = ()


@dataclass
class BuiltItem:
    key: str
    kind: str  # "page" | "email"
    title: str
    #: Path inside the bundle (`social-media-audit/request/index.html`, `emails/email-1.html`).
    file_path: str
    url_path: str = ""
    subject: str = ""
    html: str = ""
    notes: str = ""
    score: int | None = None
    verdict: str = ""
    issues: list[dict] = field(default_factory=list)
    error: str = ""
    stop_reason: str | None = None
    #: Every change the operator asked for on this file: {"note", "applied"}.
    revisions: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        """Everything but the HTML: what a progress event and the card carry."""
        return {
            "key": self.key,
            "kind": self.kind,
            "title": self.title,
            "file_path": self.file_path,
            "url_path": self.url_path,
            "subject": self.subject,
            "notes": self.notes,
            "score": self.score,
            "verdict": self.verdict,
            "issues": self.issues,
            "error": self.error,
            "revisions": self.revisions,
        }

    def to_dict(self) -> dict:
        return {**self.summary(), "html": self.html}

    @classmethod
    def from_dict(cls, value: dict) -> "BuiltItem":
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in value.items() if k in names})


# --------------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------------


def _read_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def _block(title: str, body: str) -> str:
    return f"===== BEGIN {title} =====\n{body.strip()}\n===== END {title} =====\n\n" if body.strip() else ""


def funnel_map(plan: FunnelPlan, current: FunnelFile, site_origin: str) -> str:
    rows = ["| File | URL on the client's site | Link from this file |", "|---|---|---|"]
    for other in plan.files:
        here = " (this file)" if other is current else ""
        rows.append(f"| {other.title}{here} | {site_origin}{other.url_path} | `{current.href_to(other)}` |")
    return "\n".join(rows)


def _this_file(current: FunnelFile, site_origin: str) -> str:
    head = [
        f"URL on the client's site (canonical): {site_origin}{current.url_path}",
        f"Screens in this file: {len(current.screens)}",
        "",
    ]
    for i, screen in enumerate(current.screens, 1):
        head += [f"## Screen {i}: {screen.title}", f"Page type: {screen.page_type or 'not stated'}", "", screen.copy, ""]
    return "\n".join(head)


def build_page_request(inputs: BuildInputs, current: FunnelFile) -> list[dict]:
    """The user content for one page: screenshots first (as the Pillar Page sends them), then the
    prompt and its blocks."""
    design = inputs.design
    parts = [_read_prompt(PAGE_PROMPT), "\n\n"]
    parts.append(_block("THIS FILE", _this_file(current, inputs.site_origin)))
    parts.append(_block("FUNNEL MAP", funnel_map(inputs.plan, current, inputs.site_origin)))
    parts.append(_block("FUNNEL STRATEGY", "\n\n".join(p for p in (inputs.plan.preamble, inputs.plan.strategy) if p)))
    parts.append(_block("HUB ROUTING", inputs.hub_text or "No Funnel Hub & Media document on this run."))
    parts.append(
        _block(
            "CLIENT FORMS",
            forms_markdown(inputs.forms) or "No form could be read off the client's site. Follow Rule 4's no-form case.",
        )
    )
    if design is not None:
        sheet = design.design_md or design.theme_brief
        parts.append(_brand_token_block(sheet, has_screenshots=bool(design.screenshot_urls), design_language=True))
        if design.generated_images:
            parts.append(_generated_images_block(design.generated_images) + "\n")
    standards = (_PROMPTS_DIR / PAGE_BUILD_STANDARDS).read_text(encoding="utf-8").strip()
    parts.append(_block(f"assets/Prompts/{PAGE_BUILD_STANDARDS}", standards))
    parts.append("Build THIS FILE now, following every rule above. Return the page and the build notes only.")
    text = "".join(parts)
    shots = design.screenshot_urls if design is not None else ()
    return [{"type": "image", "source": {"type": "url", "url": url}} for url in shots] + [{"type": "text", "text": text}]


def build_email_request(inputs: BuildInputs, email: FunnelEmail) -> str:
    links = "\n".join(f"- {f.title}: {inputs.site_origin}{f.url_path}" for f in inputs.plan.files)
    theme = ""
    if inputs.design is not None:
        theme = inputs.design.theme_brief or inputs.design.design_md
    return (
        _read_prompt(EMAIL_PROMPT)
        + "\n\n"
        + _block("THIS EMAIL", f"## {email.title}\n\n{email.copy}")
        + _block("FUNNEL LINKS", links)
        + _block("BRAND THEME", theme or "No brand theme on this run: use neutral greys and say so in the notes.")
        + "Build THIS EMAIL now. Return the email and the notes only."
    )


# --------------------------------------------------------------------------------------------
# Calls
# --------------------------------------------------------------------------------------------


def split_output(raw: str) -> tuple[str, str]:
    """(html, notes) from a builder's reply: the largest ```html fence, and whatever follows it."""
    fences = list(_FENCE.finditer(raw or ""))
    if not fences:
        stripped = (raw or "").strip()
        if stripped.lower().startswith(("<!doctype", "<html")):
            return stripped, ""
        return "", (raw or "").strip()
    best = max(fences, key=lambda m: len(m.group(1)))
    notes = (raw[best.end():] or "").strip()
    return best.group(1).strip(), notes


async def _call(content: list[dict] | str, max_tokens: int, on_usage: OnUsage | None) -> tuple[str, str | None]:
    kwargs: dict[str, object] = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if MODEL in EFFORT_CAPABLE_MODELS and DEFAULT_EFFORT:
        kwargs["output_config"] = {"effort": DEFAULT_EFFORT}
    started = time.monotonic()
    async with get_client().messages.stream(**kwargs) as stream:
        response = await stream.get_final_message()
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=MODEL, duration_ms=int((time.monotonic() - started) * 1000))
        )
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    return text, getattr(response, "stop_reason", None)


#: The page check's loopback-image finding. On a funnel page it is not a finding about what ships: the
#: zip writes every run image into the file (`standalone_html`), whatever host its URL names.
_LOOPBACK_IMAGE = "The image is served from this machine"


def check_built_page(item: BuiltItem, inputs: BuildInputs) -> None:
    """The deploy check, judged on what the bundle ships: a run image on this backend's loopback is
    embedded by the zip, so it is not held against the page."""
    design_md = inputs.design.design_md if inputs.design is not None else ""
    issues, _used = page_check.check_page(item.html, inputs.manifest, design_md, generated_images=inputs.known_images)
    issues = [i for i in issues if not (i.why.startswith(_LOOPBACK_IMAGE) and "/media/" in i.quote)]
    item.score = page_check.score(issues)
    item.verdict = page_check.verdict(issues)
    item.issues = [vars(issue) for issue in issues]


async def build_page(inputs: BuildInputs, current: FunnelFile, on_usage: OnUsage | None = None) -> BuiltItem:
    item = BuiltItem(
        key=current.key, kind="page", title=current.title, file_path=current.file_path, url_path=current.url_path
    )
    try:
        raw, item.stop_reason = await _call(build_page_request(inputs, current), PAGE_MAX_TOKENS, on_usage)
    except Exception as exc:  # noqa: BLE001 — one page failing never fails the funnel
        logger.exception("Funnel page %s failed", current.key)
        item.error = f"The page could not be built: {exc}"
        return item
    item.html, item.notes = split_output(raw)
    if not item.html:
        item.error = "The builder returned no HTML page." + (" It ran out of room." if item.stop_reason == "max_tokens" else "")
        return item
    if item.stop_reason == "max_tokens":
        item.error = "The page was cut off before it finished. Rebuild it."
    check_built_page(item, inputs)
    return item


async def build_email(inputs: BuildInputs, email: FunnelEmail, on_usage: OnUsage | None = None) -> BuiltItem:
    item = BuiltItem(
        key=email.key, kind="email", title=email.title, file_path=f"emails/{email.key}.html", subject=email.subject
    )
    try:
        raw, item.stop_reason = await _call(build_email_request(inputs, email), EMAIL_MAX_TOKENS, on_usage)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Funnel email %s failed", email.key)
        item.error = f"The email could not be built: {exc}"
        return item
    item.html, item.notes = split_output(raw)
    if not item.html:
        item.error = "The builder returned no HTML email."
    return item


async def build_all(
    inputs: BuildInputs,
    *,
    on_event: OnEvent,
    on_usage: OnUsage | None = None,
    include_emails: bool = True,
    files: list[FunnelFile] | None = None,
    emails: list[FunnelEmail] | None = None,
) -> list[BuiltItem]:
    """Every page and email (or the `files` / `emails` given), `MAX_PARALLEL` at a time, reporting
    each as it starts and finishes."""
    semaphore = asyncio.Semaphore(MAX_PARALLEL)
    files = inputs.plan.files if files is None else files
    emails = (inputs.plan.emails if include_emails else []) if emails is None else emails
    jobs: list[tuple[str, str, Callable[[], Awaitable[BuiltItem]]]] = [
        (f.key, "page", lambda f=f: build_page(inputs, f, on_usage)) for f in files
    ]
    jobs += [(e.key, "email", lambda e=e: build_email(inputs, e, on_usage)) for e in emails]

    async def run(key: str, kind: str, job: Callable[[], Awaitable[BuiltItem]]) -> BuiltItem:
        async with semaphore:
            await on_event({"type": "item_start", "key": key, "kind": kind})
            item = await job()
            await on_event({"type": "item_done", "item": item.summary()})
            return item

    return list(await asyncio.gather(*(run(k, kind, job) for k, kind, job in jobs)))


# --------------------------------------------------------------------------------------------
# Requested changes — edits, never a rebuild
# --------------------------------------------------------------------------------------------

#: Room for the edit list alone: the page itself is never re-emitted.
REVISION_MAX_TOKENS = 16_000


@dataclass
class RevisionResult:
    item: BuiltItem
    applied: int
    #: Why each edit or request item was not applied: (excerpt, reason).
    not_applied: list[tuple[str, str]]
    unreadable: bool = False


async def revise_item(
    item: BuiltItem,
    note: str,
    *,
    source_copy: str = "",
    design_md: str = "",
    inputs: BuildInputs | None = None,
    on_usage: OnUsage | None = None,
) -> RevisionResult:
    """Apply the operator's requested change to one built page or email, and nothing else.

    The same mechanism as the Pillar Page's Refine (`page_revision`): the model returns exact
    find/replace edits against the built HTML, code places each one, and every byte no edit names
    stays as built. Not layout-locked — the operator may be asking for a layout change in their own
    words. A reply with no readable edits leaves the file unchanged and says so; it never falls back
    to rebuilding the page.
    """
    from app.services import page_revision

    prompt = page_revision.build_edit_prompt(item.html, note, source_copy=source_copy, design_md=design_md)
    raw, _stop = await _call(prompt, REVISION_MAX_TOKENS, on_usage)
    edits = page_revision.parse_edits(raw)
    skipped = page_revision.parse_skipped(raw)
    if edits is None:
        return RevisionResult(item=item, applied=0, not_applied=skipped, unreadable=True)
    html, report = page_revision.apply_edits(item.html, edits)
    revised = BuiltItem.from_dict({**item.to_dict(), "html": html})
    revised.revisions = [*item.revisions, {"note": note.strip(), "applied": report.applied}]
    if revised.kind == "page" and inputs is not None and report.applied:
        check_built_page(revised, inputs)
    return RevisionResult(item=revised, applied=report.applied, not_applied=[*report.rejected, *skipped])


# --------------------------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------------------------


def slug_filename(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "funnel"


def site_origin(url: str) -> str:
    parsed = urlparse(url or "")
    return f"{parsed.scheme or 'https'}://{parsed.netloc}" if parsed.netloc else ""


def readme(plan_title: str, items: list[BuiltItem], forms: list[dict], notes: str, origin: str) -> str:
    pages = [i for i in items if i.kind == "page"]
    emails = [i for i in items if i.kind == "email"]
    out = [
        f"# {plan_title} — deployable funnel",
        "",
        "Upload each folder to the client's site root so every page sits at the URL the funnel was "
        "designed with. Images are written into the HTML, so the files render anywhere as they are.",
        "",
        "## Pages",
        "",
        "| Page | File | Publish at | Deploy check |",
        "|---|---|---|---|",
    ]
    for p in pages:
        state = p.error or f"{p.score}/100 — {p.verdict}"
        out.append(f"| {p.title} | `{p.file_path}` | {origin}{p.url_path} | {state} |")
    if emails:
        out += ["", "## Emails", "", "| Email | File | Subject |", "|---|---|---|"]
        out += [f"| {e.title} | `{e.file_path}` | {e.subject or '—'} |" for e in emails]
    if forms:
        out += ["", "## Where the forms post", ""]
        out += [f"- `{f['action']}` ({f['method'].upper()}), read from {f['page_url']}" for f in forms]
    for item in items:
        if item.notes:
            out += ["", f"## Notes — {item.title}", "", item.notes]
    if notes:
        out += ["", "## Implementation notes (from the Funnel document)", "", notes]
    return "\n".join(out) + "\n"


async def bundle_zip(
    plan_title: str,
    items: list[BuiltItem],
    forms: list[dict],
    implementation_notes: str,
    origin: str,
    embed: Callable[[str], Awaitable[str]],
) -> bytes:
    """The zip: every built file with its images embedded (`embed`), and the README."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            if item.html:
                html = await embed(item.html) if item.kind == "page" else item.html
                archive.writestr(item.file_path, html)
        archive.writestr("README.md", readme(plan_title, items, forms, implementation_notes, origin))
    return buffer.getvalue()
