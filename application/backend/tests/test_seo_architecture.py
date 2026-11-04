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
            )
        ],
    )
    assert "SEO_ARCHITECTURE" in S.architecture_block(arch, "pillar_page")
    assert S.architecture_block(arch, "icp") == ""
    assert S.architecture_block(None, "pillar_page") == ""


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
    text = S.render_for_plan(arch)
    assert "SEO Architecture Map" in text
    assert "seo tools" in text
