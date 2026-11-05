"""SEO architecture: SearchFit hub/spoke rules on measured keyword clusters."""

from __future__ import annotations

from app.services import seo_architecture as S


def _cluster(name: str, keyword: str, volume: int, *, intent: str = "informational", content_type: str = "guide", url: str = "") -> dict:
    return {
        "name": name,
        "intent": intent,
        "content_type": content_type,
        "funnel": "awareness",
        "recommended_url": url,
        "keywords": [{"keyword": keyword, "volume": volume, "difficulty": 20, "role": "Primary"}],
    }


def test_build_maps_clusters_to_hub_and_spokes() -> None:
    stored = {
        "provider": "dataforseo",
        "service": "AI SEO",
        "location": "Australia",
        "config_fingerprint": "fp1",
        "keyword_report": {
            "clusters": [
                _cluster("AI SEO Guide", "ai seo", 1200, url="/ai-seo/"),
                _cluster("AI SEO Tools", "ai seo tools", 800, intent="commercial", content_type="listicle", url="/ai-seo-tools/"),
                _cluster("AI SEO Agency", "ai seo agency", 400, intent="transactional", content_type="landing", url="/ai-seo-agency/"),
            ]
        },
    }
    arch = S.build_from_keyword_entry(stored, website="https://client.com.au")
    assert arch is not None
    assert len(arch.pages) == 3
    hub = arch.hub
    assert hub is not None
    assert hub.role == "hub"
    assert hub.primary_keyword == "ai seo"
    assert hub.url.endswith("/ai-seo/") or "/ai-seo/" in hub.url
    assert any(p.role == "spoke" for p in arch.pages)
    assert arch.links, "hub↔spoke edges required"
    assert any(e.reason == "hub → spoke" for e in arch.links)
    assert any(e.reason == "spoke → hub" for e in arch.links)
    md = arch.to_markdown()
    assert "## SEO Architecture Map" in md
    assert "Hub (pillar)" in md
    assert "### Page meta tags" in md
    assert hub.title_tag
    assert hub.meta_description
    assert len(hub.title_tag) <= S._TITLE_LIMIT
    assert len(hub.meta_description) <= S._META_LIMIT
    assert "ai seo" in hub.title_tag.lower() or "AI SEO" in hub.title_tag
    assert "ai seo" in hub.meta_description.lower()
    for page in arch.pages:
        assert page.title_tag
        assert page.meta_description
        assert len(page.title_tag) <= S._TITLE_LIMIT
        assert len(page.meta_description) <= S._META_LIMIT


def test_stub_provider_never_builds_a_map() -> None:
    stored = {
        "provider": "stub",
        "keyword_report": {"clusters": [_cluster("X", "x", 10)]},
    }
    assert S.build_from_keyword_entry(stored) is None


def test_existing_site_page_wins_over_proposed_slug() -> None:
    stored = {
        "provider": "dataforseo",
        "service": "SEO",
        "config_fingerprint": "fp",
        "keyword_report": {
            "clusters": [_cluster("Local SEO", "local seo melbourne", 500, url="/wrong/")],
        },
    }
    site = [S.SitePage(name="Local SEO Melbourne", url="https://client.com.au/local-seo/")]
    arch = S.build_from_keyword_entry(stored, site_pages=site, website="https://client.com.au")
    assert arch is not None
    assert arch.pages[0].status == "existing"
    assert arch.pages[0].url == "https://client.com.au/local-seo/"


def test_architecture_block_only_for_consumer_assets() -> None:
    title, meta = S.page_meta(
        primary="seo",
        cluster="SEO",
        intent="informational",
        content_type="guide",
        location="Australia",
        role="hub",
    )
    arch = S.Architecture(
        service="SEO",
        location="Australia",
        fingerprint="fp",
        pages=[
            S.MappedPage(
                cluster="SEO",
                primary_keyword="seo",
                intent="informational",
                content_type="guide",
                funnel="",
                volume=100,
                url="/seo/",
                status="proposed",
                role="hub",
                title_tag=title,
                meta_description=meta,
            )
        ],
    )
    block = S.architecture_block(arch, "pillar_page")
    assert "SEO_ARCHITECTURE" in block
    assert f"title tag `{title}`" in block
    assert f"meta description `{meta}`" in block
    assert "title tag and meta description" in block
    assert S.architecture_block(arch, "icp") == ""
    assert S.architecture_block(None, "pillar_page") == ""


def test_page_meta_stays_within_serp_limits_and_keeps_the_keyword() -> None:
    title, meta = S.page_meta(
        primary="ai seo agency near me melbourne cbd enterprise",
        cluster="AI SEO Agency",
        intent="transactional",
        content_type="landing",
        location="Melbourne, Australia",
        service="AI SEO",
        role="spoke",
    )
    assert len(title) <= S._TITLE_LIMIT
    assert len(meta) <= S._META_LIMIT
    assert "ai seo" in title.lower() or "AI SEO" in title
    assert "ai seo agency near me melbourne cbd enterprise" in meta.lower()


def test_from_stored_fills_meta_when_an_older_map_has_none() -> None:
    value = {
        "service": "SEO",
        "location": "Australia",
        "config_fingerprint": "fp",
        "pages": [
            {
                "cluster": "SEO Guide",
                "primary_keyword": "seo services",
                "intent": "informational",
                "content_type": "guide",
                "funnel": "",
                "volume": 900,
                "url": "https://x.com/seo-services/",
                "status": "proposed",
                "role": "hub",
            }
        ],
        "links": [],
        "notes": [],
    }
    arch = S.from_stored(value)
    assert arch is not None
    assert arch.pages[0].title_tag
    assert arch.pages[0].meta_description
    assert "seo services" in arch.pages[0].meta_description.lower()
    assert "### Page meta tags" in arch.to_markdown()


def test_intent_guess_from_modifiers() -> None:
    assert S._guess_intent("how to use ai for seo")[0] == "informational"
    assert S._guess_intent("best seo tools") == ("commercial", "listicle")
    assert S._guess_intent("seo agency near me")[0] == "transactional"


def test_from_stored_round_trip() -> None:
    stored = {
        "provider": "dataforseo",
        "service": "SEO",
        "location": "Australia",
        "config_fingerprint": "fp",
        "keyword_report": {"clusters": [_cluster("SEO", "seo services", 900, url="/seo-services/")]},
    }
    arch = S.build_from_keyword_entry(stored, website="https://x.com")
    assert arch is not None
    again = S.from_stored(arch.to_context_value())
    assert again is not None
    assert again.pages[0].primary_keyword == "seo services"
    assert again.to_context_value()["pages"][0]["url"]
    assert again.pages[0].title_tag == arch.pages[0].title_tag
    assert again.pages[0].meta_description == arch.pages[0].meta_description


def test_plan_render_returns_markdown() -> None:
    arch = S.Architecture(
        service="SEO",
        location="",
        fingerprint="fp",
        pages=[
            S.MappedPage("Hub", "seo", "informational", "guide", "", 10, "/seo/", "proposed", "hub"),
            S.MappedPage("Tools", "seo tools", "commercial", "listicle", "", 5, "/seo-tools/", "proposed", "spoke"),
        ],
        links=[
            S.LinkEdge("/seo/", "/seo-tools/", "Hub", "Tools", "seo tools", "hub → spoke"),
        ],
    )
    S._fill_page_meta(arch.pages, location="", service="SEO")
    text = S.render_for_plan(arch)
    assert "SEO Architecture Map" in text
    assert "seo tools" in text
    assert "### Page meta tags" in text
    assert arch.pages[0].title_tag
    assert arch.pages[1].meta_description
