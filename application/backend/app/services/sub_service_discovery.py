"""The sub-services a Plan of Action suggests for Phase 2: what the client's site already sells, what
it does not, and how much search demand each one has.

Why this exists
----------------
The Plan of Action is Phase 1's last stage. Its Phase 2 section briefs one page per sub-service, and
until this existed the sub-services were whatever the operator typed into one text box, or "infer
6-10" — a model guessing at both what the client already offers and what buyers search for. The
reference plan this was modelled on (Traffic Radius, Google Ads Management) did neither: it listed
the pages the site already had, named the ones competitors had and the client lacked, and put a
search volume on every keyword.

Measure, then judge, then measure again — the order `service_scan.py` and `asset_check.py` use:

1. **Measure, free + one Haiku call.** `service_scan.scan_services` reads the client's own menu and
   returns the sub-services it links to, each with its real URL.
2. **Judge, one Haiku call.** Given that list, the competitor listings this run already approved and
   the ICP, the model keeps the existing sub-services that belong under the primary service, and
   suggests the missing ones competitors cover. Each comes with a 4-6 keyword cluster. An "existing"
   item must carry a URL the scan found (`_parse_reply` drops it otherwise), so a page the site does
   not link to can never be reported as already built.
3. **Measure, one DataForSEO call.** `keyword_overview` returns the real monthly volume for every
   keyword at once (`dataforseo_client.py`). With no login, or any failure, the volumes stay
   `None` and `volume_error` says why, which the card shows as a warning —
   never a stub number, because a plan built on an invented volume reads exactly like one built on a
   real one.

Credits
-------
Two Haiku calls and one DataForSEO overview request, whatever the size of the site. The competitor
listings are read from the run, never searched again. `MAX_CANDIDATES` and `MAX_KEYWORDS_PER_CANDIDATE`
bound the provider request.

Nothing is stored here. The operator confirms the list on a card, the confirmed list becomes the
plan's `sub_services_to_expand_into_phase_2` answer, and `PLAN_SUB_SERVICES_KEY` is filed on the run
so Phase 2 can offer the same sub-services when it starts.
"""

from __future__ import annotations

import json
import logging

import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from app.services import dataforseo_client
from app.services import keywords as keyword_service
from app.services import keyword_rank, service_scan, service_synonyms
from app.services.claude_client import get_client
from app.services.generation import HAIKU, OnUsage
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

SUGGEST_MODEL = HAIKU
_SUGGEST_MAX_TOKENS = 4000
#: Candidates on the card, existing and suggested together. The reference plan had eight.
MAX_CANDIDATES = 12
MAX_KEYWORDS_PER_CANDIDATE = 6
#: Alternate names kept per candidate, measured alongside its keywords in the same request.
MAX_SYNONYMS_PER_CANDIDATE = 3
#: Characters of each competitor listing and of the ICP handed to the judge.
_LISTING_CHARS = 2500
_ICP_CHARS = 2500

#: The context key the confirmed list is filed under, on the Phase 1 run. Phase 2 runs inherit it
#: down `source_run_id` and offer its names as the sub-service choices.
PLAN_SUB_SERVICES_KEY = "plan_sub_services"

#: The competitor listings a Phase 1 run approves that describe competitors' service pages, best
#: first. Read from the run, never re-searched.
COMPETITOR_LISTING_KEYS: tuple[str, ...] = (
    "competitor_analysis_seo_pillar_page",
    "competitor_analysis_cro",
    "competitor_analysis_offers",
    "competitor_analysis_content_marketing",
)

EXISTING = "existing"
NEW = "new"


class DiscoveryError(Exception):
    """Discovery could not run: the site could not be read, or the model could not be asked."""


@dataclass
class KeywordVolume:
    keyword: str
    #: Monthly searches from DataForSEO. None when the provider is not configured or had no data —
    #: never 0, which would claim a measured absence of demand.
    volume: int | None = None
    #: `keyword`, or `synonym`: another name people use for the sub-service (`service_synonyms`).
    kind: str = "keyword"
    difficulty: int | None = None
    #: Search momentum, % change. None when DataForSEO gave none.
    trend: int | None = None
    #: `keyword_rank.score_of`: searches, estimated traffic and momentum. None without a volume.
    score: int | None = None


@dataclass
class SubServiceCandidate:
    name: str
    #: `existing` — the client's site already has a page for it; `new` — it does not.
    status: str
    #: The existing page's URL (from the scan), or "" for a new one.
    url: str = ""
    #: The page's path: the real one for an existing page, a proposed one for a new page.
    slug: str = ""
    #: The broader service it sits under on the site's menu, when it does.
    parent: str = ""
    #: One sentence: why it belongs in the plan (who searches for it, which competitor has it).
    why: str = ""
    keywords: list[KeywordVolume] = field(default_factory=list)

    @property
    def combined_volume(self) -> int | None:
        known = [k.volume for k in self.keywords if isinstance(k.volume, int)]
        return sum(known) if known else None


@dataclass
class Discovery:
    source_url: str
    primary_service: str
    candidates: list[SubServiceCandidate] = field(default_factory=list)
    #: `dataforseo` when the volumes are real, `unavailable` when they could not be fetched.
    volume_source: str = "unavailable"
    #: The country the volumes are for (countries only).
    location: str = ""
    #: Why the volumes are missing, when they are: shown to the operator as a warning on the card.
    volume_error: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        for row, candidate in zip(out["candidates"], self.candidates):
            row["combined_volume"] = candidate.combined_volume
        return out


# --------------------------------------------------------------------------------------
# Judge: which existing sub-services belong, and which are missing
# --------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return "/" + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") + "/"


def _path(url: str) -> str:
    path = urlparse(url).path or "/"
    return path if path.endswith("/") else path + "/"


def _excerpt(text: str, limit: int) -> str:
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n[…]"


def _suggest_prompt(
    primary_service: str,
    region: str,
    scanned: list[service_scan.ScannedService],
    competitors: str,
    icp: str,
) -> str:
    on_site = (
        "\n".join(
            f"{i}. {s.name}{f' (under: {s.parent})' if s.parent else ''} — {s.url}" for i, s in enumerate(scanned, 1)
        )
        or "(the scan found no sub-service pages)"
    )
    return (
        f'You are planning Phase 2 of an owned-media plan for a business whose primary service is "{primary_service}"'
        f"{f', selling in {region}' if region else ''}. Phase 2 builds one dedicated page and funnel per SUB-SERVICE: "
        "a specific, separately searched offering inside the primary service (for \"Social Media Marketing\": "
        "Meta Ads, LinkedIn Ads, TikTok Ads, Instagram Marketing; for \"Google Ads Management\": Search Ads, "
        "Shopping Ads, Performance Max, YouTube Ads).\n\n"
        "SUB-SERVICE PAGES ON THE CLIENT'S OWN SITE (read from its menu):\n"
        f"{on_site}\n\n"
        "COMPETITOR RESEARCH ALREADY APPROVED IN THIS ENGAGEMENT:\n"
        f"{competitors or '(none on record)'}\n\n"
        "IDEAL CUSTOMER PROFILE:\n"
        f"{icp or '(none on record)'}\n\n"
        "Task:\n"
        f'1. From the site list, keep every item that is a sub-service of "{primary_service}" as status "existing", '
        "copying its `url` exactly. Leave out items that belong to a different service, and the primary service's "
        "own page.\n"
        "2. Add the sub-services this business does NOT have a page for but should: ones buyers search for "
        "separately, that competitors give their own page, or that the ICP's needs point to. Status \"new\", "
        "`url` empty, `slug` a proposed path such as /tiktok-ads/.\n"
        f"3. At most {MAX_CANDIDATES} items in total, existing first. Every item must be genuinely distinct — "
        "not the same offering under two names.\n"
        f"4. For each, {MAX_KEYWORDS_PER_CANDIDATE - 2}-{MAX_KEYWORDS_PER_CANDIDATE} keywords a buyer in {region or 'the region'} "
        "would type to find it: lowercase, no brand names, the head term first.\n"
        "5. `why`: one sentence on why it belongs in the plan, naming the competitor or ICP need it comes from "
        "where there is one. Never invent a statistic.\n"
        f"6. `synonyms`: 2-{MAX_SYNONYMS_PER_CANDIDATE} alternate names for the sub-service itself, following the "
        "guidance below, so every way buyers search for it is measured.\n\n"
        f"ALTERNATE NAMES — GUIDANCE:\n{service_synonyms.GUIDANCE}\n\n"
        'Reply with ONLY a JSON object, no prose, no code fence: {"sub_services": [{"name": "...", '
        '"status": "existing|new", "url": "...", "slug": "...", "parent": "...", "why": "...", '
        '"keywords": ["...", "..."], "synonyms": ["...", "..."]}]}'
    )


def _parse_reply(raw: str, scanned: list[service_scan.ScannedService]) -> tuple[list[SubServiceCandidate], list[str]]:
    """The model's candidates. An `existing` item whose URL the scan did not find is demoted to
    `new` — the site does not link to it, so it cannot be reported as already built."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise DiscoveryError("The sub-service suggestion's reply was not the requested JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise DiscoveryError("The sub-service suggestion's reply was not valid JSON.") from exc
    items = data.get("sub_services") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise DiscoveryError("The sub-service suggestion's reply had no sub_services list.")

    by_url = {s.url.rstrip("/"): s for s in scanned}
    notes: list[str] = []
    out: list[SubServiceCandidate] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        status = EXISTING if str(item.get("status") or "").strip().lower() == EXISTING else NEW
        url = str(item.get("url") or "").strip().rstrip("/")
        found = by_url.get(url) if url else None
        if status == EXISTING and found is None:
            notes.append(f"{name}: suggested as existing, but the site does not link to it — listed as new.")
            status = NEW
        words: list[str] = []
        for kw in item.get("keywords") or []:
            kw = re.sub(r"\s+", " ", str(kw)).strip().lower()
            if kw and kw not in words:
                words.append(kw)
        if not words:
            words = [name.lower()]
        names = [
            n
            for n in service_synonyms.clean_synonyms(name, [str(x) for x in item.get("synonyms") or []])
            if n not in words
        ][:MAX_SYNONYMS_PER_CANDIDATE]
        candidate = SubServiceCandidate(
            name=name,
            status=status,
            url=found.url if found else "",
            slug=_path(found.url) if found else (str(item.get("slug") or "").strip() or _slug(name)),
            parent=(found.parent if found else str(item.get("parent") or "").strip()),
            why=str(item.get("why") or "").strip(),
            keywords=[KeywordVolume(k) for k in words[:MAX_KEYWORDS_PER_CANDIDATE]]
            + [KeywordVolume(n, kind="synonym") for n in names],
        )
        out.append(candidate)
        if len(out) >= MAX_CANDIDATES:
            break
    # Existing first, as the card and the plan's inventory both read them.
    out.sort(key=lambda c: c.status != EXISTING)
    return out, notes


async def _ask(prompt: str, on_usage: OnUsage | None) -> str:
    started = time.monotonic()
    response = await get_client().messages.create(
        model=SUGGEST_MODEL,
        max_tokens=_SUGGEST_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=SUGGEST_MODEL,
                                    duration_ms=int((time.monotonic() - started) * 1000))
        )
    return next((b.text for b in response.content if b.type == "text"), "")


# --------------------------------------------------------------------------------------
# Measure: real search volumes
# --------------------------------------------------------------------------------------


async def _fill_volumes(discovery: Discovery, region: str) -> None:
    """One DataForSEO `keyword_overview` request for every keyword on the card. Never the stub provider.

    Any failure — no login, a rejected login, no balance left, a region with no country, a network
    error — leaves the volumes `None` and sets `volume_error` to one sentence. The card shows it as a
    warning, so the operator knows the plan's volumes will be estimates and why, rather than finding
    a list with no numbers on it."""
    keywords = list(dict.fromkeys(k.keyword for c in discovery.candidates for k in c.keywords))
    if not keywords:
        return
    if not dataforseo_client.is_configured():
        discovery.volume_error = (
            "DataForSEO is not configured on this server (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD), so "
            "search volumes could not be fetched. The plan will label every volume as an estimate."
        )
        return
    config = keyword_service.KeywordRunConfig(
        business_name="",
        website="",
        location_name=region.strip() or keyword_service.DEFAULT_LOCATION,
        language_name=keyword_service.DEFAULT_LANGUAGE,
        services={},
    )
    client: dataforseo_client.DataForSEOClient | None = None
    try:
        country = keyword_service.resolve_location(config)
        client = dataforseo_client.DataForSEOClient()
        metrics = await client.overview(keywords, country)
    except Exception as exc:  # noqa: BLE001 — the list still stands without volumes
        logger.warning("Sub-service volumes failed: %s", exc)
        discovery.volume_error = (
            f"DataForSEO could not fetch search volumes: {exc} The plan will label every volume as an estimate."
        )
        return
    finally:
        if client is not None:
            await client.aclose()

    for candidate in discovery.candidates:
        for kw in candidate.keywords:
            found = metrics.get(kw.keyword.lower())
            kw.volume = found.volume if found else None
            kw.difficulty = found.difficulty if found else None
            kw.trend = found.trend if found else None
            kw.score = keyword_rank.score_of(kw.volume, kw.difficulty, kw.trend)
        # Best-ranked first (searches, estimated traffic, momentum); unmeasured ones last.
        candidate.keywords.sort(key=lambda k: (k.score is None, -(k.score or 0), -(k.volume or 0)))
    discovery.volume_source = "dataforseo"
    discovery.location = country


# --------------------------------------------------------------------------------------
# The whole discovery
# --------------------------------------------------------------------------------------


async def discover(
    url: str,
    primary_service: str,
    *,
    region: str = "",
    competitors: str = "",
    icp: str = "",
    on_usage: OnUsage | None = None,
) -> Discovery:
    """Existing and suggested sub-services of `primary_service`, with real volumes when available.

    Raises `DiscoveryError` only when the judge cannot run. A site that cannot be read still gets
    suggestions, from the competitors and the ICP, with a note saying nothing was found on it.
    """
    service = primary_service.strip()
    if not service:
        raise DiscoveryError("The plan needs the primary service before it can suggest sub-services.")

    discovery = Discovery(source_url=url, primary_service=service)
    scanned: list[service_scan.ScannedService] = []
    if url.strip():
        try:
            scan = await service_scan.scan_services(url, phase="phase2", focus=service, on_usage=on_usage)
            scanned = scan.services
            discovery.source_url = scan.source_url
            discovery.notes += scan.notes
        except service_scan.ServiceScanError as exc:
            discovery.notes.append(f"The client's site could not be read ({exc}), so no existing pages are listed.")
    else:
        discovery.notes.append("No client website on record, so no existing pages are listed.")

    prompt = _suggest_prompt(service, region.strip(), scanned, _excerpt(competitors, _LISTING_CHARS * 2), _excerpt(icp, _ICP_CHARS))
    try:
        raw = await _ask(prompt, on_usage)
    except Exception as exc:  # noqa: BLE001 — any model failure is the same outcome for the operator
        raise DiscoveryError(f"The sub-service suggestion could not run: {exc}") from exc

    discovery.candidates, notes = _parse_reply(raw, scanned)
    discovery.notes += notes
    if not discovery.candidates:
        discovery.notes.append("No sub-services were suggested. Type them on the card instead.")
        return discovery

    await _fill_volumes(discovery, region)
    return discovery


def competitor_block(listings: dict[str, str]) -> str:
    """The run's approved competitor listings, each cut to `_LISTING_CHARS`, in `COMPETITOR_LISTING_KEYS` order."""
    parts = [
        f"## {key.removeprefix('competitor_analysis_').replace('_', ' ')}\n{_excerpt(listings[key], _LISTING_CHARS)}"
        for key in COMPETITOR_LISTING_KEYS
        if (listings.get(key) or "").strip()
    ]
    return "\n\n".join(parts)


def names_for_phase2(content: str) -> list[str]:
    """The sub-service names a stored `plan_sub_services` entry offers Phase 2, in order."""
    out: list[str] = []
    for line in (content or "").splitlines():
        m = re.match(r"^\s*-\s+(.+?)(?:\s+—\s+|$)", line)
        if m and m.group(1).strip() and m.group(1).strip() not in out:
            out.append(m.group(1).strip())
    return out
