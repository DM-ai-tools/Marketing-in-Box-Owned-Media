"""The client's business as the asset check sees it.

The offer ladder is parsed off a real Value Ladder output rather than a fixture written to match the
parser, for the reason `test_slide_deck.py` gives: a parser written to the spec returns nothing on
what the pipeline actually generates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.business_facts import BusinessFacts, load_business_facts, money_amounts, parse_offer_ladder

_LADDER = Path(__file__).resolve().parents[3] / "manual_execution" / "Value-Ladder_TrafficRadius-SMM_All-Wishes.md"


@pytest.fixture(scope="module")
def offers():
    if not _LADDER.exists():
        pytest.skip("real ladder sample not present")
    return parse_offer_ladder(_LADDER.read_text(encoding="utf-8"))


def test_every_offer_on_a_real_ladder_is_read(offers) -> None:
    titles = [o.title for o in offers]
    assert "The Four Causes Scorecard" in titles
    assert "The Partner-Voice Flagship Program" in titles
    assert len(titles) == len(set(t.casefold() for t in titles)), "a repeated offer was not merged"
    assert all(o.price for o in offers), [o.title for o in offers if not o.price]


def test_an_offer_carries_its_rung_price_and_next_step(offers) -> None:
    kit = next(o for o in offers if o.title == "The Partner-Voice Content Capture Kit")
    assert kit.format == "Template"
    assert "Rung 1" in kit.rung
    assert kit.price.startswith("$297")
    assert 297.0 in kit.amounts and 1200.0 in kit.amounts
    assert "Strategy Consultation" in kit.ascends_to


def test_a_repeat_fills_gaps_but_never_overwrites_the_first_description(offers) -> None:
    flagship = next(o for o in offers if o.title == "The Partner-Voice Flagship Program")
    assert flagship.price.startswith("$15,000")


def test_anything_that_is_not_a_ladder_has_no_offers() -> None:
    assert parse_offer_ladder("# A blog post\n\n1. First point\n2. Second point") == []


@pytest.mark.parametrize(
    ("text", "amounts"),
    [("$1,200", (1200.0,)), ("A$297.50 per seat", (297.5,)), ("from $2.5k/month", (2500.0,)), ("no price", ())],
)
def test_money_amounts(text: str, amounts: tuple[float, ...]) -> None:
    assert money_amounts(text) == amounts


def test_settings_are_read_by_their_leading_code() -> None:
    facts = BusinessFacts(
        settings={
            "claim_substantiation_tier": "2 PROFESSIONALLY REGULATED",
            "pricing_disclosure_mode": "E QUOTE-ONLY",
            "testimonials_before_after_permitted": "NO",
            "words_to_avoid": "cheap, synergy; 'best-in-class' / or",
        }
    )
    assert facts.claim_tier == 2
    assert facts.prices_forbidden
    assert facts.testimonials == "NO"
    assert facts.words_to_avoid == ["cheap", "synergy", "best-in-class"]


@pytest.mark.asyncio
async def test_missing_facts_are_named_not_guessed() -> None:
    async def nothing(_key: str) -> None:
        return None

    facts = await load_business_facts(nothing, {}, asset_id="lead_magnet")
    assert facts.offers == [] and facts.settings == {}
    assert "the offer ladder" in facts.missing
    assert any("CRO client settings" in m for m in facts.missing)
    assert "NOT ON RECORD" in facts.summary()


@pytest.mark.asyncio
async def test_the_offers_stage_is_not_checked_against_the_ladder_it_is_replacing() -> None:
    looked: list[str] = []

    async def lookup(key: str) -> None:
        looked.append(key)
        return None

    await load_business_facts(lookup, {}, asset_id="offers")
    assert "offer_ladder" not in looked and "offers" not in looked
