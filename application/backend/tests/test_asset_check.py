"""The asset check: rules against the facts on record, then a judge whose quotes are verified.

No network: the judge is patched, and the facts are built in the test. The rules are also run over
real generated documents in `manual_execution/`, because the failure worth guarding against there
is noise — a checker that flags every competitor quote and KPI target gets ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import pipeline as pipeline_router
from app.services import asset_check as A
from app.services.business_facts import BusinessFacts, Offer
from app.services.generation import CONFIGS_BY_PHASE
from app.services.social_audit import engagement_stats
from app.services.sociavault_client import SocialFetchResult, SocialPost

_SAMPLES = Path(__file__).resolve().parents[3] / "manual_execution"

LADDER = [
    Offer(title="The Four Causes Scorecard", format="Diagnostic", price="Free", estimated_value="$150"),
    Offer(title="Partner-Voice Content Capture Kit", format="Template", price="$297", estimated_value="$1,200"),
]
REGULATED = {
    "claim_substantiation_tier": "2 PROFESSIONALLY REGULATED",
    "testimonials_before_after_permitted": "NO",
    "words_to_avoid": "cheap, synergy",
    "pricing_disclosure_mode": "B FROM-PRICE",
    "pricing_facts": "From $2,500/month",
}


def _facts(**overrides: Any) -> BusinessFacts:
    base: dict[str, Any] = {"service": "Social Media Marketing", "offers": list(LADDER), "settings": dict(REGULATED)}
    base.update(overrides)
    return BusinessFacts(**base)


def _rules(asset_id: str, text: str, facts: BusinessFacts | None = None) -> list[A.Finding]:
    return A.run_rules(asset_id, text, facts or _facts())[0]


def _whys(findings: list[A.Finding]) -> str:
    return " || ".join(f.why for f in findings)


# --------------------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------------------


def test_every_stage_in_both_phases_has_checks() -> None:
    stages = {a for configs in CONFIGS_BY_PHASE.values() for a in configs}
    assert stages <= set(A.CHECKS_BY_ASSET), sorted(stages - set(A.CHECKS_BY_ASSET))
    for checks in A.CHECKS_BY_ASSET.values():
        assert "business_logic" in checks
        assert set(checks) <= set(A.CHECKS)


# --------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------


def test_a_price_on_the_ladder_passes_and_an_invented_one_does_not() -> None:
    assert not _rules("lead_magnet", "Get the Partner-Voice Content Capture Kit for $297.")
    invented = _rules("lead_magnet", "Get the Partner-Voice Content Capture Kit for $999.")
    assert any("$999" in f.why for f in invented)


def test_any_price_is_an_error_when_the_client_publishes_none() -> None:
    facts = _facts(settings={**REGULATED, "pricing_disclosure_mode": "E QUOTE-ONLY"})
    findings = _rules("lead_magnet", "The Partner-Voice Content Capture Kit is $297.", facts)
    assert any(f.severity == "error" and "pricing disclosure mode" in f.why for f in findings)


def test_prices_are_not_checked_when_none_are_on_record() -> None:
    facts = _facts(offers=[], settings={})
    assert not [f for f in _rules("lead_magnet", "Only $999 today.", facts) if "$999" in f.why]


def test_a_routing_asset_that_names_no_real_offer_is_flagged() -> None:
    assert any("Names none of the offers" in f.why for f in _rules("funnel", "Book a call with us."))
    assert not any("Names none" in f.why for f in _rules("funnel", "Start with The Four Causes Scorecard."))


def test_words_to_avoid_are_matched_as_words() -> None:
    assert any('"cheap"' in f.why for f in _rules("blog", "A cheap way to grow."))
    assert not any('"cheap"' in f.why for f in _rules("blog", "Cheapskate is not the word."))


def test_testimonials_are_errors_when_forbidden_and_quiet_when_permitted() -> None:
    draft = 'Rated 4.9/5 stars. "They doubled our enquiries in a quarter" — Jane Smith, Director'
    forbidden = _rules("lead_magnet", draft)
    assert any(f.severity == "error" for f in forbidden)
    permitted = _rules("lead_magnet", draft, _facts(settings={**REGULATED, "testimonials_before_after_permitted": "YES"}))
    assert not any("testimonial" in f.why for f in permitted)


def test_message_numbers_are_not_ratings() -> None:
    assert not any("testimonial" in f.why for f in _rules("sms_sequence", "Tighter spacing for SMS 4/5 works better."))


def test_a_strategy_document_scoring_funnels_is_not_a_testimonial() -> None:
    assert not any("testimonial" in f.why for f in _rules("plan_of_action", "| Funnel A | ★★★★☆ |"))


def test_absolute_claims_are_errors_at_a_regulated_tier_and_negation_is_compliant() -> None:
    assert any(f.severity == "error" for f in _rules("blog", "Guaranteed results in 30 days."))
    assert not _rules("blog", "Results are not guaranteed and vary by firm.")
    assert not _rules("blog", "If you're looking for a guaranteed outcome, this isn't for you.")


def test_competitor_sections_are_evidence_not_the_clients_claims() -> None:
    draft = "# Brief\n\nOur offer.\n\n## Competitor scan\n\n| Rival | \"Guaranteed results\" |\n\n## Our angle\n\nNo claims."
    assert not any("absolute claim" in f.why for f in _rules("lead_magnet", draft))


def test_statistics_and_placeholders_are_information_only() -> None:
    findings = _rules("blog", "Firms see 400% more leads.\n\nCall `[PLACEHOLDER — phone]`.")
    assert {f.severity for f in findings} == {"info"}
    assert not _rules("blog", "Target a keyword density of 1-2%.")


def test_one_finding_per_rule_per_line() -> None:
    findings = _rules("blog", "Guaranteed, guaranteed, GUARANTEED.")
    assert len(findings) == 1


# --------------------------------------------------------------------------------------
# The judge
# --------------------------------------------------------------------------------------


def _patch_judge(monkeypatch: pytest.MonkeyPatch, reply: str | Exception) -> list[str]:
    prompts: list[str] = []

    async def fake(prompt: str, _on_usage: Any) -> str:
        prompts.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(A, "_call_judge", fake)
    return prompts


DRAFT = "Start with The Four Causes Scorecard. It finds why your social is flat in five minutes."


@pytest.mark.asyncio
async def test_judge_findings_must_quote_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "checks": {
            "offer_relevance": {"score": 82, "verdict": "Mostly on-ladder.", "findings": [
                {"severity": "warn", "quote": "in five minutes", "why": "Unsubstantiated time claim.", "fix": "Say 'quickly'."},
                {"severity": "error", "quote": "Buy our $5,000 platinum bundle", "why": "Invented offer.", "fix": ""},
            ]},
            "business_logic": {"score": 90, "verdict": "Fine.", "findings": []},
        }
    }
    _patch_judge(monkeypatch, json.dumps(reply))
    report = await A.check_asset("offers", DRAFT, _facts())
    offer = next(c for c in report.checks if c.check == "offer_relevance")
    assert offer.score == 82
    assert [f.quote for f in offer.findings] == ["in five minutes"]
    assert report.dropped_quotes == 1
    assert report.judge == "ok"


@pytest.mark.asyncio
async def test_a_failed_judge_leaves_the_rules_standing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_judge(monkeypatch, RuntimeError("overloaded"))
    report = await A.check_asset("blog", "Guaranteed results.", _facts())
    assert report.judge == "unavailable"
    assert any(f.source == "rule" for c in report.checks for f in c.findings)


@pytest.mark.asyncio
async def test_unparseable_judge_output_is_unavailable_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_judge(monkeypatch, "Looks good to me!")
    assert (await A.check_asset("blog", DRAFT, _facts())).judge == "unavailable"


@pytest.mark.asyncio
async def test_the_judge_is_told_the_facts_and_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = _patch_judge(monkeypatch, json.dumps({"checks": {}}))
    await A.check_asset("lead_magnet", DRAFT, _facts(missing=["the ICP"]))
    prompt = prompts[0]
    assert '"The Four Causes Scorecard"' in prompt
    assert "NOT ON RECORD (do not assume these): the ICP" in prompt
    assert "PREDICT" in prompt  # virality applies to a lead magnet


@pytest.mark.asyncio
async def test_virality_is_benchmarked_only_on_real_data(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = _patch_judge(monkeypatch, json.dumps({"checks": {}}))
    rubric = await A.check_asset("blog", DRAFT, _facts())
    assert rubric.virality_basis == "rubric only"
    social = [{"account": "Acme (client)", "platform": "instagram", "median_engagement": 40.0, "measured_posts": 12,
               "top_posts": [{"engagement": 900, "is_video": True, "opening": "Your social is flat because…"}]}]
    benchmarked = await A.check_asset("blog", DRAFT, _facts(social=social,
                                      keywords=[{"keyword": "social is flat", "volume": 320, "difficulty": 12}]))
    assert benchmarked.virality_basis == "benchmarked"
    assert "Your social is flat because" in prompts[-1] and "volume 320" in prompts[-1]


@pytest.mark.asyncio
async def test_html_is_checked_as_the_words_a_reader_sees(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_judge(monkeypatch, json.dumps({"checks": {}}))
    html = "```html\n<html><head><style>.guaranteed{color:red}</style></head><body><p>Hello there.</p></body></html>\n```"
    report = await A.check_asset("pillar_page", html, _facts())
    assert not any(f for c in report.checks for f in c.findings)


# --------------------------------------------------------------------------------------
# Real documents — the noise floor
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "asset_id"),
    [
        ("SMS-Sequence_TrafficRadius-LeadMagnet.md", "sms_sequence"),
        ("Blog_Social-Media-Marketing_TrafficRadius.md", "blog"),
        ("Book_TrafficRadius-SMM_WhySocialIsntWorking.md", "book"),
        ("Plan-of-Action_TrafficRadius-SMM.md", "plan_of_action"),
        ("Lead-Magnet-Brief_TrafficRadius-SMM.md", "lead_magnet"),
    ],
)
def test_real_outputs_raise_no_false_errors(name: str, asset_id: str) -> None:
    path = _SAMPLES / name
    if not path.exists():
        pytest.skip("sample not present")
    findings = _rules(asset_id, path.read_text(encoding="utf-8"))
    errors = [f for f in findings if f.severity == "error"]
    assert not errors, _whys(errors)


# --------------------------------------------------------------------------------------
# Engagement stats — measured, and honest about what was not
# --------------------------------------------------------------------------------------


def _post(pid: str, likes: int | None, comments: int | None, shares: int | None, caption: str = "Hook line\nmore") -> SocialPost:
    return SocialPost("instagram", pid, None, None, caption, likes, comments, shares)


def test_engagement_stats_use_real_counts_and_never_invent_zero() -> None:
    result = SocialFetchResult("instagram", "@acme", (_post("a", 10, 2, None), _post("b", 100, 20, None), _post("c", 1, 0, None)), False, 1)
    (stats,) = engagement_stats("Acme", (result,))
    assert stats["median_engagement"] == 12.0
    assert stats["top_posts"][0]["engagement"] == 120
    assert stats["top_posts"][0]["opening"] == "Hook line"

    linkedin = SocialFetchResult("linkedin", "acme", (_post("x", None, None, None),), False, 1)
    (none,) = engagement_stats("Acme", (linkedin,))
    assert none["median_engagement"] is None and none["measured_posts"] == 0 and none["top_posts"] == []


# --------------------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------------------


def test_route_checks_an_unsaved_draft_and_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def facts(_run_id: str | None, _profile: dict[str, str], _asset: str) -> BusinessFacts:
        return _facts()

    async def judge(_prompt: str, _on_usage: Any) -> str:
        return json.dumps({"checks": {"business_logic": {"score": 70, "verdict": "One claim to fix.", "findings": []}}})

    monkeypatch.setattr(pipeline_router, "_business_facts", facts)
    monkeypatch.setattr(A, "_call_judge", judge)
    res = TestClient(app).post("/pipeline/check/blog", json={"text": "Guaranteed results, fast.", "run_id": None})
    assert res.status_code == 200, res.text
    body = res.json()
    logic = next(c for c in body["checks"] if c["check"] == "business_logic")
    assert logic["score"] == 70
    assert any(f["source"] == "rule" and f["severity"] == "error" for f in logic["findings"])
    assert body["virality_basis"] == "rubric only"


def test_route_404s_for_a_stage_the_phase_does_not_run() -> None:
    res = TestClient(app).post("/pipeline/check/offers", json={"text": "x", "phase": "phase2"})
    assert res.status_code == 404


def test_phase2_icp_is_not_checked_but_phase1_icp_is(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def facts(_run_id: str | None, _profile: dict[str, str], _asset: str) -> BusinessFacts:
        called.append("facts")
        return _facts()

    async def judge(_prompt: str, _on_usage: Any) -> str:
        called.append("judge")
        return json.dumps({"checks": {"business_logic": {"score": 80, "verdict": "Fine.", "findings": []}}})

    monkeypatch.setattr(pipeline_router, "_business_facts", facts)
    monkeypatch.setattr(A, "_call_judge", judge)
    client = TestClient(app)

    refused = client.post("/pipeline/check/icp", json={"text": "An ICP.", "phase": "phase2"})
    assert refused.status_code == 422, refused.text
    assert called == []  # no facts read, no model call, so nothing billed

    assert client.post("/pipeline/check/icp", json={"text": "An ICP.", "phase": "phase1"}).status_code == 200
    assert A.check_applies("icp", "phase1") and not A.check_applies("icp", "phase2")
    assert A.check_applies("cro", "phase2")


def test_blank_drafts_are_rejected() -> None:
    assert TestClient(app).post("/pipeline/check/blog", json={"text": "   "}).status_code == 422


# --------------------------------------------------------------------------------------
# The social sample the virality benchmark reads
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_social_prepass_hands_its_counts_to_the_stream_not_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: list[Any] = []

    async def prepass(asset_id: str, answers: dict[str, str]) -> Any:
        return answers, {"type": "social_prepass", "skipped": False, "content": "x", "stats": [{"account": "Acme"}]}

    async def passthrough(_asset: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
        return answers, None

    async def store(run_id: str, stats: list[dict]) -> None:
        stored.append((run_id, stats))

    async def none(*_a: Any, **_k: Any) -> None:
        return None

    async def stream(*_a: Any, **_k: Any):
        yield "ok"

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", prepass)
    monkeypatch.setattr(pipeline_router, "_store_social_sample", store)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", none)
    monkeypatch.setattr(pipeline_router, "_run_industry", none)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", stream)

    events = [e async for e in pipeline_router._generation_sse_stream(
        "social_content_strategy_audit", {}, {}, "phase1", None, "11111111-1111-1111-1111-111111111111")]
    assert stored == [("11111111-1111-1111-1111-111111111111", [{"account": "Acme"}])]
    assert not any('"stats"' in e for e in events)


# --------------------------------------------------------------------------------------
# Fix with Refine: the revision sees the facts, the re-check sees the previous report
# --------------------------------------------------------------------------------------


def _previous(score: int = 60) -> list[A.CheckResult]:
    return [
        A.CheckResult("business_logic", score, "One claim to fix.", [
            A.Finding("business_logic", "error", "Guaranteed results", "Absolute claim at tier 2.", "Soften it.")
        ])
    ]


@pytest.mark.asyncio
async def test_a_recheck_shows_the_judge_the_previous_report(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = _patch_judge(monkeypatch, json.dumps({"checks": {}}))
    await A.check_asset("blog", DRAFT, _facts(), previous=_previous())
    await A.check_asset("blog", DRAFT, _facts())
    assert "PREVIOUS CHECK" in prompts[0] and "business_logic: score 60" in prompts[0]
    assert "Absolute claim at tier 2." in prompts[0]
    assert "PREVIOUS CHECK" not in prompts[1]


@pytest.mark.asyncio
async def test_a_recheck_cannot_score_lower_without_a_finding_to_show_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_judge(monkeypatch, json.dumps({"checks": {"business_logic": {"score": 52, "verdict": "", "findings": []}}}))
    report = await A.check_asset("blog", DRAFT, _facts(), previous=_previous(60))
    assert next(c for c in report.checks if c.check == "business_logic").score == 60


@pytest.mark.asyncio
async def test_a_recheck_may_score_lower_when_it_names_the_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"checks": {"business_logic": {"score": 52, "verdict": "", "findings": [
        {"severity": "warn", "quote": "in five minutes", "why": "Unsubstantiated time claim.", "fix": ""}
    ]}}}
    _patch_judge(monkeypatch, json.dumps(reply))
    report = await A.check_asset("blog", DRAFT, _facts(), previous=_previous(60))
    assert next(c for c in report.checks if c.check == "business_logic").score == 52


def test_check_route_accepts_the_previous_report(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def facts(_run_id: str | None, _profile: dict[str, str], _asset: str) -> BusinessFacts:
        return _facts()

    async def judge(prompt: str, _on_usage: Any) -> str:
        seen.append(prompt)
        return json.dumps({"checks": {}})

    monkeypatch.setattr(pipeline_router, "_business_facts", facts)
    monkeypatch.setattr(A, "_call_judge", judge)
    previous = [{"check": "business_logic", "score": 60, "verdict": "", "findings": [
        {"check": "business_logic", "severity": "error", "quote": "Guaranteed", "why": "Absolute claim.", "fix": "", "source": "rule"}
    ]}]
    res = TestClient(app).post("/pipeline/check/blog", json={"text": DRAFT, "previous": previous})
    assert res.status_code == 200, res.text
    assert "business_logic: score 60" in seen[0]


def test_the_revision_prompt_carries_the_facts_only_when_given() -> None:
    from app.services.generation import build_revision_prompt

    plain = build_revision_prompt("draft", "fix it")
    with_facts = build_revision_prompt("draft", "fix it", None, A.fix_context(_facts()))
    assert "THE CLIENT'S BUSINESS" not in plain
    assert "THE CLIENT'S BUSINESS" in with_facts and '"Partner-Voice Content Capture Kit"' in with_facts
    assert with_facts.index("END PREVIOUS DRAFT") < with_facts.index("THE CLIENT'S BUSINESS") < with_facts.index("fix it")


@pytest.mark.parametrize("business_fix", [True, False])
def test_refine_route_hands_the_facts_over_only_for_a_business_fix(monkeypatch: pytest.MonkeyPatch, business_fix: bool) -> None:
    received: list[str | None] = []

    async def facts(_run_id: str | None, _profile: dict[str, str], _asset: str) -> BusinessFacts:
        return _facts()

    async def revise(*_args: Any, business_facts: str | None = None, **_kwargs: Any):
        received.append(business_facts)
        yield "revised"

    monkeypatch.setattr(pipeline_router, "_business_facts", facts)
    monkeypatch.setattr(pipeline_router, "generate_revision_stream", revise)
    res = TestClient(app).post(
        "/pipeline/refine/blog/stream",
        json={"previous_draft": "draft", "note": "fix it", "business_fix": business_fix},
    )
    assert res.status_code == 200 and "revised" in res.text
    assert (received[0] is not None and "Partner-Voice Content Capture Kit" in received[0]) if business_fix else received == [None]


@pytest.mark.parametrize(("raw", "expected"), [(80, 80), (79.6, 79), ("80", 80), ("80/100", 80), ("80%", 80),
                                               (140, 100), ("n/a", None), (None, None), ("high", None), (True, None)])
def test_judge_scores_are_read_from_numbers_and_numeric_strings_only(raw: Any, expected: int | None) -> None:
    assert A._score_of(raw) == expected


@pytest.mark.asyncio
async def test_a_missing_previous_score_is_not_shown_to_the_judge_as_na(monkeypatch: pytest.MonkeyPatch) -> None:
    """"score n/a" in the previous block came back as `"score": "n/a"`, so one unscored check made
    every re-check after it unscored too, however many times the draft was fixed."""
    prompts = _patch_judge(monkeypatch, json.dumps({"checks": {}}))
    await A.check_asset("blog", DRAFT, _facts(), previous=[A.CheckResult("business_logic", None, "", [])])
    assert "n/a" not in prompts[0].split("PREVIOUS CHECK", 1)[1].split("RE-CHECK RULES", 1)[0]
    assert "business_logic: not scored" in prompts[0]
    assert "never null" in prompts[0]


def test_the_social_audit_is_not_scored_for_virality() -> None:
    assert "virality" not in A.CHECKS_BY_ASSET["social_content_strategy_audit"]
