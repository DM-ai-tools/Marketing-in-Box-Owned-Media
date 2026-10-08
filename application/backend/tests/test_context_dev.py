"""Tests for the Context.dev gateway and the routes over it.

Nothing here touches the live API — every call costs credits. The seam is the module-level
functions in `app/services/context_dev.py`: routes and the scraper are tested by patching those,
and the gateway's own mapping (SDK response -> dataclass, SDK exception -> message) is tested by
patching the cached client.
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import context_dev

client = TestClient(app)


# ---------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------


def test_is_configured_follows_the_env_var(monkeypatch):
    monkeypatch.delenv(context_dev.API_KEY_ENV, raising=False)
    assert context_dev.is_configured() is False
    monkeypatch.setenv(context_dev.API_KEY_ENV, "ctxt_secret_test")
    assert context_dev.is_configured() is True


def test_client_refuses_to_build_without_a_key(monkeypatch):
    monkeypatch.delenv(context_dev.API_KEY_ENV, raising=False)
    context_dev.reset_client()
    with pytest.raises(context_dev.ContextDevNotConfigured):
        context_dev._client()
    context_dev.reset_client()


# ---------------------------------------------------------------------------------------------
# scrape_markdown
# ---------------------------------------------------------------------------------------------


def _fake_client(**resources):
    """A stand-in for the cached AsyncContextDev, holding only the resources a test needs."""
    return types.SimpleNamespace(**resources)


def _md_response(markdown: str):
    return types.SimpleNamespace(
        markdown=markdown,
        metadata=types.SimpleNamespace(
            final_url="https://example.com/implants",
            source_url="https://example.com/implants",
            title="Dental Implants",
            description="Implants in Melbourne from $4,500.",
        ),
        cache_metadata=types.SimpleNamespace(age_ms=1200, status="hit"),
        key_metadata=types.SimpleNamespace(credits_consumed=1, credits_remaining=999),
    )


@pytest.mark.asyncio
async def test_scrape_markdown_maps_the_response(monkeypatch):
    captured: dict = {}

    async def fake_scrape(**kwargs):
        captured.update(kwargs)
        return _md_response("# Dental Implants\n\nReplace a missing tooth in three visits.")

    monkeypatch.setattr(
        context_dev,
        "_client",
        lambda: _fake_client(web=types.SimpleNamespace(web_scrape_md=fake_scrape)),
    )

    page = await context_dev.scrape_markdown("https://example.com/implants")

    assert page.title == "Dental Implants"
    assert page.final_url == "https://example.com/implants"
    assert page.markdown.startswith("# Dental Implants")
    assert page.cache_age_ms == 1200
    assert page.word_count == 10
    # Nav labels and button text are evidence for the CRO audit, so main-content-only stays off,
    # and no max_age_ms means the cached copy is accepted.
    assert captured["use_main_content_only"] is False
    assert "max_age_ms" not in captured


@pytest.mark.asyncio
async def test_scrape_markdown_passes_max_age_only_when_asked(monkeypatch):
    captured: dict = {}

    async def fake_scrape(**kwargs):
        captured.update(kwargs)
        return _md_response("# Fresh")

    monkeypatch.setattr(
        context_dev,
        "_client",
        lambda: _fake_client(web=types.SimpleNamespace(web_scrape_md=fake_scrape)),
    )
    await context_dev.scrape_markdown("https://example.com", max_age_ms=context_dev.FRESH_NOW_MS)
    assert captured["max_age_ms"] == context_dev.FRESH_NOW_MS


@pytest.mark.asyncio
async def test_empty_markdown_is_an_error_not_an_empty_page(monkeypatch):
    async def fake_scrape(**_):
        return _md_response("   ")

    monkeypatch.setattr(
        context_dev,
        "_client",
        lambda: _fake_client(web=types.SimpleNamespace(web_scrape_md=fake_scrape)),
    )
    with pytest.raises(context_dev.ContextDevError, match="no text content"):
        await context_dev.scrape_markdown("https://example.com")


@pytest.mark.asyncio
async def test_sdk_failures_become_one_readable_sentence(monkeypatch):
    import httpx
    from context.dev import AuthenticationError

    response = httpx.Response(401, request=httpx.Request("GET", "https://api.context.dev/v1/web/scrape/markdown"))

    async def fake_scrape(**_):
        raise AuthenticationError("bad key", response=response, body=None)

    monkeypatch.setattr(
        context_dev,
        "_client",
        lambda: _fake_client(web=types.SimpleNamespace(web_scrape_md=fake_scrape)),
    )
    with pytest.raises(context_dev.ContextDevError) as caught:
        await context_dev.scrape_markdown("https://example.com")
    assert context_dev.API_KEY_ENV in str(caught.value)


@pytest.mark.asyncio
async def test_an_exhausted_balance_is_not_reported_as_a_bad_key(monkeypatch):
    """The live API answers an empty balance with a 401 — the same status as a bad key. Reported as
    "rejected the API key", it sent an operator to rotate a key that was fine."""
    import httpx
    from context.dev import AuthenticationError

    # The real body from a live call on 2026-10-06, request id dropped.
    body = {
        "message": "Insufficient credits: this request needs 10 credits and 0 remain. To get more credits "
        "immediately, subscribe to a plan at https://www.context.dev/dashboard/subscription, or contact "
        "hello@context.dev for help.",
        "error_code": "USAGE_EXCEEDED",
        "key_metadata": {"credits_consumed": 0, "credits_remaining": 0},
    }
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.context.dev/v1/brand/retrieve"))

    async def fake_scrape(**_):
        raise AuthenticationError("Insufficient credits", response=response, body=body)

    monkeypatch.setattr(
        context_dev,
        "_client",
        lambda: _fake_client(web=types.SimpleNamespace(web_scrape_md=fake_scrape)),
    )
    with pytest.raises(context_dev.ContextDevError) as caught:
        await context_dev.scrape_markdown("https://example.com")

    message = str(caught.value)
    assert "out of credits" in message
    assert "https://www.context.dev/dashboard/subscription" in message
    assert "rejected the API key" not in message


# ---------------------------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_flattens_the_nested_markdown(monkeypatch):
    captured: dict = {}

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            query=kwargs["query"],
            results=[
                types.SimpleNamespace(
                    title="Melbourne Implants",
                    url="https://rival.example/implants",
                    description="From $3,900.",
                    relevance=0.91,
                    markdown=types.SimpleNamespace(code=200, markdown="# Implants\n\nFrom $3,900."),
                )
            ],
            key_metadata=types.SimpleNamespace(credits_consumed=1, credits_remaining=998),
        )

    monkeypatch.setattr(
        context_dev, "_client", lambda: _fake_client(web=types.SimpleNamespace(search=fake_search))
    )

    results = await context_dev.search("dental implants melbourne", country="au", num_results=10)

    assert len(results) == 1
    assert results[0].markdown == "# Implants\n\nFrom $3,900."
    assert results[0].relevance == 0.91
    assert captured["country"] == "au"
    # Optional filters are omitted rather than sent empty, so the API applies its own defaults.
    assert "include_domains" not in captured
    assert "freshness" not in captured


# ---------------------------------------------------------------------------------------------
# retrieve_brand
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_brand_pulls_the_fields_the_pipeline_reads(monkeypatch):
    brand = {
        "domain": "airbnb.com",
        "title": "Airbnb",
        "description": "A marketplace connecting hosts with guests.",
        "slogan": "Belong Anywhere",
        "colors": [{"hex": "#fc3c5c", "name": "Radical Red"}, {"no_hex": True}],
        "logos": [{"url": "https://cdn.example/logo.png", "type": "icon"}, {"type": "logo"}],
        "socials": [{"type": "x", "url": "https://x.com/airbnb"}],
        "industries": {"eic": [{"industry": "Hospitality", "subindustry": "Short-Term Stays"}]},
    }

    async def fake_retrieve(**_):
        return types.SimpleNamespace(
            brand=types.SimpleNamespace(model_dump=lambda: brand),
            key_metadata=types.SimpleNamespace(credits_consumed=10, credits_remaining=988),
        )

    monkeypatch.setattr(
        context_dev,
        "_client",
        lambda: _fake_client(brand=types.SimpleNamespace(retrieve=fake_retrieve)),
    )

    record = await context_dev.retrieve_brand("airbnb.com")

    assert record.title == "Airbnb"
    assert record.slogan == "Belong Anywhere"
    # Entries missing the field that makes them useful are dropped, not carried through as None.
    assert record.colors == ("#fc3c5c",)
    assert record.logos == (("icon", "https://cdn.example/logo.png"),)
    assert record.socials == (("x", "https://x.com/airbnb"),)
    assert record.industries == ("Hospitality / Short-Term Stays",)


# ---------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------


def test_status_reports_missing_configuration(monkeypatch):
    monkeypatch.setattr(context_dev, "is_configured", lambda: False)
    body = client.get("/intel/status").json()
    assert body["configured"] is False
    assert context_dev.API_KEY_ENV in body["detail"]


def test_search_route_returns_hits(monkeypatch):
    async def fake_search(query, **kwargs):
        assert kwargs["num_results"] == 5
        return [
            context_dev.SearchResult(
                title="Rival", url="https://rival.example", description="d", markdown="body", relevance=0.8
            )
        ]

    monkeypatch.setattr(context_dev, "search", fake_search)
    response = client.post("/intel/search", json={"query": "dental implants melbourne", "num_results": 5})
    assert response.status_code == 200
    assert response.json()["results"][0]["content"] == "body"


def test_search_route_can_drop_the_page_copy(monkeypatch):
    async def fake_search(query, **kwargs):
        return [context_dev.SearchResult(title="Rival", url="https://rival.example", description=None, markdown="body")]

    monkeypatch.setattr(context_dev, "search", fake_search)
    response = client.post("/intel/search", json={"query": "x y", "include_content": False})
    assert response.json()["results"][0]["content"] is None


def test_search_route_caps_the_credit_spend():
    response = client.post("/intel/search", json={"query": "x y", "num_results": 500})
    assert response.status_code == 422


def test_brand_route_maps_a_missing_key_to_503(monkeypatch):
    async def fake_brand(domain):
        raise context_dev.ContextDevNotConfigured("CONTEXT_DEV_API_KEY is not set")

    monkeypatch.setattr(context_dev, "retrieve_brand", fake_brand)
    assert client.get("/intel/brand", params={"domain": "airbnb.com"}).status_code == 503


def test_brand_route_maps_a_lookup_failure_to_422(monkeypatch):
    async def fake_brand(domain):
        raise context_dev.ContextDevError("Context.dev has no brand record for nope.example.")

    monkeypatch.setattr(context_dev, "retrieve_brand", fake_brand)
    response = client.get("/intel/brand", params={"domain": "nope.example"})
    assert response.status_code == 422
    assert "no brand record" in response.json()["detail"]
