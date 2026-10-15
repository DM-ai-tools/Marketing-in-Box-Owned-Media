"""DataForSEO Labs — the only module that talks to DataForSEO.

Ahrefs is the keyword source for the pipeline (`ahrefs_client.py`). DataForSEO is kept for one job:
the second column on the Pillar Page's secondary-keyword card, so an operator can compare the two
providers' suggestions for the same head term and pick from either (`secondary_keywords.py`).

Credentials are HTTP Basic: `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` in the backend `.env`
(`DataForTopicClusttering_LOGIN` / `_PASSWORD`, the names they were first added under, are read too).
Server-side only, never logged.

Shapes, from the live API when this pipeline used DataForSEO for clustering:
  - `POST https://api.dataforseo.com/v3/dataforseo_labs/google/{endpoint}/live` with a one-task
    list body; `location_name` is a country name ("Australia"), `language_name` "English".
  - A request can be HTTP 200 and still have failed: the task's own `status_code` must be 20000.
  - `keyword_suggestions` items carry `keyword_info` / `keyword_properties` / `search_intent_info`
    at the top level; `related_keywords` nests the same under `keyword_data`. `_dig` reads both.
  - A missing value is null and stays None, never 0. CPC is in dollars.

Tests never reach the live API: pass an `httpx.MockTransport`.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://api.dataforseo.com/v3/"
LOGIN_ENVS = ("DATAFORSEO_LOGIN", "DataForTopicClusttering_LOGIN")
PASSWORD_ENVS = ("DATAFORSEO_PASSWORD", "DataForTopicClusttering_PASSWORD")

#: The two endpoints the secondary-keyword card uses: phrase matches, then semantic neighbours.
SUGGESTIONS = "dataforseo_labs/google/keyword_suggestions/live"
RELATED = "dataforseo_labs/google/related_keywords/live"


class DataForSEOError(RuntimeError):
    """One actionable sentence about what went wrong with DataForSEO."""


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def is_configured() -> bool:
    return bool(_first_env(LOGIN_ENVS) and _first_env(PASSWORD_ENVS))


@dataclass(frozen=True)
class DfsKeyword:
    keyword: str
    volume: int | None
    difficulty: int | None
    cpc: float | None = None
    intent: str | None = None


def _dig(item: dict, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        node: Any = item
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            return node
    return None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _keyword(item: dict) -> DfsKeyword | None:
    text = _dig(item, ("keyword",), ("keyword_data", "keyword"))
    if not isinstance(text, str) or not text.strip():
        return None
    return DfsKeyword(
        keyword=text.strip(),
        volume=_int(_dig(item, ("keyword_info", "search_volume"), ("keyword_data", "keyword_info", "search_volume"))),
        difficulty=_int(
            _dig(item, ("keyword_properties", "keyword_difficulty"), ("keyword_data", "keyword_properties", "keyword_difficulty"))
        ),
        cpc=_float(_dig(item, ("keyword_info", "cpc"), ("keyword_data", "keyword_info", "cpc"))),
        intent=_dig(item, ("search_intent_info", "main_intent"), ("keyword_data", "search_intent_info", "main_intent")),
    )


def _http_failure(response: httpx.Response) -> DataForSEOError:
    status = response.status_code
    if status == 401:
        return DataForSEOError("DataForSEO rejected the login (401). Check DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.")
    if status == 402:
        return DataForSEOError("DataForSEO says the account is out of balance (402). Top it up at app.dataforseo.com.")
    if status == 429:
        return DataForSEOError("DataForSEO is rate-limiting this account (429); try again in a minute.")
    return DataForSEOError(f"DataForSEO failed ({status}): {response.text[:200].strip()}")


class DataForSEOClient:
    def __init__(
        self,
        login: str | None = None,
        password: str | None = None,
        *,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        login = login if login is not None else _first_env(LOGIN_ENVS)
        password = password if password is not None else _first_env(PASSWORD_ENVS)
        if not (login and password):
            raise DataForSEOError("DataForSEO is not configured: set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in the backend .env.")
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, endpoint: str, task: dict[str, Any]) -> list[dict]:
        response = await self._http.post(endpoint, json=[task])
        if response.status_code >= 400:
            raise _http_failure(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise DataForSEOError("DataForSEO returned a body that is not JSON.") from exc
        tasks = body.get("tasks") if isinstance(body, dict) else None
        if not tasks:
            raise DataForSEOError(f"DataForSEO returned no task: {str(body)[:200]}")
        first = tasks[0]
        code = first.get("status_code")
        if code != 20000:
            if code == 40200:
                raise DataForSEOError("DataForSEO says the account is out of balance. Top it up at app.dataforseo.com.")
            raise DataForSEOError(f"DataForSEO {endpoint.split('/')[-2]} failed: {code} {first.get('status_message')}")
        results = first.get("result") or []
        if not results:
            return []
        return [i for i in (results[0].get("items") or []) if isinstance(i, dict)]

    async def keywords(self, endpoint: str, seed: str, location_name: str, *, limit: int = 30, language: str = "English") -> list[DfsKeyword]:
        task: dict[str, Any] = {"keyword": seed, "location_name": location_name, "language_name": language, "limit": limit}
        if endpoint == SUGGESTIONS:
            task["order_by"] = ["keyword_info.search_volume,desc"]
        else:
            # One hop of "searches related to": depth 2+ multiplies rows (and cost) and drifts.
            task["depth"] = 1
        return [k for k in (_keyword(i) for i in await self._post(endpoint, task)) if k is not None]
