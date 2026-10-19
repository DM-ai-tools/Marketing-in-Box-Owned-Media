"""Make a generated page's HTML file self-contained, so it renders on any device.

A page the pipeline builds references its images by URL: the run's generated images and copied
logo at `/media/{id}` on this backend, and occasionally an image on the client's own site. Opened
on the operator's machine those load. Sent to anyone else they often do not — a page built locally
names `127.0.0.1:8001`, which is the recipient's own machine, and a client's host may refuse its
images to any other site (see `design_md._logo_file`). So every HTML *download* goes through
`make_standalone`, which reads each image and writes it into the file as a `data:` URI.

This is the export, never the prompt. A data URI handed to the model is thousands of characters it
has to echo byte-perfectly (`design_md.py`'s logo section documents that failure); here the bytes
are written by code, after the model is done.

- **`/media/{id}` is read from the database by id, on any host.** The page may name the loopback,
  an old public host or the current one; the bytes are the same row.
- **Anything else is a free read** through `scraper.fetch_asset` (the one request policy for site
  assets, public-host check included), with the image's own origin as the referer so a hotlink rule
  that admits its own site admits the read. Never a paid reader.
- **Nothing is fatal.** An image that cannot be read keeps its URL and is reported; the file is
  still delivered. Links (`<a href>`), `og:image` and external stylesheets are left alone — a social
  preview needs a real URL, and a link is not an image.
"""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.services import media_storage
from app.services.scraper import ScrapeError, fetch_asset

logger = logging.getLogger(__name__)

#: Reads one `/media/{id}` row: (bytes, content type), or None when there is no such row.
MediaLoader = Callable[[uuid.UUID], Awaitable["tuple[bytes, str] | None"]]

_MAX_IMAGE_BYTES = 8_000_000
#: Past this the remaining images keep their URLs. A page of a dozen photos stays well under it.
_MAX_TOTAL_BYTES = 60_000_000
_CONCURRENCY = 6

_ACCEPT = "image/avif,image/webp,image/svg+xml,image/png,image/*;q=0.8"
_MIME_BY_EXT = {
    "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif", "avif": "image/avif", "ico": "image/x-icon",
}

_MEDIA_PATH = re.compile(r"/media/([0-9a-fA-F-]{32,36})/?$")
_TAG = re.compile(r"<(img|source|video|image|link)\b[^>]*>", re.I)
_ATTR = re.compile(r"""(\s)([\w:.-]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"'=<>`]+)""")
_STYLE_ATTR = re.compile(r"""(\sstyle\s*=\s*)("[^"]*"|'[^']*')""", re.I)
_STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]+))\s*\)""", re.I)

#: Which attributes carry an image, per tag. `href` only on `<image>` (SVG) and an icon `<link>`.
_URL_ATTRS = {
    "img": ("src", "data-src"),
    "source": ("src",),
    "video": ("poster",),
    "image": ("href", "xlink:href"),
    "link": ("href",),
}
_SRCSET_ATTRS = ("srcset", "data-srcset")


@dataclass
class StandaloneResult:
    html: str
    inlined: int = 0
    total_bytes: int = 0
    #: (url, why) for each image left as a URL.
    failed: list[tuple[str, str]] = field(default_factory=list)


def _is_remote(url: str) -> bool:
    return url.startswith(("http://", "https://", "//"))


def _absolute(url: str) -> str:
    return f"https:{url}" if url.startswith("//") else url


def _srcset_candidates(value: str) -> list[tuple[str, str]]:
    """(url, descriptor) pairs, per the HTML srcset grammar: a URL is a run of non-whitespace, and a
    URL that ends in a comma ends its candidate. URLs with commas inside (image CDNs) survive."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(value)
    while i < n:
        while i < n and (value[i].isspace() or value[i] == ","):
            i += 1
        start = i
        while i < n and not value[i].isspace():
            i += 1
        url = value[start:i]
        if not url:
            break
        descriptor = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            start = i
            while i < n and value[i] != ",":
                i += 1
            descriptor = value[start:i].strip()
            i += 1
        out.append((url, descriptor))
    return out


def _largest(candidates: list[tuple[str, str]]) -> str | None:
    def weight(descriptor: str) -> float:
        try:
            return float(descriptor[:-1]) if descriptor[-1:] in ("w", "x") else 1.0
        except ValueError:
            return 1.0

    return max(candidates, key=lambda c: weight(c[1]))[0] if candidates else None


class _Inliner:
    def __init__(self, load_media: MediaLoader | None):
        self.load_media = load_media
        self.cache: dict[str, str | None] = {}
        self.result = StandaloneResult(html="")
        self.semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _bytes(self, url: str) -> tuple[bytes, str]:
        media = _MEDIA_PATH.search(urlparse(url).path)
        if media and self.load_media is not None:
            try:
                found = await self.load_media(uuid.UUID(media.group(1)))
            except ValueError:
                found = None
            if found is not None:
                return found
            # Not a row here (another deployment's media): fall through to a network read, on the
            # current public host when the URL names the loopback.
            url = media_storage.current_media_url(url)
        origin = urlparse(url)
        fetched = await fetch_asset(
            url, accept=_ACCEPT, referer=f"{origin.scheme}://{origin.netloc}/", max_bytes=_MAX_IMAGE_BYTES
        )
        mime = (fetched.content_type or "").split(";")[0].strip().lower()
        if mime in ("", "application/octet-stream", "binary/octet-stream"):
            # A generic type is the server not saying; a text/html answer is a bot wall, not an image.
            ext = origin.path.rsplit(".", 1)[-1].lower() if "." in origin.path else ""
            mime = _MIME_BY_EXT.get(ext, mime)
        return fetched.content, mime

    async def prefetch(self, url: str) -> None:
        if url in self.cache:
            return
        self.cache[url] = None
        async with self.semaphore:
            try:
                data, mime = await self._bytes(_absolute(html_lib.unescape(url)))
            except ScrapeError as exc:
                self.result.failed.append((url, str(exc)))
                return
            except Exception as exc:  # noqa: BLE001 — one unreadable image never fails the file
                logger.warning("Standalone export: could not read %s: %s", url[:90], exc)
                self.result.failed.append((url, "could not be read"))
                return
        if not mime.startswith("image/") or not data:
            self.result.failed.append((url, f"not an image ({mime or 'no type'})"))
            return
        if self.result.total_bytes + len(data) > _MAX_TOTAL_BYTES:
            self.result.failed.append((url, "the file reached its size limit"))
            return
        self.result.total_bytes += len(data)
        self.result.inlined += 1
        self.cache[url] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def data_uri(self, url: str) -> str | None:
        return self.cache.get(url)


def _tag_urls(tag_name: str, tag: str) -> list[str]:
    urls: list[str] = []
    attrs = {m.group(2).lower(): m.group(4).strip("\"'") for m in _ATTR.finditer(tag)}
    if tag_name == "link" and "icon" not in attrs.get("rel", "").lower():
        return urls
    for name in _URL_ATTRS.get(tag_name, ()):
        if _is_remote(attrs.get(name, "")):
            urls.append(attrs[name])
    for name in _SRCSET_ATTRS:
        largest = _largest(_srcset_candidates(attrs.get(name, "")))
        if largest and _is_remote(largest):
            urls.append(largest)
    return urls


def _css_urls(css: str) -> list[str]:
    return [u for m in _CSS_URL.finditer(css) if _is_remote(u := (m.group(1) or m.group(2) or m.group(3) or ""))]


def _rewrite_css(css: str, inliner: _Inliner) -> str:
    def swap(match: re.Match) -> str:
        url = match.group(1) or match.group(2) or match.group(3) or ""
        found = inliner.data_uri(url)
        # Base64 has no quotes, spaces or parentheses, so the unquoted form is safe in any context.
        return f"url({found})" if found else match.group(0)

    return _CSS_URL.sub(swap, css)


def _rewrite_style_attr(match: re.Match, inliner: _Inliner) -> str:
    """A `style="…"` on any element. Rewritten only when an image in it was inlined, so an
    attribute with nothing to change keeps its exact bytes."""
    prefix, raw = match.groups()
    quote, css = raw[0], html_lib.unescape(raw[1:-1])
    rewritten = _rewrite_css(css, inliner)
    if rewritten == css:
        return match.group(0)
    return f"{prefix}{quote}{html_lib.escape(rewritten, quote=True)}{quote}"


def _rewrite_tag(tag_name: str, tag: str, inliner: _Inliner) -> str:
    if tag_name == "link":
        rel = re.search(r"""\srel\s*=\s*["']?([^"'>]*)""", tag, re.I)
        if not rel or "icon" not in rel.group(1).lower():
            return tag
    src_inlined = False
    drop: set[str] = set()

    def swap(match: re.Match) -> str:
        nonlocal src_inlined
        space, name, eq, raw = match.groups()
        key = name.lower()
        quote = raw[0] if raw[:1] in "\"'" else '"'
        value = raw.strip("\"'")
        if key in _URL_ATTRS.get(tag_name, ()):
            found = inliner.data_uri(value)
            if found:
                if key == "src":
                    src_inlined = True
                return f"{space}{name}{eq}{quote}{found}{quote}"
        elif key in _SRCSET_ATTRS:
            found = inliner.data_uri(_largest(_srcset_candidates(value)) or "")
            if found:
                # One candidate: every other width would be a copy of the same picture in the file.
                return f"{space}{name}{eq}{quote}{found}{quote}"
        return match.group(0)

    rewritten = _ATTR.sub(swap, tag)
    if tag_name == "img" and src_inlined:
        # `src` is now the image itself; a srcset alongside would only make the browser pick a
        # remote width again, or embed the picture twice.
        for name in ("srcset", "data-srcset", "sizes"):
            drop.add(name)
        rewritten = re.sub(
            r"""\s(?:%s)\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+)""" % "|".join(drop), "", rewritten, flags=re.I
        )
    return rewritten


async def make_standalone(html: str, load_media: MediaLoader | None = None) -> StandaloneResult:
    """`html` with every image it references written in as a `data:` URI, where it can be read."""
    inliner = _Inliner(load_media)
    urls: list[str] = []
    for tag in _TAG.finditer(html):
        urls += _tag_urls(tag.group(1).lower(), tag.group(0))
    for block in _STYLE_BLOCK.finditer(html):
        urls += _css_urls(block.group(2))
    for attr in _STYLE_ATTR.finditer(html):
        urls += _css_urls(html_lib.unescape(attr.group(2)[1:-1]))
    await asyncio.gather(*(inliner.prefetch(url) for url in dict.fromkeys(urls)))

    out = _TAG.sub(lambda m: _rewrite_tag(m.group(1).lower(), m.group(0), inliner), html)
    out = _STYLE_BLOCK.sub(lambda m: m.group(1) + _rewrite_css(m.group(2), inliner) + m.group(3), out)
    out = _STYLE_ATTR.sub(lambda m: _rewrite_style_attr(m, inliner), out)
    # `og:image` and the like keep a URL; make sure it is not this machine's loopback.
    out = media_storage.current_media_urls_in(out)

    result = inliner.result
    result.html = out
    logger.info(
        "Standalone export: inlined=%s bytes=%s failed=%s", result.inlined, result.total_bytes, len(result.failed)
    )
    return result
