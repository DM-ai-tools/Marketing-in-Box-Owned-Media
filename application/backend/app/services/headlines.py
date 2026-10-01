"""Headline / topic suggestion, as a human-selectable gate in front of a stage's generation.

The pipeline already puts `COMPREHENSIVE_HEADLINE_FRAMEWORK.md` into eleven stages
(`REFERENCE_LIBRARY` in `app/services/generation.py`), where it constrains the titles a stage
writes inside its own deliverable. That is a *style* constraint on prose the operator never gets to
choose between: the Lead Magnet prompt, for instance, generates three to five candidate concepts,
scores them against its own rubric, declares its own winner, and builds that one. The four the
operator might have preferred are never shown.

This module is the other half. It produces a list of candidate headlines *before* a stage runs, as
structured data, so the operator picks one (or several) and the stage is then generated about the
thing they picked.

Three properties matter, and each is enforced somewhere specific rather than merely requested:

  Anchored.  Every candidate is about the service the run is for. Phase 1 anchors on the headline
             service ("Social Media Marketing"), Phase 2 on the sub-service ("Meta Ads"). The
             anchor is stated as a hard constraint in the prompt AND checked afterwards in
             `_ground_candidates`, because a prompt instruction is a request and a filter is not.

  Grounded.  Candidates are built from the run's own keyword cluster report — real primary
             keywords with real search volume and a real intent — not from what the model imagines
             people search for. A candidate naming a keyword outside the cleaned set is re-checked
             against `keywords.is_relevant`, exactly as `keywords.validate_clusters` re-checks an
             invented cluster term. Same gate, same reason.

  Framework-bound.  The framework is injected in full, and each candidate must *declare* how it
             used it: traffic temperature, formula family, which curiosity elements, and whether it
             passes the Part 11 pre-publication checklist. Declared structure is checkable;
             "follow the framework" is not.

Trend evidence comes from Claude's `web_search` tool, the same server tool `competitor.py` already
uses, scoped per asset (see `SLOTS`). It is what makes "viral / trendy" an observation rather than
an adjective the model applied to its own output.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services import keywords as keywords_service
from app.services.claude_client import get_client
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../application/backend
_PROMPTS_DIR = _BACKEND_ROOT / "assets" / "Prompts"

HEADLINE_FRAMEWORK = "COMPREHENSIVE_HEADLINE_FRAMEWORK.md"

SONNET = "claude-sonnet-5"
_MAX_TOKENS = 16000

# See the `cache_control` block in `suggest_headlines` for why this is an hour and not the default.
_CACHE_TTL = "1h"

# Ten is the floor the operator asked for, not a target. Asking for a couple more than will be
# shown leaves room for `_ground_candidates` to drop an off-anchor one without the card falling
# below ten — a filter that can empty the list is worse than no filter.
DEFAULT_COUNT = 10
_OVERSHOOT = 4
MAX_COUNT = 25

# Measured, not chosen from a table. This gate was taking 278 seconds and timing out, and the tool
# variant was why. Same prompt, same model, three configurations:
#
#   web_search_20260209   278.4s   input 347,925   output 30,073   searches 0
#   web_search_20250305   147.5s   input  22,053   output 16,000   searches 0   (hit the cap)
#   no tool               123.0s   input  19,966   output 14,727   searches 0
#
# The newer variant sends a *twenty-nine-fold* multiple of the prompt's own token count, which only
# happens if the server-side tool loop is iterating and re-sending the whole 45KB framework each
# time. `output_tokens` exceeding `max_tokens` confirms it — that is impossible in a single turn.
# At Sonnet 5's rates that run cost about a dollar, per gate, to produce nothing extra.
#
# So this is back to the variant `competitor.py` uses, which does not run away. The upgrade was mine
# and it was a regression: a newer tool type is only better if the account can actually use it.
_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

# Off unless explicitly switched on, which is a reversal — it used to be on unless switched off.
#
# `searches=0` in all three runs above: not one search completed, on any variant, with or without
# the tool declared. Whatever the cause (web search not enabled for the key, or the tool never being
# reached), declaring it bought zero trend evidence and cost between 25 and 155 extra seconds per
# gate. Paying that for nothing is worse than not having the feature.
#
# It is not deleted, because grounded trend evidence is genuinely wanted here — see the
# `trend_evidence` field and its prompt directive. Set `HEADLINES_WEB_SEARCH=1` to turn it back on
# once web search is confirmed working on the key, and re-measure before trusting it.
_WEB_SEARCH_DEFAULT_ON = False


class UnknownSlotError(KeyError):
    pass


class HeadlineParseError(ValueError):
    pass


# --------------------------------------------------------------------------------------
# Slots
#
# A "slot" is one place in the pipeline where a topic has to be decided. It is deliberately not the
# same thing as an asset: `blog` has one slot, but a stage could grow a second (a series name and
# an episode title) without either becoming a new asset.
#
# `channel` and `char_budget` are the framework's own per-channel limits, quoted from its Part 3 —
# a title tag is 50-60 characters and a Facebook headline is 25-40, and a candidate that ignores
# that is unusable on the channel it was written for. `extras` are the slot-specific fields a
# candidate must carry beyond the headline itself, which is what makes a lead-magnet suggestion
# actionable (a format and a mechanic) rather than just a name.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotConfig:
    slot: str
    asset_id: str
    label: str
    # What the operator is choosing, in the card's own words.
    subject: str
    channel: str
    char_budget: str
    # Whether the operator picks several. Lead magnets and content pillars are sets by nature; a
    # blog post's title is not.
    multi: bool = False
    # How many a multi-select slot expects to end up with, for the card's own guidance.
    suggested_selection: int = 1
    extras: tuple[tuple[str, str], ...] = ()
    # Off only where trend evidence would be noise rather than signal — see `web_search_enabled`.
    web_search: bool = True
    guidance: str = ""


_LEAD_MAGNET_EXTRAS = (
    ("format", "the delivery format, from the Lead-Magnet prompt's Format Library (guide, checklist, template, calculator, quiz, diagnostic, scorecard, audit, playbook, benchmark report, interactive tool, …)"),
    ("mechanic", "one sentence on what the visitor actually does and receives"),
    ("consumption_time_minutes", "integer, must be under 20 for a true Rung 0 asset"),
    ("gate", "what is exchanged for it (email only, email + company, …) — as low-friction as the format allows"),
)

SLOTS: dict[str, SlotConfig] = {
    "lead_magnet_concept": SlotConfig(
        slot="lead_magnet_concept",
        asset_id="lead_magnet",
        label="Lead magnet concepts",
        subject="lead magnet",
        channel="Landing page headline",
        char_budget="60-80 characters",
        multi=True,
        suggested_selection=10,
        extras=_LEAD_MAGNET_EXTRAS,
        guidance=(
            "Vary the format deliberately: no more than three candidates may share a format, and "
            "the set must span at least four distinct formats from the Format Library. Ten "
            "checklists is not ten lead magnets. Vary the funnel stage too — a set that is "
            "entirely TOFU gives the operator nothing to build an ascension path from."
        ),
    ),
    "blog_topic": SlotConfig(
        slot="blog_topic",
        asset_id="blog",
        label="Blog topics",
        subject="blog post",
        channel="Blog post headline / title tag",
        char_budget="50-60 characters for the title tag, 60-80 for the on-page H1",
        # Plural because a blog is a schedule, not a post. One topic per run made the operator
        # re-enter the whole stage for the second post, and every one of those re-entries paid
        # again for the same ICP, competitor and keyword context — so the stage now writes every
        # topic picked here (`universal-blog-generation-prompt.md`, Step 4).
        multi=True,
        # Lower than the lead magnet's ten on purpose: each pick here is a 2,000-word post with its
        # own intent analysis, outline, SEO checklist and brief, and five of those is already a
        # month of publishing. The operator can take more — this is the card's guidance, not a cap.
        suggested_selection=5,
        guidance=(
            "Blog headlines are the framework's Part 3D case: they must satisfy a search intent "
            "AND earn a click. Every candidate needs a primary keyword from the cluster report "
            "with informational or commercial intent — a transactional keyword belongs on a "
            "landing page, not a blog post. "
            "The operator picks several and every pick gets written as a full post, so this is a "
            "content calendar rather than a shortlist for one slot: no two candidates may share a "
            "primary keyword or serve the same search intent, because two posts over one keyword "
            "cannibalise each other and the weaker one buries the stronger. Draw each from a "
            "different cluster where the report has the clusters to allow it, and spread the set "
            "across awareness levels so it reads as a sequence a reader could move through."
        ),
    ),
    "pillar_head_term": SlotConfig(
        slot="pillar_head_term",
        asset_id="pillar_page",
        label="Pillar page head terms",
        subject="pillar page",
        channel="Landing page headline / title tag",
        char_budget="50-60 characters for the title tag",
        guidance=(
            "A pillar head term is the broadest keyword the page can credibly own, not the "
            "catchiest phrase. Prefer the highest-volume commercial or transactional cluster "
            "primaries, and say in `why_it_works` which subtopic clusters would link up into it."
        ),
    ),
    "webinar_topic": SlotConfig(
        slot="webinar_topic",
        asset_id="webinar",
        label="Webinar topics",
        subject="webinar",
        channel="Landing page headline",
        char_budget="60-80 characters",
        # Plural for the same reason `blog_topic` is: a business runs a programme of webinars, not
        # one. Single-select made the operator re-enter the whole stage for a second one, paying
        # again for the same ICP, competitor synthesis and keyword context to get it — and the
        # competitor synthesis is the expensive half, which is shared across every topic in a set
        # rather than being per-topic work.
        multi=True,
        # Three, against the blog's five and the lead magnet's ten, because a pick here is not a
        # title — it is a script, a slide brief, a registration page and an email sequence. Three
        # is a quarter's programme and about what one response carries to full depth. The card
        # states it as guidance; the operator can take more.
        suggested_selection=3,
        guidance=(
            "A webinar title has to promise a transformation worth an hour of someone's calendar. "
            "Lead with the outcome and the timeframe; a topic that reads like a syllabus fails. "
            "The operator picks several and every pick is built as its own full package, so this "
            "is a programme rather than a shortlist for one slot: no two candidates may promise "
            "the same transformation to the same awareness level, and the set should span the "
            "funnel so an attendee has a reason to come back for the next one instead of choosing "
            "between near-identical hours."
        ),
    ),
    "podcast_episode_topic": SlotConfig(
        slot="podcast_episode_topic",
        asset_id="podcast",
        label="Podcast episode topics",
        subject="podcast episode",
        channel="Blog post headline",
        char_budget="50-60 characters",
        multi=True,
        suggested_selection=10,
        guidance=(
            "The podcast prompt asks for ten episode titles, so these are a season, not ten "
            "variations of one idea. Each must stand alone as an episode while sharing the "
            "service anchor, and the set should progress from awareness to decision."
        ),
    ),
    "book_topic": SlotConfig(
        slot="book_topic",
        asset_id="book",
        label="Book topics",
        subject="book",
        channel="Landing page headline",
        char_budget="60-80 characters",
        # Plural, because the webinar stage upstream is now a programme rather than one hour: a run
        # with three webinar packages has three bodies of delivered material, and each can carry its
        # own book. Every pick is built (`Webinar-to-Book-Architect-Prompt.md`, Step 0B).
        multi=True,
        # Two. This is the largest deliverable in the pipeline — a pick here is a whole manuscript,
        # not a title — and the stage's own Book Format input reaches "25,000-50,000+ words", which
        # one response cannot hold even once. Two short-format books fit; two long ones do not, and
        # the prompt is written to build what it can to full depth and name what it did not reach
        # rather than thinning every manuscript to fit. Guidance, not a cap.
        suggested_selection=2,
        guidance=(
            "A book title carries a whole positioning, so lead with the big promise and let the "
            "subtitle carry the specificity. Give both: `headline` is the title, and open "
            "`why_it_works` with the subtitle you would pair with it. "
            "The operator picks several and every pick is written as its own manuscript, so no two "
            "candidates may argue the same thesis: a book is a position, and two books positioned "
            "the same way compete for one shelf. Where the run has several webinars behind it, "
            "prefer candidates that draw on different ones."
        ),
    ),
    "offer_ladder_theme": SlotConfig(
        slot="offer_ladder_theme",
        asset_id="offers",
        label="Value ladder themes",
        subject="value ladder",
        channel="Offer/promotion headline",
        char_budget="25-40 characters",
        guidance=(
            "This names the through-line the whole ladder ascends along, not one rung. It has to "
            "still make sense on the free entry offer and on the highest-priced rung, so avoid "
            "anything that names a single deliverable or a single price point."
        ),
        # An offer name is priced and positioned against the competitor set the Offers stage
        # already gathers, not against what is trending this week.
        web_search=False,
    ),
    "funnel_theme": SlotConfig(
        slot="funnel_theme",
        asset_id="funnel",
        label="Funnel themes",
        subject="funnel",
        channel="Landing page headline",
        char_budget="60-80 characters",
        guidance=(
            "The promise the whole funnel is built to deliver — it appears on the entry page and "
            "is echoed at every step. State the transformation, not the mechanism."
        ),
        web_search=False,
    ),
    "content_pillar_topics": SlotConfig(
        slot="content_pillar_topics",
        asset_id="content_marketing_strategy",
        label="Content pillars",
        subject="content pillar",
        channel="Blog post headline",
        char_budget="50-60 characters",
        multi=True,
        suggested_selection=5,
        guidance=(
            "Pillars are containers for months of content, so each must be broad enough to hold a "
            "cluster and narrow enough to be distinct from the others. Map each to a different "
            "cluster from the report — two pillars over one cluster is keyword cannibalization "
            "wearing a strategy hat."
        ),
    ),
    "social_theme_taxonomy": SlotConfig(
        slot="social_theme_taxonomy",
        asset_id="social_content_strategy_audit",
        label="Social content themes",
        subject="social content theme",
        channel="Facebook / Instagram ad headline",
        char_budget="25-40 characters",
        multi=True,
        suggested_selection=6,
        guidance=(
            "Short enough to survive a feed. These are recurring themes a month of posts hangs "
            "off, not one-off post captions."
        ),
    ),
    "funnel_hub_headline": SlotConfig(
        slot="funnel_hub_headline",
        asset_id="funnel_hub_media",
        label="Funnel hub headlines",
        subject="funnel hub",
        channel="Landing page headline",
        char_budget="60-80 characters",
        guidance="The one line the hub leads with, above every media asset it links to.",
        web_search=False,
    ),
    "sequence_theme": SlotConfig(
        slot="sequence_theme",
        asset_id="sms_sequence",
        label="Sequence themes",
        subject="SMS / email sequence",
        channel="Email subject line",
        char_budget="30-50 characters",
        guidance=(
            "The through-line every message in the sequence advances. It has to work as a subject "
            "line on message one and still be recognisable on message six."
        ),
        web_search=False,
    ),
}

SLOTS_BY_ASSET: dict[str, tuple[str, ...]] = {}
for _slot in SLOTS.values():
    SLOTS_BY_ASSET.setdefault(_slot.asset_id, ())
    SLOTS_BY_ASSET[_slot.asset_id] += (_slot.slot,)


# --------------------------------------------------------------------------------------
# The anchor
#
# What every candidate must be *about*. Getting this from the wrong place is the difference between
# ten lead-magnet concepts for "Social media strategy calls" and ten for whatever the ICP happened
# to name as the client's industry — which, if that answer leaned SEO, produced SEO topics on every
# gate in the run regardless of what the operator had typed into Target Service.
#
# Resolution order, most specific first:
#
#   1. **This stage's own service field.** A Lead Magnet's `target_service_offer` is the service the
#      magnet must feed into; an Offers ladder's `target_service_offer_to_ladder` is the thing being
#      laddered. These are per-asset and they are the most precise statement of intent available.
#   2. **The run's target service.** Four of the eight gates (blog, webinar, podcast, pillar page)
#      have no service field of their own — worse, three of them sit at field index 0, so their gate
#      fires before any other answer exists. They inherit whatever service was named earlier in the
#      run, which is why the frontend files every `target_service_*` answer to a run-level fact.
#   3. **Phase 2's sub-service.** The one fact a Phase 2 leg is built around.
#   4. **The client's industry.** Last resort, and only ever a category ("Digital marketing") rather
#      than a service. It was the *first* resort before this existed, which was the bug.
# --------------------------------------------------------------------------------------

# asset_id -> the field on that asset naming the service this deliverable is for.
SERVICE_FIELD_BY_ASSET: dict[str, str] = {
    "cro": "target_service_or_sub_service",
    "lead_magnet": "target_service_offer",
    "offers": "target_service_offer_to_ladder",
    "funnel": "target_service_if_different_from_pillar_page",
    "funnel_hub_media": "service_or_product_line_being_funnel_mapped",
    "content_marketing_strategy": "primary_service_pillar_page_being_supported",
    # The services the operator picked for the page (`service_scan.py`), so the head-term
    # suggestions are about what this page covers rather than the run's headline service.
    "pillar_page": "services_covered",
}

# Run-level facts, in preference order per phase. `target_service` is written by the frontend from
# whichever asset named it first (see `CLIENT_PROFILE_SOURCES`), so a service typed at the CRO stage
# reaches the blog gate eight stages later.
_ANCHOR_FACTS_BY_PHASE: dict[str, tuple[str, ...]] = {
    "phase1": ("target_service", "industry"),
    # Phase 2 leads with its sub-service: the whole leg is for "Meta Ads", and a `target_service`
    # inherited from the parent leg would put the parent's service back on every gate — exactly the
    # cross-phase bleed the context-key scoping exists to prevent.
    "phase2": ("sub_service", "target_service", "industry"),
}

_UNUSABLE_ANSWERS = {"", "N/A", "NONE", "UNKNOWN", "TBD"}


def _normalise_service(name: str) -> str:
    """A service name reduced for comparison, so casing and punctuation do not create a mismatch."""
    return " ".join(sorted(keywords_service.tokens(keywords_service.normalize(name))))


def _usable(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.upper() in _UNUSABLE_ANSWERS:
        return None
    # The marker `findNextAskable` writes for a silently auto-filled context reference. It names a
    # document, not a service.
    if text.startswith("[[context:"):
        return None
    return text


def resolve_service_anchor(
    asset_id: str,
    answers: dict[str, str] | None,
    profile: dict[str, str] | None,
    phase: str = "phase1",
) -> tuple[str | None, str]:
    """The service this stage's candidates must be about, and where it came from.

    The source is returned alongside the value because it is worth showing: an anchor that fell all
    the way through to `industry` is a category rather than a service, and a card that says so lets
    the operator correct it instead of wondering why the topics are broad.
    """
    answers = answers or {}
    profile = profile or {}

    service_field = SERVICE_FIELD_BY_ASSET.get(asset_id)
    if service_field:
        raw = answers.get(service_field)
        if service_field == "services_covered":
            # A list with URLs and the card's own formatting; the names are the anchor.
            from app.services.service_scan import anchor_text

            raw = anchor_text(raw or "")
        own = _usable(raw)
        if own:
            return own, f"this stage's {service_field}"

    for fact in _ANCHOR_FACTS_BY_PHASE.get(phase, _ANCHOR_FACTS_BY_PHASE["phase1"]):
        value = _usable(profile.get(fact))
        if value:
            return value, fact

    return None, "unresolved"


def slot_config(slot: str) -> SlotConfig:
    try:
        return SLOTS[slot]
    except KeyError as exc:
        raise UnknownSlotError(slot) from exc


def has_slot(slot: str) -> bool:
    return slot in SLOTS


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------


@dataclass
class HeadlineContext:
    """Everything a suggestion call is grounded in, gathered by the router.

    `service_anchor` is the one field with no sensible default. Phase 1 passes the headline
    service, Phase 2 the sub-service, and the two must never be crossed — a Phase 2 run handed
    "Social Media Marketing" would suggest Meta Ads topics about the parent service, which is the
    drift the whole selection gate exists to prevent.
    """

    service_anchor: str
    phase: str = "phase1"
    business_name: str = ""
    region: str = ""
    # The stored keyword report's structured half, as written by `KeywordReport.to_context_value`.
    # Absent when the prepass was skipped or the provider failed: suggestions still work, they are
    # simply framework-grounded rather than demand-grounded, and every candidate comes back
    # `grounded: false`. Worse, but honest, and visible in the UI.
    keyword_report: dict = field(default_factory=dict)
    clean_keywords: list[dict] = field(default_factory=list)
    vocabulary: list[str] = field(default_factory=list)
    # Upstream documents already approved in this run. Truncated on the way in — the suggestion
    # call needs the audience and the competitive picture, not the full deliverables.
    icp_document: str = ""
    competitor_document: str = ""
    # Headlines already shown and rejected, so "show me 10 more" produces a genuinely different
    # batch instead of paraphrases of the first one.
    exclude: list[str] = field(default_factory=list)
    # Free-text steer from the operator ("lean more technical", "avoid anything about TikTok").
    operator_note: str = ""
    # Which service the stored keyword report was built for. Not always `service_anchor`: the report
    # is built once per run for the run's headline service, while this gate anchors on the *stage's*
    # target service, which can be narrower or different. When they disagree the report's relevance
    # vocabulary describes a wider business, and treating it as evidence of "on-service" is what let
    # off-service topics through — see `_on_anchor`.
    keyword_service: str = ""
    # Where `service_anchor` came from, for the card. An anchor that fell through to the client's
    # industry is a category rather than a service, and the operator should be able to see that
    # rather than wonder why the topics are broad.
    anchor_source: str = ""

    def keyword_service_matches_anchor(self) -> bool:
        """Whether the keyword report describes the same service this gate is anchored on."""
        if not self.keyword_service or not self.service_anchor:
            return False
        return _normalise_service(self.keyword_service) == _normalise_service(self.service_anchor)


_MAX_DOC_CHARS = 6000


def _truncate(text: str, limit: int = _MAX_DOC_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…[truncated]"


def _cluster_digest(context: HeadlineContext, limit: int = 25) -> list[dict]:
    """The cluster report reduced to what a headline actually needs.

    The full report carries every keyword in every cluster; handing all of it over would spend
    thousands of tokens restating search volumes the model does not need per candidate. What it
    does need is: which topics have demand, how much, what the searcher wants, and where in the
    funnel that sits.
    """
    digest: list[dict] = []
    for cluster in (context.keyword_report.get("clusters") or [])[:limit]:
        entries = cluster.get("keywords") or []
        digest.append(
            {
                "cluster": cluster.get("name"),
                "primary_keyword": cluster.get("primary_keyword"),
                "intent": cluster.get("intent"),
                "funnel": cluster.get("funnel"),
                "content_type": cluster.get("content_type"),
                "total_volume": sum(int(e.get("volume") or 0) for e in entries),
                "secondary_keywords": [
                    e.get("keyword")
                    for e in entries
                    if str(e.get("role", "")).lower() != "primary"
                ][:6],
            }
        )
    return sorted(digest, key=lambda c: -(c["total_volume"] or 0))


def _keyword_index(context: HeadlineContext) -> dict[str, dict]:
    return {
        keywords_service.normalize(str(row.get("keyword", ""))): row
        for row in context.clean_keywords
        if row.get("keyword")
    }


# --------------------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------------------


def _load_framework() -> str:
    """The headline framework, validated against the known-bad PDF conversion.

    Reuses `generation`'s validator rather than repeating the check: the failure it catches — a
    ToUnicode-blind extraction that turns every digit into U+00FD — would silently destroy exactly
    the per-channel character limits this service asks each candidate to respect, and a second
    copy of that rule is a second place for it to rot.
    """
    from app.services.generation import _validate_reference_doc

    text = (_PROMPTS_DIR / HEADLINE_FRAMEWORK).read_text(encoding="utf-8").strip()
    _validate_reference_doc(HEADLINE_FRAMEWORK, text)
    return text


_CANDIDATE_SCHEMA_LINES = [
    ('"id"', "string, c1..cN"),
    ('"headline"', "string — the actual headline/topic, written for publication, not a description of one"),
    ('"primary_keyword"', "string — MUST be a primary or secondary keyword from the cluster report below, copied exactly"),
    ('"source_cluster"', "string — the cluster name it came from"),
    ('"intent"', '"informational" | "commercial" | "transactional" | "navigational"'),
    ('"funnel"', '"TOFU" | "MOFU" | "BOFU"'),
    ('"traffic_temperature"', '"cold" | "warm" | "hot" — the framework Part 5 temperature this headline is written for'),
    ('"framework_formula"', "string — the Part 8 template or Part 3 formula family used, named"),
    ('"curiosity_elements"', "array of at least 2 strings, drawn from the framework's Part 2 list of eight"),
    ('"specificity"', "string — the number, timeframe or named mechanism carrying the specificity requirement"),
    ('"why_it_works"', "one sentence, concrete"),
    ('"trend_evidence"', "string or null — what a search showed about this angle's current traction, with the source named. null if you did not verify it"),
    ('"char_count"', "integer — the character length of `headline`"),
    ('"checklist_pass"', "boolean — does it pass the framework's Part 11 pre-publication checklist"),
    ('"checklist_notes"', "string — what it fails, or \"\" if it passes"),
]

# JSON type for each slot-specific extra. Everything not listed is a string.
_EXTRA_JSON_TYPES: dict[str, str] = {"consumption_time_minutes": "integer"}


def response_schema(cfg: SlotConfig) -> dict:
    """The JSON schema the API is told to enforce on the response.

    This exists because free-text JSON from a headline generator is not merely fragile, it is
    *predictably* fragile: headlines are full of the exact characters that break hand-written JSON —
    apostrophes, quotation marks around a phrase, colons, em dashes — and a batch is lost whole when
    any one of ten candidates escapes a quote wrongly. Observed on the first live run: a single
    malformed string 7KB into the response threw away all ten candidates and the call that produced
    them.

    `output_config.format` moves that from "usually parses" to "cannot not parse". The prompt still
    describes each field, because the schema constrains shape and the prompt supplies meaning — a
    schema cannot say that `specificity` must be a real number or timeframe.
    """
    candidate_props: dict[str, Any] = {
        "id": {"type": "string"},
        "headline": {"type": "string"},
        # Nullable rather than omitted: a run with no keyword report has no keyword to name, and a
        # model forced to supply one would invent it — which is the failure this whole module is
        # built to prevent.
        "primary_keyword": {"type": ["string", "null"]},
        "source_cluster": {"type": ["string", "null"]},
        # Left as free nullable strings rather than enums. The API rejects `enum` alongside a
        # nullable type union, and these three have to stay nullable — a candidate with no cluster
        # behind it has no honest intent to report. The allowed values are stated per field in
        # `_CANDIDATE_SCHEMA_LINES`, and `_coerce_candidate` normalises the case on the way in, so
        # the enum was buying nothing the prompt and the parser were not already covering.
        "intent": {"type": ["string", "null"]},
        "funnel": {"type": ["string", "null"]},
        "traffic_temperature": {"type": ["string", "null"]},
        "framework_formula": {"type": ["string", "null"]},
        "curiosity_elements": {"type": "array", "items": {"type": "string"}},
        "specificity": {"type": ["string", "null"]},
        "why_it_works": {"type": ["string", "null"]},
        # Null is the honest answer when nothing was verified, and the prompt says so explicitly.
        "trend_evidence": {"type": ["string", "null"]},
        "char_count": {"type": "integer"},
        "checklist_pass": {"type": "boolean"},
        "checklist_notes": {"type": "string"},
    }
    for extra_name, _desc in cfg.extras:
        candidate_props[extra_name] = {"type": [_EXTRA_JSON_TYPES.get(extra_name, "string"), "null"]}

    return {
        "type": "object",
        "properties": {
            "service_anchor": {"type": "string"},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": candidate_props,
                    # Strict schemas require every property to be listed as required; optionality is
                    # expressed by the null unions above, not by omission.
                    "required": list(candidate_props),
                    "additionalProperties": False,
                },
            },
        },
        "required": ["service_anchor", "candidates"],
        "additionalProperties": False,
    }


def build_headline_prompt(cfg: SlotConfig, count: int, context: HeadlineContext) -> str:
    """The user message. The framework is the system prompt (see `suggest_headlines`)."""
    anchor = context.service_anchor.strip()
    scope_word = "sub-service" if context.phase == "phase2" else "service"

    schema_fields = "\n".join(f"    {name}: {desc}" for name, desc in _CANDIDATE_SCHEMA_LINES)
    for extra_name, extra_desc in cfg.extras:
        schema_fields += f'\n    "{extra_name}": {extra_desc}'

    clusters = _cluster_digest(context)
    if clusters:
        demand_block = (
            "SEARCH DEMAND (this run's own keyword cluster report — real volumes from the keyword "
            "provider, already cleaned and validated):\n"
            f"{json.dumps(clusters, indent=2)}"
        )
        keyword_rule = (
            "Every candidate's `primary_keyword` MUST be copied exactly from the cluster report "
            "above — a primary or a secondary keyword of one of those clusters. Do not invent a "
            "keyword, do not reword one, and do not estimate a volume for one. A candidate whose "
            "keyword is not in that report will be discarded before the operator sees it."
        )
    else:
        demand_block = (
            "SEARCH DEMAND: unavailable for this run — no keyword report was built. Work from the "
            "ICP and the service anchor alone."
        )
        keyword_rule = (
            "No keyword report is available, so set `primary_keyword` to the phrase you would "
            "target and set `source_cluster` to null. Do not fabricate search volumes."
        )

    parts = [
        f"Produce {count} candidate {cfg.subject} headlines for the operator to choose from.",
        "",
        "— THE ANCHOR (the hardest constraint here) —",
        f"This run is for one {scope_word}: **{anchor}**.",
        f"Every single candidate must be unmistakably about {anchor}. A headline that would make "
        f"equal sense for a different {scope_word} has failed, however good it reads. If you cannot "
        f"reach {count} candidates that are genuinely about {anchor}, return fewer — a short honest "
        f"list is useful and a padded one is not.",
    ]
    if context.phase == "phase2":
        parts.append(
            f"{anchor} is a sub-service sitting under a broader parent service. Write about "
            f"{anchor} specifically — its own tactics, platforms, metrics and buyer questions — "
            "not about the parent category it belongs to."
        )

    parts += [
        "",
        "— CONTEXT —",
        f"Client: {context.business_name or '(not specified)'}",
        f"Market / region: {context.region or '(not specified)'}",
        "",
        demand_block,
    ]

    if context.icp_document:
        parts += [
            "",
            "IDEAL CUSTOMER PROFILE (approved earlier in this run — write to this person, in their "
            "vocabulary and at their awareness level):",
            "--- begin ICP ---",
            _truncate(context.icp_document),
            "--- end ICP ---",
        ]

    if context.competitor_document:
        parts += [
            "",
            "COMPETITOR LANDSCAPE (approved earlier in this run — use it to avoid what is "
            "saturated and to name what is missing):",
            "--- begin competitor analysis ---",
            _truncate(context.competitor_document),
            "--- end competitor analysis ---",
        ]

    parts += [
        "",
        "— HOW TO BUILD EACH CANDIDATE —",
        "The framework in your system prompt is not background reading. Apply it per candidate:",
        "  1. Interest = Curiosity + a BIG PROMISE. A headline that opens a curiosity gap without "
        "promising a benefit fails, and so does one that promises a benefit without opening a gap.",
        "  2. Use at least two of its eight curiosity elements, and name which two you used.",
        "  3. Specificity is not optional. Replace every vague benefit with a number, a timeframe, "
        "or a named mechanism, and put that in the `specificity` field.",
        "  4. Match the traffic temperature to where this asset actually sits, and pull the formula "
        "family the framework lists for that temperature.",
        f"  5. Respect the channel limit for where this will be published — {cfg.channel}: "
        f"{cfg.char_budget}. Report the real length in `char_count`.",
        "  6. Run each finished candidate through the Part 11 pre-publication checklist and report "
        "the result honestly. Do not mark a candidate as passing to make the list look clean.",
        "",
        keyword_rule,
        "",
        "Invent nothing verifiable. No statistics, client names, case-study results, awards or "
        "testimonials that are not in the documents above. A headline that needs a proof point you "
        "do not have is a headline you cannot ship.",
        "",
        "Make the set genuinely different from itself. Vary the angle, the formula family and the "
        "funnel stage across candidates — ten rewrites of one idea gives the operator nothing to "
        "choose between, which defeats the point of showing them a list.",
    ]

    if cfg.guidance:
        parts += ["", f"FOR THIS SLOT SPECIFICALLY: {cfg.guidance}"]

    if context.exclude:
        # The *most recent* sixty, not the first sixty. The list only grows — every re-roll
        # appends the batch it just showed — and truncating from the front is what would let
        # round one back in on round seven, which is precisely the repeat the operator is
        # clicking away from.
        rejected = "\n".join(f"  - {line}" for line in context.exclude[-60:])
        parts += [
            "",
            "ALREADY REJECTED — the operator has seen these and did not want them. Do not repeat "
            "them and do not paraphrase them; change the angle, not the wording:",
            rejected,
        ]

    if context.operator_note:
        parts += ["", f"OPERATOR'S STEER (follow it): {context.operator_note.strip()}"]

    parts += [
        "",
        "— OUTPUT —",
        "Return ONE JSON object and nothing else. No prose, no markdown fence.",
        "",
        "{",
        f'  "service_anchor": "{anchor}",',
        '  "candidates": [',
        "    {",
        schema_fields,
        "    }",
        "  ]",
        "}",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------------------

OnUsage = Callable[[CallUsage], Awaitable[None]]


def web_search_enabled(cfg: SlotConfig) -> bool:
    """Whether this slot's call gets the web-search tool.

    Opt-in via `HEADLINES_WEB_SEARCH`, having previously been opt-out. The switch flipped because
    the measurements at `_WEB_SEARCH_TOOL` showed it completing zero searches while adding between
    25 and 155 seconds per gate — a cost with no matching benefit, which made "on by default" the
    wrong default regardless of how useful the feature is in principle.

    The slot's own flag still applies on top, for the places trend data would be actively
    misleading: an offer name is positioned against the competitor set the Offers stage already
    gathered and against the client's own price norms, and a funnel or sequence theme is internal
    structure nobody searches for.
    """
    import os

    setting = (os.environ.get("HEADLINES_WEB_SEARCH") or "").strip().lower()
    if setting in {"0", "false", "off", "no"}:
        return False
    if setting in {"1", "true", "on", "yes"}:
        return cfg.web_search
    return _WEB_SEARCH_DEFAULT_ON and cfg.web_search


def extract_json(text: str) -> dict:
    """Pull the JSON object out of the response, fenced or not."""
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise HeadlineParseError("no JSON object found in the model response")
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise HeadlineParseError(f"model response was not valid JSON: {exc}") from exc


def _extract_text(content_blocks: Iterable[Any]) -> str:
    return "".join(block.text for block in content_blocks if getattr(block, "type", None) == "text")


# --------------------------------------------------------------------------------------
# Grounding
#
# The prompt asks for anchored, keyword-grounded candidates. This is where that stops being a
# request. `keywords.validate_clusters` does the same job for the clustering call and for the same
# reason: a plausible-sounding but off-topic suggestion that reaches the operator's card is
# indistinguishable from a good one until they have built an asset on it.
# --------------------------------------------------------------------------------------


@dataclass
class Candidate:
    id: str
    headline: str
    primary_keyword: str | None = None
    source_cluster: str | None = None
    intent: str | None = None
    funnel: str | None = None
    traffic_temperature: str | None = None
    framework_formula: str | None = None
    curiosity_elements: list[str] = field(default_factory=list)
    specificity: str | None = None
    why_it_works: str | None = None
    trend_evidence: str | None = None
    char_count: int = 0
    channel_limit_ok: bool = True
    checklist_pass: bool = False
    checklist_notes: str = ""
    # Real search volume for `primary_keyword`, read from the run's own cleaned keyword set — never
    # from the model, which is not allowed to estimate one.
    search_volume: int | None = None
    difficulty: int | None = None
    # False when the keyword is not in the cleaned set. Kept and labelled rather than silently
    # dropped: a relevant-but-ungrounded idea can still be the right pick, but the operator is
    # entitled to know it carries no demand evidence.
    grounded: bool = True
    # Slot-specific fields (`format`, `mechanic`, …), kept as-is.
    extras: dict[str, Any] = field(default_factory=dict)


_CHAR_BUDGET = re.compile(r"(\d+)\s*-\s*(\d+)\s*characters")


def _char_limit(cfg: SlotConfig) -> int | None:
    """The upper bound of the slot's stated character budget, for the `channel_limit_ok` flag.

    Advisory, not a filter. A candidate eight characters over budget is a trim, not a rejection,
    and dropping it would hide a good idea over something the operator can fix in the field.
    """
    match = _CHAR_BUDGET.search(cfg.char_budget)
    return int(match.group(2)) if match else None


def _anchor_tokens(anchor: str) -> set[str]:
    """The content tokens a candidate has to touch to count as on-anchor.

    Singularised through the keyword pipeline's own stemmer so "ad" matches "ads" — the same
    normalisation the relevance vocabulary was built with, which is the point: this check and
    `keywords.is_relevant` must agree about what a word is.
    """
    return {
        keywords_service.singular(t)
        for t in keywords_service.tokens(keywords_service.normalize(anchor))
    }


def _on_anchor(
    candidate: Candidate,
    anchor_tokens: set[str],
    vocabulary: set[str],
    vocabulary_matches_anchor: bool,
) -> bool:
    """Whether this candidate is about the service this stage is for.

    Deliberately generous about *where* the evidence sits — the headline, the keyword, or the
    cluster it came from — because a good headline often carries the subject implicitly ("The
    12-Minute Audit That Finds Where Your Reach Died" is a social media marketing lead magnet) and
    a check that demanded the service name verbatim would reject exactly the candidates worth
    having.

    `vocabulary_matches_anchor` is the fix for the other half of the SEO-topics bug. The relevance
    vocabulary comes from the run's *keyword report*, which is built once per run for the run's
    headline service. When the stage's own target service is something narrower or different, that
    vocabulary describes a wider business — so accepting any candidate that merely overlaps it let
    exactly the off-service topics through that the anchor was supposed to exclude. The vocabulary
    is now only a valid second opinion when it was built for this same anchor; otherwise the anchor's
    own words are the whole test.
    """
    haystack = " ".join(
        part
        for part in (candidate.headline, candidate.primary_keyword or "", candidate.source_cluster or "")
        if part
    )
    candidate_tokens = {
        keywords_service.singular(t)
        for t in keywords_service.tokens(keywords_service.normalize(haystack))
    }
    if anchor_tokens and candidate_tokens & anchor_tokens:
        return True
    if vocabulary_matches_anchor and vocabulary and candidate_tokens & vocabulary:
        return True
    # With no anchor at all there is nothing to be off-topic against, so the vocabulary is the only
    # test available rather than a supplementary one.
    if not anchor_tokens:
        return not vocabulary or bool(candidate_tokens & vocabulary)
    return False


def _coerce_candidate(raw: dict, index: int, cfg: SlotConfig) -> Candidate | None:
    headline = str(raw.get("headline") or "").strip()
    if not headline:
        return None

    known = {
        "id", "headline", "primary_keyword", "source_cluster", "intent", "funnel",
        "traffic_temperature", "framework_formula", "curiosity_elements", "specificity",
        "why_it_works", "trend_evidence", "char_count", "checklist_pass", "checklist_notes",
    }
    elements = raw.get("curiosity_elements")
    if isinstance(elements, str):
        elements = [elements]

    return Candidate(
        id=str(raw.get("id") or f"c{index}"),
        headline=headline,
        primary_keyword=(str(raw["primary_keyword"]).strip() or None) if raw.get("primary_keyword") else None,
        source_cluster=(str(raw["source_cluster"]).strip() or None) if raw.get("source_cluster") else None,
        intent=(str(raw["intent"]).strip().lower() or None) if raw.get("intent") else None,
        funnel=(str(raw["funnel"]).strip().upper() or None) if raw.get("funnel") else None,
        traffic_temperature=(str(raw["traffic_temperature"]).strip().lower() or None)
        if raw.get("traffic_temperature")
        else None,
        framework_formula=(str(raw["framework_formula"]).strip() or None) if raw.get("framework_formula") else None,
        curiosity_elements=[str(e).strip() for e in (elements or []) if str(e).strip()],
        specificity=(str(raw["specificity"]).strip() or None) if raw.get("specificity") else None,
        why_it_works=(str(raw["why_it_works"]).strip() or None) if raw.get("why_it_works") else None,
        trend_evidence=(str(raw["trend_evidence"]).strip() or None) if raw.get("trend_evidence") else None,
        # Recomputed rather than trusted: the model reports its own character count and gets it
        # wrong often enough that a `channel_limit_ok` built on it would be decorative.
        char_count=len(headline),
        checklist_pass=bool(raw.get("checklist_pass")),
        checklist_notes=str(raw.get("checklist_notes") or "").strip(),
        extras={k: v for k, v in raw.items() if k not in known and k in dict(cfg.extras)},
    )


def _dedupe_key(headline: str) -> str:
    """What counts as "the same headline already offered".

    Not a bare `.lower()`. A re-roll's job is to produce topics the operator has not seen, and a
    model handed "do not repeat these" will happily return round one with the capitalisation
    changed, a colon swapped for a dash, or a question mark added. Those are the same topic. So the
    key drops everything but letters, digits and single spaces before comparing, which makes
    "How to Price Social Media Marketing?" and "How to price social media marketing" one entry
    rather than two.
    """
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


def ground_candidates(
    raw_candidates: list[dict],
    cfg: SlotConfig,
    context: HeadlineContext,
    count: int,
) -> tuple[list[Candidate], list[dict]]:
    """Filter, enrich and rank. Returns (kept, rejected-with-reasons).

    Rejections are returned rather than logged away because they are the evidence that the anchor
    is holding — if a slot starts rejecting half its batch, the prompt for it is wrong, and that is
    only visible if the count survives.
    """
    anchor_tokens = _anchor_tokens(context.service_anchor)
    vocabulary = set(context.vocabulary)
    # The keyword report is built once per run for the run's headline service. It is only a
    # valid second opinion on "is this on-service?" when it was built for the same service
    # this stage is for — see `_on_anchor`.
    vocabulary_matches_anchor = context.keyword_service_matches_anchor()
    index = _keyword_index(context)
    limit = _char_limit(cfg)
    already = {_dedupe_key(h) for h in context.exclude if _dedupe_key(h)}

    kept: list[Candidate] = []
    rejected: list[dict] = []
    seen: set[str] = set()

    for position, raw in enumerate(raw_candidates, start=1):
        if not isinstance(raw, dict):
            continue
        candidate = _coerce_candidate(raw, position, cfg)
        if candidate is None:
            continue

        normalized = _dedupe_key(candidate.headline)
        if normalized in seen or normalized in already:
            rejected.append({"headline": candidate.headline, "reason": "duplicate of one already offered"})
            continue
        seen.add(normalized)

        if not _on_anchor(candidate, anchor_tokens, vocabulary, vocabulary_matches_anchor):
            rejected.append(
                {
                    "headline": candidate.headline,
                    "reason": f"not about {context.service_anchor}",
                }
            )
            continue

        # Metrics come from the run's own cleaned set, never from the model.
        keyword = keywords_service.normalize(candidate.primary_keyword or "")
        row = index.get(keyword)
        if row is not None:
            candidate.primary_keyword = row["keyword"]
            candidate.search_volume = row.get("volume")
            candidate.difficulty = row.get("difficulty")
            candidate.intent = candidate.intent or row.get("intent")
            candidate.grounded = True
        elif keyword:
            # Same rule `validate_clusters` applies to an invented cluster term: an unknown keyword
            # has to pass the relevance gate on its own, and it never gets invented metrics.
            #
            # Gated on the report being for *this* service. When it is not, the vocabulary describes
            # a different business, and applying it rejects the candidates that are actually right:
            # a lead magnet for "Social media marketing" whose keyword is "social media marketing
            # audit" fails relevance against an SEO keyword set, which is a verdict about the wrong
            # question. Where the two disagree, this gate goes quiet and the anchor does the work.
            if vocabulary_matches_anchor and vocabulary and not keywords_service.is_relevant(keyword, vocabulary):
                rejected.append(
                    {
                        "headline": candidate.headline,
                        "reason": f"keyword {candidate.primary_keyword!r} is not in this run's keyword set and failed relevance",
                    }
                )
                continue
            candidate.grounded = False
            candidate.search_volume = None
            candidate.difficulty = None
        else:
            candidate.grounded = False

        if limit is not None:
            candidate.channel_limit_ok = candidate.char_count <= limit

        kept.append(candidate)

    # Grounded first, then by real demand, then by whether it passes the framework's own checklist.
    # Volume leads because it is the only ordering here that is measured rather than asserted.
    kept.sort(
        key=lambda c: (
            not c.grounded,
            -(c.search_volume or 0),
            not c.checklist_pass,
            not c.channel_limit_ok,
        )
    )
    for position, candidate in enumerate(kept, start=1):
        candidate.id = f"c{position}"
    return kept[:count], rejected


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


@dataclass
class HeadlineSuggestions:
    slot: str
    asset_id: str
    service_anchor: str
    phase: str
    candidates: list[Candidate]
    rejected: list[dict]
    multi: bool
    suggested_selection: int
    channel: str
    char_budget: str
    grounded_in_keywords: bool
    web_search_used: bool


async def suggest_headlines(
    slot: str,
    context: HeadlineContext,
    count: int = DEFAULT_COUNT,
    on_usage: OnUsage | None = None,
) -> HeadlineSuggestions:
    """Produce at least `count` candidate headlines for one slot, or as many as survive grounding."""
    cfg = slot_config(slot)
    count = max(1, min(int(count or DEFAULT_COUNT), MAX_COUNT))
    if not context.service_anchor.strip():
        raise ValueError("a service anchor is required — suggestions must be about something")

    # Over-ask so the anchor filter has room to reject without dropping the card below `count`.
    asked_for = min(count + _OVERSHOOT, MAX_COUNT + _OVERSHOOT)
    prompt = build_headline_prompt(cfg, asked_for, context)
    use_search = web_search_enabled(cfg)

    system_prompt = (
        "You are a direct-response headline strategist. The document below is the canonical "
        "headline framework for this system: every candidate you produce must be built from it, "
        "and you must be able to name which of its formulas and curiosity elements you used.\n\n"
        f"===== BEGIN assets/Prompts/{HEADLINE_FRAMEWORK} =====\n"
        f"{_load_framework()}\n"
        f"===== END assets/Prompts/{HEADLINE_FRAMEWORK} ====="
    )
    if use_search:
        system_prompt += (
            "\n\nYou have web search. Use it to check what is actually getting traction right now "
            "for this service — the angles, formats and framings currently being published and "
            "shared — and let that inform the candidates. Record what you found in each "
            "candidate's `trend_evidence`, naming the source. If you did not verify a candidate's "
            "angle, set `trend_evidence` to null. Never present an unverified guess as a trend: a "
            "fabricated trend claim is worse than an honest null, because the operator will pick "
            "on the strength of it."
        )

    client = get_client()
    request: dict[str, Any] = {
        "model": SONNET,
        "max_tokens": _MAX_TOKENS,
        # A list with a cache breakpoint, not a bare string. The framework is ~18k tokens and is
        # byte-identical on every headline call in the process, so without this each gate pays full
        # price to re-read the same document. Measured: the second call reported
        # `cache_read_input_tokens=18406` against `input_tokens=1560` — the whole framework served
        # from cache for roughly a tenth of its cost.
        #
        # It sits first and alone in the block for the same reason: caching is a prefix match, so
        # anything volatile placed before it would invalidate the framework on every call. The
        # per-run material (anchor, clusters, ICP) is all in the user message, after the breakpoint.
        #
        # `ttl: 1h`, not the 5-minute default, and the default was measurably wrong here. Every
        # suggestion gate waits on the operator choosing from the previous card, and `api_usage`
        # recorded the consequence on three consecutive calls: `cache_creation_input_tokens=18546,
        # 18385, 18385` against `cache_read_input_tokens=0, 0, 0`, at start-to-start gaps of 12 and
        # 102 minutes. The entry was written and never once read, so the breakpoint was costing the
        # 1.25x write surcharge and returning nothing. An hour costs 2x on the write and pays that
        # back on the first gate that lands inside it.
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral", "ttl": _CACHE_TTL},
            }
        ],
        "messages": [{"role": "user", "content": prompt}],
        # The API enforces the shape, so a stray apostrophe inside a headline cannot cost the whole
        # batch. See `response_schema` for why that is the expected failure and not a rare one.
        #
        # `effort: low` is the single biggest latency win here, and it costs nothing that matters.
        # Thinking is on by default on Sonnet 5 and counts against the output budget, so at the
        # default effort this call spent minutes reasoning about headline theory and returned 14,727
        # output tokens. At low effort the same prompt returned the same 14 candidates in 4,464
        # tokens and 39 seconds, down from 123. Naming ten headlines from a supplied framework and a
        # supplied keyword list is not a task that repays deep deliberation — and the parts that must
        # be right (the anchor, the grounding, the character limits) are enforced by
        # `ground_candidates` afterwards, not by how long the model thinks.
        "output_config": {
            "format": {"type": "json_schema", "schema": response_schema(cfg)},
            "effort": "low",
        },
    }
    if use_search:
        request["tools"] = [_WEB_SEARCH_TOOL]

    logger.info(
        "Suggesting %d headlines slot=%s phase=%s anchor=%r search=%s clusters=%d",
        asked_for,
        slot,
        context.phase,
        context.service_anchor,
        use_search,
        len(context.keyword_report.get("clusters") or []),
    )
    started = time.monotonic()
    message = await client.messages.create(**request)

    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(
                message,
                requested_model=SONNET,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )

    payload = extract_json(_extract_text(message.content))
    kept, rejected = ground_candidates(payload.get("candidates") or [], cfg, context, count)

    if rejected:
        logger.info("Headline grounding dropped %d/%d for slot=%s", len(rejected), len(payload.get("candidates") or []), slot)
    if len(kept) < count:
        # Not an error. Returning eight honestly-anchored candidates beats padding to ten with two
        # that are about something else — the operator can ask for more.
        logger.warning("slot=%s returned %d candidates, %d were asked for", slot, len(kept), count)

    return HeadlineSuggestions(
        slot=slot,
        asset_id=cfg.asset_id,
        service_anchor=context.service_anchor,
        phase=context.phase,
        candidates=kept,
        rejected=rejected,
        multi=cfg.multi,
        suggested_selection=cfg.suggested_selection,
        channel=cfg.channel,
        char_budget=cfg.char_budget,
        grounded_in_keywords=bool(context.keyword_report.get("clusters")),
        web_search_used=use_search,
    )


# --------------------------------------------------------------------------------------
# Turning a selection into a prompt input
# --------------------------------------------------------------------------------------


def render_selection(cfg: SlotConfig, selected: list[dict]) -> str:
    """The chosen headline(s) as the string that goes into the stage's INPUTS block.

    Single-select slots render as the bare headline, because that is what the existing prompt
    fields expect — `webinar_topic_working_title` has always been a line of text and the master
    prompt reads it as one. Multi-select slots render as a numbered list carrying the slot's
    extras, since a Lead Magnet stage handed ten concepts needs each one's format and mechanic to
    build it, and a Blog stage handed five topics needs each one's keyword and intent to write
    five posts that do not compete with each other.
    """
    if not selected:
        return ""

    if not cfg.multi and len(selected) == 1:
        return str(selected[0].get("headline") or "").strip()

    lines: list[str] = []
    for position, item in enumerate(selected, start=1):
        headline = str(item.get("headline") or "").strip()
        if not headline:
            continue
        lines.append(f"{position}. {headline}")
        detail: list[str] = []
        for name, _desc in cfg.extras:
            value = item.get(name) or (item.get("extras") or {}).get(name)
            if value not in (None, ""):
                detail.append(f"{name.replace('_', ' ').title()}: {value}")
        if item.get("primary_keyword"):
            detail.append(f"Primary keyword: {item['primary_keyword']}")
        # The two facts that keep a *set* coherent rather than describing one item: intent is what
        # stops two picks being written as the same article, and volume is what lets a stage order
        # its output by demand instead of by the order the model happened to emit them in.
        if item.get("intent"):
            detail.append(f"Search intent: {item['intent']}")
        if item.get("search_volume") not in (None, ""):
            detail.append(f"Search volume: {item['search_volume']}/mo")
        if item.get("funnel"):
            detail.append(f"Funnel stage: {item['funnel']}")
        for line in detail:
            lines.append(f"   - {line}")
    return "\n".join(lines)
