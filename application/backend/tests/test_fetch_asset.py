"""`scraper.fetch_asset` — the one request policy for the design reader's stylesheets and logo.

The design reader used to open its own httpx client for these: a hard-coded browser agent, httpx's
default of 20 redirects, and no host check on any hop. Each test pins one part of the shared policy.
No network: the transport is an `httpx.MockTransport` and host resolution is patched.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import scraper


def _install(monkeypatch, handler, *, private_hosts=()):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(scraper.httpx, "AsyncClient", client)

    def check(url: str) -> None:
        host = httpx.URL(url).host
        if host in private_hosts:
            raise scraper.ScrapeError(f"{host!r} resolves to a non-public address")

    monkeypatch.setattr(scraper, "_assert_public_host", check)


@pytest.mark.asyncio
async def test_the_honest_agent_goes_first_and_the_browser_only_after_a_bot_wall(monkeypatch):
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["user-agent"])
        if len(agents) == 1:
            return httpx.Response(403)
        return httpx.Response(200, text="body{color:red}", headers={"content-type": "text/css"})

    _install(monkeypatch, handler)
    sheet = await scraper.fetch_asset("https://site.example/a.css", accept="text/css", referer="https://site.example/")
    assert agents == [scraper._USER_AGENT, scraper._BROWSER_USER_AGENT]
    assert sheet.content == b"body{color:red}" and sheet.content_type == "text/css"


@pytest.mark.asyncio
async def test_every_redirect_hop_is_checked_and_the_cap_is_the_scrapers(monkeypatch):
    def inward(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://internal.example/secret.css"})

    _install(monkeypatch, inward, private_hosts=("internal.example",))
    with pytest.raises(scraper.ScrapeError, match="non-public"):
        await scraper.fetch_asset("https://site.example/a.css", accept="text/css")

    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://site.example/again.css"})

    _install(monkeypatch, loop)
    with pytest.raises(scraper.ScrapeError, match=f"more than {scraper._MAX_REDIRECTS}"):
        await scraper.fetch_asset("https://site.example/a.css", accept="text/css")


@pytest.mark.asyncio
async def test_a_challenge_header_is_a_failure_not_a_file(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, text="<meta http-equiv=refresh>", headers={"SG-Captcha": "challenge"})

    _install(monkeypatch, handler)
    with pytest.raises(scraper.ScrapeError, match="challenged"):
        await scraper.fetch_asset("https://site.example/logo.svg", accept="image/*")


@pytest.mark.asyncio
async def test_the_design_reader_uses_it_for_stylesheets(monkeypatch):
    from app.services import design_tokens

    seen: list[str] = []

    async def fake_fetch(url, *, accept, referer=None, max_bytes=0):
        seen.append(url)
        return scraper.FetchedAsset(url=url, content=b":root{--brand:#e8590c}", content_type="text/css", encoding="utf-8")

    monkeypatch.setattr(design_tokens, "fetch_asset", fake_fetch)
    texts, _notes = await design_tokens._fetch_stylesheets(["https://site.example/a.css"], "https://site.example/")
    assert seen == ["https://site.example/a.css"]
    assert texts == [":root{--brand:#e8590c}"]
