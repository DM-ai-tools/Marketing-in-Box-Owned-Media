"""Manus (API v2) — the only module that talks to Manus.

Manus is an agent, not a completion endpoint: `task.create` starts a task that runs for minutes in
Manus's own sandbox, and the result is read back by polling `task.listMessages` until the agent's
`status_update` says `stopped` (or `error`). Files the agent writes arrive as `attachments` on its
`assistant_message` events, each with a download `url`.

Like `firecrawl_client.py` and `sociavault_client.py`, this wrapper is dumb: it creates tasks,
reads events and downloads files. Deciding what to ask for and what counts as a usable page lives
in `manus_page.py`.

The key is `MANUS_API_KEY` in the backend `.env` (`manus` is read too, which is how it was first
added). Server-side only, sent as the `x-manus-api-key` header, never logged.

Docs: <https://open.manus.ai/docs/v2/introduction>. Shapes coded against:
  - `POST /v2/task.create` → `{ok, task_id, task_url, ...}`; text in one request is capped at about
    5,000 tokens, so a long brief travels as a `file` content part (`file_data`, a base64 data URL).
  - `GET /v2/task.listMessages?task_id=&order=asc&limit=&cursor=` → `{messages: [TaskEvent], has_more,
    next_cursor}`; `status_update.agent_status` is `running` | `stopped` | `waiting` | `error`.
  - `GET /v2/task.detail?task_id=` → `{task: {status, has_running_background_jobs, credit_usage}}`;
    `has_running_background_jobs` decides whether `stopped` is final.
  - Every error body is `{ok: false, error: {code, message}}`; 429 is `rate_limited` (task creation
    is limited to 10 a minute per user, reads to 100).

Tests never reach the live API: patch `ManusClient` or pass an `httpx.MockTransport`.
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.manus.ai/v2/"
API_KEY_ENVS = ("MANUS_API_KEY", "manus")

#: `agent_profile` on `task.create`. Overridable with `MANUS_AGENT_PROFILE` (`lite`, `max`, ...).
DEFAULT_AGENT_PROFILE = "standard"

#: A downloaded file larger than this is refused rather than held in memory.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


class ManusError(RuntimeError):
    """One actionable sentence about what went wrong with Manus."""


def api_key() -> str:
    for name in API_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def is_configured() -> bool:
    return bool(api_key())


def agent_profile() -> str:
    return os.environ.get("MANUS_AGENT_PROFILE", "").strip() or DEFAULT_AGENT_PROFILE


@dataclass(frozen=True)
class Attachment:
    type: str
    filename: str
    url: str
    content_type: str = ""


@dataclass
class TaskEvent:
    id: str
    type: str
    #: `status_update.agent_status` for a status event, else "".
    status: str = ""
    #: The short line Manus shows for what it is doing ("Writing the hero section").
    brief: str = ""
    #: `assistant_message.content` / `error_message.content`.
    content: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    #: For `waiting`: what the agent is waiting on (`messageAskUser`, an action type, ...).
    waiting_for: str = ""


@dataclass(frozen=True)
class TaskDetail:
    #: `running` | `stopped` | `waiting` | `error`.
    status: str
    #: `has_running_background_jobs`: when true, a `stopped` task may still add results.
    background: bool
    #: Credits the task has used so far, when Manus reports it.
    credit_usage: float | None = None


@dataclass(frozen=True)
class CreatedTask:
    task_id: str
    task_url: str


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300].strip()
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return f"{err.get('code') or ''}: {err.get('message') or ''}".strip(": ")
        return str(body.get("message") or "")[:300]
    return str(body)[:300]


def _fail(endpoint: str, response: httpx.Response) -> ManusError:
    detail = _error_detail(response)
    status = response.status_code
    if status == 401:
        return ManusError(f"Manus rejected the API key (401). Check MANUS_API_KEY. {detail}".strip())
    if status in (402, 403):
        return ManusError(
            f"Manus refused {endpoint} ({status}): the account may be out of credits or lack API access. {detail}".strip()
        )
    if status == 429:
        return ManusError(f"Manus is rate-limiting this account (429); try again in a minute. {detail}".strip())
    return ManusError(f"Manus {endpoint} failed ({status}): {detail}".strip())


def _attachments(raw: Any) -> list[Attachment]:
    out: list[Attachment] = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        out.append(
            Attachment(
                type=str(item.get("type") or "file"),
                filename=str(item.get("filename") or ""),
                url=str(item["url"]),
                content_type=str(item.get("content_type") or ""),
            )
        )
    return out


def _event(raw: dict[str, Any]) -> TaskEvent:
    kind = str(raw.get("type") or "")
    event = TaskEvent(id=str(raw.get("id") or ""), type=kind)
    payload = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
    if kind == "status_update":
        event.status = str(payload.get("agent_status") or "")
        event.brief = str(payload.get("brief") or payload.get("description") or "")
        detail = payload.get("status_detail") if isinstance(payload.get("status_detail"), dict) else {}
        event.waiting_for = str(detail.get("waiting_for_event_type") or "")
    elif kind in ("assistant_message", "error_message", "user_message"):
        event.content = str(payload.get("content") or "")
        event.attachments = _attachments(payload.get("attachments"))
    return event


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def file_part(filename: str, data: bytes, mime_type: str) -> dict[str, Any]:
    """An attached file, inline as a base64 data URL (up to 20 MB decoded)."""
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "file", "filename": filename, "mime_type": mime_type, "file_data": f"data:{mime_type};base64,{encoded}"}


def url_part(url: str, filename: str = "") -> dict[str, Any]:
    part: dict[str, Any] = {"type": "file", "file_url": url}
    if filename:
        part["filename"] = filename
    return part


class ManusClient:
    def __init__(self, key: str | None = None, *, timeout: float = 60, transport: httpx.AsyncBaseTransport | None = None):
        key = key if key is not None else api_key()
        if not key:
            raise ManusError("Manus is not configured: set MANUS_API_KEY in the backend .env.")
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"x-manus-api-key": key, "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        # Downloads go to a separate host (signed file URLs), so they must not carry the key.
        self._files = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=True)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._files.aclose()

    async def _json(self, endpoint: str, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise _fail(endpoint, response)
        try:
            body = response.json()
        except ValueError as exc:
            raise ManusError(f"Manus {endpoint} returned a body that is not JSON.") from exc
        if not isinstance(body, dict):
            raise ManusError(f"Manus {endpoint} returned an unexpected body.")
        if body.get("ok") is False:
            raise ManusError(f"Manus {endpoint} failed: {_error_detail(response)}")
        return body

    async def create_task(
        self,
        content: list[dict[str, Any]],
        *,
        title: str = "",
        profile: str | None = None,
        hide_in_task_list: bool = False,
        project_id: str | None = None,
    ) -> CreatedTask:
        payload: dict[str, Any] = {
            "message": {"content": content},
            "agent_profile": profile or agent_profile(),
            "interactive_mode": False,
            "hide_in_task_list": hide_in_task_list,
        }
        if project_id:
            # Files the task under that Manus project and applies its instruction and defaults.
            payload["project_id"] = project_id
        if title:
            payload["title"] = title[:120]
        body = await self._json("task.create", await self._http.post("task.create", json=payload))
        task_id = str(body.get("task_id") or "")
        if not task_id:
            raise ManusError("Manus task.create returned no task_id.")
        return CreatedTask(task_id=task_id, task_url=str(body.get("task_url") or f"https://manus.im/app/{task_id}"))

    async def list_messages(self, task_id: str, *, cursor: str | None = None, limit: int = 100) -> tuple[list[TaskEvent], str | None]:
        """One page, oldest first. Returns (events, next_cursor or None)."""
        params: dict[str, Any] = {"task_id": task_id, "order": "asc", "limit": limit}
        if cursor:
            params["cursor"] = cursor
        body = await self._json("task.listMessages", await self._http.get("task.listMessages", params=params))
        events = [_event(m) for m in body.get("messages") or [] if isinstance(m, dict)]
        return events, (str(body["next_cursor"]) if body.get("has_more") and body.get("next_cursor") else None)

    async def all_messages(self, task_id: str) -> list[TaskEvent]:
        events: list[TaskEvent] = []
        cursor: str | None = None
        for _ in range(50):  # bounded: 50 pages of 100 is far beyond any one task
            page, cursor = await self.list_messages(task_id, cursor=cursor)
            events.extend(page)
            if not cursor:
                break
        return events

    async def detail(self, task_id: str) -> TaskDetail:
        body = await self._json("task.detail", await self._http.get("task.detail", params={"task_id": task_id}))
        task = body.get("task") if isinstance(body.get("task"), dict) else {}
        usage = task.get("credit_usage")
        return TaskDetail(
            status=str(task.get("status") or ""),
            background=bool(task.get("has_running_background_jobs")),
            credit_usage=usage if isinstance(usage, (int, float)) else None,
        )

    async def send_message(self, task_id: str, text: str) -> None:
        payload = {"task_id": task_id, "message": {"content": text}}
        await self._json("task.sendMessage", await self._http.post("task.sendMessage", json=payload))

    async def stop(self, task_id: str) -> None:
        try:
            await self._json("task.stop", await self._http.post("task.stop", json={"task_id": task_id}))
        except (ManusError, httpx.HTTPError) as exc:  # best effort: the caller is already giving up
            logger.warning("Manus task.stop failed for %s: %s", task_id, exc)

    async def download(self, attachment: Attachment) -> bytes:
        response = await self._files.get(attachment.url)
        if response.status_code >= 400:
            raise ManusError(f"Could not download {attachment.filename or 'a Manus file'} ({response.status_code}).")
        if len(response.content) > MAX_DOWNLOAD_BYTES:
            raise ManusError(f"{attachment.filename or 'A Manus file'} is larger than {MAX_DOWNLOAD_BYTES // 1_000_000} MB.")
        return response.content
