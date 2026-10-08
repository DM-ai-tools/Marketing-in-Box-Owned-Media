"""The one place this backend talks to Context.dev.

Why a gateway module
--------------------
Context.dev is a single key over a wide surface — scraping, web search, brand intelligence,
structured extraction — and three different stages of this pipeline want three different pieces of
it. Scattering `AsyncContextDev()` construction across `scraper.py`, the competitor service and the
design-token service would mean three places that each have to remember the credit cost, the
cache-age rule, and the fact that a missing key is a configuration problem rather than a page
problem. So every call goes through here, and the callers get plain dataclasses back instead of SDK
response models.

What the app uses it for
------------------------
* **`scrape_markdown`** — `GET /web/scrape/markdown`, 1 credit. Reader #2 behind
  `app/services/scraper.py`: the CRO stage needs the *whole* live page (accordions, FAQ bodies,
  button labels) and a plain server-side GET loses that on any site that renders in the browser or
  whose WAF refuses non-browser clients. This endpoint egresses from Context.dev's own fleet and
  returns rendered, LLM-ready Markdown, which is precisely the shape the audit prompt wants.
* **`search`** — `POST /web/search`, 1 credit per 10 results. Grounded source discovery for the
  competitor and ICP stages: ranked results with the page copy already in Markdown, so a
  competitor analysis can quote a live page instead of a model's recollection of one.
* **`retrieve_brand`** — `POST /brand/retrieve`, 10 credits. Resolves a domain to the brand record
  (title, description, slogan, colors, logos, socials, industry classification). The ICP and
  design-token stages both currently reconstruct parts of this from raw HTML.
* **`extract_styleguide`** — `GET /web/styleguide`, 10 credits. The full design system for a URL:
  colors, the h1-h4/p type scale, a spacing scale, shadows, and button/card component styles. This
  is the backbone of the per-run `DESIGN.md` (see `app/services/design_md.py`).
* **`extract_fonts`** — `GET /web/fonts`, 5 credits. Which typefaces the page actually uses and how
  much of it each one sets, plus the webfont files to link. The styleguide names a family per
  heading level; this says which family is *the* body face.
* **`screenshot`** — `GET /web/screenshot`, 1 credit. What the page looks like. Fed to the CRO
  rewrite as an image alongside the tokens, because a token sheet describes a palette but not a
  layout — section order, hero composition and how much air sits between bands are things a model
  can only match by seeing them.
* **`extract_structured`** — `POST /web/extract`, 10 credits. A URL plus this project's own JSON
  Schema, for the stages that want fields rather than prose.

Credits, caching, and retries
-----------------------------
Every call costs money, so: nothing here loops over URLs (use `POST /batch/submit` past a few
hundred), and the default is Context.dev's cached result. `max_age_ms` is passed only when a caller
explicitly asks for fresh data — `FRESH_*` below name the two ages this app actually wants.

Retries are the SDK's: it honours `Retry-After` on 429, backs off exponentially with jitter on
408/409/5xx, and does not retry validation errors. `_MAX_RETRIES` sets the bound.

Testing
-------
Tests must not reach the live API. Patch the functions in this module (they are the seam that
exists for it) — never `_client()`. See `tests/test_context_dev.py`.

Docs
----
* Quickstart          https://docs.context.dev/quickstart
* Scrape Markdown     https://docs.context.dev/api-reference/web-scraping/markdown
* Web Search          https://docs.context.dev/api-reference/web-scraping/search
* Retrieve Brand      https://docs.context.dev/guides/get-brand-data
* Retrieve Styleguide https://docs.context.dev/api-reference/brand-intelligence/styleguide
* Extract Fonts       https://docs.context.dev/api-reference/brand-intelligence/fonts
* Screenshot          https://docs.context.dev/api-reference/web-scraping/screenshot
* Extract             https://docs.context.dev/api-reference/web-extraction/extract
* Troubleshooting     https://docs.context.dev/optimization/troubleshooting
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from context.dev import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncContextDev,
    AuthenticationError,
    ContextDevError as _SDKError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

API_KEY_ENV = "CONTEXT_DEV_API_KEY"

# Bound on the SDK's own retry loop. Two extra attempts is enough to clear a cold-cache 408 or a
# brief 429 without turning one stage into a minute of waiting for an operator watching a chat.
_MAX_RETRIES = 2

# Generous, because a cold scrape of a heavy marketing page genuinely takes this long; the pipeline
# call sites are already async and the operator sees a pending card.
_TIMEOUT_SECONDS = 60.0

# The two freshness levels this app has an opinion about. Omit `max_age_ms` entirely (the default)
# to accept whatever Context.dev has cached — right for almost everything here, since a marketing
# page audited today reads the same as one audited yesterday.
FRESH_TODAY_MS = 24 * 60 * 60 * 1000
FRESH_NOW_MS = 5 * 60 * 1000


class ContextDevError(Exception):
    """A Context.dev call did not produce a usable answer.

    The message is written for whoever is running the pipeline, not for a stack trace: it says what
    could not be read and, where the API told us, why.
    """


class ContextDevNotConfigured(ContextDevError):
    """`CONTEXT_DEV_API_KEY` is not set. A deployment problem, never a bad URL — callers that have
    a non-Context.dev path (the scraper does) should treat this as "skip me", not as a failure."""


def is_configured() -> bool:
    """True when a key is present. Cheap enough to call per request; no client is built."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


@lru_cache(maxsize=1)
def _client() -> AsyncContextDev:
    """The shared async client. Cached so the underlying httpx connection pool is reused across
    requests — building one per call would re-open TLS for every scrape.

    The key is read from the environment by the SDK itself; it is never passed in from a request,
    stored on a model, or logged.
    """
    if not is_configured():
        raise ContextDevNotConfigured(
            f"{API_KEY_ENV} is not set, so Context.dev is unavailable. "
            "Add it to the backend .env (rotate at https://www.context.dev/dashboard/api-keys)."
        )
    return AsyncContextDev(max_retries=_MAX_RETRIES, timeout=_TIMEOUT_SECONDS)


def reset_client() -> None:
    """Drop the cached client. For tests and for a key rotated without a restart."""
    _client.cache_clear()


# ----------------------------------------------------------------------------------------------
# Error translation
# ----------------------------------------------------------------------------------------------


def _fail(what: str, exc: Exception) -> ContextDevError:
    """Turn an SDK exception into one actionable sentence.

    The SDK's own repr names an HTTP status and a request id, which tells an operator nothing. What
    they can act on is the distinction between "your key is wrong" (fix the deployment), "the site
    would not answer" (paste the copy instead), and "we ran out of retries" (try again).
    """
    if isinstance(exc, AuthenticationError) and _is_out_of_credits(exc):
        detail = f"Context.dev is out of credits — top up at {_SUBSCRIPTION_URL}."
    elif isinstance(exc, AuthenticationError):
        detail = f"Context.dev rejected the API key. Check {API_KEY_ENV}."
    elif isinstance(exc, RateLimitError):
        # Reached only after the SDK has already honoured Retry-After for `_MAX_RETRIES` attempts.
        detail = "Context.dev is rate-limiting this key; the retries were used up. Try again shortly."
    elif isinstance(exc, APITimeoutError):
        detail = f"Context.dev did not answer within {int(_TIMEOUT_SECONDS)}s."
    elif isinstance(exc, APIConnectionError):
        detail = "Could not reach Context.dev from this server."
    elif isinstance(exc, APIStatusError):
        detail = f"Context.dev answered HTTP {exc.status_code}: {_status_body(exc)}"
    else:
        detail = str(exc) or exc.__class__.__name__
    logger.error("Context.dev API error operation=%s detail=%s", what, detail)
    return ContextDevError(f"{what}: {detail}")


_SUBSCRIPTION_URL = "https://www.context.dev/dashboard/subscription"


def _is_out_of_credits(exc: APIStatusError) -> bool:
    """An exhausted balance arrives as a 401, the same status as a bad key: `{"error_code":
    "USAGE_EXCEEDED", "message": "Insufficient credits: ..."}`. Reported as a rejected key, it sent
    an operator to rotate a key that was fine while every Context.dev call kept failing."""
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    message = str(body.get("message") or "").lower()
    return body.get("error_code") == "USAGE_EXCEEDED" or "insufficient credits" in message


def _status_body(exc: APIStatusError) -> str:
    """The API's own explanation, when it sent one. Truncated — some 4xx bodies are a whole page."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        message = body.get("message") or body.get("error") or body.get("detail")
        if message:
            return str(message)[:300]
    return (getattr(exc, "message", "") or str(exc))[:300]


def _log_credits(operation: str, response: Any) -> None:
    """Record what the call cost. Credits are real money and the pipeline can fan out over a run,
    so the spend belongs in the same log an operator already reads for stage timings.
    """
    meta = getattr(response, "key_metadata", None)
    if meta is None:
        logger.info("Context.dev API used operation=%s credits=unreported", operation)
        return
    logger.info(
        "Context.dev %s consumed=%s remaining=%s",
        operation,
        getattr(meta, "credits_consumed", "?"),
        getattr(meta, "credits_remaining", "?"),
    )


# ----------------------------------------------------------------------------------------------
# Scrape one URL to Markdown  —  GET /web/scrape/markdown
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MarkdownPage:
    """One page, read. Shaped to slot straight into `scraper.ScrapedPage`."""

    url: str
    final_url: str
    title: str | None
    description: str | None
    markdown: str
    #: Age of the cached copy in ms, or None when the page was fetched live for this call.
    cache_age_ms: int | None = None

    @property
    def word_count(self) -> int:
        return len(self.markdown.split())


async def scrape_markdown(
    url: str,
    *,
    max_age_ms: int | None = None,
    main_content_only: bool = False,
) -> MarkdownPage:
    """Read `url` and return its copy as clean Markdown. 1 credit.

    `main_content_only=False` is the default on purpose: the CRO audit quotes nav labels, footer
    contact details and button text as evidence, and main-content extraction is exactly what drops
    those. Pass True for a blog post or an article, where boilerplate is noise.

    `max_age_ms` is omitted unless given, which accepts Context.dev's cached copy. Pass
    `FRESH_TODAY_MS` when the stage is auditing a page the client just changed.
    """
    options: dict[str, Any] = {
        "url": url,
        "use_main_content_only": main_content_only,
        # Links are kept: the audit's internal-linking check reads them, and they are what makes
        # the Markdown navigable back to the live page.
        "include_links": True,
        # Images are not. Their alt text is already in the Markdown, and inline base64 data URIs
        # would blow the token budget of every downstream prompt for no analytical gain.
        "include_images": False,
    }
    if max_age_ms is not None:
        options["max_age_ms"] = max_age_ms

    try:
        response = await _client().web.web_scrape_md(**options)
    except _SDKError as exc:
        raise _fail(f"Could not read {url} through Context.dev", exc) from exc

    _log_credits("scrape_markdown", response)

    markdown = (response.markdown or "").strip()
    if not markdown:
        # A 200 with an empty body is a real outcome (a login wall, a pure-JS shell that never
        # settled). Named as such so the caller can fall through to another reader.
        raise ContextDevError(f"Context.dev read {url} but the page had no text content.")

    meta = response.metadata
    cache = getattr(response, "cache_metadata", None)
    return MarkdownPage(
        url=url,
        final_url=(getattr(meta, "final_url", None) or getattr(meta, "source_url", None) or url),
        title=getattr(meta, "title", None),
        description=getattr(meta, "description", None),
        markdown=markdown,
        cache_age_ms=getattr(cache, "age_ms", None),
    )


# ----------------------------------------------------------------------------------------------
# Raw HTML  —  GET /web/scrape/html
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HTMLPage:
    """One page's rendered markup."""

    url: str
    final_url: str
    title: str | None
    html: str
    cache_age_ms: int | None = None

    @property
    def byte_size(self) -> int:
        return len(self.html)


async def scrape_html(url: str, *, max_age_ms: int | None = None) -> HTMLPage:
    """Read `url` and return its **rendered** HTML. 1 credit.

    The reason this exists alongside `scrape_markdown` is `page_replica.py`, which needs the DOM
    rather than the copy: it transports the client's real markup and swaps only the words, so
    stripped text is of no use to it.

    It is the *second* reader that module tries, never the first. `scraper._fetch_html` gets the
    same document for nothing, and on a server-rendered page — which most of the pages this
    pipeline meets are, being WordPress — the two answers are identical. What this buys is the case
    the free fetch cannot serve: a React or Next.js marketing site whose served HTML is
    `<div id="root"></div>`, where the sections only exist after the bundle runs. Context.dev
    renders in a browser, so the DOM comes back populated.

    `settle_animations=True` matters more here than anywhere else in this module. A hero that
    animates in from `opacity: 0` is captured mid-transition otherwise, and the inline styles that
    freeze it there travel into the template — producing a replica whose first band is invisible.
    """
    options: dict[str, Any] = {
        "url": url,
        # False, emphatically: main-content extraction drops the header, the nav and the footer,
        # which is where the logo, the menu labels and the contact details live. Those are the
        # parts of a replica nobody forgives getting wrong.
        "use_main_content_only": False,
        "settle_animations": True,
    }
    if max_age_ms is not None:
        options["max_age_ms"] = max_age_ms

    try:
        response = await _client().web.web_scrape_html(**options)
    except _SDKError as exc:
        raise _fail(f"Could not read {url}'s HTML through Context.dev", exc) from exc

    _log_credits("scrape_html", response)

    html = response.html or ""
    if not html.strip():
        # A 200 with an empty body is a real outcome (a login wall, a shell that never settled).
        # Named so the caller can fall through rather than templating nothing.
        raise ContextDevError(f"Context.dev read {url} but returned no HTML.")

    meta = response.metadata
    cache = getattr(response, "cache_metadata", None)
    return HTMLPage(
        url=url,
        final_url=(getattr(meta, "final_url", None) or getattr(meta, "source_url", None) or url),
        title=getattr(meta, "title", None),
        html=html,
        cache_age_ms=getattr(cache, "age_ms", None),
    )


# ----------------------------------------------------------------------------------------------
# Web search  —  POST /web/search
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchResult:
    title: str | None
    url: str
    description: str | None
    #: The result page already converted to Markdown, when Context.dev returned it. This is the
    #: reason to use this endpoint over a plain SERP: a competitor claim can cite real copy.
    markdown: str | None = None
    relevance: float | None = None


async def search(
    query: str,
    *,
    num_results: int = 10,
    country: str | None = None,
    freshness: str | None = None,
    include_domains: tuple[str, ...] = (),
    exclude_domains: tuple[str, ...] = (),
) -> list[SearchResult]:
    """Search the web and return ranked, LLM-ready results. 1 credit per 10 results.

    `num_results` is the credit dial — leave it at 10 unless a stage genuinely needs more.
    `country` is a two-letter code ("au", "in"); this app's runs are location-specific, so passing
    it matters more here than it would elsewhere. `freshness` is one of `last_24_hours`,
    `last_week`, `last_month`, `last_year`.
    """
    options: dict[str, Any] = {"query": query, "num_results": num_results}
    if country:
        options["country"] = country
    if freshness:
        options["freshness"] = freshness
    if include_domains:
        options["include_domains"] = list(include_domains)
    if exclude_domains:
        options["exclude_domains"] = list(exclude_domains)

    try:
        response = await _client().web.search(**options)
    except _SDKError as exc:
        raise _fail(f"Web search for {query!r} failed", exc) from exc

    _log_credits("search", response)

    results: list[SearchResult] = []
    for item in response.results or []:
        markdown = getattr(item, "markdown", None)
        results.append(
            SearchResult(
                title=getattr(item, "title", None),
                url=getattr(item, "url", "") or "",
                description=getattr(item, "description", None),
                markdown=getattr(markdown, "markdown", None) if markdown else None,
                relevance=getattr(item, "relevance", None),
            )
        )
    return results


# ----------------------------------------------------------------------------------------------
# Brand intelligence  —  POST /brand/retrieve
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BrandRecord:
    """The parts of the brand record this pipeline's stages actually read.

    Deliberately not the whole payload: the ICP stage wants positioning language, the design-token
    stage wants colors and a logo, and neither should have to learn the SDK's model tree. The raw
    response is kept on `raw` for anything that needs a field this dataclass has not earned yet.
    """

    domain: str
    title: str | None
    description: str | None
    slogan: str | None
    #: Hex strings, brand-primary first, as Context.dev ranks them.
    colors: tuple[str, ...] = ()
    #: (type, url) — type is "logo" or "icon".
    logos: tuple[tuple[str, str], ...] = ()
    #: (platform, url) — "linkedin", "instagram", "x", ...
    socials: tuple[tuple[str, str], ...] = ()
    #: "Industry / Subindustry" pairs from the EIC classification.
    industries: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


async def retrieve_brand(domain: str) -> BrandRecord:
    """Resolve a domain to its brand record. 10 credits.

    Used at the top of a run: one call turns "the client's website" into the positioning line,
    palette, logo and industry classification that several later stages would otherwise each go
    scrape for themselves.
    """
    try:
        response = await _client().brand.retrieve(type="by_domain", domain=domain)
    except _SDKError as exc:
        raise _fail(f"Could not retrieve the brand record for {domain}", exc) from exc

    _log_credits("retrieve_brand", response)

    brand = getattr(response, "brand", None)
    if brand is None:
        raise ContextDevError(f"Context.dev has no brand record for {domain}.")

    raw = brand.model_dump() if hasattr(brand, "model_dump") else dict(brand)

    colors = tuple(c["hex"] for c in (raw.get("colors") or []) if isinstance(c, dict) and c.get("hex"))
    logos = tuple(
        (str(logo.get("type") or "logo"), str(logo["url"]))
        for logo in (raw.get("logos") or [])
        if isinstance(logo, dict) and logo.get("url")
    )
    socials = tuple(
        (str(s.get("type") or "link"), str(s["url"]))
        for s in (raw.get("socials") or [])
        if isinstance(s, dict) and s.get("url")
    )
    industries = tuple(
        " / ".join(part for part in (pair.get("industry"), pair.get("subindustry")) if part)
        for pair in ((raw.get("industries") or {}).get("eic") or [])
        if isinstance(pair, dict)
    )

    return BrandRecord(
        domain=str(raw.get("domain") or domain),
        title=raw.get("title"),
        description=raw.get("description"),
        slogan=raw.get("slogan"),
        colors=colors,
        logos=logos,
        socials=socials,
        industries=industries,
        raw=raw,
    )


# ----------------------------------------------------------------------------------------------
# Styleguide  —  GET /web/styleguide
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Styleguide:
    """One page's design system as Context.dev computes it from the rendered page.

    Computed styles, not parsed CSS — which is why this is worth 10 credits next to the free CSS
    parse in `design_tokens.py`. A theme that sets its heading font four stylesheets deep, or behind
    a `var()` chain, or only inside a media query, resolves here and does not there.

    The nested blocks stay as plain dicts rather than being mirrored into more dataclasses. They are
    already flat string maps keyed exactly as the DESIGN.md token schema wants (`xs`/`sm`/`md`/`lg`/
    `xl`, `h1`..`h4`, `primary`/`secondary`/`link`), so re-typing them would add a translation layer
    whose only job is renaming keys back to what they already are.
    """

    source_url: str
    #: "light" or "dark" — the page's own primary color mode.
    mode: str | None
    #: accent / background / text, as hex.
    colors: dict[str, str]
    #: "h1".."h4" and "p" -> {fontFamily, fontFallbacks, fontSize, fontWeight, lineHeight,
    #: letterSpacing}. Keys are camelCase, as the API returns them.
    typography: dict[str, dict[str, Any]]
    #: xs / sm / md / lg / xl -> a CSS length.
    spacing: dict[str, str]
    #: sm / md / lg / xl / inner -> a CSS box-shadow value.
    shadows: dict[str, str]
    #: "button-primary" / "button-secondary" / "button-link" / "card" -> computed properties.
    #: Flattened from the API's nested `components.button.primary`, so every component is one key —
    #: which is the shape DESIGN.md's `components:` block uses.
    components: dict[str, dict[str, Any]]
    #: Family name -> {type, files: {weight: url}, category, displayName}.
    font_links: dict[str, dict[str, Any]]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def available(self) -> bool:
        return bool(self.colors or self.typography)


def _as_dict(value: Any) -> dict[str, Any]:
    """SDK models, plain dicts and None all reduce to a dict here.

    The styleguide payload nests four levels deep and any level can be absent on a page that
    declares little, so every descent goes through this rather than through a chain of
    `getattr(..., None) or {}`. `by_alias=True` keeps the API's own camelCase keys, which is what
    the callers below index by.
    """
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True, by_alias=True)
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if v is not None}
    return {}


async def extract_styleguide(url: str, *, color_scheme: str = "light") -> Styleguide:
    """Pull a page's design system — colors, type scale, spacing, shadows, buttons. 10 credits.

    `color_scheme` is the mode to render in, not a filter on the answer: a site with a dark theme
    returns different computed values under "dark". Left at "light" because that is what a client's
    marketing page is authored in, and what the generated asset has to match.
    """
    try:
        response = await _client().web.extract_styleguide(direct_url=url, color_scheme=color_scheme)
    except _SDKError as exc:
        raise _fail(f"Could not extract a styleguide from {url}", exc) from exc

    _log_credits("extract_styleguide", response)

    raw = _as_dict(response)
    guide = _as_dict(getattr(response, "styleguide", None))
    typography = _as_dict(guide.get("typography"))

    # Headings nest one level deeper than the body style; both end up as siblings here so building a
    # DESIGN.md `typography:` block is one loop rather than two special cases.
    type_scale: dict[str, dict[str, Any]] = {}
    for level, style in _as_dict(typography.get("headings")).items():
        resolved = _as_dict(style)
        if resolved:
            type_scale[level] = resolved
    body_style = _as_dict(typography.get("p"))
    if body_style:
        type_scale["p"] = body_style

    components: dict[str, dict[str, Any]] = {}
    raw_components = _as_dict(guide.get("components"))
    for variant, style in _as_dict(raw_components.get("button")).items():
        resolved = _as_dict(style)
        if resolved:
            components[f"button-{variant}"] = resolved
    card = _as_dict(raw_components.get("card"))
    if card:
        components["card"] = card

    return Styleguide(
        source_url=url,
        mode=guide.get("mode"),
        colors={k: v for k, v in _as_dict(guide.get("colors")).items() if isinstance(v, str)},
        typography=type_scale,
        spacing={k: v for k, v in _as_dict(guide.get("elementSpacing")).items() if isinstance(v, str)},
        shadows={k: v for k, v in _as_dict(guide.get("shadows")).items() if isinstance(v, str)},
        components=components,
        font_links={k: _as_dict(v) for k, v in _as_dict(guide.get("fontLinks")).items()},
        raw=raw,
    )


# ----------------------------------------------------------------------------------------------
# Fonts  —  GET /web/fonts
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FontUsage:
    """One typeface and how much of the page it actually sets.

    `percent_words` is the field that matters. The styleguide names a family per heading level, but
    a page can name six families and set 94% of its words in one; that one is the body face, and it
    is the one a generated asset has to get right.
    """

    family: str
    fallbacks: tuple[str, ...] = ()
    percent_words: float | None = None
    percent_elements: float | None = None
    #: Where it is used ("headings", "body", ...) when the API says.
    uses: tuple[str, ...] = ()

    @property
    def stack(self) -> str:
        """The family plus its fallbacks, as a CSS font-family value."""
        parts = [self.family, *self.fallbacks]
        return ", ".join(dict.fromkeys(part for part in parts if part))


@dataclass(frozen=True)
class Fonts:
    source_url: str
    #: Ordered by share of the page's words, heaviest first.
    fonts: tuple[FontUsage, ...] = ()
    #: Family name -> {type, files: {weight: url}, category, displayName}.
    font_links: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def body_face(self) -> FontUsage | None:
        return self.fonts[0] if self.fonts else None


async def extract_fonts(url: str) -> Fonts:
    """Identify the typefaces a page uses, ranked by how much of it they set. 5 credits."""
    try:
        response = await _client().web.extract_fonts(direct_url=url)
    except _SDKError as exc:
        raise _fail(f"Could not read the fonts on {url}", exc) from exc

    _log_credits("extract_fonts", response)

    usages: list[FontUsage] = []
    for entry in getattr(response, "fonts", None) or []:
        data = _as_dict(entry)
        family = data.get("font")
        if not family:
            continue
        usages.append(
            FontUsage(
                family=str(family),
                fallbacks=tuple(str(f) for f in (data.get("fallbacks") or [])),
                percent_words=data.get("percent_words") or data.get("percentWords"),
                percent_elements=data.get("percent_elements") or data.get("percentElements"),
                uses=tuple(str(u) for u in (data.get("uses") or [])),
            )
        )
    usages.sort(key=lambda f: f.percent_words or 0.0, reverse=True)

    return Fonts(
        source_url=url,
        fonts=tuple(usages),
        font_links={k: _as_dict(v) for k, v in _as_dict(getattr(response, "font_links", None)).items()},
    )


# ----------------------------------------------------------------------------------------------
# Screenshot  —  GET /web/screenshot
# ----------------------------------------------------------------------------------------------

# The viewport the hero shot is taken at. A desktop width, because that is the layout the CRO
# rewrite reproduces; a mobile shot would show a different one from the one being matched.
HERO_VIEWPORT = (1440, 900)

# Anthropic rejects an image larger than this on either axis outright — not resizes it, rejects the
# request. A full-page shot of a long landing page blows straight past it (trafficradius.com.au
# measures 1937x15550), so `Screenshot.model_safe` is checked before a shot is put in a prompt. An
# oversized shot is still kept and shown to the operator; it just does not travel to the model.
MODEL_MAX_IMAGE_PX = 8000


@dataclass(frozen=True)
class Screenshot:
    """A captured image of the page.

    `image_url` is a public URL Context.dev hosts, not bytes — which is why this is cheap to store
    on a run and cheap to hand to Anthropic: an `image` block takes a URL source, so the file never
    passes through this backend or its database.
    """

    source_url: str
    image_url: str
    #: "fullPage" or "viewport".
    kind: str
    width: int | None = None
    height: int | None = None
    #: What this shot is for, in the words the prompt uses. Set by the caller.
    label: str = ""

    @property
    def model_safe(self) -> bool:
        """Whether this image can be sent to Anthropic at all.

        Unknown dimensions count as safe: the API is then the thing that decides, which is better
        than silently dropping a usable shot because the response omitted a field.
        """
        return max(self.width or 0, self.height or 0) <= MODEL_MAX_IMAGE_PX

    @property
    def aspect_note(self) -> str:
        """How the image will read once Anthropic downscales it, in one clause.

        Long-edge downscaling turns a very tall full-page shot into a narrow ribbon, which is
        genuinely useful for section order and colour rhythm and useless for reading a headline.
        The prompt says which, so the model does not try to transcribe copy off it.
        """
        if not self.width or not self.height:
            return ""
        ratio = self.height / self.width
        if ratio >= 4:
            return "very tall — read it for section order, band colours and vertical rhythm only, not for text"
        if ratio >= 2:
            return "tall — layout and section order are legible, fine text is not"
        return "close to screen proportions — detail is legible"


async def screenshot(
    url: str,
    *,
    full_page: bool = False,
    viewport: tuple[int, int] | None = None,
    label: str = "",
    max_age_ms: int | None = None,
) -> Screenshot:
    """Capture `url` as an image and return its public URL. 1 credit.

    Cookie banners are dismissed, because an un-dismissed consent overlay is frequently the only
    thing visible in an above-the-fold shot — and a rewrite built from that image reproduces the
    banner instead of the hero.
    """
    options: dict[str, Any] = {
        "direct_url": url,
        "handle_cookie_popup": True,
        "clear_popups": True,
    }
    if full_page:
        # The API takes this as the string "true"/"false", not a bool.
        options["full_screenshot"] = "true"
    if viewport is not None:
        options["viewport"] = {"width": viewport[0], "height": viewport[1]}
    if max_age_ms is not None:
        options["max_age_ms"] = max_age_ms

    try:
        response = await _client().web.screenshot(**options)
    except _SDKError as exc:
        raise _fail(f"Could not screenshot {url}", exc) from exc

    _log_credits("screenshot", response)

    image_url = getattr(response, "screenshot", None)
    if not image_url:
        raise ContextDevError(f"Context.dev returned no image for {url}.")

    return Screenshot(
        source_url=url,
        image_url=str(image_url),
        kind=str(getattr(response, "screenshot_type", None) or ("fullPage" if full_page else "viewport")),
        width=getattr(response, "width", None),
        height=getattr(response, "height", None),
        label=label,
    )


# ----------------------------------------------------------------------------------------------
# Structured extraction  —  POST /web/extract
# ----------------------------------------------------------------------------------------------


async def extract_structured(
    url: str,
    schema: dict[str, Any],
    *,
    instructions: str | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Pull fields off `url` into this project's own JSON Schema. 10 credits.

    Prefer this over "scrape then ask Claude to parse" whenever the wanted output is a fixed record
    (pricing tiers, service list, contact details): it is one call instead of two, and the schema is
    enforced by Context.dev rather than by a retry loop around a model that returned prose.

    `max_pages` follows links from `url`; leave it unset for a single page, because each extra page
    is more credits.
    """
    options: dict[str, Any] = {"url": url, "schema": schema}
    if instructions:
        options["instructions"] = instructions
    if max_pages is not None:
        options["max_pages"] = max_pages

    try:
        response = await _client().web.extract(**options)
    except _SDKError as exc:
        raise _fail(f"Could not extract structured data from {url}", exc) from exc

    _log_credits("extract_structured", response)
    return response.model_dump() if hasattr(response, "model_dump") else dict(response)
