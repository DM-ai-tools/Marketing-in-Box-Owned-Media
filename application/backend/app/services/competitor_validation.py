"""Will what the competitors do work for this client? Measured, judged, then ranked.

A competitor listing says who competes and what they offer. It does not say whether the play is
working for them, or whether it would work for this client. Every competitor stage's listing is
scored here, before the operator approves it, on three axes:

- **Proof — is it working for them?** Measured: DataForSEO `ranked_keywords` for the competitor's
  domain (estimated monthly organic visits, keywords in the top 10), ranking page count
  (`relevant_pages`), and whether the artefact page is live (free read). How many competitors run
  the same play is shown once, in the plays table, and the search's own verification is on the
  listing; neither is an evidence row or part of the score (removed at the operator's request).
- **Size — where they sit relative to this client.** Measured: organic traffic vs the client's
  own domain, classified as above / same level / below (2× and 0.5× cutoffs). The ranking
  keywords themselves, with positions, travel with the row.
- **Fit — would it work for this client?** Measured: how many of the keywords that page ranks for
  are about the client's own service and keyword clusters, and whether its price sits in the
  client's band. Judged: one Sonnet call compares each competitor with the ICP, and every reason it
  gives must quote the competitor's page or the ICP (`_verified`), or it is dropped.
- **Feasibility — can the client do it?** Rules from the client's own settings (testimonials,
  claim tier, pricing disclosure), plus what the judge names as a blocker, again quoted.

Each competitor gets a Fit score and a verdict (implement now / test first / differentiate / avoid
/ not a fit), and the plays are grouped across the market. Every number says whether it was
measured, judged or not available; a missing number is None, never 0.

**No tool sees a competitor's conversion rate or lead count.** Traffic, rankings and how common a
play is are proxies, and the report says so. Each "implement now" item carries the KPI and test
that would prove it for this client.

Cost: two DataForSEO requests per competitor domain (at most `MAX_MEASURED`) plus two for the
client's own site (`ranked_keywords` + `relevant_pages`), the free page reads, and one judge call.
Stores nothing; the approved listing carries the rendered ranking into the stage's prompt
(`render_md`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date

from app.services import dataforseo_client, keywords, scraper
from app.services.business_facts import BusinessFacts, amount_of, money_matches
from app.services.claude_client import get_client
from app.services.generation import EFFORT_CAPABLE_MODELS, SONNET, OnUsage
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

#: Competitor pages measured with DataForSEO. The search returns at most 10.
MAX_MEASURED = 10
#: Ranked keywords read per page: enough for overlap and the top-10 count, cheap per request.
_RANKED_LIMIT = 50
_CONCURRENCY = 4
_EXCERPT_CHARS = 1500

JUDGE_MODEL = SONNET
_JUDGE_EFFORT = "low"
_JUDGE_MAX_TOKENS = 6000

VERDICTS = ("implement_now", "test_first", "differentiate", "avoid", "not_a_fit")
VERDICT_LABEL = {
    "implement_now": "Implement now",
    "test_first": "Test first",
    "differentiate": "Differentiate",
    "avoid": "Avoid",
    "not_a_fit": "Not a fit",
}
BUSINESS_LEVELS = ("above", "peer", "below")
BUSINESS_LEVEL_LABEL = {
    "above": "Above this client",
    "peer": "Same level",
    "below": "Below this client",
}
#: Organic-traffic ratio vs the client. 2× is a different league; 0.5× is a smaller player.
_ABOVE_RATIO = 2.0
_BELOW_RATIO = 0.5

# A play that needs proof the client may not show, or claims a tier may forbid.
_NEEDS_TESTIMONIALS = re.compile(r"\b(testimonials?|case stud(?:y|ies)|reviews?|before[- ]and[- ]after|client results|success stories)\b", re.I)
_ABSOLUTE = re.compile(r"\b(guarantee[ds]?|risk[- ]free|no[- ]risk|#1|number one|100% (?:success|results)|proven to)\b", re.I)
_PUBLISHES_PRICE = re.compile(r"\b(pricing|price (?:list|calculator|guide)|cost calculator|\$\s?\d)", re.I)
_DEAD = re.compile(r"\b(404|410|not found|gone)\b", re.I)
_YEAR = re.compile(r"\b(20[12]\d)\b")


@dataclass
class Metric:
    """One number with where it came from. `source` is `measured`, `judged` or `unavailable`."""

    key: str
    label: str
    value: str
    score: int | None
    source: str
    detail: str = ""


@dataclass
class Blocker:
    reason: str
    source: str  # `rule` | `judged`
    quote: str = ""


@dataclass
class CompetitorScore:
    rank: int
    name: str
    domain: str
    page_url: str | None
    play: str
    proof: int | None
    fit: int | None
    feasibility: int | None
    total: int | None
    verdict: str
    verdict_reason: str
    metrics: list[Metric] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    what_to_take: str = ""
    how_to_prove: str = ""
    #: The concrete steps to adapt `what_to_take` for this client's own asset, in the stage's terms
    #: (a lead magnet, an offer, a funnel step).
    how_to_apply: str = ""
    top_keywords: list[dict[str, object]] = field(default_factory=list)
    business_level: str | None = None  # above | peer | below
    page_count: int | None = None
    keyword_count: int | None = None
    #: Every keyword scanned for this domain (the top `_RANKED_LIMIT` by estimated traffic, out of
    #: `keyword_count`), shown on hover over "ranking keywords".
    scanned_keywords: list[dict[str, object]] = field(default_factory=list)


def _keyword_rows(ranked: list[dataforseo_client.RankedKeyword]) -> list[dict[str, object]]:
    return [
        {"keyword": k.keyword, "position": k.position, "volume": k.volume, "url": k.url}
        for k in ranked
    ]


@dataclass
class Play:
    play: str
    count: int
    share: float
    combined_traffic: float | None
    average_fit: int | None
    verdict: str
    competitors: list[str]


@dataclass
class ValidationReport:
    asset_id: str
    location: str
    client_traffic: float | None
    client_top10: int | None
    client_page_count: int | None
    client_keyword_count: int | None
    competitors: list[CompetitorScore]
    plays: list[Play]
    market_gaps: list[str]
    sources: list[str]
    notes: list[str]
    judge: str  # `ok` | `unavailable`
    dropped_quotes: int = 0
    markdown: str = ""
    #: The client's own scanned keywords, for the baseline line's hover.
    client_keywords: list[dict[str, object]] = field(default_factory=list)
    #: How many keywords are scanned per domain, so the hover can say "50 of 319".
    scanned_limit: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------------------


@dataclass
class _Measured:
    traffic: float | None = None
    top10: int | None = None
    ranked: list[dataforseo_client.RankedKeyword] = field(default_factory=list)
    ranked_error: str = ""
    keyword_count: int | None = None
    page_count: int | None = None
    page_count_sample: bool = False  # True when unique URLs in the keyword sample, not relevant_pages
    live: str = "unknown"  # live | thin | dead | unknown
    live_detail: str = ""
    latest_year: int | None = None
    excerpt: str = ""


async def _measure_ranked(client: dataforseo_client.DataForSEOClient | None, target: str, country: str, out: _Measured) -> None:
    """Site-wide rankings for `target` (a domain). Page count is a second Labs call; a failure
    there falls back to unique URLs in the keyword sample rather than leaving the row blank."""
    if client is None or not target:
        return
    try:
        overview = await client.ranked_overview(target, country, limit=_RANKED_LIMIT)
    except Exception as exc:  # noqa: BLE001 — one domain failing costs that domain alone
        out.ranked_error = str(exc) or exc.__class__.__name__
        return
    out.ranked = list(overview.keywords)
    out.traffic = overview.traffic
    out.keyword_count = overview.keyword_count
    out.top10 = sum(1 for k in overview.keywords if k.position is not None and k.position <= 10)
    try:
        out.page_count = await client.ranking_page_count(target, country)
    except Exception:  # noqa: BLE001
        urls = {k.url for k in overview.keywords if k.url}
        out.page_count = len(urls) or None
        out.page_count_sample = out.page_count is not None


async def _measure_page(url: str | None, out: _Measured) -> None:
    if not url:
        out.live_detail = "no page URL in the listing"
        return
    try:
        page = await scraper.read_direct(url)
    except scraper.ScrapeError as exc:
        message = str(exc)
        out.live = "dead" if _DEAD.search(message) else "unknown"
        out.live_detail = message
        return
    except Exception as exc:  # noqa: BLE001
        out.live_detail = str(exc) or exc.__class__.__name__
        return
    out.live = "thin" if page.low_content else "live"
    out.live_detail = page.title or ""
    years = [int(y) for y in _YEAR.findall(page.text)]
    current = date.today().year
    years = [y for y in years if y <= current]
    out.latest_year = max(years) if years else None
    out.excerpt = page.text[:_EXCERPT_CHARS]


def _client_terms(facts: BusinessFacts, service: str) -> tuple[set[str], set[str]]:
    """The client's keyword set (exact keys) and its service's content words."""
    exact = {keywords.dedupe_key(keywords.normalize(str(k.get("keyword") or ""))) for k in facts.keywords}
    exact.discard("")
    words = {keywords.singular(t) for t in keywords.tokens(keywords.normalize(service or facts.service))}
    return exact, words


def _on_topic(keyword: str, exact: set[str], words: set[str]) -> bool:
    norm = keywords.normalize(keyword)
    if keywords.dedupe_key(norm) in exact:
        return True
    if not words:
        return False
    need = min(2, len(words))
    return len({keywords.singular(t) for t in keywords.tokens(norm)} & words) >= need


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------


def _traffic_score(traffic: float | None) -> int | None:
    """Log scale: 10 visits/mo ≈ 25, 100 ≈ 50, 1,000 ≈ 75, 10,000+ = 100."""
    if traffic is None:
        return None
    return max(0, min(100, round(25 * math.log10(1 + traffic))))


def _weighted(parts: list[tuple[int | None, float]]) -> int | None:
    present = [(s, w) for s, w in parts if s is not None]
    if not present:
        return None
    total = sum(w for _, w in present)
    return round(sum(s * w for s, w in present) / total)


def play_of(category: str | None) -> str:
    text = re.sub(r"\s+", " ", (category or "").strip())
    return text[:1].upper() + text[1:] if text else "Unclassified"


def business_level(theirs: float | None, ours: float | None) -> tuple[str | None, str]:
    """`above` / `peer` / `below` from organic-traffic ratio. Missing either side is unavailable,
    never guessed — a 0 would claim a real measurement of zero visits."""
    if theirs is None or ours is None:
        return None, ""
    if ours <= 0:
        return ("above" if theirs > 0 else "peer"), "the client has no measured organic traffic"
    ratio = theirs / ours
    if ratio >= _ABOVE_RATIO:
        return "above", f"{ratio:.1f}× the client's organic visits"
    if ratio <= _BELOW_RATIO:
        return "below", f"{ratio:.1f}× the client's organic visits"
    return "peer", f"{ratio:.1f}× the client's organic visits"


def _price_band(price: str | None, facts: BusinessFacts) -> tuple[int | None, str]:
    competitor = [amount_of(m) for m in money_matches(price or "")]
    client = sorted(a for a in facts.allowed_amounts if a > 0)
    if not competitor or not client:
        return None, ""
    theirs = competitor[0]
    nearest = min(client, key=lambda a: abs(math.log(max(a, 1)) - math.log(max(theirs, 1))))
    ratio = theirs / nearest if nearest else 0
    if 0.5 <= ratio <= 2:
        return 100, f"within the client's band (nearest client price ${nearest:,.0f})"
    return 40, f"{ratio:.1f}× the nearest client price (${nearest:,.0f})"


def _rule_blockers(text: str, facts: BusinessFacts) -> list[Blocker]:
    out: list[Blocker] = []
    if facts.testimonials in {"NO", "UNSURE"} and (m := _NEEDS_TESTIMONIALS.search(text)):
        out.append(Blocker(f"Relies on {m.group(0).lower()}; the client's settings say testimonials are {facts.testimonials}.", "rule", m.group(0)))
    tier = facts.claim_tier
    if tier is not None and tier >= 2 and (m := _ABSOLUTE.search(text)):
        out.append(Blocker(f'Uses an absolute claim ("{m.group(0)}") the client\'s claim tier {tier} does not allow.', "rule", m.group(0)))
    if facts.prices_forbidden and (m := _PUBLISHES_PRICE.search(text)):
        out.append(Blocker(f"Publishes pricing; the client's pricing disclosure mode is {facts.pricing_mode}.", "rule", m.group(0)))
    return out


def _verdict(proof: int | None, fit: int | None, feasibility: int | None, share: float, ruled_out: bool = False) -> tuple[str, str]:
    """`ruled_out`: one of the client's own settings forbids the play. That is decisive, whatever
    the play's proof: a testimonial-led offer is no use to a client who may not show testimonials."""
    if ruled_out:
        return "avoid", "The client's own settings rule it out (see blockers)."
    if feasibility is not None and feasibility < 50:
        return "avoid", "The client cannot run it as it stands (see blockers)."
    p, f = proof or 0, fit or 0
    if fit is not None and f >= 60 and proof is not None and p >= 60:
        return "implement_now", "Proven for them and close to this client's service and buyer."
    if fit is not None and f >= 60:
        return "test_first", "Close to this client, but the proof that it works is thin or unmeasured."
    if share >= 0.5 and f >= 40:
        return "differentiate", "Most of the market runs this play; match it and do it better."
    if fit is None:
        return "test_first", "Fit could not be measured or judged; check it before building."
    return "not_a_fit", "Little overlap with this client's service, keywords or buyer."


# --------------------------------------------------------------------------------------
# The judge
# --------------------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def _verified(quote: str, corpus: str) -> bool:
    q = _norm(quote).strip(" .…\"'")
    return len(q) >= 4 and q in corpus


def _judge_prompt(asset_id: str, facts: BusinessFacts, rows: list[tuple[object, _Measured]], service: str) -> str:
    lines = [
        "You are validating a competitor listing for a marketing stage before the operator relies on it.",
        f"Stage: {asset_id}. The client's service: {service or facts.service or '(not on record)'}.",
        "",
        "THE CLIENT",
        facts.summary(),
        "",
        "ICP (excerpt):",
        (facts.icp or "(no ICP on record)")[:3000],
        "",
        "COMPETITORS (what the search found, what was measured, and an excerpt of their page)",
    ]
    for c, m in rows:
        lines += [
            f"- rank {c.rank}: {c.name} ({c.domain}) — play: {play_of(c.category)}",
            f"  summary: {c.offering_summary or '(none)'}",
            f"  measured: organic visits/mo {m.traffic if m.traffic is not None else 'not measured'}, "
            f"top-10 keywords {m.top10 if m.top10 is not None else 'not measured'}, "
            f"ranking pages {m.page_count if m.page_count is not None else 'not measured'}, page {m.live}",
            f"  page excerpt: {m.excerpt[:800] or '(not read)'}",
        ]
    lines += [
        "",
        "For EACH competitor, judge only what the measurements cannot: does this play suit THIS client's",
        "buyer (awareness level, buyer type, the pains the ICP names), what specifically is worth taking,",
        "and what would stop this client doing it. Every reason must quote, word for word, either the",
        "competitor's summary/page excerpt or the ICP. A reason you cannot quote, leave out.",
        "Never estimate traffic, conversion or leads — those are measured elsewhere or unknown.",
        "",
        "Return ONLY JSON:",
        '{"competitors": [{"rank": 1, "buyer_fit": 0-10, "buyer_fit_reason": "one sentence",',
        '  "buyer_fit_quote": "exact words from the ICP or the competitor text",',
        '  "blockers": [{"reason": "one sentence", "quote": "exact words"}],',
        '  "what_to_take": "the lead magnet, offer or funnel entry that brings this competitor leads, and why it works, specific", ',
        '  "how_to_apply": "2-3 concrete steps to adapt it for this client\'s own asset, using the client\'s service, ICP and settings — adapt, never copy", ',
        '  "how_to_prove": "the KPI and test that would prove it for this client, with a threshold and timeframe"}],',
        ' "market_gaps": ["a play or angle no competitor here runs that this client\'s ICP needs — one sentence each"]}',
    ]
    return "\n".join(lines)


async def _call_judge(prompt: str, on_usage: OnUsage | None) -> str:
    kwargs: dict[str, object] = {
        "model": JUDGE_MODEL,
        "max_tokens": _JUDGE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if JUDGE_MODEL in EFFORT_CAPABLE_MODELS:
        kwargs["output_config"] = {"effort": _JUDGE_EFFORT}
    started = time.monotonic()
    response = await get_client().messages.create(**kwargs)
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=JUDGE_MODEL, duration_ms=int((time.monotonic() - started) * 1000))
        )
    return next((b.text for b in response.content if b.type == "text"), "")


def _parse_judge(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("competitors"), list) else None


# --------------------------------------------------------------------------------------
# The whole validation
# --------------------------------------------------------------------------------------


async def validate(
    asset_id: str,
    competitors: list,
    facts: BusinessFacts,
    *,
    client_url: str,
    region: str,
    service: str = "",
    on_usage: OnUsage | None = None,
) -> ValidationReport:
    """`competitors` are `ParsedCompetitor`-shaped rows (rank, name, domain, page_url, category,
    offering_summary, starting_price, verification_confidence). Never raises: a provider that fails
    leaves its numbers unavailable and says why in `notes`."""
    notes: list[str] = []
    sources: list[str] = []
    country = keywords.match_location(region, dataforseo_client.COUNTRIES) or ""
    rows = list(competitors)[:MAX_MEASURED]

    client: dataforseo_client.DataForSEOClient | None = None
    if not country:
        notes.append(f"Region {region!r} names no country, so traffic and keyword overlap were not measured.")
    elif not dataforseo_client.is_configured():
        notes.append("DataForSEO is not configured (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD); traffic and keyword overlap were not measured.")
    else:
        client = dataforseo_client.DataForSEOClient()
        sources.append(f"DataForSEO ranked keywords and ranking pages (Google, {country}), measured {date.today().isoformat()}")
    sources.append("Each competitor page read directly (free) for liveness and freshness")

    measured = [_Measured() for _ in rows]
    mine = _Measured()
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def bounded(coro):
        async with gate:
            await coro

    # The client's whole site, as the baseline a competitor page's traffic is read against.
    client_site = re.sub(r"^https?://(?:www\.)?", "", client_url.strip(), flags=re.I).split("/")[0]
    try:
        await asyncio.gather(
            bounded(_measure_ranked(client, client_site, country, mine)),
            *(bounded(_measure_ranked(client, c.domain, country, m)) for c, m in zip(rows, measured)),
            *(bounded(_measure_page(c.page_url, m)) for c, m in zip(rows, measured)),
        )
    finally:
        if client is not None:
            await client.aclose()

    failed = [c.name for c, m in zip(rows, measured) if m.ranked_error]
    if failed and client is not None:
        first = next(m.ranked_error for m in measured if m.ranked_error)
        notes.append(f"DataForSEO could not measure {', '.join(failed)}: {first}")

    # The judge, once for the listing.
    judged: dict[int, dict] = {}
    gaps: list[str] = []
    dropped = 0
    judge_state = "unavailable"
    corpus = _norm(" ".join([facts.icp, facts.summary()] + [f"{c.offering_summary or ''} {m.excerpt}" for c, m in zip(rows, measured)]))
    if rows:
        try:
            data = _parse_judge(await _call_judge(_judge_prompt(asset_id, facts, list(zip(rows, measured)), service), on_usage))
        except Exception as exc:  # noqa: BLE001 — scores still stand on what was measured
            logger.warning("Competitor validation judge failed for %s: %s", asset_id, exc)
            data = None
        if data is None:
            notes.append("The fit reviewer was unavailable; buyer fit is not scored and only rule blockers are shown.")
        else:
            judge_state = "ok"
            sources.append("Buyer fit judged by Claude Sonnet against the ICP; every reason quotes its source")
            for item in data["competitors"]:
                if isinstance(item, dict) and isinstance(item.get("rank"), int):
                    judged[item["rank"]] = item
            gaps = [str(g).strip() for g in data.get("market_gaps") or [] if str(g).strip()][:5]

    exact, words = _client_terms(facts, service)
    if not facts.keywords:
        notes.append("No keyword clusters on record; overlap is read against the service name only.")
    if not facts.icp:
        notes.append("No ICP on record; buyer fit is judged against the service alone.")

    plays_count: dict[str, int] = {}
    for c in rows:
        plays_count[play_of(c.category)] = plays_count.get(play_of(c.category), 0) + 1

    scored: list[CompetitorScore] = []
    for c, m in zip(rows, measured):
        play = play_of(c.category)
        share = plays_count[play] / len(rows) if rows else 0.0
        metrics: list[Metric] = []

        traffic_s = _traffic_score(m.traffic)
        metrics.append(Metric(
            "traffic", "Organic visits / month", "—" if m.traffic is None else f"{m.traffic:,.0f}", traffic_s,
            "measured" if m.traffic is not None else "unavailable",
            (f"vs the client's {mine.traffic:,.0f}" if m.traffic is not None and mine.traffic is not None else m.ranked_error),
        ))
        level, level_detail = business_level(m.traffic, mine.traffic)
        metrics.append(Metric(
            "level", "Business level", BUSINESS_LEVEL_LABEL[level] if level else "—",
            {"above": 70, "peer": 100, "below": 55}.get(level) if level else None,
            "measured" if level else "unavailable",
            level_detail,
        ))
        pages_s = None if m.page_count is None else min(100, round(25 * math.log10(1 + m.page_count)))
        pages_detail = []
        if m.page_count_sample:
            pages_detail.append("unique ranking URLs among the sampled keywords")
        if mine.page_count is not None:
            pages_detail.append(f"client has {mine.page_count:,}")
        metrics.append(Metric(
            "pages", "Number of pages", "—" if m.page_count is None else f"{m.page_count:,}", pages_s,
            "measured" if m.page_count is not None else "unavailable",
            "; ".join(pages_detail),
        ))
        kw_s = None if m.keyword_count is None else min(100, round(25 * math.log10(1 + m.keyword_count)))
        metrics.append(Metric(
            "keywords", "Ranking keywords", "—" if m.keyword_count is None else f"{m.keyword_count:,}", kw_s,
            "measured" if m.keyword_count is not None else "unavailable",
            (f"vs the client's {mine.keyword_count:,}" if mine.keyword_count is not None else ""),
        ))
        top10_s = None if m.top10 is None else min(100, m.top10 * 10)
        metrics.append(Metric("top10", "Keywords in Google's top 10", "—" if m.top10 is None else str(m.top10), top10_s,
                              "measured" if m.top10 is not None else "unavailable"))
        live_s = {"live": 100, "thin": 40, "dead": 0}.get(m.live)
        fresh = f"; latest year on page {m.latest_year}" if m.latest_year else ""
        metrics.append(Metric("live", "Page live", {"live": "Live", "thin": "Almost empty", "dead": "Dead", "unknown": "Couldn't check"}[m.live],
                              live_s, "measured" if live_s is not None else "unavailable", (m.live_detail + fresh).strip("; ")))

        overlap_s: int | None = None
        on_topic: list[dataforseo_client.RankedKeyword] = []
        if m.ranked:
            on_topic = [k for k in m.ranked if _on_topic(k.keyword, exact, words)]
            pct = len(on_topic) / len(m.ranked)
            overlap_s = min(100, round(pct * 200))
            metrics.append(Metric("overlap", "Ranks for the client's keywords", f"{len(on_topic)} of {len(m.ranked)}", overlap_s, "measured",
                                  ", ".join(k.keyword for k in on_topic[:4])))
        else:
            metrics.append(Metric("overlap", "Ranks for the client's keywords", "—", None, "unavailable"))

        price_s, price_detail = _price_band(c.starting_price, facts)
        if price_s is not None:
            metrics.append(Metric("price", "Price band", c.starting_price or "", price_s, "measured", price_detail))

        item = judged.get(c.rank, {})
        buyer_s: int | None = None
        reason = str(item.get("buyer_fit_reason") or "").strip()
        quote = str(item.get("buyer_fit_quote") or "").strip()
        if item and isinstance(item.get("buyer_fit"), (int, float)) and not isinstance(item.get("buyer_fit"), bool):
            if _verified(quote, corpus):
                buyer_s = max(0, min(100, round(float(item["buyer_fit"]) * 10)))
                metrics.append(Metric("buyer", "Fits the client's buyer", f"{buyer_s // 10}/10", buyer_s, "judged", f'{reason} — "{quote}"'))
            else:
                dropped += 1
        if buyer_s is None:
            metrics.append(Metric("buyer", "Fits the client's buyer", "—", None, "unavailable"))

        text = " ".join(filter(None, [c.offering_summary, c.category, m.excerpt]))
        blockers = _rule_blockers(text, facts)
        for b in item.get("blockers") or []:
            if isinstance(b, dict) and str(b.get("reason") or "").strip():
                if _verified(str(b.get("quote") or ""), corpus):
                    blockers.append(Blocker(str(b["reason"]).strip(), "judged", str(b.get("quote")).strip()))
                else:
                    dropped += 1

        proof = _weighted([(traffic_s, 0.35), (top10_s, 0.15), (live_s, 0.15)])
        fit = _weighted([(overlap_s, 0.4), (buyer_s, 0.45), (price_s, 0.15)])
        feasibility = max(0, 100 - 40 * sum(1 for b in blockers if b.source == "rule") - 30 * sum(1 for b in blockers if b.source == "judged"))
        total = _weighted([(proof, 0.4), (fit, 0.4), (feasibility, 0.2)])
        verdict, why = _verdict(proof, fit, feasibility, share, any(b.source == "rule" for b in blockers))

        scored.append(CompetitorScore(
            rank=c.rank, name=c.name, domain=c.domain, page_url=c.page_url, play=play,
            proof=proof, fit=fit, feasibility=feasibility, total=total, verdict=verdict, verdict_reason=why,
            metrics=metrics, blockers=blockers,
            what_to_take=str(item.get("what_to_take") or "").strip(),
            how_to_prove=str(item.get("how_to_prove") or "").strip(),
            how_to_apply=str(item.get("how_to_apply") or "").strip(),
            top_keywords=[{"keyword": k.keyword, "position": k.position, "volume": k.volume} for k in (on_topic or m.ranked)[:8]],
            business_level=level,
            page_count=m.page_count,
            keyword_count=m.keyword_count,
            scanned_keywords=_keyword_rows(m.ranked),
        ))

    if dropped:
        notes.append(f"{dropped} reviewer reason(s) were discarded because their quote was not in the competitor's page or the ICP.")

    order = {v: i for i, v in enumerate(VERDICTS)}
    scored.sort(key=lambda s: (order[s.verdict], -(s.total or 0), s.rank))

    plays: list[Play] = []
    for name, count in sorted(plays_count.items(), key=lambda kv: -kv[1]):
        members = [s for s in scored if s.play == name]
        traffic = [m.traffic for c, m in zip(rows, measured) if play_of(c.category) == name and m.traffic is not None]
        fits = [s.fit for s in members if s.fit is not None]
        best = min((s.verdict for s in members), key=lambda v: order[v])
        plays.append(Play(name, count, round(count / len(rows), 2), round(sum(traffic), 1) if traffic else None,
                          round(sum(fits) / len(fits)) if fits else None, best, [s.name for s in members]))

    report = ValidationReport(
        asset_id=asset_id, location=country, client_traffic=mine.traffic, client_top10=mine.top10,
        client_page_count=mine.page_count, client_keyword_count=mine.keyword_count,
        competitors=scored, plays=plays, market_gaps=gaps, sources=sources, notes=notes,
        judge=judge_state, dropped_quotes=dropped,
        client_keywords=_keyword_rows(mine.ranked), scanned_limit=_RANKED_LIMIT,
    )
    report.markdown = render_md(report)
    return report


def render_md(report: ValidationReport) -> str:
    """The ranking as the stage's prompt reads it, appended to the approved listing."""
    lines = [
        "## Competitor validation — ranked for this client",
        "Proof = is it working for them (measured traffic, rankings, live page, how common). "
        "Fit = would it work for this client (keyword overlap, buyer fit, price band). "
        "Feasibility = can the client run it (their testimonial, claim and pricing settings). "
        "Conversion rates and leads are not public; build on 'Implement now', treat 'Test first' as "
        "experiments, and never copy an 'Avoid' play.",
        "",
        "| # | Competitor | Play | Level | Pages | Keywords | Verdict | Fit score | Proof | Fit | Feasibility |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    n = lambda v: "—" if v is None else str(v)  # noqa: E731
    for s in report.competitors:
        level = BUSINESS_LEVEL_LABEL.get(s.business_level or "", "—")
        lines.append(
            f"| {s.rank} | {s.name} | {s.play} | {level} | {n(s.page_count)} | {n(s.keyword_count)} | "
            f"{VERDICT_LABEL[s.verdict]} | {n(s.total)} | {n(s.proof)} | {n(s.fit)} | {n(s.feasibility)} |"
        )
    lines.append("")
    for s in report.competitors:
        if s.verdict in {"implement_now", "test_first", "differentiate"} and (s.what_to_take or s.how_to_prove):
            lines.append(f"- **{s.name} — {VERDICT_LABEL[s.verdict]}.** Take: {s.what_to_take or '—'} Apply: {s.how_to_apply or '—'} Prove it: {s.how_to_prove or '—'}")
        for b in s.blockers:
            lines.append(f"- **{s.name} — blocker:** {b.reason}")
        if s.top_keywords:
            ranked = ", ".join(
                f"{k['keyword']} (#{k['position']})" if k.get("position") else str(k["keyword"])
                for k in s.top_keywords
            )
            lines.append(f"- **{s.name} — ranks for:** {ranked}")
    if report.plays:
        lines += ["", "Plays across the market:"]
        for p in report.plays:
            lines.append(f"- {p.play}: {p.count} competitor(s), verdict {VERDICT_LABEL[p.verdict]}")
    if report.market_gaps:
        lines += ["", "Gaps no competitor fills:"] + [f"- {g}" for g in report.market_gaps]
    return "\n".join(lines).strip()
