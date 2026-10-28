"""Better keywords for a blog post, measured against the client's page and the pages that rank.

The Blog asks for a primary keyword and supporting keywords. Typed cold, the primary is usually the
topic's own working keyword, chosen without knowing what the client's page already ranks for or what
the pages beating it rank for. This card answers "is there a better keyword?" with measurements:

1. **Who competes.** The Blog's approved competitor listing when the run has one (Phase 2 researches
   it before the intake). Otherwise the pages in Google's organic top 10 for the seed keyword
   (DataForSEO SERP, 1 request), which in Phase 1 is the only competitor set available this early:
   the Blog's own competitor search comes at the end of its intake.
2. **What each page ranks for.** DataForSEO `ranked_keywords` for the client's reference page and up
   to `MAX_COMPETITOR_PAGES` competitor pages: keyword, position, volume, difficulty, intent.
3. **Candidates.** Those rankings, plus the seed's phrase matches and related searches, kept only
   when on-topic (`secondary_keywords.filter_suggestions`'s rule).
4. **The current keyword** is measured too (one `keyword_overview` request when no read reached it),
   so every suggestion is compared with what the operator has now.

**What "better" means** (`_score`): more search demand at lower difficulty, weighted by what the
comparison shows —
  - `gap`: a competitor ranks top 20 for it and the client's page does not (the strongest signal);
  - `improve`: the client's page ranks, but past page one (11-100);
  - `new`: nobody compared ranks for it;
  - `owned`: the client already ranks top 10 — shown for context, never suggested as primary.
Informational and commercial intent suit a blog post and are weighted up; transactional down.

A failure says why; measured numbers only (a missing volume stays None).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from app.services import dataforseo_client, keyword_rank, keywords
from app.services.secondary_keywords import SecondaryKeyword, _names_another_country

logger = logging.getLogger(__name__)

MAX_COMPETITOR_PAGES = 3
#: Rows read per page ranked, before the on-topic filter.
_RANKED_LIMIT = 80
_EXPAND_LIMIT = 40
#: Suggestions per list.
MAX_PRIMARY = 6
MAX_SECONDARY = 15

_INTENT_WEIGHT = {"informational": 1.2, "commercial": 1.1, "navigational": 0.6, "transactional": 0.8}
_STATUS_WEIGHT = {"gap": 1.6, "improve": 1.3, "new": 1.0, "owned": 0.3}


@dataclass
class CompetitorRank:
    domain: str
    position: int | None
    url: str


@dataclass
class KeywordOpportunity:
    keyword: str
    #: DataForSEO's volume and difficulty; None when it has no number (or did not answer).
    dataforseo_volume: int | None = None
    dataforseo_difficulty: int | None = None
    cpc: float | None = None
    intent: str | None = None
    #: The client's reference page's position for it, when it ranks.
    reference_position: int | None = None
    competitors: list[CompetitorRank] = field(default_factory=list)
    #: `gap` | `improve` | `new` | `owned`.
    status: str = "new"
    score: float = 0.0
    #: Search momentum, % change (`dataforseo_client._trend`). None when DataForSEO gave none.
    trend: int | None = None
    #: Where the keyword came from: "reference page", "competitor", "related searches".
    sources: list[str] = field(default_factory=list)

    @property
    def volume(self) -> int | None:
        return self.dataforseo_volume

    @property
    def difficulty(self) -> int | None:
        return self.dataforseo_difficulty


@dataclass
class ComparedPage:
    role: str  # "reference" | "competitor"
    url: str
    domain: str
    #: How many keywords it ranks for (as read, before the topic filter); None when the read failed.
    ranked: int | None = None
    on_topic: int | None = None
    error: str = ""


@dataclass
class ProviderStatus:
    status: str  # "ok" | "unconfigured" | "error"
    error: str = ""


@dataclass
class BlogKeywordSuggestions:
    seed: str
    location: str
    #: The keyword the operator has now (the topic's own), measured the same way, for comparison.
    current: KeywordOpportunity | None
    primary: list[KeywordOpportunity]
    secondary: list[KeywordOpportunity]
    compared: list[ComparedPage]
    #: Where the competitor pages came from: "listing" (the Blog's approved competitors) or "serp".
    competitor_source: str
    dataforseo: ProviderStatus
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("primary", "secondary"):
            for row, item in zip(out[key], getattr(self, key)):
                row["volume"], row["difficulty"] = item.volume, item.difficulty
        if self.current is not None:
            out["current"]["volume"], out["current"]["difficulty"] = self.current.volume, self.current.difficulty
        return out


def _host(url: str) -> str:
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.")


def _target(url: str, role: str) -> str:
    """What to read for a page. The client's home page is read as the whole site, so a keyword it
    already ranks for with another page counts as "you rank". A competitor is always read as the page
    that ranked: measured live, reading StudioHawk's site-wide filled an AI SEO blog's suggestions with
    its Sydney pages ("seo services sydney")."""
    parsed = urlparse(url if "//" in url else f"https://{url}")
    if role == "reference" and parsed.path in ("", "/"):
        return parsed.netloc.removeprefix("www.")
    return url if "//" in url else f"https://{url}"


#: Country names people search by abbreviation. `_names_another_country` knows the full names only,
#: and measured live it let "seo services in usa" through for an Australian blog.
_COUNTRY_ABBREVIATIONS = {"usa": "United States", "uk": "United Kingdom", "nz": "New Zealand", "uae": "United Arab Emirates"}


def _another_country(keyword: str, country: str) -> bool:
    words = set(keyword.split())
    return _names_another_country(keyword, country) or any(
        abbr in words and name != country for abbr, name in _COUNTRY_ABBREVIATIONS.items()
    )


#: Major cities per country, for one rule: a keyword naming a city the client's region does not is
#: a keyword for someone else's market. Measured live: the top-10 page for "ai seo services" in
#: Australia was StudioHawk's /seo-sydney/, and its keywords led a Melbourne client's suggestions
#: ("seo services sydney"). Curated rather than fetched — a city list API would be a request per card
#: for a rule this small. A city not listed here is simply not filtered.
_MAJOR_CITIES: dict[str, tuple[str, ...]] = {
    "Australia": ("sydney", "melbourne", "brisbane", "perth", "adelaide", "canberra", "hobart", "darwin",
                  "gold coast", "newcastle", "geelong", "wollongong", "sunshine coast", "townsville", "cairns"),
    "United States": ("new york", "los angeles", "chicago", "houston", "phoenix", "philadelphia", "san antonio",
                      "san diego", "dallas", "austin", "san francisco", "seattle", "denver", "boston", "miami",
                      "atlanta", "las vegas", "orlando", "nashville", "portland"),
    "United Kingdom": ("london", "manchester", "birmingham", "leeds", "glasgow", "liverpool", "bristol",
                       "edinburgh", "sheffield", "cardiff", "belfast", "nottingham", "leicester"),
    "Canada": ("toronto", "montreal", "vancouver", "calgary", "edmonton", "ottawa", "winnipeg", "quebec city"),
    "New Zealand": ("auckland", "wellington", "christchurch", "hamilton", "tauranga", "dunedin"),
    "India": ("mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai", "kolkata", "pune", "ahmedabad"),
}


def _another_city(keyword: str, region: str, country: str) -> bool:
    text = f" {keyword} "
    served = region.lower()
    return any(f" {city} " in text and city not in served for city in _MAJOR_CITIES.get(country, ()))


def _display(keyword: str) -> str:
    """The keyword as searched, lowercased. `keywords.normalize` also singularises, which is right for
    matching duplicates and wrong to show: "ai seo services" is not what anyone typed as "ai seo service"."""
    return " ".join(keyword.lower().split())


def _on_topic(seed: str, keyword: str) -> bool:
    """Shares content words with the seed: two, or one for a one-word seed (`filter_suggestions`'
    rule, measured on real DataForSEO rows)."""
    seed_tokens = {keywords.singular(t) for t in keywords.tokens(keywords.normalize(seed))}
    need = min(2, len(seed_tokens))
    norm = keywords.normalize(keyword)
    if not norm or keywords.is_noise(norm, keyword) or len(norm.split()) < 2:
        return False
    return len({keywords.singular(t) for t in keywords.tokens(norm)} & seed_tokens) >= need


def _momentum_weight(trend: int | None) -> float:
    """0.8-1.2 from search momentum (`keyword_rank.virality_score`); 1.0 when there is no trend, so a
    keyword DataForSEO has no history for is neither rewarded nor marked down."""
    virality = keyword_rank.virality_score(trend)
    return 1.0 if virality is None else 0.8 + virality / 250


def _score(item: KeywordOpportunity) -> float:
    volume = item.volume or 0
    kd = item.difficulty if item.difficulty is not None else 50
    ease = 1 - min(kd, 100) / 100 * 0.7
    intent = _INTENT_WEIGHT.get((item.intent or "").lower(), 1.0)
    return round(volume * ease * intent * _STATUS_WEIGHT[item.status] * _momentum_weight(item.trend), 1)


def _classify(item: KeywordOpportunity) -> str:
    ref = item.reference_position
    if ref is not None and ref <= 10:
        return "owned"
    if ref is not None:
        return "improve"
    if any(c.position is not None and c.position <= 20 for c in item.competitors):
        return "gap"
    return "new"


async def _serp_competitors(dfs, seed: str, country: str, client_host: str) -> list[str]:
    results = await dfs.serp(seed, country)
    urls: list[str] = []
    seen: set[str] = set()
    for r in results:
        host = _host(r.url)
        if not host or host == client_host or host in seen:
            continue
        seen.add(host)
        urls.append(r.url)
        if len(urls) >= MAX_COMPETITOR_PAGES:
            break
    return urls


async def suggest(
    seed: str,
    region: str,
    *,
    reference_url: str = "",
    competitor_urls: list[str] | None = None,
    current_keyword: str = "",
) -> BlogKeywordSuggestions:
    seed = " ".join(seed.split())
    current_keyword = " ".join((current_keyword or seed).split())
    country = keywords.match_location(region, dataforseo_client.COUNTRIES)
    empty = dict(current=None, primary=[], secondary=[], compared=[], competitor_source="")
    if not country:
        why = (
            f"Region {region!r} does not name a country, so no keyword data was requested. "
            'Add the country to the region answer (for example "Melbourne, Australia").'
            if region.strip()
            else 'No region is on record for this client. Set one with a country (for example "Melbourne, Australia").'
        )
        return BlogKeywordSuggestions(seed, "", **empty, dataforseo=ProviderStatus("error", why))
    if not dataforseo_client.is_configured():
        return BlogKeywordSuggestions(
            seed, country, **empty,
            dataforseo=ProviderStatus("unconfigured", "DataForSEO is not configured (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD), and it reads the pages to compare."),
        )

    client_host = _host(reference_url) if reference_url else ""
    notes: list[str] = []
    pool: dict[str, KeywordOpportunity] = {}

    def add(keyword: str, source: str) -> KeywordOpportunity | None:
        shown = _display(keyword)
        if not _on_topic(seed, keyword) or _another_country(shown, country) or _another_city(shown, region, country):
            return None
        key = keywords.dedupe_key(keywords.normalize(keyword))
        item = pool.get(key)
        if item is None:
            item = pool[key] = KeywordOpportunity(keyword=_display(keyword))
        if source not in item.sources:
            item.sources.append(source)
        return item

    dfs = dataforseo_client.DataForSEOClient()
    pages: list[ComparedPage] = []
    dfs_status = ProviderStatus("ok")
    competitor_source = ""
    try:
        competitor_source = "listing"
        urls = [u for u in (competitor_urls or []) if u and _host(u) != client_host][:MAX_COMPETITOR_PAGES]
        if not urls:
            competitor_source = "serp"
            try:
                urls = await _serp_competitors(dfs, seed, country, client_host)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Google's results for {seed!r} could not be read, so no competitor pages were compared: {exc}")

        pages = ([ComparedPage("reference", reference_url, client_host)] if reference_url else []) + [
            ComparedPage("competitor", u, _host(u)) for u in urls
        ]
        reads = await asyncio.gather(
            *(dfs.ranked_keywords(_target(p.url, p.role), country, limit=_RANKED_LIMIT) for p in pages),
            dfs.keywords(dataforseo_client.SUGGESTIONS, seed, country, limit=_EXPAND_LIMIT),
            dfs.keywords(dataforseo_client.RELATED, seed, country, limit=_EXPAND_LIMIT),
            return_exceptions=True,
        )
        for page, result in zip(pages, reads[: len(pages)]):
            if isinstance(result, BaseException):
                page.error = str(result)
                continue
            page.ranked = len(result)
            on_topic = 0
            for row in result:
                item = add(row.keyword, "reference page" if page.role == "reference" else "competitor")
                if item is None:
                    continue
                on_topic += 1
                item.dataforseo_volume = item.dataforseo_volume if item.dataforseo_volume is not None else row.volume
                item.dataforseo_difficulty = item.dataforseo_difficulty if item.dataforseo_difficulty is not None else row.difficulty
                item.cpc = item.cpc if item.cpc is not None else row.cpc
                item.intent = item.intent or row.intent
                item.trend = item.trend if item.trend is not None else row.trend
                if page.role == "reference":
                    if row.position is not None and (item.reference_position is None or row.position < item.reference_position):
                        item.reference_position = row.position
                elif not any(c.domain == page.domain for c in item.competitors):
                    item.competitors.append(CompetitorRank(page.domain, row.position, row.url))
            page.on_topic = on_topic
        for result in reads[len(pages):]:
            if isinstance(result, BaseException):
                notes.append(f"DataForSEO's keyword suggestions failed: {result}")
                continue
            for row in result:
                item = add(row.keyword, "related searches")
                if item is None:
                    continue
                item.dataforseo_volume = item.dataforseo_volume if item.dataforseo_volume is not None else row.volume
                item.dataforseo_difficulty = item.dataforseo_difficulty if item.dataforseo_difficulty is not None else row.difficulty
                item.cpc = item.cpc if item.cpc is not None else row.cpc
                item.intent = item.intent or row.intent
                item.trend = item.trend if item.trend is not None else row.trend
        if pages and all(p.error for p in pages) and all(isinstance(r, BaseException) for r in reads[len(pages):]):
            dfs_status = ProviderStatus("error", pages[0].error)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Blog keywords: DataForSEO failed for %r: %s", seed, exc)
        dfs_status = ProviderStatus("error", str(exc))
    finally:
        await dfs.aclose()

    current = add(current_keyword, "current keyword") if current_keyword else None
    if current is not None:
        # Shown as the operator has it, whichever spelling a provider's row reached the pool with.
        current.keyword = _display(current_keyword)
    if current is None and current_keyword:
        current = KeywordOpportunity(keyword=_display(current_keyword), sources=["current keyword"])

    # The current keyword, measured the same way when no read above reached it: one overview request.
    if current is not None and current.dataforseo_volume is None and dfs_status.status == "ok":
        measure = dataforseo_client.DataForSEOClient()
        try:
            m = (await measure.overview([current.keyword], country)).get(current.keyword.lower())
            if m is not None:
                current.dataforseo_volume, current.dataforseo_difficulty = m.volume, m.difficulty
                current.trend = m.trend
        except Exception as exc:  # noqa: BLE001 — the comparison stands without it
            notes.append(f"The current keyword could not be measured: {exc}")
        finally:
            await measure.aclose()

    for item in [*pool.values(), *([current] if current else [])]:
        item.status = _classify(item)
        item.score = _score(item)

    ranked = sorted(
        (i for i in pool.values() if i is not current and i.volume is not None),
        key=lambda i: (-i.score, i.keyword),
    )
    primary = [i for i in ranked if i.status != "owned"][:MAX_PRIMARY]
    chosen = {i.keyword for i in primary}
    secondary = [i for i in ranked if i.keyword not in chosen][:MAX_SECONDARY]

    if any(p.role == "reference" and not p.error and not p.on_topic for p in pages):
        notes.append(
            "The client's reference page ranks for nothing on this topic yet, so every suggestion is a new "
            "opportunity for it — the keywords competitors already rank for are the strongest."
        )

    logger.info(
        "Blog keywords seed=%r country=%s pool=%s primary=%s compared=%s dfs=%s",
        seed, country, len(pool), len(primary), len(pages), dfs_status.status,
    )
    return BlogKeywordSuggestions(
        seed=seed,
        location=country,
        current=current,
        primary=primary,
        secondary=secondary,
        compared=pages,
        competitor_source=competitor_source,
        dataforseo=dfs_status,
        notes=notes,
    )
