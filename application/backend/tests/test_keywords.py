"""Tests for `app/services/keywords.py` and the phase-scoping rule that protects it.

Two things are pinned here, and both of them fail *silently* if they regress.

The first is phase scoping. Phase 1 clusters the headline service ("Social Media Marketing");
Phase 2 clusters a sub-service of it ("Meta Ads"). Those are different keyword universes, and every
headline suggested later in a leg is grounded in whichever one that leg built. If Phase 2 ever
reads Phase 1's keyword set — either because `config_from_profile` anchors on the wrong fact, or
because the context store hands the parent run's entry down the `source_run_id` chain — then a
sub-service run produces topics about the parent service. Nothing errors; the output is simply
about the wrong thing.

The second is the relevance gate. It is what stops an off-topic term the model invented from
becoming a cluster's public-facing primary keyword, and it is the same gate the headline service
applies to a candidate's keyword. A "social media marketing" run that accepts "best pizza
melbourne" has lost the property the whole feature exists to provide.
"""

from __future__ import annotations

import asyncio

import pytest

from app.routers.pipeline import PHASE_SCOPED_CONTEXT_KEYS
from app.services import ahrefs_client
from app.services import keywords as K

PROFILE = {
    "client_name": "Acme",
    "website_url": "https://acme.com",
    "region": "Australia",
    "industry": "Social Media Marketing",
    "sub_service": "Meta Ads",
}


# --------------------------------------------------------------------------------------
# Phase scoping
# --------------------------------------------------------------------------------------


def test_phase1_anchors_on_the_service_and_phase2_on_the_sub_service() -> None:
    assert list(K.config_from_profile(PROFILE, "phase1").services) == ["Social Media Marketing"]
    assert list(K.config_from_profile(PROFILE, "phase2").services) == ["Meta Ads"]


def test_phase2_without_a_sub_service_yields_no_config() -> None:
    """Skipped, not defaulted.

    Falling back to the parent's `industry` here is the tempting bug: it would make the prepass
    "work" on a Phase 2 run that has not chosen its sub-service yet, by clustering the wrong thing.
    """
    assert K.config_from_profile({"industry": "Social Media Marketing"}, "phase2") is None


def test_phase1_without_an_industry_yields_no_config() -> None:
    assert K.config_from_profile({"client_name": "Acme"}, "phase1") is None


def test_keyword_context_keys_are_phase_scoped() -> None:
    """The context store must refuse to inherit these two down the `source_run_id` chain.

    Everything else a Phase 2 run reads — ICP, CRO, pillar page — is inherited on purpose. These
    are the exceptions, and the exception is enforced in `_latest_context_entry`.
    """
    assert "keyword_clusters" in PHASE_SCOPED_CONTEXT_KEYS
    assert "selected_headlines" in PHASE_SCOPED_CONTEXT_KEYS


def test_fingerprint_changes_with_region_and_service() -> None:
    """What decides whether a stored report is reused.

    Region is in here because Ahrefs volumes are per-country: the same service in a different
    market is a different keyword set, and reusing one for the other would be invisible.
    """
    base = K.config_from_profile(PROFILE, "phase1").fingerprint()
    assert K.config_from_profile({**PROFILE, "region": "Singapore"}, "phase1").fingerprint() != base
    assert K.config_from_profile({**PROFILE, "industry": "SEO"}, "phase1").fingerprint() != base
    assert K.config_from_profile(dict(PROFILE), "phase1").fingerprint() == base


# --------------------------------------------------------------------------------------
# Seeds
# --------------------------------------------------------------------------------------


def test_seeds_lead_with_the_bare_service_name() -> None:
    """The bare name is the `exact` match class, and the prompt requires every seed to survive into
    the hierarchy even when its expansions come back empty."""
    assert K.seeds_for_service("Social Media Marketing")[0] == "social media marketing"


def test_seeds_do_not_double_a_suffix_the_name_already_carries() -> None:
    assert "seo services services" not in K.seeds_for_service("SEO Services")


# --------------------------------------------------------------------------------------
# Step 2 — the cleaning pipeline
# --------------------------------------------------------------------------------------


def _clean(profile_key: str = "phase1"):
    config = K.config_from_profile(PROFILE, profile_key)
    raw = asyncio.run(K.fetch_keywords(config, None))  # stub provider — no network, no spend
    clean, dropped, vocabulary = K.run_keyword_pipeline(raw, config)
    return config, clean, dropped, vocabulary


def test_stub_provider_runs_the_whole_pipeline_without_credentials() -> None:
    config, clean, _dropped, vocabulary = _clean()
    assert clean, "the stub provider must produce a usable clean set"
    assert vocabulary
    hierarchy = K.build_service_hierarchy(clean, config)
    assert len(hierarchy) == 1
    assert hierarchy[0]["service"] == "Social Media Marketing"
    # Every seed appears exactly once, empty or not — the prompt's Step 4 rule.
    assert hierarchy[0]["seed_count"] == len(config.services["Social Media Marketing"])


def test_relevance_rejects_a_term_from_a_different_business() -> None:
    _config, _clean_set, _dropped, vocabulary = _clean()
    assert K.is_relevant("social media marketing checklist", vocabulary)
    assert not K.is_relevant("best pizza melbourne", vocabulary)


def test_dedupe_merges_plurals_but_not_differing_intent() -> None:
    assert K.dedupe_key("seo service") == K.dedupe_key("seo services")
    # Both reduce to {seo} once stopwords are stripped, which is why the key keeps them.
    assert K.dedupe_key("how to do seo") != K.dedupe_key("what is seo")


def test_intent_classification_reads_stopword_signals() -> None:
    assert K.classify_intent("how to do social media marketing", False) == "informational"
    assert K.classify_intent("social media marketing pricing", False) == "transactional"
    assert K.classify_intent("best social media marketing tools", False) == "commercial"
    assert K.classify_intent("acme social media marketing", True) == "navigational"


def test_noise_filter_catches_urls_after_normalisation_strips_them() -> None:
    """`normalize` removes `:` and `.`, so the URL test has to see the original string."""
    assert K.is_noise(K.normalize("https://acme.com/seo"), "https://acme.com/seo") == "url"


def test_stale_year_uses_the_current_year_at_call_time() -> None:
    """Read per call, not at import — a long-lived server would otherwise spend January dropping
    every keyword carrying the year that had just begun."""
    from datetime import datetime, timezone

    this_year = datetime.now(timezone.utc).year
    assert K.is_noise(f"social media marketing {this_year}", f"social media marketing {this_year}") is None
    assert K.is_noise("social media marketing 2019", "social media marketing 2019") is not None


# --------------------------------------------------------------------------------------
# Step 3 — cluster validation
# --------------------------------------------------------------------------------------


def test_validation_drops_a_cluster_led_by_an_invented_off_topic_keyword() -> None:
    """The anti-drift guarantee, at the cluster level.

    An invented term that fails relevance must not merely lose its metrics — it must not be
    published as a primary keyword at all, and a cluster with nothing else left goes with it.
    """
    _config, clean, _dropped, vocabulary = _clean()
    report = {
        "clusters": [
            {
                "name": "Off topic",
                "intent": "informational",
                "content_type": "blog",
                "keywords": [{"keyword": "best pizza melbourne", "role": "Primary"}],
            }
        ]
    }
    validated, warnings, invented = K.validate_clusters(report, clean, vocabulary)
    assert validated["clusters"] == []
    assert validated["clusters_created"] == 0
    assert any("failed relevance" in row["reason"] for row in invented)
    assert warnings


def test_validation_promotes_a_grounded_keyword_when_the_primary_is_ungrounded() -> None:
    _config, clean, _dropped, vocabulary = _clean()
    grounded = clean[0].keyword
    report = {
        "clusters": [
            {
                "name": "Mixed",
                "intent": "commercial",
                "content_type": "guide",
                "keywords": [
                    # Relevant (shares service tokens) but not in the fetched set, so it survives
                    # without metrics and must not lead the cluster.
                    {"keyword": "social media marketing retainer benchmarks", "role": "Primary"},
                    {"keyword": grounded, "role": "Secondary"},
                ],
            }
        ]
    }
    validated, warnings, _invented = K.validate_clusters(report, clean, vocabulary)
    assert validated["clusters"][0]["primary_keyword"] == grounded
    assert any("promoted" in w for w in warnings)


def test_validation_never_invents_metrics_for_an_ungrounded_keyword() -> None:
    _config, clean, _dropped, vocabulary = _clean()
    report = {
        "clusters": [
            {
                "name": "Mixed",
                "intent": "commercial",
                "content_type": "guide",
                "keywords": [
                    {"keyword": clean[0].keyword, "role": "Primary"},
                    {
                        "keyword": "social media marketing retainer benchmarks",
                        "role": "Secondary",
                        "volume": 9999,
                        "difficulty": 5,
                    },
                ],
            }
        ]
    }
    validated, _warnings, _invented = K.validate_clusters(report, clean, vocabulary)
    ungrounded = [e for e in validated["clusters"][0]["keywords"] if e.get("ungrounded")]
    assert len(ungrounded) == 1
    assert ungrounded[0]["volume"] is None
    assert ungrounded[0]["difficulty"] is None


# --------------------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["stub", "STUB", " stub "])
def test_provider_can_be_forced_to_stub(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("KEYWORDS_PROVIDER", value)
    monkeypatch.setenv("AHREFS_API_KEY", "x")
    assert K.provider_name() == "stub"


def test_provider_is_ahrefs_when_the_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEYWORDS_PROVIDER", raising=False)
    monkeypatch.setenv("AHREFS_API_KEY", "x")
    assert K.provider_name() == "ahrefs"


def test_provider_falls_back_to_stub_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer with no Ahrefs key gets a working pipeline, not a 500."""
    monkeypatch.delenv("KEYWORDS_PROVIDER", raising=False)
    monkeypatch.delenv("AHREFS_API_KEY", raising=False)
    assert K.provider_name() == "stub"


def test_forcing_ahrefs_without_a_key_is_an_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYWORDS_PROVIDER", "ahrefs")
    monkeypatch.delenv("AHREFS_API_KEY", raising=False)
    config = K.config_from_profile(PROFILE, "phase1")
    with pytest.raises(K.KeywordProviderError, match="AHREFS_API_KEY"):
        asyncio.run(K.build_keyword_report(config))


# --------------------------------------------------------------------------------------
# Provider failure that does not look like a failure
# --------------------------------------------------------------------------------------


def test_a_seed_only_result_is_rejected_rather_than_clustered() -> None:
    """Every expansion coming back empty must stop the run, not shrug through it.

    Each seed is added as its own `exact` row before any expansion happens, so a set of nothing but
    exact rows is the signature of a dead provider — bad credentials, an unrecognised
    `location_name`, an exhausted balance. Observed in the wild as "3 raw -> 3 clean", which reads
    like success in a log.

    Pressing on would spend an Opus call to cluster three bare seeds into a report with no long
    tail, no intent spread and no usable volumes — and every headline suggested afterwards would be
    "grounded" in it, claiming demand evidence that does not exist. Being honestly ungrounded is
    strictly better than that, so this raises instead.
    """
    config = K.config_from_profile(PROFILE, "phase1")
    seeds_only = [
        K.RawKeyword(seed, 320, 24, "exact", seed, "Social Media Marketing", "ahrefs")
        for seed in config.services["Social Media Marketing"]
    ]

    async def fake_fetch(_config, _dfs, _errors=None):
        return seeds_only

    original = K.fetch_keywords
    K.fetch_keywords = fake_fetch  # type: ignore[assignment]
    try:
        with pytest.raises(K.KeywordProviderError) as excinfo:
            asyncio.run(K.build_keyword_report(config))
    finally:
        K.fetch_keywords = original  # type: ignore[assignment]

    # The message has to name what to check — this failure is always a configuration problem, and
    # "no results" alone sends the operator nowhere.
    message = str(excinfo.value)
    assert config.location_name in message
    assert "API units" in message


def test_the_providers_own_error_leads_the_message() -> None:
    """When every call failed for the same stated reason, that sentence IS the diagnosis.

    A rejected key or an account out of units is the common case, and burying Ahrefs' own sentence
    in the log while telling the operator something generic sends them the wrong way.
    """
    config = K.config_from_profile(PROFILE, "phase1")
    seeds_only = [
        K.RawKeyword(seed, None, None, "exact", seed, "Social Media Marketing", "ahrefs")
        for seed in config.services["Social Media Marketing"]
    ]

    async def fake_fetch(_config, _dfs, errors=None):
        if errors is not None:
            errors.append("phrase for 'social media marketing': Ahrefs rejected the API key (401).")
        return seeds_only

    original = K.fetch_keywords
    K.fetch_keywords = fake_fetch  # type: ignore[assignment]
    try:
        with pytest.raises(K.KeywordProviderError) as excinfo:
            asyncio.run(K.build_keyword_report(config))
    finally:
        K.fetch_keywords = original  # type: ignore[assignment]

    message = str(excinfo.value)
    assert "Ahrefs rejected the API key (401)." in message
    assert config.location_name in message


def test_a_normal_result_passes_the_seed_only_guard() -> None:
    """The guard must not fire on the stub provider, which is what every test above runs on."""
    config = K.config_from_profile(PROFILE, "phase1")
    raw = asyncio.run(K.fetch_keywords(config, None))
    assert any(kw.match_class != "exact" for kw in raw)


# --------------------------------------------------------------------------------------
# Location resolution
#
# `location_name` is filled from the ICP intake's free-text "region" answer. Verified against the
# live API: Ahrefs Keywords Explorer takes a two-letter **country** code and nothing else — there is no
# Melbourne, no Victoria, no California, at any spelling (`ahrefs_client.COUNTRIES`).
#
# That is why this is a lookup and not string surgery. The first version here split on commas and
# broadened outwards, which rescues "Melbourne,Victoria,Australia" and does nothing at all for
# "Melbourne VIC Australia" — the form an operator actually typed, which produced a wall of
# identical provider errors and a run holding nothing but its seeds.
# --------------------------------------------------------------------------------------

SUPPORTED = tuple(ahrefs_client.COUNTRIES)


@pytest.mark.parametrize(
    "region,expected",
    [
        ("Australia", "Australia"),
        ("australia", "Australia"),          # case
        ("  Australia  ", "Australia"),      # padding
        ("Melbourne VIC Australia", "Australia"),        # the reported failure — no commas at all
        ("Melbourne, Victoria, Australia", "Australia"),  # commas
        ("Sydney/Australia", "Australia"),               # any separator
        ("Auckland, New Zealand", "New Zealand"),        # multi-word country
        ("Port Moresby, Papua New Guinea", "Papua New Guinea"),  # not "Guinea"
        ("Lagos, Nigeria", "Nigeria"),                   # not "Niger"
    ],
)
def test_a_free_text_region_resolves_to_its_country(region: str, expected: str) -> None:
    assert K.match_location(region, SUPPORTED) == expected


def test_the_first_country_named_wins() -> None:
    """Someone listing markets leads with their main one."""
    assert K.match_location("Australia and New Zealand", SUPPORTED) == "Australia"
    assert K.match_location("New Zealand and Australia", SUPPORTED) == "New Zealand"


@pytest.mark.parametrize(
    "region,expected",
    [
        ("USA", "United States"),
        ("Dubai, UAE", "United Arab Emirates"),
        ("England", "United Kingdom"),
        ("Turkey", "Turkiye"),          # the provider's own spelling differs from the common one
        ("Czech Republic", "Czechia"),  # and from the older name
    ],
)
def test_common_aliases_resolve(region: str, expected: str) -> None:
    """Only for names sharing no words with the supported entry — no matcher can reach those."""
    assert K.match_location(region, SUPPORTED) == expected


@pytest.mark.parametrize(
    "region,expected",
    [
        ("Sydney, New South Wales, Australia", "Australia"),  # not the UK, via "wales"
        ("Newark, New Jersey, USA", "United States"),         # not the island of Jersey
        ("Santa Fe, New Mexico, USA", "United States"),       # not Mexico
    ],
)
def test_a_sub_national_name_is_not_read_as_a_country(region: str, expected: str) -> None:
    assert K.match_location(region, SUPPORTED) == expected


def test_an_alias_whose_target_is_not_supported_does_not_resolve() -> None:
    """The alias table can outlive the list it points into."""
    assert K.match_location("USA", ("Australia", "India")) is None


@pytest.mark.parametrize("region", ["Sydney NSW", "EMEA", "Narnia", "", "   "])
def test_an_unresolvable_region_returns_none_rather_than_guessing(region: str) -> None:
    """No default country. Substituting one would build a keyword set for somewhere the client does
    not trade and report it as measured fact — a wrong answer, not a coarser one, and invisible."""
    assert K.match_location(region, SUPPORTED) is None


def test_a_partial_word_is_not_a_match() -> None:
    """Whole phrases only — "Indiana" must not resolve to India."""
    assert K.match_location("Indiana", SUPPORTED) is None
    assert K.match_location("Indianapolis, Indiana", SUPPORTED) is None


def test_resolution_substitutes_the_country() -> None:
    config = K.KeywordRunConfig("Acme", "", "Melbourne VIC Australia", "English", {"SMM": ["smm"]})
    assert K.resolve_location(config) == "Australia"


def test_an_unresolvable_region_is_an_actionable_error() -> None:
    """Ahrefs only takes a country code, so a region naming no country cannot be sent at all."""
    config = K.KeywordRunConfig("Acme", "", "Sydney NSW", "English", {"SMM": ["smm"]})
    with pytest.raises(K.KeywordProviderError, match="Melbourne, Australia"):
        K.resolve_location(config)
