"""Alternate service names, keyword ranking, primary/secondary grouping, and the prompt block.

Nothing reaches DataForSEO or Anthropic: fake clients, and `service_synonyms.synonyms_for` is
stubbed by `conftest.py` unless a test sets names of its own.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.routers import pipeline
from app.services import content_keywords, dataforseo_client, generation, keyword_rank, keywords, service_synonyms
from app.services.dataforseo_client import DfsKeyword

# --------------------------------------------------------------------------------------
# Alternate names
# --------------------------------------------------------------------------------------

#: The worked examples the prompt carries, as service -> names. Every one must survive the check.
EXAMPLES = {
    "SEO": ["search engine optimisation", "organic search", "google ranking", "search visibility"],
    "Google Ads": ["PPC advertising", "pay per click", "paid search", "adwords"],
    "Family Law": ["divorce law", "matrimonial law", "family legal services", "separation law"],
    "Conveyancing": ["property transfer", "property settlement", "property law", "settlement services"],
    "Mortgage Broking": ["home loan broking", "mortgage lending", "home loan advice", "mortgage advisory"],
    "Physiotherapy": ["physical therapy", "physio treatment", "movement therapy", "rehabilitation therapy"],
    "Plumbing": ["pipe work", "plumbing repairs", "drain services", "water system services"],
    "Corporate Training": ["workplace training", "staff development", "professional training", "employee development"],
    "Buyers Agent": ["buyers advocate", "property advocate", "home buying service", "property acquisition service"],
    "Event Management": ["event planning", "event coordination", "event organisation", "event services"],
}


@pytest.mark.parametrize(("service", "names"), list(EXAMPLES.items()))
def test_every_worked_example_passes_the_check(service: str, names: list[str]) -> None:
    assert service_synonyms.clean_synonyms(service, names) == [n.lower() for n in names]


def test_the_examples_are_in_the_prompt() -> None:
    for service in EXAMPLES:
        assert f'"{service}"' in service_synonyms.GUIDANCE


@pytest.mark.parametrize(
    "rejected",
    ["seo", "SEO services", "seo agency", "best seo near me", "seo company", "seo melbourne", "cheap seo", "seo cost"],
)
def test_a_qualified_form_or_the_service_itself_is_not_another_name(rejected: str) -> None:
    assert service_synonyms.clean_synonyms("SEO", [rejected]) == []


def test_names_are_capped_deduped_and_short() -> None:
    proposed = ["organic search", "Organic Searches", "a b c d e f", *(f"name {i}" for i in range(10))]
    kept = service_synonyms.clean_synonyms("SEO", proposed)
    assert kept[0] == "organic search" and "organic searches" not in kept
    assert "a b c d e f" not in kept
    assert len(kept) == service_synonyms.MAX_SYNONYMS


def test_a_reply_that_is_not_json_gives_no_names() -> None:
    assert service_synonyms.parse_reply("Sure! Here are some names.") == []
    assert service_synonyms.parse_reply('Names: {"synonyms": ["organic search"]}') == ["organic search"]


# --------------------------------------------------------------------------------------
# Search momentum, from DataForSEO's own fields
# --------------------------------------------------------------------------------------


def test_momentum_prefers_the_quarterly_trend_then_the_yearly() -> None:
    assert dataforseo_client._trend({"search_volume_trend": {"monthly": 50, "quarterly": 12, "yearly": -5}}) == 12
    assert dataforseo_client._trend({"search_volume_trend": {"quarterly": None, "yearly": -5}}) == -5


def test_momentum_falls_back_to_the_monthly_history_and_is_none_without_it() -> None:
    months = [{"year": 2026, "month": m, "search_volume": v} for m, v in zip(range(9, 3, -1), [200, 200, 200, 100, 100, 100])]
    assert dataforseo_client._trend({"monthly_searches": months}) == 100
    assert dataforseo_client._trend({"monthly_searches": months[:4]}) is None
    assert dataforseo_client._trend({}) is None


def test_a_keyword_row_carries_its_momentum() -> None:
    row = dataforseo_client._keyword(
        {"keyword": "seo", "keyword_info": {"search_volume": 100, "search_volume_trend": {"quarterly": 30}}}
    )
    assert row is not None and row.trend == 30


# --------------------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------------------


def test_a_keyword_nobody_can_rank_for_earns_less_traffic_than_its_volume_suggests() -> None:
    easy, hard = keyword_rank.est_traffic(1000, 10), keyword_rank.est_traffic(1000, 90)
    assert easy is not None and hard is not None and easy > hard * 5


def test_momentum_moves_the_score_and_its_absence_is_not_a_fall() -> None:
    rising, falling, unknown = (keyword_rank.score_of(1000, 30, t) for t in (80, -80, None))
    assert rising > unknown > falling


def test_no_volume_no_score_and_it_ranks_last() -> None:
    ranked = keyword_rank.rank_keywords(
        [
            {"keyword": "unmeasured", "volume": None, "difficulty": 10, "trend": None},
            {"keyword": "small", "volume": 50, "difficulty": 10, "trend": None},
            {"keyword": "big", "volume": 5000, "difficulty": 20, "trend": 10},
            {"keyword": "Big", "volume": 1, "difficulty": 1, "trend": None},
        ]
    )
    assert [k.keyword for k in ranked] == ["big", "small", "unmeasured"]
    assert [k.rank for k in ranked] == [1, 2, 3]
    assert ranked[-1].score is None


# --------------------------------------------------------------------------------------
# Primary and secondary keywords
# --------------------------------------------------------------------------------------


def _ranked(*rows: tuple[str, int]) -> list[keyword_rank.RankedKeyword]:
    return keyword_rank.rank_keywords({"keyword": k, "volume": v, "difficulty": 20, "trend": None} for k, v in rows)


def test_the_bedroom_accessories_example() -> None:
    ranked = _ranked(
        ("bedroom accessories brisbane", 900),
        ("bedroom accessories", 800),
        ("bedroom accessories australia", 300),
        ("buy bedroom accessories", 250),
        ("best bedroom accessories", 200),
        ("bedroom accessories online", 150),
        ("bedroom", 50_000),
        ("bedside tables", 2000),
    )
    groups = keyword_rank.group_keywords(ranked, seeds=["bedroom accessories"])
    by_primary = {g.primary.keyword: [k.keyword for k in g.secondary] for g in groups}
    assert set(by_primary["bedroom accessories"]) == {
        "bedroom accessories brisbane",
        "bedroom accessories australia",
        "buy bedroom accessories",
        "best bedroom accessories",
        "bedroom accessories online",
    }
    assert "bedside tables" in by_primary
    # A bare word is not a primary unless it is a seed, so it cannot swallow everything above.
    assert "bedroom" not in by_primary


def test_a_one_word_service_is_a_primary_and_a_plural_joins_its_group() -> None:
    groups = keyword_rank.group_keywords(_ranked(("seo", 5000), ("seo melbourne", 900), ("seos", 10)), seeds=["seo"])
    assert [(g.primary.keyword, [k.keyword for k in g.secondary]) for g in groups] == [("seo", ["seo melbourne", "seos"])]


def test_the_service_is_always_a_primary_and_takes_its_own_secondaries() -> None:
    groups = keyword_rank.group_keywords(
        _ranked(("social media marketing", 5000), ("social media", 9000), ("social media marketing agency", 400)),
        seeds=["social media marketing"],
    )
    by_primary = {g.primary.keyword: [k.keyword for k in g.secondary] for g in groups}
    assert by_primary["social media marketing"] == ["social media marketing agency"]
    assert by_primary["social media"] == []


# --------------------------------------------------------------------------------------
# The demand map: alternate names seeded, fingerprint unchanged
# --------------------------------------------------------------------------------------


def _config(**kw: Any) -> keywords.KeywordRunConfig:
    return keywords.KeywordRunConfig("Acme", "", "Australia", "English", {"SEO": ["seo", "seo services"]}, **kw)


def test_alternate_names_are_seeded_but_never_change_the_fingerprint() -> None:
    plain, named = _config(), _config(synonyms={"SEO": ["organic search", "seo"]})
    assert plain.fingerprint() == named.fingerprint()
    assert named.seed_map() == {"SEO": ["seo", "seo services", "organic search"]}
    assert named.is_synonym("SEO", "organic search") and not named.is_synonym("SEO", "seo")
    assert "organic" in keywords.build_vocabulary(named)


def test_an_alternate_name_is_expanded_by_phrase_and_related_only() -> None:
    calls: list[tuple[str, str]] = []

    class Fake:
        async def overview(self, kws: list[str], country: str) -> dict:
            return {k.lower(): DfsKeyword(k, 100, 20, trend=5) for k in kws}

        async def expand(self, seed: str, match_class: str, country: str, limit: int) -> list[DfsKeyword]:
            calls.append((seed, match_class))
            return [DfsKeyword(f"{seed} x", 10, 10, trend=-3)]

    raw = asyncio.run(keywords.fetch_keywords(_config(synonyms={"SEO": ["organic search"]}), Fake()))  # type: ignore[arg-type]
    assert sorted(c for s, c in calls if s == "organic search") == ["phrase", "related"]
    assert sorted(c for s, c in calls if s == "seo") == ["broad", "phrase", "related"]
    assert {r.trend for r in raw if r.keyword == "organic search"} == {5}


# --------------------------------------------------------------------------------------
# The prompt block
# --------------------------------------------------------------------------------------

STORED = {
    "provider": "dataforseo",
    "service": "SEO",
    "location": "Australia",
    "service_synonyms": {"SEO": ["organic search"]},
    "keyword_report": {"service_hierarchy": [{"seeds": [{"seed": "seo"}, {"seed": "organic search"}]}]},
    "clean_keywords": [
        {"keyword": "seo", "volume": 9000, "difficulty": 60},
        {"keyword": "seo melbourne", "volume": 900, "difficulty": 30},
        {"keyword": "organic search", "volume": 700, "difficulty": 20},
        {"keyword": "unmeasured term", "volume": None, "difficulty": None},
    ],
}


def test_a_report_stored_before_ranking_is_ranked_on_read() -> None:
    targets = keyword_rank.targets_from_entry(STORED)
    assert [g.primary.keyword for g in targets.groups][:1] == ["seo"]
    assert [k.keyword for k in targets.groups[0].secondary] == ["seo melbourne"]
    assert all(g.primary.keyword != "unmeasured term" for g in targets.groups)


def test_the_stub_provider_never_reaches_a_prompt() -> None:
    assert keyword_rank.targets_from_entry({**STORED, "provider": "stub"}).groups == []
    assert keyword_rank.targets_block(keyword_rank.targets_from_entry({**STORED, "provider": "stub"})) == ""


def test_the_block_names_primaries_secondaries_and_the_rules() -> None:
    block = keyword_rank.targets_block(keyword_rank.targets_from_entry(STORED))
    assert block.startswith(f"===== BEGIN {keyword_rank.TARGETS_BLOCK} =====")
    assert "Primary keyword: seo (9,000/mo" in block
    assert "Secondary keywords: seo melbourne" in block
    assert "Buyers also call this service: organic search" in block
    assert "never repeat these numbers" in block
    assert block.rstrip().endswith(f"===== END {keyword_rank.TARGETS_BLOCK} =====")


@pytest.mark.parametrize("asset_id", ["funnel", "funnel_hub_media", "pillar_page", "lead_magnet", "blog", "webinar"])
def test_content_assets_must_use_their_secondary_keywords(asset_id: str) -> None:
    targets = keyword_rank.targets_from_entry(STORED)
    assert "must use its secondary keywords" in keyword_rank.targets_block(targets, asset_id)
    assert "must use its secondary keywords" not in keyword_rank.targets_block(targets, "sms_sequence")
    assert "must use its secondary keywords" not in keyword_rank.targets_block(targets)


def test_the_block_reaches_the_prompt_tail_and_never_the_cached_library() -> None:
    block = keyword_rank.targets_block(keyword_rank.targets_from_entry(STORED))
    library, tail = generation._prompt_parts("blog", {}, "phase1", business_brief=block)
    assert keyword_rank.TARGETS_BLOCK not in library
    assert block in tail and tail.index(block) < tail.index("— INPUTS")


def test_the_frontend_strips_an_echo_of_the_block() -> None:
    echo = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "promptEcho.ts").read_text(encoding="utf-8")
    assert keyword_rank.TARGETS_BLOCK in re.search(r'const NAMES = "([^"]+)"', echo).group(1).split("|")


def test_a_stage_reads_only_its_own_runs_report(monkeypatch: pytest.MonkeyPatch) -> None:
    keys: list[str] = []

    async def latest(_session: Any, _run: uuid.UUID, key: str):
        keys.append(key)
        return SimpleNamespace(value=STORED), _run

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(pipeline, "_latest_context_entry", latest)
    monkeypatch.setattr(pipeline, "get_sessionmaker", lambda: _Session)
    run = str(uuid.uuid4())
    targets = asyncio.run(pipeline._keyword_targets(run, "blog"))
    assert targets is not None and targets.groups and keys == [pipeline.KEYWORD_CONTEXT_KEY]
    assert pipeline.KEYWORD_CONTEXT_KEY in pipeline.PHASE_SCOPED_CONTEXT_KEYS
    assert asyncio.run(pipeline._keyword_targets(run, "icp")) is None
    assert asyncio.run(pipeline._keyword_targets(None, "blog")) is None


# --------------------------------------------------------------------------------------
# Content Marketing: each alternate name filtered against itself
# --------------------------------------------------------------------------------------


def test_content_keywords_seed_alternate_names_and_filter_each_against_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class Fake:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def keywords(self, endpoint: str, seed: str, location: str, *, limit: int = 30, language: str = "English"):
            calls.append((endpoint, seed))
            if seed == "organic search":
                return [DfsKeyword("organic search", 700, 20), DfsKeyword("organic search services", 200, 20, trend=40)]
            return [DfsKeyword("seo", 9000, 60), DfsKeyword("seo services", 1200, 40)]

        async def aclose(self) -> None:
            pass

    async def names(service: str, industry: str = "", on_usage: Any = None, **_kw: Any):
        return service_synonyms.ServiceSynonyms(service, ("organic search",), "claude")

    monkeypatch.setattr(dataforseo_client, "DataForSEOClient", Fake)
    monkeypatch.setattr(dataforseo_client, "is_configured", lambda: True)
    monkeypatch.setattr(service_synonyms, "synonyms_for", names)
    data = asyncio.run(content_keywords.fetch("SEO", "Melbourne, Australia"))

    assert (dataforseo_client.SUGGESTIONS, "organic search") in calls
    assert (dataforseo_client.RELATED, "organic search") not in calls
    kept = [r.keyword for r in data.rows]
    # Found through "organic search", which shares no word with "SEO": kept, because it was checked
    # against the name it was found through.
    assert "organic search services" in kept and "organic search" in kept
    rendered = content_keywords.render_md(data)
    assert "Alternate names also seeded: organic search" in rendered
    assert "Primary and secondary keywords" in rendered and "| Momentum |" in rendered


def test_the_run_build_seeds_alternate_names_with_the_real_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DataForSEO configured, the build route adds alternate names before clustering. That branch
    is skipped on the stub provider every other test runs on, and it shipped calling an unimported
    `replace`: every real build crashed there, and no run got a keyword map."""
    seen: dict[str, Any] = {}

    class Done(Exception):
        pass

    async def names(service: str, industry: str = "", on_usage: Any = None, **_kw: Any):
        return service_synonyms.ServiceSynonyms(service, ("ai seo",), "claude")

    async def build(config: keywords.KeywordRunConfig, on_usage: Any = None):
        seen["config"] = config
        raise Done

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def get(self, *_a: Any) -> object:
            return object()

    async def no_entry(*_a: Any) -> None:
        return None

    monkeypatch.setattr(keywords, "provider_name", lambda: "dataforseo")
    monkeypatch.setattr(service_synonyms, "synonyms_for", names)
    monkeypatch.setattr(keywords, "build_keyword_report", build)
    monkeypatch.setattr(pipeline, "get_sessionmaker", lambda: Session)
    monkeypatch.setattr(pipeline, "_stored_keyword_entry", no_entry)
    payload = pipeline.BuildKeywordsRequest(
        phase="phase1",
        profile={"industry": "Digital-Marketing - AI optimised SEO", "region": "Melbourne VIC Australia"},
    )
    with pytest.raises(Exception) as raised:
        asyncio.run(pipeline.build_run_keywords(str(uuid.uuid4()), payload))

    assert raised.type is Done or "Done" in repr(raised.value.__cause__ or raised.value)
    config = seen["config"]
    # The parent menu label is not part of the service: seeded whole, nobody searches it.
    assert list(config.services) == ["AI optimised SEO"]
    assert config.services["AI optimised SEO"][0] == "ai optimised seo"
    assert config.synonyms == {"AI optimised SEO": ["ai seo"]}


@pytest.mark.parametrize(
    ("keyword", "kept"),
    [
        ("ai seo tools", True),
        ("seo company melbourne", True),
        ("ai powered seo services", True),
        ("search optimisation tips", True),
        ("ai detector", False),
        ("character ai", False),
        ("google ai studio", False),
        ("ai agency", False),
        ("what is ai", False),
        ("seo", True),
    ],
)
def test_a_search_must_be_about_the_service_not_one_shared_word(keyword: str, kept: bool) -> None:
    config = keywords.config_from_profile({"industry": "Digital-Marketing - AI optimised SEO", "region": "Australia"})
    assert config is not None
    config = keywords.replace(config, synonyms={"AI optimised SEO": ["ai powered seo", "intelligent search optimisation"]})
    assert keywords.on_service(keyword, config) is kept
