"""`ahrefs_client.py`, the only module that talks to Ahrefs.

Pinned against the shapes the live API returned on 2026-10-14 (see the module docstring):
  - each match class goes to its own Keywords Explorer endpoint, with a country code and a limit,
  - the bearer key is sent and never needs to be passed around,
  - a field Ahrefs has no data for stays None, never 0; CPC is cents,
  - overview sends every keyword in as few requests as the chunk allows,
  - every failure is one actionable sentence: a rejected key, no units, a rate limit, a bad country.

No network: requests go to an `httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.services import ahrefs_client as A


def _client(handler: Any) -> A.AhrefsClient:
    client = A.AhrefsClient(key="test-key")
    client._client = httpx.AsyncClient(
        base_url=A.BASE_URL,
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    return client


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_each_match_class_goes_to_its_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"keywords": [{"keyword": "meta ads agency melbourne", "volume": 150, "difficulty": None}]})

    client = _client(handler)
    for match_class in ("phrase", "related", "broad"):
        rows = _run(client.expand("meta ads agency", match_class, "au", 40))
        assert rows == [A.KeywordMetrics("meta ads agency melbourne", 150, None)]
    paths = [r.url.path.rsplit("/", 1)[1] for r in seen]
    assert paths == ["matching-terms", "related-terms", "search-suggestions"]
    params = seen[0].url.params
    assert params["keywords"] == "meta ads agency" and params["country"] == "au" and params["limit"] == "40"
    assert params["order_by"] == "volume:desc"
    assert seen[0].headers["Authorization"] == "Bearer test-key"


def test_overview_keys_by_keyword_and_keeps_missing_values_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "keywords": [
                    {"keyword": "Meta Ads Agency", "volume": 400, "difficulty": 0, "cpc": 1300, "parent_topic": "meta ads agency"},
                    {"keyword": "meta ads mcp", "volume": 200, "difficulty": None, "cpc": None, "parent_topic": None},
                    {"volume": 5},
                ]
            },
        )

    found = _run(_client(handler).overview(["meta ads agency", "meta ads mcp"], "au"))
    assert found["meta ads agency"].volume == 400
    assert found["meta ads agency"].difficulty == 0, "a measured 0 stays 0"
    assert A.cpc_dollars(found["meta ads agency"].cpc) == 13.0, "CPC arrives in cents"
    assert found["meta ads mcp"].difficulty is None and found["meta ads mcp"].cpc is None
    assert len(found) == 2, "a row with no keyword is dropped"


def test_overview_sends_every_keyword_in_as_few_requests_as_possible() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["keywords"].split(","))
        return httpx.Response(200, json={"keywords": []})

    keywords = [f"kw {i}" for i in range(A._OVERVIEW_CHUNK + 5)] + ["kw 0"]
    _run(_client(handler).overview(keywords, "au"))
    assert [len(c) for c in calls] == [A._OVERVIEW_CHUNK, 5], "deduplicated, then chunked"


@pytest.mark.parametrize(
    "status,needle",
    [
        (401, "rejected the API key"),
        (402, "out of API units"),
        (403, "out of API units"),
        (429, "rate-limited"),
        (400, "bad 'country'"),
        (500, r"failed \(500\)"),
    ],
)
def test_every_failure_is_one_actionable_sentence(status: int, needle: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "bad 'country': 'zz' is not one of [...]"})

    with pytest.raises(A.AhrefsError, match=needle):
        _run(_client(handler).overview(["meta ads"], "zz"))


def test_a_rejected_key_answers_with_a_list_and_still_reads() -> None:
    """Verified live: a bad key is a 401 whose body is `["Error","Unauthorized"]`, not an object."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=["Error", "Unauthorized"])

    with pytest.raises(A.AhrefsError, match=r"rejected the API key \(401\).*Error: Unauthorized"):
        _run(_client(handler).overview(["meta ads"], "au"))


def test_a_response_without_a_keywords_list_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rows": []})

    with pytest.raises(A.AhrefsError, match="without a keywords list"):
        _run(_client(handler).expand("meta ads", "phrase", "au", 10))


def test_no_key_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AHREFS_API_KEY", raising=False)
    assert not A.is_configured()
    with pytest.raises(A.AhrefsError, match="AHREFS_API_KEY"):
        A.AhrefsClient()


def test_country_codes_are_the_ones_ahrefs_accepts() -> None:
    assert A.country_code("Australia") == "au"
    assert A.country_code("United Kingdom") == "gb"
    assert all(len(code) == 2 and code.islower() for code in A.COUNTRIES.values())
    assert len(set(A.COUNTRIES.values())) == len(A.COUNTRIES), "one name per code"
    with pytest.raises(A.AhrefsError):
        A.country_code("Narnia")
