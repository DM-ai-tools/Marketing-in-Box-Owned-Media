"""Generic per-stage generation service for all 15 Phase 1 assets.

Generalizes the pattern `app/services/icp.py` established for the ICP stage: load the *real*
canonical master prompt for a stage from `assets/Prompts/`, reproduce that prompt's own
"fill in before submitting" INPUTS block from structured intake, and call Claude with the
unmodified instructions that follow — never a paraphrased summary of the prompt.

Source of the (asset_id -> prompt file) mapping and the (asset_id -> field schema file) mapping
is `schemas/drafts/DAG_SOURCE_MAP.md`. Field labels for each INPUTS block come from that same
asset's `schemas/drafts/<file>.json` (the authoritative field registry, not the frontend's
`assetCatalog.ts` — the two agree on `field_id`s but occasionally differ in cosmetic label
wording, and the schema JSON's label text is what actually matches each prompt file's own INPUTS
block variable names).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.claude_client import get_client
from app.services.image_briefs import GeneratedImage
from app.services.industry_voice import IndustryProfile, voice_block
from app.services.page_structure import STRUCTURE_HEADING
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../application/backend
_PROMPTS_DIR = _BACKEND_ROOT / "assets" / "Prompts"
_SCHEMAS_DIR = _BACKEND_ROOT / "schemas" / "drafts"

_DEFAULT_MARKER_SUBSTR = "MASTER PROMPT (do not edit below this line)"

SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"

# See the effort note below `STAGE_CONFIGS`. Named rather than inlined so a cost sweep is one edit.
DEFAULT_EFFORT = "medium"
SHORT_FORM_EFFORT = "low"

# `output_config.effort` is not universal: it is accepted on Sonnet 5 and the Opus/Fable tier, and
# rejected outright on Haiku 4.5, where sending it returns a 400 rather than being ignored. So every
# request builder gates on the model as well as on the config — a stage retiered to Haiku later
# cannot silently start sending an unsupported parameter, which would fail the stage rather than
# cost a little more.
#
# An allow-list rather than a Haiku deny-list, because the two failure directions are not
# symmetrical: a model missing from this set loses the effort saving (costs more, still works),
# while a model wrongly assumed to accept it 400s the stage. So a new model has to be added here
# deliberately — which is also why the competitor prepass reads this set rather than keeping its
# own copy (`app/services/competitor.py`, `_effort`), since its `COMPETITOR_MODEL` override is
# exactly the path that points a call at a model this table has never seen.
EFFORT_CAPABLE_MODELS: frozenset[str] = frozenset({SONNET, "claude-opus-5", "claude-fable-5"})

# Prompt-cache duration for the reference-library block. Deliberately an hour, not the 5-minute
# default: every stage here sits behind an operator approval gate, and the measured start-to-start
# gaps between calls on this pipeline were 12, 27, 33, 50 and 102 minutes. At the default TTL the
# entry expired before the next stage ever read it — `api_usage` recorded
# `cache_creation_input_tokens=18385` against `cache_read_input_tokens=0` on three consecutive
# calls, i.e. the 1.25x write surcharge paid three times for zero reads. An hour costs 2x on the
# write and pays for itself on the first prevented miss.
_CACHE_TTL = "1h"


@dataclass(frozen=True)
class StageConfig:
    asset_id: str
    prompt_file: str
    schema_file: str
    model: str
    max_tokens: int
    # Only `funnel_hub_media`'s source file has no "MASTER PROMPT" marker (it has no separate
    # body section to splice past) — every other file uses the shared default substring search.
    marker_override: str | None = None
    # Phase-2 deltas, applied to the shared field registry by `_load_schema_fields`. Empty for
    # every Phase-1 config; see `PHASE2_OVERRIDES` for what fills them and why.
    drop_fields: frozenset[str] = frozenset()
    label_overrides: tuple[tuple[str, str], ...] = ()
    # `output_config.effort`. See the effort note below `max_tokens`; "medium" is the pipeline
    # default because these stages write documents, not one-line answers. `None` means "send no
    # `output_config`", which is mandatory for the Haiku stages — see `EFFORT_CAPABLE_MODELS`.
    effort: str | None = DEFAULT_EFFORT


# max_tokens note: every stage here streams (`generate_stage_stream`), so the ~16k ceiling that
# applies to non-streaming calls (HTTP timeouts) does not bind — Opus 5 and Sonnet 5 both allow up
# to 128k output tokens. The "produce a whole document" stages below were verified to hit their old
# 16k cap and truncate mid-deliverable (`stop_reason=max_tokens`), so they sit at 64k: the CRO
# rewrite alone emits a mode table, seven audits quoting the page as evidence, a complete rewritten
# page, and an implementation pack; the merged Pillar Page emits four parts including a built page.
#
# Do not lower these to save money — it does not work that way, and it silently ruins deliverables:
#
#   * `max_tokens` is a hard ceiling, not a target or a hint. Nothing is billed for headroom; you pay
#     for the tokens actually generated, so a 64k cap on a run that emits 9k costs exactly the same
#     as a 9k cap would. Lowering it does not reduce spend — it cuts the document off *after* you
#     have paid for everything up to the cut.
#   * The ceiling covers *thinking* as well as visible output, and adaptive thinking is on by
#     default on Sonnet 5. Measured: a small five-section page task capped at 1500 reported
#     `output_tokens=1500` with only ~3.1k characters of visible text — close to half the budget
#     went to reasoning that never reaches the document.
#
# To spend less, reduce what is *generated* rather than where it is truncated. That is what
# `effort` below is for. `generate_stage_stream` logs a WARNING naming the stage and the cap
# whenever one is hit.
#
# effort note: `output_config.effort` is the knob that actually lowers output spend, because
# thinking bills as output and adaptive thinking is on by default. Measured on this account, on
# the headline gate (`app/services/headlines.py`): the same prompt returned the same candidates in
# 4,464 output tokens at `low` where default effort spent 14,727 — a 70% cut. These stages write
# whole documents rather than naming ten headlines, so they sit at `medium` rather than `low`;
# `low` is reserved for the stages whose deliverable is short and templated. Raise a single stage
# by giving its config an explicit `effort=` rather than moving the default.
STAGE_CONFIGS: dict[str, StageConfig] = {
    "icp": StageConfig("icp", "ICP.md", "icp.json", SONNET, 20000),
    "cro": StageConfig("cro", "Master_Prompt_Universal_Page_Rewrite_v1.md", "cro.json", SONNET, 64000),
    # One merged stage, not two: `Master_Prompt_Universal_Page_Design_v1.md` is now v2.0 of that
    # prompt and carries the SEO/competitor-benchmark pass (its Step 2, Rules 8-9, PART 2 and
    # PART 4) that used to be run as a separate `seo_pillar_page` pass over the same file. The
    # competitor pillar-page benchmark reaches it through the `competitor_analysis_seo_pillar_page`
    # prepass (see app/services/competitor.py), so this stage emits the design system, the
    # competitive superiority plan, the built page, and the SEO implementation pack in one run —
    # hence the same 64k ceiling as the CRO rewrite.
    "pillar_page": StageConfig(
        "pillar_page", "Master_Prompt_Universal_Page_Design_v1.md", "pillar_page.json", SONNET, 64000
    ),
    "funnel": StageConfig("funnel", "Funnel_Prompt.md", "funnel.json", SONNET, 50000),
    "funnel_hub_media": StageConfig(
        "funnel_hub_media",
        "Funnel-Hub-Media-Architect-Prompt.md",
        "funnel_hub_media.json",
        SONNET,
        50000,
        marker_override="## PHASE 1 —",
    ),
    "offers": StageConfig("offers", "Master_Prompt_Universal_Value_Ladder_v2.md", "offers_v2.json", SONNET, 64000),
    # 128k, the highest this model allows, because this stage's deliverable is now plural. The
    # operator picks concepts at the suggestion gate (`lead_magnet_concept` in
    # `app/services/headlines.py`) — around ten — and Step 5 builds a complete single-file HTML
    # lead magnet for every one of them, plus a brief each. One interactive build runs several
    # thousand tokens on its own, so ten of them clear the old 64k ceiling comfortably; at 64k the
    # response would truncate somewhere around the sixth file, having already been paid for.
    #
    # The ceiling costs nothing when unused (see the max_tokens note above — you pay for tokens
    # generated, not for headroom), and the prompt is written to degrade honestly rather than pad:
    # it builds what it can to full depth and names the concepts it did not reach.
    "lead_magnet": StageConfig("lead_magnet", "Lead-Magnet-Architect-Prompt.md", "lead_magnet.json", SONNET, 128000),
    # 128k for the same reason as `lead_magnet` above: this stage's deliverable is now plural. The
    # operator picks topics at the suggestion gate (`blog_topic` in `app/services/headlines.py`) —
    # around five — and Steps 3-7 run per topic, each producing an intent analysis, an outline, a
    # full 1,800-2,200 word post, an SEO checklist and a content brief. One post ran comfortably
    # inside the old 40k; five of them do not, and the truncation would land mid-post after the
    # whole response had already been paid for.
    "blog": StageConfig("blog", "universal-blog-generation-prompt.md", "blog.json", SONNET, 128000),
    "content_marketing_strategy": StageConfig(
        "content_marketing_strategy",
        "Content-Marketing-Strategy-Architect-Prompt.md",
        "content_marketing_strategy.json",
        SONNET,
        40000,
    ),
    "social_content_strategy_audit": StageConfig(
        "social_content_strategy_audit",
        "Social-Content-Strategy-Audit-Architect-Prompt.md",
        "social_content_strategy_audit.json",
        SONNET,
        10000,
    ),
    # 128k, same reason as `lead_magnet` and `blog` above: the deliverable went plural. The operator
    # picks topics at the suggestion gate (`webinar_topic` in `app/services/headlines.py`) — around
    # three — and Steps 3-8 run per webinar, each producing an architecture, a full presenter script,
    # a slide brief, registration copy and an email sequence. The old 20k was sized for exactly one
    # of those, so three would truncate mid-script after the whole response had been paid for.
    # Step 2's competitor synthesis stays shared across the set rather than being repeated per
    # topic, which is what keeps three packages inside one response at all.
    "webinar": StageConfig("webinar", "universal-webinar-prompt.md", "webinar.json", SONNET, 128000),
    # effort=None on the three Haiku stages: `output_config.effort` is rejected on Haiku 4.5, so
    # sending it would turn a working stage into a 400. See `EFFORT_CAPABLE_MODELS`.
    # 64,000 — Haiku 4.5's hard ceiling, read from the Models API (`client.models.retrieve`) rather
    # than assumed, where the Sonnet stages above can go to 128k. Raised because this stage's
    # deliverable went plural (`book_topic` in `app/services/headlines.py`), but the ceiling is the
    # binding constraint here in a way it is nowhere else in this table: the stage's own Book Format
    # input offers "full-length business book (25,000-50,000+ words)", and 50,000 words is roughly
    # 67k tokens — more than one response can emit even for a single book. So the cap is not what
    # makes a long book possible; it is what stops a *short*-format set truncating. The prompt owns
    # the honest failure: it writes what it can to full depth and names the topics it did not reach.
    "book": StageConfig("book", "Webinar-to-Book-Architect-Prompt.md", "book.json", HAIKU, 64000, effort=None),
    "podcast": StageConfig("podcast", "universal-podcast-prompt.md", "podcast.json", HAIKU, 20000, effort=None),
    "sms_sequence": StageConfig(
        "sms_sequence", "universal-sms-sequence-prompt.md", "sms_sequence.json", HAIKU, 10000, effort=None
    ),
    "plan_of_action": StageConfig(
        "plan_of_action", "Plan-of-Action-Architect-Prompt.md", "plan_of_action.json", SONNET, 30000
    ),
}


# --------------------------------------------------------------------------------------
# Phase 2
#
# Phase 2 builds the same kinds of asset one level down, for a single *sub-service* (Google Ads,
# LinkedIn, Meta Ads) of the headline service Phase 1 covered. It has its own prompt files, under
# `assets/Prompts/Phase2/`, and its own seven-stage running order (see the frontend's
# `pipeline/pipelineData.ts`).
#
# Those files are overwhelmingly the Phase-1 prompt re-pointed at a sub-service: Lead Magnet, Blog
# and SMS are byte-identical to their Phase-1 counterparts, Funnel differs by one INPUTS label,
# Funnel Hub by one label, Content Marketing by one label. Two genuinely differ — Pillar Page, where
# Phase 2 uses the standalone v1.0 design prompt with no keyword/competitor-benchmark block where
# Phase 1 uses the merged v2.0 that has one, and CRO, which carries the SCOPE LOCK section that
# holds the page to the sub-service (see that override, and `tests/test_phase2_cro_scope.py`).
#
# So a phase is expressed as a *delta* over the Phase-1 config rather than as a second full table.
# The alternative — a duplicate `<asset>_phase2.json` field registry per stage — would mean seven
# near-identical 20-field JSON files kept in sync by hand, and a Phase-1 field added later would
# silently fail to reach Phase 2. One registry per asset plus the delta below cannot drift that way,
# and the delta doubles as the documentation of what Phase 2 does not ask.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Phase2Override:
    """How one stage's Phase-2 run differs from its Phase-1 run.

    `label_overrides` matters more than it looks: `build_prompt` reproduces each prompt file's own
    INPUTS block, and the labels are what tie a rendered line to the variable the master prompt
    body refers to. A Phase-2 file that says "Target Sub-Service" must be handed "Target
    Sub-Service", not Phase 1's "Target Service".
    """

    prompt_file: str
    drop_fields: frozenset[str] = frozenset()
    label_overrides: tuple[tuple[str, str], ...] = ()
    model: str | None = None
    max_tokens: int | None = None


_PHASE2_PROMPT_SUBDIR = "Phase2"

PHASE2_OVERRIDES: dict[str, Phase2Override] = {
    # Phase 2's own ICP, and stage 01. Phase 1's ICP is built from the client's headline service, so
    # the buyer it profiles is the buyer of "Social Media Marketing", not of "Meta Ads" — often a
    # different person with a different trigger, budget line and set of alternatives. Phase 2 used to
    # inherit that document down `source_run_id` and ask the CRO prompt's SCOPE LOCK to re-point it;
    # now it builds its own, and `icp` is in `PHASE_SCOPED_CONTEXT_KEYS` (the router) so the parent's can never
    # stand in for it.
    #
    # The file is `ICP.md` with exactly two edits — the service input relabelled to the sub-service,
    # and a SUB-SERVICE SCOPE section before the context rules. `tests/test_phase2_icp.py` reverses
    # both and requires the result to equal `ICP.md` byte for byte, so an improvement to the Phase 1
    # prompt that is not carried across fails a test instead of silently drifting.
    "icp": Phase2Override(
        "ICP_phase2.md",
        label_overrides=(("service_product_price_terms", "Sub-Service + Price/Terms"),),
    ),
    # Phase 2's own CRO stage, and the reason it exists: the Pillar Page prompt designs the copy it
    # is handed and has no service input of any kind, so the copy *is* the subject of the page. With
    # no CRO stage here that copy could only be the parent Phase 1 run's rewrite of the client's
    # headline service — which produced a Social Media Marketing page from a run the operator had
    # carefully pointed at Meta Ads — or a document pasted in by hand.
    #
    # Nothing is dropped: the file opens "works for any industry, any sub-service, any page scope"
    # and every one of its inputs is one this stage still asks. What the run answers for itself is
    # the sub-service, the NEW PAGE sentinels, and the client-level settings inherited from the
    # parent run (see `app/services/cro_settings.py` and the `cro` delta in the frontend's
    # `data/phase2Catalog.ts`).
    #
    # What the file adds is a **SCOPE LOCK** section Phase 1's does not have, and it is load-bearing
    # rather than decorative. Three of this stage's strategy inputs are written at the *parent's*
    # scope by construction — the inherited ICP document, plus Proof Assets Available and the outcome
    # words, which come from the parent run's `cro_client_settings`. Handed a parent-scope ICP and a
    # one-line sub-service name, a model writes the page most of its input describes, and the result
    # is the Social Media Marketing page this stage exists to prevent. The lock names the input that
    # decides the subject, bars the parent's term from the H1, title, keyword target and offer name,
    # and says what to keep, re-point and discard in each inherited document. The two files were
    # byte-identical before it landed, so re-copying Phase 1's over this one breaks nothing anything
    # else would notice — `tests/test_phase2_cro_scope.py` is what notices.
    "cro": Phase2Override("Master_Prompt_Universal_Page_Rewrite_v1_phase2.md"),
    # The one real divergence. Phase 2's file is the standalone v1.0 design prompt: it designs the
    # page from approved copy and nothing else, so the whole "Search & competitive benchmark" block
    # is absent from its INPUTS — no head term, no cluster terms, no cluster links, no competitor
    # pillar-page listing — and it has no Locked Sections input either. Asking those four questions
    # and then handing the answers to a prompt with nowhere to put them is worse than not asking.
    "pillar_page": Phase2Override(
        "Master_Prompt_Universal_Page_Design_v1_phase2.md",
        drop_fields=frozenset(
            {
                "primary_keyword_head_term",
                "secondary_cluster_terms_optional",
                "internal_cluster_pages_to_link_optional",
                "competitor_analysis_pillar_page",
                "cro_locked_sections",
            }
        ),
    ),
    "funnel": Phase2Override(
        "Funnel_Prompt_Phase2.md",
        label_overrides=(
            ("target_service_if_different_from_pillar_page", "Target Sub-Service (if different from pillar page)"),
        ),
    ),
    "lead_magnet": Phase2Override("Lead-Magnet-Architect-Prompt_phase2.md"),
    "blog": Phase2Override("universal-blog-generation-prompt_phase2.md"),
    "sms_sequence": Phase2Override("universal-sms-sequence-prompt_phase2.md"),
    "content_marketing_strategy": Phase2Override(
        "Content-Marketing-Strategy-Architect-Prompt_phase2.md",
        label_overrides=(
            ("primary_service_pillar_page_being_supported", "Primary Sub-Service / Pillar Page Being Supported"),
        ),
    ),
    "funnel_hub_media": Phase2Override(
        "Funnel-Hub-Media-Architect-Prompt_phase2.md",
        label_overrides=(("service_or_product_line_being_funnel_mapped", "sub-service or product line being funnel-mapped"),),
    ),
}


def _phase2_config(asset_id: str) -> StageConfig:
    """The Phase-1 config for `asset_id` with its Phase-2 override applied."""
    base = STAGE_CONFIGS[asset_id]
    override = PHASE2_OVERRIDES[asset_id]
    return StageConfig(
        asset_id=base.asset_id,
        prompt_file=f"{_PHASE2_PROMPT_SUBDIR}/{override.prompt_file}",
        schema_file=base.schema_file,
        model=override.model or base.model,
        max_tokens=override.max_tokens or base.max_tokens,
        marker_override=base.marker_override,
        drop_fields=override.drop_fields,
        label_overrides=override.label_overrides,
        effort=base.effort,
    )


PHASE2_STAGE_CONFIGS: dict[str, StageConfig] = {asset_id: _phase2_config(asset_id) for asset_id in PHASE2_OVERRIDES}

CONFIGS_BY_PHASE: dict[str, dict[str, StageConfig]] = {
    "phase1": STAGE_CONFIGS,
    "phase2": PHASE2_STAGE_CONFIGS,
}

DEFAULT_PHASE = "phase1"


# --------------------------------------------------------------------------------------
# Reference-document injection
#
# `CRO_Framework_Universal_v1.md` is a shared reference/library document, not a stage: it has
# no INPUTS block and no asset_id (see DAG_SOURCE_MAP.md, "Reference document, not a stage").
# The `cro` prompt consumes it through its "CRO Framework" input, whose own instruction reads
# `[PASTE OR ATTACH — or write "USE DEFAULT" ...]`.
#
# Left as the literal string "USE DEFAULT", the prompt falls back to the abridged ~22-line
# summary embedded in its Step 1A. Pasting the real file instead gives the rewrite the full
# 188-line framework — the pricing disclosure modes, claim substantiation tiers, geo modes and
# SEO preservation rules that the embedded summary drops. So we paste it, exactly as the field
# asks, rather than spending an extra LLM call asking Claude to restate a static document.
# --------------------------------------------------------------------------------------

REFERENCE_DOC_INJECTIONS: dict[tuple[str, str], str] = {
    ("cro", "cro_framework"): "CRO_Framework_Universal_v1.md",
}

_INJECT_WHEN_ANSWER_IS = {"", "USE DEFAULT", "N/A", "NONE"}


# --------------------------------------------------------------------------------------
# Reference library
#
# The mechanism above fills an *input* the operator would otherwise have to paste. This one is for
# the other case: a canonical document a prompt cites by filename and instructs the model to
# consult, which is not an input at all and has no field to live in.
#
# `COMPREHENSIVE_HEADLINE_FRAMEWORK.md` is the case that motivated it. The Value Ladder prompt says
# every Title "MUST be derived from" it and to "consult it before writing a single Title" — while
# the file was never in context, so two of its six fallback rules were unfollowable: Rule 4 wants
# the Framework's per-channel character limits (Google Search 30, Facebook 25-40, title tag 50-60)
# and Rule 5 wants its pre-publication checklist, neither of which the prompt restates.
#
# Injected for the stages whose deliverable is substantially titles, topics or headlines, chosen
# from what each prompt's own output schema asks for — Offers writes a Title per rung, Lead Magnet a
# "Benefit-led name", Blog a Title (H1) it then checks against a 60-character budget, Podcast ten
# episode titles, Webinar a title and subtitle plus per-slide headlines. Stages that emit no
# headline get nothing: ICP, Funnel (structure, not copy), SMS (no headline, and the Framework has
# no SMS channel), Plan of Action (task names).
#
# Keyed by asset_id, so a phase's stage picks its library up automatically — Phase 2's Blog and Lead
# Magnet need this every bit as much as Phase 1's.
# --------------------------------------------------------------------------------------

HEADLINE_FRAMEWORK = "COMPREHENSIVE_HEADLINE_FRAMEWORK.md"

# What a supplied document *binds*, stated per document.
#
# Only the Value Ladder prompt cites the Headline Framework in its own text; the other ten stages
# here emit titles and topics without ever mentioning it. Handing those stages 44KB of headline
# theory under a neutral "here is a reference" header would be inert — a model has no reason to
# apply an unbidden document to work its instructions never connected it to.
#
# So the binding instruction travels with the document rather than being added to ten of the
# authored prompt files. That keeps those files as written (they are the canonical deliverable
# specs, and editing eleven of them to say the same paragraph is eleven places to drift), and it
# puts the rule where it cannot be separated from the thing it refers to.
REFERENCE_DOC_DIRECTIVES: dict[str, str] = {
    HEADLINE_FRAMEWORK: (
        "This is the canonical headline reference for this system. Every headline, title, topic "
        "name, episode or chapter title, offer name, lead-magnet name, subject line and on-page "
        "H1 you write anywhere in your response must be built from it — never invented from "
        "scratch, and never left as a flat descriptive label.\n"
        "Apply it concretely, not decoratively:\n"
        "  - Interest = Curiosity + a big promise: every title must open a gap AND state the "
        "desired benefit. One without the other fails.\n"
        "  - Use at least two of its eight curiosity elements per title. Specificity is never "
        "optional: replace every vague benefit with a number, timeframe, or named mechanism.\n"
        "  - Match the traffic temperature of the thing being named (cold / warm / hot) and pull "
        "the formula family it lists for that temperature.\n"
        "  - Respect its per-channel character limits for the channel the item will actually be "
        "published on.\n"
        "  - Run every title through its pre-publication checklist before finalising; rewrite what "
        "fails rather than shipping it with a caveat.\n"
        "Where this conflicts with a claim, compliance, or regulatory constraint stated in the "
        "prompt below, the constraint wins and the title is rebuilt within it."
    ),
}

REFERENCE_LIBRARY: dict[str, tuple[str, ...]] = {
    "cro": (HEADLINE_FRAMEWORK,),
    "pillar_page": (HEADLINE_FRAMEWORK,),
    "offers": (HEADLINE_FRAMEWORK,),
    "lead_magnet": (HEADLINE_FRAMEWORK,),
    "blog": (HEADLINE_FRAMEWORK,),
    "content_marketing_strategy": (HEADLINE_FRAMEWORK,),
    "social_content_strategy_audit": (HEADLINE_FRAMEWORK,),
    "funnel_hub_media": (HEADLINE_FRAMEWORK,),
    "webinar": (HEADLINE_FRAMEWORK,),
    "book": (HEADLINE_FRAMEWORK,),
    "podcast": (HEADLINE_FRAMEWORK,),
}


class CorruptReferenceDocError(ValueError):
    pass


def _validate_reference_doc(filename: str, text: str) -> None:
    """Reject the known-bad conversion of a PDF-sourced reference.

    `COMPREHENSIVE_HEADLINE_FRAMEWORK.md.pdf` embeds subsetted, Identity-encoded CIDFontType2
    fonts. A converter that ignores the PDF's /ToUnicode CMaps maps every digit glyph to U+00FD
    ("y-acute"), which reads as ordinary prose while having quietly destroyed the only part of the
    document the prompts actually need a number from: "Google Search Ads: 30 characters" becomes
    "Google Search Ads: yy characters".

    Worth failing loudly rather than injecting: a reference stating its limits as "yy characters" is
    worse than no reference, because the prompt's inline fallback rules are written to bind on their
    own when the document is absent, and cannot detect one that is present but wrong.
    """
    corrupt_digits = text.count("\u00fd")
    real_digits = sum(c.isdigit() for c in text)
    if corrupt_digits > real_digits:
        raise CorruptReferenceDocError(
            f"{filename} looks like a ToUnicode-blind PDF conversion: {corrupt_digits} "
            f"U+00FD characters vs {real_digits} real digits. Re-convert with an extractor that "
            f"honours the PDF's /ToUnicode CMaps."
        )


def _load_reference_library(asset_id: str) -> str:
    """The reference documents for `asset_id`, fenced and named, or "" when it has none.

    Named with the same path the prompts cite, so an instruction to consult
    `assets/Prompts/COMPREHENSIVE_HEADLINE_FRAMEWORK.md` resolves to a block the model can see it
    has been given, rather than to a filename it has to take on faith.
    """
    filenames = REFERENCE_LIBRARY.get(asset_id, ())
    if not filenames:
        return ""

    blocks = []
    for filename in filenames:
        text = (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
        _validate_reference_doc(filename, text)
        directive = REFERENCE_DOC_DIRECTIVES.get(filename)
        header = f"===== BEGIN assets/Prompts/{filename} ====="
        if directive:
            header += f"\nHOW THIS DOCUMENT BINDS YOUR RESPONSE:\n{directive}\n"
        blocks.append(f"{header}\n{text}\n===== END assets/Prompts/{filename} =====")
        logger.info("Injected reference library doc %s into %s (%d chars)", filename, asset_id, len(text))

    return (
        "— REFERENCE LIBRARY —\n\n"
        "The document(s) below are canonical references, reproduced in full. Consult them directly:\n"
        "any instruction anywhere in this prompt to \"consult\", \"derive from\", or \"comply with\" one\n"
        "of them refers to the copy provided here, and must never be treated as unavailable.\n\n"
        + "\n\n".join(blocks)
        + "\n\n— END OF REFERENCE LIBRARY —\n\n"
    )


# --------------------------------------------------------------------------------------
# Brand design tokens
#
# The stages below build actual HTML that a client is expected to publish under their own brand.
# Every one of their prompts already forbids inventing a palette — the Pillar Page prompt states it
# as Rule 1, "No design invention. Every colour, button, layout, and visual element must trace back"
# — and until now none of them had anything to trace back *to*: the design reference reached the
# model through `scraper.py`, which strips every stylesheet and style attribute because its job is
# extracting copy.
#
# So the instruction was unfollowable and the model did the only thing it could, which was invent a
# plausible palette and present it as extracted. That failure is invisible in the output: a clean,
# professional lead magnet in the wrong brand's colours looks exactly like a correct one.
#
# `app/services/design_tokens.py` measures the real page instead. What it produces is injected here
# — beside the headline framework, through the same mechanism — so the values are in context before
# the master prompt tells the model to build.
# --------------------------------------------------------------------------------------

BRAND_DESIGN_TOKENS = "BRAND_DESIGN_TOKENS"

# Only the stages that emit HTML someone will publish. A stage that writes structure or copy has no
# palette to get wrong: ICP, Funnel (stage structure), SMS, Plan of Action, and the Book/Podcast
# scripts are all absent on purpose.
BRAND_TOKEN_STAGES: frozenset[str] = frozenset(
    {"cro", "pillar_page", "lead_magnet", "funnel_hub_media", "webinar"}
)

# Of those, the stages rebuilding a page that already exists. They get the whole DESIGN.md —
# spacing scale, component styles, elevation — because their output stands next to the original and
# a visitor must not be able to tell them apart.
PAGE_REPLICA_STAGES: frozenset[str] = frozenset({"cro", "pillar_page"})

# The rest get the theme brief: colours, type, shapes and the logo, with layout and components
# stripped. A lead magnet is a different artifact from the client's landing page — it has its own
# structure — and handing it the source page's section spacing and button geometry invites it to
# reproduce a layout it was never meant to copy. It still has to look like the same brand.
BRAND_THEME_STAGES: frozenset[str] = BRAND_TOKEN_STAGES - PAGE_REPLICA_STAGES

# The stages shown the page itself. Both of them rebuild a page that already exists, so section
# order and hero composition are part of their brief rather than context.
#
# `pillar_page` was missing here, and its own intake is the argument for including it: the stage's
# required `reference_design_source` field is "URL / description of the page whose visual design to
# replicate", and its master prompt states as Rule 1 that every colour, button, layout and visual
# element must trace back to that reference. Withholding the reference images from the one stage
# whose first instruction is "replicate this page" left it tracing back to a token sheet, which
# says what the page is made of and nothing about what it looks like.
#
# Free: the two shots are captured once per run either way (`capture_page_design`), so adding a
# stage here costs prompt tokens and no credits.
SCREENSHOT_STAGES: frozenset[str] = frozenset({"cro", "pillar_page"})

_BRAND_TOKEN_DIRECTIVE = (
    "These are the client's REAL design tokens, read from their own live page's CSS — not a "
    "suggestion, and not a starting point to improve on. Any HTML you produce anywhere in this "
    "response must strictly follow the reference page's UI layout, padding scale, typography, "
    "Header, and Footer:\n"
    "  - Use ONLY colours from the palette below. Do not invent a colour, do not 'refine' one, and "
    "do not substitute a colour you consider more tasteful. A visitor must not be able to tell the "
    "generated page from the client's own.\n"
    "  - Take the page background, body text colour, and brand/accent colour from the Core table, "
    "and honour the stated role of each palette colour — a colour listed as a border colour is not "
    "a background.\n"
    "  - Reproduce the font stacks verbatim, including their fallbacks, and include the webfont "
    "links exactly as listed in the generated <head>. A font name without its link renders as "
    "something else entirely. Strictly follow the reference page's typography and heading scale.\n"
    "  - **UI Layout & Spacing**: Strictly match the reference page's container max-widths, section "
    "vertical paddings, component/card inner paddings, margins, and grid/flex gaps from the Spacing "
    "scale. Do not invent arbitrary spacing or squash/expand sections.\n"
    "  - **Header & Navigation**: Strictly replicate the reference page's header architecture (logo "
    "placement, navigation links, primary/secondary action buttons, sticky/fixed behavior, padding, "
    "and background).\n"
    "  - **Footer**: Strictly replicate the reference page's footer structure (multi-column link "
    "layout, branding, contact details, copyright, and padding).\n"
    "  - Match the button styles as given: background, text colour, radius, padding, weight, and "
    "the hover state where one is listed.\n"
    "  - **Use the client's real logo exactly as supplied in the Logo section or Page Structure.** "
    "Render it in both the header and the footer of any generated HTML. Paste the inline SVG, or use the data URI, "
    "or reference the absolute URL — whichever that section gives you, verbatim. Do NOT recreate the "
    "logo as styled text, do NOT redraw it as an SVG of your own, and do NOT substitute an icon or a "
    "generic mark. A recreated wordmark is a drawing of a logo, not the logo, and it is the single "
    "most obvious tell that an asset was not made by the client. Preserve its aspect ratio: set one "
    "dimension and leave the other `auto`. Only if the Logo section is absent may you fall back to a "
    "text treatment, and then say so explicitly.\n"
    "  - Declare the supplied CSS custom properties at the top of your <style> block and reference "
    "them throughout, rather than hard-coding values in multiple places.\n"
    "Where a token you need is genuinely absent from this sheet, derive it from what IS here "
    "(a tint or shade of a listed colour) and say so in a comment — never introduce an unrelated "
    "hue. If the sheet says NOT AVAILABLE, do not guess a palette: build in neutral greys and mark "
    "every colour as a placeholder needing the client's real values."
)


# Appended for the replica stages. The token sheet says what the colours are; the screenshots say
# what the page looks like, and the two answer different questions.
_SCREENSHOT_DIRECTIVE = (
    "\n  - **Reference screenshots of the live page are attached to this message as images.** They "
    "are the visual UI layout brief: section order, header composition, hero composition, padding "
    "and whitespace rhythm, footer layout, and how elements sit side by side. Strictly reproduce "
    "that visual layout. Read the tokens above for every colour, font, padding, and component value "
    "— do not eyedrop them off the image, which is compressed and downscaled. And do not copy any "
    "words out of the screenshots: the copy for this page is the generated content, and the old "
    "page's wording is exactly what is being replaced."
)

# Appended for the replica stages whose sheet carries a structure walk. The tokens say what the
# page is made of and the screenshots show the top of it; this is the only input that states the
# whole sequence, so it is the one the layout has to be built from.
_PAGE_STRUCTURE_DIRECTIVE = (
    "\n  - **The Page structure section is the layout brief, and it is measured off the live DOM "
    "rather than suggested.** Build the same bands in the same order, with the same grid counts "
    "(three cards means three, not four), container widths, and heading levels. Always render the "
    "client's real logo in the header and footer. Replicate the reference header and footer layouts, "
    "the nav labels, the contact details, and the client / partner logo URLs and alt text in trust "
    "bands verbatim — those are real and a plausible substitute is simply wrong. What you replace is "
    "the wording of the headings and body copy, which comes from the generated content. If the "
    "generated content has nothing to say in a band the page has, keep the band and write real copy "
    "for it; do not delete it, and do not invent an extra band to use up material."
)

# Appended for the theme stages. Without it a lead magnet handed a landing page's design system
# builds a landing page.
_THEME_ONLY_DIRECTIVE = (
    "\n  - **This is a theme, not a template.** Match the colours, typefaces, radii and the logo so "
    "the asset is unmistakably the same brand. Do NOT reproduce the source page's layout, section "
    "order, or page furniture \u2014 this asset has its own structure, defined by the master prompt "
    "below."
)

_GENERATED_IMAGES_HEADING = "===== GENERATED IMAGES ====="


def _generated_images_block(images: tuple[GeneratedImage, ...]) -> str:
    """Real, already-hosted URLs for this stage's imagery — never an instruction to produce one.
    The model reading this has no tool access to an image generator, so telling it to "generate
    the image and place the returned URL" (the previous version of this block) was unfollowable:
    it could only fabricate a plausible-looking URL. This hands it the resolved value instead, the
    same fix `design_tokens.py` documents for a measured colour versus an invented one. The URL
    points at this backend's own `/media/generated/...` — see `app/services/media_storage.py`.
    """
    lines = [
        f"\n\n{_GENERATED_IMAGES_HEADING}\n",
        "Use these exact URLs verbatim in an <img> src for the section named. Do not alter the URL, "
        "do not embed it as base64, and do not substitute a stock photo or an invented URL.\n",
    ]
    for image in images:
        lines.append(f"- **{image.role}** (`{image.size}`): {image.url}\n")
    lines.append(f"===== END {_GENERATED_IMAGES_HEADING.strip('= ')} =====\n")
    return "".join(lines)


def _brand_token_block(design_markdown: str | None, *, theme_only: bool = False, has_screenshots: bool = False) -> str:
    """The extracted design sheet, fenced and bound, or "" when the stage has none.

    Deliberately shares the reference-library framing rather than arriving as another INPUTS line.
    An INPUTS field is something the operator filled in and the prompt may weigh against other
    inputs; this is a measurement that overrides the model's taste, and it needs to read that way.

    The directive is assembled per stage rather than being one constant, because the two views of
    the same document carry opposite instructions about layout: the replica stages must copy it and
    the theme stages must not.
    """
    if not design_markdown or not design_markdown.strip():
        return ""
    directive = _BRAND_TOKEN_DIRECTIVE
    if theme_only:
        directive += _THEME_ONLY_DIRECTIVE
    # Read off the sheet rather than passed in. The rule this pipeline keeps breaking is a directive
    # that points at a section which is not in the document, and the model fills that gap itself;
    # deriving the flag from the text about to be sent makes the two incapable of drifting. It also
    # means an older stored sheet, captured before the structure walk existed, simply does not get
    # the clause — which is correct, because it does not have the section either.
    if not theme_only and STRUCTURE_HEADING in design_markdown:
        directive += _PAGE_STRUCTURE_DIRECTIVE
    if has_screenshots:
        directive += _SCREENSHOT_DIRECTIVE
    return (
        f"===== BEGIN {BRAND_DESIGN_TOKENS} =====\n"
        f"HOW THIS DOCUMENT BINDS YOUR RESPONSE:\n{directive}\n\n"
        f"{design_markdown.strip()}\n"
        f"===== END {BRAND_DESIGN_TOKENS} =====\n\n"
    )


@dataclass(frozen=True)
class PageDesignInput:
    """The client's page design, in the two views the stages need, plus the shots.

    A dataclass rather than three parameters threaded through six functions: `build_prompt`,
    `build_stage_request`, both stream entry points and the router all pass it along unchanged, and
    every one of them would otherwise grow the same three arguments.

    Built by `app/services/design_md.py`. Passing `None` anywhere is a supported state and means
    "this run has no readable page" \u2014 the block is omitted and each stage's own prompt falls back
    to asking for brand values.
    """

    #: The full DESIGN.md. For the stages rebuilding a page.
    design_md: str = ""
    #: Colours, type, shapes, logo. For the stages that must match the brand, not the layout.
    theme_brief: str = ""
    #: Public image URLs, already filtered to what the model API will accept.
    screenshot_urls: tuple[str, ...] = ()
    #: Already-generated Runway images — real, hosted URLs, not prompts. Generated server-side
    #: once per run by `resolve_page_design` (see `app/services/image_briefs.py`); empty when
    #: Runway is unconfigured or every generation failed, in which case the stage simply gets no
    #: imagery block rather than an instruction it cannot carry out.
    generated_images: tuple[GeneratedImage, ...] = ()

    def sheet_for(self, asset_id: str) -> str:
        """Which view this stage reads. Falls back to whichever one was built, so a caller that
        only has one (an older stored entry, a theme-only capture) still gets a sheet."""
        if asset_id in PAGE_REPLICA_STAGES:
            return self.design_md or self.theme_brief
        return self.theme_brief or self.design_md

    def screenshots_for(self, asset_id: str) -> tuple[str, ...]:
        return self.screenshot_urls if asset_id in SCREENSHOT_STAGES else ()

    def generated_images_for(self, asset_id: str) -> tuple[GeneratedImage, ...]:
        return self.generated_images if asset_id in PAGE_REPLICA_STAGES else ()


class UnknownStageError(KeyError):
    pass


class UnknownPhaseError(KeyError):
    pass


def _config(asset_id: str, phase: str = DEFAULT_PHASE) -> StageConfig:
    try:
        configs = CONFIGS_BY_PHASE[phase]
    except KeyError as exc:
        raise UnknownPhaseError(phase) from exc
    try:
        return configs[asset_id]
    except KeyError as exc:
        # Named with the phase, because "unknown stage: blog" is baffling when blog plainly exists —
        # the real cause is a stage being asked for in a phase that does not run it.
        raise UnknownStageError(f"{asset_id!r} is not a {phase} stage") from exc


def has_stage(asset_id: str, phase: str = DEFAULT_PHASE) -> bool:
    """Whether `phase` runs `asset_id` at all — the route's 404 check."""
    return asset_id in CONFIGS_BY_PHASE.get(phase, {})


def _apply_reference_injections(asset_id: str, answers: dict[str, str]) -> dict[str, str]:
    """Replace "USE DEFAULT"-style placeholders with the referenced document's real contents.

    An operator who actually pasted their own framework keeps it — only the default/blank
    sentinel values listed in `_INJECT_WHEN_ANSWER_IS` are overwritten.
    """
    resolved = dict(answers)
    for (target_asset_id, field_id), filename in REFERENCE_DOC_INJECTIONS.items():
        if target_asset_id != asset_id:
            continue
        current = (resolved.get(field_id) or "").strip()
        if current.upper() not in _INJECT_WHEN_ANSWER_IS:
            continue
        resolved[field_id] = (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
        logger.info("Injected reference doc %s into %s.%s", filename, asset_id, field_id)
    return resolved


def _load_schema_fields(cfg: StageConfig) -> list[tuple[str, str]]:
    """This stage's (field_id, label) pairs in INPUTS-block order, with any phase delta applied.

    One registry per asset serves both phases: `drop_fields` removes the inputs a phase's own
    prompt file does not have, and `label_overrides` re-words the ones it words differently.
    """
    data = json.loads((_SCHEMAS_DIR / cfg.schema_file).read_text(encoding="utf-8"))
    relabelled = dict(cfg.label_overrides)
    return [
        (f["field_id"], relabelled.get(f["field_id"], f["label"]))
        for f in data["fields"]
        if f["field_id"] not in cfg.drop_fields
    ]


def _load_master_prompt_body(cfg: StageConfig) -> str:
    """Read the stage's real prompt file and return everything from its actual instructions
    onward — the INPUTS placeholder block stripped off (we build that block ourselves)."""
    text = (_PROMPTS_DIR / cfg.prompt_file).read_text(encoding="utf-8")
    marker = cfg.marker_override or _DEFAULT_MARKER_SUBSTR
    idx = text.index(marker)
    if cfg.marker_override:
        return text[idx:].strip()
    newline_idx = text.index("\n", idx)
    return text[newline_idx:].strip()


def _render_field(label: str, value: str) -> str:
    """One line of the INPUTS block — or a fenced block, when the answer is itself multi-line.

    Every prompt file writes its INPUTS as one `Label: answer` per line, which stops being
    unambiguous the moment an answer spans lines: a whole scraped page, a pasted ICP document, a
    competitor benchmark. Read as plain lines, the field after it looks like part of it. Fencing
    those (and only those) keeps single-line answers byte-identical to what the prompts describe,
    while giving the model an explicit end to every long one.
    """
    if not value:
        return f"{label}: (not specified)"
    if "\n" not in value:
        return f"{label}: {value}"
    return f"{label}:\n--- begin {label} ---\n{value}\n--- end {label} ---"


def _prompt_parts(
    asset_id: str,
    answers: dict[str, str],
    phase: str = DEFAULT_PHASE,
    page_design: PageDesignInput | None = None,
    voice: IndustryProfile | None = None,
) -> tuple[str, str]:
    """This stage's prompt, split at its cache boundary: (reference library, everything else).

    The split is the whole point, and it is why the brand tokens no longer lead. Prompt caching is a
    prefix match: whatever sits first has to be byte-identical from call to call, or nothing after it
    caches either. The reference library qualifies and the brand tokens do not — the library is one
    file plus one fixed directive, identical across all eleven stages that cite it, while the tokens
    are measured per client from a live page. Leading with the tokens (which is what this function
    used to do, for the five HTML stages) put the volatile part in front of the 12k-token static part
    and made the largest cacheable block in the pipeline uncacheable.

    Moving them costs little: they still precede the INPUTS block and the master prompt body, so they
    are still read before the instruction to build, which is what their directive needs.

    The industry voice block (`industry_voice.voice_block`) leads the tail on every stage, HTML or
    not, for the same reason and on the same side of the cache boundary: it is per client, so it
    must not sit in front of the library. It is "" for the default bucket, which keeps a run that
    never set an industry byte-identical to the prompt before this block existed.
    """
    cfg = _config(asset_id, phase)
    answers = _apply_reference_injections(asset_id, answers)
    lines = [_render_field(label, (answers.get(field_id) or "").strip()) for field_id, label in _load_schema_fields(cfg)]
    brand = ""
    if page_design is not None and asset_id in BRAND_TOKEN_STAGES:
        brand = _brand_token_block(
            page_design.sheet_for(asset_id),
            theme_only=asset_id in BRAND_THEME_STAGES,
            has_screenshots=bool(page_design.screenshots_for(asset_id)),
        )

    return (
        _load_reference_library(asset_id),
        voice_block(voice)
        + brand
        + "— INPUTS (fill in before submitting) —\n\n"
        + "\n".join(lines)
        + "\n\n— END OF INPUTS —\n\n"
        + _load_master_prompt_body(cfg),
    )


def build_prompt(
    asset_id: str,
    answers: dict[str, str],
    phase: str = DEFAULT_PHASE,
    page_design: PageDesignInput | None = None,
    voice: IndustryProfile | None = None,
) -> str:
    """The whole prompt as one string: any reference library this stage cites, then its own "fill in
    before submitting" INPUTS block reproduced from the caller's intake, then the file's real master
    prompt unchanged.

    The library goes first and the master prompt last, because the master prompt is what the model
    has to act on: every one of these files ends by telling it to proceed ("Now proceed using the
    Wish/Mode selected in the inputs above"), and a 44KB reference document appended *after* that
    would put 11k tokens between the final instruction and the response. Front-loading the static
    document also keeps INPUTS directly above the body that refers to them as "the inputs above".

    `generate_stage_stream` sends these two halves as separate request fields rather than as this
    concatenation, so the first half can carry a cache breakpoint — see `build_stage_request`. The
    text the model sees is identical either way, which is what this function pins.
    """
    library, tail = _prompt_parts(asset_id, answers, phase, page_design, voice)
    return library + tail


def build_stage_request(
    asset_id: str,
    answers: dict[str, str],
    phase: str = DEFAULT_PHASE,
    page_design: PageDesignInput | None = None,
    voice: IndustryProfile | None = None,
) -> tuple[list[dict[str, object]] | None, str | list[dict[str, object]]]:
    """The same prompt as `build_prompt`, as `(system_blocks, user_content)` ready for the API.

    The reference library becomes a cached `system` block and everything volatile stays in the user
    message. Two things make that worth doing:

      * It is the same document for all eleven stages that cite it, so a run writes the entry once
        and the rest read it at a tenth of the input price. Measured on the authored files, the
        library is 12,300 of each stage's 13,805-17,812 static tokens.

        Caches are scoped per model, so that is two entries rather than one: the nine Sonnet stages
        share one, and `book` and `podcast` share a Haiku one. The Haiku pair roughly breaks even
        (one write plus one read costs about what two uncached reads cost at Haiku rates) and is
        left in for uniformity — the saving is the Sonnet group.
      * The API renders `system` before `messages`, so the library is the prefix by construction
        rather than by this module policing what might get prepended later.

    Returns `None` for the system half on the four stages that have no library (icp, funnel,
    sms_sequence, plan_of_action) rather than an empty block: a sub-minimum prefix does not cache,
    and an empty `system` list is noise on the wire.

    The user half is a plain string unless the stage gets screenshots, in which case it becomes a
    content-block list with the images first. Images lead because the text ends with the master
    prompt's instruction to proceed, and an image appended after that sits between the instruction
    and the response.
    """
    library, tail = _prompt_parts(asset_id, answers, phase, page_design, voice)

    if page_design is not None:
        images = page_design.generated_images_for(asset_id)
        if images:
            tail += _generated_images_block(images)

    user_content: str | list[dict[str, object]] = tail
    shots = page_design.screenshots_for(asset_id) if page_design is not None else ()
    if shots:
        user_content = [
            # A URL source, not base64: Context.dev hosts the image, so it never passes through this
            # backend and costs nothing to store on the run.
            {"type": "image", "source": {"type": "url", "url": url}}
            for url in shots
        ] + [{"type": "text", "text": tail}]

    if not library:
        return None, user_content
    return (
        [{"type": "text", "text": library, "cache_control": {"type": "ephemeral", "ttl": _CACHE_TTL}}],
        user_content,
    )


def _stream_kwargs(
    cfg: StageConfig,
    system_blocks: list[dict[str, object]] | None,
    user_content: str | list[dict[str, object]],
) -> dict[str, object]:
    """The request body shared by the generation and revision streams.

    `effort` is gated on the model as well as on the config, because it is rejected outright on
    Haiku 4.5 rather than ignored — see `EFFORT_CAPABLE_MODELS`.
    """
    kwargs: dict[str, object] = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "messages": [{"role": "user", "content": user_content}],
    }
    if system_blocks is not None:
        kwargs["system"] = system_blocks
    if cfg.effort and cfg.model in EFFORT_CAPABLE_MODELS:
        kwargs["output_config"] = {"effort": cfg.effort}
    return kwargs


def build_revision_prompt(previous_draft: str, note: str, voice: IndustryProfile | None = None) -> str:
    """A deliberately different, much smaller prompt for the "Refine / Request Changes" path:
    hands Claude the exact previous draft plus the operator's requested change, rather than
    re-running the entire master prompt from scratch (which would ignore the previous output
    and likely produce a different document, not a revision of the one being reviewed).

    The industry voice block still leads it: a revision written without it drifts back to the
    pipeline's default voice on exactly the paragraphs the operator asked to change."""
    return voice_block(voice) + (
        "You previously produced the following document:\n\n"
        "----- PREVIOUS DRAFT -----\n"
        f"{previous_draft}\n"
        "----- END PREVIOUS DRAFT -----\n\n"
        "The operator reviewed this draft and requested the following change:\n"
        f'"{note.strip()}"\n\n'
        "Apply the requested change and return the FULL revised document in the same format and "
        "structure as the previous draft — not a diff, not a summary of changes, the complete "
        "document, ready to replace the previous draft."
    )


# Called with the measured usage of a finished call. Async because the only implementation writes a
# row (see `app/services/usage.py`); this module never touches the database itself.
OnUsage = Callable[[CallUsage], Awaitable[None]]


async def generate_stage_stream(
    asset_id: str,
    answers: dict[str, str],
    phase: str = DEFAULT_PHASE,
    on_usage: OnUsage | None = None,
    page_design: PageDesignInput | None = None,
    voice: IndustryProfile | None = None,
) -> AsyncIterator[str]:
    """Stream this stage's real generation as Markdown text deltas."""
    cfg = _config(asset_id, phase)
    client = get_client()
    system_blocks, user_content = build_stage_request(asset_id, answers, phase, page_design, voice)

    logger.info(
        "Streaming stage=%s phase=%s model=%s effort=%s cached_prefix=%s prompt=%s industry=%s",
        asset_id,
        phase,
        cfg.model,
        cfg.effort if cfg.model in EFFORT_CAPABLE_MODELS else "n/a",
        system_blocks is not None,
        cfg.prompt_file,
        voice.bucket if voice is not None else "default",
    )
    if page_design is not None and asset_id in BRAND_TOKEN_STAGES:
        logger.info(
            "Stage %s design sheet: view=%s chars=%s screenshots=%s",
            asset_id,
            "theme" if asset_id in BRAND_THEME_STAGES else "full",
            len(page_design.sheet_for(asset_id)),
            len(page_design.screenshots_for(asset_id)),
        )
    started = time.monotonic()

    async with client.messages.stream(**_stream_kwargs(cfg, system_blocks, user_content)) as stream:
        async for text in stream.text_stream:
            yield text

        final = await stream.get_final_message()
        # cache_read/cache_write are logged because they are the only way to tell a working
        # breakpoint from a decorative one: a warmed run should show reads, not writes. Three
        # consecutive writes with zero reads is the TTL-expiry signature that `_CACHE_TTL` fixes.
        logger.info(
            "Stream done stage=%s model=%s stop_reason=%s input_tokens=%s output_tokens=%s "
            "cache_read=%s cache_write=%s",
            asset_id,
            final.model,
            final.stop_reason,
            final.usage.input_tokens,
            final.usage.output_tokens,
            getattr(final.usage, "cache_read_input_tokens", None),
            getattr(final.usage, "cache_creation_input_tokens", None),
        )
        if final.stop_reason == "max_tokens":
            logger.warning("Stage=%s hit the %s-token cap and was truncated", asset_id, cfg.max_tokens)
        if on_usage is not None:
            await on_usage(
                CallUsage.from_response(
                    final,
                    requested_model=cfg.model,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )


async def generate_revision_stream(
    asset_id: str,
    previous_draft: str,
    note: str,
    phase: str = DEFAULT_PHASE,
    on_usage: OnUsage | None = None,
    voice: IndustryProfile | None = None,
) -> AsyncIterator[str]:
    """Stream a revision of `previous_draft` per the operator's `note`, using the same model
    tier as the stage's original generation."""
    cfg = _config(asset_id, phase)
    client = get_client()
    prompt = build_revision_prompt(previous_draft, note, voice)

    logger.info("Streaming revision stage=%s model=%s effort=%s", asset_id, cfg.model, cfg.effort)
    started = time.monotonic()

    # No cached prefix: a revision prompt is the previous draft plus a note, and both are unique to
    # this call. There is nothing stable in front of them to cache.
    async with client.messages.stream(**_stream_kwargs(cfg, None, prompt)) as stream:
        async for text in stream.text_stream:
            yield text

        final = await stream.get_final_message()
        logger.info(
            "Revision stream done stage=%s model=%s stop_reason=%s input_tokens=%s output_tokens=%s",
            asset_id,
            final.model,
            final.stop_reason,
            final.usage.input_tokens,
            final.usage.output_tokens,
        )
        if final.stop_reason == "max_tokens":
            logger.warning("Revision for stage=%s hit the %s-token cap and was truncated", asset_id, cfg.max_tokens)
        if on_usage is not None:
            await on_usage(
                CallUsage.from_response(
                    final,
                    requested_model=cfg.model,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )


# ----------------------------------------------------------------------------------------------
# The replica assembly pass
#
# A second, small call that runs *after* a replica stage's own generation, and only for the stages
# in `PAGE_REPLICA_STAGES` when a page template was captured.
#
# It is a separate call rather than a change to those stages, and that is the whole reason this
# fits: the canonical prompt files in `assets/Prompts/` run unmodified — that is the contract this
# module opens by stating — and they emit a whole document (a mode table, seven audits, a built
# page, an implementation pack). Nothing about that changes. This pass reads the document that call
# produced, and maps the copy inside it onto the real page's slots.
#
# So a replica stage now has two deliverables: the stage's own document, exactly as before, plus
# the client's real page with the new wording written into it.
#
# It is cheap in the way that matters. The model is handed a copy deck (3-6KB) and the draft it
# just wrote, and returns a JSON object of a few thousand tokens. It never emits HTML, so the
# truncation failure the `max_tokens` note above describes at length — a document cut off two
# thirds through, after the whole response has been paid for — has no way to occur here.
# ----------------------------------------------------------------------------------------------

#: Sonnet, not Haiku. The task looks mechanical and is not: it has to read a long strategy document
#: and decide which of its headlines belongs in slot 12 of band 4, which is a comprehension
#: problem. Haiku was not measured on it, so the cheaper tier is a change to make deliberately and
#: with a comparison in hand, not an assumption to start from.
ASSEMBLY_MODEL = SONNET

#: The output is a JSON object of short strings — a 200-slot page is comfortably inside 16k. Not
#: raised "just in case": unlike the document stages, a response that wants more than this is
#: a response that has misunderstood the format, and truncating it is the correct outcome.
ASSEMBLY_MAX_TOKENS = 16000

#: `low`, where the stages sit at `medium`. See the effort note above: thinking bills as output,
#: and this call is a mapping decision per line rather than a document to compose.
ASSEMBLY_EFFORT = SHORT_FORM_EFFORT

_ASSEMBLY_INSTRUCTION = (
    "The document above is the deliverable you just produced for this client. Its new copy now has "
    "to be placed onto the client's real page.\n\n"
    "The page is reproduced exactly by this system — its markup, layout, styling, images, inline "
    "icons, logo, and links are the client's own and are carried over untouched. You are not "
    "rebuilding it and you are not being shown it. Your only job is to say what each numbered slot "
    "should now read.\n\n"
    "Take the wording from the document above wherever it supplies it: its rewritten headlines, "
    "subheads, body copy and button labels go into the matching slots. Where the document has "
    "nothing for a slot, write copy for it that is consistent with the document's positioning, "
    "audience and offer — or omit the slot to keep the client's existing wording, which is the "
    "right answer for a line that is already correct.\n\n"
    "Respond with the JSON object and nothing else — no preamble, no explanation, no code fence "
    "commentary."
)


def build_assembly_request(copy_deck: str, stage_draft: str) -> str:
    """The assembly prompt: the finished stage document, then the deck, then the instruction.

    The deck goes *after* the draft and immediately before the instruction, which is the same
    ordering argument `build_prompt` makes about the master prompt going last: the deck is what the
    model has to act on line by line, and putting a long strategy document between it and the
    instruction to answer costs accuracy on the slots at the end of the list.
    """
    return (
        "===== BEGIN GENERATED DELIVERABLE =====\n"
        f"{stage_draft.strip()}\n"
        "===== END GENERATED DELIVERABLE =====\n\n"
        f"{copy_deck.strip()}\n\n"
        f"{_ASSEMBLY_INSTRUCTION}"
    )


async def generate_replica_copy(
    copy_deck: str,
    stage_draft: str,
    on_usage: OnUsage | None = None,
) -> str:
    """Run the assembly pass and return the model's raw reply.

    Returns the text rather than a parsed mapping: `page_replica.parse_copy_map` owns the parsing,
    including what to do with a reply it cannot read, and splitting that across two modules would
    put half the tolerance here and half there.

    Not a stream. Every other call in this module streams because an operator is watching a
    document appear; nothing here is worth showing incrementally — a half-received JSON object is
    not a partial answer, it is an unparseable one.
    """
    client = get_client()
    prompt = build_assembly_request(copy_deck, stage_draft)

    kwargs: dict[str, object] = {
        "model": ASSEMBLY_MODEL,
        "max_tokens": ASSEMBLY_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if ASSEMBLY_MODEL in EFFORT_CAPABLE_MODELS:
        kwargs["output_config"] = {"effort": ASSEMBLY_EFFORT}

    logger.info(
        "Replica assembly: model=%s deck_chars=%d draft_chars=%d",
        ASSEMBLY_MODEL,
        len(copy_deck),
        len(stage_draft),
    )
    started = time.monotonic()
    response = await client.messages.create(**kwargs)

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    logger.info(
        "Replica assembly done stop_reason=%s input_tokens=%s output_tokens=%s reply_chars=%d",
        response.stop_reason,
        response.usage.input_tokens,
        response.usage.output_tokens,
        len(text),
    )
    if response.stop_reason == "max_tokens":
        # Named loudly. A truncated JSON object parses as nothing, so this is the difference between
        # a page with new copy and a page with the client's original copy throughout.
        logger.warning(
            "Replica assembly hit the %s-token cap; the copy map is likely unparseable and the "
            "page will assemble with the client's original wording",
            ASSEMBLY_MAX_TOKENS,
        )
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(
                response,
                requested_model=ASSEMBLY_MODEL,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
    return text
