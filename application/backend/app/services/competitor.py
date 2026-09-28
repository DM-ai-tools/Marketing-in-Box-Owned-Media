"""Competitor-analysis prepass stages — the 10 `competitor_analysis_*` assets.

These are the auxiliary stages that must run *before* their paired main asset, so the main
asset's prompt receives a real competitor benchmark instead of a placeholder. Per
`schemas/drafts/DAG_SOURCE_MAP.md` and `scripts/seed_asset_definitions.py`, 10 of the 15 main
Phase-1 assets declare a `competitor_analysis_*` stage in their `depends_on` edges; the other
5 (icp, funnel, funnel_hub_media, sms_sequence, plan_of_action) have none. `pillar_page` is one of
the 10 since the former `seo_pillar_page` variant stage was merged into it.

Two things make these stages structurally different from the 15 main ones in
`app/services/generation.py`, which is why they live in their own module rather than as more
rows in `STAGE_CONFIGS`:

1. **Prompt shape.** Main prompts carry a "— INPUTS (fill in before submitting) —" block
   followed by a `# — MASTER PROMPT (do not edit below this line) —` marker, so
   `generation.build_prompt` rebuilds the INPUTS block and splices on everything after the
   marker. The competitor files have no marker and no INPUTS block: the whole file *is* the
   prompt, with `{TARGET_URL}` / `{NICHE}` / `{LOCATION}` / `{SERVICE}` placeholders
   substituted in place (verified across all 10 files; `01_CRO.md` and
   `07_Social_Content_Strategy_and_Posts.md` hardcode their service and so have no
   `{SERVICE}`).

2. **Tool use.** Each file explicitly requires verifying candidates by fetching the actual page
   ("do not rely on titles or search snippets alone"). Without a real search tool Claude can
   only produce plausible-looking domains from training memory, which would then silently
   corrupt every downstream asset that reads the competitor context key. These calls therefore
   run with Anthropic's server-side `web_search` tool by default — see `_web_search_enabled`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.claude_client import get_client
from app.services.generation import EFFORT_CAPABLE_MODELS
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # .../application/backend
_COMPETITOR_PROMPTS_DIR = _BACKEND_ROOT / "assets" / "Prompts" / "Competitor Analysis"
_SCHEMAS_DIR = _BACKEND_ROOT / "schemas" / "drafts"

SONNET = "claude-sonnet-5"

# Per `seed_asset_definitions.py`, every competitor stage is model_tier=sonnet: this work needs
# real verification reasoning (confirming a competitor is a genuine service provider, not a
# directory or aggregator) rather than cheap pattern-matching.
#
# `COMPETITOR_MODEL` overrides it, for measuring that claim rather than restating it — the argument
# for Sonnet here is a judgement about a task, and a judgement about a task is testable.
#
# It is also, on this stage specifically, the biggest cost lever there is. Priced from the 22
# measured prepasses recorded at `_DEFAULT_SEARCH_BUDGET` below (276,465 input, 8,141 output, 10.5
# searches, $0.739/call — which reproduces exactly at Sonnet 5's $2/$10):
#
#   Sonnet 5     input $0.553 (75%)   output $0.081 (11%)   search $0.105 (14%)   = $0.739
#   Haiku 4.5    input $0.276 (65%)   output $0.041 (10%)   search $0.105 (25%)   = $0.422
#
# A 43% cut, because this call is *input*-dominated — the server-side tool loop re-reads the prompt
# and every result gathered so far on each iteration — and Haiku 4.5 halves the input rate. That is
# a much larger effect than `effort` can reach here (see `_effort`), which is the reverse of the
# ordering that holds on the headline gate, where output dominates. Cost profile, not model tier,
# is what decides which lever pays.
#
# So why is Sonnet still the default? Because the risk is not symmetrical either. This stage decides
# whether a domain is a genuine service provider or a directory dressed as one, and a wrong verdict
# does not raise — it writes a plausible-looking competitor into the context key that ten
# downstream assets read. $0.32 a call is worth paying until an A/B on real runs says it is not,
# which is what this switch exists to make possible.
#
# Whatever it is set to is what gets *billed*, so it is also what gets recorded: the usage row is
# stamped with the resolved model, not with `SONNET`.
_MODEL_ENV = "COMPETITOR_MODEL"

# Sonnet 5's ceiling is 128k output tokens; 16k is generous headroom for a 10-row JSON array plus
# the notes section each prompt asks for, without risking the mid-array truncation that would make
# the output unparseable.
_MAX_TOKENS = 16000

# `output_config.effort`, which this call previously did not send at all — so it ran at the API
# default of `high`, the most expensive setting, by omission rather than by decision. Worth fixing,
# but worth being honest about the size of: on the measured split above, output is 11% of this
# call, so even the 70% output cut `headlines.py` measured at `low` (4,464 tokens against 14,727 at
# the default) is about 8% of the bill here. Effort is a real saving on a small share; the input
# side is where this stage's money is, and `_DEFAULT_SEARCH_BUDGET` owns that.
#
# There is a second, unmeasured path by which it may reach the big number: lower effort means fewer
# and more consolidated tool calls, and iterations are exactly what drive the input total. If that
# holds here it would matter more than the output saving — which is a reason to compare `medium`
# against `none` on `api_usage` rather than to assume either way.
#
# `medium` rather than `low`, deliberately. The stage's job is discrimination and its failures are
# silent, so this is not a place to buy the last 3% by thinking less. `low` is for stages whose
# deliverable is short and templated. `COMPETITOR_EFFORT` moves it; `none` sends no `output_config`
# at all, which is the setting to measure against.
_EFFORT_ENV = "COMPETITOR_EFFORT"
_DEFAULT_EFFORT = "medium"
_EFFORT_OFF = frozenset({"", "none", "off", "default"})
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

# Deliberately the basic `web_search_20250305`, not the newer `web_search_20260209` that Sonnet 5
# also supports. `_20260209` adds dynamic filtering (it runs code execution internally to filter
# results before they reach the context window), which is a real quality/token win in general — but
# it is markedly slower, and this prepass BLOCKS the stage the operator is waiting on. Measured on
# this workload: `_20250305` completes a competitor stage in ~60-80s; `_20260209` did not finish a
# comparable prompt inside 7 minutes. The basic tool already returns verified, real competitors
# here, so the latency is not worth trading. Revisit if the prepass ever moves off the critical
# path (e.g. runs ahead of time as its own queued job).
#
# `max_uses` is the search budget for one stage, and it is the single most expensive number in this
# repo. 8 was tuned when every competitor stage was an invisible prepass the operator was blocked
# on; it was raised to 12 because finding *ten* competitors that each maintain a genuine pillar page
# on one topic — and opening each candidate to confirm it is a cornerstone page rather than a thin
# service page — is several searches per keeper.
#
# What that raise actually cost, measured from `api_usage` over 22 real prepasses at `max_uses: 12`:
#
#   avg input 276,465 tokens/call   avg output 8,141   avg 10.5 searches   avg $0.739/call
#
# 276k input tokens against a ~1,500-token prompt file is not a bug — it is how a server-side tool
# loop bills. Each of the ~11 internal iterations re-reads the prompt plus every result gathered so
# far, so input grows with the square of the budget while the deliverable (a 10-row array) does not
# grow at all. Those 22 calls were $16.27 of a $45.47 bill: 36% of total spend, on the stage with
# the smallest output in the pipeline.
#
# Back to 8, which is a value this pipeline already ran in production rather than a guess, and
# env-tunable so the tradeoff can be measured instead of argued about. Note what does *not* work
# here: a `cache_control` breakpoint. The iterations happen inside one `messages.create`, so there
# is no second request whose prefix could hit a cache — the only lever on this cost is how many
# times the loop goes round.
_DEFAULT_SEARCH_BUDGET = 8


def resolve_model() -> str:
    """The model one competitor prepass runs on. `COMPETITOR_MODEL` overrides `SONNET`.

    Read at call time rather than at import, the same late binding `_web_search_enabled` and
    `_search_budget` use: a deploy or a test can set it without re-importing the module, and an
    A/B that needed a restart to switch arms is an A/B nobody runs.
    """
    return (os.environ.get(_MODEL_ENV) or "").strip() or SONNET


def _effort(model: str) -> str | None:
    """`output_config.effort` for one prepass, or `None` to send no `output_config` at all.

    Two ways to get `None`, and they are different things. `COMPETITOR_EFFORT=none` is the operator
    asking for the API default, which is the control arm when measuring what effort is worth. A
    model outside `EFFORT_CAPABLE_MODELS` is not a preference — Haiku 4.5 *rejects* the parameter
    with a 400 rather than ignoring it, so sending it would convert a `COMPETITOR_MODEL` experiment
    into a stage that fails outright, and the experiment would read as "Haiku cannot do this".

    An unrecognised level falls back to the default and says so, rather than passing a typo to the
    API: `COMPETITOR_EFFORT=meduim` should cost someone a log line, not a 400 on ten stages.
    """
    raw = os.environ.get(_EFFORT_ENV)
    level = (_DEFAULT_EFFORT if raw is None else raw).strip().lower()

    if level not in _EFFORT_OFF and level not in _EFFORT_LEVELS:
        logger.warning(
            "%s=%r is not one of %s — falling back to %r",
            _EFFORT_ENV,
            raw,
            sorted(_EFFORT_LEVELS),
            _DEFAULT_EFFORT,
        )
        level = _DEFAULT_EFFORT

    if level in _EFFORT_OFF:
        return None
    if model not in EFFORT_CAPABLE_MODELS:
        logger.info("Effort %r not sent: %s does not accept output_config.effort", level, model)
        return None
    return level


def _search_budget() -> int:
    """`max_uses` for one competitor prepass. `COMPETITOR_SEARCH_BUDGET` overrides the default.

    Clamped to 1-12: zero would declare a tool the model cannot use, and past 12 the input growth
    described above outruns any improvement in the listing.
    """
    raw = os.environ.get("COMPETITOR_SEARCH_BUDGET", "").strip()
    if not raw.isdigit():
        return _DEFAULT_SEARCH_BUDGET
    return max(1, min(int(raw), 12))


def _web_search_tool() -> dict[str, object]:
    """Built per call rather than held as a module constant, so `_search_budget`'s env read has the
    same late-binding behaviour as `_web_search_enabled`'s — a test or a deploy can set either
    without re-importing the module."""
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": _search_budget()}


@dataclass(frozen=True)
class CompetitorConfig:
    """One competitor-analysis stage: its prompt file, its schema, and how its placeholders are
    filled from the *paired main asset's* intake answers.

    `source_fields` maps a competitor placeholder field (`target_url`, `niche`, `location`) to
    the field_ids on the main asset's own schema that can supply it, in priority order. This is
    what makes the prepass invisible to the operator: it never asks its own questions, it reads
    what the main stage already collected.
    """

    asset_id: str
    paired_main_asset_id: str
    # Relative to `assets/Prompts/Competitor Analysis/`. Phase 2's files sit in a subdirectory of
    # it, so this carries the separator rather than the loader guessing a directory per phase.
    prompt_file: str
    schema_file: str
    # field_id on the MAIN asset's schema that receives this stage's output
    target_field_id: str
    source_fields: dict[str, tuple[str, ...]]


def _sources(url: tuple[str, ...], niche: tuple[str, ...], location: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {"target_url": url, "niche": niche, "location": location}


# Field-name drift across the 16 main schemas is real and intentional (each was transcribed from
# its own source prompt's wording): the same concept is `sub_vertical_niche` on cro, `industry` on
# offers/lead_magnet, and `industry_niche` on content_marketing/social/book. The tuples below are
# tried in order, so this table absorbs that drift in one place instead of at every call site.
COMPETITOR_CONFIGS: dict[str, CompetitorConfig] = {
    "competitor_analysis_cro": CompetitorConfig(
        "competitor_analysis_cro",
        "cro",
        "01_CRO.md",
        "competitor_analysis_cro.json",
        "competitor_analysis",
        _sources(("client_website_url", "existing_page_url"), ("sub_vertical_niche", "client_industry"), ("region_location",)),
    ),
    "competitor_analysis_offers": CompetitorConfig(
        "competitor_analysis_offers",
        "offers",
        "02_Offers.md",
        "competitor_analysis_offers.json",
        "competitor_analysis",
        _sources(("client_website_url",), ("industry",), ("region_location",)),
    ),
    "competitor_analysis_lead_magnet": CompetitorConfig(
        "competitor_analysis_lead_magnet",
        "lead_magnet",
        "03_Lead_Magnet.md",
        "competitor_analysis_lead_magnet.json",
        "competitor_lead_magnet_list",
        _sources(("client_website_url",), ("industry",), ("region_location",)),
    ),
    "competitor_analysis_blog": CompetitorConfig(
        "competitor_analysis_blog",
        "blog",
        "04_Blog.md",
        "competitor_analysis_blog.json",
        "competitor_analysis_blog",
        # `blog` collects only a topic — url/niche/region come from the run-level client profile.
        _sources((), ("blog_topic_working_title",), ()),
    ),
    # Paired with `pillar_page` since the SEO variant stage was merged into it: one Pillar Page
    # stage now designs the page *and* benchmarks its architecture against these competitor pillar
    # pages (Master_Prompt_Universal_Page_Design_v1.md v2.0, Step 2). `service` is sourced from the
    # main stage's Primary Keyword / Head Term, which is the topic a pillar page has to be found
    # for — searching on it is what makes the returned set genuine pillar pages on *this* topic
    # rather than any page the competitor happens to rank with.
    "competitor_analysis_seo_pillar_page": CompetitorConfig(
        "competitor_analysis_seo_pillar_page",
        "pillar_page",
        "05_SEO_Pillar_Page.md",
        "competitor_analysis_seo_pillar_page.json",
        "competitor_analysis_pillar_page",
        _sources(("client_website_url",), (), ()) | {"service": ("primary_keyword_head_term",)},
    ),
    "competitor_analysis_content_marketing": CompetitorConfig(
        "competitor_analysis_content_marketing",
        "content_marketing_strategy",
        "06_Content_Marketing.md",
        "competitor_analysis_content_marketing.json",
        "competitor_list",
        _sources(("client_website_url",), ("industry_niche",), ("region_country",)),
    ),
    "competitor_analysis_social_content_strategy": CompetitorConfig(
        "competitor_analysis_social_content_strategy",
        "social_content_strategy_audit",
        "07_Social_Content_Strategy_and_Posts.md",
        "competitor_analysis_social_content_strategy.json",
        "competitor_list",
        _sources(("client_website_url",), ("industry_niche",), ("region_country",)),
    ),
    "competitor_analysis_webinars": CompetitorConfig(
        "competitor_analysis_webinars",
        "webinar",
        "08_Webinars.md",
        "competitor_analysis_webinars.json",
        "competitor_analysis_webinars",
        _sources((), ("webinar_topic_working_title",), ()),
    ),
    "competitor_analysis_book": CompetitorConfig(
        "competitor_analysis_book",
        "book",
        "09_Book.md",
        "competitor_analysis_book.json",
        "competitor_analysis_book",
        _sources(("client_website_url",), ("industry_niche",), ("region_country",)),
    ),
    "competitor_analysis_podcast": CompetitorConfig(
        "competitor_analysis_podcast",
        "podcast",
        "10_Podcast.md",
        "competitor_analysis_podcast.json",
        "competitor_analysis_podcast",
        _sources((), ("episode_topic_working_title",), ()),
    ),
}

# --------------------------------------------------------------------------------------
# Phase 2
#
# Three of the seven Phase-2 stages research competitors: Lead Magnet, Blog and Content Marketing.
# Their prompt files live in `Competitor Analysis/../Phase2/competitors_phase2/` and are the Phase-1
# files re-pointed at a sub-service — `03_Lead_Magnet_phase2.md` is byte-identical to Phase 1's, and
# the other two differ only in wording ("service" -> "sub-service"). The JSON output contract is the
# same in all six, so the same schema files, the same parser, and the same review card serve both
# phases; only the file read and the value substituted into `{SERVICE}` change.
#
# That substitution is the whole point of the phase split. In Phase 1 `{SERVICE}` is the client's
# headline service; here it is the sub-service the operator named at the start of the run, so the
# search returns competitors doing *Google Ads* lead magnets rather than competitors doing marketing
# lead magnets. It reaches `resolve_inputs` through the run-level client profile's `sub_service`.
#
# All three are gated: each one is reviewed before the stage it feeds runs, because a sub-service
# competitor set is exactly the thing an operator needs to read before choosing a blog topic or
# committing to a content cluster. None of them runs as an invisible prepass.
# --------------------------------------------------------------------------------------

_PHASE2_PROMPT_SUBDIR = "../Phase2/competitors_phase2"


def _phase2_sources(
    url: tuple[str, ...], niche: tuple[str, ...], location: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    """Phase-2 placeholder sources.

    `service` is deliberately absent from every tuple: no Phase-2 stage's intake asks for the
    sub-service (it is a run-level fact established before stage 1), so it resolves from the client
    profile in `resolve_inputs` rather than from any one stage's answers.
    """
    return _sources(url, niche, location) | {"service": ()}


PHASE2_COMPETITOR_CONFIGS: dict[str, CompetitorConfig] = {
    # Phase 2's CRO stage audits and rewrites the page for one sub-service, so what it has to be
    # benchmarked against is competitors' pages *for that sub-service* — not their CRO offering,
    # which is what Phase 1's file searches for. Hence its own prompt file rather than the Phase-1
    # one: `01_CRO_phase2.md` looks for a dedicated {SERVICE} page with its own proposition, process
    # and proof, and rejects a company that sells {SERVICE} only as a line item on a combined
    # services page — such a company has no page for this one to be measured against.
    #
    # `existing_page_url` is deliberately absent from the target-URL tuple, where Phase 1 has it
    # second. In Phase 2 that field is the NEW PAGE sentinel rather than a URL (there is no existing
    # page — that is the whole reason this stage runs here), so reading it would search the market
    # for the phrase "NEW PAGE — no existing URL".
    "competitor_analysis_cro": CompetitorConfig(
        "competitor_analysis_cro",
        "cro",
        f"{_PHASE2_PROMPT_SUBDIR}/01_CRO_phase2.md",
        "competitor_analysis_cro.json",
        "competitor_analysis",
        _phase2_sources(("client_website_url",), ("sub_vertical_niche", "client_industry"), ("region_location",)),
    ),
    "competitor_analysis_lead_magnet": CompetitorConfig(
        "competitor_analysis_lead_magnet",
        "lead_magnet",
        f"{_PHASE2_PROMPT_SUBDIR}/03_Lead_Magnet_phase2.md",
        "competitor_analysis_lead_magnet.json",
        "competitor_lead_magnet_list",
        _phase2_sources(("client_website_url",), ("industry",), ("region_location",)),
    ),
    # Runs *before* the Blog stage's own intake in Phase 2, not mid-intake as in Phase 1. The
    # operator is meant to read the market's blog coverage and then choose their topic, keyword and
    # awareness level in light of it — so the search cannot be keyed on a topic they have not picked
    # yet. It searches on the sub-service instead, which is known from the start of the run.
    "competitor_analysis_blog": CompetitorConfig(
        "competitor_analysis_blog",
        "blog",
        f"{_PHASE2_PROMPT_SUBDIR}/04_Blog_phase2.md",
        "competitor_analysis_blog.json",
        "competitor_analysis_blog",
        _phase2_sources((), (), ()),
    ),
    "competitor_analysis_content_marketing": CompetitorConfig(
        "competitor_analysis_content_marketing",
        "content_marketing_strategy",
        f"{_PHASE2_PROMPT_SUBDIR}/06_Content_Marketing_phase2.md",
        "competitor_analysis_content_marketing.json",
        "competitor_list",
        _phase2_sources(("client_website_url",), ("industry_niche",), ("region_country",)),
    ),
}

CONFIGS_BY_PHASE: dict[str, dict[str, CompetitorConfig]] = {
    "phase1": COMPETITOR_CONFIGS,
    "phase2": PHASE2_COMPETITOR_CONFIGS,
}

DEFAULT_PHASE = "phase1"

# Competitor stages the operator reviews and approves in their own right, rather than having them
# run invisibly inside the paired main stage's generation call.
#
# A gated stage becomes a visible sub-step: it runs on its own, renders a competitor listing the
# operator reads, and only advances to the main asset once they save it. That is worth the extra
# gate where the competitor set materially shapes the deliverable and a bad list is worth catching
# before it is spent — which is the CRO rewrite, whose page architecture is derived from it.
#
# Everything not listed here keeps the original behavior: it runs as an invisible prepass folded
# into the main stage's own generation (see `_run_competitor_prepass` in `routers/pipeline.py`).
# Adding an asset_id here is all that is required to promote it to a gated sub-step.
# `pillar_page` joins `cro` here for a slightly different reason. Its competitor set must be pillar
# pages *on this page's topic*, and that topic (`primary_keyword_head_term`) is one of its own intake
# answers — so the search cannot be run ahead of the stage at all. The UI therefore asks the operator,
# mid-intake, whether to research it or to paste their own list, and drives the run itself
# (`COMPETITOR_CONSENT_FIELDS` in the frontend's `pipeline/pipelineData.ts`). Either way it must not
# also run as an invisible prepass inside generation, which is what listing it here prevents.
GATED_COMPETITOR_MAIN_ASSET_IDS: frozenset[str] = frozenset(
    {
        # Reviewed before the stage's own intake: everything their search needs (client URL,
        # industry, region) is already in the run-level profile.
        "cro",
        "offers",
        "lead_magnet",
        "content_marketing_strategy",
        "social_content_strategy_audit",
        "book",
        # Reviewed mid-intake, because what their search looks for is an intake answer — the pillar
        # page's head term, the blog's topic, the webinar's topic, the episode's topic. The UI asks
        # permission when the walk reaches the competitor field (`COMPETITOR_CONSENT_FIELDS` in the
        # frontend's `pipeline/pipelineData.ts`). Either way they must not *also* run as an invisible
        # prepass inside generation, which is what listing them here prevents.
        "pillar_page",
        "blog",
        "webinar",
        "podcast",
    }
)

# main asset_id -> its competitor prepass stage. The inverse of CompetitorConfig.paired_main_asset_id,
# built once so the router can answer "does this stage need a prepass?" in O(1). Gated stages are
# excluded: they are driven explicitly by the UI, so folding them in here too would run them twice.
PREPASS_BY_MAIN_ASSET: dict[str, CompetitorConfig] = {
    cfg.paired_main_asset_id: cfg
    for cfg in COMPETITOR_CONFIGS.values()
    if cfg.paired_main_asset_id not in GATED_COMPETITOR_MAIN_ASSET_IDS
}

# main asset_id -> its competitor stage, for the gated ones the UI drives directly.
GATED_COMPETITOR_BY_MAIN_ASSET: dict[str, CompetitorConfig] = {
    cfg.paired_main_asset_id: cfg
    for cfg in COMPETITOR_CONFIGS.values()
    if cfg.paired_main_asset_id in GATED_COMPETITOR_MAIN_ASSET_IDS
}

# Every Phase-2 competitor stage is gated (see the Phase 2 comment above), so Phase 2 has no
# invisible prepass at all: the map the router consults to decide "does this generation call need to
# run a search first?" is empty, and the gated map holds all three.
PREPASS_BY_MAIN_ASSET_BY_PHASE: dict[str, dict[str, CompetitorConfig]] = {
    "phase1": PREPASS_BY_MAIN_ASSET,
    "phase2": {},
}

GATED_COMPETITOR_BY_MAIN_ASSET_BY_PHASE: dict[str, dict[str, CompetitorConfig]] = {
    "phase1": GATED_COMPETITOR_BY_MAIN_ASSET,
    "phase2": {cfg.paired_main_asset_id: cfg for cfg in PHASE2_COMPETITOR_CONFIGS.values()},
}


@dataclass(frozen=True)
class ParsedCompetitor:
    rank: int
    domain: str
    name: str
    page_url: str | None
    verification_confidence: str
    offering_summary: str | None
    # Only the Offers stage reports a price (02_Offers.md's `starting_price`); everywhere else this
    # is None. Kept verbatim as published — "From $1,500/mo" — never normalised into a number.
    starting_price: str | None
    # One short stage-specific classifier where the prompt has one: lead-magnet type, blog content
    # focus, podcast topical focus. None elsewhere.
    category: str | None
    similarity_score: float | None
    avg_position: float | None
    intersections: int | None


@dataclass(frozen=True)
class ParsedAnalysis:
    """A competitor stage's output, decoded into the shape the UI renders and the DB stores.

    The raw JSON never reaches the frontend — the operator reviews a competitor listing and the
    notes, not a code block — so decoding happens here, once, rather than in the client.
    """

    competitors: list[ParsedCompetitor]
    notes: str | None
    raw_output: str

    @property
    def returned_count(self) -> int:
        return len(self.competitors)


class UnknownCompetitorStageError(KeyError):
    pass


class CompetitorParseError(ValueError):
    """The model's output could not be decoded into competitors.

    Surfaced to the operator as a retryable stage failure rather than being swallowed: an empty
    listing and a failed parse look identical in the UI otherwise, and the difference matters
    (one means "no qualifying competitors exist", the other means "we lost the result").
    """


_VALID_CONFIDENCE = {"Verified", "Partially verified", "Unverified"}

# With the server-side web_search tool, the model writes citation markup inline — observed verbatim
# in a real run: `<cite index="58-1">Every single B2B buyer…</cite>`. Left alone it is persisted to
# `competitors.offering_summary`, rendered in the listing, and spliced into the paired main prompt,
# where it reads as broken markup rather than as a citation. The tag goes; the sentence inside it
# stays, because that sentence is the observation.
_CITE_TAG = re.compile(r"</?cite[^>]*>", re.IGNORECASE)


def _clean_text(value) -> str | None:
    """Normalise one model-written text field: drop citation tags, collapse whitespace, empty -> None."""
    if value is None:
        return None
    text = _CITE_TAG.sub("", str(value))
    text = " ".join(text.split())
    return text or None


def _extract_json_payload(text: str) -> dict:
    """Pull the JSON object out of the model's response.

    The prompt demands a bare JSON object with no surrounding prose, but models routinely wrap it
    in a ```json fence anyway, and older revisions of this prompt asked for a bare *array* — so
    accept a fenced or unfenced object, and a bare array (normalised to `{"competitors": [...]}`),
    rather than failing a run over formatting the operator cannot control.
    """
    candidate = text.strip()

    if candidate.startswith("```"):
        # Strip the opening fence (```json / ```) and the trailing fence.
        first_newline = candidate.find("\n")
        candidate = candidate[first_newline + 1 :] if first_newline != -1 else candidate
        closing = candidate.rfind("```")
        if closing != -1:
            candidate = candidate[:closing]
        candidate = candidate.strip()

    # Fall back to the outermost {...} or [...] span if there is still stray prose around it.
    if not candidate.startswith(("{", "[")):
        starts = [i for i in (candidate.find("{"), candidate.find("[")) if i != -1]
        if not starts:
            raise CompetitorParseError("No JSON object or array found in the model output.")
        start = min(starts)
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        if end <= start:
            raise CompetitorParseError("Model output contained an unterminated JSON payload.")
        candidate = candidate[start : end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise CompetitorParseError(f"Model output was not valid JSON: {exc}") from exc

    if isinstance(data, list):
        return {"competitors": data, "notes": None}
    if isinstance(data, dict):
        return data
    raise CompetitorParseError(f"Expected a JSON object or array, got {type(data).__name__}.")


def _coerce_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_analysis(asset_id: str, raw_output: str) -> ParsedAnalysis:
    """Decode one competitor stage's raw response into `ParsedAnalysis`.

    Deliberately lenient on optional/metric fields (a missing `avg_position` is expected — the
    prompt says to use null) and strict on identity: a row with no domain is not a competitor and
    is dropped rather than persisted as a blank listing entry.
    """
    payload = _extract_json_payload(raw_output)
    rows = payload.get("competitors")
    if not isinstance(rows, list):
        raise CompetitorParseError("Payload has no `competitors` array.")

    competitors: list[ParsedCompetitor] = []
    seen: set[str] = set()
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        domain = str(entry.get("domain") or "").strip().lower()
        if not domain or domain in seen:
            continue  # unusable or a duplicate of one already ranked above it
        seen.add(domain)

        confidence = str(entry.get("verification_confidence") or "").strip()
        if confidence not in _VALID_CONFIDENCE:
            # Never silently upgrade an unrecognised value into "Verified" — the whole point of
            # the field is to flag what was *not* confirmed.
            confidence = "Unverified"

        # `cro_page_url` on the CRO stage; the sibling prompts name it per-asset, so accept any
        # of the *_url keys rather than hardcoding one stage's spelling.
        page_url = next(
            (str(v).strip() for k, v in entry.items() if k.endswith("_url") and v),
            None,
        )

        # `starting_price` is this prompt's own key; earlier revisions of 02_Offers.md asked for
        # `starting_price_aud`, so both are accepted rather than silently dropping the price from a
        # response that used the older spelling.
        price = next(
            (
                _clean_text(entry[key])
                for key in ("starting_price", "starting_price_aud", "price_from")
                if entry.get(key)
            ),
            None,
        )

        # The stage-specific classifier, under whichever name its own prompt uses. One field on the
        # row rather than three, since no prompt emits more than one.
        category = next(
            (
                _clean_text(entry[key])
                for key in ("lead_magnet_type", "content_focus", "topical_focus", "category")
                if entry.get(key)
            ),
            None,
        )

        competitors.append(
            ParsedCompetitor(
                rank=len(competitors) + 1,
                domain=domain,
                name=str(entry.get("name") or domain).strip(),
                page_url=page_url,
                verification_confidence=confidence,
                offering_summary=_clean_text(entry.get("offering_summary")),
                starting_price=price,
                category=category,
                similarity_score=_coerce_float(entry.get("similarity_score")),
                avg_position=_coerce_float(entry.get("avg_position")),
                intersections=_coerce_int(entry.get("intersections")),
            )
        )

    notes = _clean_text(payload.get("notes"))

    logger.info(
        "Parsed competitor stage=%s competitors=%s notes=%s",
        asset_id,
        len(competitors),
        "yes" if notes else "no",
    )
    return ParsedAnalysis(competitors=competitors, notes=notes, raw_output=raw_output)


def to_prompt_text(analysis: ParsedAnalysis, target_url: str) -> str:
    """Render an approved analysis as prose for the paired main asset's prompt.

    The main prompt's field says "PASTE OR ATTACH — architecture and structural patterns will be
    extracted", so it wants a readable benchmark, not the wire JSON. This is also what gets
    written to `context_entries`, so the context store stays human-readable.
    """
    lines = [f"Competitor analysis — benchmarked against {target_url}", ""]
    for c in analysis.competitors:
        lines.append(f"{c.rank}. {c.name} ({c.domain}) — {c.verification_confidence}")
        if c.page_url:
            lines.append(f"   Page: {c.page_url}")
        if c.category:
            lines.append(f"   Type / focus: {c.category}")
        if c.starting_price:
            lines.append(f"   Starting price: {c.starting_price}")
        if c.offering_summary:
            lines.append(f"   Offering: {c.offering_summary}")
        metrics = []
        if c.similarity_score is not None:
            metrics.append(f"similarity {c.similarity_score:g}")
        if c.avg_position is not None:
            metrics.append(f"avg position {c.avg_position:g}")
        if c.intersections is not None:
            metrics.append(f"{c.intersections} intersections")
        if metrics:
            lines.append(f"   Metrics: {', '.join(metrics)}")
        lines.append("")

    if not analysis.competitors:
        lines.append("(No qualifying competitors were returned for this run.)")
        lines.append("")
    if analysis.notes:
        lines.append(f"Notes: {analysis.notes}")
    return "\n".join(lines).strip()


# The header and entry lines `to_prompt_text` writes, read back. Kept beside it so a change to one is
# made with the other in view: the social audit reads this format off `competitor_list` to decide
# whose posts to fetch, and a drift between the two costs every competitor's data silently.
_PROSE_HEADER = re.compile(r"^\s*Competitor analysis\s*[—-]\s*benchmarked against\b", re.IGNORECASE)
_PROSE_ENTRY = re.compile(r"^\s*\d+\.\s+(?P<name>.+?)\s+\((?P<domain>[^()\s]+)\)\s+[—-]\s+(?P<confidence>.+?)\s*$")
_PROSE_PAGE = re.compile(r"^\s+Page:\s*(?P<url>\S+)\s*$")
# A website anywhere on a free-typed line: a URL, or a bare host with a dot and a 2+ letter TLD. Not
# inside an email address — `sales@acme.com` names a contact, not a competitor.
_TYPED_SITE = re.compile(
    r"(?:https?://)?(?<![@\w.-])(?P<host>(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?P<path>/[^\s,;)]*)?",
    re.IGNORECASE,
)


def _bare_domain(host: str) -> str:
    host = host.strip().lower()
    return host[4:] if host.startswith("www.") else host


def parse_competitor_listing(asset_id: str, text: str) -> ParsedAnalysis:
    """Read a competitor listing in whichever form a main stage's field actually holds it.

    `parse_analysis` reads the search model's raw JSON, and nothing downstream of approval ever sees
    that: `save_competitor_stage` files `to_prompt_text`'s prose under the context key, and that
    prose is what a `context_reference` field resolves to. An operator can also answer the field by
    hand — a list they typed, or the card's own-list option, which saves through the same route.
    So three readings, in order:

    1. the raw JSON, for a caller that still has it;
    2. `to_prompt_text`'s prose, the normal case;
    3. any line naming a website, for a list typed by hand.

    The benchmark header is skipped in every reading — its URL is the client's own site, and reading
    it as a competitor would audit the client against itself. Raises `CompetitorParseError` when no
    reading finds a single domain.
    """
    try:
        return parse_analysis(asset_id, text)
    except CompetitorParseError:
        pass

    lines = [line for line in text.splitlines() if not _PROSE_HEADER.match(line)]

    competitors: list[ParsedCompetitor] = []
    seen: set[str] = set()

    def _add(domain: str, name: str, confidence: str, page_url: str | None) -> None:
        if not domain or domain in seen:
            return
        seen.add(domain)
        competitors.append(
            ParsedCompetitor(
                rank=len(competitors) + 1,
                domain=domain,
                name=name or domain,
                page_url=page_url,
                verification_confidence=confidence if confidence in _VALID_CONFIDENCE else "Unverified",
                offering_summary=None,
                starting_price=None,
                category=None,
                similarity_score=None,
                avg_position=None,
                intersections=None,
            )
        )

    for i, line in enumerate(lines):
        entry = _PROSE_ENTRY.match(line)
        if not entry:
            continue
        page = next(
            (m.group("url") for m in (_PROSE_PAGE.match(nxt) for nxt in lines[i + 1 : i + 3]) if m),
            None,
        )
        _add(_bare_domain(entry.group("domain")), entry.group("name").strip(), entry.group("confidence"), page)

    if not competitors:
        for line in lines:
            if re.match(r"^\s*Notes:", line):
                continue
            stripped = _LIST_MARKER.sub("", line).strip()
            site = _TYPED_SITE.search(stripped)
            if not site:
                continue
            typed = site.group(0)
            page_url = typed if re.match(r"https?://", typed, re.IGNORECASE) else None
            name = (stripped[: site.start()] + " " + stripped[site.end() :]).replace("(", " ").replace(")", " ")
            name = re.sub(r"\s+", " ", name).strip(" ,;:|—–-")
            _add(_bare_domain(site.group("host")), name, "Unverified", page_url)

    if not competitors:
        raise CompetitorParseError("No competitor domains found in the listing.")

    logger.info("Read competitor listing stage=%s competitors=%s (from text)", asset_id, len(competitors))
    return ParsedAnalysis(competitors=competitors, notes=None, raw_output=text)


def _config(asset_id: str, phase: str = DEFAULT_PHASE) -> CompetitorConfig:
    try:
        configs = CONFIGS_BY_PHASE[phase]
    except KeyError as exc:
        raise UnknownCompetitorStageError(f"unknown phase {phase!r}") from exc
    try:
        return configs[asset_id]
    except KeyError as exc:
        raise UnknownCompetitorStageError(f"{asset_id!r} is not a {phase} competitor stage") from exc


def config_for(asset_id: str, phase: str = DEFAULT_PHASE) -> CompetitorConfig:
    """Public lookup for the router, which needs a stage's target field and sources by name."""
    return _config(asset_id, phase)


def _web_search_enabled() -> bool:
    """Server-side web search is ON unless explicitly disabled.

    Off means competitor domains come from training memory rather than the live web — cheaper,
    but the resulting list is unverifiable and likely stale. Set `COMPETITOR_WEB_SEARCH=0` in
    `.env` to disable.
    """
    return os.environ.get("COMPETITOR_WEB_SEARCH", "1").strip().lower() not in {"0", "false", "no", "off"}


def _schema_defaults(cfg: CompetitorConfig) -> dict[str, str]:
    """Per-stage placeholder defaults declared in the competitor schema JSON (e.g. `service` is
    fixed to "conversion rate optimisation (CRO)" for the CRO stage, `location` to
    "Australia-wide")."""
    data = json.loads((_SCHEMAS_DIR / cfg.schema_file).read_text(encoding="utf-8"))
    return {f["field_id"]: str(f["default"]) for f in data["fields"] if f.get("default") is not None}


_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s*")


def _search_subject(value: str) -> str:
    """One searchable subject out of an answer that may be a whole selection.

    A multi-select suggestion gate (`blog_topic`, `podcast_episode_topic`) answers its field with a
    numbered list carrying each pick's keyword and intent on indented lines beneath it. That is the
    right thing to hand a stage that writes every one of them, and exactly the wrong thing to
    substitute into a `{NICHE}` placeholder: the search would run on five titles and their metadata
    at once and return whatever survived the noise.

    So a list collapses to its first item, which the suggestion service has already ordered by
    demand — the strongest pick, and a genuine subject for a competitor search. A single-line answer
    is returned untouched, which is every other stage.
    """
    first = ""
    for line in value.splitlines():
        stripped = line.strip()
        # Detail lines are indented under their item; skipping them keeps "Primary keyword: ..."
        # out of the query when a list somehow leads with one.
        if not stripped or line[:1].isspace():
            continue
        first = _LIST_MARKER.sub("", stripped)
        break
    return first.strip()


def resolve_inputs(
    cfg: CompetitorConfig,
    main_answers: dict[str, str],
    client_profile: dict[str, str] | None = None,
) -> dict[str, str]:
    """Derive this prepass's placeholder values from the paired main stage's intake answers,
    falling back to the run-level client profile for stages whose own intake never asks for a
    URL/industry/region (blog, webinar, podcast collect only a topic — and blog and podcast collect
    several, which `_search_subject` reduces to the one this prepass can search on)."""
    profile = client_profile or {}
    profile_fallback = {
        "target_url": profile.get("website_url", ""),
        "niche": profile.get("industry", ""),
        "location": profile.get("region", ""),
        # Phase 2's `{SERVICE}` — the sub-service the run is for, established before stage 1 and
        # never asked again. Empty on a Phase 1 run, where no stage sources `service` from the
        # profile and the schema default or an intake answer supplies it instead.
        "service": profile.get("sub_service", ""),
    }

    resolved: dict[str, str] = {}
    for placeholder, candidates in cfg.source_fields.items():
        value = ""
        for field_id in candidates:
            candidate = _search_subject(main_answers.get(field_id) or "")
            if candidate and candidate.upper() not in {"N/A", "NONE", "UNKNOWN"}:
                value = candidate
                break
        if not value:
            value = profile_fallback.get(placeholder, "").strip()
        resolved[placeholder] = value

    for field_id, default in _schema_defaults(cfg).items():
        if not resolved.get(field_id):
            resolved[field_id] = default
    return resolved


def build_competitor_prompt(
    asset_id: str, inputs: dict[str, str], excluded_competitors: str = "", phase: str = DEFAULT_PHASE
) -> str:
    """Substitute this stage's `{PLACEHOLDER}` tokens with resolved values and return the file
    otherwise unchanged — the same "never paraphrase the real prompt" rule `generation.py`
    follows for the 16 main stages."""
    cfg = _config(asset_id, phase)
    text = (_COMPETITOR_PROMPTS_DIR / cfg.prompt_file).read_text(encoding="utf-8")

    for placeholder, key in (("{TARGET_URL}", "target_url"), ("{NICHE}", "niche"), ("{LOCATION}", "location"), ("{SERVICE}", "service")):
        value = (inputs.get(key) or "").strip()
        # An unfilled optional placeholder must not be left as a literal "{NICHE}" in the prompt —
        # each file already describes what to do when the value is absent, so say so in words.
        text = text.replace(placeholder, value or "(not specified — follow this prompt's stated fallback)")

    excluded = excluded_competitors.strip()
    if excluded:
        text += f"\n\nAlready sourced in prior runs for this client — exclude these domains: {excluded}\n"
    return text


def _extract_text(content_blocks) -> str:
    """Concatenate only the assistant's own text blocks.

    With the server-side web_search tool the response also carries `server_tool_use` and
    `web_search_tool_result` blocks; those are the search machinery, not the deliverable, and
    must not be spliced into the competitor document.
    """
    return "".join(block.text for block in content_blocks if getattr(block, "type", None) == "text")


async def generate_competitor_analysis(
    asset_id: str,
    inputs: dict[str, str],
    excluded_competitors: str = "",
    phase: str = DEFAULT_PHASE,
    on_usage: Callable[[CallUsage], Awaitable[None]] | None = None,
) -> str:
    """Run one competitor-analysis stage to completion and return its full text.

    Deliberately non-streaming: this is a prepass whose output feeds the *next* prompt rather
    than the operator's screen, and with `web_search` the model spends most of the call in tool
    round-trips that would surface as long silent gaps in a token stream anyway.
    """
    cfg = _config(asset_id, phase)
    client = get_client()
    prompt = build_competitor_prompt(asset_id, inputs, excluded_competitors, phase)

    model = resolve_model()
    effort = _effort(model)

    kwargs = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if effort:
        kwargs["output_config"] = {"effort": effort}
    if _web_search_enabled():
        kwargs["tools"] = [_web_search_tool()]

    logger.info(
        "Running competitor stage=%s phase=%s model=%s effort=%s target_url=%r service=%r "
        "web_search=%s budget=%s",
        asset_id,
        phase,
        model,
        effort or "default",
        inputs.get("target_url", ""),
        inputs.get("service", ""),
        _web_search_enabled(),
        _search_budget() if _web_search_enabled() else 0,
    )

    started = time.monotonic()
    response = await client.messages.create(**kwargs)
    text = _extract_text(response.content)

    # `searches` is logged next to `input_tokens` because those two numbers move together and are
    # the whole cost story for this stage — see the `_DEFAULT_SEARCH_BUDGET` note.
    logger.info(
        "Competitor prepass done stage=%s stop_reason=%s input_tokens=%s output_tokens=%s "
        "searches=%s chars=%s",
        asset_id,
        response.stop_reason,
        response.usage.input_tokens,
        response.usage.output_tokens,
        getattr(getattr(response.usage, "server_tool_use", None), "web_search_requests", None),
        len(text),
    )
    if response.stop_reason == "max_tokens":
        logger.warning("Competitor prepass stage=%s hit the %s-token cap and was truncated", asset_id, _MAX_TOKENS)
    if on_usage is not None:
        # The only calls in this app that use a billed server-side tool. `CallUsage.from_response`
        # reads `web_search_requests` off the response, so the per-search fee is charged on searches
        # actually performed rather than on the `max_uses` budget.
        await on_usage(
            CallUsage.from_response(
                response, requested_model=model, duration_ms=int((time.monotonic() - started) * 1000)
            )
        )
    return text
