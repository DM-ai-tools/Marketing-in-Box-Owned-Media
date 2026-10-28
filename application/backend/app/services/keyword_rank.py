"""One ranking for every keyword this pipeline fetches, and the block that hands the top of it to a stage.

Three signals, all measured by DataForSEO, never invented:

- **Search demand.** Monthly searches (`volume`).
- **Traffic.** `est_traffic`: the monthly visits a page would get at the position its difficulty
  usually allows. That is `volume × CTR(expected position)`. The position comes from keyword
  difficulty (`expected_position`), and the CTRs are an organic click-through curve
  (`_CTR_BY_POSITION`). A high-volume keyword nobody can rank for therefore scores lower than its
  volume alone would.
- **Virality.** Search momentum: DataForSEO's own `search_volume_trend` (the % change against the
  previous quarter, else the previous year), or its monthly history (`dataforseo_client._trend`).
  It is rising interest in the search, not shares of a post. The prompt block names it "momentum"
  so nothing downstream reads it as social reach.

`score` (0-100) weights them 40 / 40 / 20, over whichever are present, the same "weigh what was
measured" rule `competitor_validation._weighted` uses. A keyword with no trend is ranked on the
other two; it is not marked down as a fall. A keyword with no volume has no score and ranks last.

`targets_block` is the `KEYWORD_TARGETS` block every stage's prompt tail carries once the run has a
keyword report. It asks for the top-ranked terms to be used where they fit and forbids quoting the
numbers to customers. `lib/promptEcho.ts` strips an echo of it.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

#: Organic click-through rate by position (positions 1-10, then a page-two floor). A conservative
#: curve: it is used for ranking keywords against each other, so its shape matters, not its level.
_CTR_BY_POSITION = {1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.07, 6: 0.05, 7: 0.04, 8: 0.03, 9: 0.025, 10: 0.02}
_PAGE_TWO_CTR = 0.01
#: The position a new, well-made page can usually reach, by keyword difficulty band.
_POSITION_BY_KD = ((15, 2), (30, 3), (50, 5), (70, 8))
_UNKNOWN_KD_POSITION = 5

#: Where each signal saturates on its 0-100 scale.
_VOLUME_CEILING = 100_000
_TRAFFIC_CEILING = 20_000
_WEIGHTS = {"demand": 0.4, "traffic": 0.4, "virality": 0.2}

TARGETS_BLOCK = "KEYWORD_TARGETS"
#: Stages that never get the block. The ICP is built before any keyword report exists.
NO_TARGETS: frozenset[str] = frozenset({"icp"})


def expected_position(difficulty: int | None) -> int:
    if difficulty is None:
        return _UNKNOWN_KD_POSITION
    for ceiling, position in _POSITION_BY_KD:
        if difficulty <= ceiling:
            return position
    return 12


def est_traffic(volume: int | None, difficulty: int | None) -> int | None:
    if volume is None:
        return None
    ctr = _CTR_BY_POSITION.get(expected_position(difficulty), _PAGE_TWO_CTR)
    return round(volume * ctr)


def virality_score(trend: int | None) -> int | None:
    """0-100, 50 being flat: +100% or more is 100, -100% is 0."""
    if trend is None:
        return None
    return max(0, min(100, round(50 + trend / 2)))


def _log_scale(value: int | None, ceiling: int) -> int | None:
    if value is None:
        return None
    return min(100, round(100 * math.log10(1 + max(0, value)) / math.log10(1 + ceiling)))


@dataclass(frozen=True)
class RankedKeyword:
    keyword: str
    volume: int | None
    difficulty: int | None
    trend: int | None
    est_traffic: int | None
    virality: int | None
    score: int | None
    rank: int = 0
    intent: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_of(volume: int | None, difficulty: int | None, trend: int | None) -> int | None:
    if volume is None:
        return None
    parts = [
        (_log_scale(volume, _VOLUME_CEILING), _WEIGHTS["demand"]),
        (_log_scale(est_traffic(volume, difficulty), _TRAFFIC_CEILING), _WEIGHTS["traffic"]),
        (virality_score(trend), _WEIGHTS["virality"]),
    ]
    present = [(s, w) for s, w in parts if s is not None]
    total = sum(w for _, w in present)
    return round(sum(s * w for s, w in present) / total) if total else None


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def rank_keywords(rows: Iterable[Any], limit: int | None = None) -> list[RankedKeyword]:
    """`rows` (objects or dicts with keyword / volume / difficulty / trend / intent), ranked best first.

    Ties break on volume, then alphabetically, so the order is stable from run to run. The same
    keyword twice (case-insensitive) keeps its first row."""
    seen: set[str] = set()
    out: list[RankedKeyword] = []
    for row in rows:
        keyword = str(_field(row, "keyword") or "").strip()
        if not keyword or keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        volume, difficulty, trend = _field(row, "volume"), _field(row, "difficulty"), _field(row, "trend")
        out.append(
            RankedKeyword(
                keyword=keyword,
                volume=volume,
                difficulty=difficulty,
                trend=trend,
                est_traffic=est_traffic(volume, difficulty),
                virality=virality_score(trend),
                score=score_of(volume, difficulty, trend),
                intent=_field(row, "intent"),
            )
        )
    out.sort(key=lambda k: (k.score is None, -(k.score or 0), -(k.volume or 0), k.keyword))
    ranked = [RankedKeyword(**{**k.as_dict(), "rank": i}) for i, k in enumerate(out, start=1)]
    return ranked[:limit] if limit is not None else ranked


def _num(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def momentum(trend: int | None) -> str:
    return "—" if trend is None else f"{trend:+d}%"


# --------------------------------------------------------------------------------------
# Primary and secondary keywords
#
# A primary keyword is a core term ("bedroom accessories"). Its secondary keywords are the searches
# that contain every word of it plus a modifier: a place, "buy", "best", "online"
# ("bedroom accessories brisbane", "buy bedroom accessories"). Measured by words, not judged: a
# keyword is a secondary of a primary exactly when it holds all of the primary's words.
# --------------------------------------------------------------------------------------

#: Primary keywords kept, and secondaries listed under each.
MAX_PRIMARIES = 8
MAX_SECONDARIES = 8
_STOP = frozenset({"a", "an", "and", "the", "for", "of", "in", "to", "with", "on", "&"})


def singular(word: str) -> str:
    """"accessories" -> "accessory", "searches" -> "search", "ads" -> "ad". Crude on purpose: it
    only has to make a plural and its singular compare equal."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _key_words(keyword: str) -> frozenset[str]:
    return frozenset(singular(w) for w in re.findall(r"[a-z0-9]+", keyword.lower()) if w not in _STOP)


@dataclass(frozen=True)
class KeywordGroup:
    primary: RankedKeyword
    secondary: tuple[RankedKeyword, ...]

    @property
    def combined_volume(self) -> int:
        return sum(k.volume or 0 for k in (self.primary, *self.secondary))

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.as_dict(),
            "secondary": [k.as_dict() for k in self.secondary],
            "combined_volume": self.combined_volume,
        }


def group_keywords(
    ranked: list[RankedKeyword],
    seeds: Iterable[str] = (),
    *,
    max_primaries: int = MAX_PRIMARIES,
    max_secondaries: int = MAX_SECONDARIES,
) -> list[KeywordGroup]:
    """`ranked` as primary keywords, each with its secondaries, best primary first.

    Shorter keywords are considered for primary first, so "bedroom accessories" becomes the primary
    and "bedroom accessories brisbane" its secondary, whichever ranks higher. A keyword holding every
    word of an existing primary is that primary's secondary (the most specific primary, when several
    match). Anything else becomes a primary of its own.

    Seeds (the service and its alternate names) are always primaries, never another keyword's
    secondary: "social media marketing" is a service, not a modified "social media". One word alone
    ("bedroom") is a primary only when it is a seed, so a bare word cannot swallow every keyword that
    mentions it."""
    seed_words = {_key_words(s) for s in seeds}
    primaries: list[tuple[frozenset[str], RankedKeyword]] = []
    secondaries: dict[str, list[RankedKeyword]] = {}

    ordered = sorted(ranked, key=lambda k: (len(_key_words(k.keyword)), k.rank or 10**6))
    for kw in ordered:
        words = _key_words(kw.keyword)
        if words in seed_words and not any(w == words for w, _ in primaries):
            primaries.append((words, kw))
            secondaries.setdefault(kw.keyword, [])

    for kw in ordered:
        words = _key_words(kw.keyword)
        if not words or kw.keyword in secondaries:
            continue
        # `<=`: a plural or reordered variant of a primary has the same words, and belongs under it.
        holders = [(w, p) for w, p in primaries if w <= words]
        if holders:
            _, parent = max(holders, key=lambda h: (len(h[0]), -(h[1].rank or 0)))
            secondaries.setdefault(parent.keyword, []).append(kw)
        elif len(words) > 1 or words in seed_words:
            primaries.append((words, kw))
            secondaries.setdefault(kw.keyword, [])

    groups = [
        KeywordGroup(p, tuple(sorted(secondaries[p.keyword], key=lambda k: k.rank or 10**6)[:max_secondaries]))
        for _, p in primaries
    ]
    groups.sort(key=lambda g: (g.primary.score is None, -(g.primary.score or 0), -g.combined_volume, g.primary.keyword))
    return groups[:max_primaries]


@dataclass
class KeywordTargets:
    """What a stage is told to target, read from the run's stored keyword report."""

    groups: list[KeywordGroup]
    service: str = ""
    country: str = ""
    synonyms: dict[str, list[str]] | None = None

    def as_event(self) -> dict[str, Any]:
        """The `keyword_targets` SSE event: the deliverable's "Keyword data" section lists these and
        checks which of them the asset actually uses."""
        return {
            "type": "keyword_targets",
            "service": self.service,
            "location": self.country,
            "groups": [g.as_dict() for g in self.groups],
            "synonyms": self.synonyms or {},
        }


def targets_from_entry(value: Any) -> KeywordTargets:
    """The run's primary and secondary keywords from a stored `keyword_clusters` entry. A report
    stored before ranking existed is ranked here from its keyword rows (no trend on those, so it ranks
    on demand and traffic). The stub provider's placeholders never count: no groups."""
    if not isinstance(value, dict) or value.get("provider") != "dataforseo":
        return KeywordTargets([])
    rows = value.get("ranked_keywords") or value.get("clean_keywords") or []
    ranked = rank_keywords([r for r in rows if isinstance(r, dict) and r.get("volume") is not None])
    report = value.get("keyword_report") if isinstance(value.get("keyword_report"), dict) else {}
    seeds = [
        str(b.get("seed"))
        for s in report.get("service_hierarchy") or []
        for b in s.get("seeds") or []
        if isinstance(b, dict) and b.get("seed")
    ]
    synonyms = value.get("service_synonyms") if isinstance(value.get("service_synonyms"), dict) else {}
    groups = group_keywords(ranked, seeds, max_primaries=TARGET_PRIMARIES, max_secondaries=TARGET_SECONDARIES)
    return KeywordTargets(groups, str(value.get("service") or ""), str(value.get("location") or ""), synonyms)


#: Groups handed to a stage, and secondaries under each.
TARGET_PRIMARIES = 5
TARGET_SECONDARIES = 5


def _kw_line(k: RankedKeyword) -> str:
    return f"{k.keyword} ({_num(k.volume)}/mo, est. traffic {_num(k.est_traffic)}, momentum {momentum(k.trend)})"


#: Where each content asset puts its secondary keywords. These stages write the pages and sequences a
#: buyer finds by searching, so for them "where they fit" is not enough: each secondary keyword has
#: a named home, and the deliverable's "Keyword data" section counts which ones landed.
SECONDARY_PLACEMENT: dict[str, str] = {
    "funnel": "each funnel step's headline, subhead and body copy, and the email subject lines and bodies",
    "funnel_hub_media": "hub and spoke topic titles, the page and media briefs, and the commitment ladder's step names",
    "pillar_page": "H2/H3 headings, the opening paragraph of each section, the FAQ questions and answers, and image alt text",
    "lead_magnet": "the title and subtitle, chapter or section headings, the landing-page copy and the opt-in promise",
    "blog": "H2/H3 headings, the first 100 words, the body paragraphs, the FAQ and the meta description",
    "webinar": "the session title and promise, slide titles, talking points, and the registration page and email copy",
}


def _secondary_duty(asset_id: str | None, targets: KeywordTargets) -> str:
    placement = SECONDARY_PLACEMENT.get(asset_id or "")
    if not placement or not any(g.secondary for g in targets.groups):
        return ""
    return (
        "- This asset must use its secondary keywords: work every secondary keyword listed above into "
        f"{placement}, each at least once and worded naturally. Prefer the exact phrase; a close inflection "
        "of it is acceptable. A secondary keyword that cannot be used without reading as forced is left "
        "out rather than stuffed.\n"
    )


def targets_block(targets: KeywordTargets, asset_id: str | None = None) -> str:
    """The `KEYWORD_TARGETS` block for a stage's prompt tail, or "" when there is nothing measured."""
    if not targets.groups:
        return ""
    groups = []
    for i, g in enumerate(targets.groups, start=1):
        lines = [f"{i}. Primary keyword: {_kw_line(g.primary)}"]
        if g.secondary:
            lines.append("   Secondary keywords: " + "; ".join(_kw_line(k) for k in g.secondary))
        groups.append("\n".join(lines))
    names = sorted({s for group in (targets.synonyms or {}).values() for s in group})
    also = (
        f"\nBuyers also call this service: {', '.join(names)}. Use these names naturally too, so the asset "
        "matches every way people search for it.\n"
        if names
        else ""
    )
    country = targets.country
    return (
        f"===== BEGIN {TARGETS_BLOCK} =====\n"
        "HOW THIS BLOCK BINDS YOUR RESPONSE:\n"
        "It says which words buyers search with. Do not quote, name or acknowledge this block in the "
        "output, and never repeat these numbers in copy written for the client's customer.\n\n"
        f"Measured by DataForSEO for {targets.service or 'this run'}{f' in {country}' if country else ''}, "
        "ranked by monthly searches, estimated traffic and search momentum (the % change in searches). "
        "Each primary keyword is a core search term; its secondary keywords are the same search with "
        "a modifier (a place, buy, best, online):\n\n"
        + "\n".join(groups)
        + "\n"
        f"{also}\n"
        "How to use them:\n"
        "- Lead with primary keyword 1 where this asset has a title, H1, subject line, hook or opening "
        "line, worded naturally.\n"
        "- Use the other primary keywords in headings and the secondary keywords in body copy, where "
        "they fit what is being said. Never stuff them, list them for their own sake, or force one "
        "where it reads unnaturally.\n"
        f"{_secondary_duty(asset_id, targets)}"
        "- Where the client's terminology map or locked copy words something differently, keep the "
        "client's wording there and use the keyword elsewhere.\n"
        "- In assets not read from search (SMS, emails, scripts, social posts), use them as vocabulary "
        "only.\n"
        "- Where this asset reports keywords or volumes (a strategy, a plan, a cluster map), use these "
        "measured figures and do not invent others.\n"
        f"===== END {TARGETS_BLOCK} =====\n\n"
    )
