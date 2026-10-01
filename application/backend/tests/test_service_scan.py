"""The Pillar Page's service picker: scan a site's links, pick services, read their pages.

No network. The page fetch, the free reader and the Haiku call are all patched. The rules worth
pinning are the ones that keep the feature honest: only links the site actually has can become a
service, reading the selected pages never touches a paid reader, and the no-reference path takes a
landing page's design without cloning its markup. The files that have to agree (schema, both
prompts, the frontend's marker and catalog) are read here too, so they cannot drift apart unseen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import pipeline as pipeline_router
from app.services import scraper
from app.services import service_scan as S
from app.services.generation import build_prompt

_BACKEND = Path(__file__).resolve().parents[1]
_FRONTEND = _BACKEND.parent / "frontend" / "src"

SITE = """
<html><body>
<header><nav class="main-menu">
  <a href="/">Home</a>
  <a href="/commercial-loans/">Commercial Loans</a>
  <a href="/asset-finance">Asset Finance</a>
  <a href="/property-development-finance/">Property Development Finance</a>
  <a href="/about-us/">About Us</a>
  <a href="/contact/">Contact</a>
</nav></header>
<main>
  <a href="/asset-finance">Learn more</a>
  <a href="#top">Back to top</a>
  <a href="mailto:hello@example.com.au">Email us</a>
  <a href="/brochure.pdf">Download brochure</a>
  <a href="https://www.facebook.com/example">Facebook</a>
  <a href="/blog/rates-update/">Rates update for October</a>
</main>
</body></html>
"""


# --------------------------------------------------------------------------------------
# Measure: the links
# --------------------------------------------------------------------------------------


def test_candidates_are_same_site_readable_links_with_the_menus_wording() -> None:
    candidates = S.extract_link_candidates(SITE, "https://www.example.com.au/")
    by_url = {c.url: c for c in candidates}

    assert "https://www.example.com.au/asset-finance" in by_url
    # The menu's "Asset Finance" wins over the body's "Learn more" for the same page.
    assert by_url["https://www.example.com.au/asset-finance"].text == "Asset Finance"
    assert by_url["https://www.example.com.au/asset-finance"].in_nav is True
    assert by_url["https://www.example.com.au/blog/rates-update"].in_nav is False
    # Off-site, anchors, mail links and files are never candidates.
    assert not any("facebook.com" in u or u.endswith(".pdf") or "mailto" in u or "#" in u for u in by_url)
    # Navigation links come first, so a cap cuts the body before the menu.
    assert all(c.in_nav for c in candidates[: sum(1 for c in candidates if c.in_nav)])


NESTED_MENU = """
<nav><ul class="menu">
  <li><a href="/business/">Business &amp; Commercial</a>
    <ul class="sub-menu">
      <li><a href="/truck-loans/">Truck Loan</a></li>
      <li><a href="/car-loans/">Car Loan</a></li>
    </ul>
  </li>
  <li><span>Home Loans</span>
    <ul><li><a href="/refinancing/">Refinancing</a></li></ul>
  </li>
  <li><a href="/contact/">Contact</a></li>
</ul></nav>
"""


def test_each_menu_link_carries_the_item_it_is_nested_under() -> None:
    under = {c.text: c.under for c in S.extract_link_candidates(NESTED_MENU, "https://x.com.au/")}
    assert under == {"Business & Commercial": "", "Truck Loan": "Business & Commercial", "Car Loan": "Business & Commercial",
                     "Refinancing": "Home Loans", "Contact": ""}
    prompt = S._scan_prompt(S.extract_link_candidates(NESTED_MENU, "https://x.com.au/"), "phase2", "")
    assert "Truck Loan (in the menu under: Business & Commercial)" in prompt


def test_the_model_can_only_pick_links_the_site_has() -> None:
    candidates = S.extract_link_candidates(SITE, "https://www.example.com.au/")
    reply = json.dumps({"services": [
        {"name": "Commercial Loans", "url": "https://www.example.com.au/commercial-loans", "parent": ""},
        {"name": "Invoice Finance", "url": "https://www.example.com.au/invoice-finance", "parent": ""},
        {"name": "Commercial Loans", "url": "https://www.example.com.au/commercial-loans/", "parent": ""},
    ]})
    services, dropped = S._parse_reply(reply, candidates)
    assert [s.name for s in services] == ["Commercial Loans"]
    assert dropped == 1


@pytest.mark.asyncio
async def test_a_scan_returns_the_services_and_asks_for_sub_services_in_phase_2(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    async def fetch(_url: str) -> tuple[str, str]:
        return "https://www.example.com.au/", SITE

    async def ask(prompt: str, _on_usage: Any) -> str:
        prompts.append(prompt)
        return json.dumps({"services": [
            {"name": "Asset Finance", "url": "https://www.example.com.au/asset-finance", "parent": "Business Loans"},
        ]})

    monkeypatch.setattr(scraper, "_fetch_html", fetch)
    monkeypatch.setattr(S, "_ask", ask)

    scan = await S.scan_services("example.com.au", phase="phase2", focus="Asset Finance")
    assert scan.services == [S.ScannedService("Asset Finance", "https://www.example.com.au/asset-finance", "Business Loans")]
    assert "SUB-SERVICES" in prompts[0] and '"Asset Finance"' in prompts[0]

    await S.scan_services("example.com.au", phase="phase1")
    assert "List the SERVICES" in prompts[1]


@pytest.mark.asyncio
async def test_a_page_with_no_links_is_a_note_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch(_url: str) -> tuple[str, str]:
        return "https://spa.example.com/", "<html><body><div id=root></div></body></html>"

    async def never(_prompt: str, _on_usage: Any) -> str:
        raise AssertionError("no candidates means nothing to ask about")

    monkeypatch.setattr(scraper, "_fetch_html", fetch)
    monkeypatch.setattr(S, "_ask", never)
    scan = await S.scan_services("spa.example.com")
    assert scan.services == [] and "JavaScript" in scan.notes[0]


# --------------------------------------------------------------------------------------
# The selection
# --------------------------------------------------------------------------------------

# The exact shape `composeServicesAnswer` in `pipeline/pipelineData.ts` writes.
CARD_ANSWER = (
    "- Commercial Loans — https://www.example.com.au/commercial-loans\n"
    "- Asset Finance — https://www.example.com.au/asset-finance\n"
    "- Other (typed by the operator): Invoice Finance, Truck Loans"
)


def test_the_cards_answer_reads_back_as_services_with_their_pages() -> None:
    selected = S.parse_selection(CARD_ANSWER)
    assert selected == [
        S.SelectedService("Commercial Loans", "https://www.example.com.au/commercial-loans"),
        S.SelectedService("Asset Finance", "https://www.example.com.au/asset-finance"),
        S.SelectedService("Invoice Finance", None),
        S.SelectedService("Truck Loans", None),
    ]
    assert S.anchor_text(CARD_ANSWER) == "Commercial Loans, Asset Finance, Invoice Finance, Truck Loans"


def test_a_hand_typed_line_is_a_service_too() -> None:
    assert S.parse_selection("Asset Finance") == [S.SelectedService("Asset Finance", None)]


@pytest.mark.asyncio
async def test_selected_pages_are_read_free_and_one_failure_costs_only_that_page(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read_direct(url: str) -> scraper.ScrapedPage:
        if "asset" in url:
            raise scraper.ScrapeError("blocked an automated read")
        text = "We arrange commercial loans for owner-occupiers and investors. " * 40
        return scraper.ScrapedPage(url=url, final_url=url, title="t", meta_description=None, text=text, word_count=len(text.split()), truncated=False)

    async def paid(_url: str) -> Any:
        raise AssertionError("reading a list of URLs must never reach a paid reader")

    monkeypatch.setattr(scraper, "read_direct", read_direct)
    monkeypatch.setattr(scraper, "scrape_page", paid)

    block, report = await S.read_selected_pages(CARD_ANSWER)
    assert "### Commercial Loans (https://www.example.com.au/commercial-loans)" in block
    assert "owner-occupiers" in block
    assert [(r["name"], r["ok"]) for r in report] == [("Commercial Loans", True), ("Asset Finance", False)]


@pytest.mark.asyncio
async def test_no_selected_page_means_no_block() -> None:
    assert await S.read_selected_pages("- Invoice Finance") == ("", [])


@pytest.mark.asyncio
async def test_the_generation_step_appends_the_pages_for_pillar_page_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read(_answer: str):
        return "SOURCE CONTENT FROM EACH SELECTED SERVICE'S EXISTING PAGE ...", [{"name": "x", "ok": True}]

    monkeypatch.setattr(S, "read_selected_pages", read)
    answers, event = await pipeline_router._read_selected_service_pages("pillar_page", {"services_covered": CARD_ANSWER})
    assert answers["services_covered"].startswith(CARD_ANSWER) and "SOURCE CONTENT" in answers["services_covered"]
    assert event == {"type": "service_sources", "pages": [{"name": "x", "ok": True}]}

    untouched, none = await pipeline_router._read_selected_service_pages("cro", {"services_covered": CARD_ANSWER})
    assert untouched == {"services_covered": CARD_ANSWER} and none is None


# --------------------------------------------------------------------------------------
# No reference page
# --------------------------------------------------------------------------------------


def _frontend_no_reference_answer(url: str) -> str:
    """`noReferenceAnswer` from `pipeline/pipelineData.ts`, read out of the file rather than retyped."""
    source = (_FRONTEND / "pipeline" / "pipelineData.ts").read_text(encoding="utf-8")
    marker = re.search(r'export const NO_REFERENCE_MARKER = "([^"]+)"', source)
    template = re.search(r"return `\$\{NO_REFERENCE_MARKER\}([^`]*)\$\{landingUrl\}`", source)
    assert marker and template, "pipelineData.ts no longer defines the no-reference answer the backend reads"
    return marker.group(1) + template.group(1) + url


def test_the_frontends_no_reference_answer_is_recognised_and_still_names_the_design_source() -> None:
    answer = _frontend_no_reference_answer("https://www.example.com.au/")
    assert S.is_no_reference(answer)
    assert pipeline_router._as_url(answer) == "https://www.example.com.au/"
    assert not S.is_no_reference("https://www.example.com.au/commercial-loans/")


@pytest.mark.asyncio
async def test_no_reference_mode_takes_the_brand_design_but_never_clones_the_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def capture(_url: str):
        raise AssertionError("a landing page's markup must not become the pillar page's template")

    monkeypatch.setattr(pipeline_router.page_replica_service, "capture_page_template", capture)
    answers = {"reference_design_source": _frontend_no_reference_answer("https://www.example.com.au/")}
    assert await pipeline_router.resolve_page_template(None, answers, {}) is None
    # The design source itself is still the landing page, so DESIGN.md is captured from it.
    assert pipeline_router._design_source_url(answers, {}) == "https://www.example.com.au/"


# --------------------------------------------------------------------------------------
# The files that have to agree
# --------------------------------------------------------------------------------------


def test_the_field_sits_after_the_reference_questions_in_schema_and_catalog() -> None:
    schema = json.loads((_BACKEND / "schemas" / "drafts" / "pillar_page.json").read_text(encoding="utf-8"))
    ids = [f["field_id"] for f in schema["fields"]]
    assert ids.index("reference_design_source") < ids.index("reference_design_scope") < ids.index(S.SERVICES_FIELD_ID)

    catalog = (_FRONTEND / "data" / "assetCatalog.ts").read_text(encoding="utf-8")
    block = catalog[catalog.index('asset_id: "pillar_page"'):]
    block = block[: block.index("asset_id:", 10)]
    assert block.index('"reference_design_scope"') < block.index(f'"{S.SERVICES_FIELD_ID}"') < block.index('"improved_page_content"')


@pytest.mark.parametrize("phase", ["phase1", "phase2"])
def test_both_phases_prompts_say_how_to_use_the_selection_and_the_no_reference_mode(phase: str) -> None:
    prompt = build_prompt("pillar_page", {"services_covered": CARD_ANSWER}, phase)
    assert "Services / Sub-Services to Cover:" in prompt
    assert "## SERVICES TO COVER, AND A REFERENCE PAGE THAT DOES NOT EXIST" in prompt
    assert f"Reference Design Source begins `{S.NO_REFERENCE_MARKER}`" in prompt
    assert "Never add a claim" in prompt
    assert "**Services to cover**" in prompt  # reported back in the Step 0 table


def test_the_head_term_suggestions_are_anchored_on_the_selected_services() -> None:
    from app.services.headlines import resolve_service_anchor

    anchor, source = resolve_service_anchor("pillar_page", {"services_covered": CARD_ANSWER}, {"target_service": "Commercial Loans"})
    assert anchor == "Commercial Loans, Asset Finance, Invoice Finance, Truck Loans"
    assert source == "this stage's services_covered"
    # Skipped ("NONE") falls back to the run's service, as before.
    assert resolve_service_anchor("pillar_page", {"services_covered": "NONE"}, {"target_service": "Commercial Loans"})[0] == "Commercial Loans"


# --------------------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------------------


def test_the_scan_route_returns_services_and_422s_an_unreadable_page(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok(url: str, **_kwargs: Any) -> S.ServiceScan:
        return S.ServiceScan(source_url=url, services=[S.ScannedService("Asset Finance", "https://x.com.au/asset-finance")])

    monkeypatch.setattr(S, "scan_services", ok)
    res = TestClient(app).post("/pipeline/services/scan", json={"url": "https://x.com.au/", "phase": "phase2"})
    assert res.status_code == 200, res.text
    assert res.json()["services"] == [{"name": "Asset Finance", "url": "https://x.com.au/asset-finance", "parent": ""}]

    async def fail(_url: str, **_kwargs: Any) -> S.ServiceScan:
        raise S.ServiceScanError("Could not read https://x.com.au/: returned HTTP 403")

    monkeypatch.setattr(S, "scan_services", fail)
    res = TestClient(app).post("/pipeline/services/scan", json={"url": "https://x.com.au/"})
    assert res.status_code == 422 and "403" in res.json()["detail"]
