"""The deployable funnel: plan, forms, prompts, build, bundle and routes.

Run against the two real Funnel documents in `manual_execution/`: one page per URL (the Social Media
audit funnel) and three screens at one URL (the Partner Voice quiz). No live model call — `_call` is
stubbed — and no database: the routes' run reads are patched.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.routers import funnel_build as route
from app.services import funnel_build
from app.services.funnel_plan import hub_routing_text, parse_funnel_document
from app.services.generation import PageDesignInput
from app.services.image_briefs import GeneratedImage
from app.services.link_manifest import Link, LinkManifest
from app.services.reference_forms import extract_forms

SAMPLES = Path(__file__).resolve().parents[3] / "manual_execution"
AUDIT = (SAMPLES / "Funnel-Document_Social-Media-Marketing.md").read_text(encoding="utf-8")
QUIZ = (SAMPLES / "Partner-Voice-Audit-Funnel_LinkedIn-Marketing-Ads.md").read_text(encoding="utf-8")
DESIGN = PageDesignInput(
    design_md="---\nname: x\n---\n# x\n## Links\n| a | b |\n## Responsive\nbreakpoints",
    theme_brief="---\nname: x\n---\n# theme",
    screenshot_urls=("https://shots.example/hero.png",),
    generated_images=(GeneratedImage(role="hero", size="1536x1024", prompt="p", url="https://api.example.app/media/abc"),),
)
CF7 = """<form action="/contact-us/#wpcf7-f1-o1" method="post"><input type="hidden" name="_wpcf7" value="1">
<label for="n">Name</label><input id="n" name="your-name" required><input name="your-email" type="email" required>
<textarea name="your-message"></textarea><button type="submit">Send</button></form>"""


def _inputs(text: str = AUDIT, **over) -> funnel_build.BuildInputs:
    plan = parse_funnel_document(text)
    manifest = LinkManifest(
        source_url="https://trafficradius.com.au/",
        available=True,
        links=(Link(link_id="C1", group="cta", label="Book a call", href="https://trafficradius.com.au/contact/"),),
    )
    values = dict(
        plan=plan,
        design=DESIGN,
        manifest=manifest,
        forms=extract_forms(CF7, "https://trafficradius.com.au/contact-us/"),
        hub_text="Convert stage: the Social Media Strategy Consultation. Cascade: Growth Network.",
        site_origin="https://trafficradius.com.au",
    )
    values.update(over)
    return funnel_build.BuildInputs(**values)


# -- The plan ----------------------------------------------------------------------------------


def test_one_file_per_url_with_paths_from_the_funnel():
    plan = parse_funnel_document(AUDIT)
    assert [f.url_path for f in plan.files] == [
        "/social-media-audit/",
        "/social-media-audit/request/",
        "/social-media-audit/thank-you/",
        "/book-strategy-consultation/",
    ]
    assert plan.files[1].file_path == "social-media-audit/request/index.html"
    assert [e.key for e in plan.emails] == ["email-1", "email-2", "email-3"]
    assert plan.strategy and plan.notes


def test_screens_at_one_url_become_one_file():
    plan = parse_funnel_document(QUIZ)
    assert [f.url_path for f in plan.files] == ["/partner-voice-audit/", "/book-linkedin-strategy-session/"]
    assert len(plan.files[0].screens) == 3 and plan.files[0].is_multi_screen


def test_relative_links_between_files():
    files = parse_funnel_document(AUDIT).files
    assert files[0].href_to(files[1]) == "request/"
    assert files[1].href_to(files[0]) == "../"
    assert files[3].href_to(files[2]) == "../social-media-audit/thank-you/"
    assert files[0].href_to(files[0]) == "./"


def test_a_document_without_page_copy_is_not_buildable():
    assert not parse_funnel_document("# Something\n\nNo parts here.").available


def test_the_hub_document_loses_its_html_and_is_capped():
    text = hub_routing_text("## Hub\nConvert stage: X\n```html\n<div>mindmap</div>\n```\n" + "y" * 20_000)
    assert "mindmap" not in text and "Convert stage: X" in text
    assert len(text) < 15_000


# -- Forms ---------------------------------------------------------------------------------------


def test_a_search_box_is_not_a_lead_form():
    forms = extract_forms('<form method="get" action="/"><input name="s"></form>' + CF7, "https://x.com.au/c/")
    assert len(forms) == 1
    form = forms[0]
    assert form.action == "https://x.com.au/contact-us/#wpcf7-f1-o1" and form.method == "post"
    assert form.hidden == [("_wpcf7", "1")]
    assert [f.name for f in form.fields] == ["your-name", "your-email", "your-message"]
    assert form.captures_email and form.submit_label == "Send"


# -- Prompts -------------------------------------------------------------------------------------


def test_a_page_request_carries_its_copy_map_forms_hub_and_design():
    inputs = _inputs()
    gate = inputs.plan.files[1]
    content = funnel_build.build_page_request(inputs, gate)
    assert content[0] == {"type": "image", "source": {"type": "url", "url": "https://shots.example/hero.png"}}
    text = content[-1]["text"]
    assert "AUDIT REQUEST FORM (GATE PAGE)" in text
    assert "https://trafficradius.com.au/social-media-audit/request/" in text  # canonical
    assert "| FREE SOCIAL MEDIA AUDIT LANDING PAGE | https://trafficradius.com.au/social-media-audit/ | `../` |" in text
    assert '`<input type="hidden" name="_wpcf7" value="1">`' in text
    assert "Growth Network" in text
    assert "Design language, not a clone of the page" in text  # the pillar page's own directive
    assert "https://api.example.app/media/abc" in text
    assert "PAGE_BUILD_STANDARDS" in text


def test_no_forms_says_so_rather_than_inventing_an_endpoint():
    text = funnel_build.build_page_request(_inputs(forms=[]), parse_funnel_document(AUDIT).files[1])[-1]["text"]
    assert "No form could be read off the client's site" in text


def test_an_email_request_carries_its_copy_links_and_theme():
    inputs = _inputs()
    text = funnel_build.build_email_request(inputs, inputs.plan.emails[0])
    assert "Your Social Media Audit is underway" in text
    assert "https://trafficradius.com.au/book-strategy-consultation/" in text
    assert "# theme" in text


# -- Build ---------------------------------------------------------------------------------------


PAGE = "<!doctype html><html lang='en'><head><title>t</title></head><body><main><h1>Hi</h1></main></body></html>"


def test_the_page_and_its_notes_are_split():
    html, notes = funnel_build.split_output(f"```html\n{PAGE}\n```\n\n## Build notes\n- **Form mapping** — x")
    assert html == PAGE and notes.startswith("## Build notes")


@pytest.mark.asyncio
async def test_every_page_and_email_is_built_and_checked(monkeypatch):
    calls = []

    async def fake_call(content, max_tokens, on_usage):
        calls.append(max_tokens)
        return f"```html\n{PAGE}\n```\n## Build notes\n- ok", "end_turn"

    monkeypatch.setattr(funnel_build, "_call", fake_call)
    events = []

    async def on_event(event):
        events.append(event)

    items = await funnel_build.build_all(_inputs(), on_event=on_event)

    assert [i.key for i in items] == ["social-media-audit", "request", "thank-you", "book-strategy-consultation", "email-1", "email-2", "email-3"]
    assert calls.count(funnel_build.PAGE_MAX_TOKENS) == 4 and calls.count(funnel_build.EMAIL_MAX_TOKENS) == 3
    pages = [i for i in items if i.kind == "page"]
    assert all(p.score is not None and p.verdict for p in pages)
    assert sum(1 for e in events if e["type"] == "item_done") == 7
    assert all("html" not in e.get("item", {}) for e in events)  # progress never carries the page


@pytest.mark.asyncio
async def test_one_failed_page_does_not_fail_the_funnel(monkeypatch):
    async def flaky(content, max_tokens, on_usage):
        text = content if isinstance(content, str) else content[-1]["text"]
        if "AUDIT REQUEST FORM" in text.split("===== BEGIN FUNNEL MAP")[0]:
            raise RuntimeError("overloaded")
        return f"```html\n{PAGE}\n```", "end_turn"

    monkeypatch.setattr(funnel_build, "_call", flaky)

    async def ignore(event):
        pass

    items = await funnel_build.build_all(_inputs(), on_event=ignore, include_emails=False)
    failed = [i for i in items if i.error]
    assert [i.key for i in failed] == ["request"] and "overloaded" in failed[0].error
    assert all(i.html for i in items if not i.error)


@pytest.mark.asyncio
async def test_a_cut_off_page_is_flagged(monkeypatch):
    async def cut(content, max_tokens, on_usage):
        return f"```html\n{PAGE}\n```", "max_tokens"

    monkeypatch.setattr(funnel_build, "_call", cut)
    item = await funnel_build.build_page(_inputs(), parse_funnel_document(AUDIT).files[0])
    assert "cut off" in item.error


# -- Bundle --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_zip_puts_every_page_at_its_url_with_images_embedded():
    page = funnel_build.BuiltItem(
        key="request", kind="page", title="Gate", file_path="social-media-audit/request/index.html",
        url_path="/social-media-audit/request/", html=PAGE, score=95, verdict="ok", notes="## Build notes\n- x",
    )
    email = funnel_build.BuiltItem(key="email-1", kind="email", title="Email 1", file_path="emails/email-1.html", html="<html>e</html>", subject="Hi")

    async def embed(html):
        return html.replace("<h1>", "<h1 data-embedded>")

    data = await funnel_build.bundle_zip("Funnel", [page, email], [{"action": "https://x/a", "method": "post", "page_url": "https://x/"}], "Integrations: CRM", "https://x.com.au", embed)
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert set(archive.namelist()) == {"social-media-audit/request/index.html", "emails/email-1.html", "README.md"}
    assert "data-embedded" in archive.read("social-media-audit/request/index.html").decode()
    assert "data-embedded" not in archive.read("emails/email-1.html").decode()  # emails keep absolute URLs
    readme = archive.read("README.md").decode()
    assert "https://x.com.au/social-media-audit/request/" in readme and "Integrations: CRM" in readme


# -- Routes --------------------------------------------------------------------------------------


def _client():
    from app.main import app

    return TestClient(app)


RUN = "11111111-2222-3333-4444-555555555555"


def test_ready_reports_the_plan(monkeypatch):
    async def docs(run_uuid):
        return AUDIT, "hub"

    async def none(run_uuid):
        return None

    monkeypatch.setattr(route, "_documents", docs)
    monkeypatch.setattr(route, "_stored_build", none)
    body = _client().get(f"/pipeline/runs/{RUN}/funnel-build/ready").json()
    assert body["buildable"] and body["hub"] and not body["built"]
    assert [f["key"] for f in body["plan"]["files"]][0] == "social-media-audit"


def test_building_without_an_approved_funnel_is_a_422(monkeypatch):
    async def docs(run_uuid):
        return "", ""

    monkeypatch.setattr(route, "_documents", docs)
    response = _client().post(f"/pipeline/runs/{RUN}/funnel-build/stream", json={})
    assert response.status_code == 422 and "Approve the Funnel stage" in response.json()["detail"]


def test_the_stream_reports_progress_and_stores_the_build(monkeypatch):
    stored = {}

    async def docs(run_uuid):
        return AUDIT, "hub text"

    async def inputs(run_id, run_uuid, plan, hub_text, profile):
        return _inputs()

    async def store(run_uuid, value):
        stored.update(value)
        return 3

    async def fake_call(content, max_tokens, on_usage):
        return f"```html\n{PAGE}\n```", "end_turn"

    monkeypatch.setattr(route, "_documents", docs)
    monkeypatch.setattr(route, "_inputs", inputs)
    monkeypatch.setattr(route, "_store", store)
    monkeypatch.setattr(funnel_build, "_call", fake_call)

    response = _client().post(f"/pipeline/runs/{RUN}/funnel-build/stream", json={"include_emails": False})
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "plan" and kinds[1] == "inputs" and kinds[-1] == "done"
    assert kinds.count("item_done") == 4
    assert events[-1]["version"] == 3
    assert len(stored["items"]) == 4 and stored["items"][0]["html"] == PAGE
    assert "Publish at" in stored["content"]


def test_a_page_without_a_url_line_joins_the_page_its_header_names():
    """Click Trends' AI SEO scorecard funnel: the gate has no URL line, only "same URL as entry"."""
    doc = (
        "# F\n\n# PART 3 — ENTRY / LANDING PAGE — Full Copy\n**Page type:** Lead magnet entry page\n"
        "**URL:** `/ai-seo-scorecard/`\n\n### Section 1: Hero\ncopy\n\n"
        "# PART 4 — INTERACTIVE TOOL (GATE) — Full Copy\n"
        "**Page type:** Interactive scorecard (in-page, same URL as entry: `/ai-seo-scorecard/`)\n\n"
        "### Section 1: Q1\nquestion\n\n"
        "# PART 5 — RESULTS / DELIVERY PAGE — Full Copy\n**Page type:** Results\n**URL:** `/ai-seo-scorecard/results/`\n\nbody\n"
    )
    plan = parse_funnel_document(doc)
    assert [f.url_path for f in plan.files] == ["/ai-seo-scorecard/", "/ai-seo-scorecard/results/"]
    assert [s.title for s in plan.files[0].screens] == ["ENTRY / LANDING PAGE", "INTERACTIVE TOOL (GATE)"]


@pytest.mark.asyncio
async def test_a_walled_page_s_forms_are_read_rendered_once(monkeypatch):
    from app.services import context_dev, reference_forms
    from app.services.scraper import ScrapeError

    async def walled(url):
        raise ScrapeError(f"{url} challenged the request instead of serving the page (sg-captcha bot protection)")

    class Page:
        html = "<html><body>" + CF7 + "</body></html>"
        final_url = "https://x.com.au/contact/"

    rendered = []

    async def scrape_html(url):
        rendered.append(url)
        return Page()

    monkeypatch.setattr(reference_forms, "_fetch_html", walled)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "scrape_html", scrape_html)

    forms, notes = await reference_forms.read_forms(["https://x.com.au/", "https://x.com.au/contact/"])
    assert rendered == ["https://x.com.au/", "https://x.com.au/contact/"]
    assert len(forms) == 1 and forms[0].action == "https://x.com.au/contact-us/#wpcf7-f1-o1"
    assert any("Context.dev (1 credit)" in n for n in notes)


@pytest.mark.asyncio
async def test_a_missing_page_spends_nothing(monkeypatch):
    from app.services import context_dev, reference_forms
    from app.services.scraper import ScrapeError

    async def missing(url):
        raise ScrapeError(f"{url} answered 404")

    async def must_not(url):
        raise AssertionError("a 404 was paid for")

    monkeypatch.setattr(reference_forms, "_fetch_html", missing)
    monkeypatch.setattr(context_dev, "is_configured", lambda: True)
    monkeypatch.setattr(context_dev, "scrape_html", must_not)
    forms, notes = await reference_forms.read_forms(["https://x.com.au/nope/"])
    assert forms == [] and "404" in notes[0]


def test_a_run_image_on_the_loopback_is_not_held_against_the_page():
    """The zip writes run images into the file, so a local run's `127.0.0.1:8001/media/…` ships fine."""
    html = PAGE.replace("<h1>Hi</h1>", '<h1>Hi</h1><img src="http://127.0.0.1:8001/media/abc" alt="logo" width="1" height="1">')
    item = funnel_build.BuiltItem(key="k", kind="page", title="t", file_path="k/index.html", html=html)
    funnel_build.check_built_page(item, _inputs(known_images=["http://127.0.0.1:8001/media/abc"]))
    assert not any("served from this machine" in i["why"] for i in item.issues)
