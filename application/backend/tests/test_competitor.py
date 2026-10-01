"""Tests for `app/services/competitor.py` — the decoding every competitor sub-step depends on.

All ten prompts now return one object shape (`{"competitors": [...], "notes": "..."}`), but each
names its own page-URL key and, for three of them, its own classifier key. `parse_analysis` is what
absorbs that per-stage naming, and it is the only thing standing between a model response and the
listing an operator approves — so its tolerance is worth pinning.

Deliberately no network: `generate_competitor_analysis` is a live Anthropic + web-search call and is
verified by running it.
"""

from __future__ import annotations

import json

import pytest

from app.services.competitor import (
    COMPETITOR_CONFIGS,
    GATED_COMPETITOR_MAIN_ASSET_IDS,
    PREPASS_BY_MAIN_ASSET,
    SONNET,
    CompetitorParseError,
    _effort,
    config_for,
    parse_analysis,
    parse_competitor_listing,
    resolve_inputs,
    resolve_model,
    to_prompt_text,
)


def _payload(**competitor_fields) -> str:
    return json.dumps(
        {
            "competitors": [{"domain": "rival.com.au", "name": "Rival", **competitor_fields}],
            "notes": "Returned 1 of 10; the rest publish nothing comparable.",
        }
    )


def test_notes_survive_the_object_contract():
    """The whole reason all ten prompts moved off a bare JSON array: an array has nowhere to put the
    notes each one is asked to write, so they were silently impossible."""
    parsed = parse_analysis("competitor_analysis_offers", _payload(offer_page_url="https://rival.com.au/pricing"))
    assert parsed.notes == "Returned 1 of 10; the rest publish nothing comparable."
    assert parsed.returned_count == 1


@pytest.mark.parametrize(
    ("asset_id", "url_key"),
    [
        ("competitor_analysis_cro", "cro_page_url"),
        ("competitor_analysis_offers", "offer_page_url"),
        ("competitor_analysis_lead_magnet", "lead_magnet_url"),
        ("competitor_analysis_blog", "blog_url"),
        ("competitor_analysis_seo_pillar_page", "pillar_page_url"),
        ("competitor_analysis_content_marketing", "content_marketing_page_url"),
        ("competitor_analysis_social_content_strategy", "service_page_url"),
        ("competitor_analysis_webinars", "webinar_page_url"),
        ("competitor_analysis_book", "book_page_url"),
        ("competitor_analysis_podcast", "podcast_page_url"),
    ],
)
def test_each_stage_s_own_url_key_is_found(asset_id, url_key):
    """Every prompt names its page URL after its own artefact. The parser takes any `*_url` key, so
    a new stage cannot silently lose its links by picking a different noun."""
    parsed = parse_analysis(asset_id, _payload(**{url_key: "https://rival.com.au/x/"}))
    assert parsed.competitors[0].page_url == "https://rival.com.au/x/"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("lead_magnet_type", "Gated ebook"),  # 03_Lead_Magnet.md
        ("content_focus", "Local SEO"),  # 04_Blog.md
        ("topical_focus", "Agency pricing"),  # 10_Podcast.md
        ("category", "Something else"),  # the shared name, for any future stage
    ],
)
def test_the_three_classifier_keys_land_on_one_column(key, value):
    parsed = parse_analysis("competitor_analysis_lead_magnet", _payload(**{key: value}))
    assert parsed.competitors[0].category == value


def test_no_classifier_means_none_not_empty_string():
    parsed = parse_analysis("competitor_analysis_book", _payload(book_page_url="https://rival.com.au/book/"))
    assert parsed.competitors[0].category is None
    assert parsed.competitors[0].starting_price is None


@pytest.mark.parametrize("key", ["starting_price", "starting_price_aud", "price_from"])
def test_price_is_read_verbatim_under_any_of_its_spellings(key):
    """Kept as published — the unit, the band and the "from" are the informative parts, and an
    earlier revision of 02_Offers.md used `starting_price_aud`."""
    parsed = parse_analysis("competitor_analysis_offers", _payload(**{key: "From $1,490/mo"}))
    assert parsed.competitors[0].starting_price == "From $1,490/mo"


def test_unrecognised_confidence_is_never_upgraded_to_verified():
    parsed = parse_analysis("competitor_analysis_blog", _payload(verification_confidence="probably fine"))
    assert parsed.competitors[0].verification_confidence == "Unverified"


def test_a_fenced_bare_array_still_parses():
    """Models wrap JSON in a ```json fence whatever the prompt says, and older prompt revisions asked
    for a bare array. Neither is worth failing a paid run over."""
    parsed = parse_analysis(
        "competitor_analysis_webinars",
        '```json\n[{"domain": "rival.com.au", "name": "Rival", "webinar_page_url": "https://rival.com.au/w/"}]\n```',
    )
    assert parsed.returned_count == 1
    assert parsed.notes is None


def test_rows_without_a_domain_are_dropped_and_duplicates_collapse():
    raw = json.dumps(
        {
            "competitors": [
                {"domain": "rival.com.au", "name": "Rival"},
                {"name": "No domain at all"},
                {"domain": "RIVAL.COM.AU", "name": "Rival again"},
            ],
            "notes": "n/a",
        }
    )
    parsed = parse_analysis("competitor_analysis_cro", raw)
    assert [c.domain for c in parsed.competitors] == ["rival.com.au"]
    assert parsed.competitors[0].rank == 1


def test_prose_handed_downstream_carries_the_stage_specific_fields():
    """`to_prompt_text` is what the paired main prompt actually reads — the classifier and the price
    have to reach it, or the main stage benchmarks against less than the operator approved."""
    parsed = parse_analysis(
        "competitor_analysis_lead_magnet",
        _payload(lead_magnet_url="https://rival.com.au/guide/", lead_magnet_type="ROI calculator"),
    )
    prose = to_prompt_text(parsed, "https://client.example")
    assert "Type / focus: ROI calculator" in prose
    assert "https://rival.com.au/guide/" in prose
    assert "Notes: Returned 1 of 10" in prose
    assert "{" not in prose  # prose for a prompt, never the wire JSON


def test_unparseable_output_raises_rather_than_looking_empty():
    """An empty listing and a lost result look identical in the UI otherwise, and the difference
    matters: one means "no qualifying competitors exist", the other means "retry"."""
    with pytest.raises(CompetitorParseError):
        parse_analysis("competitor_analysis_cro", "Sorry, I could not complete that search.")


def test_every_competitor_stage_is_ui_driven_and_none_double_runs():
    """The two paths must partition the ten stages exactly: anything in the gated set is driven by
    the UI, and anything left in `PREPASS_BY_MAIN_ASSET` runs invisibly inside generation. A stage in
    both would have its (paid, web-searching) analysis run twice per run."""
    paired_mains = {cfg.paired_main_asset_id for cfg in COMPETITOR_CONFIGS.values()}
    assert paired_mains == GATED_COMPETITOR_MAIN_ASSET_IDS
    assert PREPASS_BY_MAIN_ASSET == {}


def test_search_citation_markup_is_stripped_from_every_text_field():
    """Observed verbatim in a real run: with the server-side web_search tool the model writes
    `<cite index="58-1">…</cite>` inline. Persisted and rendered as-is it reads as broken markup, and
    it also reaches the paired main prompt. The tag goes; the sentence inside it stays."""
    raw = json.dumps(
        {
            "competitors": [
                {
                    "domain": "rival.com.au",
                    "name": "Rival",
                    "blog_url": "https://rival.com.au/blog/",
                    "content_focus": '<cite index="12-3">B2B social</cite>',
                    "offering_summary": 'Active blog; one post opens <cite index="58-1">Every single B2B buyer</cite> researches first.',
                }
            ],
            "notes": '<cite index="1-1">Returned 7 of 10</cite> after excluding three dormant blogs.',
        }
    )
    parsed = parse_analysis("competitor_analysis_blog", raw)
    row = parsed.competitors[0]
    assert "<cite" not in (row.offering_summary or "") and "</cite>" not in (row.offering_summary or "")
    assert row.offering_summary == "Active blog; one post opens Every single B2B buyer researches first."
    assert row.category == "B2B social"
    assert parsed.notes == "Returned 7 of 10 after excluding three dormant blogs."


# --------------------------------------------------------------------------------------
# Resolving what to search on
#
# Two of the paired stages (blog, podcast) source their `{NICHE}` from a multi-select suggestion
# gate, so the answer they hand over is a numbered list with each pick's keyword and intent
# indented beneath it. That is the right input for the stage that writes all of them and the wrong
# input for a search: pasted whole, it would run one query over five titles and their metadata.
# --------------------------------------------------------------------------------------

MULTI_TOPIC_ANSWER = (
    "1. What Social Media Marketing Costs In Melbourne\n"
    "   - Primary keyword: social media marketing cost melbourne\n"
    "   - Search intent: commercial\n"
    "2. Agency Or In-House For Social\n"
    "   - Primary keyword: agency vs in-house social"
)


def test_a_multi_topic_answer_searches_on_its_first_topic():
    """The first is the one the suggestion service ordered by demand — the strongest pick, and a
    real subject for a search."""
    resolved = resolve_inputs(
        config_for("competitor_analysis_blog"),
        {"blog_topic_working_title": MULTI_TOPIC_ANSWER},
        {"website_url": "https://acme.com.au", "industry": "Digital marketing"},
    )
    assert resolved["niche"] == "What Social Media Marketing Costs In Melbourne"


def test_a_single_topic_answer_is_left_exactly_as_it_was():
    """Every other stage answers this field with one line, and those must reach the prompt
    untouched."""
    resolved = resolve_inputs(
        config_for("competitor_analysis_webinars"),
        {"webinar_topic_working_title": "What agencies don't tell you about pricing"},
        {"website_url": "https://acme.com.au"},
    )
    assert resolved["niche"] == "What agencies don't tell you about pricing"


def test_an_empty_topic_still_falls_back_to_the_run_profile():
    """The gate can be skipped. The prepass then searches the client's industry, as it did before
    any of this existed."""
    resolved = resolve_inputs(
        config_for("competitor_analysis_blog"),
        {"blog_topic_working_title": ""},
        {"website_url": "https://acme.com.au", "industry": "Digital marketing"},
    )
    assert resolved["niche"] == "Digital marketing"


# --------------------------------------------------------------------------------------
# Model and effort resolution
#
# These are the two knobs an operator turns to measure this stage's cost against `api_usage`, and
# both are read from the environment at call time. What is worth pinning is not that they read an
# env var but that the *combination* is safe: `output_config.effort` is a 400 on Haiku 4.5 rather
# than a no-op, so a `COMPETITOR_MODEL` experiment must not carry the default effort along with it.
# --------------------------------------------------------------------------------------


def test_the_default_is_sonnet_at_medium_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset means Sonnet 5 with effort sent — the call used to send no `output_config` at all,
    which meant the API default of `high` by omission."""
    monkeypatch.delenv("COMPETITOR_MODEL", raising=False)
    monkeypatch.delenv("COMPETITOR_EFFORT", raising=False)
    assert resolve_model() == SONNET
    assert _effort(resolve_model()) == "medium"


def test_the_model_override_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPETITOR_MODEL", "claude-opus-5")
    assert resolve_model() == "claude-opus-5"
    # Still effort-capable, so the saving is not silently dropped when the arm changes.
    assert _effort(resolve_model()) == "medium"


def test_effort_is_withheld_from_a_model_that_rejects_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the gate. Haiku 4.5 answers `output_config.effort` with a 400, so sending
    the default alongside a Haiku override would fail all ten stages and read as a verdict on the
    model rather than on the request."""
    monkeypatch.setenv("COMPETITOR_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("COMPETITOR_EFFORT", "medium")
    assert _effort(resolve_model()) is None


def test_effort_can_be_switched_off_for_a_control_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    """`none` has to mean "send no `output_config`", because that is the arm every effort
    measurement is compared against."""
    monkeypatch.delenv("COMPETITOR_MODEL", raising=False)
    monkeypatch.setenv("COMPETITOR_EFFORT", "none")
    assert _effort(resolve_model()) is None


def test_an_unrecognised_effort_level_falls_back_rather_than_reaching_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo should cost a log line, not a 400 on ten stages."""
    monkeypatch.delenv("COMPETITOR_MODEL", raising=False)
    monkeypatch.setenv("COMPETITOR_EFFORT", "meduim")
    assert _effort(resolve_model()) == "medium"


# --- parse_competitor_listing: reading a listing back out of a main stage's field ----------------
#
# A `context_reference` field holds `to_prompt_text`'s prose, never the search's JSON. The social
# audit reads that field to decide whose posts to fetch, and while it could only read JSON every
# competitor came back N/D — so the round trip from the real writer is what is pinned here.

_APPROVED_JSON = json.dumps(
    {
        "competitors": [
            {"domain": "entourage.com.au", "name": "Entourage", "page_url": "https://www.entourage.com.au/social",
             "verification_confidence": "Verified", "offering_summary": "Social media for trades (and more)."},
            {"domain": "axtonfinance.com.au", "name": "Axton Finance", "verification_confidence": "Partially verified"},
        ],
        "notes": "Two qualified — benchmarked against https://client.example as well.",
    }
)


def test_listing_round_trips_through_the_prose_the_field_actually_holds():
    prose = to_prompt_text(parse_analysis("competitor_analysis_social_content_strategy", _APPROVED_JSON), "https://client.example")
    listing = parse_competitor_listing("competitor_analysis_social_content_strategy", prose)

    assert [c.domain for c in listing.competitors] == ["entourage.com.au", "axtonfinance.com.au"]
    assert [c.name for c in listing.competitors] == ["Entourage", "Axton Finance"]
    assert [c.verification_confidence for c in listing.competitors] == ["Verified", "Partially verified"]
    assert listing.competitors[0].page_url == "https://www.entourage.com.au/social"


def test_listing_never_reads_the_benchmark_header_as_a_competitor():
    """The header's URL is the client's own site; auditing it as a competitor compares the client to
    itself. Checked on the typed-list reading too, which is the one that would pick it up."""
    prose = "Competitor analysis — benchmarked against https://client.example\n\nWe mostly lose to rival.com.au"
    listing = parse_competitor_listing("x", prose)
    assert [c.domain for c in listing.competitors] == ["rival.com.au"]


def test_listing_still_reads_raw_json():
    listing = parse_competitor_listing("x", _APPROVED_JSON)
    assert [c.domain for c in listing.competitors] == ["entourage.com.au", "axtonfinance.com.au"]


def test_listing_reads_a_hand_typed_list():
    typed = "\n".join(
        [
            "1. Entourage — www.entourage.com.au",
            "- https://axtonfinance.com.au/about",
            "Bright (bright.co)",
            "email sales@notarival.com",
            "Entourage again: entourage.com.au",
        ]
    )
    listing = parse_competitor_listing("x", typed)

    assert [c.domain for c in listing.competitors] == ["entourage.com.au", "axtonfinance.com.au", "bright.co"]
    assert listing.competitors[0].name == "Entourage"
    assert listing.competitors[1].page_url == "https://axtonfinance.com.au/about"
    assert listing.competitors[2].name == "Bright"
    # Typed by the operator, so never claimed as researched.
    assert {c.verification_confidence for c in listing.competitors} == {"Unverified"}


def test_listing_with_no_domain_is_still_a_parse_error():
    with pytest.raises(CompetitorParseError):
        parse_competitor_listing("x", "Entourage and Axton Finance are the two main competitors.")


def test_the_social_audit_searches_the_clients_own_market_not_social_agencies() -> None:
    """Stage 10 benchmarks each competitor's posts and service coverage against the client's own,
    so its competitors must sell what the client sells. The prompt used to ask for companies that
    sell social content strategy, which for ARG Finance (a commercial broker) returned three social
    media agencies — suppliers to its market, with nothing to compare against its services."""
    from app.services.competitor import _schema_defaults, build_competitor_prompt, _config

    asset = "competitor_analysis_social_content_strategy"
    prompt = build_competitor_prompt(asset, {"target_url": "https://argfinance.com.au/", "niche": "", "location": "Australia"})
    assert "competes with the target for the same buyers" in prompt
    assert "suppliers to the target's market" in prompt
    assert "active public social presence" in prompt
    assert "specialised in social content strategy" not in prompt
    # No fixed default may re-point the search at the social-media service itself.
    assert "service" not in _schema_defaults(_config(asset))
