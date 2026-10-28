"""Keyword data for the Content Marketing Strategy, measured by DataForSEO instead of asked for.

The stage's `keyword_search_volume_data_source_optional` input asked the operator to paste a keyword
or Search Console export. Left blank, the prompt estimates every volume and labels it an estimate,
so the topic clusters were built on guesses. Here the input is filled before generation from the
stage's own pillar service, in the client's country:

- **The service and its alternate names.** `service_synonyms` names the other ways people search
  for the service (one Haiku call). The service gets `keyword_suggestions` and `related_keywords`
  (depth 1); each alternate name gets `keyword_suggestions`. Two requests plus one per name, never
  a loop over keywords.
- **The Pillar Page's filter** (`secondary_keywords.filter_suggestions`), applied per seed: a
  keyword found through "search engine optimisation" shares no words with "SEO", so it is checked
  against the name it was found through. On-topic, deduped, no other country's market.
- **Ranked and grouped** (`keyword_rank`): best first by searches, estimated traffic and search
  momentum, then as primary keywords with their secondary keywords.
- **Measured numbers only.** A volume DataForSEO does not publish is "—", never 0.
- **No country, no request**, the same rule as `keywords.resolve_location`. Every failure is one
  sentence; the stage then falls back to the prompt's own "estimate and label" rule.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date

from app.services import dataforseo_client, keyword_rank, keywords, service_synonyms
from app.services.generation import OnUsage
from app.services.secondary_keywords import SecondaryKeyword, filter_suggestions

logger = logging.getLogger(__name__)

ASSET_ID = "content_marketing_strategy"
FIELD_ID = "keyword_search_volume_data_source_optional"
SERVICE_FIELD_ID = "primary_service_pillar_page_being_supported"
REGION_FIELD_ID = "region_country"

#: Rows kept for the document, after filtering. Enough for several clusters of 4-8 terms.
MAX_KEYWORDS = 40
#: Rows asked for per request, before filtering.
_REQUEST_LIMIT = 100
#: Rows asked for per alternate name: its own phrase matches, not a second full sweep.
_SYNONYM_REQUEST_LIMIT = 40
#: A seed longer than this is a sentence, not a keyword (the same bound the secondary-terms card uses).
_MAX_SEED_CHARS = 80

#: Answers that mean "nothing supplied", so the field is filled.
_EMPTY_ANSWERS = {"", "n/a", "na", "none", "skip", "skipped", "-", "—"}


@dataclass
class ContentKeywordData:
    seed: str
    country: str
    head: SecondaryKeyword | None
    rows: list[SecondaryKeyword]
    #: The alternate names seeded beside the service.
    synonyms: tuple[str, ...] = ()


class ContentKeywordError(RuntimeError):
    """One sentence for the operator: why no keyword data was fetched."""


def wants_fill(answer: str | None) -> bool:
    """Blank, skipped or an unresolved context marker: the operator supplied no export of their own."""
    text = (answer or "").strip()
    return text.lower() in _EMPTY_ANSWERS or text.startswith("[[context:")


def seed_from_service(answer: str) -> str:
    """"Social Media Marketing — /social-media-marketing" -> "Social Media Marketing".

    The field asks for the service *and* its page, so the URL or slug is cut off at the first dash,
    comma, bracket or link, and any leftover word holding a slash or a domain is dropped."""
    head = re.split(r"\s[—–-]\s|[,(|]|https?://", answer or "", maxsplit=1)[0]
    words = [w for w in head.split() if "/" not in w and not re.search(r"\.[a-z]{2,}$", w, re.IGNORECASE)]
    return " ".join(words).strip(" \"'“”.:;")


def _vol(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _cpc(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def render_md(data: ContentKeywordData) -> str:
    names = f' Alternate names also seeded: {", ".join(data.synonyms)}.' if data.synonyms else ""
    lines = [
        f"Keyword data — DataForSEO (Google, {data.country}, English), measured {date.today().isoformat()}.",
        f'Seed: "{data.seed}".{names} Method: keyword_suggestions + related_keywords (depth 1), filtered to '
        "on-topic terms, ranked by searches, estimated traffic and search momentum.",
        "Volumes are measured average monthly searches; KD is DataForSEO keyword difficulty (0-100); "
        "est. traffic is searches × the click-through rate at the position that difficulty usually "
        "allows; momentum is the % change in searches; CPC is in USD. "
        '"—" means DataForSEO published no figure, not zero — do not estimate one.',
        "",
    ]
    if data.head is not None:
        lines.append(
            f"Head term: {data.head.keyword} — volume {_vol(data.head.volume)}, KD {_vol(data.head.difficulty)}, "
            f"CPC {_cpc(data.head.cpc)}"
        )
        lines.append("")
    groups = keyword_rank.group_keywords(
        keyword_rank.rank_keywords(r for r in data.rows if r.volume is not None), [data.seed, *data.synonyms]
    )
    if groups:
        lines += [
            "Primary and secondary keywords:",
            "",
            "| Primary keyword | Monthly volume | Secondary keywords |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {g.primary.keyword} | {_vol(g.primary.volume)} | "
            f"{', '.join(f'{k.keyword} ({_vol(k.volume)})' for k in g.secondary) or '—'} |"
            for g in groups
        ]
        lines.append("")
    lines += [
        "| Rank | Keyword | Monthly volume | KD | Est. traffic | Momentum | CPC (USD) | Intent |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {i} | {r.keyword} | {_vol(r.volume)} | {_vol(r.difficulty)} | {_vol(r.est_traffic)} "
        f"| {keyword_rank.momentum(r.trend)} | {_cpc(r.cpc)} | {r.intent or '—'} |"
        for i, r in enumerate(data.rows, start=1)
    ]
    return "\n".join(lines) + "\n"


async def fetch(
    service_answer: str, region: str, *, industry: str = "", on_usage: OnUsage | None = None
) -> ContentKeywordData:
    """Raises `ContentKeywordError` with one sentence on any failure, before or after a request."""
    seed = seed_from_service(service_answer)
    if not seed or len(seed) > _MAX_SEED_CHARS:
        raise ContentKeywordError(
            "The pillar service answer does not name a usable keyword, so no DataForSEO data was requested."
        )
    country = keywords.match_location(region, dataforseo_client.COUNTRIES)
    if not country:
        raise ContentKeywordError(
            f"Region {region!r} does not name a country, so no DataForSEO data was requested. "
            'Add the country to the region answer (for example "Melbourne, Australia").'
            if region.strip()
            else "No region is on record for this client, so no DataForSEO data was requested."
        )
    if not dataforseo_client.is_configured():
        raise ContentKeywordError("DataForSEO is not configured (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD).")

    names = (await service_synonyms.synonyms_for(seed, industry, on_usage, market=region)).synonyms

    def rows_of(found: list[dataforseo_client.DfsKeyword]) -> list[SecondaryKeyword]:
        return [SecondaryKeyword(k.keyword, k.volume, k.difficulty, k.cpc, k.intent, k.trend) for k in found]

    client = dataforseo_client.DataForSEOClient()
    try:
        phrase, related, *by_name = await asyncio.gather(
            client.keywords(dataforseo_client.SUGGESTIONS, seed, country, limit=_REQUEST_LIMIT),
            client.keywords(dataforseo_client.RELATED, seed, country, limit=_REQUEST_LIMIT),
            *(client.keywords(dataforseo_client.SUGGESTIONS, name, country, limit=_SYNONYM_REQUEST_LIMIT) for name in names),
            return_exceptions=True,
        )
    finally:
        await client.aclose()
    # The service's own two requests decide success; an alternate name that fails costs only itself.
    for result in (phrase, related):
        if isinstance(result, BaseException):
            logger.warning("DataForSEO content keywords failed for %r: %s", seed, result)
            raise ContentKeywordError(str(result) or result.__class__.__name__) from result

    found = rows_of([*phrase, *related])
    seed_key = keywords.dedupe_key(keywords.normalize(seed))
    head = next((r for r in found if keywords.dedupe_key(keywords.normalize(r.keyword)) == seed_key), None)
    # Each seed's results are filtered against that seed, then merged, ranked and capped.
    candidates = filter_suggestions(seed, found, country, limit=MAX_KEYWORDS)
    for name, result in zip(names, by_name):
        if isinstance(result, BaseException):
            logger.warning("DataForSEO content keywords failed for alternate name %r: %s", name, result)
            continue
        named = rows_of(result)
        named_head = next((r for r in named if r.keyword.lower() == name), None)
        candidates += ([named_head] if named_head else []) + filter_suggestions(name, named, country, limit=MAX_KEYWORDS)
    seen: set[str] = {seed_key}
    rows: list[SecondaryKeyword] = []
    for row in sorted(candidates, key=lambda r: (r.score is None, -(r.score or 0), -(r.volume or 0), r.keyword)):
        key = keywords.dedupe_key(keywords.normalize(row.keyword))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows = rows[:MAX_KEYWORDS]
    if not rows and head is None:
        raise ContentKeywordError(f'DataForSEO returned no keywords for "{seed}" in {country}.')
    return ContentKeywordData(seed, country, head, rows, tuple(names))
