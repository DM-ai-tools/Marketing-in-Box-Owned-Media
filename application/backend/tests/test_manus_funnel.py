"""The deployable funnel's pages, built by Manus from the live reference page.

No live Manus call: a scripted fake client stands in, as in `test_manus.py`. Checks that the brief
puts the reference page and its motion first, that the files come back by name, that a missing page
is reported per row, and that the route uses Manus for pages, Claude for emails, and Claude for the
pages when Manus fails.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import funnel_build as route
from app.services import funnel_build, manus_client, manus_funnel, manus_page
from app.services.manus_client import Attachment, TaskDetail, TaskEvent
from tests.test_funnel_build import AUDIT, PAGE, _inputs

REF = "https://trafficradius.com.au/social-media-marketing/"
SHOTS = ("https://shots.example/full.png", "https://shots.example/hero.png")


def _manus_inputs(**over):
    values = dict(reference_url=REF, reference_screenshots=SHOTS)
    values.update(over)
    return _inputs(**values)


class FakeManus:
    def __init__(self, files: dict[str, str], statuses=("running", "stopped")) -> None:
        self.files = files
        self.statuses = list(statuses)
        self.content: list[dict[str, Any]] = []
        self.created = 0

    async def create_task(self, content, **kwargs):
        self.created += 1
        self.content = content
        return manus_client.CreatedTask("t1", "https://manus.im/app/t1")

    async def detail(self, task_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return TaskDetail(status=status, background=False, credit_usage=120)

    async def all_messages(self, task_id):
        return [
            TaskEvent(
                id="a",
                type="assistant_message",
                content="Measured the reference page's scroll reveals",
                attachments=[Attachment("file", name, f"https://files.example/{name}") for name in self.files],
            )
        ]

    async def send_message(self, task_id, text):
        pass

    async def stop(self, task_id):
        pass

    async def download(self, attachment):
        return self.files[attachment.filename].encode()

    async def aclose(self):
        pass


async def _build(fake, inputs=None, files=None):
    manus_page._inflight.clear()
    inputs = inputs or _manus_inputs()
    files = files if files is not None else inputs.plan.files
    return [
        e async for e in manus_funnel.build_pages(inputs, files, run_id="r1", client_factory=lambda: fake, poll_seconds=0)
    ]


def test_the_brief_puts_the_reference_page_and_its_motion_first():
    inputs = _manus_inputs()
    brief = manus_funnel.build_brief(inputs, inputs.plan.files)
    assert brief.index(REF) < brief.index("# PAGE BUILD RULES")
    assert "THE REFERENCE PAGE IS THE DESIGN" in brief and "scroll-triggered" in brief
    assert "prefers-reduced-motion" in brief
    for f in inputs.plan.files:
        assert f"## File `{f.key}.html`" in brief
    assert '`<input type="hidden" name="_wpcf7" value="1">`' in brief
    # The page rules come along, but not the Claude prompt's own output format.
    assert "Forms post to the client's real form" in brief and "Return exactly two parts" not in brief


def test_the_request_carries_every_reference_screenshot():
    inputs = _manus_inputs()
    content = manus_funnel.build_content(inputs, inputs.plan.files, "brief")
    assert content[0]["type"] == "text" and "Open the REFERENCE PAGE URL" in content[0]["text"]
    urls = [part.get("file_url") or part.get("url") for part in content[2:]]
    assert len(content) == 2 + len(SHOTS)
    assert all(any(shot in json.dumps(part) for part in content[2:]) for shot in SHOTS), urls


@pytest.mark.asyncio
async def test_files_come_back_by_name_with_their_notes():
    inputs = _manus_inputs()
    keys = [f.key for f in inputs.plan.files]
    notes = "**Design measured**\n- Palette #ff602f\n\n" + "\n".join(f"## {k}\n- notes for {k}" for k in keys)
    fake = FakeManus({**{f"{k}.html": PAGE for k in keys}, manus_funnel.NOTES_FILE: notes})
    events = await _build(fake, inputs)

    assert events[0]["status"] == "started" and fake.created == 1
    result = events[-1]
    assert result["type"] == "manus_result" and result["credits"] == 120
    items = result["items"]
    assert [i["key"] for i in items] == keys
    assert all(i["html"] == PAGE and i["score"] is not None for i in items)
    assert f"notes for {keys[1]}" in items[1]["notes"] and "Design measured" in items[1]["notes"]


@pytest.mark.asyncio
async def test_a_page_manus_did_not_deliver_is_reported_on_its_row():
    inputs = _manus_inputs()
    first = inputs.plan.files[0].key
    events = await _build(FakeManus({f"{first}.html": PAGE}), inputs)
    items = events[-1]["items"]
    assert items[0]["html"] == PAGE and not items[0]["error"]
    assert all("did not deliver" in i["error"] for i in items[1:])


@pytest.mark.asyncio
async def test_no_page_at_all_is_a_failure_the_router_falls_back_on():
    with pytest.raises(manus_page.ManusPageError, match="without delivering any"):
        await _build(FakeManus({"notes.txt": "x"}))


def test_notes_split_per_page_with_the_shared_part_on_each():
    out = manus_funnel.split_notes("Shared\n\n## a\none\n\n## `b.html`\ntwo")
    assert out == {"a": "one\n\nShared", "b": "two\n\nShared"}


# -- The route -----------------------------------------------------------------------------------

RUN = "11111111-2222-3333-4444-555555555555"


def _route(monkeypatch, fake, stored: dict | None = None):
    writes = []

    async def docs(run_uuid):
        return AUDIT, "hub"

    async def inputs(run_id, run_uuid, plan, hub_text, profile):
        return _manus_inputs()

    async def store(run_uuid, value):
        writes.append(value)
        return 4

    async def stored_build(run_uuid):
        return stored

    emails_built = []

    async def claude(content, max_tokens, on_usage):
        emails_built.append(max_tokens)
        return f"```html\n{PAGE}\n```", "end_turn"

    manus_page._inflight.clear()
    monkeypatch.setattr(route, "_documents", docs)
    monkeypatch.setattr(route, "_inputs", inputs)
    monkeypatch.setattr(route, "_store", store)
    monkeypatch.setattr(route, "_stored_build", stored_build)
    monkeypatch.setattr(funnel_build, "_call", claude)
    monkeypatch.setattr(manus_funnel, "uses_manus", lambda: True)
    monkeypatch.setattr(manus_page, "POLL_SECONDS", 0)
    real = manus_funnel.build_pages

    def patched(inputs, files, *, run_id, **kwargs):
        return real(inputs, files, run_id=run_id, client_factory=lambda: fake, poll_seconds=0)

    monkeypatch.setattr(manus_funnel, "build_pages", patched)
    return writes, emails_built


def _events(response):
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def test_pages_by_manus_and_emails_by_claude(monkeypatch):
    keys = [f.key for f in _manus_inputs().plan.files]
    fake = FakeManus({f"{k}.html": PAGE for k in keys})
    writes, claude_calls = _route(monkeypatch, fake)
    from app.main import app

    events = _events(TestClient(app).post(f"/pipeline/runs/{RUN}/funnel-build/stream", json={}))
    kinds = [e["type"] for e in events]
    assert "manus" in kinds and kinds[-1] == "done"
    assert events[-1]["engine"]["pages"] == "manus" and events[-1]["engine"]["task_url"] == "https://manus.im/app/t1"
    assert claude_calls == [funnel_build.EMAIL_MAX_TOKENS] * 3  # only the emails went to Claude
    assert len(writes[0]["items"]) == len(keys) + 3


def test_manus_failing_falls_back_to_claude_for_the_pages(monkeypatch):
    writes, claude_calls = _route(monkeypatch, FakeManus({"nothing.txt": "x"}))
    from app.main import app

    events = _events(TestClient(app).post(f"/pipeline/runs/{RUN}/funnel-build/stream", json={"include_emails": False}))
    fallback = [e for e in events if e["type"] == "manus" and e.get("status") == "fallback"]
    assert fallback and "without delivering any" in fallback[0]["reason"]
    assert events[-1]["engine"]["pages"] == "claude"
    assert claude_calls.count(funnel_build.PAGE_MAX_TOKENS) == 4


def test_a_single_page_rebuild_merges_into_the_stored_build(monkeypatch):
    plan_keys = [f.key for f in _manus_inputs().plan.files]
    old = [{"key": k, "kind": "page", "title": k, "file_path": f"{k}/index.html", "html": "<old>"} for k in plan_keys]
    fake = FakeManus({f"{plan_keys[1]}.html": PAGE})
    writes, _ = _route(monkeypatch, fake, stored={"items": old})
    from app.main import app

    _events(TestClient(app).post(f"/pipeline/runs/{RUN}/funnel-build/stream", json={"only": [plan_keys[1]]}))
    items = writes[0]["items"]
    assert [i["key"] for i in items] == plan_keys
    assert items[1]["html"] == PAGE and items[0]["html"] == "<old>"
