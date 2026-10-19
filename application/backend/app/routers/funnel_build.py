"""The deployable funnel — routes. See `app/services/funnel_build.py` for what is built and how.

  GET  /pipeline/runs/{run_id}/funnel-build/ready        free: is there a funnel to build, and what
  POST /pipeline/runs/{run_id}/funnel-build/stream       build everything, or `only` some (SSE progress)
  GET  /pipeline/runs/{run_id}/funnel-build              the stored build, HTML included
  GET  /pipeline/runs/{run_id}/funnel-build/zip          the bundle: pages with images written in

The build reads only this run's approved Funnel and Funnel Hub & Media documents — never a parent
run's, so a Phase 2 run builds its own sub-service funnel. It is stored on the run under
`funnel_build`, a new version per build, like every other document.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.db.models import ContextEntry, Run
from app.routers.pipeline import (
    _SSE_HEADERS,
    _entry_text_on_run,
    _load_media,
    _next_version,
    _page_design_input,
    _sse,
    _stored_design_tokens,
    resolve_page_design,
)
from app.services import funnel_build, manus_funnel, manus_page, media_storage, reference_forms, standalone_html
from app.services import usage as usage_service
from app.services.funnel_plan import FunnelPlan, hub_routing_text, parse_funnel_document
from app.services.link_manifest import LinkManifest

logger = logging.getLogger(__name__)

router = APIRouter()

FUNNEL_KEYS = ("funnel", "funnel_stages")
HUB_KEYS = ("funnel_hub_media",)


class FunnelBuildRequest(BaseModel):
    client_profile: dict[str, str] = {}
    phase: str = "phase1"
    chat_session_id: str | None = None
    include_emails: bool = True
    #: Build only these pages / emails and merge them into the stored build (a rebuild). Empty
    #: builds everything.
    only: list[str] = []


def _run_uuid(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Not a run id.") from exc


async def _documents(run_uuid: uuid.UUID) -> tuple[str, str]:
    async with get_sessionmaker()() as session:
        if await session.get(Run, run_uuid) is None:
            raise HTTPException(status_code=404, detail="No such run.")
        funnel = await _entry_text_on_run(session, run_uuid, FUNNEL_KEYS)
        hub = await _entry_text_on_run(session, run_uuid, HUB_KEYS)
    return funnel, hub


def _plan_or_422(funnel_text: str) -> FunnelPlan:
    if not funnel_text.strip():
        raise HTTPException(status_code=422, detail="Approve the Funnel stage first: there is no funnel to build.")
    plan = parse_funnel_document(funnel_text)
    if not plan.available:
        raise HTTPException(
            status_code=422,
            detail="The approved Funnel document has no page copy in the expected shape "
            "(\"PART n — PAGE k: … — FULL COPY\"). Re-run the Funnel stage, then build.",
        )
    return plan


def _plan_summary(plan: FunnelPlan) -> dict:
    return {
        "title": plan.title,
        "files": [
            {"key": f.key, "title": f.title, "url_path": f.url_path, "file_path": f.file_path, "screens": len(f.screens)}
            for f in plan.files
        ],
        "emails": [{"key": e.key, "title": e.title, "subject": e.subject} for e in plan.emails],
    }


def _known_images(value: dict) -> list[str]:
    urls = [
        media_storage.current_media_url(str(item["url"]))
        for key in ("generated_images", "section_images", "hosted_images")
        for item in (value.get(key) or [])
        if isinstance(item, dict) and item.get("url")
    ]
    logo = value.get("hosted_logo")
    if isinstance(logo, dict) and logo.get("url"):
        urls.append(media_storage.current_media_url(str(logo["url"])))
    return urls


async def _inputs(run_id: str, run_uuid: uuid.UUID, plan: FunnelPlan, hub_text: str, profile: dict[str, str]) -> funnel_build.BuildInputs:
    """Everything the builder reads, from the run. The design sheet is the run's own — reused as
    stored (asked for at the URL it was captured from, so this never triggers a re-capture of a
    different page), and captured only when the run has none."""
    async with get_sessionmaker()() as session:
        stored = await _stored_design_tokens(session, run_uuid)
    value = stored[0] if stored is not None else {}
    reference_url = str(value.get("source_url") or profile.get("website_url") or "")
    answers = {"reference_design_source": reference_url} if reference_url else {}
    design = await resolve_page_design(run_id, answers, profile)

    manifest = LinkManifest.from_dict(value.get("links") if isinstance(value.get("links"), dict) else None)
    cta = manifest.primary_cta.href if manifest is not None and manifest.primary_cta is not None else ""
    forms, form_notes = await reference_forms.read_forms([reference_url, cta])
    origin = funnel_build.site_origin(profile.get("website_url") or reference_url)
    if not origin:
        origin = funnel_build.site_origin(reference_url)
    return funnel_build.BuildInputs(
        plan=plan,
        design=design,
        manifest=manifest,
        forms=forms,
        hub_text=hub_routing_text(hub_text),
        site_origin=origin,
        known_images=_known_images(value),
        reference_url=reference_url,
        reference_screenshots=tuple(
            str(shot["url"]) for shot in value.get("screenshots") or [] if isinstance(shot, dict) and shot.get("url")
        ),
        notes=form_notes + ([] if design is not None else ["The run has no captured page design, so the pages are built without the client's brand tokens."]),
    )


async def _store(run_uuid: uuid.UUID, value: dict) -> int:
    async with get_sessionmaker()() as session:
        version = await _next_version(session, run_uuid, funnel_build.CONTEXT_KEY)
        session.add(
            ContextEntry(
                run_id=run_uuid,
                context_key=funnel_build.CONTEXT_KEY,
                version=version,
                value=value,
                written_by_asset_id=None,
            )
        )
        await session.commit()
    return version


async def _stored_build(run_uuid: uuid.UUID) -> dict | None:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(ContextEntry)
            .where(ContextEntry.run_id == run_uuid, ContextEntry.context_key == funnel_build.CONTEXT_KEY)
            .order_by(ContextEntry.version.desc())
            .limit(1)
        )
        entry = result.scalar_one_or_none()
    return entry.value if entry is not None and isinstance(entry.value, dict) else None


def _stored_value(
    plan: FunnelPlan, inputs: funnel_build.BuildInputs, items: list[funnel_build.BuiltItem], engine: dict | None = None
) -> dict:
    forms = [f.to_dict() for f in inputs.forms]
    return {
        # Which builder made the pages, and the Manus task when it was Manus.
        "engine": engine or {"pages": "claude"},
        # `content` is what the Context Store shows and what any reader of the generic shape gets.
        "content": funnel_build.readme(plan.title, items, forms, plan.notes, inputs.site_origin),
        "plan_title": plan.title,
        "site_origin": inputs.site_origin,
        "implementation_notes": plan.notes,
        "forms": forms,
        "notes": inputs.notes,
        "items": [i.to_dict() for i in items],
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


async def _build(
    run_id: str,
    inputs: funnel_build.BuildInputs,
    files: list,
    emails: list,
    queue: asyncio.Queue,
    on_event,
    on_usage,
) -> tuple[list[funnel_build.BuiltItem], dict]:
    """Pages by Manus when it is configured (Claude if it fails), emails by Claude alongside."""
    if not files or not manus_funnel.uses_manus():
        items = await funnel_build.build_all(inputs, on_event=on_event, on_usage=on_usage, files=files, emails=emails)
        return items, {"pages": "claude"}

    email_job = asyncio.create_task(
        funnel_build.build_all(inputs, on_event=on_event, on_usage=on_usage, files=[], emails=emails)
    ) if emails else None
    for f in files:
        await queue.put({"type": "item_start", "key": f.key, "kind": "page"})
    engine: dict = {"pages": "manus"}
    pages: list[funnel_build.BuiltItem] = []
    try:
        async for event in manus_funnel.build_pages(inputs, files, run_id=run_id):
            if event["type"] == "manus":
                await queue.put(event)
                continue
            pages = [funnel_build.BuiltItem.from_dict(i) for i in event["items"]]
            engine.update(task_url=event.get("task_url"), credits=event.get("credits"))
            await queue.put({"type": "manus", "status": "done", "task_url": event.get("task_url"), "credits": event.get("credits")})
            for item in pages:
                await queue.put({"type": "item_done", "item": item.summary()})
    except manus_page.ManusPageError as exc:
        logger.warning("Manus funnel build failed for run %s, falling back to Claude: %s", run_id, exc)
        await queue.put({"type": "manus", "status": "fallback", "reason": str(exc)})
        engine = {"pages": "claude", "manus_fallback": str(exc)}
        pages = await funnel_build.build_all(inputs, on_event=on_event, on_usage=on_usage, files=files, emails=[])
    built_emails = await email_job if email_job is not None else []
    return [*pages, *built_emails], engine


@router.get("/runs/{run_id}/funnel-build/ready")
async def funnel_build_ready(run_id: str) -> dict:
    """Free. Whether this run holds an approved Funnel to build (and a hub document to route with),
    what the build would make, and whether a build is already stored."""
    run_uuid = _run_uuid(run_id)
    funnel, hub = await _documents(run_uuid)
    plan = parse_funnel_document(funnel) if funnel.strip() else None
    stored = await _stored_build(run_uuid)
    return {
        "funnel": bool(funnel.strip()),
        "hub": bool(hub.strip()),
        "buildable": bool(plan and plan.available),
        "plan": _plan_summary(plan) if plan and plan.available else None,
        "built": stored is not None,
        "built_at": stored.get("built_at") if stored else None,
    }


@router.post("/runs/{run_id}/funnel-build/stream")
async def funnel_build_stream(run_id: str, payload: FunnelBuildRequest) -> StreamingResponse:
    run_uuid = _run_uuid(run_id)
    funnel, hub = await _documents(run_uuid)
    plan = _plan_or_422(funnel)
    on_usage = usage_service.recorder(
        kind="funnel_build",
        chat_session_id=payload.chat_session_id,
        run_id=run_id,
        asset_id=funnel_build.ASSET_ID,
        phase=payload.phase,
    )

    async def events():
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def on_event(event: dict) -> None:
            await queue.put(event)

        async def work() -> None:
            try:
                await queue.put({"type": "plan", "plan": _plan_summary(plan), "hub": bool(hub.strip())})
                inputs = await _inputs(run_id, run_uuid, plan, hub, payload.client_profile)
                await queue.put(
                    {
                        "type": "inputs",
                        "forms": [{"action": f.action, "page_url": f.page_url} for f in inputs.forms],
                        "notes": inputs.notes,
                        "branded": inputs.design is not None,
                    }
                )
                files = [f for f in plan.files if not payload.only or f.key in payload.only]
                emails = [
                    e for e in (plan.emails if payload.include_emails else [])
                    if not payload.only or e.key in payload.only
                ]
                items, engine = await _build(run_id, inputs, files, emails, queue, on_event, on_usage)
                if payload.only:
                    stored = await _stored_build(run_uuid)
                    kept = [
                        funnel_build.BuiltItem.from_dict(i)
                        for i in (stored or {}).get("items") or []
                        if isinstance(i, dict) and i.get("key") not in {x.key for x in items}
                    ]
                    order = [f.key for f in plan.files] + [e.key for e in plan.emails]
                    items = sorted([*kept, *items], key=lambda i: order.index(i.key) if i.key in order else len(order))
                version = await _store(run_uuid, _stored_value(plan, inputs, items, engine))
                await queue.put({"type": "done", "version": version, "items": [i.summary() for i in items], "engine": engine})
            except HTTPException as exc:
                await queue.put({"type": "error", "message": str(exc.detail)})
            except Exception as exc:  # noqa: BLE001 — reported on the card, never a dropped stream
                logger.exception("Funnel build failed for run %s", run_id)
                await queue.put({"type": "error", "message": f"The funnel build failed: {exc}"})
            finally:
                await queue.put(None)

        task = asyncio.create_task(work())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/runs/{run_id}/funnel-build")
async def get_funnel_build(run_id: str) -> dict:
    stored = await _stored_build(_run_uuid(run_id))
    if stored is None:
        raise HTTPException(status_code=404, detail="This run has no deployable funnel yet.")
    return stored


async def _check_inputs(run_uuid: uuid.UUID, plan: FunnelPlan) -> funnel_build.BuildInputs:
    """What a revision reads: the stored design sheet, links and run images — for the edit prompt and
    the re-check. No form read and no capture, so a requested change spends one model call only."""
    async with get_sessionmaker()() as session:
        stored = await _stored_design_tokens(session, run_uuid)
    value = stored[0] if stored is not None else {}
    manifest = LinkManifest.from_dict(value.get("links") if isinstance(value.get("links"), dict) else None)
    return funnel_build.BuildInputs(
        plan=plan,
        design=_page_design_input(value) if value else None,
        manifest=manifest,
        forms=[],
        hub_text="",
        site_origin=funnel_build.site_origin(str(value.get("source_url") or "")),
        known_images=_known_images(value),
    )


class FunnelReviseRequest(FunnelBuildRequest):
    note: str = Field(..., min_length=1, max_length=4000)


@router.post("/runs/{run_id}/funnel-build/items/{key}/revise")
async def revise_funnel_item(run_id: str, key: str, payload: FunnelReviseRequest) -> dict:
    """Apply the operator's requested change to one built page or email — as exact edits, so only
    what the note asks for changes (`funnel_build.revise_item`). Stores the build with it replaced."""
    run_uuid = _run_uuid(run_id)
    stored = await _stored_build(run_uuid)
    if stored is None:
        raise HTTPException(status_code=404, detail="Build the funnel first.")
    items = [funnel_build.BuiltItem.from_dict(i) for i in stored.get("items") or [] if isinstance(i, dict)]
    item = next((i for i in items if i.key == key), None)
    if item is None or not item.html:
        raise HTTPException(status_code=404, detail=f"The build has no finished page or email {key!r}.")

    funnel, _hub = await _documents(run_uuid)
    plan = _plan_or_422(funnel)
    page = next((f for f in plan.files if f.key == key), None)
    email = next((e for e in plan.emails if e.key == key), None)
    source_copy = "\n\n".join(s.copy for s in page.screens) if page is not None else (email.copy if email else "")
    inputs = await _check_inputs(run_uuid, plan)
    design = inputs.design
    design_md = ""
    if design is not None:
        design_md = design.design_md if page is not None else (design.theme_brief or design.design_md)

    on_usage = usage_service.recorder(
        kind="funnel_revision", chat_session_id=payload.chat_session_id, run_id=run_id,
        asset_id=funnel_build.ASSET_ID, phase=payload.phase,
    )
    result = await funnel_build.revise_item(
        item, payload.note, source_copy=source_copy, design_md=design_md,
        inputs=inputs if page is not None else None, on_usage=on_usage,
    )
    if result.applied:
        replaced = [result.item if i.key == key else i for i in items]
        forms = stored.get("forms") or []
        value = {
            **stored,
            "content": funnel_build.readme(plan.title, replaced, forms, plan.notes, str(stored.get("site_origin") or "")),
            "items": [i.to_dict() for i in replaced],
        }
        await _store(run_uuid, value)
    return {
        "item": result.item.summary(),
        "applied": result.applied,
        "not_applied": [{"excerpt": e, "why": w} for e, w in result.not_applied],
        "unreadable": result.unreadable,
    }


@router.get("/runs/{run_id}/funnel-build/zip")
async def download_funnel_build(run_id: str) -> Response:
    stored = await _stored_build(_run_uuid(run_id))
    if stored is None:
        raise HTTPException(status_code=404, detail="This run has no deployable funnel yet.")
    items = [funnel_build.BuiltItem.from_dict(i) for i in stored.get("items") or [] if isinstance(i, dict)]

    async def embed(html: str) -> str:
        return (await standalone_html.make_standalone(html, _load_media)).html

    data = await funnel_build.bundle_zip(
        str(stored.get("plan_title") or "Funnel"),
        items,
        stored.get("forms") or [],
        str(stored.get("implementation_notes") or ""),
        str(stored.get("site_origin") or ""),
        embed,
    )
    name = funnel_build.slug_filename(str(stored.get("plan_title") or "funnel"))
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-deployable.zip"'},
    )
