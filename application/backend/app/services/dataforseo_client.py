"""DataForSEO Labs — the only module that talks to DataForSEO.

The pipeline's only keyword source: the keyword clusters (`keywords.py`), plan discovery's volumes,
the secondary-terms, blog and content keyword cards, and competitor validation's rankings. Nothing
else talks to a keyword provider.

Credentials are HTTP Basic: `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` in the backend `.env`
(`DataForTopicClusttering_LOGIN` / `_PASSWORD`, the names they were first added under, are read too).
Server-side only, never logged.

Shapes, from the live API when this pipeline used DataForSEO for clustering:
  - `POST https://api.dataforseo.com/v3/dataforseo_labs/google/{endpoint}/live` with a one-task
    list body; `location_name` is a country name ("Australia"), `language_name` "English".
  - A request can be HTTP 200 and still have failed: the task's own `status_code` must be 20000.
  - `keyword_suggestions` items carry `keyword_info` / `keyword_properties` / `search_intent_info`
    at the top level; `related_keywords` nests the same under `keyword_data`. `_dig` reads both.
  - A missing value is null and stays None, never 0. CPC is in dollars.

Tests never reach the live API: pass an `httpx.MockTransport`.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = "https://api.dataforseo.com/v3/"
LOGIN_ENVS = ("DATAFORSEO_LOGIN", "DataForTopicClusttering_LOGIN")
PASSWORD_ENVS = ("DATAFORSEO_PASSWORD", "DataForTopicClusttering_PASSWORD")

#: The two endpoints the secondary-keyword card uses: phrase matches, then semantic neighbours.
SUGGESTIONS = "dataforseo_labs/google/keyword_suggestions/live"
RELATED = "dataforseo_labs/google/related_keywords/live"
#: What a page or domain ranks for, with its position and URL (the blog keyword card).
RANKED = "dataforseo_labs/google/ranked_keywords/live"
#: Google's organic results for a keyword: who the competitors for a topic actually are.
SERP = "serp/google/organic/live/regular"
OVERVIEW = "dataforseo_labs/google/keyword_overview/live"
IDEAS = "dataforseo_labs/google/keyword_ideas/live"
#: Seeds sent in one `keyword_ideas` request (`DataForSEOClient.ideas`).
_IDEAS_SEEDS = 20
#: Distinct URLs a domain ranks with. `total_count` is the page count even at `limit: 1`.
RELEVANT_PAGES = "dataforseo_labs/google/relevant_pages/live"

#: The match classes `keywords.py` expands each seed through, and the DataForSEO Labs endpoint for
#: each. `phrase`: terms containing the seed; `related`: Google's "searches related to"; `broad`:
#: keyword ideas around the seed's topic, which is where the buyer long tail lives.
MATCH_CLASS_ENDPOINTS: dict[str, str] = {"phrase": SUGGESTIONS, "related": RELATED, "broad": IDEAS}
#: Keywords per overview request; the endpoint accepts 700.
_OVERVIEW_CHUNK = 500

#: Countries this pipeline accepts, by the `location_name` DataForSEO takes. `keywords.match_location`
#: matches the operator's free-text region against these names.
COUNTRIES: tuple[str, ...] = (
    "Andorra", "United Arab Emirates", "Afghanistan", "Antigua and Barbuda", "Anguilla", "Albania",
    "Armenia", "Angola", "Argentina", "American Samoa", "Austria", "Australia", "Aruba", "Azerbaijan",
    "Bosnia and Herzegovina", "Barbados", "Bangladesh", "Belgium", "Burkina Faso", "Bulgaria",
    "Bahrain", "Burundi", "Benin", "Brunei", "Bolivia", "Brazil", "Bahamas", "Bhutan", "Botswana",
    "Belarus", "Belize", "Canada", "Democratic Republic of the Congo", "Central African Republic",
    "Republic of the Congo", "Switzerland", "Cote d'Ivoire", "Cook Islands", "Chile", "Cameroon",
    "China", "Colombia", "Costa Rica", "Cuba", "Cape Verde", "Cyprus", "Czechia", "Germany",
    "Djibouti", "Denmark", "Dominica", "Dominican Republic", "Algeria", "Ecuador", "Estonia", "Egypt",
    "Spain", "Ethiopia", "Finland", "Fiji", "Micronesia", "Faroe Islands", "France", "Gabon",
    "United Kingdom", "Grenada", "Georgia", "French Guiana", "Guernsey", "Ghana", "Gibraltar",
    "Greenland", "Gambia", "Guinea", "Guadeloupe", "Equatorial Guinea", "Greece", "Guatemala", "Guam",
    "Guyana", "Hong Kong", "Honduras", "Croatia", "Haiti", "Hungary", "Indonesia", "Ireland", "Israel",
    "Isle of Man", "India", "Iraq", "Iceland", "Italy", "Jersey", "Jamaica", "Jordan", "Japan",
    "Kenya", "Kyrgyzstan", "Cambodia", "Kiribati", "Saint Kitts and Nevis", "South Korea", "Kuwait",
    "Cayman Islands", "Kazakhstan", "Laos", "Lebanon", "Saint Lucia", "Liechtenstein", "Sri Lanka",
    "Lesotho", "Lithuania", "Luxembourg", "Latvia", "Libya", "Morocco", "Monaco", "Moldova",
    "Montenegro", "Madagascar", "North Macedonia", "Mali", "Myanmar", "Mongolia", "Martinique",
    "Mauritania", "Montserrat", "Malta", "Mauritius", "Maldives", "Malawi", "Mexico", "Malaysia",
    "Mozambique", "Namibia", "New Caledonia", "Niger", "Nigeria", "Nicaragua", "Netherlands", "Norway",
    "Nepal", "Nauru", "Niue", "New Zealand", "Oman", "Panama", "Peru", "French Polynesia",
    "Papua New Guinea", "Philippines", "Pakistan", "Poland", "Pitcairn Islands", "Puerto Rico",
    "Palestine", "Portugal", "Paraguay", "Qatar", "Reunion", "Romania", "Serbia", "Russia", "Rwanda",
    "Saudi Arabia", "Solomon Islands", "Seychelles", "Sweden", "Singapore", "Saint Helena", "Slovenia",
    "Slovakia", "Sierra Leone", "San Marino", "Senegal", "Somalia", "Suriname",
    "Sao Tome and Principe", "El Salvador", "Chad", "Togo", "Thailand", "Tajikistan", "Tokelau",
    "Timor-Leste", "Turkmenistan", "Tunisia", "Tonga", "Turkiye", "Trinidad and Tobago", "Taiwan",
    "Tanzania", "Ukraine", "Uganda", "United States", "Uruguay", "Uzbekistan",
    "Saint Vincent and the Grenadines", "Venezuela", "British Virgin Islands", "U.S. Virgin Islands",
    "Vietnam", "Vanuatu", "Samoa", "Yemen", "Mayotte", "South Africa", "Zambia", "Zimbabwe",
)


class DataForSEOError(RuntimeError):
    """One actionable sentence about what went wrong with DataForSEO."""


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def is_configured() -> bool:
    return bool(_first_env(LOGIN_ENVS) and _first_env(PASSWORD_ENVS))


@dataclass(frozen=True)
class DfsKeyword:
    keyword: str
    volume: int | None
    difficulty: int | None
    cpc: float | None = None
    intent: str | None = None
    #: Search momentum: the % change in searches against the previous quarter, else the previous
    #: year, else computed from `monthly_searches` (`_trend`). None when DataForSEO gave neither.
    trend: int | None = None


def _dig(item: dict, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        node: Any = item
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            return node
    return None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _trend(info: Any) -> int | None:
    """% change in searches, from `keyword_info`. DataForSEO's own `search_volume_trend` first
    (quarterly, then yearly: a monthly swing is mostly seasonality). Without it, the last three
    months of `monthly_searches` against the three before. None, never 0, when neither is there."""
    if not isinstance(info, dict):
        return None
    trend = info.get("search_volume_trend")
    if isinstance(trend, dict):
        for key in ("quarterly", "yearly"):
            value = _int(trend.get(key))
            if value is not None:
                return value
    months = info.get("monthly_searches")
    if isinstance(months, list):
        rows = sorted(
            (
                (int(m["year"]), int(m["month"]), _int(m.get("search_volume")))
                for m in months
                if isinstance(m, dict) and m.get("year") and m.get("month")
            ),
            reverse=True,
        )
        volumes = [v for _, _, v in rows if v is not None]
        if len(volumes) >= 6:
            recent, before = sum(volumes[:3]), sum(volumes[3:6])
            if before > 0:
                return round((recent - before) / before * 100)
    return None


def _keyword(item: dict) -> DfsKeyword | None:
    text = _dig(item, ("keyword",), ("keyword_data", "keyword"))
    if not isinstance(text, str) or not text.strip():
        return None
    return DfsKeyword(
        keyword=text.strip(),
        volume=_int(_dig(item, ("keyword_info", "search_volume"), ("keyword_data", "keyword_info", "search_volume"))),
        difficulty=_int(
            _dig(item, ("keyword_properties", "keyword_difficulty"), ("keyword_data", "keyword_properties", "keyword_difficulty"))
        ),
        cpc=_float(_dig(item, ("keyword_info", "cpc"), ("keyword_data", "keyword_info", "cpc"))),
        intent=_dig(item, ("search_intent_info", "main_intent"), ("keyword_data", "search_intent_info", "main_intent")),
        trend=_trend(_dig(item, ("keyword_info",), ("keyword_data", "keyword_info"))),
    )


@dataclass(frozen=True)
class RankedKeyword:
    """One keyword a target ranks for. Shape verified live: `keyword_data` holds the keyword and its
    metrics, `ranked_serp_element.serp_item` the ranking URL, its position and estimated traffic."""

    keyword: str
    volume: int | None
    difficulty: int | None
    position: int | None
    url: str
    cpc: float | None = None
    intent: str | None = None
    traffic: float | None = None
    trend: int | None = None


@dataclass(frozen=True)
class SerpResult:
    position: int
    domain: str
    url: str
    title: str = ""


@dataclass(frozen=True)
class RankedOverview:
    """What a domain ranks for, plus the Labs result-level totals.

    `keyword_count` is every ranking keyword DataForSEO knows (`metrics.organic.count` /
    `total_count`), not just the `limit` rows in `keywords`. `page_count` is filled by
    `ranking_page_count` (`relevant_pages` `total_count`), not this call. `traffic` is
    `metrics.organic.etv`; when that is missing it is the sum of the returned rows' etv.
    """

    keywords: tuple[RankedKeyword, ...]
    keyword_count: int | None = None
    traffic: float | None = None
    page_count: int | None = None


def _ranked(item: dict) -> RankedKeyword | None:
    base = _keyword(item)
    if base is None:
        return None
    serp_item = _dig(item, ("ranked_serp_element", "serp_item")) or {}
    return RankedKeyword(
        keyword=base.keyword,
        volume=base.volume,
        difficulty=base.difficulty,
        position=_int(serp_item.get("rank_absolute")) if isinstance(serp_item, dict) else None,
        url=str(serp_item.get("url") or "") if isinstance(serp_item, dict) else "",
        cpc=base.cpc,
        intent=base.intent,
        traffic=_float(serp_item.get("etv")) if isinstance(serp_item, dict) else None,
        trend=base.trend,
    )


def _http_failure(response: httpx.Response) -> DataForSEOError:
    status = response.status_code
    if status == 401:
        return DataForSEOError("DataForSEO rejected the login (401). Check DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.")
    if status == 402:
        return DataForSEOError("DataForSEO says the account is out of balance (402). Top it up at app.dataforseo.com.")
    if status == 429:
        return DataForSEOError("DataForSEO is rate-limiting this account (429); try again in a minute.")
    return DataForSEOError(f"DataForSEO failed ({status}): {response.text[:200].strip()}")


class DataForSEOClient:
    def __init__(
        self,
        login: str | None = None,
        password: str | None = None,
        *,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        login = login if login is not None else _first_env(LOGIN_ENVS)
        password = password if password is not None else _first_env(PASSWORD_ENVS)
        if not (login and password):
            raise DataForSEOError("DataForSEO is not configured: set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD in the backend .env.")
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _result(self, endpoint: str, task: dict[str, Any]) -> dict[str, Any]:
        """The first task's first result object. Empty dict when DataForSEO returned no result row."""
        response = await self._http.post(endpoint, json=[task])
        if response.status_code >= 400:
            raise _http_failure(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise DataForSEOError("DataForSEO returned a body that is not JSON.") from exc
        tasks = body.get("tasks") if isinstance(body, dict) else None
        if not tasks:
            raise DataForSEOError(f"DataForSEO returned no task: {str(body)[:200]}")
        first = tasks[0]
        code = first.get("status_code")
        if code != 20000:
            if code == 40200:
                raise DataForSEOError("DataForSEO says the account is out of balance. Top it up at app.dataforseo.com.")
            raise DataForSEOError(f"DataForSEO {endpoint.split('/')[-2]} failed: {code} {first.get('status_message')}")
        results = first.get("result") or []
        if not results or not isinstance(results[0], dict):
            return {}
        return results[0]

    async def _post(self, endpoint: str, task: dict[str, Any]) -> list[dict]:
        result = await self._result(endpoint, task)
        return [i for i in (result.get("items") or []) if isinstance(i, dict)]

    async def keywords(self, endpoint: str, seed: str, location_name: str, *, limit: int = 30, language: str = "English") -> list[DfsKeyword]:
        task: dict[str, Any] = {"keyword": seed, "location_name": location_name, "language_name": language, "limit": limit}
        if endpoint == SUGGESTIONS:
            task["order_by"] = ["keyword_info.search_volume,desc"]
        else:
            # One hop of "searches related to": depth 2+ multiplies rows (and cost) and drifts.
            task["depth"] = 1
        return [k for k in (_keyword(i) for i in await self._post(endpoint, task)) if k is not None]

    async def overview(self, keywords: list[str], location_name: str, *, language: str = "English") -> dict[str, DfsKeyword]:
        """Metrics for exactly these keywords, keyed by lowercased keyword. One request per
        `_OVERVIEW_CHUNK` keywords."""
        out: dict[str, DfsKeyword] = {}
        unique = list(dict.fromkeys(k.strip() for k in keywords if k and k.strip()))
        for start in range(0, len(unique), _OVERVIEW_CHUNK):
            task = {"keywords": unique[start : start + _OVERVIEW_CHUNK], "location_name": location_name, "language_name": language}
            for item in await self._post(OVERVIEW, task):
                row = _keyword(item)
                if row is not None:
                    out[row.keyword.lower()] = row
        return out

    async def expand(self, seed: str, match_class: str, location_name: str, limit: int, *, language: str = "English") -> list[DfsKeyword]:
        """The keywords one match class finds around `seed`, highest volume first."""
        endpoint = MATCH_CLASS_ENDPOINTS[match_class]
        if endpoint == IDEAS:
            task: dict[str, Any] = {
                "keywords": [seed],
                "location_name": location_name,
                "language_name": language,
                "limit": limit,
                "order_by": ["keyword_info.search_volume,desc"],
            }
            return [k for k in (_keyword(i) for i in await self._post(IDEAS, task)) if k is not None]
        return await self.keywords(endpoint, seed, location_name, limit=limit, language=language)

    async def ideas(self, seeds: list[str], location_name: str, *, limit: int = 50, language: str = "English") -> list[DfsKeyword]:
        """`keyword_ideas` for several seeds in one request: searches in the same topic as the seeds,
        not only ones containing a seed's exact words. Highest volume first."""
        unique = list(dict.fromkeys(s.strip() for s in seeds if s and s.strip()))[:_IDEAS_SEEDS]
        if not unique:
            return []
        task: dict[str, Any] = {
            "keywords": unique,
            "location_name": location_name,
            "language_name": language,
            "limit": limit,
            "order_by": ["keyword_info.search_volume,desc"],
        }
        return [k for k in (_keyword(i) for i in await self._post(IDEAS, task)) if k is not None]

    async def ranked_keywords(
        self, target: str, location_name: str, *, limit: int = 60, language: str = "English"
    ) -> list[RankedKeyword]:
        """What `target` ranks for, highest estimated traffic first. A page URL (with its scheme) is
        read as that page; a bare domain as the whole site."""
        return list((await self.ranked_overview(target, location_name, limit=limit, language=language)).keywords)

    async def ranked_overview(
        self, target: str, location_name: str, *, limit: int = 60, language: str = "English"
    ) -> RankedOverview:
        """`ranked_keywords` plus the result-level totals (`metrics.organic.count` / `etv`)."""
        task: dict[str, Any] = {
            "target": target,
            "location_name": location_name,
            "language_name": language,
            "limit": limit,
            "order_by": ["ranked_serp_element.serp_item.etv,desc"],
        }
        result = await self._result(RANKED, task)
        keywords = tuple(
            k
            for k in (_ranked(i) for i in (result.get("items") or []) if isinstance(i, dict))
            if k is not None
        )
        organic = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        organic = organic.get("organic") if isinstance(organic, dict) else None
        organic = organic if isinstance(organic, dict) else {}
        traffic = _float(organic.get("etv"))
        if traffic is None:
            etvs = [k.traffic for k in keywords if k.traffic is not None]
            traffic = round(sum(etvs), 1) if etvs else (0.0 if not keywords else None)
        count = organic.get("count")
        if count is None:
            count = result.get("total_count")
        return RankedOverview(keywords=keywords, keyword_count=_int(count), traffic=traffic)

    async def ranking_page_count(
        self, target: str, location_name: str, *, language: str = "English"
    ) -> int | None:
        """How many distinct URLs `target` ranks with. `relevant_pages` `total_count` at `limit: 1`,
        so the call pays for the total rather than the pages. None when the endpoint returns no row.
        A page URL is reduced to its host — this endpoint is domain-level."""
        host = target.strip()
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0].removeprefix("www.")
        if not host:
            return None
        result = await self._result(
            RELEVANT_PAGES,
            {"target": host, "location_name": location_name, "language_name": language, "limit": 1},
        )
        return _int(result.get("total_count"))

    async def serp(self, keyword: str, location_name: str, *, depth: int = 10, language: str = "English") -> list[SerpResult]:
        """Google's organic results for `keyword`, in rank order."""
        task = {"keyword": keyword, "location_name": location_name, "language_name": language, "depth": depth}
        out: list[SerpResult] = []
        for item in await self._post(SERP, task):
            if item.get("type") != "organic" or not item.get("url"):
                continue
            out.append(
                SerpResult(
                    position=_int(item.get("rank_absolute")) or len(out) + 1,
                    domain=str(item.get("domain") or ""),
                    url=str(item["url"]),
                    title=str(item.get("title") or ""),
                )
            )
        return out
