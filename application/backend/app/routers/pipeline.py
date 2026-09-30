"""Routes for the Phase 1 pipeline UI.

Phases: every generation/competitor route takes a `phase` ("phase1" | "phase2"). It selects the
prompt file and INPUTS block for the stage (see app/services/generation.py) and, for competitor
stages, which market the search is run against. A Phase 2 run is created with `source_run_id` set to
the Phase 1 run it builds on, and inherits that run's approved context.

Persistence (Postgres):
  - POST /pipeline/runs                          bootstrap a Client + Run for one browser session,
                                                  or a linked sub-service run over an existing one
  - GET  /pipeline/source-runs                   runs a Phase 2 run could inherit context from
  - POST /pipeline/runs/{run_id}/stages/{asset_id}/save
                                                  persist a stage's approved output as a new
                                                  ContextEntry version, advance its RunStage to
                                                  APPROVED, and record the approval in the audit log

Standalone runs (skip to a stage without running the ones before it — see
docs/Asset_Dependency_Map.md):
  - GET  /pipeline/runs/{run_id}/readiness/{asset_id}
                                                  which of a stage's upstream documents this run
                                                  already has, which are missing, and what would
                                                  have to run to fill them (free, read-only)
  - POST /pipeline/runs/{run_id}/context          file a document under a context key by hand, so a
                                                  stage can run without its producer

Intake helper:
  - POST /pipeline/design                        capture a page's DESIGN.md + screenshots (17 credits)
  - GET  /pipeline/runs/{run_id}/design          the DESIGN.md already captured for a run (free)
  - POST /pipeline/scrape                        read a live page and return its copy as text, so
                                                  the operator does not paste a whole page by hand

Generation (Anthropic, real per-stage master prompts — see app/services/generation.py):
  - POST /pipeline/generate/{asset_id}/stream    stream a stage's real generation from its
                                                  intake answers
  - POST /pipeline/refine/{asset_id}/stream      stream a revision of a previous draft per an
                                                  operator's requested change
  - POST /pipeline/competitor-briefing/{asset_id}
                                                  summarise an approved competitor listing for the
                                                  operator before that stage's own intake

No context-resolution/read side or rejection/edit flow beyond what's listed above.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_sessionmaker
from app.db.models import (
    ApprovalAuditLog,
    AssetDefinition,
    AuditAction,
    ChatSession,
    Client,
    Competitor,
    CompetitorAnalysis,
    ContextEntry,
    Run,
    RunStage,
    StageStatus,
    User,
    VerificationConfidence,
)
from app.routers.auth import current_user
from app.services.competitor import (
    CONFIGS_BY_PHASE as COMPETITOR_CONFIGS_BY_PHASE,
    PREPASS_BY_MAIN_ASSET_BY_PHASE,
    CompetitorParseError,
    generate_competitor_analysis,
    parse_analysis,
    parse_competitor_listing,
    resolve_inputs,
    to_prompt_text,
)
from app.services import (
    asset_check as asset_check_service,
    business_facts as business_facts_service,
    cro_settings,
    design_md as design_md_service,
    design_tokens as design_tokens_service,
    headlines as headlines_service,
    image_briefs as image_briefs_service,
    industry_voice,
    insights,
    keywords as keywords_service,
    media_storage,
    openai_image_client,
    runway_image_client,
    page_replica as page_replica_service,
    slide_deck as slide_deck_service,
    social_audit as social_audit_service,
    sociavault_client,
    usage as usage_service,
)
from app.services.api_errors import classify as classify_api_error
from app.services.image_briefs import GeneratedImage, ImageBrief
from app.services.dependencies import (
    UNRESOLVED,
    Dependency,
    dependencies_for,
    producer_of,
    seedable_keys,
)
from app.services.generation import (
    DEFAULT_PHASE,
    PAGE_REPLICA_STAGES,
    PageDesignInput,
    generate_replica_copy,
    generate_revision_stream,
    generate_stage_stream,
    has_stage,
)
from app.services.scraper import ScrapeError, scrape_page

logger = logging.getLogger(__name__)

router = APIRouter()

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CreateRunRequest(BaseModel):
    company_name: str = "Untitled Client"
    # Set when starting a Phase 2 run against a finished Phase 1 one. The new run is created under
    # the *same client* with `source_run_id` pointing at it, which is what lets Phase 2 read Phase
    # 1's approved context (see `get_run_context`) without its own outputs overwriting them.
    source_run_id: str | None = None


class CreateRunResponse(BaseModel):
    run_id: str
    client_id: str
    source_run_id: str | None = None


class SaveStageRequest(BaseModel):
    content: NonBlankStr
    # The intake answers behind this output, for the stages that publish something from them rather
    # than from the document. Optional, and absent is a real case rather than a client that forgot:
    # a stage approved from a pasted draft has no intake behind it. See `cro_settings.capture`.
    answers: dict[str, str] | None = None


class SaveStageResponse(BaseModel):
    run_id: str
    asset_id: str
    version: int
    status: str
    saved_at: datetime


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(payload: CreateRunRequest) -> CreateRunResponse:
    """Create a fresh Client + Run so the pipeline UI has somewhere to save stage output.

    One Run per browser session/page load — no session resumption in this minimal pass.
    """
    source_uuid: uuid.UUID | None = None
    if payload.source_run_id:
        try:
            source_uuid = uuid.UUID(payload.source_run_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid source_run_id: {payload.source_run_id!r}"
            ) from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        client_id: uuid.UUID
        if source_uuid is not None:
            source = await session.get(Run, source_uuid)
            if source is None:
                raise HTTPException(status_code=404, detail=f"Source run not found: {payload.source_run_id}")
            # A sub-service belongs to the client the parent run was for. Creating a second Client
            # here would leave the two runs unrelated in every report that groups by client.
            client_id = source.client_id
        else:
            client = Client(company_name=payload.company_name)
            session.add(client)
            await session.flush()
            client_id = client.id

        run = Run(client_id=client_id, source_run_id=source_uuid)
        session.add(run)
        await session.commit()

        logger.info(
            "Created pipeline run run_id=%s client_id=%s source_run_id=%s",
            run.id,
            client_id,
            source_uuid,
        )
        return CreateRunResponse(
            run_id=str(run.id),
            client_id=str(client_id),
            source_run_id=str(source_uuid) if source_uuid else None,
        )


async def _next_version(session: AsyncSession, run_id: uuid.UUID, context_key: str) -> int:
    result = await session.execute(
        select(ContextEntry.version)
        .where(ContextEntry.run_id == run_id, ContextEntry.context_key == context_key)
        .order_by(ContextEntry.version.desc())
        .limit(1)
    )
    current = result.scalar_one_or_none()
    return (current or 0) + 1


@router.post("/runs/{run_id}/stages/{asset_id}/save", response_model=SaveStageResponse)
async def save_stage(run_id: str, asset_id: str, payload: SaveStageRequest) -> SaveStageResponse:
    """Persist one stage's approved generation output: new ContextEntry version, RunStage ->
    APPROVED, and an audit log row. This is what the "Save It" button in the chat stream calls."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        asset = await session.get(AssetDefinition, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Unknown asset_id: {asset_id!r}")

        version = await _next_version(session, run_uuid, asset_id)
        now = datetime.now(timezone.utc)

        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=asset_id,
                version=version,
                value={"content": payload.content},
                written_by_asset_id=asset_id,
            )
        )

        # The CRO stage publishes a second row: the half of its intake that describes the client
        # rather than this page. It is what lets a Phase 2 run for a sub-service inherit the buyer
        # type, sales motion, claim tier, pricing mode and resolved vocabulary instead of asking the
        # operator to settle all of it a second time. Versioned on its own key rather than folded
        # into the document, so re-approving a corrected CRO stage supersedes the settings with it
        # and a run that reads them gets the newest either way.
        if asset_id == cro_settings.PRODUCER_ASSET_ID and payload.answers:
            captured = cro_settings.capture(payload.answers)
            if captured is not None:
                session.add(
                    ContextEntry(
                        run_id=run_uuid,
                        context_key=cro_settings.CONTEXT_KEY,
                        version=await _next_version(session, run_uuid, cro_settings.CONTEXT_KEY),
                        value=captured,
                        written_by_asset_id=asset_id,
                    )
                )

        result = await session.execute(
            select(RunStage).where(RunStage.run_id == run_uuid, RunStage.asset_id == asset_id)
        )
        stage = result.scalar_one_or_none()
        if stage is None:
            stage = RunStage(run_id=run_uuid, asset_id=asset_id, started_at=now)
            session.add(stage)
        stage.status = StageStatus.APPROVED
        stage.completed_at = now
        if stage.started_at is None:
            stage.started_at = now

        run.current_stage_id = asset_id

        session.add(
            ApprovalAuditLog(
                run_id=run_uuid,
                asset_id=asset_id,
                action=AuditAction.APPROVED,
                actor="ui-operator",
                notes=f"Saved via pipeline UI (v{version})",
            )
        )

        await session.commit()

        logger.info(
            "Saved stage run_id=%s asset_id=%s version=%s",
            run_id,
            asset_id,
            version,
        )
        return SaveStageResponse(
            run_id=run_id,
            asset_id=asset_id,
            version=version,
            status=StageStatus.APPROVED.value,
            saved_at=now,
        )


# --------------------------------------------------------------------------------------
# Gated competitor sub-stage (currently `competitor_analysis_cro`)
#
# Runs as its own reviewable step between ICP and the paired main asset. Deliberately NOT an SSE
# stream like the main stages: the operator never reads this output as prose — they read a parsed
# listing — and with web search the call spends most of its time in tool round-trips that would
# stream as dead air anyway. So it is one request that returns structured rows.
# --------------------------------------------------------------------------------------


class RunCompetitorRequest(BaseModel):
    """Placeholder values for the competitor prompt. All optional: whatever the UI has learned so
    far (from ICP intake) is passed, and anything missing falls back to the stage's schema
    defaults inside `resolve_inputs`."""

    target_url: str = ""
    niche: str = ""
    location: str = ""
    service: str = ""
    # Which pipeline is asking. Phase 2 reads the same stages from its own prompt files, with the
    # sub-service substituted for the client's headline service — see the Phase 2 section of
    # app/services/competitor.py.
    phase: str = DEFAULT_PHASE
    # Which chat and run this call is spent by, so `api_usage` can attribute it. Both optional and
    # both free-form: a brand-new chat makes its first call before its session row exists, and a run
    # only exists once a stage is approved — usage from before either is still recorded, just
    # unattributed, rather than being dropped for want of a foreign key.
    chat_session_id: str | None = None
    run_id: str | None = None


class CompetitorOut(BaseModel):
    rank: int
    domain: str
    name: str
    page_url: str | None = None
    verification_confidence: str
    offering_summary: str | None = None
    # Populated by the Offers stage only, verbatim as the competitor publishes it ("From $1,500/mo").
    starting_price: str | None = None
    # One stage-specific classifier where the prompt has one: lead-magnet type, blog content focus,
    # podcast topical focus.
    category: str | None = None
    similarity_score: float | None = None
    avg_position: float | None = None
    intersections: int | None = None


class RunCompetitorResponse(BaseModel):
    asset_id: str
    target_url: str
    # The model's exact JSON response. Sent back so `/save` can persist it on the analysis row for
    # audit and re-parsing, NOT for display — the UI renders the parsed rows below and never this.
    raw_output: str = ""
    service: str | None = None
    niche: str | None = None
    location: str | None = None
    requested_count: int
    returned_count: int
    competitors: list[CompetitorOut]
    notes: str | None = None


class SaveCompetitorRequest(BaseModel):
    """The reviewed analysis, echoed back for persistence.

    Sent back rather than re-generated so that saving costs nothing and cannot return a *different*
    competitor set than the one the operator actually approved.
    """

    target_url: str
    raw_output: str = ""
    service: str | None = None
    niche: str | None = None
    location: str | None = None
    notes: str | None = None
    competitors: list[CompetitorOut] = []


class SaveCompetitorResponse(BaseModel):
    run_id: str
    asset_id: str
    analysis_id: str
    competitor_count: int
    version: int
    saved_at: datetime
    # The prose written to `context_entries` — returned so the UI can put the identical text into
    # its own context store, rather than re-deriving a near-copy that could drift from what the
    # paired main prompt actually receives.
    context_text: str


@router.post("/competitor/{asset_id}/run", response_model=RunCompetitorResponse)
async def run_competitor_stage(asset_id: str, payload: RunCompetitorRequest) -> RunCompetitorResponse:
    """Execute one competitor-analysis stage and return its parsed listing.

    Nothing is persisted here — the operator reviews first, then `/save` writes it. That keeps a
    rejected or re-run analysis from leaving orphaned rows behind.
    """
    cfg = COMPETITOR_CONFIGS_BY_PHASE.get(payload.phase, {}).get(asset_id)
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown competitor asset_id {asset_id!r} for phase {payload.phase!r}",
        )

    inputs = resolve_inputs(
        cfg,
        main_answers={},
        client_profile={
            "website_url": payload.target_url,
            "industry": payload.niche,
            "region": payload.location,
            "sub_service": payload.service,
        },
    )
    if payload.service.strip():
        inputs["service"] = payload.service.strip()

    if not inputs.get("target_url"):
        raise HTTPException(
            status_code=400,
            detail=(
                "No target URL available for competitor analysis. Capture the client's website URL "
                "in an earlier stage before running this step."
            ),
        )

    try:
        raw = await generate_competitor_analysis(
            asset_id,
            inputs,
            phase=payload.phase,
            on_usage=usage_service.recorder(
                kind="competitor",
                chat_session_id=payload.chat_session_id,
                run_id=payload.run_id,
                asset_id=asset_id,
                phase=payload.phase,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - classified below and returned to the UI
        fault = classify_api_error(exc)
        logger.exception("Competitor stage failed asset_id=%r fault=%s", asset_id, fault.code)
        # The detail is the fault object itself, not a sentence: the UI renders the same dialog for
        # an account-level failure whether it surfaced here or mid-stream.
        raise HTTPException(status_code=502, detail=fault.as_event()) from exc

    try:
        parsed = parse_analysis(asset_id, raw)
    except CompetitorParseError as exc:
        logger.exception("Could not parse competitor output for stage=%r", asset_id)
        raise HTTPException(status_code=502, detail=f"Could not read competitor results: {exc}") from exc

    return RunCompetitorResponse(
        asset_id=asset_id,
        target_url=inputs["target_url"],
        raw_output=parsed.raw_output,
        service=inputs.get("service") or None,
        niche=inputs.get("niche") or None,
        location=inputs.get("location") or None,
        requested_count=10,
        returned_count=parsed.returned_count,
        competitors=[
            CompetitorOut(
                rank=c.rank,
                domain=c.domain,
                name=c.name,
                page_url=c.page_url,
                verification_confidence=c.verification_confidence,
                offering_summary=c.offering_summary,
                starting_price=c.starting_price,
                category=c.category,
                similarity_score=c.similarity_score,
                avg_position=c.avg_position,
                intersections=c.intersections,
            )
            for c in parsed.competitors
        ],
        notes=parsed.notes,
    )


@router.post("/runs/{run_id}/competitor/{asset_id}/save", response_model=SaveCompetitorResponse)
async def save_competitor_stage(
    run_id: str, asset_id: str, payload: SaveCompetitorRequest
) -> SaveCompetitorResponse:
    """Persist an approved competitor analysis to its own tables, and mirror it into the context
    store as prose so the paired main asset's prompt can read it."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    # Phase-agnostic on purpose: the two phases share every competitor stage id, schema and output
    # contract, so what is being saved is the same shape either way.
    if not any(asset_id in configs for configs in COMPETITOR_CONFIGS_BY_PHASE.values()):
        raise HTTPException(status_code=404, detail=f"Unknown competitor asset_id: {asset_id!r}")

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        now = datetime.now(timezone.utc)
        analysis = CompetitorAnalysis(
            run_id=run_uuid,
            asset_id=asset_id,
            target_url=payload.target_url,
            service=payload.service,
            niche=payload.niche,
            location=payload.location,
            requested_count=10,
            returned_count=len(payload.competitors),
            notes=payload.notes,
            # The generating model's exact JSON, kept so a listing can be re-parsed or audited
            # without paying for another web-search run.
            raw_output=payload.raw_output or None,
        )
        session.add(analysis)
        await session.flush()

        for c in payload.competitors:
            session.add(
                Competitor(
                    analysis_id=analysis.id,
                    rank=c.rank,
                    domain=c.domain,
                    name=c.name,
                    page_url=c.page_url,
                    verification_confidence=VerificationConfidence(c.verification_confidence),
                    offering_summary=c.offering_summary,
                    starting_price=c.starting_price,
                    category=c.category,
                    similarity_score=c.similarity_score,
                    avg_position=c.avg_position,
                    intersections=c.intersections,
                )
            )

        # Mirror into the context store: `context_entries` is what the paired main prompt reads,
        # and it is deliberately prose rather than JSON (see `to_prompt_text`).
        from app.services.competitor import ParsedAnalysis, ParsedCompetitor  # local: avoid cycle

        prose = to_prompt_text(
            ParsedAnalysis(
                competitors=[
                    ParsedCompetitor(
                        rank=c.rank,
                        domain=c.domain,
                        name=c.name,
                        page_url=c.page_url,
                        verification_confidence=c.verification_confidence,
                        offering_summary=c.offering_summary,
                        starting_price=c.starting_price,
                        category=c.category,
                        similarity_score=c.similarity_score,
                        avg_position=c.avg_position,
                        intersections=c.intersections,
                    )
                    for c in payload.competitors
                ],
                notes=payload.notes,
                raw_output="",
            ),
            payload.target_url,
        )

        version = await _next_version(session, run_uuid, asset_id)
        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=asset_id,
                version=version,
                value={"content": prose, "competitor_analysis_id": str(analysis.id)},
                written_by_asset_id=asset_id,
            )
        )

        result = await session.execute(
            select(RunStage).where(RunStage.run_id == run_uuid, RunStage.asset_id == asset_id)
        )
        stage = result.scalar_one_or_none()
        if stage is None:
            stage = RunStage(run_id=run_uuid, asset_id=asset_id, started_at=now)
            session.add(stage)
        stage.status = StageStatus.APPROVED
        stage.completed_at = now
        if stage.started_at is None:
            stage.started_at = now

        session.add(
            ApprovalAuditLog(
                run_id=run_uuid,
                asset_id=asset_id,
                action=AuditAction.APPROVED,
                actor="ui-operator",
                notes=f"Approved {len(payload.competitors)} competitors (v{version})",
            )
        )

        await session.commit()

        logger.info(
            "Saved competitor analysis run_id=%s asset_id=%s competitors=%s",
            run_id,
            asset_id,
            len(payload.competitors),
        )
        return SaveCompetitorResponse(
            run_id=run_id,
            asset_id=asset_id,
            analysis_id=str(analysis.id),
            competitor_count=len(payload.competitors),
            version=version,
            saved_at=now,
            context_text=prose,
        )


# --------------------------------------------------------------------------------------
# Page reader
#
# Serves the intake fields that ask an operator to paste a whole live page — today the CRO stage's
# "Existing Page Content", whose URL the stage collected one question earlier. Kept as its own
# request rather than folded into generation: the operator has to be able to see what was read
# before it becomes the page the audit quotes.
# --------------------------------------------------------------------------------------


class ScrapePageRequest(BaseModel):
    url: NonBlankStr


class ScrapePageResponse(BaseModel):
    url: str
    final_url: str
    title: str | None = None
    meta_description: str | None = None
    content: str
    char_count: int
    word_count: int
    truncated: bool
    # True when so little text came back that the page is probably client-rendered. Not an error —
    # the UI decides whether to use it or ask the operator to paste instead.
    low_content: bool
    # "direct" (this backend's own fetch), "context.dev" (Context.dev's rendered Markdown scrape),
    # or "claude" (Anthropic's server-side fetcher). The last two are used only when the direct read
    # is refused or empty. Shown in the UI so a fallback read is never invisible.
    source: str = "direct"
    warnings: list[str] = []


@router.post("/scrape", response_model=ScrapePageResponse)
async def scrape_page_route(payload: ScrapePageRequest) -> ScrapePageResponse:
    """Read a live page and return its copy as structure-preserving text."""
    try:
        page = await scrape_page(payload.url)
    except ScrapeError as exc:
        # 422, not 502: every `ScrapeError` describes something about the requested URL that the
        # operator can act on (wrong address, login wall, not a web page), not a failure of ours.
        #
        # Logged at WARNING deliberately: the operator sees a card in the chat, but whoever is
        # watching the server console is the one who can tell a bot wall from a typo, and an INFO
        # line is invisible at the default level.
        logger.warning("Page read failed url=%r: %s", payload.url, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ScrapePageResponse(
        url=page.url,
        final_url=page.final_url,
        title=page.title,
        meta_description=page.meta_description,
        content=page.text,
        char_count=page.char_count,
        word_count=page.word_count,
        truncated=page.truncated,
        low_content=page.low_content,
        source=page.source,
        warnings=page.warnings,
    )


class RunContextResponse(BaseModel):
    run_id: str
    context_key: str
    version: int
    content: str
    # Set when the value came from a run this one inherits from rather than from this run — i.e. a
    # Phase 2 stage reading a Phase 1 asset. The UI labels reused context with it, so an operator can
    # tell "the ICP from the parent run" from "an ICP approved in this run".
    inherited_from_run_id: str | None = None


# A Phase-2 run chains to the Phase-1 run it was started from, and in principle that chain could be
# longer (a sub-sub-service). Bounded so a `source_run_id` cycle — which nothing creates today, but
# which a bad backfill or a hand-edited row could — cannot spin this request forever.
_MAX_SOURCE_RUN_HOPS = 4


# Context keys that stop at the run that wrote them, instead of being inherited down the
# `source_run_id` chain like everything else.
#
# Inheritance is the default because what a sub-service run inherits describes the *client* — its
# brand design, its CRO client settings, its industry — and none of that changes per sub-service.
# These keys are the exception, and the distinction is about *scope*, not about freshness: each one
# describes the *service* a run is for, and a sub-service is a different service.
#
#   `icp`, `offers`,      — the buyer, and the value ladder priced for that buyer. Phase 1 profiles
#   `offer_ladder`          the buyer of the headline service; Phase 2 runs its own ICP stage for the
#                           sub-service, because the person who buys "Meta Ads" is often not the one
#                           who buys "Social Media Marketing". Inherited, the parent's ICP silently
#                           answered six Phase 2 stages whenever the run's own had not been built —
#                           a stage started out of order read the wrong buyer with no sign of it.
#                           `offers` is here as well as `offer_ladder` because an approved stage is
#                           stored under its asset id, and readiness looks a document up under both.
#   `keyword_clusters`    — search demand for the service the run is for. Phase 1's is demand for
#                           "Social Media Marketing"; Phase 2's is demand for "Meta Ads". They are
#                           different keyword universes with different volumes and different
#                           competition, and one is not an approximation of the other.
#   `selected_headlines`  — the topics the operator picked, which are what every later stage in
#                           that leg is written about. Inheriting them would put Phase 2's Meta Ads
#                           blog, lead magnets and SMS sequence under a headline chosen for the
#                           parent service — the exact topic drift the selection gate exists to
#                           stop, reintroduced silently one level down.
#
# So a Phase-2 run that has not built its own gets a 404 and builds one, rather than quietly
# working from the parent's. Everything else still inherits.
PHASE_SCOPED_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"keyword_clusters", "selected_headlines", "icp", "offers", "offer_ladder"}
)


async def _latest_context_entry(
    session: AsyncSession, run_uuid: uuid.UUID, context_key: str
) -> tuple[ContextEntry, uuid.UUID] | None:
    """The newest approved entry for `context_key` on this run, or on the nearest run it inherits
    from. Returns the entry and the run it was actually found on.

    Keys in `PHASE_SCOPED_CONTEXT_KEYS` never leave the run that wrote them — the walk is capped at
    one hop for those, so a Phase-2 run cannot read its parent's keyword set or chosen topics.
    """
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = run_uuid
    max_hops = 1 if context_key in PHASE_SCOPED_CONTEXT_KEYS else _MAX_SOURCE_RUN_HOPS

    for _ in range(max_hops):
        if current is None or current in seen:
            return None
        seen.add(current)

        result = await session.execute(
            select(ContextEntry)
            .where(ContextEntry.run_id == current, ContextEntry.context_key == context_key)
            .order_by(ContextEntry.version.desc())
            .limit(1)
        )
        entry = result.scalar_one_or_none()
        if entry is not None:
            return entry, current

        run = await session.get(Run, current)
        current = run.source_run_id if run is not None else None

    return None


@router.get("/runs/{run_id}/context/{context_key}", response_model=RunContextResponse)
async def get_run_context(run_id: str, context_key: str) -> RunContextResponse:
    """Return the latest approved output stored under `context_key` for this run.

    The UI keeps its own in-memory copy of everything approved during a session, so this is the
    fallback for when that copy isn't available — a chat resumed on another machine, or a stage
    re-entered after the tab was closed. `context_entries` is append-only, so "latest" is the
    highest version rather than a mutable current row.

    A run that declares a `source_run_id` also inherits its source's context: this is how a Phase 2
    sub-service run reads the ICP, CRO rewrite and value ladder its Phase 1 parent approved, without
    those keys having to be copied forward. The inheritance is one-directional and the run's own
    entries always win, so a Phase-2 pillar page never becomes the Phase-1 client's pillar page, and
    two sub-services off one parent cannot see each other's work.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        found = await _latest_context_entry(session, run_uuid, context_key)
        if found is None:
            raise HTTPException(
                status_code=404, detail=f"No saved output for {context_key!r} on run {run_id}"
            )
        entry, found_on = found

        content = entry.value.get("content") if isinstance(entry.value, dict) else None
        if not content:
            raise HTTPException(
                status_code=404, detail=f"Saved entry for {context_key!r} has no content"
            )

        return RunContextResponse(
            run_id=run_id,
            context_key=context_key,
            version=entry.version,
            content=content,
            inherited_from_run_id=str(found_on) if found_on != run_uuid else None,
        )


# --------------------------------------------------------------------------------------
# Standalone runs: seed a dependency, and report what a stage is still missing
#
# The pipeline's default is to run in order, and these two routes are what let an operator skip to
# a stage anyway. `docs/Asset_Dependency_Map.md` is the analysis behind them; the graph itself lives
# in `app/services/dependencies.py`, so nothing here maintains a second copy of it.
#
# The division of labour between the two:
#
#   GET  /runs/{id}/readiness/{asset}  — read-only, free. "Can `offers` run on this run, and if not,
#                                        what exactly is missing and what are my options for each."
#   POST /runs/{id}/context            — the "I'll provide it myself" option: file a document under
#                                        a context key by hand, as if a stage had approved it.
#
# What is deliberately *not* here: anything to do with the competitor prepasses. Each of the ten
# runs itself inside its own stage and does its own web search, so it is never something an operator
# has to supply or run first. Readiness reports those fields under `prepass_fields`, apart from
# `dependencies`, for exactly that reason — `lead_magnet`'s competitor list is a `required` field
# that no asset upstream produces, and listing it as a dependency would send an operator hunting
# for a stage that does not exist.
# --------------------------------------------------------------------------------------


class SeedContextRequest(BaseModel):
    context_key: NonBlankStr
    # The document itself. No upper bound — a real ICP is several thousand characters. Blank is the
    # only rejected value, because a blank seed satisfies every downstream check while carrying
    # nothing, which is worse for the operator than the stage having asked.
    content: NonBlankStr
    # Free text the operator leaves for themselves ("client's own ICP deck, Aug 2026"). Stored on
    # the entry, not in the audit log: `approval_audit_log.asset_id` is a non-null FK to a stage and
    # a seeded key has no stage behind it.
    note: str | None = None


class SeedContextResponse(BaseModel):
    run_id: str
    context_key: str
    version: int
    chars: int
    #: The asset that normally writes this key, so the UI can say what has been stood in for.
    producer: str | None = None


@router.post("/runs/{run_id}/context", response_model=SeedContextResponse)
async def seed_run_context(run_id: str, payload: SeedContextRequest) -> SeedContextResponse:
    """File a document under a context key by hand, so a stage that depends on it can run without
    the stage that would normally have produced it.

    This is the "I already have this" path: a client who arrives with their own ICP, or an operator
    who wants only the Plan of Action and has the twelve documents before it sitting in a folder.
    The entry is written exactly like an approved stage output — same table, same key, same version
    sequence — so every reader downstream (`get_run_context`, and the UI's own context resolution
    on top of it) needs no special case. It is marked `seeded: true` in the stored value so the one
    thing that *is* different is visible: no stage produced it and nothing was reviewed.

    `written_by_asset_id` is left null rather than pointed at the producing asset. Claiming `icp`
    wrote a document `icp` never saw would corrupt the one column that says where output came from.

    Seeding does not lock anything out. If the real stage is run later it appends a higher version
    and wins, because "latest" is `ORDER BY version DESC` — the seed becomes history rather than an
    obstacle.

    Only keys some asset actually reads are accepted (422 lists them). That is not caution about
    write volume: a mistyped key is accepted silently by the database, and the operator's evidence
    that they had supplied the dependency would be the stage carrying on asking for it.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    allowed = seedable_keys()
    if payload.context_key not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{payload.context_key!r} is not a seedable context key — no stage reads it. "
                f"Seedable keys: {', '.join(sorted(allowed))}"
            ),
        )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        version = await _next_version(session, run_uuid, payload.context_key)
        value: dict[str, object] = {"content": payload.content, "seeded": True}
        if payload.note:
            value["note"] = payload.note

        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=payload.context_key,
                version=version,
                value=value,
                written_by_asset_id=None,
            )
        )
        await session.commit()

    logger.info(
        "Seeded context run_id=%s context_key=%r version=%d chars=%d",
        run_id,
        payload.context_key,
        version,
        len(payload.content),
    )
    return SeedContextResponse(
        run_id=run_id,
        context_key=payload.context_key,
        version=version,
        chars=len(payload.content),
        producer=producer_of(payload.context_key),
    )


# --------------------------------------------------------------------------------------
# The client's industry — inferred from ICP intake, confirmed by the operator
#
# `industry_voice.py` owns the taxonomy, the classifier and the voice packs; these routes only move
# a profile between the UI and the run. Inference stores nothing: a guess is not an answer until the
# operator confirms it, and it is the confirmation that is written.
# --------------------------------------------------------------------------------------


class IndustryProfileOut(BaseModel):
    bucket: str
    bucket_label: str
    label: str
    source: str
    confidence: float | None = None
    rationale: str = ""


def _industry_out(profile: industry_voice.IndustryProfile) -> IndustryProfileOut:
    return IndustryProfileOut(
        bucket=profile.bucket,
        bucket_label=profile.bucket_label,
        label=profile.label,
        source=profile.source,
        confidence=profile.confidence,
        rationale=profile.rationale,
    )


class InferIndustryRequest(BaseModel):
    #: The ICP intake answers (or whatever intake is at hand) — the classifier reads a fixed subset.
    answers: dict[str, str] = Field(default_factory=dict)
    #: Set when the operator typed the industry themselves: the classifier then only maps it.
    typed: str | None = None


class InferIndustryResponse(BaseModel):
    #: Null means "no confident guess" — the UI asks the operator outright.
    profile: IndustryProfileOut | None = None


@router.post("/industry/infer", response_model=InferIndustryResponse)
async def infer_industry_route(payload: InferIndustryRequest) -> InferIndustryResponse:
    """Guess the client's own industry bucket, or map a typed one. One small Haiku call plus a free
    direct read of the home page's title; stores nothing."""
    profile = await industry_voice.infer_industry(payload.answers, typed=payload.typed)
    return InferIndustryResponse(profile=_industry_out(profile) if profile is not None else None)


class SaveIndustryRequest(BaseModel):
    bucket: NonBlankStr
    label: NonBlankStr
    source: Literal["inferred_confirmed", "operator_picked", "operator_typed"]
    confidence: float | None = None
    rationale: str = ""


class IndustryResponse(BaseModel):
    run_id: str
    profile: IndustryProfileOut
    version: int | None = None
    inherited_from_run_id: str | None = None


@router.post("/runs/{run_id}/industry", response_model=IndustryResponse)
async def save_run_industry(run_id: str, payload: SaveIndustryRequest) -> IndustryResponse:
    """File the operator's confirmed industry on the run. Append-only like every context entry, so a
    correction is a new version and the latest wins; a Phase 2 child inherits it down `source_run_id`."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc
    if not industry_voice.is_valid_bucket(payload.bucket):
        raise HTTPException(
            status_code=422,
            detail=f"{payload.bucket!r} is not an industry bucket. Valid buckets: {', '.join(industry_voice.BUCKETS)}",
        )

    profile = industry_voice.IndustryProfile(
        bucket=payload.bucket,
        label=" ".join(payload.label.split()),
        source=payload.source,
        confidence=payload.confidence,
        rationale=payload.rationale,
    )
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        version = await _next_version(session, run_uuid, industry_voice.CONTEXT_KEY)
        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=industry_voice.CONTEXT_KEY,
                version=version,
                value=industry_voice.render(profile),
                written_by_asset_id=None,
            )
        )
        await session.commit()

    logger.info("Saved industry run_id=%s bucket=%s source=%s version=%d", run_id, profile.bucket, profile.source, version)
    return IndustryResponse(run_id=run_id, profile=_industry_out(profile), version=version)


@router.get("/runs/{run_id}/industry", response_model=IndustryResponse)
async def get_run_industry(run_id: str) -> IndustryResponse:
    """The run's confirmed industry, inherited from its source run when it has none of its own.
    404 when neither has one — the UI's cue to ask. Free, safe to poll."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        found = await _latest_context_entry(session, run_uuid, industry_voice.CONTEXT_KEY)
    profile = industry_voice.from_entry(found[0].value) if found is not None else None
    if found is None or profile is None:
        raise HTTPException(status_code=404, detail=f"No industry recorded for run {run_id}")
    entry, found_on = found
    return IndustryResponse(
        run_id=run_id,
        profile=_industry_out(profile),
        version=entry.version,
        inherited_from_run_id=str(found_on) if found_on != run_uuid else None,
    )


async def _run_industry(run_id: str | None) -> industry_voice.IndustryProfile | None:
    """The profile a stage is written with, or None (the default voice). Never raises: a lookup
    failure costs the voice pack, not the stage."""
    if not run_id:
        return None
    try:
        run_uuid = uuid.UUID(run_id)
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            found = await _latest_context_entry(session, run_uuid, industry_voice.CONTEXT_KEY)
    except Exception as exc:  # noqa: BLE001 — degrade to the default voice
        logger.warning("Industry lookup failed for run_id=%s: %s", run_id, exc)
        return None
    return industry_voice.from_entry(found[0].value) if found is not None else None


class DependencyStatus(BaseModel):
    field_id: str
    label: str
    context_key: str
    #: Set when the field reads one row out of the document rather than the whole thing. Reported so
    #: the UI can say which part is wanted; readiness itself is judged at the key, because whether a
    #: sub-key can be found inside a document is the resolver's business, not this route's.
    sub_key: str | None = None
    required: bool
    fallback: str
    #: The asset whose output lands under `context_key`, or null when nothing writes it — in which
    #: case "run the producer first" is not one of the operator's options and seeding is.
    producer: str | None = None
    ready: bool
    #: The key the document was actually found under, which is what to fetch it back with. Either
    #: `context_key` (a hand-seeded document) or `producer` (an approved stage, which the store
    #: files under its asset id and nothing else). Null when nothing was found.
    stored_under: str | None = None
    #: A field whose schema declares no upstream key at all — the `unresolved_context_key`
    #: placeholder, on three fields. Nothing produces it and nothing can seed it: the stage asks
    #: for it every time, by design. Reported so the UI can show it as a question rather than as a
    #: missing document, and excluded from `blocked` for the same reason — `funnel_hub_media`'s
    #: reference folder is `required` and would otherwise make that stage permanently blocked.
    manual: bool = False
    #: True when what is there was seeded by hand rather than approved from a stage.
    seeded: bool = False
    source: Literal["this_run", "inherited"] | None = None
    #: The run the value was actually found on, when that is not this one (a Phase 2 run reading its
    #: Phase 1 parent). Null for anything found on this run.
    from_run_id: str | None = None
    version: int | None = None
    chars: int | None = None


class PrepassFieldStatus(BaseModel):
    """A field the stage's own competitor prepass fills. Never an operator's problem — reported so
    the UI can show the step without offering to satisfy it."""

    field_id: str
    label: str
    context_key: str
    required: bool
    #: Whether this run has already run the prepass, i.e. whether entering the stage will spend a
    #: search or reuse what is there.
    ready: bool
    version: int | None = None


class AssetReadinessResponse(BaseModel):
    run_id: str
    asset_id: str
    phase: str
    #: True when at least one required dependency is missing. The stage can still be entered — the
    #: fallback on 64 of the 65 context fields is to ask — but it will ask for a document.
    blocked: bool
    dependencies: list[DependencyStatus]
    #: The competitor prepass that runs inside this stage, if it has one.
    prepass: str | None = None
    prepass_fields: list[PrepassFieldStatus] = []
    #: Assets that would have to run, in order, to satisfy what is *still* missing. Computed against
    #: this run rather than statically, so a run that already holds the ICP is not told to run it.
    run_first: list[str] = []
    #: Context keys this stage reads that a caller may seed by hand, for the "I'll provide it" path.
    seedable: list[str] = []
    #: Keys this stage publishes when it completes, so a caller can see what running it unblocks.
    writes: list[str] = []


@router.get("/runs/{run_id}/readiness/{asset_id}", response_model=AssetReadinessResponse)
async def get_asset_readiness(
    run_id: str, asset_id: str, phase: str = DEFAULT_PHASE
) -> AssetReadinessResponse:
    """What `asset_id` needs before it can run, and which of those this run already has.

    Free and read-only — it reads the context store and the dependency graph, and nothing else. This
    is what the "start at this stage" gate is built on: for each missing dependency the operator gets
    the same three options, and the fields here are what decide which of them are even offered.

      - **use what is there** — `ready` is true. `source`, `from_run_id` and `seeded` say where it
        came from, because "the ICP from the parent run" and "an ICP approved in this run" are not
        the same offer.
      - **provide it yourself** — always available for a key in `seedable`; the only option when
        `producer` is null (`email_sequence_copy`, which `sms_sequence` reads and no asset writes).
      - **run the producer first** — `run_first` in dependency order, already filtered to what is
        actually missing.

    Inheritance is honoured throughout: a Phase 2 run reading its Phase 1 parent's ICP is ready, not
    blocked. `PHASE_SCOPED_CONTEXT_KEYS` still does not cross the boundary.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    if not has_stage(asset_id, phase):
        raise HTTPException(
            status_code=404, detail=f"Unknown asset_id {asset_id!r} for phase {phase!r}"
        )

    spec = dependencies_for(asset_id, phase)

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # One probe per distinct key, however many fields read it — `offers` reads the CRO
        # terminology map three times, for three different rows of it.
        probes: dict[str, tuple[ContextEntry, uuid.UUID] | None] = {}

        async def probe(context_key: str) -> tuple[ContextEntry, uuid.UUID] | None:
            if context_key not in probes:
                probes[context_key] = await _latest_context_entry(session, run_uuid, context_key)
            return probes[context_key]

        async def resolve(dep: Dependency) -> tuple[ContextEntry, uuid.UUID, str] | None:
            """The stored entry that satisfies `dep`, and the key it was actually stored under.

            Two keys have to be tried, because the store has two ways of holding the same document
            and a field can be satisfied by either:

              - the context key itself — which is where a hand-seeded document lands, and
              - the *producing asset's id* — which is where an approved stage lands. `save_stage`
                writes `context_key = asset_id` and nothing else, so the four extra keys `cro`
                publishes (`cro_rewritten_copy` and the rest) have no rows of their own. Probing
                only the key would report the CRO rewrite as missing on a run that had approved
                the CRO stage — the one wrong answer that would make this route worse than nothing,
                because the operator would go and run a stage they had already run.

            The session store on the client resolves the same way round: an approved stage's entry
            is filed under every key in its `writesContextKeys`.

            An entry whose value carries no `content` counts as missing rather than as ready — it is
            the shape a pre-DESIGN.md row can have, and reporting it ready would hand the stage an
            empty document. `get_run_context` 404s on the same condition.
            """
            candidates = [dep.context_key]
            if dep.producer and dep.producer != dep.context_key:
                candidates.append(dep.producer)
            for key in candidates:
                found = await probe(key)
                if found is None or not isinstance(found[0].value, dict):
                    continue
                content = found[0].value.get("content")
                if isinstance(content, str) and content:
                    return found[0], found[1], key
            return None

        dependencies: list[DependencyStatus] = []
        for dep in spec.dependencies:
            found = await resolve(dep)
            status = DependencyStatus(
                field_id=dep.field_id,
                label=dep.label,
                context_key=dep.context_key,
                sub_key=dep.sub_key,
                required=dep.required,
                fallback=dep.fallback,
                producer=dep.producer,
                ready=found is not None,
                manual=dep.context_key == UNRESOLVED,
            )
            if found is not None:
                entry, found_on, stored_under = found
                status = status.model_copy(
                    update={
                        "stored_under": stored_under,
                        "seeded": bool(entry.value.get("seeded")),
                        "source": "this_run" if found_on == run_uuid else "inherited",
                        "from_run_id": None if found_on == run_uuid else str(found_on),
                        "version": entry.version,
                        "chars": len(str(entry.value.get("content"))),
                    }
                )
            dependencies.append(status)

        prepass_fields: list[PrepassFieldStatus] = []
        for dep in spec.prepass_fields:
            found = await probe(dep.context_key)
            prepass_fields.append(
                PrepassFieldStatus(
                    field_id=dep.field_id,
                    label=dep.label,
                    context_key=dep.context_key,
                    required=dep.required,
                    ready=found is not None,
                    version=found[0].version if found is not None else None,
                )
            )

        # The chain of assets still to run, in dependency order. Same walk as
        # `transitive_producers`, except a branch stops the moment the run already holds the
        # document — which is the difference between "run twelve stages" and "run one".

        order: list[str] = []
        walked: set[str] = set()

        async def walk(current: str) -> None:
            if current in walked:
                return
            walked.add(current)
            try:
                current_spec = dependencies_for(current, phase)
            except KeyError:
                # An asset this phase does not run. Not an error worth failing the report over —
                # it simply contributes nothing to walk.
                return
            for dep in current_spec.required:
                if dep.producer and dep.producer != current and await resolve(dep) is None:
                    await walk(dep.producer)
            if current != asset_id and current not in order:
                order.append(current)

        await walk(asset_id)

    seedable = sorted({d.context_key for d in spec.dependencies} & seedable_keys())

    return AssetReadinessResponse(
        run_id=run_id,
        asset_id=asset_id,
        phase=phase,
        blocked=any(d.required and not d.ready and not d.manual for d in dependencies),
        dependencies=dependencies,
        prepass=spec.prepass,
        prepass_fields=prepass_fields,
        run_first=order,
        seedable=seedable,
        writes=list(spec.writes),
    )


# --------------------------------------------------------------------------------------
# Keyword clustering
#
# Runs once per run as a prepass immediately after ICP is approved — the first moment the run knows
# what service it is for, which region, and under what brand. Not a stage: there is no reviewable
# deliverable here and nothing for the operator to approve. It produces the search-demand evidence
# every later headline suggestion is grounded in, so it behaves like `_run_competitor_prepass` —
# it runs, it reports a status, and its output is filed to the Context Store.
#
# Deliberately per-run rather than per-stage. One clustering pass costs real money at DataForTopicClusttering
# (three calls per seed) and, more importantly, running it per stage would give the blog and the
# pillar page *different* keyword universes for the same service — so the topics chosen at one
# stage would stop lining up with the topics chosen at the next.
# --------------------------------------------------------------------------------------

KEYWORD_CONTEXT_KEY = "keyword_clusters"


class BuildKeywordsRequest(BaseModel):
    phase: str = DEFAULT_PHASE
    # The run-level client facts the UI has collected (client_name, website_url, region, industry,
    # and for Phase 2 sub_service). Sent rather than re-read from the database because the profile
    # lives in the session, not in a table — same reason the competitor routes take it.
    profile: dict[str, str] = {}
    competitor_brands: list[str] = []
    # Rebuild even when a matching report is already stored. The button behind this is "refresh",
    # not the normal path — the fingerprint check below handles the normal path on its own.
    force: bool = False
    chat_session_id: str | None = None


class KeywordClusterOut(BaseModel):
    name: str
    intent: str | None = None
    funnel: str | None = None
    content_type: str | None = None
    recommended_content: str | None = None
    recommended_url: str | None = None
    primary_keyword: str | None = None
    keyword_count: int = 0
    total_volume: int = 0


class KeywordReportResponse(BaseModel):
    run_id: str
    # `skipped` is not a failure: a run whose service is not known yet has nothing to cluster, and
    # the caller carries on without keyword grounding rather than showing an error for a step the
    # operator never asked for.
    status: Literal["built", "reused", "skipped"]
    phase: str
    service: str | None = None
    provider: str | None = None
    version: int | None = None
    total_keywords: int = 0
    clusters: list[KeywordClusterOut] = []
    warnings: list[str] = []
    reason: str | None = None


def _cluster_summaries(report: dict) -> list[KeywordClusterOut]:
    out: list[KeywordClusterOut] = []
    for cluster in report.get("clusters") or []:
        entries = cluster.get("keywords") or []
        out.append(
            KeywordClusterOut(
                name=str(cluster.get("name") or "(unnamed)"),
                intent=cluster.get("intent"),
                funnel=cluster.get("funnel"),
                content_type=cluster.get("content_type"),
                recommended_content=cluster.get("recommended_content"),
                recommended_url=cluster.get("recommended_url"),
                primary_keyword=cluster.get("primary_keyword"),
                keyword_count=len(entries),
                total_volume=sum(int(e.get("volume") or 0) for e in entries),
            )
        )
    return out


async def _stored_keyword_entry(session: AsyncSession, run_uuid: uuid.UUID) -> ContextEntry | None:
    """This run's own latest keyword report, never an inherited one.

    Queried directly rather than through `_latest_context_entry` because the phase-scoping rule
    matters more here than anywhere else: reusing a parent run's report is precisely the bug this
    whole key is fenced off to prevent.
    """
    result = await session.execute(
        select(ContextEntry)
        .where(ContextEntry.run_id == run_uuid, ContextEntry.context_key == KEYWORD_CONTEXT_KEY)
        .order_by(ContextEntry.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/runs/{run_id}/keywords/build", response_model=KeywordReportResponse)
async def build_run_keywords(run_id: str, payload: BuildKeywordsRequest) -> KeywordReportResponse:
    """Build this run's keyword cluster report, or hand back the one already stored.

    Reuse is decided on the config fingerprint, not on mere presence: an operator who corrects the
    region or the service name after ICP was approved has changed what should be clustered, and
    gets a rebuild. Re-entering a stage has changed nothing, and does not.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    config = keywords_service.config_from_profile(
        payload.profile,
        payload.phase,
        competitor_brands=payload.competitor_brands,
    )
    if config is None:
        # Phase 1 without an industry, or Phase 2 before the sub-service has been chosen.
        return KeywordReportResponse(
            run_id=run_id,
            status="skipped",
            phase=payload.phase,
            reason="No service is known for this run yet, so there is nothing to cluster.",
        )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        existing = await _stored_keyword_entry(session, run_uuid)
        if existing is not None and not payload.force:
            stored = existing.value if isinstance(existing.value, dict) else {}
            if stored.get("config_fingerprint") == config.fingerprint():
                report = stored.get("keyword_report") or {}
                logger.info("Reusing keyword report run_id=%s v%s", run_id, existing.version)
                return KeywordReportResponse(
                    run_id=run_id,
                    status="reused",
                    phase=payload.phase,
                    service=stored.get("service"),
                    provider=stored.get("provider"),
                    version=existing.version,
                    total_keywords=int(report.get("total_keywords") or 0),
                    clusters=_cluster_summaries(report),
                    warnings=list(stored.get("warnings") or []),
                )

    async def on_usage(call_usage: usage_service.CallUsage) -> None:
        await usage_service.record(
            call_usage,
            kind="keywords",
            chat_session_id=payload.chat_session_id,
            run_id=run_id,
            phase=payload.phase,
        )

    try:
        result = await keywords_service.build_keyword_report(config, on_usage)
    except keywords_service.KeywordProviderError as exc:
        # The provider refusing is not a run-blocking fault: headline suggestions degrade to
        # framework-only grounding, which is worse but still works. Surfaced as a 502 so the caller
        # can say so, and so a bad `location_name` is visible rather than silently absorbed.
        logger.warning("Keyword provider failed for run_id=%s: %s", run_id, exc)
        raise HTTPException(status_code=502, detail=f"Keyword provider failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — classified into the shared fault shape below
        fault = classify_api_error(exc)
        logger.exception("Keyword clustering failed run_id=%s fault=%s", run_id, fault.code)
        raise HTTPException(status_code=502, detail=fault.as_event()) from exc

    async with session_factory() as session:
        version = await _next_version(session, run_uuid, KEYWORD_CONTEXT_KEY)
        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=KEYWORD_CONTEXT_KEY,
                version=version,
                value=result.to_context_value(),
                # No `asset_definitions` row backs this: it is a prepass, not an asset. The column
                # is nullable precisely for context a stage did not write.
                written_by_asset_id=None,
            )
        )
        await session.commit()

    logger.info(
        "Built keyword report run_id=%s phase=%s service=%r provider=%s clusters=%d v%s",
        run_id,
        payload.phase,
        result.service,
        result.provider,
        len(result.report.get("clusters") or []),
        version,
    )
    return KeywordReportResponse(
        run_id=run_id,
        status="built",
        phase=payload.phase,
        service=result.service,
        provider=result.provider,
        version=version,
        total_keywords=int(result.report.get("total_keywords") or 0),
        clusters=_cluster_summaries(result.report),
        warnings=result.warnings,
    )


@router.get("/runs/{run_id}/keywords", response_model=KeywordReportResponse)
async def get_run_keywords(run_id: str) -> KeywordReportResponse:
    """This run's stored keyword report. 404 when it has none of its own — including a Phase 2 run
    whose parent has one, which is the point of `PHASE_SCOPED_CONTEXT_KEYS`."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        entry = await _stored_keyword_entry(session, run_uuid)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"No keyword report for run {run_id}")

    stored = entry.value if isinstance(entry.value, dict) else {}
    report = stored.get("keyword_report") or {}
    return KeywordReportResponse(
        run_id=run_id,
        status="reused",
        phase=DEFAULT_PHASE,
        service=stored.get("service"),
        provider=stored.get("provider"),
        version=entry.version,
        total_keywords=int(report.get("total_keywords") or 0),
        clusters=_cluster_summaries(report),
        warnings=list(stored.get("warnings") or []),
    )


# --------------------------------------------------------------------------------------
# Headline / topic suggestion
#
# The human gate in front of a stage that has a topic to decide. The operator sees at least ten
# candidates built from this run's own search demand and the headline framework, picks one (or
# several), or writes their own — and the stage is then generated about what they picked.
#
# Not a stage and not saved from here: a suggestion is an *input* to a stage, so the selection
# travels back as that stage's intake answer and is persisted with it. What IS saved from here is
# `selected_headlines`, so every later stage in the same leg stays on the chosen theme.
# --------------------------------------------------------------------------------------

SELECTED_HEADLINES_CONTEXT_KEY = "selected_headlines"

# What each slot's suggestion call reads for competitive context, by the asset it belongs to.
# Stored under the competitor stage's own asset_id (see `save_competitor_stage`).
_COMPETITOR_CONTEXT_PREFIX = "competitor_analysis_"


class SuggestHeadlinesRequest(BaseModel):
    run_id: str | None = None
    phase: str = DEFAULT_PHASE
    profile: dict[str, str] = {}
    # The answers collected for this stage so far. Load-bearing: the anchor comes first from
    # this stage's own service field (a Lead Magnet's "Target Service / Offer"), and without
    # these the resolver can only fall back to run-level facts — which is how every gate ended
    # up anchored on the client's *industry* rather than the service the operator typed.
    answers: dict[str, str] = {}
    count: int = headlines_service.DEFAULT_COUNT
    # Headlines already shown and turned down, so a re-roll is genuinely different.
    exclude: list[str] = []
    operator_note: str = ""
    chat_session_id: str | None = None


class HeadlineCandidateOut(BaseModel):
    id: str
    headline: str
    primary_keyword: str | None = None
    source_cluster: str | None = None
    intent: str | None = None
    funnel: str | None = None
    traffic_temperature: str | None = None
    framework_formula: str | None = None
    curiosity_elements: list[str] = []
    specificity: str | None = None
    why_it_works: str | None = None
    trend_evidence: str | None = None
    char_count: int = 0
    channel_limit_ok: bool = True
    checklist_pass: bool = False
    checklist_notes: str = ""
    search_volume: int | None = None
    difficulty: int | None = None
    grounded: bool = True
    extras: dict = {}


class SuggestHeadlinesResponse(BaseModel):
    slot: str
    asset_id: str
    phase: str
    service_anchor: str
    anchor_source: str
    label: str
    subject: str
    channel: str
    char_budget: str
    multi: bool
    suggested_selection: int
    candidates: list[HeadlineCandidateOut]
    # True when the run had a keyword report to ground these in. False means the candidates are
    # framework-grounded only — still usable, but carrying no demand evidence, and the card says so
    # rather than presenting them as if they did.
    grounded_in_keywords: bool
    web_search_used: bool
    # Why the batch came back short, when it did.
    rejected_count: int = 0


class SaveHeadlineSelectionRequest(BaseModel):
    slot: str
    phase: str = DEFAULT_PHASE
    # The candidates the operator chose, or one they wrote themselves.
    selected: list[dict] = []
    source: Literal["suggested", "operator"] = "suggested"


class SaveHeadlineSelectionResponse(BaseModel):
    run_id: str
    slot: str
    asset_id: str
    version: int
    rendered: str


async def _context_text(session: AsyncSession, run_uuid: uuid.UUID, context_key: str) -> str:
    """One approved document as plain text, or "" when the run has none.

    Uses the inheriting lookup on purpose: the ICP and the competitor listing a Phase 2 run reads
    are its Phase 1 parent's, and that is exactly what should happen — a sub-service sells to the
    same people. Only `PHASE_SCOPED_CONTEXT_KEYS` is fenced off from that.
    """
    found = await _latest_context_entry(session, run_uuid, context_key)
    if found is None:
        return ""
    entry, _run = found
    value = entry.value if isinstance(entry.value, dict) else {}
    return str(value.get("content") or "")


class HeadlineSlotOut(BaseModel):
    slot: str
    asset_id: str
    label: str
    subject: str
    channel: str
    char_budget: str
    multi: bool
    suggested_selection: int


@router.get("/headlines/slots", response_model=list[HeadlineSlotOut])
async def list_headline_slots() -> list[HeadlineSlotOut]:
    """The slot table as it stands now — no model call, no run required.

    Exists because a suggestion card is snapshotted into the chat with the answer it was built
    from, `multi` included. A slot that later becomes multi-select (blog topics did) would leave
    every already-open gate stuck on the old behaviour, restored from the snapshot on every reopen,
    with a restart of both halves changing nothing. The frontend re-reads this on hydration so a
    pending gate follows the current config rather than the config it was born under.

    Declared above `POST /headlines/{slot}` for readability only — the two never collide, the
    methods differ.
    """
    return [
        HeadlineSlotOut(
            slot=cfg.slot,
            asset_id=cfg.asset_id,
            label=cfg.label,
            subject=cfg.subject,
            channel=cfg.channel,
            char_budget=cfg.char_budget,
            multi=cfg.multi,
            suggested_selection=cfg.suggested_selection,
        )
        for cfg in headlines_service.SLOTS.values()
    ]


@router.post("/headlines/{slot}", response_model=SuggestHeadlinesResponse)
async def suggest_headlines_route(slot: str, payload: SuggestHeadlinesRequest) -> SuggestHeadlinesResponse:
    """At least `count` candidate headlines for one slot, grounded in this run's own evidence."""
    if not headlines_service.has_slot(slot):
        raise HTTPException(status_code=404, detail=f"Unknown headline slot: {slot!r}")
    cfg = headlines_service.slot_config(slot)

    # The service THIS stage is for, not the client's industry. See `resolve_service_anchor`.
    service_anchor, anchor_source = headlines_service.resolve_service_anchor(
        cfg.asset_id, payload.answers, payload.profile, payload.phase
    )
    if not service_anchor:
        # Nothing to anchor on. Suggesting headlines against no service is precisely the drift
        # this gate exists to prevent, so it refuses rather than guessing.
        raise HTTPException(
            status_code=409,
            detail=(
                "This run does not know what service it is for yet, so topics cannot be "
                "anchored to anything. Answer the Target Service question, or complete the "
                "ICP stage first."
            ),
        )
    logger.info(
        "Headline anchor slot=%s asset=%s phase=%s anchor=%r via=%s",
        slot,
        cfg.asset_id,
        payload.phase,
        service_anchor,
        anchor_source,
    )

    context = headlines_service.HeadlineContext(
        service_anchor=service_anchor,
        anchor_source=anchor_source,
        phase=payload.phase,
        business_name=(payload.profile.get("client_name") or "").strip(),
        region=(payload.profile.get("region") or "").strip(),
        exclude=list(payload.exclude),
        operator_note=payload.operator_note,
    )

    if payload.run_id:
        try:
            run_uuid = uuid.UUID(payload.run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid run_id: {payload.run_id!r}") from exc

        session_factory = get_sessionmaker()
        async with session_factory() as session:
            # This run's own keyword report — never an inherited one (`_stored_keyword_entry`).
            entry = await _stored_keyword_entry(session, run_uuid)
            if entry is not None and isinstance(entry.value, dict):
                context.keyword_report = entry.value.get("keyword_report") or {}
                context.keyword_service = entry.value.get("service") or ""
                context.clean_keywords = entry.value.get("clean_keywords") or []
                context.vocabulary = entry.value.get("vocabulary") or []

            context.icp_document = await _context_text(session, run_uuid, "icp")
            context.competitor_document = await _context_text(
                session, run_uuid, f"{_COMPETITOR_CONTEXT_PREFIX}{cfg.asset_id}"
            )

    async def on_usage(call_usage: usage_service.CallUsage) -> None:
        await usage_service.record(
            call_usage,
            kind="headlines",
            chat_session_id=payload.chat_session_id,
            run_id=payload.run_id,
            asset_id=cfg.asset_id,
            phase=payload.phase,
        )

    try:
        result = await headlines_service.suggest_headlines(slot, context, payload.count, on_usage)
    except headlines_service.HeadlineParseError as exc:
        logger.warning("Headline suggestion for slot=%s returned unparseable output: %s", slot, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — classified into the shared fault shape
        fault = classify_api_error(exc)
        logger.exception("Headline suggestion failed slot=%s fault=%s", slot, fault.code)
        raise HTTPException(status_code=502, detail=fault.as_event()) from exc

    return SuggestHeadlinesResponse(
        slot=slot,
        asset_id=cfg.asset_id,
        phase=payload.phase,
        service_anchor=result.service_anchor,
        anchor_source=anchor_source,
        label=cfg.label,
        subject=cfg.subject,
        channel=cfg.channel,
        char_budget=cfg.char_budget,
        multi=cfg.multi,
        suggested_selection=cfg.suggested_selection,
        candidates=[
            HeadlineCandidateOut(
                id=c.id,
                headline=c.headline,
                primary_keyword=c.primary_keyword,
                source_cluster=c.source_cluster,
                intent=c.intent,
                funnel=c.funnel,
                traffic_temperature=c.traffic_temperature,
                framework_formula=c.framework_formula,
                curiosity_elements=c.curiosity_elements,
                specificity=c.specificity,
                why_it_works=c.why_it_works,
                trend_evidence=c.trend_evidence,
                char_count=c.char_count,
                channel_limit_ok=c.channel_limit_ok,
                checklist_pass=c.checklist_pass,
                checklist_notes=c.checklist_notes,
                search_volume=c.search_volume,
                difficulty=c.difficulty,
                grounded=c.grounded,
                extras=c.extras,
            )
            for c in result.candidates
        ],
        grounded_in_keywords=result.grounded_in_keywords,
        web_search_used=result.web_search_used,
        rejected_count=len(result.rejected),
    )


@router.post("/runs/{run_id}/headlines/select", response_model=SaveHeadlineSelectionResponse)
async def save_headline_selection(
    run_id: str, payload: SaveHeadlineSelectionRequest
) -> SaveHeadlineSelectionResponse:
    """Record what the operator chose, so later stages in this leg stay on the same theme.

    Written as a versioned `selected_headlines` entry accumulating one slot at a time, because the
    binding is cross-stage: a blog topic chosen at stage 08 should still be visible to the SMS
    sequence at stage 14. Phase-scoped — a Phase 2 leg builds its own from scratch rather than
    inheriting the parent's chosen topics.
    """
    if not headlines_service.has_slot(payload.slot):
        raise HTTPException(status_code=404, detail=f"Unknown headline slot: {payload.slot!r}")
    cfg = headlines_service.slot_config(payload.slot)

    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc

    rendered = headlines_service.render_selection(cfg, payload.selected)
    if not rendered:
        raise HTTPException(status_code=400, detail="No headline was selected.")

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        run = await session.get(Run, run_uuid)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

        # Carry forward what earlier slots in this leg already chose. `context_entries` is
        # append-only, so a new version restates the whole map rather than patching the old row.
        previous = await _latest_context_entry(session, run_uuid, SELECTED_HEADLINES_CONTEXT_KEY)
        selections: dict = {}
        if previous is not None:
            entry, found_on = previous
            # Belt and braces over `PHASE_SCOPED_CONTEXT_KEYS`: never build on a parent run's map.
            if found_on == run_uuid and isinstance(entry.value, dict):
                selections = dict(entry.value.get("selections") or {})

        selections[payload.slot] = {
            "asset_id": cfg.asset_id,
            "phase": payload.phase,
            "source": payload.source,
            "rendered": rendered,
            "selected": payload.selected,
        }

        version = await _next_version(session, run_uuid, SELECTED_HEADLINES_CONTEXT_KEY)
        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=SELECTED_HEADLINES_CONTEXT_KEY,
                version=version,
                value={
                    # `content` is what any prompt consuming this as a document reads.
                    "content": "\n\n".join(
                        f"{headlines_service.slot_config(s).label}:\n{v['rendered']}"
                        for s, v in selections.items()
                        if headlines_service.has_slot(s)
                    ),
                    "selections": selections,
                },
                written_by_asset_id=None,
            )
        )
        await session.commit()

    logger.info(
        "Saved headline selection run_id=%s slot=%s source=%s items=%d v%s",
        run_id,
        payload.slot,
        payload.source,
        len(payload.selected),
        version,
    )
    return SaveHeadlineSelectionResponse(
        run_id=run_id,
        slot=payload.slot,
        asset_id=cfg.asset_id,
        version=version,
        rendered=rendered,
    )


# --------------------------------------------------------------------------------------
# Brand design tokens
#
# Read once per run from the client's own live page, cached as context, and handed to every stage
# that builds publishable HTML (`BRAND_TOKEN_STAGES` in `app/services/generation.py`).
#
# The page it reads is `existing_page_url` — the parent page the CRO stage is rewriting, which is
# exactly the page a generated lead magnet or funnel step has to look like it came from. It falls
# back to the client's website URL, which is the same brand at worse resolution.
#
# Inheritable down the `source_run_id` chain, unlike the keyword and headline keys: a sub-service
# is the same client with the same brand, and re-reading the same CSS per sub-service would spend
# a fetch to arrive at the same palette.
# --------------------------------------------------------------------------------------

DESIGN_TOKENS_CONTEXT_KEY = "brand_design_tokens"

# What the capture *produces*, not what the page contains. A stored sheet is reused for the life of
# a run — a brand does not change between stages and 17 credits per stage would be the largest line
# on a run — but that cache was unconditional, so a run captured before an improvement to
# `design_md.py` kept serving the older document forever and re-running the stage changed nothing.
# That is exactly what happened when the `## Page structure` walk landed.
#
# Bumping this re-captures every run once, on its next stage, and is the only way an improvement to
# the captured document reaches a run that already exists. Bump it whenever `build_design_md` starts
# emitting something a stage needs.
#
#   1 — tokens only (pre-DESIGN.md, `content` alone)
#   2 — DESIGN.md: front matter, colours, type, components, logo
#   3 — adds the `## Page structure` band walk (`page_structure.py`)
DESIGN_CAPTURE_VERSION = 3

# In preference order. The parent page first — a company's brand is most concretely expressed on
# the page actually being extended, and a home page can differ from a service page's template.
#
# `reference_design_source` sits third, and its absence here was a real bug worth naming: it is the
# Pillar Page stage's *own* design reference field ("URL / description of the page whose visual
# design to replicate"), and `pillar_page.json` has neither `existing_page_url` nor
# `parent_pillar_page_url`. So a Pillar Page run read `client_website_url` — the client's home page
# — no matter which page the operator had actually pointed at, and a run with only the reference
# filled in read nothing at all and built in placeholder greys. No stage carries both this field
# and `existing_page_url`, so the ordering between them never arises.
_DESIGN_SOURCE_FIELDS = (
    "existing_page_url",
    "parent_pillar_page_url",
    "reference_design_source",
    "client_website_url",
)
_DESIGN_SOURCE_FACTS = ("website_url",)

# `reference_design_source` is a `file_attach` accepting a file *or* text, so its value is a URL
# only some of the time — it is also where an operator pastes "take the spacing and card treatment,
# not the palette", or attaches a screenshot. A URL is extracted from it rather than assumed, and a
# value with no URL in it is passed over as though the field were blank.
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
_BARE_DOMAIN = re.compile(r"^(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.I)


def _as_url(value: str) -> str | None:
    """A URL out of an intake answer, or None when there is not one in it."""
    value = value.strip()
    if not value:
        return None
    found = _URL_IN_TEXT.search(value)
    if found:
        return found.group(0).rstrip(".,;:")
    # A bare host, which is how people write a URL when a field does not insist on the scheme.
    # Only when it is the *whole* answer: a domain mentioned inside a sentence of art direction is
    # being talked about, not nominated.
    if _BARE_DOMAIN.match(value):
        return f"https://{value}"
    return None


# Which answer fields say what the business actually sells, in the order they are most likely to
# be a real, specific description. Checked against `answers` first (the stage's own intake, e.g.
# `cro`'s `target_service_or_sub_service`) and `profile` second (the run-level facts collected
# across earlier stages — see `GenerateStageRequest.client_profile`).
_SUBJECT_CONTEXT_ANSWER_FIELDS = ("target_service_or_sub_service", "client_industry", "sub_vertical_niche")
_SUBJECT_CONTEXT_PROFILE_FIELDS = ("industry", "sub_service")
# Sentinels that mean "nothing was actually said" — the NEW PAGE mode strings, and the ordinary
# placeholders an operator types into a skipped field. Passing one of these through as the subject
# would brief a generated image on the literal words "NEW PAGE", not on a business.
_SUBJECT_CONTEXT_BLANK = {"", "N/A", "NONE", "UNKNOWN", "TBD"}


def _image_subject_context(answers: dict[str, str], profile: dict[str, str]) -> str:
    """A short, real description of the business this run's images should depict.

    Without this, `image_briefs.build_image_briefs` had only brand colours and typography to work
    from — nothing said what the client's business or service actually is, so a generated hero/proof
    image was photorealistic but of an arbitrary scene: the "random character images" bug. This
    pulls the plainest, most specific answer already on hand rather than asking a new question.
    """
    parts: list[str] = []
    for field_id in _SUBJECT_CONTEXT_ANSWER_FIELDS:
        value = (answers.get(field_id) or "").strip()
        if value and value.upper() not in _SUBJECT_CONTEXT_BLANK and not value.startswith("[[context:"):
            parts.append(value)
    for field_id in _SUBJECT_CONTEXT_PROFILE_FIELDS:
        value = (profile.get(field_id) or "").strip()
        if value and value.upper() not in _SUBJECT_CONTEXT_BLANK:
            parts.append(value)
    # Dedup while keeping order — `client_industry` and `profile["industry"]` are very often the
    # same string, and repeating it in the prompt wastes words without adding information.
    seen = list(dict.fromkeys(parts))
    return " — ".join(seen[:3])


def _design_source_url(answers: dict[str, str], profile: dict[str, str]) -> str | None:
    for field_id in _DESIGN_SOURCE_FIELDS:
        value = (answers.get(field_id) or "").strip()
        if not value or value.startswith("[[context:"):
            continue
        url = _as_url(value)
        if url:
            return url
    for fact in _DESIGN_SOURCE_FACTS:
        url = _as_url(profile.get(fact) or "")
        if url:
            return url
    return None


async def _stored_design_tokens(session: AsyncSession, run_uuid: uuid.UUID) -> tuple[dict, uuid.UUID] | None:
    found = await _latest_context_entry(session, run_uuid, DESIGN_TOKENS_CONTEXT_KEY)
    if found is None:
        return None
    entry, on_run = found
    return (entry.value if isinstance(entry.value, dict) else {}), on_run


def _reuse_stored_design(value: dict, url: str | None) -> tuple[bool, str]:
    """Whether this run's stored design sheet may be served as-is. Returns `(reuse, why not)`.

    Split out of `resolve_page_design` so the rule is testable without a database, because it is the
    rule that decides whether an operator re-running a stage sees any change at all — and it
    previously decided "no" unconditionally.
    """
    if not url:
        # Nothing better is available: there is no URL to re-read. Whatever is stored is served
        # regardless of its age.
        return True, ""
    if value.get("source_url") != url:
        return False, f"the run now points at {url} rather than {value.get('source_url')!r}"
    stored_version = int(value.get("capture_version") or 1)
    if stored_version < DESIGN_CAPTURE_VERSION:
        return False, (
            f"the stored sheet is a v{stored_version} capture and the current format is "
            f"v{DESIGN_CAPTURE_VERSION}"
        )
    return True, ""


def _briefs_from_stored(raw: list | None) -> tuple[ImageBrief, ...]:
    """Rebuild `ImageBrief` specs from a stored context entry.

    An entry written before this shape existed holds bare prompt strings with no size — read
    those with OpenAI's default size rather than discarding them, the same "legacy entries are
    read, not re-captured" rule `_page_design_input` already applies to `content`.
    """
    briefs: list[ImageBrief] = []
    for item in raw or ():
        if isinstance(item, dict) and item.get("prompt"):
            briefs.append(
                ImageBrief(
                    role=str(item.get("role") or ""),
                    size=str(item.get("size") or openai_image_client.DEFAULT_SIZE),
                    prompt=str(item["prompt"]),
                    reference_image_urls=tuple(
                        str(u) for u in (item.get("reference_image_urls") or ()) if u
                    ),
                )
            )
        elif isinstance(item, str) and item:
            briefs.append(ImageBrief(role="", size=openai_image_client.DEFAULT_SIZE, prompt=item))
    return tuple(briefs)


def _generated_images_from_stored(raw: list | None) -> tuple[GeneratedImage, ...]:
    images: list[GeneratedImage] = []
    for item in raw or ():
        if isinstance(item, dict) and item.get("url"):
            images.append(
                GeneratedImage(
                    role=str(item.get("role") or ""),
                    size=str(item.get("size") or ""),
                    prompt=str(item.get("prompt") or ""),
                    url=str(item["url"]),
                )
            )
    return tuple(images)


async def _ensure_generated_images(
    session_factory, run_uuid: uuid.UUID, value: dict, asset_id: str | None
) -> dict:
    """Generate this run's brief images on the first stage that actually needs them, and persist
    the result so the second `PAGE_REPLICA_STAGES` member reuses it for free.

    A no-op — returns `value` unchanged — whenever any of: the stage does not need imagery, the
    run already has generated images stored, there are no briefs to fulfil, or OpenAI has no key
    configured. Each of those is the same "skip cleanly" behaviour `image_briefs.generate_all`
    already has; this just adds the persistence layer around it.
    """
    if asset_id not in PAGE_REPLICA_STAGES or value.get("generated_images"):
        return value
    briefs = _briefs_from_stored(value.get("image_briefs"))
    if not briefs or (not openai_image_client.is_configured() and not runway_image_client.is_configured()):
        return value

    # One session for the whole operation: `generate_all` stages each successfully-generated
    # image as a `MediaAsset` row on it (see that function's docstring for why saving happens
    # sequentially, after the concurrent OpenAI calls, rather than inside them), and the
    # `generated_images` context entry commits on the same session right after — the images and
    # the pointer to them land together in one transaction, or neither does.
    async with session_factory() as session:
        if await session.get(Run, run_uuid) is None:
            return value

        generated, notes = await image_briefs_service.generate_all(briefs, session)
        if not generated:
            # Nothing to persist — leave the run to try again on its next stage rather than
            # writing an empty marker that would be indistinguishable from "already tried and got
            # nothing".
            if notes:
                logger.info("Image generation produced nothing for run_id=%s: %s", run_uuid, "; ".join(notes))
            return value

        updated = {
            **value,
            "generated_images": [
                {"role": g.role, "size": g.size, "prompt": g.prompt, "url": g.url} for g in generated
            ],
        }
        version = await _next_version(session, run_uuid, DESIGN_TOKENS_CONTEXT_KEY)
        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=DESIGN_TOKENS_CONTEXT_KEY,
                version=version,
                value=updated,
                written_by_asset_id=None,
            )
        )
        await session.commit()

    logger.info(
        "Generated %s/%s brief image(s) for run_id=%s: %s",
        len(generated),
        len(briefs),
        run_uuid,
        ", ".join(g.role for g in generated),
    )
    return updated


def _page_design_input(value: dict) -> PageDesignInput | None:
    """Rebuild the generation input from a stored context entry.

    Entries written before DESIGN.md existed hold only `content` (the old token sheet). They are
    read as a theme brief rather than discarded: the sheet is still real measurements of the right
    page, and re-capturing every historical run to gain a front-matter block would spend 17 credits
    per run to restate what is already there.
    """
    design_md = (value.get("design_md") or "").strip()
    theme_brief = (value.get("theme_brief") or "").strip()
    legacy = (value.get("content") or "").strip()
    if not (design_md or theme_brief or legacy):
        return None
    return PageDesignInput(
        design_md=design_md or legacy,
        theme_brief=theme_brief or legacy,
        screenshot_urls=tuple(value.get("model_screenshot_urls") or ()),
        generated_images=_generated_images_from_stored(value.get("generated_images")),
    )


async def resolve_page_design(
    run_id: str | None,
    answers: dict[str, str],
    profile: dict[str, str],
    *,
    asset_id: str | None = None,
) -> PageDesignInput | None:
    """This run's DESIGN.md and reference screenshots, capturing them first if it has none.

    Returns None when there is no page to read or every reader failed. That is deliberately not an
    error: `build_prompt` simply omits the block, and the stage's own prompt falls back to asking
    for brand values. Never returns a partial or invented sheet — a generated page in a plausible
    but wrong palette is indistinguishable from a correct one until someone who knows the brand
    looks at it, which is the whole failure mode this exists to prevent.

    Capture costs 17 Context.dev credits, so it happens once per run and is read from the run's
    context on every stage after the first.

    `asset_id` is the stage actually asking. Passed only by the per-stage generation flow — a
    preview or readiness check has no reason to trigger an OpenAI spend — and used only to decide
    whether this is a `PAGE_REPLICA_STAGES` member that still needs its brief images generated;
    see `_ensure_generated_images`.
    """
    url = _design_source_url(answers, profile)
    session_factory = get_sessionmaker()

    run_uuid: uuid.UUID | None = None
    if run_id:
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError:
            run_uuid = None

    if run_uuid is not None:
        async with session_factory() as session:
            stored = await _stored_design_tokens(session, run_uuid)
        if stored is not None:
            value, _on_run = stored
            reuse, why = _reuse_stored_design(value, url)
            if reuse:
                value = await _ensure_generated_images(session_factory, run_uuid, value, asset_id)
                return _page_design_input(value)
            logger.info("Re-capturing page design for run_id=%s: %s", run_id, why)

    if not url:
        return None

    design = await design_md_service.capture_page_design(
        url, subject_context=_image_subject_context(answers, profile)
    )

    stored_value: dict | None = None
    if run_uuid is not None:
        # Stored either way. An unavailable sheet is a real finding worth keeping: it stops every
        # later stage in the run from re-fetching a page that is known to refuse readers, and it
        # tells the operator why their HTML came out in placeholder greys.
        stored_value = {
            # `content` is kept as an alias of the full sheet so anything still reading the
            # pre-DESIGN.md shape keeps working.
            "content": design.design_md,
            "design_md": design.design_md,
            "theme_brief": design.theme_brief,
            "source_url": url,
            "capture_version": DESIGN_CAPTURE_VERSION,
            "available": design.available,
            "reason": design.reason,
            "sources": list(design.sources),
            "notes": design.notes,
            "screenshots": [
                {
                    "url": shot.image_url,
                    "label": shot.label,
                    "kind": shot.kind,
                    "width": shot.width,
                    "height": shot.height,
                    "model_safe": shot.model_safe,
                }
                for shot in design.screenshots
            ],
            # Pre-filtered, so the prompt builder never has to re-derive which images the model
            # API will accept.
            "model_screenshot_urls": [s.image_url for s in design.model_screenshots],
            "image_briefs": [
                {
                    "role": b.role,
                    "size": b.size,
                    "prompt": b.prompt,
                    "reference_image_urls": list(b.reference_image_urls),
                }
                for b in design.image_briefs
            ],
        }
        async with session_factory() as session:
            if await session.get(Run, run_uuid) is not None:
                version = await _next_version(session, run_uuid, DESIGN_TOKENS_CONTEXT_KEY)
                session.add(
                    ContextEntry(
                        run_id=run_uuid,
                        context_key=DESIGN_TOKENS_CONTEXT_KEY,
                        version=version,
                        value=stored_value,
                        written_by_asset_id=None,
                    )
                )
                await session.commit()
                logger.info(
                    "Stored page design run_id=%s url=%s available=%s sources=%s shots=%s v%s",
                    run_id,
                    url,
                    design.available,
                    ",".join(design.sources) or "none",
                    len(design.screenshots),
                    version,
                )

    if not design.available:
        # Stored above either way — an unreadable page is a finding worth keeping, because it stops
        # every later stage re-paying for a page known to refuse readers. But the stages get the
        # stated-unavailable sheet, not nothing, so they build in flagged greys instead of guessing.
        # No page to read means no briefs either, so there is nothing for images to be generated
        # from — `_ensure_generated_images` is skipped rather than called on an empty list.
        return PageDesignInput(
            design_md=design.design_md, theme_brief=design.theme_brief
        )

    generated_images: tuple[GeneratedImage, ...] = ()
    if run_uuid is not None and stored_value is not None:
        stored_value = await _ensure_generated_images(session_factory, run_uuid, stored_value, asset_id)
        generated_images = _generated_images_from_stored(stored_value.get("generated_images"))

    return PageDesignInput(
        design_md=design.design_md,
        theme_brief=design.theme_brief,
        screenshot_urls=tuple(s.image_url for s in design.model_screenshots),
        generated_images=generated_images,
    )


class ScreenshotOut(BaseModel):
    url: str
    label: str = ""
    kind: str = ""
    width: int | None = None
    height: int | None = None
    #: False when the image is past what the model API accepts, so it is shown to the operator but
    #: never sent to the model. The UI says so rather than leaving a silent gap.
    model_safe: bool = True


class PageDesignResponse(BaseModel):
    source_url: str
    available: bool
    reason: str | None = None
    #: The full DESIGN.md — YAML token front matter plus rationale. What the CRO rewrite is built
    #: against, and what the operator downloads.
    design_md: str = ""
    #: Colours, type, shapes and logo only. What the lead-magnet stages are built against.
    theme_brief: str = ""
    screenshots: list[ScreenshotOut] = []
    #: Which readers answered: "styleguide", "fonts", "css parse", "screenshots".
    sources: list[str] = []
    notes: list[str] = []
    image_briefs: list[dict[str, str]] = []
    #: Real, already-hosted URLs — empty until a `PAGE_REPLICA_STAGES` stage has actually
    #: run for this run (this route never generates them; see `POST /pipeline/design/images`).
    generated_images: list[dict[str, str]] = []


@router.get("/runs/{run_id}/design", response_model=PageDesignResponse)
async def read_run_page_design(run_id: str) -> PageDesignResponse:
    """The DESIGN.md captured for this run, as stored — no capture, no credits.

    Separate from `POST /pipeline/design` on purpose: this one is free and safe for the UI to poll
    or for an operator to open twice, because the expensive read already happened on the run's first
    HTML stage.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{run_id!r} is not a run id.") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stored = await _stored_design_tokens(session, run_uuid)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="No design has been captured for this run yet. It is read on the first stage "
            "that builds HTML.",
        )

    value, _on_run = stored
    return PageDesignResponse(
        source_url=value.get("source_url") or "",
        available=bool(value.get("available")),
        reason=value.get("reason"),
        design_md=value.get("design_md") or value.get("content") or "",
        theme_brief=value.get("theme_brief") or "",
        screenshots=[ScreenshotOut(**shot) for shot in (value.get("screenshots") or [])],
        sources=list(value.get("sources") or []),
        notes=list(value.get("notes") or []),
        image_briefs=[
            {"role": b.role, "size": b.size, "prompt": b.prompt}
            for b in _briefs_from_stored(value.get("image_briefs"))
        ],
        generated_images=[
            {"role": g.role, "size": g.size, "url": g.url}
            for g in _generated_images_from_stored(value.get("generated_images"))
        ],
    )


class PageDesignRequest(BaseModel):
    url: str
    #: Skip the two screenshot calls. For a preview where the operator only wants to see the tokens.
    with_screenshots: bool = True


@router.post("/design", response_model=PageDesignResponse)
async def capture_page_design_route(payload: PageDesignRequest) -> PageDesignResponse:
    """Capture one page's DESIGN.md and reference screenshots. **17 Context.dev credits**
    (10 styleguide + 5 fonts + 2 screenshots), or 15 with `with_screenshots=false`.

    Unattached to a run, so nothing is stored: this is the "show me what you'd read off this URL"
    button, not the path a generation takes. A stage capture goes through `resolve_page_design`,
    which caches on the run.
    """
    design = await design_md_service.capture_page_design(
        payload.url, with_screenshots=payload.with_screenshots
    )
    return PageDesignResponse(
        source_url=design.source_url,
        available=design.available,
        reason=design.reason,
        design_md=design.design_md,
        theme_brief=design.theme_brief,
        screenshots=[
            ScreenshotOut(
                url=shot.image_url,
                label=shot.label,
                kind=shot.kind,
                width=shot.width,
                height=shot.height,
                model_safe=shot.model_safe,
            )
            for shot in design.screenshots
        ],
        sources=list(design.sources),
        notes=list(design.notes),
        image_briefs=[
            {"role": brief.role, "size": brief.size, "prompt": brief.prompt}
            for brief in design.image_briefs
        ],
    )


class SocialStatusResponse(BaseModel):
    configured: bool
    detail: str = ""


@router.get("/social/status", response_model=SocialStatusResponse)
async def social_status() -> SocialStatusResponse:
    """Whether SociaVault is available. No API call, so it costs nothing — the UI uses this to
    decide whether to offer the "fetch live posts" button on Stage 10 at all."""
    if sociavault_client.is_configured():
        return SocialStatusResponse(configured=True)
    return SocialStatusResponse(
        configured=False,
        detail=f"{sociavault_client.API_KEY_ENV} is not set on the backend, so live post data is off.",
    )


class SocialPostsRequest(BaseModel):
    #: What this account is called in the rendered document — "Client" or a competitor's name.
    label: str = Field(min_length=1)
    #: Free text in the same shape as the `client_s_own_social_pages_handles` field itself, e.g.
    #: "Facebook: /trafficradius, Instagram: @trafficradius, LinkedIn: /company/trafficradius".
    handles_text: str = Field(min_length=1)
    limit_per_platform: int = Field(default=40, ge=1, le=100)


class SocialAccountOut(BaseModel):
    platform: str
    handle: str
    sample_size: int
    more_available: bool
    credits_used: int


class SocialPostsResponse(BaseModel):
    label: str
    #: Ready to paste straight into `raw_post_data_source` as text.
    markdown: str
    accounts: list[SocialAccountOut]
    notes: list[str]


@router.post("/social/posts", response_model=SocialPostsResponse)
async def fetch_social_posts(payload: SocialPostsRequest) -> SocialPostsResponse:
    """Fetch one account's real, live posts across Facebook/Instagram/LinkedIn via SociaVault, and
    render them into the document Stage 10's `raw_post_data_source` field asks for.

    Unattached to a run and stores nothing — like `/design`, this is a preview button the operator
    calls once per account (the client, then each competitor being audited) and pastes the result
    into that field, rather than typing "research live via browser" and letting the model guess at
    engagement numbers it cannot actually measure. A platform in `handles_text` this module has no
    endpoint for (YouTube, TikTok, ...) is silently skipped and reported in `notes`, not an error —
    naming an unsupported platform is a normal thing for the field to do.
    """
    accounts = social_audit_service.parse_account_handles(payload.handles_text)
    if not accounts:
        raise HTTPException(
            status_code=422,
            detail=(
                "No recognisable Facebook/Instagram/LinkedIn handle found in handles_text. Expected "
                "the same format as the intake field, e.g. 'Instagram: @trafficradius'."
            ),
        )

    try:
        results, notes = await social_audit_service.fetch_account_snapshot(
            accounts, limit_per_platform=payload.limit_per_platform
        )
    except sociavault_client.SociaVaultNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    markdown = social_audit_service.render_social_data_md(payload.label, results, notes)
    return SocialPostsResponse(
        label=payload.label,
        markdown=markdown,
        accounts=[
            SocialAccountOut(
                platform=r.platform,
                handle=r.handle,
                sample_size=len(r.posts),
                more_available=r.more_available,
                credits_used=r.credits_used,
            )
            for r in results
        ],
        notes=list(notes),
    )


class GeneratedImageRequest(BaseModel):
    prompt: str
    size: str = openai_image_client.DEFAULT_SIZE
    quality: str = openai_image_client.DEFAULT_QUALITY


class GeneratedImageResponse(BaseModel):
    model: str
    url: str


@router.post("/design/images", response_model=GeneratedImageResponse)
async def generate_design_image(payload: GeneratedImageRequest) -> GeneratedImageResponse:
    """Generate one image from a DESIGN.md brief, with OpenAI as primary and Runway as fallback.

    Ad hoc — for an operator previewing a brief before it is wired into a run. The per-stage flow
    (`resolve_page_design` -> `_ensure_generated_images`) is what generates and caches a run's
    real hero/proof/closing-cta set; this route does not touch that cache.
    """
    result = None
    provider = openai_image_client.MODEL_ENV  # used in response label

    # --- primary: OpenAI ---
    if openai_image_client.is_configured():
        try:
            result = await openai_image_client.generate_image(
                payload.prompt,
                size=payload.size,
                quality=payload.quality,
            )
            provider = os.environ.get(openai_image_client.MODEL_ENV, openai_image_client.DEFAULT_MODEL)
        except openai_image_client.OpenAIImageInvalidRequest as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except openai_image_client.OpenAIImageError as exc:
            logger.warning("OpenAI image failed in /design/images — trying Runway: %s", exc)

    # --- fallback: Runway ---
    if result is None and runway_image_client.is_configured():
        try:
            result = await runway_image_client.generate_image(
                payload.prompt,
                size=payload.size,
            )
            provider = runway_image_client.DEFAULT_MODEL
        except runway_image_client.RunwayImageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No image provider is configured. Set OPENAI_API_KEY or RUNWAYML_API_SECRET.",
        )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        url = media_storage.save_generated_image(session, result.data, output_format=result.output_format)
        await session.commit()
    return GeneratedImageResponse(model=provider, url=url)


# --------------------------------------------------------------------------------------------
# Slide decks — the webinar stage's Step 5 brief as a file somebody can open
# --------------------------------------------------------------------------------------------


class SlideDeckSummary(BaseModel):
    index: int
    title: str
    slide_count: int
    filename: str


class SlideDecksResponse(BaseModel):
    decks: list[SlideDeckSummary]
    #: Filename the "download everything" button would produce. A zip once there is more than one.
    bundle_filename: str


# --------------------------------------------------------------------------------------
# Checking a draft against the client's business — see `app/services/asset_check.py`
#
# Advisory: nothing here blocks approval, and nothing is stored. The report lives on the draft card,
# like a slide deck is built from the text in the request rather than read off the run.
# --------------------------------------------------------------------------------------


class AssetCheckRequest(BaseModel):
    text: NonBlankStr
    run_id: str | None = None
    phase: str = DEFAULT_PHASE
    client_profile: dict[str, str] = Field(default_factory=dict)
    chat_session_id: str | None = None


class CheckFindingOut(BaseModel):
    check: str
    severity: str
    quote: str
    why: str
    fix: str = ""
    source: str = "rule"


class CheckResultOut(BaseModel):
    check: str
    score: int | None = None
    verdict: str = ""
    findings: list[CheckFindingOut] = []


class AssetCheckResponse(BaseModel):
    asset_id: str
    checks: list[CheckResultOut]
    facts_used: list[str]
    facts_missing: list[str]
    judge: str
    dropped_quotes: int = 0
    virality_basis: str = ""
    duration_ms: int = 0


async def _business_facts(run_id: str | None, client_profile: dict[str, str], asset_id: str):
    """The run's facts, read through the same inheritance every other document uses. A run that
    cannot be read yields empty facts — reported as missing, never as a failed check."""
    run_uuid: uuid.UUID | None = None
    if run_id:
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError:
            run_uuid = None
    if run_uuid is None:
        async def nothing(_key: str) -> None:
            return None

        return await business_facts_service.load_business_facts(nothing, client_profile, asset_id=asset_id)

    session_factory = get_sessionmaker()
    async with session_factory() as session:

        async def lookup(key: str) -> object | None:
            found = await _latest_context_entry(session, run_uuid, key)
            return found[0].value if found is not None else None

        return await business_facts_service.load_business_facts(lookup, client_profile, asset_id=asset_id)


@router.post("/check/{asset_id}", response_model=AssetCheckResponse)
async def check_asset_route(asset_id: str, payload: AssetCheckRequest) -> AssetCheckResponse:
    """Check a draft against the client's offers, funnel, business rules and — for content —
    predicted virality. Deterministic rules plus one low-effort model call; works on an unsaved
    draft. A model failure returns the rule findings alone (`judge: "unavailable"`)."""
    if not has_stage(asset_id, payload.phase):
        raise HTTPException(status_code=404, detail=f"Unknown asset_id {asset_id!r} for phase {payload.phase!r}")

    try:
        facts = await _business_facts(payload.run_id, payload.client_profile, asset_id)
    except Exception as exc:  # noqa: BLE001 — facts that cannot be read are missing facts
        logger.warning("Asset check could not read facts for run %s: %s", payload.run_id, exc)
        facts = business_facts_service.BusinessFacts(missing=["everything on the run (it could not be read)"])

    report = await asset_check_service.check_asset(
        asset_id,
        payload.text,
        facts,
        on_usage=usage_service.recorder(
            kind="check",
            chat_session_id=payload.chat_session_id,
            run_id=payload.run_id,
            asset_id=asset_id,
            phase=payload.phase,
        ),
    )
    return AssetCheckResponse.model_validate(report.as_dict())


class SlideDeckRequest(BaseModel):
    #: The generated webinar document. Sent by the client rather than read from the run, so the
    #: button works on an unsaved draft exactly as `Download` does — an operator who wants the deck
    #: is as likely to want it before approving the stage as after.
    text: str
    #: Optional. Only used to look up the run's captured palette, so a deck built from a draft on a
    #: run with no design yet is plain rather than refused.
    run_id: str | None = None


def _decks_or_422(text: str) -> list[slide_deck_service.SlideDeck]:
    decks = slide_deck_service.find_slide_decks(text)
    if not decks:
        raise HTTPException(
            status_code=422,
            detail="No SLIDE DECK BRIEF section was found in this document. The webinar prompt "
            "writes one at Step 5 — re-run the stage if the brief is missing.",
        )
    return decks


async def _deck_brand(run_id: str | None) -> slide_deck_service.DeckBrand:
    """The run's palette, or neutral greys.

    Never an error: a webinar run is in `BRAND_THEME_STAGES`, so it may legitimately have no
    captured design at all, and refusing the download over a missing palette would withhold the
    deliverable to protect a colour.
    """
    if not run_id:
        return slide_deck_service.DeckBrand()
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        return slide_deck_service.DeckBrand()

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stored = await _stored_design_tokens(session, run_uuid)
    if stored is None:
        return slide_deck_service.DeckBrand()

    value, _on_run = stored
    return slide_deck_service.brand_from_design_md(
        value.get("design_md") or value.get("theme_brief") or value.get("content") or ""
    )


@router.post("/slides", response_model=SlideDecksResponse)
async def list_slide_decks(payload: SlideDeckRequest) -> SlideDecksResponse:
    """What decks this webinar document contains. Free, and builds nothing.

    The UI asks this before offering the button, so a webinar whose Step 5 never ran shows no
    download rather than a button that 422s when pressed.
    """
    decks = _decks_or_422(payload.text)
    summaries = [
        SlideDeckSummary(
            index=index,
            title=deck.title,
            slide_count=len(deck.slides),
            filename=f"{slide_deck_service.slug(deck.title)}-slides.pptx",
        )
        for index, deck in enumerate(decks)
    ]
    bundle = (
        summaries[0].filename if len(summaries) == 1 else "webinar-slide-decks.zip"
    )
    return SlideDecksResponse(decks=summaries, bundle_filename=bundle)


@router.post("/slides/pptx")
async def download_slide_deck(payload: SlideDeckRequest, index: int | None = None) -> Response:
    """The deck itself. A `.pptx`, or a `.zip` when the document holds several and none was named.

    Returns the bytes rather than a URL: nothing is stored, so there is no file to point at later,
    and a deck is cheap enough to rebuild that caching it would only add a thing to invalidate.
    """
    decks = _decks_or_422(payload.text)
    brand = await _deck_brand(payload.run_id)

    if index is not None:
        if not 0 <= index < len(decks):
            raise HTTPException(
                status_code=422,
                detail=f"This document has {len(decks)} slide deck brief(s); there is no deck {index}.",
            )
        deck = decks[index]
        return Response(
            content=slide_deck_service.build_pptx(deck, brand),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{slide_deck_service.slug(deck.title)}-slides.pptx"'
                )
            },
        )

    if len(decks) == 1:
        deck = decks[0]
        return Response(
            content=slide_deck_service.build_pptx(deck, brand),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{slide_deck_service.slug(deck.title)}-slides.pptx"'
                )
            },
        )

    bundle = slide_deck_service.build_zip(
        [
            (f"{slide_deck_service.slug(deck.title)}-slides.pptx", slide_deck_service.build_pptx(deck, brand))
            for deck in decks
        ]
    )
    return Response(
        content=bundle,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="webinar-slide-decks.zip"'},
    )


class DesignTokensRequest(BaseModel):
    url: str
    # Page source the operator pasted, for a site that refuses server-side reads. Some hosts answer
    # a server with a JS captcha while serving browsers normally (SiteGround's `sgcaptcha` does),
    # and no header or cookie gets a server past it — but the operator's own browser already has
    # the page. `url` is still required alongside it: relative stylesheet and logo references have
    # nothing to resolve against without the page's own address.
    html: str | None = None


class DesignTokensResponse(BaseModel):
    source_url: str
    available: bool
    reason: str | None = None
    page_background: str | None = None
    body_text: str | None = None
    accent: str | None = None
    heading_font: str | None = None
    palette: list[str] = []
    font_links: list[str] = []
    markdown: str
    css: str


@router.post("/design-tokens", response_model=DesignTokensResponse)
async def read_design_tokens(payload: DesignTokensRequest) -> DesignTokensResponse:
    """Read one page's design system. Used by the UI to show what was picked up before a build."""
    if payload.html and payload.html.strip():
        tokens = await design_tokens_service.extract_design_tokens_from_html(payload.html, payload.url)
    else:
        tokens = await design_tokens_service.extract_design_tokens(payload.url)
    return DesignTokensResponse(
        source_url=tokens.source_url,
        available=tokens.available,
        reason=tokens.reason,
        page_background=tokens.page_background,
        body_text=tokens.body_text,
        accent=tokens.accent,
        heading_font=tokens.heading_font,
        palette=[c.hex for c in tokens.palette[:12]],
        font_links=tokens.font_links,
        markdown=design_tokens_service.tokens_to_markdown(tokens),
        css=design_tokens_service.tokens_to_css(tokens),
    )


# --------------------------------------------------------------------------------------
# The page replica
#
# `design_md.py` gives a stage the client's palette and, since the structure walk, the client's
# section order. This is the step past both: the client's actual page, with only its words
# replaced. See `app/services/page_replica.py` for why that has to be a different mechanism rather
# than a stronger instruction.
#
# The template is captured once per run and cached here exactly as the design sheet is, and for the
# same reason — it is a property of the page, not of the stage reading it. It is *not* inheritable
# down the `source_run_id` chain, which is the one place this key differs from
# `brand_design_tokens`: a brand is shared between a parent page and its sub-services, but a
# template is one specific document, and a sub-service page assembled from its parent's template
# would be a copy of the parent page.
# --------------------------------------------------------------------------------------

PAGE_TEMPLATE_CONTEXT_KEY = "page_replica_template"

#: See `DESIGN_CAPTURE_VERSION` for the argument. Bump when the slot rules change — a stored
#: template's slot ids are identifiers, so a run holding ids from older rules must re-capture rather
#: than have them reinterpreted.
REPLICA_CAPTURE_VERSION = 1


def _registrable_host(url: str) -> str:
    """The host, minus `www.`, lowercased. Enough to answer "is this the client's own site?" —
    deliberately not a public-suffix parse, which would need a dependency and a suffix list to tell
    `example.co.uk` from `example.co`."""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _client_hosts(answers: dict[str, str], profile: dict[str, str]) -> set[str]:
    candidates = [answers.get("client_website_url") or "", profile.get("website_url") or ""]
    return {host for host in (_registrable_host(value) for value in candidates if value.strip()) if host}


def _replica_refusal(url: str, answers: dict[str, str], profile: dict[str, str]) -> str | None:
    """Why this URL must not be templated, or None when it may be.

    Matching a page's palette and reproducing a page's markup are different acts. The first is
    fine against any reference — a client is allowed to admire somebody's design. The second
    produces a near-copy of the document, and that is only appropriate when the document is the
    client's own.

    `existing_page_url` and `parent_pillar_page_url` are normally the client's own pages, but
    "normally" is not a guarantee: pasting a competitor's URL into the CRO stage's Existing Page
    field is an ordinary thing to do while researching, and it must not quietly yield a clone of
    that competitor's site.

    When there is no client domain on record there is nothing to compare against, and this allows
    the capture rather than refusing. Refusing would block the common case — a run where
    `existing_page_url` is the only URL anybody filled in — on the basis of an absent field rather
    than a detected problem.
    """
    target = _registrable_host(url)
    if not target:
        return f"{url!r} has no host to check against the client's own domain."

    hosts = _client_hosts(answers, profile)
    if not hosts:
        return None
    if any(target == host or target.endswith(f".{host}") or host.endswith(f".{target}") for host in hosts):
        return None
    return (
        f"{target} is not the client's own domain ({', '.join(sorted(hosts))}). Its palette, type "
        "and section order are still used as a reference, but its markup is not copied — "
        "reproducing another company's page is a different thing from matching its design."
    )


async def _stored_page_template(session: AsyncSession, run_uuid: uuid.UUID) -> tuple[dict, uuid.UUID] | None:
    found = await _latest_context_entry(session, run_uuid, PAGE_TEMPLATE_CONTEXT_KEY)
    if found is None:
        return None
    entry, on_run = found
    return (entry.value if isinstance(entry.value, dict) else {}), on_run


def _template_from_stored(value: dict) -> page_replica_service.PageTemplate | None:
    """Rebuild a `PageTemplate` from its stored row.

    The slots are rehydrated rather than re-derived. Re-walking the stored template would produce
    the same ids from the same document *today*, and would silently produce different ones the
    moment the slot rules change — writing the copy meant for one line into another, on every run
    captured before the change. The ids are stored because they are identifiers, not derivations.
    """
    if not value:
        return None
    slots = tuple(
        page_replica_service.Slot(
            slot_id=row["slot_id"],
            path=tuple(row["path"]),
            role=row.get("role", "text"),
            original=row.get("original", ""),
            max_chars=int(row.get("max_chars", 0)),
            band_index=int(row.get("band_index", 0)),
            band_role=row.get("band_role", ""),
            holder=row.get("holder", ""),
            locked=bool(row.get("locked")),
            lock_reason=row.get("lock_reason", ""),
        )
        for row in (value.get("slots") or [])
    )
    return page_replica_service.PageTemplate(
        source_url=value.get("source_url") or "",
        available=bool(value.get("available")),
        reason=value.get("reason"),
        template_html=value.get("template_html") or "",
        slots=slots,
        source=value.get("source") or "direct",
        notes=list(value.get("notes") or []),
    )


def _template_to_stored(template: page_replica_service.PageTemplate) -> dict:
    return {
        "source_url": template.source_url,
        "available": template.available,
        "reason": template.reason,
        "template_html": template.template_html,
        "source": template.source,
        "notes": template.notes,
        "capture_version": REPLICA_CAPTURE_VERSION,
        "band_count": template.structure.band_count if template.structure is not None else 0,
        "slots": [
            {
                "slot_id": slot.slot_id,
                "path": list(slot.path),
                "role": slot.role,
                "original": slot.original,
                "max_chars": slot.max_chars,
                "band_index": slot.band_index,
                "band_role": slot.band_role,
                "holder": slot.holder,
                "locked": slot.locked,
                "lock_reason": slot.lock_reason,
            }
            for slot in template.slots
        ],
    }


async def resolve_page_template(
    run_id: str | None,
    answers: dict[str, str],
    profile: dict[str, str],
) -> page_replica_service.PageTemplate | None:
    """This run's page template, capturing it once if it has none.

    Returns None when there is no URL to read. That is not an error: the replica is an *addition*
    to a replica stage's deliverable, and a stage without a template produces exactly what it
    produced before this existed — its own document, built against DESIGN.md and the structure walk.

    Capture costs 0 credits when the free fetch answers and 1 when the page has to be rendered, so
    it happens once per run and is read from context on every stage after the first.
    """
    url = _design_source_url(answers, profile)
    if not url:
        return None

    session_factory = get_sessionmaker()
    run_uuid: uuid.UUID | None = None
    if run_id:
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError:
            run_uuid = None

    if run_uuid is not None:
        async with session_factory() as session:
            stored = await _stored_page_template(session, run_uuid)
        if stored is not None:
            value, _on_run = stored
            # Re-capture when the operator has pointed the run at a different page, or when the
            # stored template predates the current slot rules; reuse otherwise.
            if (
                value.get("source_url") == url
                and int(value.get("capture_version") or 0) >= REPLICA_CAPTURE_VERSION
            ):
                return _template_from_stored(value)

    refusal = _replica_refusal(url, answers, profile)
    if refusal:
        logger.info("Page replica declined for run_id=%s url=%s: %s", run_id, url, refusal)
        template = page_replica_service.PageTemplate(source_url=url, available=False, reason=refusal)
    else:
        template = await page_replica_service.capture_page_template(url)

    if run_uuid is not None:
        # Stored either way, for the reason the design sheet is: a page known to refuse readers,
        # or a reference known to be somebody else's, is a finding worth keeping rather than a
        # question every later stage in the run re-asks.
        async with session_factory() as session:
            if await session.get(Run, run_uuid) is not None:
                version = await _next_version(session, run_uuid, PAGE_TEMPLATE_CONTEXT_KEY)
                session.add(
                    ContextEntry(
                        run_id=run_uuid,
                        context_key=PAGE_TEMPLATE_CONTEXT_KEY,
                        version=version,
                        value=_template_to_stored(template),
                        written_by_asset_id=None,
                    )
                )
                await session.commit()
                logger.info(
                    "Stored page template run_id=%s url=%s available=%s slots=%s bytes=%s v%s",
                    run_id,
                    url,
                    template.available,
                    len(template.slots),
                    template.byte_size,
                    version,
                )
    return template


class SlotOut(BaseModel):
    slot_id: str
    role: str
    original: str
    max_chars: int
    band_index: int = 0
    band_role: str = ""
    holder: str = ""
    locked: bool = False
    lock_reason: str = ""


class PageTemplateResponse(BaseModel):
    source_url: str
    available: bool
    reason: str | None = None
    #: How the HTML was read: "direct" (free) or "context.dev" (1 credit).
    source: str = "direct"
    band_count: int = 0
    slot_count: int = 0
    open_slot_count: int = 0
    locked_slot_count: int = 0
    template_bytes: int = 0
    #: The deck exactly as the assembly pass will see it, so an operator can read what the model is
    #: being asked before spending a generation on it.
    copy_deck: str = ""
    #: Every slot, locked ones included. The locked list is the point of the preview: it is where an
    #: operator checks that the phone number, the nav and the brand mark really are being held.
    slots: list[SlotOut] = []
    notes: list[str] = []


def _template_response(template: page_replica_service.PageTemplate) -> PageTemplateResponse:
    return PageTemplateResponse(
        source_url=template.source_url,
        available=template.available,
        reason=template.reason,
        source=template.source,
        band_count=template.structure.band_count if template.structure is not None else 0,
        slot_count=len(template.slots),
        open_slot_count=len(template.open_slots),
        locked_slot_count=len(template.locked_slots),
        template_bytes=template.byte_size,
        copy_deck=page_replica_service.copy_deck_markdown(template),
        slots=[
            SlotOut(
                slot_id=slot.slot_id,
                role=slot.role,
                original=slot.original,
                max_chars=slot.max_chars,
                band_index=slot.band_index,
                band_role=slot.band_role,
                holder=slot.holder,
                locked=slot.locked,
                lock_reason=slot.lock_reason,
            )
            for slot in template.slots
        ],
        notes=list(template.notes),
    )


class PageTemplateRequest(BaseModel):
    url: str


@router.post("/replica", response_model=PageTemplateResponse)
async def capture_page_template_route(payload: PageTemplateRequest) -> PageTemplateResponse:
    """Capture one page as a fillable template. **0 credits** normally, 1 if it has to be rendered.

    Unattached to a run, so nothing is stored, and no domain check is applied: this is the "show me
    the slots you would fill on this page" button, and looking at a page is not copying it. The
    domain check belongs on `resolve_page_template`, which is the path that produces a document.
    """
    template = await page_replica_service.capture_page_template(payload.url)
    return _template_response(template)


@router.get("/runs/{run_id}/replica", response_model=PageTemplateResponse)
async def read_run_page_template(run_id: str) -> PageTemplateResponse:
    """The template captured for this run, as stored. Free, and safe to poll."""
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{run_id!r} is not a run id.") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stored = await _stored_page_template(session, run_uuid)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="No page template has been captured for this run yet. It is read on the first "
            "replica stage (CRO or Pillar Page).",
        )

    value, _on_run = stored
    template = _template_from_stored(value)
    if template is None:
        raise HTTPException(status_code=404, detail="This run's stored page template is empty.")

    response = _template_response(template)
    # From the row rather than re-walked: the stored template carries no `PageStructure`, and
    # re-extracting one to fill in a single integer would parse a 300KB document per poll.
    response.band_count = int(value.get("band_count") or 0)
    return response


#: Where an assembled page is kept. A deliverable, not a cache: it is the artifact an operator
#: hands over, and re-assembling writes a new version rather than replacing this one, like every
#: other row in this table.
PAGE_REPLICA_OUTPUT_CONTEXT_KEY = "page_replica_output"


async def _assemble_replica(
    run_uuid: uuid.UUID,
    asset_id: str,
    template: page_replica_service.PageTemplate,
    draft: str,
    *,
    chat_session_id: str | None = None,
    allow_structural_changes: bool = False,
) -> page_replica_service.ReplicaResult:
    """Run the assembly pass, store the assembled page, and return the report.

    One helper for both callers — the stage stream, which assembles automatically when a replica
    stage finishes, and the explicit re-assemble route an operator uses after editing the draft.
    They must not drift: an operator comparing the two would be comparing this pipeline against
    itself.
    """
    deck = page_replica_service.copy_deck_markdown(
        template, allow_structural_changes=allow_structural_changes
    )
    reply = await generate_replica_copy(
        deck,
        draft,
        on_usage=usage_service.recorder(
            kind="replica_assembly",
            chat_session_id=chat_session_id,
            run_id=str(run_uuid),
            asset_id=asset_id,
        ),
    )
    # The ops are read only when the channel was opened. Parsing them unconditionally would let a
    # model that volunteered a `structure` array reshape a page nobody asked it to reshape.
    changes = page_replica_service.parse_structure_ops(reply) if allow_structural_changes else ()
    result = page_replica_service.apply_copy(
        template, page_replica_service.parse_copy_map(reply), changes
    )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        if await session.get(Run, run_uuid) is not None:
            version = await _next_version(session, run_uuid, PAGE_REPLICA_OUTPUT_CONTEXT_KEY)
            session.add(
                ContextEntry(
                    run_id=run_uuid,
                    context_key=PAGE_REPLICA_OUTPUT_CONTEXT_KEY,
                    version=version,
                    value={
                        "asset_id": asset_id,
                        "source_url": template.source_url,
                        "html": result.html,
                        "filled": result.filled,
                        "kept": result.kept,
                        "intact": result.intact,
                        "rejected": [list(row) for row in result.rejected],
                        "warnings": [list(row) for row in result.warnings],
                        "checks": [[name, passed, detail] for name, passed, detail in result.checks],
                        "structural": [list(row) for row in result.structural],
                    },
                    written_by_asset_id=asset_id,
                )
            )
            await session.commit()
            logger.info(
                "Stored assembled replica run_id=%s asset_id=%s filled=%d/%d intact=%s v%s",
                run_uuid,
                asset_id,
                result.filled,
                result.total,
                result.intact,
                version,
            )
    return result


class AssembleReplicaRequest(BaseModel):
    asset_id: str = "cro"
    #: The stage document to take the copy from. Defaults to the stage's latest stored output, so
    #: the ordinary call is `{}`.
    draft: str | None = None
    chat_session_id: str | None = None
    #: Let the assembly request a shape change — hide a band, trim a grid, reorder bands. Off by
    #: default, and the default is the product decision: the replica exists so the generated page is
    #: indistinguishable from the client's own. This is the operator saying "I have read the CRO
    #: audit and I want its structural recommendations applied too", which is a different request.
    allow_structural_changes: bool = False


class ReplicaCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class AssembledReplicaResponse(BaseModel):
    source_url: str
    #: The assembled page: the client's markup, the generated words.
    html: str
    filled: int
    kept: int
    fill_rate: float
    #: True only when every structural check passed. The gate worth showing in the UI.
    intact: bool
    #: `[slot_id, why]` for copy that was refused, and for copy applied but worth a second look.
    rejected: list[list[str]] = []
    warnings: list[list[str]] = []
    checks: list[ReplicaCheckOut] = []
    #: `[what was done, the reason given]` for each applied shape change. Empty on the default
    #: path. Shown to the operator verbatim — an unjustifiable change should be visible as one.
    structural: list[list[str]] = []


@router.post("/runs/{run_id}/replica/assemble", response_model=AssembledReplicaResponse)
async def assemble_run_replica(run_id: str, payload: AssembleReplicaRequest) -> AssembledReplicaResponse:
    """Run the assembly pass for this run and return the client's page with the new copy in it.

    Separate from stage generation on purpose. The stage's own document is the deliverable its
    master prompt defines and is untouched by any of this; the replica is a second artifact built
    *from* it, and an operator who has just edited the draft wants it re-assembled without paying
    for the whole stage again.
    """
    if payload.asset_id not in PAGE_REPLICA_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"{payload.asset_id!r} does not build a page, so there is nothing to assemble. "
            f"The replica applies to: {', '.join(sorted(PAGE_REPLICA_STAGES))}.",
        )
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{run_id!r} is not a run id.") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        stored = await _stored_page_template(session, run_uuid)
        draft = payload.draft or ""
        if not draft.strip():
            # The stage's own output, which lives in the context store under the asset id.
            found = await _latest_context_entry(session, run_uuid, payload.asset_id)
            if found is not None:
                entry, _on_run = found
                draft = (entry.value or {}).get("content") or ""

    if stored is None:
        raise HTTPException(
            status_code=404,
            detail="No page template has been captured for this run, so there is nothing to "
            "assemble into.",
        )
    if not draft.strip():
        raise HTTPException(
            status_code=422,
            detail=f"The {payload.asset_id} stage has no output to take copy from yet. Run it "
            "first, or pass a draft.",
        )

    value, _on_run = stored
    template = _template_from_stored(value)
    if template is None or not template.available:
        reason = (template.reason if template is not None else None) or "no template was captured"
        raise HTTPException(status_code=422, detail=f"This run's page template is unavailable: {reason}")

    result = await _assemble_replica(
        run_uuid,
        payload.asset_id,
        template,
        draft,
        chat_session_id=payload.chat_session_id,
        allow_structural_changes=payload.allow_structural_changes,
    )

    return AssembledReplicaResponse(
        source_url=template.source_url,
        html=result.html,
        filled=result.filled,
        kept=result.kept,
        fill_rate=round(result.fill_rate, 3),
        intact=result.intact,
        rejected=[[slot_id, why] for slot_id, why in result.rejected],
        warnings=[[slot_id, why] for slot_id, why in result.warnings],
        checks=[
            ReplicaCheckOut(name=name, passed=passed, detail=detail)
            for name, passed, detail in result.checks
        ],
        structural=[[what, why] for what, why in result.structural],
    )


@router.get("/runs/{run_id}/replica/output", response_model=AssembledReplicaResponse)
async def read_run_replica_output(run_id: str) -> AssembledReplicaResponse:
    """The most recently assembled page for this run. Free — the generation already happened.

    Separate from `POST .../assemble` for the same reason `GET .../design` is separate from
    `POST /design`: one of them spends a call and one of them does not, and the UI needs to be able
    to show the artifact without deciding to pay for it.
    """
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{run_id!r} is not a run id.") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        found = await _latest_context_entry(session, run_uuid, PAGE_REPLICA_OUTPUT_CONTEXT_KEY)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail="No page has been assembled for this run yet. It is built when a replica stage "
            "(CRO or Pillar Page) finishes.",
        )

    value = found[0].value or {}
    filled = int(value.get("filled") or 0)
    kept = int(value.get("kept") or 0)
    total = filled + kept
    return AssembledReplicaResponse(
        source_url=value.get("source_url") or "",
        html=value.get("html") or "",
        filled=filled,
        kept=kept,
        fill_rate=round(filled / total, 3) if total else 0.0,
        intact=bool(value.get("intact")),
        rejected=[list(row) for row in (value.get("rejected") or [])],
        warnings=[list(row) for row in (value.get("warnings") or [])],
        checks=[
            ReplicaCheckOut(name=row[0], passed=bool(row[1]), detail=row[2] if len(row) > 2 else "")
            for row in (value.get("checks") or [])
        ],
        structural=[list(row) for row in (value.get("structural") or [])],
    )


class SourceRunAsset(BaseModel):
    context_key: str
    version: int
    chars: int


class SourceRunSummary(BaseModel):
    """One run a new Phase 2 run could be started against."""

    run_id: str
    client_id: str
    company_name: str
    created_at: datetime
    updated_at: datetime
    # The chat this run was built in, when there is one, so the picker can show the operator the same
    # title they see in their history sidebar rather than a UUID.
    chat_title: str | None = None
    assets: list[SourceRunAsset] = []


@router.get("/source-runs", response_model=list[SourceRunSummary])
async def list_source_runs(
    user: Annotated[User, Depends(current_user)],
) -> list[SourceRunSummary]:
    """Runs that a Phase 2 sub-service run can inherit context from, newest first.

    A run qualifies when it is a root run (nothing above it), has at least one approved asset, and
    **belongs to the caller**. There is nothing to inherit from an empty one, and offering it would
    only invite the operator to pick a run that answers no question. Each row carries the keys it
    actually holds, so the picker can say what Phase 2 would get rather than just naming a run.

    Ownership is read through `chat_sessions.run_id` rather than off the run itself, because `runs`
    has no owner column: a run is created by a chat, and that chat has one. The inner join is what
    scopes the list — a run no chat of this user's claims is not selected at all. Before this, the
    picker named every run in the installation, so one operator chose from another's client list by
    company name and could then inherit their ICP and page copy into a new run.

    The consequence worth knowing: a run whose chat was deleted, or one seeded directly through
    `POST /pipeline/runs`, has no chat pointing at it and so appears for nobody. That is the same
    fail-closed default as an unowned chat session — recoverable by reattaching the chat, where
    listing it for everyone would not be.
    """
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        result = await session.execute(
            select(Run, Client.company_name)
            .join(Client, Client.id == Run.client_id)
            .join(ChatSession, ChatSession.run_id == Run.id)
            .where(Run.source_run_id.is_(None), ChatSession.user_id == user.id)
            .order_by(Run.updated_at.desc())
        )
        rows = result.all()
        if not rows:
            return []

        run_ids = [run.id for run, _ in rows]

        entries = await session.execute(
            select(ContextEntry)
            .where(ContextEntry.run_id.in_(run_ids))
            .order_by(ContextEntry.run_id, ContextEntry.context_key, ContextEntry.version.desc())
        )
        # Latest version per (run, key) — the ordering above puts it first within each group.
        latest: dict[uuid.UUID, dict[str, ContextEntry]] = {}
        for entry in entries.scalars().all():
            latest.setdefault(entry.run_id, {}).setdefault(entry.context_key, entry)

        titles = await session.execute(
            select(ChatSession.run_id, ChatSession.title).where(ChatSession.run_id.in_(run_ids))
        )
        title_by_run = {run_id: title for run_id, title in titles.all() if run_id is not None}

        summaries: list[SourceRunSummary] = []
        for run, company_name in rows:
            keys = latest.get(run.id, {})
            if not keys:
                continue
            summaries.append(
                SourceRunSummary(
                    run_id=str(run.id),
                    client_id=str(run.client_id),
                    company_name=company_name,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    chat_title=title_by_run.get(run.id),
                    assets=[
                        SourceRunAsset(
                            context_key=key,
                            version=entry.version,
                            chars=len((entry.value or {}).get("content") or "")
                            if isinstance(entry.value, dict)
                            else 0,
                        )
                        for key, entry in sorted(keys.items())
                    ],
                )
            )
        return summaries


class CompetitorBriefingRequest(BaseModel):
    """The approved competitor listing to read, plus what the run is for."""

    competitor_output: NonBlankStr
    sub_service: str = ""
    # Which chat and run this call is spent by, so `api_usage` can attribute it. Both optional and
    # both free-form: a brand-new chat makes its first call before its session row exists, and a run
    # only exists once a stage is approved — usage from before either is still recorded, just
    # unattributed, rather than being dropped for want of a foreign key.
    chat_session_id: str | None = None
    run_id: str | None = None


class CompetitorBriefingResponse(BaseModel):
    asset_id: str
    summary: str


@router.post("/competitor-briefing/{asset_id}", response_model=CompetitorBriefingResponse)
async def competitor_briefing(asset_id: str, payload: CompetitorBriefingRequest) -> CompetitorBriefingResponse:
    """Summarise an approved competitor listing for the operator, before the stage's own intake.

    Only the two stages whose next questions depend on having read the market have one — see
    app/services/insights.py.
    """
    if not insights.has_briefing(asset_id):
        raise HTTPException(status_code=404, detail=f"No competitor briefing defined for {asset_id!r}")

    try:
        summary = await insights.summarize_competitors(
            asset_id,
            payload.competitor_output,
            payload.sub_service,
            on_usage=usage_service.recorder(
                kind="briefing",
                chat_session_id=payload.chat_session_id,
                run_id=payload.run_id,
                asset_id=asset_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - classified and returned, same as every other call
        fault = classify_api_error(exc)
        logger.exception("Competitor briefing failed asset_id=%r fault=%s", asset_id, fault.code)
        raise HTTPException(status_code=502, detail=fault.as_event()) from exc

    return CompetitorBriefingResponse(asset_id=asset_id, summary=summary)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


class GenerateStageRequest(BaseModel):
    answers: dict[str, str] = {}
    # Which pipeline this stage is being generated for. Selects the prompt file and the INPUTS block
    # (see `CONFIGS_BY_PHASE` in app/services/generation.py); a Phase-2 stage run as Phase 1 would
    # silently produce the headline-service asset instead of the sub-service one.
    phase: str = DEFAULT_PHASE
    # Which chat and run this call is spent by, so `api_usage` can attribute it. Both optional and
    # both free-form: a brand-new chat makes its first call before its session row exists, and a run
    # only exists once a stage is approved — usage from before either is still recorded, just
    # unattributed, rather than being dropped for want of a foreign key.
    chat_session_id: str | None = None
    run_id: str | None = None
    # Run-level client facts (website_url / industry / region) accumulated by the UI across
    # earlier stages. Needed because blog / webinar / podcast collect only a topic, yet their
    # competitor prepass still needs a target URL to benchmark against.
    client_profile: dict[str, str] = {}


class RefineStageRequest(BaseModel):
    previous_draft: NonBlankStr
    note: NonBlankStr
    phase: str = DEFAULT_PHASE
    # Which chat and run this call is spent by, so `api_usage` can attribute it. Both optional and
    # both free-form: a brand-new chat makes its first call before its session row exists, and a run
    # only exists once a stage is approved — usage from before either is still recorded, just
    # unattributed, rather than being dropped for want of a foreign key.
    chat_session_id: str | None = None
    run_id: str | None = None


# Answers that mean "the operator did not supply a competitor analysis", so the prepass should run.
# `N/A` is the one that matters: the UI writes it verbatim when an optional question is skipped
# (`skipField` in `pipeline/pipelineStore.ts`), and skipping is the normal way through a competitor
# field the pipeline is expected to fill itself. Read as a real answer it silently cancels the
# prepass and hands the master prompt the literal string "N/A" as its competitor benchmark.
_NO_COMPETITOR_INPUT = {"", "N/A", "NONE", "UNKNOWN", "SKIP", "SKIPPED", "NOT SPECIFIED"}


async def _run_competitor_prepass(
    asset_id: str,
    answers: dict[str, str],
    client_profile: dict[str, str],
    phase: str = DEFAULT_PHASE,
    on_usage=None,
) -> tuple[dict[str, str], dict | None]:
    """If `asset_id` has a paired competitor-analysis stage, run it and fold its output into
    `answers` under the field the main schema reads it from.

    Returns `(answers, prepass_event)` — `prepass_event` is None when this stage has no paired
    competitor stage, or when the operator already supplied that input themselves.

    A prepass failure is deliberately non-fatal: the main asset is still worth generating from
    the ICP and the operator's own intake, so the failure is reported to the UI as a `prepass`
    event carrying an `error` and the main prompt is told the benchmark is unavailable — rather
    than being handed a fabricated competitor list.
    """
    cfg = PREPASS_BY_MAIN_ASSET_BY_PHASE.get(phase, {}).get(asset_id)
    if cfg is None:
        return answers, None

    existing = (answers.get(cfg.target_field_id) or "").strip()
    if existing.upper() in _NO_COMPETITOR_INPUT:
        existing = ""
    if existing and not existing.startswith("[[context:"):
        logger.info(
            "Skipping competitor prepass for stage=%r — %s already supplied (%s chars)",
            asset_id,
            cfg.target_field_id,
            len(existing),
        )
        return answers, None

    inputs = resolve_inputs(cfg, answers, client_profile)
    resolved = dict(answers)

    if not inputs.get("target_url"):
        logger.warning(
            "Competitor prepass for stage=%r skipped: no target URL resolvable from this stage's "
            "answers or the run's client profile",
            asset_id,
        )
        resolved[cfg.target_field_id] = (
            "(No competitor analysis available — no client website URL has been captured in this "
            "run yet. Do not invent competitors; proceed using the ICP and the inputs above, and "
            "state explicitly wherever a competitor benchmark would have informed the output.)"
        )
        return resolved, {
            "type": "prepass",
            "asset_id": cfg.asset_id,
            "target_field_id": cfg.target_field_id,
            "skipped": True,
            "error": "No client website URL captured yet in this run.",
        }

    try:
        content = await generate_competitor_analysis(cfg.asset_id, inputs, phase=phase, on_usage=on_usage)
    except Exception as exc:  # noqa: BLE001 - degraded, not fatal; see docstring
        logger.exception("Competitor prepass failed for stage=%r", asset_id)
        resolved[cfg.target_field_id] = (
            "(Competitor analysis could not be produced for this run. Do not invent competitors; "
            "proceed using the ICP and the inputs above, and state explicitly wherever a "
            "competitor benchmark would have informed the output.)"
        )
        return resolved, {
            "type": "prepass",
            "asset_id": cfg.asset_id,
            "target_field_id": cfg.target_field_id,
            "skipped": True,
            "error": str(exc),
        }

    resolved[cfg.target_field_id] = content
    return resolved, {
        "type": "prepass",
        "asset_id": cfg.asset_id,
        "target_field_id": cfg.target_field_id,
        "skipped": False,
        "content": content,
        "inputs": inputs,
    }


# Stage 10. Its `raw_post_data_source` field is the one `_run_social_data_prepass` fills — see that
# function's docstring for why "research live via browser" cannot actually be executed by the model
# and needs a real fetch instead.
_SOCIAL_AUDIT_ASSET_ID = "social_content_strategy_audit"
_SOCIAL_POST_DATA_FIELD_ID = "raw_post_data_source"
_SOCIAL_HANDLES_FIELD_ID = "client_s_own_social_pages_handles"
_SOCIAL_CLIENT_NAME_FIELD_ID = "client_name"
_SOCIAL_COMPETITOR_LIST_FIELD_ID = "competitor_list"
_SOCIAL_COMPETITOR_COUNT_FIELD_ID = "number_of_competitors_to_audit"
_SOCIAL_COMPETITOR_ANALYSIS_ASSET_ID = "competitor_analysis_social_content_strategy"

# Hard cap on how many competitors this prepass resolves and fetches *automatically*, independent
# of what the operator typed into `number_of_competitors_to_audit`. Each one costs a Firecrawl (or
# Context.dev, at 10 credits) handle-resolution call plus a SociaVault fetch per platform resolved —
# credits this prepass spends without being asked to, on every stage run, so the bound is fixed
# rather than following an operator's dial that this call was never shown. `POST /pipeline/social/
# posts` is still there for auditing a 6th, 7th, ... competitor by hand.
_MAX_AUTO_COMPETITORS = 5
# Lower than the client's own default (`sociavault_client`'s 40): this cost is now multiplied by up
# to `_MAX_AUTO_COMPETITORS` accounts, each on up to three platforms, on every stage run.
_COMPETITOR_LIMIT_PER_PLATFORM = 15

# What the field's own `raw_explanation` calls option (b) — matched case-insensitively against
# whatever the operator actually typed, e.g. "research live via browser, 40-60 per platform". A
# blank answer is treated the same way: the field is required, so an operator who left it blank in
# the UI and is relying on the model to sort it out meant this option too.
_SOCIAL_LIVE_RESEARCH_PATTERN = re.compile(r"research\s+live", re.IGNORECASE)


async def _fetch_competitor_social_blocks(
    competitor_list_raw: str, requested_count: str
) -> tuple[str, tuple[str, ...], bool, list[dict]]:
    """Resolve up to `_MAX_AUTO_COMPETITORS` competitors from `competitor_list` to real social
    handles (Firecrawl first, Context.dev as fallback — see `social_audit.resolve_competitor_handles`)
    and fetch their live posts. Returns `(markdown, notes, any_data)`; never raises — a competitor
    whose list cannot be parsed, or whose site links to no social profile, is a stated finding, not
    a failure that should cost the client's own already-fetched data.

    `any_data` is `True` only when at least one competitor's social accounts were actually resolved
    — the caller uses it to decide whether this prepass produced anything real at all, since the
    markdown itself is never empty (a "not fetched, here's why" sentence is still text).

    The fourth element is `social_audit.engagement_stats` for every account fetched — the same posts,
    counted rather than rendered, for the asset check's virality benchmark.
    """
    if not competitor_list_raw or competitor_list_raw.startswith("[[context:"):
        return (
            "Not fetched — no competitor list is available on this run yet.",
            ("Competitor list is empty or unresolved; competitor rows are N/D.",),
            False,
            [],
        )

    try:
        # Not `parse_analysis`: that reads the search model's raw JSON, and this field never holds
        # it — an approved listing reaches it as `to_prompt_text`'s prose, and a hand-answered one as
        # whatever the operator typed. Reading only JSON here left every competitor N/D.
        analysis = parse_competitor_listing(_SOCIAL_COMPETITOR_ANALYSIS_ASSET_ID, competitor_list_raw)
    except CompetitorParseError as exc:
        logger.info("Social data prepass: competitor list could not be parsed for domains: %s", exc)
        return (
            "Not fetched — the competitor list on this run could not be parsed for domains "
            f"({exc}). Competitor rows are N/D per this brief's own rule.",
            (f"Competitor list parse failed: {exc}",),
            False,
            [],
        )

    try:
        requested = max(1, int(requested_count))
    except (TypeError, ValueError):
        requested = _MAX_AUTO_COMPETITORS
    take = min(requested, _MAX_AUTO_COMPETITORS, len(analysis.competitors))
    competitors = analysis.competitors[:take]

    if not competitors:
        return "Not fetched — the competitor list has no entries.", (), False, []

    notes: list[str] = []
    if requested > take:
        notes.append(
            f"{requested} competitors were requested; {take} were fetched automatically to bound "
            f"API spend (cap: {_MAX_AUTO_COMPETITORS}). Use POST /pipeline/social/posts for the rest."
        )

    async def _one(competitor) -> tuple[str, bool, list[dict]]:
        accounts = await social_audit_service.resolve_competitor_handles(competitor.domain)
        if not accounts:
            return (
                f"## {competitor.name} — live social data\n\nNo Facebook/Instagram/LinkedIn "
                f"profile could be resolved from `{competitor.domain}` via Firecrawl or "
                "Context.dev. Mark this competitor's post-level metrics N/D.\n",
                False,
                [],
            )
        results, account_notes = await social_audit_service.fetch_account_snapshot(
            accounts, limit_per_platform=_COMPETITOR_LIMIT_PER_PLATFORM
        )
        return (
            social_audit_service.render_social_data_md(competitor.name, results, account_notes),
            True,
            social_audit_service.engagement_stats(f"{competitor.name} (competitor)", results),
        )

    outcomes = await asyncio.gather(*(_one(c) for c in competitors), return_exceptions=True)

    rendered: list[str] = []
    stats: list[dict] = []
    any_data = False
    for competitor, outcome in zip(competitors, outcomes):
        if isinstance(outcome, Exception):
            logger.info("Social data prepass: competitor %r failed: %s", competitor.name, outcome)
            notes.append(f"{competitor.name}: {outcome}")
            rendered.append(
                f"## {competitor.name} — live social data\n\nCould not be fetched ({outcome}). "
                "Mark this competitor's post-level metrics N/D.\n"
            )
            continue
        block, resolved_any, account_stats = outcome
        rendered.append(block)
        stats.extend(account_stats)
        any_data = any_data or resolved_any

    return "\n".join(rendered), tuple(notes), any_data, stats


async def _run_social_data_prepass(
    asset_id: str, answers: dict[str, str]
) -> tuple[dict[str, str], dict | None]:
    """Fetch real, live social posts — the client's own via `client_s_own_social_pages_handles`,
    and up to `_MAX_AUTO_COMPETITORS` competitors' via their resolved domains — and fill
    `raw_post_data_source` with them, so the stage never has to fall back to "research live via
    browser": an option the model has no actual way to execute (no browsing tool reaches this
    backend's generation calls), which is why the field kept coming back full of `N/D` rows even
    when the operator had supplied real client handles.

    Competitor coverage is best-effort, not guaranteed: `competitor_list` is company names and
    domains, and `social_audit.resolve_competitor_handles` still has to find that domain's actual
    social links before SociaVault can be asked for anything — a competitor with no discoverable
    social presence, or an unparseable competitor list, legitimately stays N/D, and the document
    says so explicitly rather than silently omitting the section.

    Only runs for `social_content_strategy_audit`, and only when the operator has not already
    supplied a real answer (an attached CSV/XLSX, or pasted post data) — the same "don't overwrite
    what's already there" rule `_run_competitor_prepass` follows for its own target field.
    """
    if asset_id != _SOCIAL_AUDIT_ASSET_ID:
        return answers, None

    existing = (answers.get(_SOCIAL_POST_DATA_FIELD_ID) or "").strip()
    if existing and not _SOCIAL_LIVE_RESEARCH_PATTERN.search(existing) and not existing.startswith("[[context:"):
        logger.info(
            "Skipping social data prepass — %s already supplied (%s chars)",
            _SOCIAL_POST_DATA_FIELD_ID,
            len(existing),
        )
        return answers, None

    handles_text = (answers.get(_SOCIAL_HANDLES_FIELD_ID) or "").strip()
    accounts = social_audit_service.parse_account_handles(handles_text)
    resolved = dict(answers)
    all_notes: list[str] = []

    client_results: tuple = ()
    if accounts:
        try:
            client_results, client_notes = await social_audit_service.fetch_account_snapshot(accounts)
            all_notes.extend(client_notes)
        except Exception as exc:  # noqa: BLE001 - degraded, not fatal; see `_run_competitor_prepass`
            logger.exception("Social data prepass failed for client handles=%r", handles_text)
            all_notes.append(f"Client fetch failed: {exc}")
    else:
        all_notes.append(
            f"No Facebook/Instagram/LinkedIn handle recognised in {_SOCIAL_HANDLES_FIELD_ID!r} for "
            "the client."
        )

    label = (answers.get(_SOCIAL_CLIENT_NAME_FIELD_ID) or "Client").strip() or "Client"
    client_block = social_audit_service.render_social_data_md(f"{label} (client)", client_results, ())

    competitor_block, competitor_notes, competitor_any_data, competitor_stats = await _fetch_competitor_social_blocks(
        (answers.get(_SOCIAL_COMPETITOR_LIST_FIELD_ID) or "").strip(),
        answers.get(_SOCIAL_COMPETITOR_COUNT_FIELD_ID) or "",
    )
    all_notes.extend(competitor_notes)

    if not client_results and not competitor_any_data:
        return answers, {
            "type": "social_prepass",
            "asset_id": asset_id,
            "target_field_id": _SOCIAL_POST_DATA_FIELD_ID,
            "skipped": True,
            "error": "; ".join(all_notes) or "No live data could be fetched for any account.",
        }

    document = client_block + "\n## Competitor accounts\n\n" + competitor_block + "\n"
    if all_notes:
        document += "\n### Collection notes\n\n" + "\n".join(f"- {note}" for note in all_notes) + "\n"
    resolved[_SOCIAL_POST_DATA_FIELD_ID] = document

    return resolved, {
        "type": "social_prepass",
        "asset_id": asset_id,
        "target_field_id": _SOCIAL_POST_DATA_FIELD_ID,
        "skipped": False,
        "content": document,
        "accounts": [
            {"platform": r.platform, "handle": r.handle, "sample_size": len(r.posts), "credits_used": r.credits_used}
            for r in client_results
        ],
        # Taken off the event and stored by `_generation_sse_stream` as `social_post_sample`, which
        # has the run id this function does not. Never sent to the browser.
        "stats": social_audit_service.engagement_stats(f"{label} (client)", client_results) + competitor_stats,
    }


#: The engagement stats of the last fetched social sample — the asset check's virality benchmark.
SOCIAL_SAMPLE_CONTEXT_KEY = "social_post_sample"


async def _store_social_sample(run_id: str, stats: list[dict]) -> None:
    """File the posts the social prepass already paid for, counted, so the asset check can compare a
    draft's hook against the hooks that actually travelled for this client and its competitors.
    Costs no credits. Never raises: a benchmark is not worth failing Stage 10 over."""
    try:
        run_uuid = uuid.UUID(run_id)
        session_factory = get_sessionmaker()
        async with session_factory() as session:
            version = await _next_version(session, run_uuid, SOCIAL_SAMPLE_CONTEXT_KEY)
            lines = [
                f"- {s.get('account')} · {s.get('platform')}: {s.get('sample_size')} posts, median engagement "
                f"{s.get('median_engagement') if s.get('median_engagement') is not None else 'not published'}"
                for s in stats
            ]
            session.add(
                ContextEntry(
                    run_id=run_uuid,
                    context_key=SOCIAL_SAMPLE_CONTEXT_KEY,
                    version=version,
                    value={"content": "# Social engagement sample\n\n" + "\n".join(lines) + "\n", "stats": stats},
                    written_by_asset_id=None,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store the social sample for run %s: %s", run_id, exc)


async def _generation_sse_stream(
    asset_id: str,
    answers: dict[str, str],
    client_profile: dict[str, str],
    phase: str = DEFAULT_PHASE,
    chat_session_id: str | None = None,
    run_id: str | None = None,
):
    try:
        prepasses = PREPASS_BY_MAIN_ASSET_BY_PHASE.get(phase, {})
        if asset_id in prepasses:
            yield _sse({"type": "prepass_start", "asset_id": prepasses[asset_id].asset_id})
        # The prepass is a second billed call inside this one request, on a different asset_id and
        # with a web-search fee of its own, so it records separately rather than being folded into
        # the stage's row — otherwise the panel shows a stage that mysteriously cost double.
        answers, prepass_event = await _run_competitor_prepass(
            asset_id,
            answers,
            client_profile,
            phase,
            on_usage=usage_service.recorder(
                kind="competitor",
                chat_session_id=chat_session_id,
                run_id=run_id,
                asset_id=prepasses[asset_id].asset_id if asset_id in prepasses else None,
                phase=phase,
            ),
        )
        if prepass_event is not None:
            yield _sse(prepass_event)

        # Stage 10 only. Fetches the client's real, live posts via SociaVault so the field never
        # has to fall back to a browsing option this model has no way to actually execute — see
        # `_run_social_data_prepass`'s docstring for why the competitor rows stay N/D.
        answers, social_prepass_event = await _run_social_data_prepass(asset_id, answers)
        if social_prepass_event is not None:
            social_stats = social_prepass_event.pop("stats", None)
            if social_stats and run_id:
                await _store_social_sample(run_id, social_stats)
            yield _sse(social_prepass_event)

        # Captured once per run and cached; None when there is no page to read. `build_prompt`
        # simply omits the block then, and the stage's own prompt falls back to asking for brand
        # values rather than inventing a palette. Which view of it a stage sees — the full DESIGN.md
        # or the theme brief — and whether it gets the screenshots is decided in `generation.py`.
        page_design = await resolve_page_design(run_id, answers, client_profile, asset_id=asset_id)

        # The client's confirmed industry, if one is on the run (or its source run). None keeps the
        # default voice, which is the prompt exactly as it was before voice packs existed.
        voice = await _run_industry(run_id)

        # Only the stages that rebuild a page, and only when there is a page to rebuild. Captured
        # before the generation rather than after it so the operator learns *now* whether the
        # replica is going to happen — a template that turns out to be unavailable is worth knowing
        # about while the stage is still running, not after it has finished.
        page_template = None
        if asset_id in PAGE_REPLICA_STAGES:
            page_template = await resolve_page_template(run_id, answers, client_profile)
            if page_template is not None:
                yield _sse(
                    {
                        "type": "replica_template",
                        "available": page_template.available,
                        "reason": page_template.reason,
                        "source_url": page_template.source_url,
                        "open_slots": len(page_template.open_slots),
                        "locked_slots": len(page_template.locked_slots),
                        "notes": page_template.notes,
                    }
                )

        assembling = page_template is not None and page_template.available and run_id
        chunks: list[str] = []

        async for delta in generate_stage_stream(
            asset_id,
            answers,
            phase,
            on_usage=usage_service.recorder(
                kind="generation",
                chat_session_id=chat_session_id,
                run_id=run_id,
                asset_id=asset_id,
                phase=phase,
            ),
            page_design=page_design,
            voice=voice,
        ):
            if assembling:
                chunks.append(delta)
            yield _sse({"type": "delta", "text": delta})

        if assembling and chunks:
            # A second billed call, so it announces itself. Wrapped separately from the stage's own
            # handler below: the stage document is already streamed and complete at this point, and
            # a failure to build the *extra* artifact must not be reported as the stage failing.
            yield _sse({"type": "replica_start"})
            try:
                assert page_template is not None
                result = await _assemble_replica(
                    uuid.UUID(run_id),
                    asset_id,
                    page_template,
                    "".join(chunks),
                    chat_session_id=chat_session_id,
                )
            except Exception as exc:  # noqa: BLE001 — the stage succeeded; only the extra failed
                fault = classify_api_error(exc)
                logger.exception("Replica assembly failed stage=%r fault=%s", asset_id, fault.code)
                yield _sse({"type": "replica_error", "message": fault.message, "code": fault.code})
            else:
                # The HTML itself is not put on the wire. It is 100-400KB, it is already stored, and
                # `GET /runs/{run_id}/replica/output` serves it — a single SSE frame carrying a
                # whole document is a good way to stall a UI mid-stream for no gain.
                yield _sse(
                    {
                        "type": "replica",
                        "asset_id": asset_id,
                        "source_url": page_template.source_url,
                        "filled": result.filled,
                        "kept": result.kept,
                        "intact": result.intact,
                        "rejected": [list(row) for row in result.rejected],
                        "warnings": [list(row) for row in result.warnings],
                        "checks": [[name, passed, detail] for name, passed, detail in result.checks],
                        "structural": [list(row) for row in result.structural],
                    }
                )
    except Exception as exc:  # noqa: BLE001 - every failure is classified and streamed, never swallowed
        # One handler, because what the operator needs is the same either way: a classified fault
        # rather than a raw SDK string. `classify` never raises, so an unrecognised error still
        # arrives as something the UI can render.
        fault = classify_api_error(exc)
        logger.exception("Stream failed stage=%r fault=%s", asset_id, fault.code)
        yield _sse(fault.as_event())
        return

    yield _sse({"type": "done"})


async def _revision_sse_stream(
    asset_id: str,
    previous_draft: str,
    note: str,
    phase: str = DEFAULT_PHASE,
    chat_session_id: str | None = None,
    run_id: str | None = None,
):
    try:
        voice = await _run_industry(run_id)
        async for delta in generate_revision_stream(
            asset_id,
            previous_draft,
            note,
            phase,
            on_usage=usage_service.recorder(
                kind="revision",
                chat_session_id=chat_session_id,
                run_id=run_id,
                asset_id=asset_id,
                phase=phase,
            ),
            voice=voice,
        ):
            yield _sse({"type": "delta", "text": delta})
    except Exception as exc:  # noqa: BLE001 - every failure is classified and streamed, never swallowed
        fault = classify_api_error(exc)
        logger.exception("Revision stream failed stage=%r fault=%s", asset_id, fault.code)
        yield _sse(fault.as_event())
        return

    yield _sse({"type": "done"})


@router.post("/generate/{asset_id}/stream")
async def generate_stage_stream_route(asset_id: str, payload: GenerateStageRequest) -> StreamingResponse:
    """Stream one stage's real generation — the actual master prompt from `assets/Prompts/`
    for `asset_id`, filled in from `payload.answers` — as Markdown text deltas over SSE.

    For the 10 stages with a paired competitor-analysis stage, that prepass runs first and its
    output is folded into this prompt's competitor input before generation starts (emitted as a
    `prepass` event so the UI can file it to the Context Store alongside the main output)."""
    if not has_stage(asset_id, payload.phase):
        raise HTTPException(
            status_code=404, detail=f"Unknown asset_id {asset_id!r} for phase {payload.phase!r}"
        )

    logger.info("Generate-stream requested for asset_id=%r phase=%r", asset_id, payload.phase)
    return StreamingResponse(
        _generation_sse_stream(
            asset_id,
            payload.answers,
            payload.client_profile,
            payload.phase,
            payload.chat_session_id,
            payload.run_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/refine/{asset_id}/stream")
async def refine_stage_stream_route(asset_id: str, payload: RefineStageRequest) -> StreamingResponse:
    """Stream a revision of `payload.previous_draft` per `payload.note`, using the same model
    tier as `asset_id`'s original generation."""
    if not has_stage(asset_id, payload.phase):
        raise HTTPException(
            status_code=404, detail=f"Unknown asset_id {asset_id!r} for phase {payload.phase!r}"
        )

    logger.info("Refine-stream requested for asset_id=%r phase=%r", asset_id, payload.phase)
    return StreamingResponse(
        _revision_sse_stream(
            asset_id,
            payload.previous_draft,
            payload.note,
            payload.phase,
            payload.chat_session_id,
            payload.run_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
