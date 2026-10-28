"""Suggestions for two CRO intake questions, from real search data and the client's own page.

- **"Words the buyer uses for the outcome"** (`outcome_words`). Their words for what they want, not
  the industry's. Searches are the largest record of buyers' own words, so the suggestions come
  from them.
  - **Clusters around the service, never generic goals.** This has broken twice. Seeded with plain
    outcomes, "AI-Optimised SEO" came back as "grow my business" and "increase online sales". Then,
    held to outcome words only, it offered "rank higher on google" but nothing about SEO or AI SEO
    itself. The operator wants the service's own territory: for AI-Optimised SEO "ai seo", "how to use
    ai for seo"; for Social Media Marketing "instagram ads", "meta ads".
  - **The service's lexicon** (`ServiceProfile`, one Haiku call: `profile_prompt`, `parse_profile`):
    - **core**: its short names as searched (seo, ai seo, search engine optimisation);
    - **facets**: its topics, parts and channels, the paid side included (ai seo tools; instagram
      ads, meta ads);
    - **anchors**: where the result happens (google, ranking);
    - **distinct**: what the qualifier in the name adds (ai, chatgpt). Only a name with a qualifier
      has any (`qualifier_words`); the qualifier's own words count too.
    - A term made only of `GENERIC_WORDS`, or naming a job or course (`OFF_INTENT_WORDS`), is
      dropped in code. A bare list under one `- facet ||` label is read whole (`_profile_lines`).
    - The seeds are two core names then the facets, up to `MAX_TOPIC_SEEDS`. With no profile the
      service's own words are the anchors and the seed.
  - **Service names.** A sub-service picked with its parent ("Digital-Marketing - AI optimised SEO")
    is cut to its last part, and spellings of one name are one service (`_clean_services`).
  - **Measure.** The pool is real queries:
    - the run's own keyword report (free);
    - DataForSEO `keyword_suggestions` and `related_keywords` for each of the session's services;
    - `keyword_suggestions` around each seed, plus one `keyword_ideas` request over all of them.
      The seeds are only starting points: what is shown is what DataForSEO returns;
    - `ranked_keywords` for the landing page (what it is already found by).
  - **The relevance gate** (`on_service`). A search reaches the judge only if it holds one of the
    profile's terms as whole words, and no job or course word. A one-word term under four letters
    ("ai") counts only beside a core word: "kling ai" is an image app. How many were left out is a note.
  - **Judge.** One Haiku call groups the searches into clusters around the service's topics and the
    results buyers want from it. It refers to queries by number, so every phrase shown is a query
    DataForSEO measured; a number outside the pool is dropped. Searches naming the service are kept.
  - **Order.** Clusters about what sets the service apart (`OutcomeCluster.distinct`) lead, then
    the rest, each by combined volume.
- **"Words to avoid"** (`words_to_avoid`), two groups. Competitor names are not suggested: the
  operator asked for them off the card.
  - The page is read with `scraper.scrape_page`: free, then Context.dev's rendered read (1 credit)
    only when the free read is walled off.
  - **Jargon** on the landing page. Haiku proposes each with a plain alternative. A term is kept only
    if it is on the page, and only if DataForSEO shows buyers search the plain alternative more
    (one `keyword_overview` request for all of them). Jargon buyers do search is not jargon to them.
  - **Risky claims** on the page (guarantees, "#1", "best"). Kept only if the phrase is on the page.

Replies are one item per line, not JSON: page copy is full of quotes (see `asset_summary.py`).
Every failure is one sentence on the card. The card always lets the operator type their own words,
so a failure never blocks the question.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field

from app.services import dataforseo_client, keyword_rank, keywords, scraper
from app.services.claude_client import get_client
from app.services.generation import HAIKU, OnUsage
from app.services.secondary_keywords import _names_another_country
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

MODEL = HAIKU
_MAX_TOKENS = 1500
#: Services seeded per request, and rows asked for per DataForSEO request.
MAX_SERVICES = 3
_REQUEST_LIMIT = 50
#: Queries shown to the judge, best-ranked first.
_POOL_LIMIT = 150
MAX_CLUSTERS = 6
MAX_PHRASES_PER_CLUSTER = 8
#: Rows asked for around each seed (the service's names and topics).
_SEED_REQUEST_LIMIT = 30
#: Landing-page copy shown to the judge for words to avoid.
_PAGE_CHARS = 12_000
MAX_JARGON = 8
MAX_CLAIMS = 6


class BuyerLanguageError(RuntimeError):
    """One sentence for the card."""


@dataclass
class Phrase:
    keyword: str
    volume: int | None = None
    difficulty: int | None = None
    trend: int | None = None
    score: int | None = None


@dataclass
class OutcomeCluster:
    name: str
    phrases: list[Phrase] = field(default_factory=list)
    #: Holds a distinct anchor: an outcome of what sets this service apart from its plain version.
    distinct: bool = False

    @property
    def combined_volume(self) -> int:
        return sum(p.volume or 0 for p in self.phrases)


@dataclass
class AvoidItem:
    term: str
    #: `jargon` | `claim`. (`competitor` only on a card stored before names were dropped.)
    kind: str
    reason: str = ""
    #: Jargon only: the plain words buyers search instead, with both measured volumes.
    alternative: str = ""
    term_volume: int | None = None
    alternative_volume: int | None = None


@dataclass
class BuyerLanguage:
    mode: str
    location: str = ""
    clusters: list[OutcomeCluster] = field(default_factory=list)
    avoid: list[AvoidItem] = field(default_factory=list)
    #: Where the suggestions came from, in words for the card ("3 DataForSEO requests…").
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = asdict(self)
        for cluster, raw in zip(self.clusters, out["clusters"]):
            raw["combined_volume"] = cluster.combined_volume
        return out


# --------------------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------------------


def _country(region: str) -> str:
    country = keywords.match_location(region, dataforseo_client.COUNTRIES)
    if not country:
        raise BuyerLanguageError(
            f"Region {region!r} does not name a country, so no search data was requested. "
            'Add the country to the region (for example "Melbourne, Australia").'
            if region.strip()
            else "No region is on record for this client, so no search data was requested."
        )
    return country


def _clean_services(services: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for s in services:
        name = " ".join(str(s or "").split())
        # A picked sub-service arrives with its parent ("Digital-Marketing - AI optimised SEO", "SEO >
        # Local SEO"). The last part is the service; the parent would seed the whole parent market.
        name = re.split(r"\s+[-–—>|/]\s+|\s*›\s*", name)[-1].strip()
        # "AI-Optimised SEO" and "ai optimized seo" are one service, and one set of searches.
        key = tuple(_tokens(name))
        if name and len(name) <= 80 and key not in seen:
            seen.add(key)
            out.append(name)
    return out[:MAX_SERVICES]


def _lines(raw: str) -> list[list[str]]:
    """`- a || b || c` lines as their parts. Anything else is ignored."""
    out: list[list[str]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if re.match(r"^[-•*]\s+", line):
            out.append([part.strip() for part in re.sub(r"^[-•*]\s+", "", line).split("||")])
    return out


async def _ask(prompt: str, on_usage: OnUsage | None) -> str:
    started = time.monotonic()
    response = await get_client().messages.create(
        model=MODEL, max_tokens=_MAX_TOKENS, messages=[{"role": "user", "content": prompt}]
    )
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=MODEL, duration_ms=int((time.monotonic() - started) * 1000))
        )
    return next((b.text for b in response.content if b.type == "text"), "")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def _tokens(text: str) -> list[str]:
    """Words as compared everywhere here: lowercase, singular, -ize spelled -ise (optimized and
    optimised are one word to a buyer)."""
    out: list[str] = []
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        word = re.sub(r"iz(e|ed|es|ing|ation|ations)$", r"is\1", word)
        word = keyword_rank.singular(word)
        # ranking, ranked -> rank; listing -> list. Only where a real stem of 4+ letters is left.
        stem = re.sub(r"(ing|ed)$", "", word)
        out.append(stem if stem != word and len(stem) >= 4 else word)
    return out


# --------------------------------------------------------------------------------------
# Outcome words
# --------------------------------------------------------------------------------------


#: Words every business wants more of. An outcome made only of these describes no service in
#: particular, so none is an anchor: "grow my business" is not about SEO, roofing or tax.
GENERIC_WORDS = frozenset(
    _w
    for _w in """
    a an the my our your more most get getting increase increasing improve improving boost grow growing
    growth best top how to for of in on and with without fast faster quick quickly cheap free new
    business businesse company companie brand small local online web website site page traffic visitor
    sale sell selling revenue profit money income customer client lead enquiry inquiry booking order
    marketing advertising promotion result success help service agency expert professional
    """.split()
)
#: Anchors kept from one profile.
MAX_ANCHORS = 24
#: Searches seeded from the profile: the service's short names first, then its topics.
MAX_TOPIC_SEEDS = 8
#: Searches about a job, a course or a salary are about the trade, never the buyer.
OFF_INTENT_WORDS = frozenset(
    "job jobs career careers salary salaries course courses certification certificate internship hiring "
    "resume interview".split()
)


@dataclass
class ServiceProfile:
    """What this service is about, as buyers search it (`profile_prompt`, `parse_profile`)."""

    #: The service's own short names as searched ("seo", "ai seo", "search engine optimisation").
    core: list[str] = field(default_factory=list)
    #: Its topics and parts ("ai seo tools", "seo with ai"; for Social Media Marketing: "instagram ads",
    #: "meta ads").
    facets: list[str] = field(default_factory=list)
    #: Every term a search may hold to count as about the service: core, facets and the medium the
    #: result happens in ("google", "ranking").
    anchors: list[str] = field(default_factory=list)
    #: The terms for what the service's qualifier adds over its plain version.
    distinct: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)


def is_generic(term: str) -> bool:
    """Every word of `term` is something any business wants: never an anchor."""
    words = _tokens(term)
    return not words or all(w in GENERIC_WORDS for w in words)


def on_service(keyword: str, anchors: list[str], core: list[str] = ()) -> bool:
    """Whether a search holds one of the anchors, as whole words in order (singular, -ise/-ize).

    A one-word anchor of under four letters ("ai") is too common alone: "kling ai" is an image app.
    It counts only beside a word of a core name ("ai for seo"). With no core names it counts alone."""
    words = _tokens(keyword)
    core_words = {w for c in core for w in _tokens(c)}
    for anchor in anchors:
        need = _tokens(anchor)
        if not need or not any(words[i : i + len(need)] == need for i in range(len(words) - len(need) + 1)):
            continue
        if len(need) == 1 and len(need[0]) < 4 and core_words and not ((core_words - {need[0]}) & set(words)):
            continue
        return True
    return False


def fallback_anchors(services: list[str]) -> list[str]:
    """The services' own content words, when no profile could be made."""
    out: list[str] = []
    for service in services:
        for word in re.findall(r"[a-z0-9]+", service.lower()):
            if not is_generic(word) and len(word) > 1 and word not in out:
                out.append(word)
    return out


def profile_prompt(services: list[str], industry: str) -> str:
    return (
        f"Services: {', '.join(services)}. Industry: {industry or 'not stated'}.\n\n"
        "List the words buyers type into Google about THIS service: its names, its topics and parts, and "
        "the result it produces. Never generic business goals (grow my business, more sales, more leads, "
        "website traffic): every service promises those.\n\n"
        "Reply with lines in exactly these forms and nothing else:\n"
        "- core || <term>   2-4 lines. The service's own short names as people search them, 1-3 words "
        "(AI-Optimised SEO: seo, ai seo, search engine optimisation, generative engine optimisation).\n"
        "- facet || <term>   6-10 lines. The service's topics, parts, channels and ways of doing it, 1-4 "
        "words, as searched. Cover the paid side as well as the organic one wherever the service has both "
        "(ads on each platform it runs on). Examples (AI-Optimised SEO: ai seo tools, seo with ai, ai content optimisation, chatgpt "
        "seo, ai overviews; Social Media Marketing: instagram ads, meta ads, facebook ads, tiktok ads, "
        "social media content, social media management; Emergency Plumbing: burst pipe, blocked drain, hot water repair).\n"
        "- anchor || <term>   4-8 lines. Where the result happens, 1-2 words (SEO: google, ranking, search "
        "results, organic; roofing: roof, leak).\n"
        "- distinct || <term>   What the qualifier in the service's name adds over the plain service "
        "(AI-Optimised SEO: ai, chatgpt, ai overview, ai search, perplexity). None when the name has no "
        "qualifier.\n"
        "Use the plainest, most common wording, because an unusual phrasing has no search data."
    )


def _searchable(value: str, services: list[str]) -> bool:
    return bool(value) and not is_generic(value) and not (set(_tokens(value)) & OFF_INTENT_WORDS)


def _profile_lines(raw: str) -> list[tuple[str, str]]:
    """`(kind, term)` pairs. The model writes either `- facet || a` per line or one `- facet || a`
    followed by bare lines (`b`, `- c`): a bare line belongs to the kind above it. Measured live on
    Social Media Marketing: read as one term per kind, the profile kept "instagram ads" of 17 facets."""
    out: list[tuple[str, str]] = []
    kind = ""
    for line in (raw or "").splitlines():
        line = re.sub(r"^\s*[-•*]\s*", "", line).strip()
        if not line:
            continue
        if "||" in line:
            head, _, value = line.partition("||")
            kind = head.strip().lower()
            line = value
        if kind:
            for term in line.split(","):
                out.append((kind, " ".join(term.strip(" \"'").lower().split())))
    return out


def qualifier_words(services: list[str], core: list[str]) -> set[str]:
    """The words of the service's name beyond its shortest core name: the "AI-Optimised" of
    AI-Optimised SEO. Empty for "Social Media Marketing", whose name is its core."""
    names = set()
    for service in services:
        names |= set(_tokens(service))
    shortest = min((set(_tokens(c)) for c in core if set(_tokens(c)) <= names), key=len, default=set())
    return {w for w in names - shortest if w not in GENERIC_WORDS}


def parse_profile(raw: str, services: list[str]) -> ServiceProfile:
    """The profile, checked: a generic or off-intent term is dropped, every core term, facet and
    distinct term is also an anchor, a service whose name has no qualifier has no distinct terms,
    and the seeds are up to two core names, then the facets."""
    profile = ServiceProfile()
    lists = {"core": profile.core, "facet": profile.facets, "distinct": profile.distinct, "anchor": []}
    for kind, value in _profile_lines(raw):
        if kind not in lists or not value or len(value.split()) > 4 or not _searchable(value, services):
            continue
        if value not in lists[kind]:
            lists[kind].append(value)
    qualifier = qualifier_words(services, profile.core)
    if not qualifier:
        profile.distinct.clear()
    else:
        # The qualifier's own words ("ai", "optimised") are what sets the service apart too.
        for service in services:
            for word in re.findall(r"[a-z0-9]+", service.lower()):
                if _tokens(word)[0] in qualifier and word not in profile.distinct:
                    profile.distinct.append(word)
    for value in profile.core + profile.facets + profile.distinct + lists["anchor"]:
        if value not in profile.anchors:
            profile.anchors.append(value)
    profile.anchors = profile.anchors[:MAX_ANCHORS]
    profile.seeds = list(dict.fromkeys(profile.core[:2] + profile.facets))[:MAX_TOPIC_SEEDS]
    return profile


def names_a_service(keyword: str, services: list[str]) -> bool:
    """A search holding every word of a service's name is a search for the service ("shopify seo
    services"), not the buyer's words for what they want. Measured on words, singular or plural."""
    words = set(_tokens(keyword))
    for service in services:
        need = set(_tokens(service))
        if need and need <= words:
            return True
    return False


async def _search_pool(
    services: list[str], landing_url: str, country: str, outcome_seeds: list[str] = ()
) -> tuple[list[Phrase], list[str], list[str]]:
    """Measured queries around the services, the outcome seeds and the landing page:
    `(phrases, sources, notes)`."""
    client = dataforseo_client.DataForSEOClient()
    jobs: list[tuple[str, object]] = []
    try:
        for service in services:
            jobs.append((f'suggestions for "{service}"', client.keywords(dataforseo_client.SUGGESTIONS, service, country, limit=_REQUEST_LIMIT)))
            jobs.append((f'related searches for "{service}"', client.keywords(dataforseo_client.RELATED, service, country, limit=_REQUEST_LIMIT)))
        for seed in outcome_seeds:
            jobs.append((f'searches around "{seed}"', client.keywords(dataforseo_client.SUGGESTIONS, seed, country, limit=_SEED_REQUEST_LIMIT)))
        if outcome_seeds:
            # One request over every seed together: searches in the same topic, not only ones holding a
            # seed's exact words. Measured live: suggestions alone found 2 searches for AI-Optimised SEO.
            jobs.append(("ideas around the outcome searches", client.ideas(list(outcome_seeds), country, limit=_REQUEST_LIMIT)))
        if landing_url:
            jobs.append(("what the landing page ranks for", client.ranked_keywords(landing_url, country, limit=_REQUEST_LIMIT)))
        results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
    finally:
        await client.aclose()

    phrases: list[Phrase] = []
    sources: list[str] = []
    notes: list[str] = []
    for (label, _), result in zip(jobs, results):
        if isinstance(result, BaseException):
            notes.append(f"DataForSEO {label} failed: {result}")
            continue
        sources.append(f"{label} ({len(result)})")
        phrases += [Phrase(k.keyword, k.volume, k.difficulty, k.trend) for k in result]
    return phrases, sources, notes


def _rank_pool(phrases: list[Phrase], country: str) -> list[Phrase]:
    seen: set[str] = set()
    kept: list[Phrase] = []
    for p in phrases:
        norm = keywords.normalize(p.keyword)
        key = keywords.dedupe_key(norm) if norm else ""
        if not norm or key in seen or keywords.is_noise(norm, p.keyword) or _names_another_country(norm, country):
            continue
        seen.add(key)
        kept.append(Phrase(norm, p.volume, p.difficulty, p.trend))
    return [Phrase(r.keyword, r.volume, r.difficulty, r.trend, r.score) for r in keyword_rank.rank_keywords(kept, _POOL_LIMIT)]


def outcome_prompt(pool: list[Phrase], services: list[str], industry: str, profile: ServiceProfile | None = None) -> str:
    listed = "\n".join(f"{i}. {p.keyword}" for i, p in enumerate(pool, start=1))
    focus = ""
    if profile is not None:
        if profile.facets:
            focus += f"Its topics: {', '.join(profile.facets)}.\n"
        if profile.distinct:
            focus += (
                f"What sets it apart from the plain service: {', '.join(profile.distinct)}. Give those searches "
                "their own clusters.\n"
            )
    return (
        "Below are real Google searches people make around this business's service, numbered.\n"
        f"Services: {', '.join(services)}. Industry: {industry or 'not stated'}.\n{focus}\n"
        "Group the searches into clusters around THIS service, in the words buyers use: its own topics and "
        "parts (for AI-Optimised SEO: ai seo, how to use ai for seo, ai seo tools; for Social Media "
        "Marketing: instagram ads, meta ads) and the results buyers want from it (rank higher on google). "
        "Leave out searches about a job, a course, a competitor's brand or another service, and any "
        "outcome every business wants whatever it buys (grow my business, more sales).\n\n"
        f"Make at most {MAX_CLUSTERS} clusters. Name each cluster in the buyer's own plain words, 2-5 words, "
        "lowercase. List its searches by number only.\n\n"
        "Reply with only the clusters, one per line, in exactly this form and nothing else:\n"
        "- <cluster name> || <numbers, comma-separated>\n\n"
        f"SEARCHES\n{listed}"
    )


def parse_clusters(
    raw: str, pool: list[Phrase], services: list[str] = (), distinct: list[str] = ()
) -> tuple[list[OutcomeCluster], int]:
    """Clusters whose members are numbers in the pool. Returns the clusters and how many numbers
    were not in it (an invented query cannot reach the card). Searches naming the service are kept:
    the operator asked for clusters around the service ("ai seo", "how to use ai for seo"), and the
    relevance gate has already held the pool to it. A cluster left with no members goes."""
    clusters: list[OutcomeCluster] = []
    dropped = 0
    used: set[int] = set()
    for parts in _lines(raw):
        if len(parts) < 2:
            continue
        name = " ".join(parts[0].strip(" \"'").lower().split())
        if not name or len(name.split()) > 6:
            continue
        members: list[Phrase] = []
        for token in re.findall(r"\d+", parts[1]):
            index = int(token)
            if not 1 <= index <= len(pool):
                dropped += 1
                continue
            if index in used:
                continue
            used.add(index)
            members.append(pool[index - 1])
        if members:
            members.sort(key=lambda p: (p.score is None, -(p.score or 0)))
            kept = members[:MAX_PHRASES_PER_CLUSTER]
            unique = bool(distinct) and (on_service(name, list(distinct)) or any(on_service(p.keyword, list(distinct)) for p in kept))
            clusters.append(OutcomeCluster(name, kept, distinct=unique))
        if len(clusters) >= MAX_CLUSTERS:
            break
    # What sets the service apart leads; within each group, the most searched first.
    clusters.sort(key=lambda c: (not c.distinct, -c.combined_volume))
    return clusters, dropped


async def outcome_words(
    services: list[str],
    region: str,
    *,
    landing_url: str = "",
    industry: str = "",
    run_keywords: list[dict] | None = None,
    on_usage: OnUsage | None = None,
) -> BuyerLanguage:
    services = _clean_services(services)
    if not services:
        raise BuyerLanguageError("No service is on record for this session, so there is nothing to search around.")
    country = _country(region)
    if not dataforseo_client.is_configured():
        raise BuyerLanguageError("DataForSEO is not configured (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD).")

    result = BuyerLanguage(mode="outcome", location=country)
    # The service's lexicon: its short names, its topics and where its result happens. The names and
    # topics seed the searches, and every search must hold one of these terms to be judged at all.
    # Only what DataForSEO returns is ever shown; the seeds themselves are not.
    try:
        profile = parse_profile(await _ask(profile_prompt(services, industry), on_usage), services)
    except Exception as exc:  # noqa: BLE001 — the service's own words still anchor the searches
        profile = ServiceProfile()
        result.notes.append(f"The service's topics could not be worked out: {exc}")
    if not profile.anchors:
        profile.anchors = fallback_anchors(services)
    if not profile.seeds:
        profile.seeds = [" ".join(_tokens(s)) for s in services][:MAX_TOPIC_SEEDS]
    phrases, result.sources, notes = await _search_pool(services, landing_url, country, profile.seeds)
    result.notes += notes
    stored = [Phrase(str(r.get("keyword")), r.get("volume"), r.get("difficulty"), r.get("trend")) for r in run_keywords or [] if r.get("keyword")]
    if stored:
        result.sources.insert(0, f"this run's keyword report ({len(stored)})")
    measured = _rank_pool(stored + phrases, country)
    if not measured:
        raise BuyerLanguageError(
            ("DataForSEO returned no searches around these services. " + (result.notes[0] if result.notes else "")).strip()
        )
    # The relevance gate: a search with none of the service's anchors is an outcome of some other
    # service, or of every business, and never reaches the judge.
    pool = [
        p
        for p in measured
        if on_service(p.keyword, profile.anchors, profile.core) and not (set(_tokens(p.keyword)) & OFF_INTENT_WORDS)
    ]
    off = len(measured) - len(pool)
    if off:
        result.notes.append(
            f"{off} search(es) were left out for not being about {services[0]} "
            f"({', '.join((profile.core + profile.facets)[:6] or profile.anchors[:6])}): generic business goals, "
            "other services, jobs or courses."
        )
    if not pool:
        result.notes.append(f"None of the searches found are about {services[0]}'s results. Type the buyer's words from reviews or call notes instead.")
        return result

    try:
        raw = await _ask(outcome_prompt(pool, services, industry, profile), on_usage)
    except Exception as exc:  # noqa: BLE001 — one sentence for the card
        raise BuyerLanguageError(f"The searches could not be sorted into outcomes: {exc}") from exc
    result.clusters, dropped = parse_clusters(raw, pool, services, profile.distinct)
    if dropped:
        result.notes.append(f"{dropped} suggested search(es) were not in DataForSEO's results and were left out.")
    if profile.distinct and not any(c.distinct for c in result.clusters):
        result.notes.append(
            f"No searches were found for what sets {services[0]} apart ({', '.join(profile.distinct[:4])}): "
            "buyers may not search for it yet. Add their words for it below."
        )
    if not result.clusters:
        result.notes.append("None of the searches state an outcome in the buyer's words. Type them from reviews or call notes instead.")
    logger.info("Outcome words for %s: %d clusters from %d searches", services, len(result.clusters), len(pool))
    return result


# --------------------------------------------------------------------------------------
# Words to avoid
# --------------------------------------------------------------------------------------


def avoid_prompt(page_text: str, services: list[str], industry: str, market: str = "") -> str:
    return (
        f"Below is the copy of a business's landing page. Services: {', '.join(services) or 'not stated'}. "
        f"Industry: {industry or 'not stated'}. Market: {market or 'not stated'} (write the plain alternatives "
        "in that market's spelling).\n\n"
        "List words on this page that the business should avoid in its marketing copy:\n"
        f"- JARGON: up to {MAX_JARGON} internal or industry terms a buyer would not use, each with the plain "
        "words a buyer would search instead (2-4 words).\n"
        f"- CLAIM: up to {MAX_CLAIMS} phrases that overpromise or could not be substantiated (guarantees, "
        '"#1", "best", "leading", instant results), each with a one-line reason.\n'
        "Copy each term or phrase exactly as it appears on the page. Leave a group out if the page has none.\n\n"
        "Reply with only the items, one per line, in exactly this form and nothing else:\n"
        "- jargon || <term> || <plain alternative>\n"
        "- claim || <phrase> || <reason>\n\n"
        f"PAGE\n{page_text[:_PAGE_CHARS]}"
    )


def parse_avoid(raw: str, page_text: str) -> tuple[list[AvoidItem], list[AvoidItem], int]:
    """`(jargon, claims, dropped)`. A term is kept only when it is on the page."""
    page = _norm(page_text)
    jargon: list[AvoidItem] = []
    claims: list[AvoidItem] = []
    dropped = 0
    for parts in _lines(raw):
        if len(parts) < 3:
            continue
        kind, term = parts[0].lower(), parts[1].strip(" \"'")
        # The reason is prose and keeps its quotes; only the alternative is a bare phrase.
        extra = parts[2].strip() if kind == "claim" else parts[2].strip(" \"'")
        if not term or _norm(term) not in page:
            dropped += 1
            continue
        if kind == "jargon" and extra and len(jargon) < MAX_JARGON:
            jargon.append(AvoidItem(term, "jargon", alternative=extra.lower()))
        elif kind == "claim" and len(claims) < MAX_CLAIMS:
            claims.append(AvoidItem(term, "claim", reason=extra))
    return jargon, claims, dropped


async def _measure_jargon(items: list[AvoidItem], country: str) -> tuple[list[AvoidItem], str]:
    """Keep jargon buyers search less than its plain alternative. One overview request."""
    if not items:
        return [], ""
    words = list(dict.fromkeys(w for i in items for w in (i.term.lower(), i.alternative)))
    client = dataforseo_client.DataForSEOClient()
    try:
        metrics = await client.overview(words, country)
    except Exception as exc:  # noqa: BLE001 — unmeasured jargon is still offered, with no numbers
        return items, f"Search volumes for the jargon could not be measured: {exc}"
    finally:
        await client.aclose()
    kept: list[AvoidItem] = []
    for item in items:
        term = metrics.get(item.term.lower())
        alt = metrics.get(item.alternative)
        item.term_volume = term.volume if term else None
        item.alternative_volume = alt.volume if alt else None
        # Evidence or nothing: the plain words must be measured, and searched more than the term.
        # Measured live: "crawlability evaluation" vs an alternative with no data either was offered
        # as jargon on no evidence at all.
        if not item.alternative_volume or (item.term_volume or 0) >= item.alternative_volume:
            continue
        term_said = f"{item.term_volume:,}/mo" if item.term_volume else "no measured searches"
        item.reason = f'Buyers search "{item.alternative}" ({item.alternative_volume:,}/mo); this term: {term_said}.'
        kept.append(item)
    return kept, ""


async def words_to_avoid(
    services: list[str],
    region: str,
    *,
    landing_url: str = "",
    industry: str = "",
    on_usage: OnUsage | None = None,
) -> BuyerLanguage:
    result = BuyerLanguage(mode="avoid")

    page_text = ""
    if landing_url:
        try:
            # The copy reader: free first, then Context.dev's rendered read (1 credit) only when the
            # free read is walled off, which SiteGround's captcha does to servers. One page, never a
            # loop. Measured live: the free read alone saw nothing of trafficradius.com.au.
            page = await scraper.scrape_page(landing_url)
            page_text = page.text
            result.sources.append(
                "the landing page's own copy" + (f" (read via {page.source})" if page.source != "direct" else "")
            )
        except Exception as exc:  # noqa: BLE001 — the card still takes typed words
            result.notes.append(f"The landing page could not be read ({exc}), so no jargon or claims were checked.")
    else:
        result.notes.append("No landing page is on record, so no jargon or claims were checked.")

    if page_text.strip():
        services = _clean_services(services)
        try:
            raw = await _ask(avoid_prompt(page_text, services, industry, region), on_usage)
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"The page could not be checked for jargon: {exc}")
            raw = ""
        jargon, claims, dropped = parse_avoid(raw, page_text)
        if dropped:
            result.notes.append(f"{dropped} suggested term(s) were not on the page and were left out.")
        if jargon and dataforseo_client.is_configured():
            try:
                country = _country(region)
                result.location = country
                jargon, note = await _measure_jargon(jargon, country)
                if note:
                    result.notes.append(note)
                result.sources.append("DataForSEO search volumes for each term and its plain alternative")
            except BuyerLanguageError as exc:
                result.notes.append(str(exc))
        result.avoid += jargon + claims

    if not result.avoid:
        result.notes.append("Nothing to suggest. Type any words the client has banned.")
    return result
