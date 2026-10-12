"""Brand-aware image briefs for HTML-producing assets, and the image-generation calls that fulfil them.

Why generation happens here and not in the model's own prompt
----------------------------------------------------------------
A brief used to be handed to the model as an instruction — "generate this with Runway, then place
the returned URL in the HTML" — inside the HTML-producing stage's own prompt. That call has no tool
access to an image generator, so the instruction was unfollowable: the model could only fabricate a
plausible-looking URL, exactly the failure `design_tokens.py` documents for an invented palette,
one layer up. `generate_all` is the fix — the same shape as that fix, too: measure first, then hand
the model the real, already-resolved value, never an instruction to produce one itself.

Runway -> OpenAI
----------------
Originally wired to Runway, which hosts the image it generates and returns a URL. OpenAI's image
models return base64 bytes instead (confirmed against OpenAI's own docs before switching:
`response_format=url` is DALL-E-only), so `generate_all` now does one more step than it used to —
`media_storage.save_generated_image` stages those bytes as a `MediaAsset` row and returns the URL
this backend serves them back at. See that module's docstring for why a data URI is not the fix,
and why the bytes land in Postgres rather than on local disk (this app deploys to Railway, where
local disk is the container's own ephemeral disk).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import media_storage, openai_image_client, runway_image_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageBrief:
    role: str
    size: str
    prompt: str
    #: Real photographs off the client's own reference page (`firecrawl_client.extract_reference_images`).
    #: When present, `generate_all` grounds the image in these via `openai_image_client.edit_image`
    #: instead of a text-only generation — see that function's docstring for why.
    reference_image_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedImage:
    """One brief, fulfilled. `url` is this backend's own `GET /media/{id}`, absolute on the public
    base (`BACKEND_PUBLIC_URL`) — the bytes are a `MediaAsset` row in Postgres. See
    `media_storage.py` for why they end up there rather than inline or on local disk, and
    `media_storage.current_media_url` for how a URL stored on the loopback default is corrected
    when it is read back."""

    role: str
    size: str
    prompt: str
    url: str


def _token(document: str, name: str, fallback: str) -> str:
    match = re.search(rf"^\s+{re.escape(name)}:\s+[\"']?([^\"'\n]+)", document, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def _brand_base(
    design_md: str, *, asset_label: str, subject_context: str, reference_image_urls: tuple[str, ...]
) -> str:
    """The shared half of every brief: subject, reference grounding and the measured brand system."""
    primary = _token(design_md, "primary", "the measured primary brand colour")
    surface = _token(design_md, "surface", "the measured surface colour")
    ink = _token(design_md, "on-surface", "the measured text colour")
    heading_font = _token(design_md, "fontFamily", "the measured heading typeface")

    if subject_context:
        subject_line = (
            f"This image is for a page about: {subject_context}. Every image MUST depict this "
            "specific business, service or outcome — real-looking people, settings and objects "
            "plausible for it. Do not depict an unrelated industry, product or scene."
        )
    else:
        subject_line = (
            "No specific business description is available for this run. Depict a plausible, "
            "industry-neutral professional scene (an office, a consultation, a workspace) rather "
            "than an arbitrary or unrelated subject — do not invent a specific product, uniform, "
            "signage or setting that could contradict the real client's business."
        )

    if reference_image_urls:
        reference_line = (
            "Real photographs from this client's own page are supplied as reference. Match their "
            "actual subject matter, setting, people and mood — this is not a mood-board suggestion, "
            "it is what the image must depict, restyled only enough to fit the composition below."
        )
    else:
        reference_line = ""

    return " ".join(
        part
        for part in (
            f"Photorealistic editorial image for {asset_label}.",
            subject_line,
            reference_line,
            f"Match the brand system exactly: primary accent {primary}, surface {surface}, "
            f"text contrast {ink}, typography reference {heading_font}.",
            "Use natural light, believable human expression, realistic skin and materials, premium "
            "commercial photography, restrained composition, and intentional negative space for "
            "HTML overlay text. Do not add logos, watermarks, legible text, fake UI, gradients, "
            "illustration, or generic stock-photo styling.",
        )
        if part
    )


def build_image_briefs(
    design_md: str,
    *,
    asset_label: str = "the HTML asset",
    subject_context: str = "",
    reference_image_urls: tuple[str, ...] = (),
) -> tuple[ImageBrief, ...]:
    """Create prompts that bind subject matter to the measured brand system.

    `subject_context` and `reference_image_urls` are what stop the output being a plausible but
    unrelated scene ("random character images"): a brief built from brand tokens alone tells the
    model what colours and typography to use, never what the business *is*, so the model filled
    that gap itself. `subject_context` is a short description of the actual service (from the
    stage's own intake — see `pipeline._image_subject_context`); `reference_image_urls` are real
    photographs off the client's page (`firecrawl_client.extract_reference_images`), which
    `generate_all` uses to ground the image via `openai_image_client.edit_image` rather than a
    text-only generation.
    """
    base = _brand_base(
        design_md,
        asset_label=asset_label,
        subject_context=subject_context,
        reference_image_urls=reference_image_urls,
    )
    return (
        ImageBrief(
            role="hero",
            # The widest of OpenAI's three named GPT-image sizes — the hero band a landing page
            # composes against is wide, not square. See `openai_image_client.VALID_SIZES`.
            size="1536x1024",
            prompt=f"{base} Hero composition: a credible scene that communicates the service outcome, with the focal subject on the right and clean copy space on the left.",
            reference_image_urls=reference_image_urls,
        ),
        ImageBrief(
            role="proof",
            # Square — a proof/testimonial card crops far more forgivingly than a wide hero shot.
            size="1024x1024",
            prompt=f"{base} Proof section composition: a specific, grounded moment showing the client or customer experiencing the measurable outcome, with room for a short heading and caption.",
            reference_image_urls=reference_image_urls,
        ),
        ImageBrief(
            role="closing-cta",
            size="1536x1024",
            prompt=f"{base} Closing call-to-action composition: confident but human, visually calm, with a clear foreground subject and generous uncluttered space for a CTA overlay.",
            reference_image_urls=reference_image_urls,
        ),
    )


#: Section images on top of the three page-level ones, per run. Image generation is money exactly as
#: a Context.dev credit is, so the count is fixed rather than following the number of sections.
MAX_SECTION_IMAGES = 2

#: A section image's role starts with this, followed by the section heading. The prompt block lists
#: the role, so the model knows which section the URL belongs to.
SECTION_ROLE_PREFIX = "section: "

_MARKDOWN_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Sections the three page-level briefs already illustrate, or that never carry a photograph. An FAQ
# with a photo is padding; the hero, proof and closing CTA have their own images.
_NOT_A_PICTURE_SECTION = re.compile(
    r"\b(faq|faqs|question|questions|contents|table of contents|testimonial|testimonials|review|"
    r"reviews|pricing|price|cost|investment|contact|get in touch|call to action|cta|footer|"
    r"related|resources|book|ready to|next step|summary|conclusion|hero|overview of this page)\b",
    re.IGNORECASE,
)


def content_section_headings(content: str, limit: int = MAX_SECTION_IMAGES) -> tuple[str, ...]:
    """The improved content's section headings that deserve an image of their own.

    Reads `##` headings (falling back to `###` when the copy has no `##`), skips the first — it is
    the hero, which already has an image — and skips sections that never carry a photograph.
    Formatting marks are stripped; an empty or unparseable document yields none.
    """
    found = _MARKDOWN_HEADING.findall(content or "")
    if not found:
        return ()
    level = "##" if any(marks == "##" for marks, _ in found) else "###"
    headings = [re.sub(r"[*_`\[\]]", "", text).strip() for marks, text in found if marks == level]
    out: list[str] = []
    for heading in headings[1:]:
        if not heading or len(heading) > 90 or _NOT_A_PICTURE_SECTION.search(heading):
            continue
        if heading.lower() in {h.lower() for h in out}:
            continue
        out.append(heading)
        if len(out) >= limit:
            break
    return tuple(out)


def build_section_briefs(
    design_md: str,
    headings: tuple[str, ...],
    *,
    asset_label: str = "the HTML asset",
    subject_context: str = "",
    reference_image_urls: tuple[str, ...] = (),
) -> tuple[ImageBrief, ...]:
    """One brief per content section that needs its own image (a pillar page's body sections).

    Same brand binding and grounding as `build_image_briefs`; the composition names the section so
    the image shows what that part of the page is about. Sizes alternate wide / square so a split
    layout and a card both have something that crops well.
    """
    base = _brand_base(
        design_md,
        asset_label=asset_label,
        subject_context=subject_context,
        reference_image_urls=reference_image_urls,
    )
    briefs = []
    for index, heading in enumerate(headings[:MAX_SECTION_IMAGES]):
        briefs.append(
            ImageBrief(
                role=f"{SECTION_ROLE_PREFIX}{heading}",
                size="1536x1024" if index % 2 == 0 else "1024x1024",
                prompt=(
                    f"{base} Section image for the part of the page headed \"{heading}\": a concrete, "
                    "believable scene that shows what this section describes, for a split image-and-"
                    "text layout, with the subject clear at small sizes."
                ),
                reference_image_urls=reference_image_urls,
            )
        )
    return tuple(briefs)


async def generate_all(
    briefs: tuple[ImageBrief, ...], session: AsyncSession
) -> tuple[tuple[GeneratedImage, ...], tuple[str, ...]]:
    """Fulfil every brief and return `(generated, notes)`.

    Generation order, per brief
    ----------------------------
    1. If the brief carries `reference_image_urls`, try OpenAI's reference-grounded edit
       (`openai_image_client.edit_image`) — the image is built from the client's own real
       photographs, not invented from a text description alone. See that function's docstring.
    2. Otherwise, or if step 1 failed (a reference URL that no longer resolves, a content-policy
       rejection), try plain OpenAI generation (`openai_image_client.generate_image`).
    3. If OpenAI fails entirely *and* `RUNWAYML_API_SECRET` is set, retry with Runway
       (`runway_image_client.generate_image`). Runway is transparent to the rest of the pipeline:
       it returns the same `GeneratedImageBytes` type. Runway has no edit equivalent wired here, so
       a reference-grounded brief that falls through to Runway loses the grounding, not the image.
    4. If everything fails, the brief is skipped and a note is appended.

    The generation calls are issued concurrently per brief, like the two Context.dev screenshots in
    `design_md.capture_page_design`. Saving to Postgres happens *after* that gather, one at a time
    — an `AsyncSession` is not safe for concurrent use across coroutines.

    `notes` carries one line per failure for the caller to fold into the run's own notes.

    Returns `((), ())` immediately when neither provider is configured.
    """
    openai_ok = openai_image_client.is_configured()
    runway_ok = runway_image_client.is_configured()
    if not briefs or (not openai_ok and not runway_ok):
        return (), ()

    async def _one(brief: ImageBrief) -> openai_image_client.GeneratedImageBytes | Exception:
        # --- primary: OpenAI, reference-grounded when a real photo is available ---
        if openai_ok:
            if brief.reference_image_urls:
                try:
                    return await openai_image_client.edit_image(
                        brief.prompt, brief.reference_image_urls, size=brief.size
                    )
                except openai_image_client.OpenAIImageError as exc:
                    logger.warning(
                        "OpenAI reference-grounded edit failed for brief %r — trying a text-only "
                        "generation: %s",
                        brief.role,
                        exc,
                    )
            try:
                return await openai_image_client.generate_image(brief.prompt, size=brief.size)
            except openai_image_client.OpenAIImageError as exc:
                openai_failure = exc
                logger.warning(
                    "OpenAI image failed for brief %r — trying Runway fallback: %s",
                    brief.role,
                    exc,
                )
                # --- fallback: Runway ---
                if runway_ok:
                    try:
                        return await runway_image_client.generate_image(brief.prompt, size=brief.size)
                    except runway_image_client.RunwayImageError as runway_exc:
                        return runway_exc
                # Runway not configured (or this brief never reached it): the real reason this
                # brief failed is OpenAI's own error, not "no provider is configured" — that string
                # would be actively misleading when OpenAI *is* configured and simply refused.
                return openai_failure
        # --- OpenAI not configured at all ---
        if runway_ok:
            try:
                return await runway_image_client.generate_image(brief.prompt, size=brief.size)
            except runway_image_client.RunwayImageError as exc:
                return exc
        # Neither provider available for this brief.
        return openai_image_client.OpenAIImageError(
            "No image provider is configured (OPENAI_API_KEY and RUNWAYML_API_SECRET are both unset)."
        )

    results = await asyncio.gather(*(_one(brief) for brief in briefs))

    generated: list[GeneratedImage] = []
    notes: list[str] = []
    for brief, result in zip(briefs, results):
        if isinstance(result, Exception):
            logger.info("Image brief %r could not be fulfilled: %s", brief.role, result)
            notes.append(f"The {brief.role} image could not be generated: {result}")
            continue
        try:
            url = media_storage.save_generated_image(session, result.data, output_format=result.output_format)
        except Exception as exc:  # noqa: BLE001 - degrades like any other brief failure, not fatal
            logger.info("Image brief %r generated but could not be saved: %s", brief.role, exc)
            notes.append(f"The {brief.role} image could not be saved: {exc}")
            continue
        generated.append(GeneratedImage(role=brief.role, size=brief.size, prompt=brief.prompt, url=url))

    logger.info(
        "Image briefs fulfilled=%s/%s roles=%s",
        len(generated),
        len(briefs),
        ",".join(g.role for g in generated) or "none",
    )
    return tuple(generated), tuple(notes)
