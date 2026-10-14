"""The only module that talks to Ahrefs — Keywords Explorer, API v3.

Ahrefs replaced DataForSEO as this app's keyword source. It is what `keywords.py` expands and
measures seeds with, and what `sub_service_discovery.py` puts volumes on the Plan of Action's
sub-services with. Same shape as `sociavault_client.py` and `firecrawl_client.py`: one exception
type, `is_configured()`, a small client, and raw fields back — deciding what a keyword *means*
(relevant, branded, which intent) stays with the caller.

The key
-------
`AHREFS_API_KEY` in the backend `.env`, sent as a bearer token. A server-side secret like every
other key here: never logged, never sent to the browser. Rotate it in the Ahrefs account.

What it costs
-------------
Ahrefs bills API **units** per request and per row returned, not a flat price per call. So `limit`
on every expansion is the cost dial, the same role `num_results` plays on Context.dev's search.
`keywords.py` asks for at most `limit_per_class` rows per seed and class, and `overview` sends every
keyword in one request (chunked at `_OVERVIEW_CHUNK`) rather than one request each.

Verified against the live API (2026-10-14), not just the docs:

* Keywords Explorer takes `country` as a **lowercase two-letter code** and nothing else. A name
  ("Australia") or an unknown code is a 400 that lists every accepted code; `COUNTRIES` is that
  list, named, so a free-text region can be matched against it (`keywords.match_location`).
* CPC comes back in **US cents** (1300 = $13.00). `cpc_dollars` converts it.
* A field Ahrefs has no data for is `null`, never 0 — kept as `None` here for the same reason
  `sociavault_client` does: 0 would claim a measured absence.
* `related-terms` returns broad topical neighbours ("meta ads agency" -> "facebook", "instagram");
  `keywords.is_relevant` is what filters them, so they are fetched with the same limit as the rest.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ahrefs.com/v3/keywords-explorer/"
_TIMEOUT = 90.0
_OVERVIEW_CHUNK = 100

#: The match classes `keywords.py` expands each seed through, and the Ahrefs endpoint for each.
#: `phrase` — terms containing the seed; `related` — terms the same pages also rank for;
#: `broad` — Google's own search suggestions around the seed, which is where the buyer long tail
#: ("meta ads agency melbourne") lives.
MATCH_CLASS_ENDPOINTS: dict[str, str] = {
    "phrase": "matching-terms",
    "related": "related-terms",
    "broad": "search-suggestions",
}

_SELECT = "keyword,volume,difficulty,cpc,parent_topic"

#: Every country Ahrefs Keywords Explorer accepts, by the name an operator would type. The codes are
#: the ones the live API listed; the names are what `keywords.match_location` matches a region
#: against.
COUNTRIES: dict[str, str] = {
    "Andorra": "ad", "United Arab Emirates": "ae", "Afghanistan": "af", "Antigua and Barbuda": "ag",
    "Anguilla": "ai", "Albania": "al", "Armenia": "am", "Angola": "ao", "Argentina": "ar",
    "American Samoa": "as", "Austria": "at", "Australia": "au", "Aruba": "aw", "Azerbaijan": "az",
    "Bosnia and Herzegovina": "ba", "Barbados": "bb", "Bangladesh": "bd", "Belgium": "be",
    "Burkina Faso": "bf", "Bulgaria": "bg", "Bahrain": "bh", "Burundi": "bi", "Benin": "bj",
    "Brunei": "bn", "Bolivia": "bo", "Brazil": "br", "Bahamas": "bs", "Bhutan": "bt", "Botswana": "bw",
    "Belarus": "by", "Belize": "bz", "Canada": "ca", "Democratic Republic of the Congo": "cd",
    "Central African Republic": "cf", "Republic of the Congo": "cg", "Switzerland": "ch",
    "Cote d'Ivoire": "ci", "Cook Islands": "ck", "Chile": "cl", "Cameroon": "cm", "China": "cn",
    "Colombia": "co", "Costa Rica": "cr", "Cuba": "cu", "Cape Verde": "cv", "Cyprus": "cy",
    "Czechia": "cz", "Germany": "de", "Djibouti": "dj", "Denmark": "dk", "Dominica": "dm",
    "Dominican Republic": "do", "Algeria": "dz", "Ecuador": "ec", "Estonia": "ee", "Egypt": "eg",
    "Spain": "es", "Ethiopia": "et", "Finland": "fi", "Fiji": "fj", "Micronesia": "fm",
    "Faroe Islands": "fo", "France": "fr", "Gabon": "ga", "United Kingdom": "gb", "Grenada": "gd",
    "Georgia": "ge", "French Guiana": "gf", "Guernsey": "gg", "Ghana": "gh", "Gibraltar": "gi",
    "Greenland": "gl", "Gambia": "gm", "Guinea": "gn", "Guadeloupe": "gp", "Equatorial Guinea": "gq",
    "Greece": "gr", "Guatemala": "gt", "Guam": "gu", "Guyana": "gy", "Hong Kong": "hk",
    "Honduras": "hn", "Croatia": "hr", "Haiti": "ht", "Hungary": "hu", "Indonesia": "id",
    "Ireland": "ie", "Israel": "il", "Isle of Man": "im", "India": "in", "Iraq": "iq", "Iceland": "is",
    "Italy": "it", "Jersey": "je", "Jamaica": "jm", "Jordan": "jo", "Japan": "jp", "Kenya": "ke",
    "Kyrgyzstan": "kg", "Cambodia": "kh", "Kiribati": "ki", "Saint Kitts and Nevis": "kn",
    "South Korea": "kr", "Kuwait": "kw", "Cayman Islands": "ky", "Kazakhstan": "kz", "Laos": "la",
    "Lebanon": "lb", "Saint Lucia": "lc", "Liechtenstein": "li", "Sri Lanka": "lk", "Lesotho": "ls",
    "Lithuania": "lt", "Luxembourg": "lu", "Latvia": "lv", "Libya": "ly", "Morocco": "ma",
    "Monaco": "mc", "Moldova": "md", "Montenegro": "me", "Madagascar": "mg", "North Macedonia": "mk",
    "Mali": "ml", "Myanmar": "mm", "Mongolia": "mn", "Martinique": "mq", "Mauritania": "mr",
    "Montserrat": "ms", "Malta": "mt", "Mauritius": "mu", "Maldives": "mv", "Malawi": "mw",
    "Mexico": "mx", "Malaysia": "my", "Mozambique": "mz", "Namibia": "na", "New Caledonia": "nc",
    "Niger": "ne", "Nigeria": "ng", "Nicaragua": "ni", "Netherlands": "nl", "Norway": "no",
    "Nepal": "np", "Nauru": "nr", "Niue": "nu", "New Zealand": "nz", "Oman": "om", "Panama": "pa",
    "Peru": "pe", "French Polynesia": "pf", "Papua New Guinea": "pg", "Philippines": "ph",
    "Pakistan": "pk", "Poland": "pl", "Pitcairn Islands": "pn", "Puerto Rico": "pr", "Palestine": "ps",
    "Portugal": "pt", "Paraguay": "py", "Qatar": "qa", "Reunion": "re", "Romania": "ro",
    "Serbia": "rs", "Russia": "ru", "Rwanda": "rw", "Saudi Arabia": "sa", "Solomon Islands": "sb",
    "Seychelles": "sc", "Sweden": "se", "Singapore": "sg", "Saint Helena": "sh", "Slovenia": "si",
    "Slovakia": "sk", "Sierra Leone": "sl", "San Marino": "sm", "Senegal": "sn", "Somalia": "so",
    "Suriname": "sr", "Sao Tome and Principe": "st", "El Salvador": "sv", "Chad": "td", "Togo": "tg",
    "Thailand": "th", "Tajikistan": "tj", "Tokelau": "tk", "Timor-Leste": "tl", "Turkmenistan": "tm",
    "Tunisia": "tn", "Tonga": "to", "Turkiye": "tr", "Trinidad and Tobago": "tt", "Taiwan": "tw",
    "Tanzania": "tz", "Ukraine": "ua", "Uganda": "ug", "United States": "us", "Uruguay": "uy",
    "Uzbekistan": "uz", "Saint Vincent and the Grenadines": "vc", "Venezuela": "ve",
    "British Virgin Islands": "vg", "U.S. Virgin Islands": "vi", "Vietnam": "vn", "Vanuatu": "vu",
    "Samoa": "ws", "Yemen": "ye", "Mayotte": "yt", "South Africa": "za", "Zambia": "zm",
    "Zimbabwe": "zw",
}


class AhrefsError(RuntimeError):
    """Ahrefs refused a request, or answered in a shape this module cannot read. The message is one
    sentence an operator can act on: a missing key, a rejected key, no units left, a bad country."""


@dataclass(frozen=True)
class KeywordMetrics:
    keyword: str
    volume: int | None
    difficulty: int | None
    #: US cents, as Ahrefs reports it. None when Ahrefs has no CPC for the keyword.
    cpc: int | None = None
    parent_topic: str | None = None


def api_key() -> str:
    return (os.environ.get("AHREFS_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(api_key())


def cpc_dollars(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def country_code(name: str) -> str:
    """The Ahrefs code for a name in `COUNTRIES`. Raises for anything else, rather than letting the
    request go out and come back as a 400."""
    code = COUNTRIES.get(name)
    if code is None:
        raise AhrefsError(f"{name!r} is not a country Ahrefs has keyword data for.")
    return code


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _metrics(row: dict[str, Any]) -> KeywordMetrics | None:
    keyword = row.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        return None
    parent = row.get("parent_topic")
    return KeywordMetrics(
        keyword=keyword.strip(),
        volume=_int(row.get("volume")),
        difficulty=_int(row.get("difficulty")),
        cpc=_int(row.get("cpc")),
        parent_topic=parent if isinstance(parent, str) and parent.strip() else None,
    )


def _error_detail(response: httpx.Response) -> str:
    """Ahrefs' own reason, whatever shape it came in. A bad key answers `["Error","Unauthorized"]`
    (a list, verified live), a bad parameter `{"error": "..."}`, and a proxy error plain text."""
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        return str(body.get("error") or body)
    if isinstance(body, list):
        return ": ".join(str(part) for part in body)
    return str(body)


def _fail(endpoint: str, response: httpx.Response) -> AhrefsError:
    detail = _error_detail(response)
    detail = detail.strip()[:300]
    status = response.status_code
    if status == 401:
        return AhrefsError(f"Ahrefs rejected the API key (401). Check AHREFS_API_KEY. {detail}")
    if status in (402, 403):
        return AhrefsError(
            f"Ahrefs refused {endpoint} ({status}): the plan may be out of API units or not include "
            f"Keywords Explorer. {detail}"
        )
    if status == 429:
        return AhrefsError(f"Ahrefs rate-limited {endpoint} (429). Try again shortly. {detail}")
    return AhrefsError(f"Ahrefs {endpoint} failed ({status}): {detail}")


class AhrefsClient:
    """A minimal async Keywords Explorer client: one GET per call, raw rows back."""

    def __init__(self, key: str | None = None, timeout: float = _TIMEOUT) -> None:
        key = key if key is not None else api_key()
        if not key:
            raise AhrefsError("AHREFS_API_KEY is not set, so keyword data cannot be fetched.")
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self._client.get(endpoint, params=params)
        if response.status_code != 200:
            raise _fail(endpoint, response)
        try:
            body = response.json()
        except ValueError as exc:
            raise AhrefsError(f"Ahrefs {endpoint} did not answer with JSON.") from exc
        rows = body.get("keywords") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise AhrefsError(f"Ahrefs {endpoint} answered without a keywords list.")
        return [r for r in rows if isinstance(r, dict)]

    async def overview(self, keywords: list[str], country: str) -> dict[str, KeywordMetrics]:
        """Metrics for exactly these keywords, keyed by lowercased keyword. One request per
        `_OVERVIEW_CHUNK` keywords."""
        out: dict[str, KeywordMetrics] = {}
        unique = list(dict.fromkeys(k.strip() for k in keywords if k and k.strip()))
        for start in range(0, len(unique), _OVERVIEW_CHUNK):
            chunk = unique[start : start + _OVERVIEW_CHUNK]
            rows = await self._get("overview", {"keywords": ",".join(chunk), "country": country, "select": _SELECT})
            for row in rows:
                metrics = _metrics(row)
                if metrics is not None:
                    out[metrics.keyword.lower()] = metrics
        return out

    async def expand(self, seed: str, match_class: str, country: str, limit: int) -> list[KeywordMetrics]:
        """The keywords one match class finds around `seed`, highest volume first."""
        endpoint = MATCH_CLASS_ENDPOINTS[match_class]
        rows = await self._get(
            endpoint,
            {"keywords": seed, "country": country, "select": _SELECT, "order_by": "volume:desc", "limit": limit},
        )
        return [m for m in (_metrics(r) for r in rows) if m is not None]
