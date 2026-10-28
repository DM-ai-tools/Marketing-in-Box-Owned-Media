"""The Plan of Action's sub-services: read off the client's site, suggested from the competitor
research, measured with DataForSEO.

Pinned here:
  - a sub-service is reported as existing only when the site links to it,
  - volumes are DataForSEO's or None — never the stub provider's numbers, never 0,
  - one DataForSEO request covers every keyword on the card,
  - any DataForSEO failure keeps the list and sets `volume_error`, which the card shows as a warning,
  - an unreadable site still gets suggestions, and says why nothing existing is listed,
  - the route answers with the shape the card reads, and a judge failure is a 422,
  - the stored list's line shape is the one the frontend composes (`composePlanSubServicesAnswer`).

No network and no model call: the scan, the judge and the provider are patched.
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import dataforseo_client
from app.services import keywords as K
from app.services import service_scan
from app.services import sub_service_discovery as D

_PIPELINE_DATA = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pipeline" / "pipelineData.ts").read_text(
    encoding="utf-8"
)

_SCANNED = [
    service_scan.ScannedService("Meta Ads", "https://trafficradius.com.au/meta-ads/", "Social Media Marketing"),
    service_scan.ScannedService("LinkedIn Ads", "https://trafficradius.com.au/linkedin-ads/", "Social Media Marketing"),
]

_REPLY = {
    "sub_services": [
        {"name": "TikTok Ads", "status": "new", "slug": "/tiktok-ads/", "why": "Two competitors have a page.",
         "keywords": ["tiktok ads agency", "tiktok advertising"]},
        {"name": "Meta Ads", "status": "existing", "url": "https://trafficradius.com.au/meta-ads", "why": "",
         "keywords": ["meta ads agency", "facebook ads agency", "Meta Ads Agency"]},
        {"name": "Instagram Marketing", "status": "existing", "url": "https://trafficradius.com.au/instagram/",
         "keywords": ["instagram marketing agency"]},
        {"name": "LinkedIn Ads", "status": "existing", "url": "https://trafficradius.com.au/linkedin-ads/",
         "keywords": []},
    ]
}


def _patch_judge(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any] | str = _REPLY) -> list[str]:
    prompts: list[str] = []

    async def ask(prompt: str, on_usage: Any) -> str:
        prompts.append(prompt)
        return reply if isinstance(reply, str) else json.dumps(reply)

    monkeypatch.setattr(D, "_ask", ask)
    return prompts


def _patch_scan(monkeypatch: pytest.MonkeyPatch, services: list[service_scan.ScannedService] | None = None) -> None:
    async def scan(url: str, *, phase: str, focus: str, on_usage: Any) -> service_scan.ServiceScan:
        assert phase == "phase2" and focus == "Social Media Marketing"
        return service_scan.ServiceScan(source_url=url, services=list(_SCANNED if services is None else services))

    monkeypatch.setattr(service_scan, "scan_services", scan)


# --------------------------------------------------------------------------------------
# The judge's reply
# --------------------------------------------------------------------------------------


def test_existing_needs_a_url_the_site_links_to() -> None:
    candidates, notes = D._parse_reply(json.dumps(_REPLY), _SCANNED)
    by_name = {c.name: c for c in candidates}
    assert by_name["Meta Ads"].status == D.EXISTING
    assert by_name["Meta Ads"].url == "https://trafficradius.com.au/meta-ads/", "the scan's URL, not the model's copy"
    assert by_name["Meta Ads"].slug == "/meta-ads/"
    assert by_name["Instagram Marketing"].status == D.NEW, "the site does not link to it"
    assert by_name["Instagram Marketing"].url == ""
    assert any("Instagram Marketing" in n for n in notes)


def test_existing_come_first_and_keywords_are_tidied() -> None:
    candidates, _ = D._parse_reply(json.dumps(_REPLY), _SCANNED)
    statuses = [c.status for c in candidates]
    assert statuses == sorted(statuses, key=lambda s: s != D.EXISTING)
    meta = next(c for c in candidates if c.name == "Meta Ads")
    assert [k.keyword for k in meta.keywords] == ["meta ads agency", "facebook ads agency"], "deduplicated, lowercased"
    linkedin = next(c for c in candidates if c.name == "LinkedIn Ads")
    assert [k.keyword for k in linkedin.keywords] == ["linkedin ads"], "a candidate always has its own name to measure"


def test_the_list_is_capped() -> None:
    many = {"sub_services": [{"name": f"Sub {i}", "status": "new", "keywords": [f"k{i}"]} for i in range(30)]}
    candidates, _ = D._parse_reply(json.dumps(many), [])
    assert len(candidates) == D.MAX_CANDIDATES


def test_an_unreadable_reply_is_an_error() -> None:
    with pytest.raises(D.DiscoveryError):
        D._parse_reply("Sure! Here are some ideas.", [])


def test_the_judge_is_shown_the_site_the_competitors_and_the_icp(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_scan(monkeypatch)
    prompts = _patch_judge(monkeypatch)
    for name in (*dataforseo_client.LOGIN_ENVS, *dataforseo_client.PASSWORD_ENVS):
        monkeypatch.delenv(name, raising=False)
    D_run(D.discover("https://trafficradius.com.au/", "Social Media Marketing", region="Australia",
                     competitors="## cro\nRival Co has a TikTok Ads page.", icp="Ecommerce founders."))
    prompt = prompts[0]
    assert "https://trafficradius.com.au/meta-ads/" in prompt
    assert "Rival Co has a TikTok Ads page." in prompt
    assert "Ecommerce founders." in prompt


# --------------------------------------------------------------------------------------
# Volumes
# --------------------------------------------------------------------------------------


def D_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def test_no_key_means_no_volumes_and_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_scan(monkeypatch)
    _patch_judge(monkeypatch)
    for name in (*dataforseo_client.LOGIN_ENVS, *dataforseo_client.PASSWORD_ENVS):
        monkeypatch.delenv(name, raising=False)
    found = D_run(D.discover("https://trafficradius.com.au/", "Social Media Marketing"))
    assert found.volume_source == "unavailable"
    assert all(k.volume is None for c in found.candidates for k in c.keywords), "never the stub provider's numbers"
    assert all(c.combined_volume is None for c in found.candidates)
    assert "DATAFORSEO_LOGIN" in found.volume_error, "the card warns with the reason"


class _FakeDfs:
    instances: list["_FakeDfs"] = []
    fail: Exception | None = None

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.closed = False
        _FakeDfs.instances.append(self)

    async def overview(self, keywords: list[str], country: str) -> dict[str, dataforseo_client.DfsKeyword]:
        self.calls.append((keywords, country))
        if _FakeDfs.fail is not None:
            raise _FakeDfs.fail
        rows = {"meta ads agency": 880, "facebook ads agency": 590, "tiktok ads agency": 320}
        return {k: dataforseo_client.DfsKeyword(k, v, 10) for k, v in rows.items()}

    async def aclose(self) -> None:
        self.closed = True


def _patch_dfs(monkeypatch: pytest.MonkeyPatch, fail: Exception | None = None) -> None:
    _FakeDfs.instances.clear()
    _FakeDfs.fail = fail
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: True)
    monkeypatch.setattr(dataforseo_client, "DataForSEOClient", _FakeDfs)


def test_a_provider_failure_keeps_the_list_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_scan(monkeypatch)
    _patch_judge(monkeypatch)
    _patch_dfs(monkeypatch, dataforseo_client.DataForSEOError("DataForSEO rejected the login (401)."))
    found = D_run(D.discover("https://trafficradius.com.au/", "Social Media Marketing", region="Australia"))
    assert found.candidates, "the suggestions still stand"
    assert found.volume_source == "unavailable"
    assert all(k.volume is None for c in found.candidates for k in c.keywords)
    assert "rejected the login (401)" in found.volume_error
    assert _FakeDfs.instances[0].closed, "the client is closed on failure too"


def test_a_region_with_no_country_is_a_warning_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_scan(monkeypatch)
    _patch_judge(monkeypatch)
    _patch_dfs(monkeypatch)
    found = D_run(D.discover("https://trafficradius.com.au/", "Social Media Marketing", region="Sydney NSW"))
    assert found.candidates and found.volume_source == "unavailable"
    assert "does not name a country" in found.volume_error
    assert not _FakeDfs.instances, "nothing is sent for a region with no country"


def test_one_request_measures_every_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_scan(monkeypatch)
    _patch_judge(monkeypatch)
    _patch_dfs(monkeypatch)
    found = D_run(D.discover("https://trafficradius.com.au/", "Social Media Marketing", region="Melbourne VIC Australia"))

    (client,) = _FakeDfs.instances
    assert len(client.calls) == 1 and client.closed
    assert client.calls[0][1] == "Australia", "the region becomes a country"
    assert found.volume_source == "dataforseo" and found.location == "Australia"
    assert found.volume_error == ""
    meta = next(c for c in found.candidates if c.name == "Meta Ads")
    assert meta.combined_volume == 880 + 590
    tiktok = next(c for c in found.candidates if c.name == "TikTok Ads")
    assert [k.volume for k in tiktok.keywords] == [320, None], "an unmeasured keyword stays None, not 0"
    linkedin = next(c for c in found.candidates if c.name == "LinkedIn Ads")
    assert linkedin.combined_volume is None


def test_an_unreadable_site_still_gets_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scan(url: str, **_: Any) -> service_scan.ServiceScan:
        raise service_scan.ServiceScanError("Could not read it")

    monkeypatch.setattr(service_scan, "scan_services", scan)
    _patch_judge(monkeypatch, {"sub_services": [{"name": "TikTok Ads", "status": "existing", "url": "https://x/", "keywords": ["tiktok ads"]}]})
    for name in (*dataforseo_client.LOGIN_ENVS, *dataforseo_client.PASSWORD_ENVS):
        monkeypatch.delenv(name, raising=False)
    found = D_run(D.discover("https://trafficradius.com.au/", "Social Media Marketing"))
    assert [c.status for c in found.candidates] == [D.NEW]
    assert any("could not be read" in n for n in found.notes)


def test_a_missing_primary_service_is_refused() -> None:
    with pytest.raises(D.DiscoveryError):
        D_run(D.discover("https://x.com/", "  "))


# --------------------------------------------------------------------------------------
# What is read from the run, and what is stored
# --------------------------------------------------------------------------------------


def test_competitor_block_keeps_order_and_bounds_each_listing() -> None:
    block = D.competitor_block(
        {"competitor_analysis_cro": "B" * 10_000, "competitor_analysis_seo_pillar_page": "A listing.", "other": "x"}
    )
    assert block.index("seo pillar page") < block.index("## cro")
    assert len(block) < D._LISTING_CHARS * 2 + 200
    assert "other" not in block


def test_stored_names_read_the_frontends_line_shape() -> None:
    content = (
        "Volume source: DataForSEO, monthly Google searches in Australia.\n"
        "- Meta Ads — EXISTING PAGE — enhance: https://trafficradius.com.au/meta-ads/ — keywords: meta ads agency (880/mo)\n"
        "- TikTok Ads — NEW PAGE — proposed slug: /tiktok-ads/\n"
        "- Pinterest Ads — NEW PAGE (typed by the operator; keywords not measured)\n"
    )
    assert D.names_for_phase2(content) == ["Meta Ads", "TikTok Ads", "Pinterest Ads"]


def test_the_frontend_composes_the_shape_the_server_reads() -> None:
    composer = _PIPELINE_DATA.split("export function composePlanSubServicesAnswer", 1)[1].split("\n}\n", 1)[0]
    assert "`- ${c.name} — ${where}" in composer
    assert "`- ${name} — NEW PAGE (typed by the operator" in composer
    reader = _PIPELINE_DATA.split("export function planSubServiceNames", 1)[1].split("\n}\n", 1)[0]
    assert r"/^\s*-\s+(.+?)(?:\s+—\s+|$)/" in reader
    server = Path(D.__file__).read_text(encoding="utf-8").split("def names_for_phase2", 1)[1]
    assert r'r"^\s*-\s+(.+?)(?:\s+—\s+|$)"' in server, "the two readers use one pattern"


# --------------------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------------------


def test_the_route_returns_the_cards_shape_and_422s_a_judge_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok(url: str, primary_service: str, **kwargs: Any) -> D.Discovery:
        assert primary_service == "Social Media Marketing" and kwargs["region"] == "Australia"
        return D.Discovery(
            source_url=url,
            primary_service=primary_service,
            candidates=[D.SubServiceCandidate("Meta Ads", D.EXISTING, url + "meta-ads/", "/meta-ads/",
                                              keywords=[D.KeywordVolume("meta ads agency", 880)])],
            volume_source="dataforseo",
            location="Australia",
        )

    monkeypatch.setattr(D, "discover", ok)
    body = {"url": "https://trafficradius.com.au/", "primary_service": "Social Media Marketing", "region": "Australia"}
    res = TestClient(app).post("/pipeline/plan/sub-services/discover", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["volume_source"] == "dataforseo"
    assert data["candidates"][0]["combined_volume"] == 880
    assert data["candidates"][0]["keywords"] == [{"keyword": "meta ads agency", "volume": 880}]

    async def fail(*_: Any, **__: Any) -> D.Discovery:
        raise D.DiscoveryError("The sub-service suggestion could not run: boom")

    monkeypatch.setattr(D, "discover", fail)
    res = TestClient(app).post("/pipeline/plan/sub-services/discover", json=body)
    assert res.status_code == 422 and "boom" in res.json()["detail"]
