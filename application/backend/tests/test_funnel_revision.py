"""Requested changes on a deployable funnel page: only what the note asks for changes.

Same mechanism as the Pillar Page's Refine (`page_revision`): the model returns find/replace edits,
code places them, and every byte no edit names stays as built. No live model call: `_call` is stubbed.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.routers import funnel_build as route
from app.services import funnel_build

PAGE = (
    "<!doctype html><html lang='en'><head><title>Book</title></head><body><main>"
    "<h1>Book your strategy call</h1><p>Thirty minutes, no obligation.</p>"
    "<a class='btn' href='https://x.com.au/booking/'>Book now</a></main></body></html>"
)


def _item(**over) -> funnel_build.BuiltItem:
    values = dict(key="book", kind="page", title="Book", file_path="book/index.html", url_path="/book/", html=PAGE, score=90)
    values.update(over)
    return funnel_build.BuiltItem(**values)


def _reply(edits, skipped=()):
    return json.dumps({"edits": edits, "skipped": [{"finding": f, "why": w} for f, w in skipped]})


@pytest.mark.asyncio
async def test_only_the_requested_change_lands(monkeypatch):
    seen = {}

    async def fake_call(prompt, max_tokens, on_usage):
        seen["prompt"], seen["max_tokens"] = prompt, max_tokens
        return _reply([{"find": "Book now", "replace": "Book my free call", "why": "button wording"}]), "end_turn"

    monkeypatch.setattr(funnel_build, "_call", fake_call)
    result = await funnel_build.revise_item(_item(), "Change the button to 'Book my free call'", source_copy="approved copy")

    assert result.applied == 1 and not result.unreadable
    assert result.item.html == PAGE.replace("Book now", "Book my free call")
    assert result.item.revisions == [{"note": "Change the button to 'Book my free call'", "applied": 1}]
    # The model is handed the built page and asked for edits, never a whole page.
    assert PAGE in seen["prompt"] and '"edits"' in seen["prompt"] and "approved copy" in seen["prompt"]
    assert seen["max_tokens"] == funnel_build.REVISION_MAX_TOKENS


@pytest.mark.asyncio
async def test_an_edit_that_cannot_be_placed_is_reported_not_guessed(monkeypatch):
    async def fake_call(prompt, max_tokens, on_usage):
        return _reply([{"find": "text not on the page", "replace": "x"}], skipped=[("add a video", "no video on record")]), "end_turn"

    monkeypatch.setattr(funnel_build, "_call", fake_call)
    result = await funnel_build.revise_item(_item(), "Change the subheading and add a video")

    assert result.applied == 0 and result.item.html == PAGE
    reasons = " ".join(w for _e, w in result.not_applied)
    assert "Not applied" in reasons and "no video on record" in reasons


@pytest.mark.asyncio
async def test_an_unreadable_reply_leaves_the_page_untouched(monkeypatch):
    async def fake_call(prompt, max_tokens, on_usage):
        return "Sure! Here is the whole page again: <html>…</html>", "end_turn"

    monkeypatch.setattr(funnel_build, "_call", fake_call)
    result = await funnel_build.revise_item(_item(), "Make it pop")
    assert result.unreadable and result.item.html == PAGE and result.applied == 0


RUN = "11111111-2222-3333-4444-555555555555"


def test_the_route_stores_the_revised_page_only_when_something_changed(monkeypatch):
    stored_build = {"plan_title": "F", "site_origin": "https://x.com.au", "forms": [], "items": [_item().to_dict()]}
    writes = []

    async def stored(run_uuid):
        return stored_build

    async def docs(run_uuid):
        return "", ""

    async def store(run_uuid, value):
        writes.append(value)
        return 2

    async def check_inputs(run_uuid, plan):
        return funnel_build.BuildInputs(plan=plan, design=None, manifest=None, forms=[], hub_text="", site_origin="https://x.com.au")

    from app.services.funnel_plan import FunnelFile, FunnelPlan

    plan = FunnelPlan(title="F", preamble="", strategy="", files=[FunnelFile(key="book", url_path="/book/")], emails=[], notes="")

    async def fake_call(prompt, max_tokens, on_usage):
        return _reply([{"find": "Thirty minutes", "replace": "Twenty minutes"}]), "end_turn"

    monkeypatch.setattr(route, "_stored_build", stored)
    monkeypatch.setattr(route, "_documents", docs)
    monkeypatch.setattr(route, "_plan_or_422", lambda text: plan)
    monkeypatch.setattr(route, "_check_inputs", check_inputs)
    monkeypatch.setattr(route, "_store", store)
    monkeypatch.setattr(funnel_build, "_call", fake_call)
    from app.main import app

    body = TestClient(app).post(f"/pipeline/runs/{RUN}/funnel-build/items/book/revise", json={"note": "Say twenty minutes"}).json()
    assert body["applied"] == 1 and body["item"]["revisions"][0]["note"] == "Say twenty minutes"
    assert "Twenty minutes" in writes[0]["items"][0]["html"]

    async def nothing(prompt, max_tokens, on_usage):
        return _reply([]), "end_turn"

    monkeypatch.setattr(funnel_build, "_call", nothing)
    TestClient(app).post(f"/pipeline/runs/{RUN}/funnel-build/items/book/revise", json={"note": "no-op"})
    assert len(writes) == 1  # nothing applied, nothing stored
