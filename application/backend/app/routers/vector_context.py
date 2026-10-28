"""Context search routes: suggested intake answers, and the index behind them.

Thin, like every router here. The work is in `app/services/vector_context.py`.

- `GET  /pipeline/vector/status`                  — free. Whether search is on, and why not.
- `POST /pipeline/runs/{run_id}/prefill/{asset}`  — suggested answers for a stage's questions. One
  embedding request and at most one Haiku call. Always a 200: a suggestion is advisory, and a
  failure leaves the intake asking as it always has, with the reason in `notes`.
- `POST /pipeline/runs/{run_id}/context/index`    — index what the run can see now. Normally
  unnecessary (saves index in the background and searches index lazily); here for a re-index after a
  model change or to warm a large run.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.base import get_sessionmaker
from app.db.models import Run
from app.services import embedding_client, usage as usage_service, vector_context

router = APIRouter()


def _run_uuid(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid run_id: {run_id!r}") from exc


@router.get("/vector/status")
async def vector_status() -> dict[str, object]:
    configured = vector_context.enabled()
    async with get_sessionmaker()() as session:
        available = await vector_context.available(session)
    return {
        "configured": configured,
        "available": available,
        "model": embedding_client.MODEL,
        "on": configured and available,
    }


class PrefillFieldIn(BaseModel):
    field_id: str
    label: str
    help: str = ""
    kind: str = "text"
    choices: list[str] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)


class PrefillRequest(BaseModel):
    fields: list[PrefillFieldIn] = Field(default_factory=list)
    phase: str | None = None
    chat_session_id: str | None = None


@router.post("/runs/{run_id}/prefill/{asset_id}")
async def prefill_stage(run_id: str, asset_id: str, payload: PrefillRequest) -> dict:
    run_uuid = _run_uuid(run_id)
    fields = [
        vector_context.PrefillField(
            field_id=f.field_id,
            label=f.label,
            help=f.help,
            kind=f.kind,
            choices=tuple(f.choices),
            keys=tuple(f.keys),
        )
        for f in payload.fields
    ]
    async with get_sessionmaker()() as session:
        if await session.get(Run, run_uuid) is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        result = await vector_context.prefill(
            session,
            run_uuid,
            asset_id,
            fields,
            on_usage=usage_service.recorder(
                kind="prefill",
                chat_session_id=payload.chat_session_id,
                run_id=run_id,
                asset_id=asset_id,
                phase=payload.phase,
            ),
        )
    return result.to_dict()


@router.post("/runs/{run_id}/context/index")
async def index_run_context(run_id: str) -> dict[str, object]:
    run_uuid = _run_uuid(run_id)
    if not vector_context.enabled():
        raise HTTPException(status_code=503, detail="Context search is off: OPENAI_API_KEY is unset or VECTOR_CONTEXT=off.")
    async with get_sessionmaker()() as session:
        if await session.get(Run, run_uuid) is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        if not await vector_context.available(session):
            raise HTTPException(status_code=503, detail="pgvector is not installed on this database.")
        entries = await vector_context.visible_entries(session, run_uuid)
        try:
            written = await vector_context.ensure_indexed(session, entries)
        except embedding_client.EmbeddingError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"run_id": run_id, "entries": len(entries), "chunks_written": written}
