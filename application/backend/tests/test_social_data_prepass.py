"""Tests for `app/routers/pipeline.py`'s `_run_social_data_prepass`.

The regression this file exists to prevent: Stage 10 kept returning every row as `N/D` even when
the operator had supplied real handles in `client_s_own_social_pages_handles`, because nothing
actually called SociaVault before the stage generated — the field's only other option, "research
live via browser", is not something the model can execute. This prepass is what closes that gap.
"""

from __future__ import annotations

import pytest

from app.services import social_audit, sociavault_client
from app.services.sociavault_client import SocialFetchResult


@pytest.mark.asyncio
async def test_skips_entirely_for_a_different_asset():
    from app.routers.pipeline import _run_social_data_prepass

    answers = {"client_s_own_social_pages_handles": "Instagram: @acme"}
    resolved, event = await _run_social_data_prepass("pillar_page", answers)

    assert resolved is answers
    assert event is None


@pytest.mark.asyncio
async def test_a_real_answer_already_supplied_is_left_untouched(monkeypatch):
    from app.routers.pipeline import _run_social_data_prepass

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("should not fetch when the operator already answered")

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fail_if_called)

    answers = {
        "raw_post_data_source": "Attached: q3_posts_export.csv, 240 rows",
        "client_s_own_social_pages_handles": "Instagram: @acme",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert resolved["raw_post_data_source"] == "Attached: q3_posts_export.csv, 240 rows"
    assert event is None


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["", "Research live via browser, 40 per platform", "  "])
async def test_runs_when_the_field_is_blank_or_asks_for_browser_research(monkeypatch, existing):
    from app.routers.pipeline import _run_social_data_prepass

    async def fake_snapshot(accounts, *, limit_per_platform=40):
        return (
            SocialFetchResult(platform="instagram", handle="acme", posts=(), more_available=False, credits_used=1),
        ), ()

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_snapshot)

    answers = {"raw_post_data_source": existing, "client_s_own_social_pages_handles": "Instagram: @acme"}
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert event is not None
    assert event["type"] == "social_prepass"


@pytest.mark.asyncio
async def test_fills_the_field_with_real_data_and_flags_competitors_as_not_covered(monkeypatch):
    from app.routers.pipeline import _run_social_data_prepass

    async def fake_snapshot(accounts, *, limit_per_platform=40):
        assert accounts == (social_audit.SocialAccount(platform="instagram", handle="acme"),)
        result = SocialFetchResult(
            platform="instagram", handle="acme", posts=(), more_available=False, credits_used=1, note="no posts"
        )
        return (result,), ("instagram (acme): no posts",)

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_snapshot)

    answers = {
        "client_name": "ARG Finance",
        "raw_post_data_source": "",
        "client_s_own_social_pages_handles": "Instagram: @acme",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert event["skipped"] is False
    assert "ARG Finance (client)" in resolved["raw_post_data_source"]
    assert "Competitor accounts" in resolved["raw_post_data_source"]
    assert "N/D" in resolved["raw_post_data_source"]


@pytest.mark.asyncio
async def test_reports_a_skip_when_no_handles_are_recognisable(monkeypatch):
    from app.routers.pipeline import _run_social_data_prepass

    answers = {"raw_post_data_source": "", "client_s_own_social_pages_handles": "YouTube: @acme only"}
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert resolved is answers  # untouched — falls through to whatever the operator answered
    assert event["skipped"] is True
    assert "no handle" in event["error"].lower() or "no facebook" in event["error"].lower()


_COMPETITOR_LIST_JSON = """
{
  "competitors": [
    {"rank": 1, "domain": "entourage.com.au", "name": "Entourage", "page_url": "https://entourage.com.au", "verification_confidence": "Verified"},
    {"rank": 2, "domain": "axtonfinance.com.au", "name": "Axton Finance", "page_url": "https://axtonfinance.com.au", "verification_confidence": "Verified"}
  ],
  "notes": null
}
"""


@pytest.mark.asyncio
async def test_competitor_accounts_are_resolved_and_fetched_automatically(monkeypatch):
    """The end-to-end fix: a real competitor_list with domains must produce real competitor data,
    not just the client's own — this is what 'add the competitor-handles field, via Firecrawl/
    Context.dev' actually asked for."""
    from app.routers.pipeline import _run_social_data_prepass

    async def fake_client_snapshot(accounts, *, limit_per_platform=40):
        return (
            SocialFetchResult(platform="instagram", handle="acme", posts=(), more_available=False, credits_used=1),
        ), ()

    async def fake_resolve(domain):
        assert domain in ("entourage.com.au", "axtonfinance.com.au")
        return (social_audit.SocialAccount(platform="instagram", handle=f"https://instagram.com/{domain}"),)

    calls = {"snapshot": 0}

    async def fake_snapshot(accounts, *, limit_per_platform=40):
        calls["snapshot"] += 1
        if limit_per_platform == 40:
            return await fake_client_snapshot(accounts, limit_per_platform=limit_per_platform)
        # Competitor calls use the lower, credit-conscious limit.
        assert limit_per_platform == 15
        return (
            SocialFetchResult(
                platform=accounts[0].platform, handle=accounts[0].handle, posts=(), more_available=False, credits_used=1
            ),
        ), ()

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_snapshot)
    monkeypatch.setattr(social_audit, "resolve_competitor_handles", fake_resolve)

    answers = {
        "client_name": "ARG Finance",
        "raw_post_data_source": "",
        "client_s_own_social_pages_handles": "Instagram: @acme",
        "competitor_list": _COMPETITOR_LIST_JSON,
        "number_of_competitors_to_audit": "5",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    document = resolved["raw_post_data_source"]
    assert event["skipped"] is False
    assert "Entourage" in document
    assert "Axton Finance" in document
    assert "instagram.com/entourage.com.au" in document
    assert calls["snapshot"] == 3  # client + 2 competitors


@pytest.mark.asyncio
async def test_competitors_are_fetched_from_the_listing_as_the_stage_actually_receives_it(monkeypatch):
    """The field is filled from the approved listing's context entry, which is `to_prompt_text`'s
    prose — not the search JSON the test above feeds it. That is the only shape a real run sends, and
    while this prepass could read only JSON, every competitor was N/D on every real run."""
    from app.routers.pipeline import _run_social_data_prepass
    from app.services.competitor import parse_analysis, to_prompt_text

    prose = to_prompt_text(
        parse_analysis("competitor_analysis_social_content_strategy", _COMPETITOR_LIST_JSON),
        "https://argfinance.com.au",
    )
    resolved_domains = []

    async def fake_resolve(domain):
        resolved_domains.append(domain)
        return (social_audit.SocialAccount(platform="instagram", handle=f"https://instagram.com/{domain}"),)

    async def fake_snapshot(accounts, *, limit_per_platform=40):
        return (
            SocialFetchResult(
                platform=accounts[0].platform, handle=accounts[0].handle, posts=(), more_available=False, credits_used=1
            ),
        ), ()

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_snapshot)
    monkeypatch.setattr(social_audit, "resolve_competitor_handles", fake_resolve)

    answers = {
        "client_name": "ARG Finance",
        "raw_post_data_source": "",
        "client_s_own_social_pages_handles": "Instagram: @acme",
        "competitor_list": prose,
        "number_of_competitors_to_audit": "5",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert sorted(resolved_domains) == ["axtonfinance.com.au", "entourage.com.au"]
    assert "argfinance.com.au" not in resolved_domains  # the benchmark header is the client itself
    assert "could not be parsed" not in resolved["raw_post_data_source"]
    assert "instagram.com/entourage.com.au" in resolved["raw_post_data_source"]


@pytest.mark.asyncio
async def test_competitor_count_is_capped_regardless_of_what_was_requested(monkeypatch):
    from app.routers.pipeline import _run_social_data_prepass, _MAX_AUTO_COMPETITORS

    many_competitors = {
        "competitors": [
            {
                "rank": i,
                "domain": f"c{i}.com",
                "name": f"Competitor {i}",
                "page_url": f"https://c{i}.com",
                "verification_confidence": "Verified",
            }
            for i in range(1, 9)
        ],
        "notes": None,
    }
    import json as _json

    resolve_calls = []

    async def fake_resolve(domain):
        resolve_calls.append(domain)
        return ()  # no accounts found — keeps this test to the counting question only

    async def fake_client_snapshot(accounts, *, limit_per_platform=40):
        return (), ()

    monkeypatch.setattr(social_audit, "resolve_competitor_handles", fake_resolve)
    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_client_snapshot)

    answers = {
        "raw_post_data_source": "",
        "client_s_own_social_pages_handles": "Instagram: @acme",
        "competitor_list": _json.dumps(many_competitors),
        "number_of_competitors_to_audit": "8",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    # Nothing was actually found for the client or any competitor here, so the prepass reports a
    # skip (the field falls through to the operator's own answer) — but the cap is still enforced,
    # and the "N requested vs M fetched" note still surfaces, in the skip's error this time.
    assert len(resolve_calls) == _MAX_AUTO_COMPETITORS
    assert event["skipped"] is True
    assert "8 competitors were requested" in event["error"]


@pytest.mark.asyncio
async def test_an_unparseable_competitor_list_leaves_competitors_as_nd_without_failing_the_client(monkeypatch):
    from app.routers.pipeline import _run_social_data_prepass

    async def fake_client_snapshot(accounts, *, limit_per_platform=40):
        return (
            SocialFetchResult(platform="instagram", handle="acme", posts=(), more_available=False, credits_used=1),
        ), ()

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_client_snapshot)

    answers = {
        "client_name": "ARG Finance",
        "raw_post_data_source": "",
        "client_s_own_social_pages_handles": "Instagram: @acme",
        "competitor_list": "Entourage and Axton Finance are the two main competitors.",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert event["skipped"] is False
    assert "ARG Finance (client)" in resolved["raw_post_data_source"]
    assert "N/D" in resolved["raw_post_data_source"]


@pytest.mark.asyncio
async def test_reports_a_skip_when_sociavault_is_not_configured(monkeypatch):
    from app.routers.pipeline import _run_social_data_prepass

    async def fake_snapshot(accounts, *, limit_per_platform=40):
        return (), (f"{sociavault_client.API_KEY_ENV} is not set — no live post data was fetched.",)

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_snapshot)

    answers = {"raw_post_data_source": "", "client_s_own_social_pages_handles": "Instagram: @acme"}
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    assert resolved is answers
    assert event["skipped"] is True
    assert sociavault_client.API_KEY_ENV in event["error"]
