"""Tests for `app/services/social_intel.py` — the measured comparison Step 7 of the social audit reads."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import social_audit
from app.services.social_intel import INTEL_HEADING, AccountSample, build_intel_md, offer_lines, signals_of
from app.services.sociavault_client import SocialFetchResult, SocialPost

_PROMPT = Path(__file__).resolve().parents[1] / "assets" / "Prompts" / "Social-Content-Strategy-Audit-Architect-Prompt.md"
_DAY = 86_400
_T0 = 1_790_000_000


def _post(n: int, *, likes: int | None, comments: int | None = 0, fmt: str = "image", caption: str = "", views=None,
          platform: str = "instagram") -> SocialPost:
    return SocialPost(
        platform=platform, id=str(n), url=f"https://example.com/p/{n}", published_at=_T0 - n * _DAY,
        caption=caption, like_count=likes, comment_count=comments if likes is not None else None,
        share_count=None, view_count=views, format=fmt,
    )


def _result(posts, platform: str = "instagram", handle: str = "acct") -> SocialFetchResult:
    return SocialFetchResult(platform=platform, handle=handle, posts=tuple(posts), more_available=False, credits_used=1)


def _samples() -> list[AccountSample]:
    competitor = AccountSample(
        "Rival Co",
        False,
        (
            _result(
                [
                    _post(1, likes=40, comments=6, fmt="reel", views=2000, caption="Meet Sam, our lending manager. Book a free consult today."),
                    _post(2, likes=35, comments=4, fmt="reel", views=1800, caption="Client win: settled a $3M construction facility."),
                    _post(3, likes=30, comments=3, fmt="reel", views=1500, caption="Our team at the awards night — finalist again!"),
                    _post(4, likes=3, fmt="image", caption="5 tips for cash flow."),
                    _post(5, likes=2, fmt="image", caption="What is a bridging loan?"),
                    _post(6, likes=4, fmt="image", caption="How to prepare your application."),
                ]
            ),
            _result([_post(7, likes=None, fmt="text", caption="Download our free guide.", platform="linkedin")], "linkedin"),
        ),
    )
    client = AccountSample(
        "Acme (client)",
        True,
        (_result([_post(10, likes=1, caption="Commercial loans explained."), _post(11, likes=0, caption="Why choose a broker?")]),),
    )
    return [client, competitor]


def test_signals_are_tagged_from_caption_wording() -> None:
    assert {"offer", "lead_cta", "people"} <= signals_of("Meet Sam. Book a free consult today.")
    assert "proof" in signals_of("Client win: settled a $3M facility")
    assert "question" in signals_of("Is your bank saying no? Here is why.")
    assert "timely" not in signals_of("You may qualify. Call us today.")
    assert signals_of(None) == frozenset() and signals_of("   ") == frozenset()


def test_offer_lines_are_the_caption_sentences_verbatim() -> None:
    caption = "Great week for the team. Book a free consult today! Rates are moving."
    assert offer_lines(caption) == ["Book a free consult today!"]


def test_nothing_is_built_without_a_competitor_post() -> None:
    client_only = [s for s in _samples() if s.is_client]
    assert build_intel_md(client_only) == ""
    assert build_intel_md([*client_only, AccountSample("Rival", False, (_result([]),))]) == ""


def test_the_block_measures_formats_signals_offers_and_the_clients_weakest_posts() -> None:
    md = build_intel_md(_samples())

    assert md.startswith(f"## {INTEL_HEADING}")
    assert "Leads are not observable" in md
    # Reels out-earned images for the competitor: 39.3 mean against 3.0.
    assert re.search(r"\| Instagram \| reel \| 3 \| 39\.3 \| 1,800 \|", md)
    assert re.search(r"\| Instagram \| image \| 3 \| 3\.0 \|", md)
    # A platform that publishes no counts is N/D, never 0.
    assert re.search(r"\| LinkedIn \| text \| 1 \| N/D \|", md)
    assert re.search(r"\| Rival Co \| LinkedIn \| 1 \| N/D \| \d+ \| N/D \| N/D \| N/D \| N/D \|", md)
    # The offer sentence is quoted as written.
    assert "Book a free consult today." in md
    # The client's own weakest post is listed for a rewrite, verbatim.
    assert "Why choose a broker?" in md
    assert "### G. The client's own posts to optimise" in md


def test_a_lift_needs_enough_posts_on_both_sides() -> None:
    md = build_intel_md(_samples())
    people_row = next(line for line in md.splitlines() if line.startswith("| People"))
    assert "N/D (fewer than 3 measured posts on one side)" in people_row
    education_row = next(line for line in md.splitlines() if line.startswith("| Education"))
    assert "x |" in education_row  # 3 measured with, 3 without


def test_the_prompts_step_7_names_the_section_and_follows_step_6() -> None:
    prompt = _PROMPT.read_text(encoding="utf-8")
    assert INTEL_HEADING in prompt
    headings = re.findall(r"^# (STEP \d)", prompt, flags=re.MULTILINE)
    assert headings == [f"STEP {n}" for n in range(8)]
    assert prompt.index("# STEP 7") > prompt.index("# STEP 6") and prompt.index("# STEP 7") < prompt.index("# OUTPUT FORMAT")
    assert "Steps 1 through 7" in prompt
    assert "never report interaction as leads" in prompt


@pytest.mark.asyncio
async def test_the_prepass_appends_the_block_when_competitor_posts_were_fetched(monkeypatch) -> None:
    from app.routers.pipeline import _run_social_data_prepass

    client, competitor = _samples()

    async def fake_snapshot(accounts, *, limit_per_platform=40):
        return (competitor.results if limit_per_platform == 15 else client.results), ()

    async def fake_resolve(domain, **_kw):
        return (social_audit.SocialAccount(platform="instagram", handle="rival"),)

    monkeypatch.setattr(social_audit, "fetch_account_snapshot", fake_snapshot)
    monkeypatch.setattr(social_audit, "resolve_competitor_handles", fake_resolve)

    answers = {
        "client_name": "Acme",
        "raw_post_data_source": "",
        "client_s_own_social_pages_handles": "Instagram: @acme",
        "competitor_list": '{"competitors": [{"rank": 1, "domain": "rival.com.au", "name": "Rival Co", '
        '"page_url": "https://rival.com.au", "verification_confidence": "Verified"}], "notes": null}',
        "number_of_competitors_to_audit": "5",
    }
    resolved, event = await _run_social_data_prepass("social_content_strategy_audit", answers)

    document = resolved["raw_post_data_source"]
    assert event["skipped"] is False
    # Appended after the existing per-account tables, which are unchanged.
    assert document.index("Acme (client) — live social data") < document.index(INTEL_HEADING)
    assert document.index("Rival Co — live social data") < document.index(INTEL_HEADING)
    assert "| Rival Co | Instagram | 6 |" in document
