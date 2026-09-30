"""Fetch a live web page and return its copy as readable, structure-preserving text.

Why this exists
---------------
The CRO stage's "Existing Page Content" input is the whole current page — per its own prompt file,
`[PASTE FULL COPY]`, which the audit then quotes line by line as evidence. Asking an operator to
produce that by hand is both the longest step in a run and the least reliable one: what actually
gets pasted is the visible prose, while accordion bodies, FAQ answers, button labels and footer
contact details — exactly the things Audits 1, 3 and 7 look for — are the parts people miss. The
stage already collects "Existing Page URL" one question earlier, so the page is one fetch away.

Three readers, tried in order
-----------------------------
1. **Direct** — this backend's own HTTP fetch plus the extractor below. Fast, free, and right for
   the server-rendered marketing page this stage usually points at.
2. **Context.dev** (`GET /web/scrape/markdown`, 1 credit) — used only when the direct read fails or
   comes back almost empty. That covers the two classes a plain GET can never handle: sites whose
   WAF refuses server-side clients outright (403 to every user agent — g2.com answers this backend
   with a 403), and sites that render their copy in the browser. Context.dev egresses from its own
   fleet and renders the page, so it clears both, and it returns Markdown — headings, lists and
   link text preserved — which is the same shape the extractor below produces. No headless browser
   and no Chromium dependency here. Disable with `CONTEXT_DEV_SCRAPER=0`.
   Docs: https://docs.context.dev/api-reference/web-scraping/markdown
3. **Anthropic's `web_fetch` server tool** — last, for the case where Context.dev is unconfigured
   or itself comes back thin. The page text is taken from the tool result rather than from anything
   the model writes, so nothing is paraphrased. Disable with `SCRAPER_CLAUDE_FALLBACK=0`.

Context.dev sits ahead of the Claude fetcher because it is the cheaper and more literal of the two:
one credit against a model call, and a rendered-page-to-Markdown conversion rather than a model
being asked to trigger a fetch. Both are only ever reached when the free direct read has already
failed.

`ScrapedPage.source` records which reader answered, and the UI says so. If all fail the operator is
asked to paste, with the direct read's reason shown — silent partial extraction is the one outcome
worth ruling out, because the CRO audit would then quote a page that isn't the client's.

Extraction shape
----------------
Text, not HTML — but headings keep their level as `#`/`##` markers, list items keep their bullet,
and `<button>`/submit labels are marked `[BUTTON] …`. The audit reasons about page *structure*
("Audit 5 — Architecture Gap Analysis" compares section order), so flattening everything into one
paragraph blob would throw away the input it needs. Built on `html.parser` from the stdlib rather
than adding a parsing dependency: the job is small, and its lenient handling of real-world markup
is the same reason BeautifulSoup defaults to it.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse, urlunparse

import httpx

from app.services import context_dev
from app.services.claude_client import get_client

logger = logging.getLogger(__name__)

# A page whose copy is worth auditing is far smaller than this; the cap is here so a mis-typed URL
# pointing at a media file or a database export cannot pull an unbounded body into memory.
_MAX_BYTES = 3_000_000
_TIMEOUT_SECONDS = 20.0
_MAX_REDIRECTS = 5

# Presented honestly rather than spoofing a browser: the request is made on behalf of an operator
# who is auditing a page they work on, and a site owner reading their logs should be able to tell
# what it was.
_USER_AGENT = "MarketingInABox-PageReader/1.0 (+CRO audit intake; server-side fetch)"

# The fallback identity, tried only after a site rejects the honest one. Measured across real sites,
# neither string wins outright: some CMS/WAF setups (Wordfence and friends) reject any agent they do
# not recognise, while others — Zillow, for one — serve the unknown agent and block the Chrome
# string. So both are attempted before a page is declared unreadable.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# Statuses that mean "a human with a browser would have got this page" rather than "this page is
# not here": worth a second attempt with the other agent, and worth naming as a block if both fail.
_BOT_WALL_STATUSES = frozenset({401, 403, 406, 409, 418, 429, 451, 503})

# Response headers that announce a bot challenge outright. SiteGround's SG-Captcha is the one that
# prompted this list: trafficradius.com.au answers a server-side read with `HTTP 202 Accepted`,
# `SG-Captcha: challenge`, and a 193-byte body that is nothing but a meta-refresh to
# `/.well-known/sgcaptcha/`. A 2xx with a tiny body reads as "thin page" unless something like this
# catches it — and "thin page" sends the operator off to check their own site's rendering when the
# real answer is that the request was challenged.
_CHALLENGE_HEADERS = ("sg-captcha", "cf-mitigated", "x-datadome", "x-sucuri-block")

# A body this small cannot be a page worth auditing; paired with a meta-refresh or a challenge
# header it is an interstitial, not content.
_INTERSTITIAL_MAX_BYTES = 2_048
_META_REFRESH = re.compile(r"<meta[^>]+http-equiv=[\"']?refresh", re.I)

# Below this, treat the result as "we did not really get the page" — a JS-rendered shell, a consent
# wall, or a redirect stub. Chosen against the shortest page this stage would legitimately see: a
# thin one-service landing page still runs 200+ words, and the CRO prompt's whole premise is that
# there is copy to audit.
_LOW_CONTENT_WORDS = 120

# The fallback reader's model. Sonnet rather than Opus because the model does no reasoning here — it
# triggers one tool call and the page text is taken from the tool result, not from anything it
# writes. One line to change if this should run on a different tier.
_FALLBACK_MODEL = "claude-sonnet-5"
# ~120k characters of page text, well past any marketing page, and a hard ceiling on what one read
# can pull into the context of the stage that consumes it.
_FALLBACK_MAX_CONTENT_TOKENS = 30000


def _request_headers(user_agent: str) -> dict[str, str]:
    """Headers for one attempt. `Accept-Language` is here because some WAFs treat its absence as a
    bot signal on its own — it costs nothing and removes one avoidable rejection."""
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
    }

# Everything inside these never appears on the page as copy.
_DROP_CONTENT_TAGS = frozenset(
    {"script", "style", "noscript", "template", "svg", "canvas", "head", "iframe", "object", "video", "audio"}
)

# Dropped for signal, not correctness: a site nav is a menu-link soup that would show up in the
# audit as if it were page copy. `footer` is deliberately NOT here — the CRO prompt asks about
# contact details and next-step info, which is often exactly what lives there.
_DROP_NAVIGATION_TAGS = frozenset({"nav"})

_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "aside", "main", "figure", "figcaption",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "dl", "dt", "dd", "tr", "table",
        "thead", "tbody", "blockquote", "pre", "form", "fieldset", "label", "details", "summary",
        "address", "hr",
    }
)

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# Runs of horizontal whitespace, including the two invisibles real pages are full of: a
# non-breaking space (what a CMS emits for &nbsp;) and a zero-width space.
_WS_RUN = re.compile("[ \t\xa0\u200b]+")
_BLANK_RUN = re.compile(r"\n{3,}")


class ScrapeError(Exception):
    """The page could not be fetched or was not a web page. Surfaced to the operator verbatim, so
    the message says what to do next rather than naming an exception class."""


@dataclass
class ScrapedPage:
    url: str
    final_url: str
    title: str | None
    meta_description: str | None
    text: str
    word_count: int
    truncated: bool
    # How the page was read: "direct" is this backend's own HTTP fetch, "context.dev" is
    # Context.dev's rendered scrape, "claude" is Anthropic's server-side fetcher. The last two are
    # only reached when the direct read is blocked or comes back empty.
    source: str = "direct"
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def low_content(self) -> bool:
        return self.word_count < _LOW_CONTENT_WORDS


class _PageTextExtractor(HTMLParser):
    """Collects page copy as markdown-ish lines, skipping whatever is not copy."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.meta_description: str | None = None
        self._parts: list[str] = []
        # Counted, not flagged: `<div><script>` inside a dropped subtree still has to close its own
        # tag before text counts again, and malformed markup nests these more than once.
        self._suppress_depth = 0
        self._in_title = False

    # -- helpers ----------------------------------------------------------------------
    def _emit(self, text: str) -> None:
        if text:
            self._parts.append(text)

    def _break(self) -> None:
        if self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    # -- HTMLParser hooks -------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}

        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            if attr.get("name", "").lower() == "description" and attr.get("content"):
                self.meta_description = attr["content"].strip() or None
            return

        if tag in _DROP_CONTENT_TAGS or tag in _DROP_NAVIGATION_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return

        if tag == "br":
            self._break()
            return
        if tag == "img":
            # Alt text is page copy in every sense that matters here — it is often where the only
            # description of a hero or a before/after lives.
            alt = attr.get("alt", "").strip()
            if alt:
                self._emit(f"[IMAGE] {alt}")
                self._break()
            return
        if tag == "input":
            if attr.get("type", "").lower() in {"submit", "button"} and attr.get("value"):
                self._break()
                self._emit(f"[BUTTON] {attr['value'].strip()}")
                self._break()
            return

        if tag in _HEADING_TAGS:
            self._break()
            self._emit("\n" + "#" * _HEADING_TAGS[tag] + " ")
            return
        if tag == "li":
            self._break()
            self._emit("- ")
            return
        if tag == "button":
            self._break()
            self._emit("[BUTTON] ")
            return
        if tag in _BLOCK_TAGS:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _DROP_CONTENT_TAGS or tag in _DROP_NAVIGATION_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth:
            return

        if tag in _BLOCK_TAGS or tag in _HEADING_TAGS or tag == "button":
            self._break()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            existing = self.title or ""
            self.title = (existing + data).strip() or None
            return
        if self._suppress_depth:
            return
        cleaned = _WS_RUN.sub(" ", data.replace("\r", " ").replace("\n", " "))
        if not cleaned.strip():
            # Whitespace between inline elements still separates words ("<b>Book</b> now").
            if self._parts and not self._parts[-1].endswith((" ", "\n")):
                self._parts.append(" ")
            return
        self._emit(cleaned)

    def result(self) -> str:
        raw = "".join(self._parts)
        lines = [_WS_RUN.sub(" ", line).strip() for line in raw.split("\n")]
        # Drop lines that are only a marker — an empty heading or a `<button>` with an icon and no
        # label, which would otherwise read as content that isn't there.
        kept = [ln for ln in lines if ln and ln not in {"#", "##", "###", "####", "#####", "######", "-", "[BUTTON]"}]
        # A blank line before each heading, so the extracted copy reads as sections rather than one
        # unbroken column — this text is shown back to the operator to check, not just fed to Claude.
        spaced: list[str] = []
        for line in kept:
            if line.startswith("#") and spaced:
                spaced.append("")
            spaced.append(line)
        return _BLANK_RUN.sub("\n\n", "\n".join(spaced)).strip()


def extract_readable_text(html: str) -> tuple[str, str | None, str | None]:
    """Parse `html` into (text, title, meta_description). Pure — no network, so it is the part
    covered by tests."""
    parser = _PageTextExtractor()
    parser.feed(html)
    parser.close()
    return parser.result(), parser.title, parser.meta_description


def normalize_url(raw: str) -> str:
    """Accept what an operator actually types ("brightsidedental.com.au/implants") and return an
    absolute http(s) URL, or raise `ScrapeError`."""
    candidate = (raw or "").strip()
    if not candidate:
        raise ScrapeError("No URL was provided.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlparse(candidate)
    if parts.scheme not in {"http", "https"}:
        raise ScrapeError(f"Only http and https URLs can be read, not {parts.scheme!r}.")
    if not parts.hostname:
        raise ScrapeError(f"{raw!r} is not a URL that can be opened.")
    return urlunparse(parts)


def _assert_public_host(url: str) -> None:
    """Refuse URLs that resolve to a private, loopback, or link-local address.

    This endpoint fetches a URL chosen by whoever is using the UI, from inside the network the API
    runs in — the textbook shape of an SSRF. Without this check, `http://169.254.169.254/...` or
    `http://localhost:8001/...` would be fetched by the server and the response handed back as
    "page content". Checked per redirect hop rather than once, since a public URL is free to
    redirect inward.
    """
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ScrapeError(f"Could not resolve {host!r}. Check the URL and try again.") from exc

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address.split("%")[0])  # strip any IPv6 scope id
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ScrapeError(
                f"{host!r} resolves to a non-public address ({ip}), which this reader will not fetch."
            )


def _describe_transport_error(url: str, exc: httpx.HTTPError) -> str:
    """Turn an httpx transport failure into something the operator can act on.

    httpx surfaces a TLS problem as a bare `ConnectError` whose text is the raw OpenSSL message —
    accurate and unreadable. Since a lapsed certificate on a small business site is one of the
    likelier reasons a read fails, it is worth naming rather than passing through.
    """
    detail = str(exc)
    if "certificate" in detail.lower() or "ssl" in detail.lower() or "tls" in detail.lower():
        return (
            f"{url} has an HTTPS certificate this reader could not verify (expired, self-signed, or "
            f"for a different domain). The page still opens in a browser, so paste the copy instead. "
            f"[{detail}]"
        )
    return f"Could not reach {url}: {detail}"


async def _fetch_html(url: str) -> tuple[str, str]:
    """Fetch `url`, following redirects by hand so every hop can be re-validated. Returns
    (final_url, body_text)."""
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=_TIMEOUT_SECONDS,
        headers=_request_headers(_USER_AGENT),
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _assert_public_host(current)
            try:
                response = await client.get(current)
                # Some sites refuse anything that isn't a browser; others refuse the browser strings
                # specifically. Neither is predictable from the outside, so a rejection is retried
                # once with the other identity before giving up on the page.
                if response.status_code in _BOT_WALL_STATUSES:
                    logger.info(
                        "Page read got HTTP %s for %r with the default agent; retrying as a browser",
                        response.status_code,
                        current,
                    )
                    response = await client.get(current, headers=_request_headers(_BROWSER_USER_AGENT))
            except httpx.TimeoutException as exc:
                raise ScrapeError(f"{current} did not respond within {int(_TIMEOUT_SECONDS)} seconds.") from exc
            except httpx.HTTPError as exc:
                raise ScrapeError(_describe_transport_error(current, exc)) from exc

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ScrapeError(f"{current} returned a redirect with no destination.")
                current = str(response.url.join(location))
                continue

            if response.status_code in _BOT_WALL_STATUSES:
                # Named for what it is. "returned HTTP 403" invites the operator to think the URL is
                # wrong, when the URL is fine and the site is simply refusing automated readers.
                raise ScrapeError(
                    f"{current} blocked an automated read (HTTP {response.status_code}) — it allows "
                    "browsers but not servers, which no change here can get around. Paste the copy "
                    "instead."
                )

            if response.status_code >= 400:
                raise ScrapeError(
                    f"{current} returned HTTP {response.status_code}. "
                    "If the page has moved or is behind a login, check the URL or paste the copy instead."
                )

            challenge_header = next(
                (h for h in _CHALLENGE_HEADERS if h in {k.lower() for k in response.headers}), None
            )
            body_preview = response.content[:_INTERSTITIAL_MAX_BYTES].decode("utf-8", errors="replace")
            tiny = len(response.content) <= _INTERSTITIAL_MAX_BYTES
            if challenge_header or (tiny and _META_REFRESH.search(body_preview)):
                raise ScrapeError(
                    f"{current} challenged the request instead of serving the page"
                    + (f" ({challenge_header} bot protection)" if challenge_header else " (a bot-check interstitial)")
                    + " — it allows browsers but not servers."
                )

            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and not (content_type.startswith("text/") or content_type.endswith("+xml")):
                raise ScrapeError(f"{current} is {content_type}, not a web page.")

            body = response.content[:_MAX_BYTES]
            # `response.text` would decode the *whole* body; decode the capped slice instead.
            return str(response.url), body.decode(response.encoding or "utf-8", errors="replace")

    raise ScrapeError(f"{url} redirected more than {_MAX_REDIRECTS} times.")


def _split_fetcher_front_matter(text: str) -> tuple[str, str | None, str | None]:
    """Peel Claude's fetcher front-matter off the page text.

    Its output opens with a `---`-delimited block of page metadata (`canonical:`, `title:`,
    `meta-description:`, and assorted CSRF junk). The title and description are worth keeping as
    fields; the rest is not page copy and would read as content to the CRO audit.
    """
    if not text.startswith("---"):
        return text, None, None

    lines = text.split("\n")
    end = next((i for i, line in enumerate(lines[1:61], start=1) if line.strip() == "---"), None)
    if end is None:
        return text, None, None

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        if value.strip():
            meta[key.strip().lower()] = value.strip()

    body = "\n".join(lines[end + 1 :]).strip()
    return body, meta.get("title"), meta.get("meta-description") or meta.get("description")


# Markdown the fetcher emits, reduced to the copy a reader actually sees. `![alt](url)` becomes the
# `[IMAGE] alt` marker the direct extractor already uses, and `[text](url)` keeps the link text —
# the CRO audit quotes phone numbers and CTA labels out of these, but a page of raw URLs and
# wp-content paths is noise it would have to read past.
# A character no page carries, used to hold an image's place while links are unwrapped around it.
_IMAGE_SENTINEL = ""
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _tidy_fetcher_markdown(text: str) -> str:
    # Images become a bracket-free sentinel first, links are unwrapped second, and only then does the
    # sentinel become the `[IMAGE]` marker. Emitting the marker up front breaks the link pattern on a
    # linked image (`[![alt](img)](href)`, every logo in a site header): the `]` inside `[IMAGE]`
    # terminates the link text early, and the whole construct survives as raw markdown.
    text = _MD_IMAGE.sub(lambda m: f"{_IMAGE_SENTINEL}{m.group(1).strip()}" if m.group(1).strip() else "", text)
    text = _MD_LINK.sub(lambda m: m.group(1).strip(), text)
    text = text.replace(_IMAGE_SENTINEL, "[IMAGE] ")
    lines = [_WS_RUN.sub(" ", line).strip() for line in text.split("\n")]
    # Drop the bullets and brackets the substitutions above left empty; keep blank lines, since they
    # are the paragraph breaks that make the copy readable.
    junk = {"-", "*", "[]", "[IMAGE]", "|"}
    kept = [line for line in lines if line == "" or line not in junk]
    return _BLANK_RUN.sub("\n\n", "\n".join(kept)).strip()


def _context_dev_enabled() -> bool:
    """The Context.dev reader is ON whenever a key is configured, unless `CONTEXT_DEV_SCRAPER=0`.

    Two switches rather than one: the env flag is for turning the spend off deliberately, and the
    key check is so a deployment that simply has not been given a key degrades to the old two-reader
    behaviour instead of logging an error on every blocked page.
    """
    if os.environ.get("CONTEXT_DEV_SCRAPER", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return context_dev.is_configured()


async def _fetch_via_context_dev(url: str) -> ScrapedPage:
    """Read `url` through Context.dev's Markdown scrape. Costs 1 credit, so it is only ever called
    after the free direct read has failed or come back thin.

    The Markdown comes back with headings as `#`, list items bulleted and link text inline — the
    same structure-preserving shape `extract_readable_text` produces from HTML — so the CRO audit's
    "compare section order" checks work on it unchanged. No `max_age_ms` is passed: the cached copy
    is the right default for a marketing page, and paying for a live fetch of a page that has not
    moved is waste.
    """
    try:
        page = await context_dev.scrape_markdown(url)
    except context_dev.ContextDevError as exc:
        # Re-raised as a ScrapeError so `scrape_page` has one exception type to reason about; the
        # wrapper has already turned the SDK failure into a sentence worth showing.
        raise ScrapeError(str(exc)) from exc

    text = page.markdown
    logger.info(
        "Read page via Context.dev url=%r chars=%s cache_age_ms=%s",
        url,
        len(text),
        page.cache_age_ms,
    )
    return ScrapedPage(
        url=url,
        final_url=page.final_url,
        title=page.title,
        meta_description=page.description,
        text=text,
        word_count=page.word_count,
        truncated=False,
        source="context.dev",
    )


def _claude_fallback_enabled() -> bool:
    """The Claude-fetcher fallback is ON unless explicitly disabled with `SCRAPER_CLAUDE_FALLBACK=0`."""
    return os.environ.get("SCRAPER_CLAUDE_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}


async def _fetch_via_claude(url: str) -> ScrapedPage:
    """Read `url` through Anthropic's server-side `web_fetch` tool.

    The direct fetch in `_fetch_html` fails on two whole classes of page that a plain HTTP GET can
    never handle: sites whose WAF refuses server-side clients outright (403 to every user agent), and
    sites that render their copy in the browser. Anthropic's fetcher egresses from somewhere else and
    returns rendered text, so it clears both — verified against g2.com, which answers this backend
    with a 403 and that fetcher with 24k characters of page text.
    """
    client = get_client()
    response = await client.messages.create(
        model=_FALLBACK_MODEL,
        max_tokens=1000,
        tools=[
            {
                "type": "web_fetch_20260209",
                "name": "web_fetch",
                "max_uses": 2,
                "max_content_tokens": _FALLBACK_MAX_CONTENT_TOKENS,
            }
        ],
        # The page text is taken from the tool result, not from anything the model writes: a model
        # asked to "return the page" paraphrases and abridges it, and the CRO audit quotes this text
        # back as evidence. So the model's only job is to trigger the fetch.
        messages=[{"role": "user", "content": f"Fetch {url} and reply with the single word DONE."}],
    )

    for block in response.content:
        if getattr(block, "type", None) != "web_fetch_tool_result":
            continue
        result = getattr(block, "content", None)
        # A server-tool failure arrives as HTTP 200 with an error object in place of the result.
        error_code = getattr(result, "error_code", None)
        if error_code:
            raise ScrapeError(f"Claude's page reader could not fetch {url} ({error_code}).")

        document = getattr(result, "content", None)
        source = getattr(document, "source", None)
        data = getattr(source, "data", None)
        if not isinstance(data, str) or not data.strip():
            continue

        text, title, description = _split_fetcher_front_matter(data)
        text = _tidy_fetcher_markdown(text)
        logger.info("Read page via Claude's fetcher url=%r chars=%s", url, len(text))
        return ScrapedPage(
            url=url,
            final_url=str(getattr(result, "url", "") or url),
            title=title,
            meta_description=description,
            text=text,
            word_count=len(text.split()),
            truncated=False,
            source="claude",
        )

    raise ScrapeError(f"Claude's page reader returned nothing for {url}.")


async def _read_direct(url: str) -> ScrapedPage:
    """The direct reader: this backend's own fetch, through the extractor above."""
    final_url, html = await _fetch_html(url)
    text, title, meta_description = extract_readable_text(html)
    truncated = len(html.encode("utf-8", errors="replace")) >= _MAX_BYTES

    page = ScrapedPage(
        url=url,
        final_url=final_url,
        title=title,
        meta_description=meta_description,
        text=text,
        word_count=len(text.split()),
        truncated=truncated,
        source="direct",
    )
    if truncated:
        page.warnings.append("The page was larger than the read limit, so the tail of it was not included.")
    return page


async def read_direct(raw_url: str) -> ScrapedPage:
    """The free direct reader alone, with no paid fallback — for callers that want a hint from a
    page (its title, its meta description) and would rather go without than spend a credit on it."""
    return await _read_direct(normalize_url(raw_url))


# The fallback readers, in the order they are tried, each paired with the line the UI shows so a
# fallback read is never invisible. Both are only reached when the free direct read has failed.
_FALLBACK_READERS: tuple[tuple[str, str], ...] = (
    ("context.dev", "Read through Context.dev's page reader"),
    ("claude", "Read through Claude's page reader"),
)


def _fallback_reader(name: str):
    """Map a reader name to its enabled-check and its coroutine. Kept as a lookup rather than an
    if/elif chain so adding a fourth reader is one tuple entry above and one line here."""
    return {
        "context.dev": (_context_dev_enabled, _fetch_via_context_dev),
        "claude": (_claude_fallback_enabled, _fetch_via_claude),
    }[name]


async def scrape_page(raw_url: str) -> ScrapedPage:
    """Read one page and return its copy, trying the direct reader first, then Context.dev, then
    Anthropic's fetcher. Raises `ScrapeError` only when all of them fail — the direct reader's
    reason is the one raised, since it is the one that describes the page ("blocked an automated
    read", "returned HTTP 404").
    """
    url = normalize_url(raw_url)

    direct_error: ScrapeError | None = None
    page: ScrapedPage | None = None
    try:
        page = await _read_direct(url)
    except ScrapeError as exc:
        direct_error = exc
        logger.info("Direct read failed url=%r: %s", url, exc)

    # A fallback earns its cost in exactly two situations: the direct read was refused, or it came
    # back with too little text to be the page (a JS shell, a consent wall). A good direct read is
    # never second-guessed, so neither paid reader is touched on the common path.
    if direct_error or (page and page.low_content):
        why = (
            "this site refuses direct server-side requests."
            if direct_error
            else "the direct read came back almost empty, so the page renders in the browser."
        )
        for name, label in _FALLBACK_READERS:
            enabled, read = _fallback_reader(name)
            if not enabled():
                continue
            try:
                fallback = await read(url)
            except ScrapeError as exc:
                logger.warning("Fallback read via %s also failed url=%r: %s", name, url, exc)
                continue
            except Exception:  # noqa: BLE001 — an upstream/transport failure is not the operator's problem
                # Swallowed on purpose: the direct reader's verdict below is the actionable one, and
                # a rate limit on a fallback should not become the message the operator reads.
                logger.exception("Fallback read via %s errored url=%r", name, url)
                continue

            if fallback.low_content:
                logger.info(
                    "Fallback read via %s for url=%r was also thin (%s words)",
                    name,
                    url,
                    fallback.word_count,
                )
                continue

            fallback.warnings.append(f"{label}: {why}")
            logger.info(
                "Read page url=%r via=%s words=%s (direct: %s)",
                url,
                name,
                fallback.word_count,
                "failed" if direct_error else "thin",
            )
            return fallback

    if page is None:
        # Both readers are out. Raise the direct reader's message: it names what the site did.
        raise direct_error or ScrapeError(f"Could not read {url}.")

    if page.low_content:
        page.warnings.append(
            "Only a little text came back, which usually means the page renders its copy in the "
            "browser. Check what was read before relying on it."
        )

    logger.info(
        "Scraped page url=%r final_url=%r via=%s words=%s chars=%s low_content=%s truncated=%s",
        url,
        page.final_url,
        page.source,
        page.word_count,
        page.char_count,
        page.low_content,
        page.truncated,
    )
    return page
