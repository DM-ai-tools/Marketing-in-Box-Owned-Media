"""A downloaded page carries its images, so it renders on a device that cannot reach their URLs.

A pillar page shared from a local run named `http://127.0.0.1:8001/media/...` for every generated
image — the recipient's own machine — and the client's own site for its logo, which that host
refuses to other sites. `standalone_html.make_standalone` writes each image into the file.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from fastapi.testclient import TestClient

from app.routers import pipeline
from app.services import standalone_html
from app.services.scraper import FetchedAsset, ScrapeError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 32
JPG = b"\xff\xd8\xff" + b"\x02" * 32
MEDIA_ID = uuid.UUID("c5b2e15f-c047-4c5d-95bd-4a83e9404db0")
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()
JPG_URI = "data:image/jpeg;base64," + base64.b64encode(JPG).decode()


async def _media(asset_id: uuid.UUID):
    return (PNG, "image/png") if asset_id == MEDIA_ID else None


def _remote(files: dict[str, tuple[bytes, str]], seen: list | None = None):
    async def fetch(url, **kwargs):
        if seen is not None:
            seen.append((url, kwargs.get("referer")))
        if url not in files:
            raise ScrapeError(f"{url} answered 403")
        data, mime = files[url]
        return FetchedAsset(url, data, mime, None)

    return fetch


@pytest.mark.asyncio
async def test_media_images_are_read_from_the_database_on_any_host(monkeypatch):
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({}))
    page = (
        f'<img src="http://127.0.0.1:8001/media/{MEDIA_ID}" alt="hero">'
        f'<img src="https://old-host.up.railway.app/media/{MEDIA_ID}" alt="again">'
    )

    result = await standalone_html.make_standalone(page, _media)

    assert result.html.count(PNG_URI) == 2
    assert "127.0.0.1" not in result.html
    # The same row is read once, however many hosts name it.
    assert result.inlined == 2 and not result.failed


@pytest.mark.asyncio
async def test_a_client_site_image_is_read_with_its_own_site_as_referer(monkeypatch):
    seen: list = []
    logo = "https://clicktrends.com.au/wp-content/uploads/2025/05/image-1-2.png"
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({logo: (PNG, "image/png")}, seen))

    result = await standalone_html.make_standalone(f'<a href="/"><img src="{logo}" alt="Click Trends"></a>', _media)

    assert f'<img src="{PNG_URI}" alt="Click Trends">' in result.html
    assert seen == [(logo, "https://clicktrends.com.au/")]


@pytest.mark.asyncio
async def test_an_unreadable_image_keeps_its_url_and_is_reported(monkeypatch):
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({}))
    page = '<img src="https://walled.example/x.png" alt="x">'

    result = await standalone_html.make_standalone(page, _media)

    assert result.html == page
    assert result.failed and result.failed[0][0] == "https://walled.example/x.png"


@pytest.mark.asyncio
async def test_srcset_collapses_to_the_largest_candidate(monkeypatch):
    big = "https://cdn.example/w_1600,q_80/hero.jpg"
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({big: (JPG, "image/jpeg")}))
    page = (
        '<picture><source srcset="https://cdn.example/w_800,q_80/hero.jpg 800w, '
        f'{big} 1600w" type="image/jpeg">'
        f'<img src="{big}" srcset="https://cdn.example/w_800,q_80/hero.jpg 800w, {big} 1600w" '
        'sizes="100vw" alt="hero"></picture>'
    )

    result = await standalone_html.make_standalone(page, _media)

    assert f'<source srcset="{JPG_URI}" type="image/jpeg">' in result.html
    # The <img> keeps only its inlined src: no remote width for the browser to pick instead.
    assert f'<img src="{JPG_URI}" alt="hero">' in result.html
    assert "w_800" not in result.html


@pytest.mark.asyncio
async def test_css_backgrounds_are_inlined_in_style_blocks_and_attributes(monkeypatch):
    bg = "https://cdn.example/bg.png"
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({bg: (PNG, "image/png")}))
    page = (
        f'<style>.hero{{background:url("{bg}") center/cover}}</style>'
        f'<div style="background-image: url(&quot;{bg}&quot;)"></div>'
    )

    result = await standalone_html.make_standalone(page, _media)

    assert f".hero{{background:url({PNG_URI}) center/cover}}" in result.html
    assert f'style="background-image: url({PNG_URI})"' in result.html


@pytest.mark.asyncio
async def test_links_and_social_previews_keep_their_urls(monkeypatch):
    calls: list = []
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({}, calls))
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://api.example.app")
    page = (
        f'<meta property="og:image" content="http://127.0.0.1:8001/media/{MEDIA_ID}">'
        '<a href="https://clicktrends.com.au/brochure.png">Brochure</a>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Oswald">'
    )

    result = await standalone_html.make_standalone(page, _media)

    assert calls == [] and result.inlined == 0
    assert "https://clicktrends.com.au/brochure.png" in result.html
    # A social preview needs a URL, but never the loopback.
    assert f'content="https://api.example.app/media/{MEDIA_ID}"' in result.html


@pytest.mark.asyncio
async def test_an_icon_link_is_inlined(monkeypatch):
    icon = "https://clicktrends.com.au/favicon.png"
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({icon: (PNG, "image/png")}))

    result = await standalone_html.make_standalone(f'<link rel="icon" href="{icon}">', _media)

    assert result.html == f'<link rel="icon" href="{PNG_URI}">'


@pytest.mark.asyncio
async def test_a_non_image_answer_is_not_inlined(monkeypatch):
    url = "https://walled.example/logo.png"
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({url: (b"<html>robot</html>", "text/html")}))

    result = await standalone_html.make_standalone(f'<img src="{url}">', _media)

    assert result.inlined == 0 and url in result.html


def test_srcset_parsing_keeps_commas_inside_urls():
    candidates = standalone_html._srcset_candidates("https://a/w_1,h_2/x.jpg 1x,https://a/w_2,h_4/x.jpg 2x")
    assert candidates == [("https://a/w_1,h_2/x.jpg", "1x"), ("https://a/w_2,h_4/x.jpg", "2x")]


def test_the_route_returns_the_file_and_what_it_could_not_inline(monkeypatch):
    monkeypatch.setattr(standalone_html, "fetch_asset", _remote({}))
    monkeypatch.setattr(pipeline, "_load_media", _media)
    from app.main import app

    response = TestClient(app).post(
        "/pipeline/html/standalone",
        json={"html": f'<img src="http://127.0.0.1:8001/media/{MEDIA_ID}"><img src="https://x.example/a.png">'},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inlined"] == 1 and PNG_URI in body["html"]
    assert [f["url"] for f in body["failed"]] == ["https://x.example/a.png"]
