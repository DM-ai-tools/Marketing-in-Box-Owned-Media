"""`POST /pipeline/summary/{asset_id}`: the reader's Summary tab. Thin; see
`app/services/asset_summary.py`.

Takes the document in the body rather than reading it off the run, like the slide-deck and check
routes, so an unsaved draft can be summarised too. Stores nothing. A failure is a 422 with one
sentence, which the tab shows with a retry.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import asset_summary, usage as usage_service

router = APIRouter()


class SummaryRequest(BaseModel):
    text: str
    #: The deliverable's name as the reader shows it ("ICP — Ideal Customer Profile").
    label: str = ""
    phase: str | None = None
    chat_session_id: str | None = None
    run_id: str | None = None


@router.post("/summary/{asset_id}")
async def summarise_document(asset_id: str, payload: SummaryRequest) -> dict:
    try:
        result = await asset_summary.summarise(
            payload.label or asset_id,
            payload.text,
            on_usage=usage_service.recorder(
                kind="summary",
                chat_session_id=payload.chat_session_id,
                run_id=payload.run_id,
                asset_id=asset_id,
                phase=payload.phase,
            ),
        )
    except asset_summary.SummaryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()
