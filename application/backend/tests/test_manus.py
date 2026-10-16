"""The Pillar Page built by Manus: the wrapper, the build loop, and the stage stream's use of it.

Never reaches the live API. The wrapper is driven through an `httpx.MockTransport` shaped like the
real responses (attachments on `assistant_message`, `task.detail` nested under `task`, files on a
separate signed host); the build loop and the router use a fake client.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.routers import pipeline as pipeline_router
from app.services import manus_client, manus_page
from app.services.manus_client import Attachment, ManusClient, ManusError, TaskDetail, TaskEvent

PAGE = "<!doctype html>\n<html lang=\"en\"><body><h1>Meta Ads</h1></body></html>"
REPORT = "## PART 1 — DESIGN SYSTEM EXTRACTED\n\nTokens.\n\n## PART 3 — FULL DESIGNED PAGE\n\n{{PAGE_HTML}}\n\n## PART 4 — SEO\n\nNotes."


# ---------- compose_document ----------


def test_the_page_goes_where_the_marker_stands() -> None:
    doc = manus_page.compose_document(REPORT, PAGE)
    assert "{{PAGE_HTML}}" not in doc
    assert doc.index("PART 3") < doc.index("```html\n<!doctype html>") < doc.index("PART 4")


def test_a_report_without_the_marker_gets_the_page_under_its_own_heading() -> None:
    doc = manus_page.compose_document("## Notes\n\nDone.", PAGE)
    assert doc.startswith("## Notes") and "## Full designed page\n\n```html" in doc


def test_a_report_that_already_fences_the_page_is_used_as_it_is() -> None:
    report = "## Page\n\n```html\n" + PAGE + "\n```"
    assert manus_page.compose_document(report, None).strip() == report


def test_no_page_at_all_is_a_failure_not_an_empty_draft() -> None:
    with pytest.raises(manus_page.ManusPageError, match="without delivering the page"):
        manus_page.compose_document("## Notes only", None)


def test_the_brief_travels_as_a_file_and_the_message_stays_short() -> None:
    parts = manus_page.build_content("x" * 200_000, ("https://shots.example/hero.png",))
    text = [p for p in parts if p["type"] == "text"]
    files = [p for p in parts if p["type"] == "file"]
    assert len(text) == 1 and len(text[0]["text"]) < 4_000  # the request text cap is ~5,000 tokens
    assert files[0]["filename"] == "brief.md" and files[0]["file_data"].startswith("data:text/markdown;base64,")
    assert files[1]["file_url"] == "https://shots.example/hero.png"
    assert "Do not publish" in text[0]["text"] and manus_page.PAGE_MARKER in text[0]["text"]


# ---------- the wrapper, against live-shaped responses ----------


def _transport(calls: list[httpx.Request], *, status_code: int = 200, create_body: Any = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if request.url.host == "files.example":
            return httpx.Response(200, content=PAGE.encode())
        if path.endswith("task.create"):
            body = create_body if create_body is not None else {"ok": True, "task_id": "t1", "task_url": "https://manus.im/app/t1"}
            return httpx.Response(status_code, json=body)
        if path.endswith("task.detail"):
            return httpx.Response(200, json={"ok": True, "task": {"status": "stopped", "has_running_background_jobs": False, "credit_usage": 42}})
        if path.endswith("task.listMessages"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [
                        {"id": "e1", "type": "status_update", "status_update": {"agent_status": "running", "brief": "Manus is running"}},
                        {
                            "id": "e2",
                            "type": "assistant_message",
                            "assistant_message": {
                                "content": "Created and verified both files.",
                                "attachments": [
                                    {"type": "file", "filename": "page.html", "url": "https://files.example/page.html", "content_type": "text/html; charset=utf-8"}
                                ],
                            },
                        },
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404, json={"ok": False, "error": {"code": "not_found", "message": "no"}})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_create_sends_the_key_header_and_no_questions() -> None:
    calls: list[httpx.Request] = []
    client = ManusClient("k", transport=_transport(calls))
    task = await client.create_task([manus_client.text_part("hi")], title="Pillar Page — Acme", project_id="p1")
    await client.create_task([manus_client.text_part("hi")])
    await client.aclose()
    assert task.task_id == "t1"
    body = json.loads(calls[0].content)
    assert calls[0].headers["x-manus-api-key"] == "k"
    assert body["interactive_mode"] is False and body["title"] == "Pillar Page — Acme"
    assert body["project_id"] == "p1"
    assert "project_id" not in json.loads(calls[1].content), "no project is sent unless one is named"


@pytest.mark.asyncio
async def test_events_detail_and_download_read_the_real_shapes() -> None:
    calls: list[httpx.Request] = []
    client = ManusClient("k", transport=_transport(calls))
    events = await client.all_messages("t1")
    detail = await client.detail("t1")
    page = events[1].attachments[0]
    data = await client.download(page)
    await client.aclose()
    assert (events[0].status, events[1].content) == ("running", "Created and verified both files.")
    assert page.filename == "page.html" and data.decode() == PAGE
    assert detail == TaskDetail(status="stopped", background=False, credit_usage=42)
    # The signed file host never sees the key.
    assert "x-manus-api-key" not in calls[-1].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "words"),
    [(401, "rejected the API key"), (402, "out of credits"), (429, "rate-limiting"), (500, "failed (500)")],
)
async def test_every_failure_is_one_actionable_sentence(status: int, words: str) -> None:
    client = ManusClient("k", transport=_transport([], status_code=status, create_body={"ok": False, "error": {"code": "x", "message": "y"}}))
    with pytest.raises(ManusError, match=words.replace("(", r"\(").replace(")", r"\)")):
        await client.create_task([manus_client.text_part("hi")])
    await client.aclose()


def test_no_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in manus_client.API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    assert not manus_client.is_configured()
    assert not manus_page.uses_manus("pillar_page")
    monkeypatch.setenv("manus", "k")
    assert manus_page.uses_manus("pillar_page")
    assert not manus_page.uses_manus("cro"), "CRO writes copy; Manus builds only the page"
    monkeypatch.setenv("MANUS_PAGE_BUILDER", "off")
    assert not manus_page.uses_manus("pillar_page")


# ---------- the build loop ----------


class FakeManus:
    """Scripted task: `statuses` are returned by successive `detail` calls."""

    instances: list["FakeManus"] = []

    def __init__(self, statuses: list[str], files: dict[str, str] | None = None, error: str = "") -> None:
        self.statuses = list(statuses)
        self.files = files if files is not None else {"page.html": PAGE, "report.md": REPORT}
        self.error = error
        self.created = 0
        self.project_ids: list[str | None] = []
        self.sent: list[str] = []
        self.stopped = False
        FakeManus.instances.append(self)

    async def create_task(self, content: list[dict[str, Any]], **kwargs: Any) -> manus_client.CreatedTask:
        self.created += 1
        self.project_ids.append(kwargs.get("project_id"))
        return manus_client.CreatedTask("t1", "https://manus.im/app/t1")

    async def detail(self, task_id: str) -> TaskDetail:
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return TaskDetail(status=status, background=False, credit_usage=10)

    async def all_messages(self, task_id: str) -> list[TaskEvent]:
        events = [TaskEvent(id="s", type="status_update", status="running", brief="Manus is running")]
        if self.error:
            events.append(TaskEvent(id="x", type="error_message", content=self.error))
        events.append(
            TaskEvent(
                id="a",
                type="assistant_message",
                content="Writing the hero section\nmore detail",
                attachments=[Attachment("file", name, f"https://files.example/{name}") for name in self.files],
            )
        )
        return events

    async def send_message(self, task_id: str, text: str) -> None:
        self.sent.append(text)

    async def stop(self, task_id: str) -> None:
        self.stopped = True

    async def download(self, attachment: Attachment) -> bytes:
        return self.files[attachment.filename].encode()

    async def aclose(self) -> None:
        pass


async def _run(fake: FakeManus, **kwargs: Any) -> list[dict[str, Any]]:
    manus_page._inflight.clear()
    return [e async for e in manus_page.build_page("brief", (), client_factory=lambda: fake, poll_seconds=0, **kwargs)]


@pytest.mark.asyncio
async def test_a_finished_task_becomes_the_stage_document() -> None:
    events = await _run(FakeManus(["running", "running", "stopped"]))
    assert events[0]["status"] == "started"
    assert any(e.get("brief") == "Writing the hero section" for e in events), "progress shows the agent's own words"
    result = events[-1]
    assert result["type"] == "manus_result" and result["credits"] == 10
    assert "```html\n<!doctype html>" in result["document"] and "PART 4" in result["document"]
    assert not manus_page._inflight


@pytest.mark.asyncio
async def test_every_pillar_page_task_is_created_in_the_miab_landing_page_project() -> None:
    fake = FakeManus(["stopped"])
    await _run(fake)
    assert fake.project_ids == ["mXepyiMV6XxnDz3oTcQ3kr"]
    assert (manus_page.MANUS_PROJECT_NAME, manus_page.MANUS_PROJECT_ID) == ("MIAB - Landing Page", "mXepyiMV6XxnDz3oTcQ3kr")


@pytest.mark.asyncio
async def test_a_waiting_agent_is_told_to_proceed_then_stopped() -> None:
    fake = FakeManus(["waiting", "stopped"])
    events = await _run(fake)
    assert len(fake.sent) == 1 and events[-1]["type"] == "manus_result"

    stuck = FakeManus(["waiting"])
    with pytest.raises(manus_page.ManusPageError, match="kept stopping to ask"):
        await _run(stuck)
    assert stuck.stopped and len(stuck.sent) == manus_page._MAX_NUDGES


@pytest.mark.asyncio
async def test_an_error_names_the_reason() -> None:
    with pytest.raises(manus_page.ManusPageError, match="ran out of context"):
        await _run(FakeManus(["error"], error="ran out of context"))


@pytest.mark.asyncio
async def test_stopping_without_files_is_a_failure() -> None:
    with pytest.raises(manus_page.ManusPageError, match="without attaching the page"):
        await _run(FakeManus(["stopped"], files={}))


@pytest.mark.asyncio
async def test_a_build_that_runs_too_long_is_stopped() -> None:
    fake = FakeManus(["running"])
    with pytest.raises(manus_page.ManusPageError, match="had not finished"):
        await _run(fake, max_wait_seconds=0)
    assert fake.stopped


@pytest.mark.asyncio
async def test_a_reconnect_resumes_the_task_already_paid_for() -> None:
    manus_page._inflight.clear()
    first = FakeManus(["running"])
    gen = manus_page.build_page("brief", (), client_factory=lambda: first, poll_seconds=0)
    assert (await gen.__anext__())["status"] == "started"
    await gen.aclose()  # the browser dropped the connection

    second = FakeManus(["stopped"])
    events = [e async for e in manus_page.build_page("brief", (), client_factory=lambda: second, poll_seconds=0)]
    assert second.created == 0, "a second task was bought for the same brief"
    assert events[0]["status"] == "resumed" and events[-1]["type"] == "manus_result"


# ---------- the stage stream ----------


async def _stream(monkeypatch: pytest.MonkeyPatch, asset_id: str, build: Any) -> tuple[list[dict[str, Any]], list[str]]:
    claude: list[str] = []

    async def passthrough(_asset_id: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
        return answers, None

    async def nothing(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_claude(_asset_id: str, _answers: dict[str, str], _phase: str, **_kwargs: Any):
        claude.append(_asset_id)
        yield "claude page"

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_read_selected_service_pages", passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", nothing)
    monkeypatch.setattr(pipeline_router, "resolve_page_template", nothing)
    monkeypatch.setattr(pipeline_router, "_run_industry", nothing)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_claude)
    monkeypatch.setattr(manus_page, "build_page", build)
    monkeypatch.setenv("MANUS_API_KEY", "k")

    raw = [e async for e in pipeline_router._generation_sse_stream(asset_id, {}, {}, "phase1", None, None)]
    events = [json.loads(e.split("data:", 1)[1]) for e in raw if "data:" in e]
    return events, claude


async def _manus_ok(*_a: Any, **_k: Any):
    yield {"type": "manus", "status": "started", "task_url": "https://manus.im/app/t1"}
    yield {"type": "manus_result", "document": "## Page\n\n```html\n" + PAGE + "\n```\n", "task_url": "https://manus.im/app/t1", "credits": 10}


async def _manus_fails(*_a: Any, **_k: Any):
    yield {"type": "manus", "status": "started", "task_url": "https://manus.im/app/t1"}
    raise manus_page.ManusPageError("Manus could not build the page: boom")


@pytest.mark.asyncio
async def test_the_pillar_page_is_built_by_manus_and_streamed_as_ordinary_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    events, claude = await _stream(monkeypatch, "pillar_page", _manus_ok)
    assert claude == [], "Claude was asked to build a page Manus had already built"
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "<h1>Meta Ads</h1>" in text
    assert [e["status"] for e in events if e["type"] == "manus"] == ["started", "done"]
    assert events[-1] == {"type": "done", "stop_reason": "end_turn", "truncated": False}


@pytest.mark.asyncio
async def test_a_manus_failure_is_reported_and_claude_builds_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    events, claude = await _stream(monkeypatch, "pillar_page", _manus_fails)
    fallback = [e for e in events if e["type"] == "manus" and e["status"] == "fallback"]
    assert fallback and "boom" in fallback[0]["message"] and "Claude" in fallback[0]["message"]
    assert claude == ["pillar_page"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "claude page"


@pytest.mark.asyncio
async def test_no_other_stage_goes_to_manus(monkeypatch: pytest.MonkeyPatch) -> None:
    events, claude = await _stream(monkeypatch, "cro", _manus_ok)
    assert claude == ["cro"] and not any(e["type"] == "manus" for e in events)
