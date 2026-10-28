"""`POST /pipeline/keywords/buyer-language`: suggestions for CRO's "Words the buyer uses for the
outcome" (`mode: outcome`) and "Words to avoid" (`mode: avoid`). Thin; see
`app/services/buyer_language.py`.

The browser sends what the session knows (the landing page, the services, the region). The run's
own keyword report is read here, free. Stores nothing. A failure
is a 422 with one sentence; the card says it and still takes typed words.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.base import get_sessionmaker
from app.routers.pipeline import KEYWORD_CONTEXT_KEY, _latest_context_entry
from app.services import buyer_language, usage as usage_service

router = APIRouter()

class BuyerLanguageRequest(BaseModel):
    mode: Literal["outcome", "avoid"]
    services: list[str] = Field(default_factory=list)
    region: str = ""
    landing_url: str = ""
    industry: str = ""
    run_id: str | None = None
    phase: str | None = None
    chat_session_id: str | None = None


async def _run_keywords(run_id: str | None) -> list[dict]:
    """This run's ranked keywords. Empty when there is no run."""
    try:
        run_uuid = uuid.UUID(run_id) if run_id else None
    except ValueError:
        run_uuid = None
    if run_uuid is None:
        return []
    rows: list[dict] = []
    async with get_sessionmaker()() as session:
        found = await _latest_context_entry(session, run_uuid, KEYWORD_CONTEXT_KEY)
        if found is not None and isinstance(found[0].value, dict) and found[0].value.get("provider") == "dataforseo":
            value = found[0].value
            rows = [r for r in value.get("ranked_keywords") or value.get("clean_keywords") or [] if isinstance(r, dict)]
    return rows


@router.post("/keywords/buyer-language")
async def buyer_language_suggestions(payload: BuyerLanguageRequest) -> dict:
    run_keywords = await _run_keywords(payload.run_id) if payload.mode == "outcome" else []
    on_usage = usage_service.recorder(
        kind="keywords", chat_session_id=payload.chat_session_id, run_id=payload.run_id, asset_id="cro", phase=payload.phase
    )
    try:
        if payload.mode == "outcome":
            result = await buyer_language.outcome_words(
                payload.services,
                payload.region,
                landing_url=payload.landing_url,
                industry=payload.industry,
                run_keywords=run_keywords,
                on_usage=on_usage,
            )
        else:
            result = await buyer_language.words_to_avoid(
                payload.services,
                payload.region,
                landing_url=payload.landing_url,
                industry=payload.industry,
                on_usage=on_usage,
            )
    except buyer_language.BuyerLanguageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()
