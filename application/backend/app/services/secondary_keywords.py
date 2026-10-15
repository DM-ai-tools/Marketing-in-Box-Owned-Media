"""Secondary / cluster terms for the Pillar Page, suggested from its head term by Ahrefs and DataForSEO.

The Pillar Page asks for "supporting terms this page should cover in passing" right after the head
term is chosen. Typed cold, that list was whatever the operator remembered. Here both providers are
asked for the head term's phrase matches and related searches in the same country, filtered the
same way, and returned side by side, so the operator can compare them and pick from either.

- **Two requests per provider.** Ahrefs `matching-terms` + `related-terms`; DataForSEO
  `keyword_suggestions` + `related_keywords`. Never a loop over keywords.
- **Each provider fails on its own.** One that is unconfigured, out of units or erroring gets a
  status and one sentence; the other column still shows. Both failing is still a 200: the card
  offers the text box, because the field is optional.
- **Same filter for both columns**, so the comparison is of the data and not of the cleaning: the
  head term itself, its plurals and stale-year/URL junk are dropped, and a suggestion has to share a
  content word with the head term (Ahrefs `related-terms` otherwise returns "facebook").
- **Measured numbers only.** A missing volume stays None and is shown as "—", never 0.
- **No country, no request.** A region that names no country is an error on both columns with the
  fix, never a default country (the same rule as `keywords.resolve_location`).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field

from app.services import ahrefs_client, dataforseo_client, keywords

logger = logging.getLogger(__name__)

#: Rows shown per provider, after filtering.
MAX_PER_PROVIDER = 15
#: Rows asked for per request, before filtering.
_REQUEST_LIMIT = 40


@dataclass(frozen=True)
class SecondaryKeyword:
    keyword: str
    volume: int | None
    difficulty: int | None
    cpc: float | None = None
    intent: str | None = None


@dataclass
class ProviderSuggestions:
    provider: str
    #: `ok` | `unconfigured` | `error`.
    status: str
    keywords: list[SecondaryKeyword] = field(default_factory=list)
    error: str = ""


@dataclass
class SecondarySuggestions:
    head_term: str
    location: str
    ahrefs: ProviderSuggestions
    dataforseo: ProviderSuggestions

    def as_dict(self) -> dict:
        return asdict(self)


def _names_another_country(keyword: str, country: str) -> bool:
    """"social media marketing agency india" for an Australian page: a term for someone else's market."""
    words = f" {keyword} "
    return any(
        f" {name.lower()} " in words for name in ahrefs_client.COUNTRIES if name.lower() != country.lower() and len(name) > 3
    )


def filter_suggestions(
    head_term: str, rows: list[SecondaryKeyword], country: str = "", limit: int = MAX_PER_PROVIDER
) -> list[SecondaryKeyword]:
    """The same cleaning for both columns: highest volume first, deduped, on-topic, never the head term.

    On-topic means sharing two content words with the head term (one, for a one-word head term).
    Measured live: with a one-word rule Ahrefs' column filled with "social", "agency" and "social
    club" for "social media marketing agency". Single words are dropped for the same reason, and so
    is any term naming a country other than the page's (DataForSEO offered "... agency india")."""
    head_norm = keywords.normalize(head_term)
    head_key = keywords.dedupe_key(head_norm)
    head_tokens = {keywords.singular(t) for t in keywords.tokens(head_norm)}
    need = min(2, len(head_tokens))
    seen: set[str] = {head_key}
    out: list[SecondaryKeyword] = []
    for row in sorted(rows, key=lambda r: (r.volume is None, -(r.volume or 0), r.keyword)):
        norm = keywords.normalize(row.keyword)
        if not norm or keywords.is_noise(norm, row.keyword) or len(norm.split()) < 2:
            continue
        key = keywords.dedupe_key(norm)
        if key in seen:
            continue
        if len({keywords.singular(t) for t in keywords.tokens(norm)} & head_tokens) < need:
            continue
        if country and _names_another_country(norm, country):
            continue
        seen.add(key)
        out.append(SecondaryKeyword(norm, row.volume, row.difficulty, row.cpc, row.intent))
        if len(out) >= limit:
            break
    return out


async def _from_ahrefs(head_term: str, country_name: str) -> ProviderSuggestions:
    if not ahrefs_client.is_configured():
        return ProviderSuggestions("ahrefs", "unconfigured", error="Ahrefs is not configured (AHREFS_API_KEY).")
    client = ahrefs_client.AhrefsClient()
    try:
        code = ahrefs_client.country_code(country_name)
        phrase, related = await asyncio.gather(
            client.expand(head_term, "phrase", code, _REQUEST_LIMIT),
            client.expand(head_term, "related", code, _REQUEST_LIMIT),
        )
        rows = [
            SecondaryKeyword(m.keyword, m.volume, m.difficulty, ahrefs_client.cpc_dollars(m.cpc), None)
            for m in [*phrase, *related]
        ]
        return ProviderSuggestions("ahrefs", "ok", filter_suggestions(head_term, rows, country_name))
    except Exception as exc:  # noqa: BLE001 — one provider failing must not cost the other column
        logger.warning("Ahrefs secondary keywords failed for %r: %s", head_term, exc)
        return ProviderSuggestions("ahrefs", "error", error=str(exc) or exc.__class__.__name__)
    finally:
        await client.aclose()


async def _from_dataforseo(head_term: str, country_name: str) -> ProviderSuggestions:
    if not dataforseo_client.is_configured():
        return ProviderSuggestions(
            "dataforseo", "unconfigured", error="DataForSEO is not configured (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD)."
        )
    client = dataforseo_client.DataForSEOClient()
    try:
        phrase, related = await asyncio.gather(
            client.keywords(dataforseo_client.SUGGESTIONS, head_term, country_name, limit=_REQUEST_LIMIT),
            client.keywords(dataforseo_client.RELATED, head_term, country_name, limit=_REQUEST_LIMIT),
        )
        rows = [SecondaryKeyword(k.keyword, k.volume, k.difficulty, k.cpc, k.intent) for k in [*phrase, *related]]
        return ProviderSuggestions("dataforseo", "ok", filter_suggestions(head_term, rows, country_name))
    except Exception as exc:  # noqa: BLE001 — one provider failing must not cost the other column
        logger.warning("DataForSEO secondary keywords failed for %r: %s", head_term, exc)
        return ProviderSuggestions("dataforseo", "error", error=str(exc) or exc.__class__.__name__)
    finally:
        await client.aclose()


async def suggest(head_term: str, region: str) -> SecondarySuggestions:
    head_term = " ".join(head_term.split())
    country = keywords.match_location(region, ahrefs_client.COUNTRIES)
    if not country:
        why = (
            f"Region {region!r} does not name a country, so no keyword data was requested. "
            'Add the country to the region answer (for example "Melbourne, Australia").'
            if region.strip()
            else "No region is on record for this client, so no keyword data was requested. "
            'Set a region with a country (for example "Melbourne, Australia").'
        )
        return SecondarySuggestions(
            head_term, "", ProviderSuggestions("ahrefs", "error", error=why), ProviderSuggestions("dataforseo", "error", error=why)
        )
    ahrefs, dfs = await asyncio.gather(_from_ahrefs(head_term, country), _from_dataforseo(head_term, country))
    return SecondarySuggestions(head_term, country, ahrefs, dfs)
