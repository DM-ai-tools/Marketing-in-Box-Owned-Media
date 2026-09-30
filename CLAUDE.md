# Marketing-in-a-Box — conventions

## Layout

- `application/backend` — FastAPI + SQLAlchemy/Alembic + Celery, Python 3.11, venv at `application/backend/.venv`.
  Routers in `app/routers/` stay thin and call into `app/services/`; persistence lives in `app/db/`.
- `application/frontend` — React + TypeScript + Vite.

Run the API with `python -m app --reload` from `application/backend` (see the event-loop note in
`app/main.py` — on Windows it must not start under uvicorn's default loop). Tests: `pytest` from the
same directory.

---

## Context.dev

One API and one key for web data: scraping, web search, brand intelligence, structured extraction.
Base URL `https://api.context.dev/v1`, docs at <https://docs.context.dev> (append `.md` to any docs
URL for plain markdown).

### The key

`CONTEXT_DEV_API_KEY` in `application/backend/.env` (git-ignored). It is a **server-side secret**:
the official SDK reads it from the environment on its own, so nothing passes it explicitly, stores
it on a model, logs it, or lets it reach the Vite bundle. The frontend gets Context.dev data only
through this backend's own routes. Rotate at <https://www.context.dev/dashboard/api-keys>.

Also read: `CONTEXT_DEV_SCRAPER` (default on) turns off the Context.dev reader inside
`scrape_page` without removing the key.

### The one wrapper module

**`application/backend/app/services/context_dev.py` is the only module that constructs a
Context.dev client.** Add a new endpoint there as a function returning a plain dataclass or dict;
do not call the SDK from a router, a service, or a script. The module owns the shared async client
(cached, so the connection pool is reused), error translation into one actionable sentence, and the
credit logging every call emits.

SDK: `context.dev` on PyPI, imported as `context.dev` (**not** `context_dev`). Pinned in both
`requirements.txt` and `pyproject.toml`.

### Endpoints in use

| Wrapper function | Endpoint | Cost | Why this app uses it |
| --- | --- | --- | --- |
| `scrape_markdown` | `GET /web/scrape/markdown` | 1 credit | Reader #2 in `app/services/scraper.py`. [Docs](https://docs.context.dev/api-reference/web-scraping/markdown) |
| `scrape_html` | `GET /web/scrape/html` | 1 credit | **Rendered** DOM, for `app/services/page_replica.py`. Reader #2 there, only when the free fetch returns a JS shell. [Docs](https://docs.context.dev/api-reference/web-scraping/html) |
| `search` | `POST /web/search` | 1 / 10 results | Grounded competitor + ICP source discovery, behind `POST /intel/search`. [Docs](https://docs.context.dev/api-reference/web-scraping/search) |
| `retrieve_brand` | `POST /brand/retrieve` | 10 credits | Domain → title, slogan, palette, logos, socials, industry, behind `GET /intel/brand`. [Docs](https://docs.context.dev/guides/get-brand-data) |
| `extract_styleguide` | `GET /web/styleguide` | 10 credits | Computed colors, type scale, spacing, shadows and button/card styles — the backbone of `DESIGN.md`. [Docs](https://docs.context.dev/api-reference/brand-intelligence/styleguide) |
| `extract_fonts` | `GET /web/fonts` | 5 credits | Which typeface sets most of the page's words, plus webfont files per weight. [Docs](https://docs.context.dev/api-reference/brand-intelligence/fonts) |
| `screenshot` | `GET /web/screenshot` | 1 credit | Reference images of the live page for the CRO rewrite. [Docs](https://docs.context.dev/api-reference/web-scraping/screenshot) |
| `extract_structured` | `POST /web/extract` | 10 credits | URL + our own JSON Schema, for stages that want fields rather than prose. [Docs](https://docs.context.dev/api-reference/web-extraction/extract) |

Past a few hundred URLs use `POST /batch/submit`
([docs](https://docs.context.dev/guides/scrape-websites-in-batches)) rather than looping
`scrape_markdown`; for recurring re-checks use Monitors
([docs](https://docs.context.dev/guides/monitor-website-changes)) rather than a polling loop.

### Where it is wired in

- **`app/services/scraper.py`** — `scrape_page` now tries three readers in order: the free direct
  fetch, then Context.dev, then Anthropic's `web_fetch`. Neither paid reader is touched unless the
  direct read was refused or came back thin, and a fallback that is *also* thin is rejected rather
  than passed off as the page. `ScrapedPage.source` records which one answered
  (`direct` | `context.dev` | `claude`) and the UI shows it.
- **`app/services/design_md.py`** — builds the per-run `DESIGN.md` from the CRO stage's
  **Existing Page URL**. See the section below.
- **`app/routers/intel.py`** (`/intel`) — `GET /intel/status`, `GET /intel/brand`,
  `POST /intel/search`. A missing key is a **503** (deployment problem); a lookup that fails for
  the requested domain or URL is a **422** (the operator can act on it), matching
  `POST /pipeline/scrape`.

### Rules

- **Credits are money.** Never loop a Context.dev call over a list of URLs. `num_results` on search
  is the credit dial — leave it at 10 unless a stage needs more.
- **Caching is the default.** Most endpoints serve a cached result; `max_age_ms` is passed only when
  a caller explicitly asks for fresh data (`context_dev.FRESH_TODAY_MS` / `FRESH_NOW_MS`).
- **Retries are the SDK's.** It honours `Retry-After` on 429 and backs off with jitter on 408/5xx,
  bounded by `_MAX_RETRIES`; validation errors are not retried. Do not add a retry loop on top.
- **Tests never hit the live API.** Patch the module-level functions in `context_dev` (routes,
  scraper) or `context_dev._client` (the wrapper's own mapping). See `tests/test_context_dev.py`
  and the reader-order tests at the bottom of `tests/test_scraper.py`.
- If a call fails or comes back empty, check
  <https://docs.context.dev/optimization/troubleshooting> before changing approach. Note that
  heavily bot-walled review sites (g2.com, trustpilot.com) defeat every reader here — Context.dev
  returns a 400 or a near-empty page, which the thin-content guard already handles.

---

## DESIGN.md — the page design pipeline

The CRO stage collects an **Existing Page URL**. That one URL is turned into a `DESIGN.md` and two
screenshots, which every HTML-producing stage then builds against, so a generated page carries the
client's real palette, type and components rather than a plausible invented one.

`app/services/design_md.py` owns this. `capture_page_design(url)` costs **17 Context.dev credits**
(10 styleguide + 5 fonts + 2 screenshots) and runs **once per run**, cached on the run as a
`ContextEntry` under `brand_design_tokens`. Never call it per stage.

### Where the URL comes from

`_design_source_url` in `app/routers/pipeline.py`, in preference order: `existing_page_url` (the
CRO stage's own field) → `parent_pillar_page_url` → **`reference_design_source`** (the Pillar Page
stage's own field) → `client_website_url` → the profile's `website_url`.

`reference_design_source` belongs in that list and its omission was a real bug: `pillar_page.json`
carries neither of the first two fields, so a Pillar Page run read `client_website_url` — the
client's *home page* — whichever page the operator had actually nominated, and a run with only the
reference filled in resolved to nothing and built in placeholder greys.

It is a `file_attach` accepting a file *or* text, so its value is a URL only some of the time — it
is equally where somebody pastes "take the spacing and card treatment, not the palette" or attaches
a screenshot. `_as_url` extracts a URL rather than assuming one: the first `https?://` run, or the
whole answer if it is a bare domain. A domain mentioned *inside* a sentence is being talked about,
not nominated, so it does not count, and an answer with no URL falls through as if blank.

### When there is no page to audit — `NEW PAGE` mode

A run's ICP is routinely for a service the client has never had a page for: "Social Media Marketing
for e-commerce" when the site sells one generic Social Media page. The CRO stage audits an
*existing* page and both `existing_page_url` and `existing_page_content` are `required`, so with
nothing offered the operator nominates whichever page exists. That is wrong twice: the audit is
about a page the run is not for, and because `existing_page_url` heads `_DESIGN_SOURCE_FIELDS`, the
home page silently becomes the design reference every page in the run is built against.

**The prompt already had the answer.** `Master_Prompt_Universal_Page_Rewrite_v1.md`: *"If the inputs
state `NEW PAGE`, switch to build-from-scratch mode and skip Step 2's before/after comparisons,
retaining every other instruction."* Both fields' own instructions offer the sentinel. The mode was
reachable all along by typing one exact phrase, and unreachable in practice.

`NEW_PAGE_OPTIONS` in `pipeline/pipelineData.ts` writes both answers from a button on the URL
question. Under it, a second option skips CRO for `pillar_page` via the stage gate below.

- **Build-from-scratch is the recommended option, and the order says so.** It keeps the stage, and
  the stage is what writes `cro_rewritten_copy`, `cro_locked_sections` and `cro_terminology_map` —
  the three documents `pillar_page` requires. Skipping CRO does not produce them, so it moves the
  copy problem to the next stage rather than solving it; the gate then asks for five documents by
  hand. Both are offered because the second is what an operator who only wants a page will ask for,
  and it is better to state that cost up front than to let them find it three questions later.
- **Both fields are answered together.** With no page there is nothing to read, so `SCRAPE_SOURCES`
  does not fire and `existing_page_content` becomes the same question again — which is where an
  operator who has just said there is no page types "N/A", and the prompt reads a page whose copy is
  the letters N/A.
- **The answers are the field instructions verbatim**, em dash included, not a bare `NEW PAGE`: they
  land in the INPUTS block an operator may read back. `_as_url` returns None for both, so the design
  source falls through to `parent_pillar_page_url` → `reference_design_source` → `client_website_url`
  rather than sitting at the top of the list as an unusable value.
- **Three files have to agree** — the prompt's clause, the schema's `raw_explanation`, and that
  table — and nothing but `tests/test_new_page_mode.py` connects them. It parses all three. Any one
  drifting gives the same failure: a button that claims to switch modes, an answer that reads as
  ordinary page copy, and a "rewrite" of a page that does not exist.

### Staleness — `DESIGN_CAPTURE_VERSION`

A run's sheet is cached under `brand_design_tokens` and reused for the life of the run, because a
brand does not change between stages and 17 credits per stage would be the largest line on a run.
That cache used to be unconditional whenever the URL matched, which had a consequence nobody
noticed until the structure walk landed: **a run captured before an improvement kept serving the
older document forever, and re-running the stage could not change anything.**

So the stored row carries `capture_version`, and `_reuse_stored_design` re-reads when it is behind.
Bump `DESIGN_CAPTURE_VERSION` whenever `build_design_md` starts emitting something a stage needs —
it is the only way an improvement reaches a run that already exists. Bumping it costs one
re-capture (17 credits) per existing run, on that run's next stage. `REPLICA_CAPTURE_VERSION` does
the same job for `page_replica_template`, where it matters more: stored slot ids are identifiers,
so a template captured under older slot rules must be re-read rather than reinterpreted.

Note that **"Refine / Request Changes" never consults the sheet at all** — `generate_revision_stream`
builds from the previous draft plus the operator's note (`build_revision_prompt`) and is handed no
`page_design`. Only a full re-run picks up a re-captured sheet.

### What is merged

Context.dev is primary; the free local CSS parse in `design_tokens.py` fills what it has no field
for. Neither covers for the other, and when both fail the document says `NOT AVAILABLE` with a
reason rather than inventing a palette.

| From Context.dev | From `design_tokens.py` (local CSS parse) |
| --- | --- |
| colors, h1-h4 + p type scale, spacing, shadows | the real logo (inline SVG or data URI) |
| button and card component styles | palette *usage roles* — which hex is a border, which a surface |
| webfont families and files per weight | button hover states, the site's own `--custom-property` names |

### The third tier — Firecrawl and Brandfetch, for named gaps only

Context.dev stays primary. `app/services/design_md.py`'s `_brand_gaps` checks, after Context.dev
and the local CSS parse have both had a turn, whether colours, a type scale, or a logo are still
missing (a bot-walled page, a JS theme the local parse cannot resolve) — and only for a gap that
is actually still open, tries `app/services/firecrawl_client.py` (structured extraction, `POST
/extract`) and then `app/services/brandfetch_client.py` (brand-by-domain, `GET
/v2/brands/domain/{domain}`), each filling only the keys the one before it left empty.

- **Never a reordering.** Neither module is a peer of Context.dev; both are called from inside
  `design_md.py`'s own merge logic, exactly the way the local CSS parse already is. A value
  Context.dev or the local parse found is never replaced. `test_fallback_never_overrides_a_value_
  context_dev_already_found` pins this.
- **Skipped entirely when there is no gap, and again when neither key is set.** `FIRECRAWL_API_KEY`
  / `BRANDFETCH_API_KEY` in the backend `.env`; either may be blank. A run with both unset behaves
  exactly as it did before this tier existed — `test_fallback_is_never_called_when_context_dev_
  left_no_gap` is what would fail if a future change made this tier chatty.
- **Both wrappers are dumb, like `context_dev.py`'s own dataclasses.** They return raw fields
  (hex strings, a bare URL); validating a colour as a real hex and picking "primary" vs
  "secondary" happens once, in `design_md.py`'s merge — the same place that already validates
  Context.dev's own colours. Neither module builds its own opinion of the brand.
- **Provenance is stated in the document, not silent.** A colour or logo filled by this tier says
  so in the rendered DESIGN.md ("Context.dev found nothing here; via firecrawl"), because a value
  with no stated source is indistinguishable from one Context.dev actually measured.
- **This tier can rescue an otherwise-`NOT AVAILABLE` page.** If Context.dev is unconfigured (or
  fails) and the local parse also comes back empty, but Firecrawl or Brandfetch answers, the page
  is `available=True` built from whatever this tier found — still real, measured values, never an
  invented palette.

### The two views — which stage gets which

Set in `app/services/generation.py`; `PageDesignInput.sheet_for(asset_id)` picks between them.

| Stage set | Members | Gets |
| --- | --- | --- |
| `PAGE_REPLICA_STAGES` | `cro`, `pillar_page` | the **full DESIGN.md** — spacing, elevation, components. Its output stands next to the original. |
| `BRAND_THEME_STAGES` | `lead_magnet`, `funnel_hub_media`, `webinar` | the **theme brief** — colours, type, shapes, logo. Layout and components stripped, with an explicit "this is a theme, not a template" directive, because a lead magnet has its own structure. |
| `SCREENSHOT_STAGES` | `cro`, `pillar_page` | the reference images, as `image` content blocks. Free — the shots are captured once per run either way, so a stage here costs prompt tokens and no credits. `pillar_page` was missing and should not have been: its Rule 1 is that every colour, button and layout element traces back to the reference, and it was the one stage not shown the reference. |
| everything else | `icp`, `funnel`, `sms_sequence`, `plan_of_action`, … | nothing — no palette to get wrong. |

### Screenshots

Two per capture: a full-page shot for section order and vertical rhythm, and a 1440×900 hero shot
at readable resolution. They travel as **URL image sources** (`{"type":"image","source":{"type":
"url",…}}`) — Context.dev hosts the file, so it never passes through this backend or its database.

**Anthropic rejects an image past 8000px on either axis outright — it does not resize it.** A
full-page shot of a long landing page routinely exceeds that (a real one measured 1920×17615), so
`Screenshot.model_safe` gates it: an oversized shot is stored and shown to the operator but withheld
from the prompt, and a note says why. In practice the hero shot is usually the only one the model
sees. Do not "fix" this by sending it anyway.

Images lead the user message, ahead of the text: the text ends with the master prompt's instruction
to proceed, and an image after that sits between the instruction and the response.

### Routes

- `POST /pipeline/design` — capture for an arbitrary URL. **17 credits**, stores nothing. The
  "show me what you'd read off this page" button.
- `GET /pipeline/runs/{run_id}/design` — what was already captured for a run. **Free**, safe to
  poll; returns 404 before the run's first HTML stage.

### Rules

- The front matter is **hand-written YAML** (`_scalar` / `_yaml_block` in `design_md.py`), not
  PyYAML — nothing in the runtime depends on a YAML library. A bare `#a4d36b` opens a YAML comment,
  so colours **must** stay quoted; `tests/test_design_md.py` parses the block to prove it.
- Font weights arrive from the API as floats. Both views normalise through `_scalar` / `_plain`,
  because `font-weight: 500.0` is not a font weight.
- Only the weights the page's own tokens reference are listed. Emitting all nine Google weights per
  family put ~1,500 characters of hashed filename into every HTML stage's prompt.
- A `ContextEntry` written before DESIGN.md existed holds only `content`. `_page_design_input`
  reads it as a theme brief rather than discarding it — do not re-capture historical runs to
  upgrade the shape.
- **The logo section must always offer the absolute URL, before the data URI.** This has broken
  once. A real logo's data URI is ~16,000 characters of base64 — roughly 4,000 output tokens the
  model has to echo byte-perfectly mid-build. Given only that form it burns its output budget on
  the blob and the logo never lands, or it draws a substitute wordmark, which looks entirely
  plausible until somebody who knows the brand sees it. Measured on a real page: data-URI-only
  produced no logo and hit the output cap mid-base64; URL-first used the real file and finished
  normally. `tests/test_design_md.py` pins both the presence and the ordering.
- **Every section `_BRAND_TOKEN_DIRECTIVE` names must exist in the document.** The directive tells
  the model to use "the Logo section" and "the supplied CSS custom properties"; an instruction
  pointing at a section that is not there is worse than no instruction, because the model fills the
  gap itself. `test_every_section_the_directive_names_is_present` fails when they drift apart.
- Spec for the format: `.claude/skills/design-spec/SKILL.md`.

---

## Image briefs — OpenAI, this backend's own media route, and why the model never calls either itself

`capture_page_design` also derives three `ImageBrief`s (`app/services/image_briefs.py`) from the
measured brand tokens — hero, proof, closing-cta — each a photorealistic prompt bound to the run's
real palette and typography, at one of `gpt-image-2`'s accepted sizes.

**The briefs used to be handed to the model as an instruction, not a value — and that was a real
bug.** The `cro`/`pillar_page` prompt used to read *"Generate the image with Runway GPT Image 2,
then place the returned hosted URL in the HTML."* That generation call has no tool access to an
image generator; the model cannot literally do that. It could only fabricate a plausible-looking
URL, which is the exact failure `design_tokens.py` documents for an invented palette, one layer
up — a generated asset that looks correct until someone opens it and the image is a dead link.

The fix is the same shape as that one: measure first, then hand the model the resolved value.

- **Originally wired to Runway, since replaced with OpenAI's `gpt-image-2`** — this was a real
  provider swap, not just an endpoint change, because of one difference confirmed against OpenAI's
  own docs before switching: `response_format` (which picks `url` vs `b64_json`) is documented as
  DALL-E-only. GPT image models always return base64 bytes, never a hosted URL. Runway hosted the
  file for you; OpenAI does not, so this pipeline now has to.
- **`app/services/media_storage.py` is the piece that fact requires — and it stores the bytes in
  Postgres, not on local disk.** A first version wrote to `media/generated/` and served it with
  `StaticFiles`; that broke on deployment. This app runs on Railway, where local disk is the
  *container's* disk — ephemeral across every redeploy/restart by default — and a Railway Volume
  does not fix it either: confirmed against Railway's own docs, a Volume "cannot be used with
  replicas" and is capped at one per service, so it would not survive this backend running as more
  than one instance. Postgres is already durable, already provisioned, and already
  multi-instance-safe, so the bytes live in a `MediaAsset` row (`app/db/models.py`, migration
  `c8d914e6a5b3`) and are served back at `GET /media/{id}` (`app/routers/media.py`) — the one
  deliberate exception to this schema's usual "store a reference, not the bytes" rule (see
  `MediaAsset`'s own docstring for why the `.pptx` slide-deck's generate-and-stream pattern
  doesn't apply here: an OpenAI image is neither free nor deterministic to regenerate, and has to
  keep answering at the same URL for as long as the HTML it is embedded in exists).
  **A base64 data URI was deliberately not the fix either** — that is the exact failure the logo
  section (`DESIGN.md` pipeline, above) already documents: thousands of characters the model would
  have to echo byte-perfectly into its own output.
- **`BACKEND_PUBLIC_URL` must be set on any real deployment.** It is what `media_storage.py` uses
  to build the absolute URL handed to the model; unset, it falls back to `127.0.0.1:8001` for
  local dev, which is the *container's own loopback* on Railway — unreachable from outside, so
  every generated image would be a dead link in the delivered HTML. On Railway:
  `BACKEND_PUBLIC_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}` (`RAILWAY_PUBLIC_DOMAIN` is injected by
  Railway automatically).
- **`image_briefs.generate_all(briefs, session)`** calls `openai_image_client.generate_image` for
  all three briefs concurrently (mirroring the two Context.dev screenshots), and lets each brief
  fail independently — a content-policy rejection on one costs that image alone, never the other
  two. Saving to Postgres happens *after* that gather, one at a time: an `AsyncSession` is not
  safe for concurrent use, so `media_storage.save_generated_image` cannot be called from inside
  the concurrent branch. Returns `((), ())` immediately, no delay and no log noise, whenever
  `OPENAI_API_KEY` is unset — the same "skip cleanly" contract `context_dev.is_configured()`,
  `firecrawl_client`, and `brandfetch_client` all already have.
- **Generation is server-side, triggered from `resolve_page_design`, cached per run, and the
  image rows and the pointer to them commit together.** `_ensure_generated_images` in
  `app/routers/pipeline.py` runs on the first `PAGE_REPLICA_STAGES` stage (`cro` or `pillar_page`)
  that actually needs imagery, and only then: it is a no-op when the run already has
  `generated_images` stored, when there are no briefs, or when OpenAI is unconfigured. It opens
  one session, passes it into `generate_all` (so every `MediaAsset` it stages lands on that same
  session), and only then writes the run's `generated_images` context entry and commits once —
  the images and the run's pointer to them either both persist or neither does. The second
  `PAGE_REPLICA_STAGES` member reuses the first's result for free, the same "capture once, read
  from context after" rule `DESIGN_CAPTURE_VERSION` already applies to the brand tokens
  themselves — image generation is real money exactly like a Context.dev credit is, and a stage
  re-run for a copy tweak should not re-spend on images that did not need to change.
- **The prompt gets real URLs, never an instruction.** `generation.py`'s `_generated_images_block`
  lists each `GeneratedImage`'s role, size and already-hosted `/media/{id}` URL and tells the
  model to use it verbatim — nothing here asks the model to produce a URL itself.
  `PageDesignInput.generated_images` replaces the old `image_briefs: tuple[str, ...]` field for
  this reason: a prompt string is something to act on, a resolved URL is something to copy.
- **Every brief's size must be one `gpt-image-2` actually accepts.** `1024x1024` / `1536x1024` /
  `1024x1536` / `auto`, per OpenAI's own parameter reference — this is the same lesson the Runway
  integration learned the hard way (its ratio strings, `1360:768` / `1168:880`, were never in
  Runway's accepted enum and 400'd on every live call, caught only by hitting the real API rather
  than a mocked test). `openai_image_client.VALID_SIZES` is the source of truth;
  `test_every_brief_size_is_one_openai_actually_accepts` in `tests/test_image_briefs.py` is the
  permanent cross-check, and `openai_image_client.generate_image` rejects an invalid size, quality
  or output_format locally (`OpenAIImageInvalidRequest`, mapped to a 422) before spending a
  request on one.
- **A failed generation degrades, it does not fail the stage.** `_ensure_generated_images` writes
  nothing when every brief failed, so the run tries again on its next qualifying stage rather than
  caching an empty result that looks identical to "already tried."

---

## Page replication — following the client's real page

DESIGN.md answers "what is this page made of". It does not answer "what shape is it", and it never
answered "is this the client's actual page". Two modules cover those, and they are steps on one
ladder rather than alternatives — a run uses whichever rung it can reach.

| Rung | Module | What the stage gets | Cost | Fidelity |
| --- | --- | --- | --- | --- |
| 1 | `design_md.py` | palette, type, components, logo | 17 credits | the right brand, an invented page |
| 2 | `page_structure.py` | the band sequence, nav labels, contacts, client logos, SVG inventory | **free** | the right shape, generated markup |
| 3 | `page_replica.py` | the client's own markup with new words in it | 0-1 credits | the actual page |

### Why rungs 2 and 3 exist

The replica stages are told "a visitor must not be able to tell the generated page from the
client's own" (`_BRAND_TOKEN_DIRECTIVE`). Until rung 2 they were told that having been shown: page
text with every tag stripped — and `<nav>` and `<svg>` dropped outright by
`scraper._DROP_CONTENT_TAGS`, so the menu, the logo's placement and every icon were gone before the
model saw anything; a token sheet, which is a style sheet; and one 1440x900 hero screenshot, because
`Screenshot.model_safe` withholds the full-page shot of any real landing page. Asked to reproduce a
page it has never seen below the fold, a model invents a plausible section order and presents it as
a replica — the same unfollowable-instruction failure `design_tokens.py` documents for colours, one
level up. Same fix: measurement, not a stronger directive.

Rung 2 is a *description*, so the geometry is still generated and can still drift. Rung 3 removes
the drift by removing the generation: the model never emits HTML at all.

### `html_dom.py` — the shared tree

Both walks need ancestry and sibling order, which a streaming parser has already discarded. Stdlib
`html.parser` only; do not add lxml or bs4 for this. It is a tolerant reader, not an HTML5 tree
constructor — it handles unclosed `<p>`/`<li>` and stray end tags and nothing more exotic.

- `render()` round-trips **stably** (render → parse → render is byte-identical). `page_replica`'s
  slot addressing depends on that; `_verify_paths` proves it per capture rather than trusting it.
- `SVG_TAG_CASE` / `SVG_ATTR_CASE` restore `viewBox` and `linearGradient` on the way out, because
  an HTML tokeniser lowercases every name and an icon quoted into a prompt gets pasted into files
  that parse strictly.
- `text_content()` puts a space at every block boundary. Without it, an `<address>` ending
  "VIC 3000" followed by a phone link produced `30001300 049 490` — which the phone pattern then
  matched and reported as the client's number.

### `page_structure.py` — the band walk

`extract_page_structure(html, base_url)` off the HTML the CSS parse already fetched
(`DesignTokens.html` exists for this), so it costs no request and no credit. Renders as the
`## Page structure` section of the **full** DESIGN.md only — never the theme brief, which carries
the opposite instruction about layout.

- `STRUCTURE_HEADING` is public because `generation._brand_token_block` decides whether to append
  `_PAGE_STRUCTURE_DIRECTIVE` by looking for that string **in the sheet it is about to send**. Derive
  the flag from the document, never from a separate boolean: that is what makes it impossible for
  the directive to name a section the document lacks, which is the bug this pipeline keeps having.
- Contacts are read in three passes — `tel:`/`mailto:` links, then those values wherever else they
  appear, then a pattern scan **only if no link of that kind was found**. The scan is a fallback,
  not an addition: run alongside the links it returned `3000 1300 049 490` (a postcode fused to the
  number) and the company's ABN as extra "phone numbers", and four candidates of which three are
  wrong is worse than one.
- A band's grid is its **shallowest** repetition, not its largest. Selecting by count reported a
  services band as "6x li" because the bullets inside the cards outnumbered the cards.

### `page_replica.py` — the template

`capture_page_template(url)`: free fetch first, `context_dev.scrape_html` (1 credit) only when the
served HTML is a JS shell. Cached once per run under the `page_replica_template` context key.

- A slot is a **text node**, addressed by a path of child indices — not an element, and not a
  `data-` marker. `<p>Call <a href="tel:…">1300 049 490</a></p>` is one element with no block
  children, so element-level replacement destroys the phone link; and injecting marker `<span>`s
  shifts `:first-child`, `+` selectors and flex child counts on a page that has to be pixel-exact.
- **Attributes are never slotted.** Every `src`, `srcset`, `href`, `alt`, `class` and inline `style`
  survives because there is no mechanism by which the assembly could change one. That is the
  guarantee behind the client logo strip, the icons and the `tel:` href — not an instruction.
  It costs `<meta name="description">` and `alt` text, which carry over unchanged; the document
  `<title>` is a text node and is offered. Do not puncture the invariant for one attribute.
- Locked slots (phone, email, nav labels, brand mark, `<option>` values, postal address, footer
  legal) are **absent from the deck**, not flagged in it. A model that cannot see an id cannot
  address it by accident.
- The model receives the deck (3-6KB), never the template (100-400KB), and returns JSON. So
  `stop_reason=max_tokens` mid-page cannot happen, and the call runs at `low` effort.
- `apply_copy` returns a **report**, not just a document: filled/kept counts, rejections, overflow
  warnings, and structural checks (band sequence, contact links, logo files, SVG count). Nothing is
  fatal — a slot that cannot be written keeps the client's wording, because failing the whole
  assembly over one bad id throws away 93 good lines.

### Rules

- **Copy first, shape second.** Slot paths are child-index chains that any removal or permutation
  invalidates. `_apply_structural_changes` runs after substitution for that reason.
- **The shape channel is opt-in and stays that way.** `allow_structural_changes=False` is the
  default and the product decision. It exists because the CRO prompt's job is genuinely to *change*
  a page (cut a weak band, move proof above the fold), and only `hide`, `trim` and `reorder` are
  offered. There is deliberately **no `grow`** — a cloned grid cell arrives carrying the copy of the
  cell it came from. A model never told the ops exist cannot request one.
- **Cloning is gated on whose page it is.** `_replica_refusal` in `app/routers/pipeline.py` refuses
  a URL whose host is not the client's own; matching a palette is fine against any reference,
  reproducing markup is not. With no client domain on record it allows the capture — refusing on an
  absent field rather than a detected problem would block the ordinary run.
- **Do not re-derive stored slot ids.** `_template_from_stored` rehydrates them. Re-walking would
  give the same ids today and different ones the moment the slot rules change, writing one line's
  copy into another on every previously captured run.
- **`scraper.py` still drops `<nav>` and `<svg>` on purpose.** That is correct: it feeds *copy* to a
  CRO audit, where a menu is link soup. The nav and the icons now reach the model properly, through
  the structure walk and the template. Do not "fix" the scraper.
- The template is not inheritable down `source_run_id` — unlike `brand_design_tokens`. A brand is
  shared between a parent page and its sub-services; a template is one specific document.

### Routes

- `POST /pipeline/replica` — template preview for any URL. 0-1 credits, stores nothing, no domain
  check (looking is not copying). Returns the deck and every slot, locked ones included — the locked
  list is where an operator checks the phone number really is held.
- `GET /pipeline/runs/{run_id}/replica` — what was captured. Free, safe to poll.
- `POST /pipeline/runs/{run_id}/replica/assemble` — re-assemble after editing the draft. One billed
  call.
- `GET /pipeline/runs/{run_id}/replica/output` — the assembled page. Free.
- A replica stage's SSE stream emits `replica_template`, then `replica_start`, then `replica` (the
  report; the HTML is stored, not put on the wire) or `replica_error`. An assembly failure never
  reports the stage as failed — the stage's own document is already complete.

---

## Slide decks — the webinar's Step 5, as a file

`universal-webinar-prompt.md` Step 5 writes a slide-by-slide brief "for the designer or presenter",
and that is where it stopped: the operator read a description of fourteen slides and then built
fourteen slides by hand. `app/services/slide_deck.py` does that transcription — parse the brief,
emit a real `.pptx` with speaker notes, on the client's palette.

It is **not** a designer. Title, body and notes on the run's colours, and nothing else. The brief's
`Visual Direction` ("split-screen symptom vs cause", "4-icon row") describes artwork nothing here
can draw, so it is carried into the **speaker notes verbatim** rather than approximated — a slide
that says what it should look like helps whoever opens the file; a slide that guesses is one they
have to undo.

### Two input formats, both real

The prompt specifies a key/value block per slide. What real runs actually produced is a Markdown
**table** — see `manual_execution/Webinar-Package_TrafficRadius_CompetitorSynthesis.md`, one wide
table with a row per slide. A parser written only to the spec returns nothing on the output the
pipeline has actually been generating, so `find_slide_decks` tries the table first and falls back to
blocks, and `tests/test_slide_deck.py` pins each against a real sample rather than a fixture written
to match the parser.

Other things the parser must not get wrong, each with a test:

- **The section ends at the next heading of the same level or shallower.** Step 6's registration
  copy is not slides.
- **The heading is matched on the phrase, not the number.** Real outputs have called it both
  `STEP 5 — SLIDE DECK BRIEF` and `PART 4 — SLIDE DECK BRIEF`.
- **`(none — single line only)` is an instruction, not a bullet.** A slide reading "(none)" is worse
  than an empty one.
- **Body splits on line breaks, `<br>`, bullet glyphs and semicolons — never on commas or full
  stops.** "9-day approval, 2-day news cycle." is one line on one slide; splitting it invents two.
- **The deck is named from its enclosing heading**, skipping `PART`/`STEP`/`OUTPUT` headings, which
  name a step rather than a webinar.
- **One document can hold several decks.** `webinar.json`'s first field is a *programme* — the
  prompt builds one package per topic, so Steps 3-8 repeat. That is why the route offers a zip.

### Brand

`brand_from_design_md` reads `colors.primary` / `surface` / `on-surface` and the first
`fontFamily` out of the run's DESIGN.md front matter — hand-parsed, like everything else that reads
that block, so nothing in the runtime gains a YAML dependency. Colours are quoted there (a bare
`#a4d36b` opens a YAML comment), so the quotes come off here.

Defaults are **neutral greys, never an invented brand colour** — the failure `design_tokens.py`
documents for generated pages applies identically to a deck. A webinar is a `BRAND_THEME_STAGES`
member and may legitimately have no captured design at all, so a missing or malformed palette never
blocks the download: withholding the deliverable to protect a colour is the wrong trade.

### Routes

- `POST /pipeline/slides` — what decks this document holds. **Free, builds nothing.** The UI asks
  before offering the button, so a webinar whose Step 5 never ran shows no row rather than a row
  that 422s when pressed.
- `POST /pipeline/slides/pptx` — the bytes. A `.pptx`, or a `.zip` when the document holds several
  and no `index` was named. Stores nothing: a deck is cheap to rebuild, so caching it would only add
  something to invalidate.

Both take the document `text` in the body rather than reading it off the run, so the button works on
an **unsaved draft** exactly as `Download` does — an operator who wants the deck is as likely to
want it before approving the stage as after. `run_id` is optional and only ever used to look up the
palette.

### Rules

- **The UI never decides for itself whether a brief exists.** `SlideDeckMenuItem` probes the route
  and renders nothing until decks come back. Grepping the text in the browser would put the
  detection rule in two places, and the one that matters — can it be *parsed* into slides — is the
  server's. The probe costs one request per menu-open, because `OverflowMenu` only renders its
  children while open.
- **`python-pptx` pulls `lxml`.** That is for OOXML only. The HTML rule still stands: `html_dom.py`
  stays on stdlib `html.parser`, and nothing may start parsing HTML with lxml because it is now
  installed.
- Pinned in both `requirements.txt` and `pyproject.toml`, like every other runtime dependency.

---

## SociaVault — live social post data for Stage 10

`social_content_strategy_audit`'s `raw_post_data_source` field asks for real posts with real
timestamps and engagement counts, and offers exactly two ways to supply them: an exported CSV/XLSX,
or "research live via browser" — a model reading a scrape or a screenshot and guessing at like
counts, which is the same invented-measurement failure `design_tokens.py` documents for a palette
and `image_briefs.py` for a photo, one platform over. `app/services/sociavault_client.py` is a real
third option: [SociaVault](https://docs.sociavault.com/) is a paid, structured API over Facebook,
Instagram and LinkedIn's public data, keyed by `SOCIO_VAULT` in the backend `.env`.

### The wrapper

**`app/services/sociavault_client.py` is the only module that talks to SociaVault.** Same shape as
`firecrawl_client.py` and `brandfetch_client.py`: a cached client, `is_configured()`, one exception
type, and `fetch_recent_posts(platform, handle, *, limit)` normalising all three platforms' very
different raw shapes into one `SocialPost`. A field a platform genuinely does not publish — LinkedIn
has no documented like/comment count — is left `None`, never `0`; printing `0` would claim a real
measurement of zero engagement, which is a different, false claim.

Pagination is capped at `_MAX_PAGES_PER_ACCOUNT` regardless of `limit` or platform: Facebook returns
3 posts/page against Instagram's ~12, so a same-sized sample costs roughly 4x the credits, and the
cap bounds worst-case spend either way rather than looping until a very long history runs out.

### `social_audit.py` — parsing the intake field and rendering the document

`parse_account_handles` reads the exact free-text shape `client_s_own_social_pages_handles` already
uses ("Facebook: /trafficradius, Instagram: @trafficradius, LinkedIn: /company/trafficradius") —
the same text is typed for a competitor. A platform this module has no endpoint for (YouTube,
TikTok, Pinterest, ...) is silently skipped, not an error: naming one is a normal thing for the
field to do, and `render_social_data_md` reports it as "provide manually" rather than failing the
whole fetch.

`render_social_data_md` builds the document the field's own instruction requires: a stated sample
size, a stated collection method, and a `more_available` flag rather than presenting a sample as a
full archive. This is prose for a prompt to read, not a token schema like `DESIGN.md` — nothing
downstream references a value here by name.

### The prepass — why the field doesn't need a manual fetch first

Attaching a real answer to `raw_post_data_source` before running Stage 10 is optional, not the only
path in: `_run_social_data_prepass` (`app/routers/pipeline.py`, alongside `_run_competitor_prepass`)
runs automatically inside `_generation_sse_stream` whenever the stage is `social_content_strategy_
audit` and the field is blank or asks for "research live" — the same guard shape the competitor
prepass uses for its own target field. It parses `client_s_own_social_pages_handles`, fetches the
client's real posts via `sociavault_client`, and fills the field before the stage's own prompt is
built, exactly the way a Context.dev capture fills `DESIGN.md` before a replica stage reads it.

**A real answer already supplied is never overwritten.** An operator who attached a CSV, or pasted
actual post data, keeps it — the prepass only fires on blank or "research live" wording, matching
the exact "don't clobber what's already there" rule every other prepass in this codebase follows.

### Competitors — resolving a domain to a handle, Firecrawl first

`competitor_list` names a company and a domain, never a social handle, and a domain is not
something `sociavault_client` can look up. `social_audit.resolve_competitor_handles(domain)` is the
missing step: almost every business site links to its own social profiles somewhere (a header icon,
a footer, "follow us"), so reading those links off the page turns a domain into a real, fetchable
handle.

**Firecrawl first, Context.dev as the fallback for whatever Firecrawl leaves unresolved** — the
reverse of `design_md.py`'s usual Context.dev-primary ordering, by explicit choice for this job:
this is a one-off per-competitor lookup rather than the once-per-run brand capture Context.dev's
own credit cost is weighed against there, and Firecrawl's `extract_social_links` (a `/extract` call
against a small fixed schema, same shape as `extract_brand_tokens`) is the cheaper of the two.
Context.dev's `retrieve_brand` — 10 credits, already used elsewhere in this codebase for a domain's
brand record — only runs for platforms Firecrawl didn't find, and never overrides one it did; same
"each tier only fills what the one before it left open" rule `design_md._capture_brand_fallback`
already follows for brand tokens.

**Automatic, but bounded — `_MAX_AUTO_COMPETITORS` (5), independent of what the operator typed
into `number_of_competitors_to_audit`.** Every competitor resolved this way costs a real Firecrawl
or Context.dev call before SociaVault can even be asked, on every stage run, so the cap is fixed
rather than following an operator's own dial for a decision this prepass makes without asking. A
run naming more competitors than the cap gets a note saying so; `POST /pipeline/social/posts` still
covers a 6th, 7th, ... competitor by hand. Competitor SociaVault fetches also use a lower sample
size than the client's own (`_COMPETITOR_LIMIT_PER_PLATFORM`, 15 vs. 40) for the same reason: this
cost is now multiplied by up to five accounts on up to three platforms each.

**`competitor_list` is prose, not JSON — read it with `parse_competitor_listing`.** This has broken
once. The field resolves from the approved listing's context entry, which `save_competitor_stage`
writes as `to_prompt_text`'s prose; the search's raw JSON never reaches it. The prepass used to call
`parse_analysis` (JSON only), so on every real run the list failed to parse and every competitor was
`N/D` — while the tests, which fed it JSON, passed. `parse_competitor_listing` (beside
`to_prompt_text` in `competitor.py`) reads JSON, then that prose, then any line naming a website (a
hand-typed list), and skips the benchmark header, whose URL is the client's own site.
`test_competitors_are_fetched_from_the_listing_as_the_stage_actually_receives_it` feeds it the real
writer's output.

**Still legitimately `N/D` when it is.** A competitor whose site links to no discoverable social
profile, or whose `competitor_list` entry could not be parsed for a domain at all, gets a stated
"could not be resolved" line rather than a silently-omitted section — the stage still correctly
marks that competitor's post-level metrics `N/D` per its own brief rule, and the document says why.

Emits a `social_prepass` SSE event (`skipped`, and either `content`/`accounts` or `error`) so the
transcript shows what was fetched, the same shape the `prepass` event already has for competitors.

### Routes

- `POST /pipeline/social/posts` — `{label, handles_text, limit_per_platform}` → real posts fetched
  and rendered to markdown. What the prepass calls internally for the client; also usable directly
  to preview one competitor's data by hand if the operator has that competitor's own handles.
  Unattached to a run and stores nothing, like `POST /pipeline/design`.
- `GET /pipeline/social/status` — free, for the UI to decide whether to offer a manual-fetch button.

### Rules

- **Credits are money.** `limit_per_platform` is the credit dial, same role `num_results` plays on
  `POST /intel/search` — leave it at the default unless a stage genuinely needs a longer sample.
- **A missing key is a 503, a bad handle is a 422 (or a per-account note), matching `/intel/*`.**
  `fetch_account_snapshot` degrades one account/platform at a time — one private or stale handle
  must not cost the rest of the audit, the same rule `image_briefs.generate_all` already follows
  for a content-policy rejection on one brief.
- **Tests never hit the live API.** Patch the module-level functions in `sociavault_client` (routes,
  `social_audit`) or `sociavault_client._client()`. See `tests/test_sociavault_client.py`,
  `tests/test_social_audit.py` and `tests/test_social_data_prepass.py`.

---

## Phase 2 — a sub-service, and why it writes its own copy

Phase 2 builds the same stack one level down, for a single *sub-service* (Google Ads, Meta Ads,
LinkedIn) of the headline service Phase 1 covered. It is expressed as a **delta** over the Phase 1
definitions rather than as a second set of tables — `PHASE2_OVERRIDES` in
`app/services/generation.py` on the server, `DELTAS` in `data/phase2Catalog.ts` in the UI — because
seven near-identical field registries kept in step by hand is how a field added later silently fails
to reach its Phase 2 twin. `tests/test_phase2.py` pins the delta landing.

### Its own ICP is stage 01: Phase 2 inherits the client, not the audience

Phase 1's ICP is built from the client's headline service ("Social Media Marketing"). The buyer of
one sub-service ("Meta Ads") is often a different person, with a different trigger, budget line and
set of alternatives. So Phase 2 opens with an ICP stage of its own, and **Phase 1's ICP and value
ladder never reach a Phase 2 run.**

- **The prompt is `Phase2/ICP_phase2.md`: `ICP.md` plus exactly two edits.** The service input is
  relabelled "Sub-Service + Price/Terms", and a SUB-SERVICE SCOPE section is added before the context
  rules. `test_phase2_prompt_is_phase1s_plus_exactly_two_edits` reverses both edits and requires the
  result to equal `ICP.md` byte for byte. So an improvement to the Phase 1 prompt that isn't carried
  across fails a test instead of drifting, which is the SCOPE LOCK lesson applied in advance.
- **The sub-service answers the service input** (`fromSubService` in the `icp` delta). The answer
  stays editable, which is where a price or terms get added.
- **The cut is in two places, over the same three keys** (`icp`, `offers`, `offer_ladder`):
  - On the server, they are in `PHASE_SCOPED_CONTEXT_KEYS`, so `_latest_context_entry` never walks
    them down `source_run_id`.
  - On the client, they are in `PHASE1_ONLY_CONTEXT_KEYS`, so `phase2StartingContext` drops them from
    the in-memory copy a Phase 2 leg starts from.
  - Both halves matter. Without the server half, a Phase 2 stage started out of order would quietly
    read the parent's ICP. Without the client half, the in-session copy would supply it before the
    database was ever asked.
- **What still crosses is client-level**: brand design tokens, `cro_client_settings` and
  `industry_profile`. None of it changes per sub-service, and re-asking or re-capturing it would cost
  CRO's 28 inherited questions and 17 Context.dev credits per run.
  `test_client_level_documents_still_inherit` pins that line.
- **Offer Ladder has no Phase 2 stage.** Lead Magnet's and SMS's offer-ladder inputs are optional and
  now ask or go without, rather than borrowing a ladder priced for the headline service's buyer.
- **The audience's industry is asked fresh, per run.** ICP's `industry` ("of the ICP you are
  targeting"), Lead Magnet's `industry` and Content Marketing's `industry_niche` all describe the
  audience.
  - In Phase 1 they share the run-level `industry` fact.
  - In Phase 2 they map to `PHASE2_AUDIENCE_INDUSTRY_FACT` instead, stored per run under
    `phase2AudienceIndustryKey(runId)`, because each sub-service track has its own ICP and its own
    audience. It is empty when the Phase 2 ICP asks, so the ICP asks it rather than reusing Phase 1's
    answer. The later stages in that run then reuse it.
  - A Phase 2 competitor search is scoped to that audience once it exists (`competitorNiche`).
  - This is different from the *client's* industry (`industry_profile`), which drives the voice pack.
    That one is inherited, and the industry question at the end of the ICP intake moves straight on.
    `smoke/phase2icp.tsx` pins the difference.

### The CRO stage follows it, and that is the point

The Phase 2 Pillar Page prompt (`Master_Prompt_Universal_Page_Design_v1_phase2.md`) is a **design
replicator with no service input of any kind**. Read its INPUTS: there is no service field, no topic
field, no keyword field. It lays out whatever copy it is handed — so **the copy handed to it is the
subject of the page**, and nothing else on that stage decides what the page is about.

While Phase 2 had no CRO stage, that copy could only come from two places, and both were wrong:

- the parent Phase 1 run's `cro_rewritten_copy`, which is the rewrite of the client's *headline*
  service — so a run carefully pointed at "Meta Ads" came back a Social Media Marketing page; or
- the operator, who was asked to write the page copy themselves and paste it in (an `askOutright`
  on `improved_page_content`, since an inherited document that is always wrong must not sit under a
  "use it" button).

So Phase 2 runs `cro` straight after its ICP. It starts from the Phase 1 prompt — that file already opens "works for
any industry, any sub-service, any page scope" — and drops nothing, and it writes the four keys the
rest of the phase reads: `cro_rewritten_copy`, `cro_locked_sections`, `cro_terminology_map` and
`cro_audit_findings`. Pillar Page's copy field then fills from the Context Store like any other
document approved two stages ago.

**It always runs in NEW PAGE mode**, which is not a limitation but the actual situation: this phase
exists precisely because the client has no page for the sub-service yet. `PHASE2_CONSTANT_ANSWERS`
in `pipeline/pipelineData.ts` seeds both sentinels off `NEW_PAGE_OPTIONS` rather than restating
them, so the strings stay the ones the prompt switches build-from-scratch mode on, plus
`page_scope: "SUB-SERVICE"`.

### The SCOPE LOCK — why the prompt file is no longer a copy

Naming the sub-service is not the same as writing about it. `target_service_or_sub_service` is one
line of an INPUTS block, and part of that block's strategy half arrives at the *parent's* scope by
construction: Proof Assets Available, the outcome words and the tone of voice come from the parent
run's `cro_client_settings`. Handed parent-scope material and a one-line sub-service name, a model
writes the page most of its input describes. That is the Social Media Marketing page this stage
exists to prevent, arriving by a different route. (The ICP used to be the largest such input. It is
now built in-run for the sub-service, and the lock says so.)

So `Master_Prompt_Universal_Page_Rewrite_v1_phase2.md` carries a **SCOPE LOCK** section, immediately
after ROLE, that Phase 1's file does not. It names the input that decides the subject; bars the
parent's term from the H1, title tag, meta description, URL, primary keyword, offer name and CTA
while keeping it reachable as the internal link up and as the term not to compete for; and splits
each inherited input into what to **keep** (properties of the client and the buyer), what to
**re-point** at the sub-service (the outcome, objections, proof and decision criteria) and what to
**discard**. Step 0 makes the model state the subject and every re-pointing before it writes. Step
1B uses this run's ICP as written, but still re-points one the operator replaced with a parent-scope
document. Rule 8 says what "keep the existing H1 intent" means in a
mode where there is no existing H1, and Part 0 reports the lot back to the operator.

This is the same fix `design_tokens.py` documents for colours: an instruction that cannot be
followed gets filled in by the model, so measure and constrain rather than exhort. The two files
were **byte-identical** before the lock landed, which is the risk worth naming — re-copying Phase
1's over Phase 2's still builds, still renders INPUTS, still produces a polished page, and the page
is about the wrong service. `tests/test_phase2_cro_scope.py` is the only thing that notices.

**The ICP card on CRO is Phase 1's card again.** It is this run's own stage 01 document, so Phase
1's help text ("the ICP generated in this run") is true, and the `rewordHelp` that used to say
"from the parent Phase 1 run" is gone. It stays `overridable`, so an operator with commissioned
research can replace it. `smoke/phase2cro.tsx` pins the card and its wording.

### `cro_client_settings` — asking once per client, not once per service

A stage that asks thirty-seven questions has not solved the copy problem, it has moved it. Most of
CRO's intake is not about the page at all: buyer type, sales motion, geo mode, claim substantiation
tier, pricing disclosure mode, whether testimonials are permitted, and the five resolved vocabulary
words are properties of **the client**, settled once during Phase 1.

They used to be thrown away. `field_sessions.resolved_fields` exists in the schema for exactly this
and nothing has ever written to it; what actually crosses from a parent run into its Phase 2
children is `context_entries`, resolved down `source_run_id`. So `app/services/cro_settings.py`
composes those answers into a document and `save_stage` files it under `cro_client_settings` —
a `ContextEntry` like any other, read through the inheritance every other document already uses.
No new table, no new resolver.

- **The server composes it, not the model.** That is why it is in `SERVER_COMPOSED_CONTEXT_KEYS`
  (`data/assetCatalog.ts`) and excluded from the post-save context fan-out: pointing the key at the
  80KB rewrite would hand the sub-key extractor a haystack instead of the one-line-per-setting sheet
  it is written against, and whatever it dug out of the prose would then be believed over the real
  row in the database.
- **`capture()` returns None rather than an empty document.** `_latest_context_entry` takes the
  highest version, not the fullest, so an empty row written by a stage approved from a pasted draft
  would shadow the real one a later re-run produces.
- **Page-specific answers are deliberately not captured.** The four `locked_*` fields are the
  sharpest case: a locked heading is locked on *that page*, and carrying one across would pin a
  section the sub-service page has no reason to carry.
- **The fields are patched, not rebuilt.** `planField` takes the context route on
  `kind === "context_reference"` *or* `source === "auto_from_context"`, so moving the source alone is
  enough — and the original `kind` survives, which matters because seven of these are `enum_choice`
  and `QuestionWidget` renders its pills on `kind`. Rebuilt, a parent run with no settings document
  would ask the operator to hand-type "2 PROFESSIONALLY REGULATED".
- **Absent settings fall back to asking, never to blank.** A parent run approved before this key
  existed has no settings row, so the lookup falls through to the producing stage's own document and
  usually finds nothing — and asking is the right answer there. The legal guardrails are in this set;
  an unanswered claim tier is not a default.
- **Four files have to agree** — the captured list, `CRO_CLIENT_SETTINGS` in `data/phase2Catalog.ts`,
  the `cro_client_settings` block in `lib/contextSubKeys.ts`, and the rendered shape itself.
  `tests/test_cro_settings.py` parses all of them; `smoke/phase2cro.tsx` runs the real extractor over
  the real rendered shape from the other side. Drift here has no symptom: the extractor finds
  nothing, the field falls back to asking, and Phase 2 quietly goes back to asking twenty questions
  nobody noticed it had stopped asking.

The net: **Phase 2's CRO stage asks 9 of its 37 fields**, and all nine are page- or run-specific —
the parent pillar URL, sibling pages, existing ranking keywords, the four locked blocks, the CRO
framework and free-text notes. Two more (this run's ICP and its competitor listing) stop
as accept-or-replace cards rather than questions, which is `overridable: true` behaviour Phase 1 has
too. `smoke/phase2cro.tsx` walks the intake end to end and names that list, so a field joining it
fails a test rather than landing on an operator.

### Competitor research

Four of the eight stages research competitors and **all of it is gated** — reviewed on its own card
before the stage it feeds, never folded invisibly into a generation call
(`PREPASS_BY_MAIN_ASSET_BY_PHASE["phase2"]` is empty and stays empty). `{SERVICE}` resolves from the
run-level `sub_service` fact rather than from any stage's intake, which is the whole point of the
phase split: the search returns competitors doing *Google Ads* lead magnets, not marketing ones.

`01_CRO_phase2.md` searches for competitors' **pages for the sub-service** — a real page with its own
proposition, process and proof — rather than for a CRO offering the way Phase 1's file does, because
what the rewrite is benchmarked against is the page, not the vendor. Its target-URL tuple omits
`existing_page_url`, which Phase 1 has second: in Phase 2 that field is the NEW PAGE sentinel, so
reading it would search the market for the phrase "NEW PAGE — no existing URL".

---

## Industry personality — one voice block per client, and advice about which stages to run

Every prompt in `assets/Prompts/` is industry-agnostic, and until this existed nothing told the model
how a regulated lender should sound next to a Shopify store. The pipeline's own voice, a marketing
agency's, bled into every client. Every client was also walked through all fifteen stages, Blog and
Webinar included, whatever it sold.

### The industry is the client's own, inferred then confirmed

- **Whose industry.** ICP's `industry` field asks for the *target customer's* industry. A lender
  whose customers are builders is still bound by financial-promotion rules, so the classifier is
  shown that answer labelled as "a hint only".
- **When it is asked.** At the end of the first stage's intake (ICP, normally), before anything
  generates. `advanceIntake` pauses on `resolveIndustry`, which tries three things in order:
  1. A value the run already holds. A Phase 2 leg inherits its parent's through `source_run_id`.
  2. A guess to confirm. `POST /pipeline/industry/infer` makes one Haiku call and does a **free**
     direct read of the home page's title, never a paid reader.
  3. An outright question in a text box, when there is no guess at or above `MIN_CONFIDENCE`.
- **Typed answers.** A typed answer, or one given through "Other: specify", is mapped server-side.
  It falls to `general` when nothing fits, and the operator's own words are kept as `label`.
- **Where it is stored.** The confirmed answer is filed by `POST /pipeline/runs/{id}/industry` as a
  `ContextEntry` under `industry_profile`. It is server-composed, listed in
  `SERVER_COMPOSED_CONTEXT_KEYS`.
- **Changing it.** "Change" re-asks it between stages or mid-intake. The change applies to stages
  generated from then on. Saved stages keep the voice they were written in.

### The voice block

- **Where it goes.** `industry_voice.voice_block` leads the tail of every stage's prompt in
  `generation._prompt_parts`, in both phases, and also leads the refine prompt.
- **Why there.** It sits after the cached reference library, so the 1h prompt cache still hits,
  and before the brand tokens and INPUTS.
- **What it contains.** One pack file per bucket in `assets/voice_packs/`, covering tone,
  compliance posture, vocabulary and how aggressive claims may be. Every block carries the same
  precedence clause: **INPUTS win**. A tone, claim tier or testimonial permission the operator
  settled in CRO overrides the pack's default.
- **`marketing_agency` is the default and renders no block at all.** A run that never sets an
  industry sends every prompt byte-for-byte as before;
  `test_default_bucket_and_no_profile_leave_every_prompt_byte_identical` pins that for every stage
  in both phases. It has no pack file, on purpose.
- **Never fork prompt files per industry.** Phase 2's SCOPE LOCK is the lesson: two files that
  start identical drift silently. One block composed in one place cannot drift.

### Stage advisories: advice, never removal

`INDUSTRY_PROFILES` in `data/industryProfiles.ts` lists, per bucket, the stages it rarely needs and
why.

- **The advisory card.** When the forward walk reaches one (`proceedToStage`, called from
  `saveStage`), it pushes a `stage-advisory` card. **Skip** is the primary option, following the
  `NEW_PAGE_OPTIONS` pattern, and **Build it anyway** sits beside it.
- **What a skip is.** A skip is recorded on the card itself. `skippedAssetIds` reads it off the
  transcript the way `approvedAssetIds` reads approvals, so a skip needs no state of its own and is
  cleared by `clearPhase`.
- **What a skip is not.** It never marks the stage done. The diagram shows "Skipped", and
  "Start here" still builds the stage.
- **Only the forward walk is advised.** "Start here", a gate, a re-run and a resume are the operator
  having already chosen the stage.
- **A skip must never strand a later stage.** A bucket may not advise against a stage that a stage
  it still recommends depends on. Book is built from Webinar, so the two are always listed together.
  `test_no_advised_against_stage_is_a_prerequisite_of_a_recommended_one` enforces this against
  `dependencies.py`.

### Three files have to agree

1. `BUCKETS` in `app/services/industry_voice.py`.
2. One pack per bucket in `assets/voice_packs/`.
3. `INDUSTRY_PROFILES` (keys *and* labels) in `data/industryProfiles.ts`.

The confirm card answers with a label and the store maps it back to a bucket by that label, so a
label that drifts matches no bucket at all. `tests/test_industry_voice.py` and
`tests/test_industry_profiles_agree.py` check all three; `smoke/industry.tsx` drives the UI half.

**Not yet built (Phase C).** Per-bucket default answers for repetitive CRO intake (claim tier,
testimonials, pricing disclosure), offered as accept-or-replace. Deferred because the claim tier is a
legal guardrail, and a default there is a decision someone has to own.

## Deliverables, shown visually

The reader (`pipeline/AssetReader.tsx`) opens every approved asset in a **Visual** view, with a
**Text** toggle that shows the document exactly as exported. The preference is `uiStore.readerView`,
remembered like `docView`.

The structure was always in the documents. CRO scores its layers N/10 and the page /100, the
ladder's offers carry `- Price:` and `- Ascends To:`, SMS messages carry `**Timing:**`, the lead
magnet has a `Total /25` scorecard, and the webinar has a timed run of show. The Visual view draws
that structure. It is a **re-layout, never a summary**.

### The two guarantees, pinned by `smoke/visual.tsx`

- **Partition.** `parseVisualBlocks` (`lib/visualBlocks.ts`) splits a section into blocks, and each
  block records the exact text it consumed. The sources rejoin to the body byte for byte, and
  anything no recogniser claims is a `markdown` block rendered as before.
- **Containment.** Every recognised block renders every word of its source. This is the
  word-multiset check `smoke/plan.tsx` uses for the mind map. A bar is drawn **beside** "6/10",
  never instead of it, so no meaning is carried by colour alone.

Both are run over **every real output in `manual_execution/`**. A recogniser that drops a word
fails there, whichever document it happens on.

### Recognisers, and table classification

- **Recognisers, in order:**
  - tables;
  - score lines (`**Score: 58/100**`, drawn as a gauge);
  - Value Ladder offer items (drawn as offer cards);
  - runs of three or more timed slots (`- 0:00–0:03 — …`, drawn as a run of show);
  - runs of same-level headings that are all timed (drawn as a sequence);
  - runs of three or more whole-line bold leads with content (drawn as cards).

  Fenced code is never parsed, so the funnel's ASCII flow stays as written.
- **`classifyTable` decides a table's visual. The order is the specification:**
  1. kanban, when at least 70% of rows are P0–P4 or a tier. "Ongoing" becomes its own column, and a
     priority table with a Timeline column is still a kanban;
  2. a rubric (`Total /25` over bare numbers, each criterion's maximum shared from the total);
  3. score cells (N/10, %, ★, HIGH/MED/LOW, ✓/⚠/✗);
  4. a time column, drawn as a timeline;
  5. Stage/Page plus goal or source columns, drawn as a flow;
  6. Metric/KPI plus Target/Baseline, drawn as KPI tiles;
  7. anything else is a plain table, rendered exactly as the Text view does.

**"At a glance"** (`lib/assetGlance.ts`) is derived and additive. It picks one picture per asset from
the same blocks: a CRO score with layer bars, ladder rungs with offer counts and headline price
ranges, the funnel's stages, the lead magnet's winning concept, a sequence's steps, a run of show,
or the Plan of Action's existing `PlanMindMap`. Each item jumps to the section it came from. The
Deliverables cards show the same glance, card-sized (`MiniGlance`), so a card and the document it
opens never disagree.

### Rules

- **Exports never see any of this.** They read raw `message.text`.
- **No chart library.** Everything is hand-built divs and SVG on the `var(--…)` tokens, so dark mode
  needs nothing of its own.
- **Adding a recogniser.** Claim contiguous lines only, keep every word in what you render, and add
  a real-sample recognition assertion to `smoke/visual.tsx`. The corpus checks will then tell you if
  it loses anything.

## Checking a draft against the business — offers, funnel, business rules, virality

Every stage writes a polished document, and nothing used to check it against the client's actual
business: `save_stage` never reads the content. So an offer ladder could sell a service the client
doesn't offer, a lead magnet could quote a price nobody set, and nothing estimated whether a post
would travel. `app/services/asset_check.py` checks every finished draft, **automatically and
advisorily**.

### The facts it checks against — `business_facts.py`

`load_business_facts` reads what the run already holds, through the usual inheritance:

- **The offer catalogue**, from `offer_ladder` (or `offers`).
  - `parse_offer_ladder` parses it deterministically, because the Value Ladder prompt fixes the item
    format (`N. **[Format] "Title"**`, then `- Price:` / `- Estimated Value:` / `- Ascends To:`).
  - A repeated offer is merged into its first description.
  - `test_business_facts.py` parses the real ladder in `manual_execution/`.
- **Pricing, claim tier, testimonials, words to avoid and tone**, from `cro_client_settings.fields`.
- **Industry**, from `industry_profile`.
- **ICP and funnel**, as text for the judge.
- **Keyword volume**, from `keyword_clusters`.
- **Social engagement**, from `social_post_sample`.

**A fact that isn't there is listed as missing, never guessed.** A price check with no prices on
record doesn't run; the panel says "not on record, so not checked".

### Measure, then judge

1. **Rules first: free and deterministic.** They are run against the facts and cover:
   - a price that isn't on the ladder or in the pricing facts;
   - any price at all under disclosure modes D or E;
   - a routing asset (funnel, lead magnet, SMS, webinar) that names no real offer;
   - words-to-avoid hits;
   - ratings and attributed quotes when testimonials are NO or UNSURE;
   - absolute claims at claim tier 2 or above;
   - unsourced statistics and placeholders, as `info`.
2. **One judge call** (Sonnet, `low` effort) for what the rules can't decide: whether the offer fits
   the ICP, whether the funnel step matches the buyer's awareness, contradictions, and virality.
3. **Every judge finding must quote the draft verbatim.** `_verified` drops any quote that isn't in
   the text, and the report counts what it dropped. A checker that invents problems is as bad as a
   stage that invents facts.

### The noise floor is the product

These documents mix customer copy with strategist notes, competitor tables and KPI targets. A
checker that flags every "Guaranteed results" quoted from a competitor gets ignored. So:

- Text under competitor, benchmark or market-scan headings is skipped (`_others_spans`).
- Negation is read over the sentence ("results are not guaranteed" is the compliant sentence).
- Ratings require a rating shape (`4.9/5`, not "SMS 4/5").
- The testimonial and statistic rules apply only to `CUSTOMER_FACING` assets.
- Rules report one finding per rule per line.
- `test_real_outputs_raise_no_false_errors` runs the rules over real outputs. **Loosen a rule and
  that test is what notices.**

### Virality is a prediction

Virality is scored against a rubric taken from the headline framework: hook, curiosity,
specificity, emotion, shareability and platform fit. Where the run has real data, it is anchored to
that data: keyword volume for terms the draft uses, and the real top posts' opening lines.

- **The real data is collected at no extra cost.** The Stage 10 social prepass now also files
  `social_post_sample`: `social_audit.engagement_stats` over posts it had already paid for.
  `stats` is taken off the SSE event before it goes to the browser.
- **Missing counts stay `None`**, never 0.
- The report states its basis: `benchmarked` or `rubric only`. The panel labels the score a
  prediction, always.

### The UI

- **When it runs.** `runAssetCheck` runs after every clean stream in `streamIntoMessage`
  (generation, retry, refine). A truncated or failed draft is not checked, and a retry clears the
  old report.
- **What the panel shows.** `AssetCheckPanel` sits **above** the Approve row and never disables it.
  It opens only when something is an error.
- **Fixing.** "Fix N with Refine" sends the chosen findings to `submitRefine` as one quoted note
  (`composeFixNote`). Errors and warnings are pre-selected; info is opt-in.
- **The route.** `POST /pipeline/check/{asset_id}` stores nothing and records its cost as
  `kind="check"`. A judge failure returns the rule findings alone, with `judge: "unavailable"`.

## Running a stage out of order

The pipeline runs fifteen stages in sequence and that is the right default, not a constraint. Only
five assets are ever a hard prerequisite for another (`icp`, `cro`, `pillar_page`, `funnel`,
`webinar`), so "skip to stage 09" usually needs one or two documents rather than the eight stages in
between. `docs/Asset_Dependency_Map.md` is the analysis; `app/services/dependencies.py` is the graph,
read from the same `schemas/drafts/*.json` so the two cannot disagree.

### The two routes

- `GET /pipeline/runs/{run_id}/readiness/{asset_id}?phase=` — free. Per dependency: `ready`,
  `source` (`this_run` | `inherited`), `from_run_id`, `seeded`, `version`, `chars`, `producer`,
  `stored_under`. Plus `blocked`, `run_first`, `seedable`, `writes`, and `prepass`/`prepass_fields`.
- `POST /pipeline/runs/{run_id}/context` — `{context_key, content, note?}`. Writes a `ContextEntry`
  exactly as stage approval does, so every reader downstream needs no special case.

### Rules

- **A document is looked for under two keys.** `save_stage` writes `context_key = asset_id` and
  nothing else, so the four extra keys `cro` publishes have no rows of their own. Probing only
  `cro_rewritten_copy` finds nothing on a run whose CRO stage was approved — the one wrong answer
  that would make readiness worse than nothing, because the operator would re-run a stage they had
  already run. The context key is tried first (a hand-supplied document) and the producing asset's
  id second (an approved stage). `hydrateContextFromDb` resolves the same way round.
- **A seeded entry's `written_by_asset_id` is null.** Pointing it at the producing asset would claim
  `icp` wrote a document `icp` never saw, corrupting the one column that records where output came
  from. The value carries `"seeded": true` instead.
- **Seeding locks nothing out.** Running the real stage later appends a higher version and wins,
  because "latest" is `ORDER BY version DESC`.
- **A key outside `seedable_keys()` is a 422 that lists the valid keys.** Not caution about write
  volume: a mistyped key is accepted silently by the database, and the operator's evidence that they
  had supplied the dependency would be the stage carrying on asking for it.
- **The competitor prepasses are never dependencies.** All ten search the web from inside their own
  stage, so `prepass_fields` is reported apart from `dependencies`.
  `lead_magnet.competitor_lead_magnet_list` is `required` and nothing upstream writes it — counted
  as a dependency it would report that stage as permanently unstartable and send an operator looking
  for a stage that does not exist. `enterGatedStage` hydrates a stored prepass before entering,
  because the decision to re-search is made from the *session* context, which a reopened chat does
  not have; without it a resumed run pays for a listing already in the store.
- **`unresolved_context_key` fields are `manual: true` and excluded from `blocked`.** They are
  questions the stage asks every time. Counted as missing documents, `funnel_hub_media` reports as
  permanently blocked with nothing an operator could do about it.
- **`run_first` is computed against the run, not read off the graph.** A branch stops the moment the
  run holds the document, so seeding `funnel_stages` drops `pillar_page` and `cro` behind it too —
  `sms_sequence` goes from four stages to none.
### Stopping a stage part-way

"Stop this asset" sits above the transcript whenever a stage is in progress — mid-question,
mid-competitor-scan, mid-stream, or on a draft nobody has saved. All four are states an operator can
decide they are done with, so there is one control rather than one per state, and it names the stage
it would stop. `selectCanStop` / `selectStoppableStage` decide both.

- **The stream is genuinely aborted.** `generationRequests` (a module-level map, mirroring
  `headlineRequests`) holds one `AbortController` per streaming message, and `streamIntoMessage`
  returns early when `signal.aborted` — a deliberate stop is neither a truncated draft nor a
  failure, and the old catch would have overwritten "Ready" with "Awaiting Review" of a card no
  longer on offer. Before this the signal was plumbed through `pipelineApi` and never passed, so a
  stop would have left the request running and billing.
- **Nothing approved is touched.** Saved generations and saved competitor analyses keep their cards
  and their Context Store versions — the same carve-out `rerunStage` makes, and what lets the stop
  be one click behind an inline confirm rather than a modal.
- **`rerunReturnIndex` is cleared.** Left set, the next stage to finish would send the operator back
  to the stage they walked out of.
- **Phase 2's two run-level questions are not stoppable.** Which parent run, and which sub-service,
  belong to the run rather than to a stage; the sub-service card is pushed without an `assetId`, so
  the supersede pass would not reach it and the question would be left on screen with no intake
  behind it.
- **The `stage-stopped` card counts as the operator's turn** in `deriveResumeActivity` and
  `selectNeedsResume`. Without that the resume banner appears underneath it offering to continue the
  stage they just stopped.

### Clearing a leg, and the warning in front of every deletion

"Clear chat" sits in the nav, beside the run's status, and it clears **the phase on screen and
nothing else** (`clearPhase`). It is not "New chat": that one keeps the chat it leaves and opens a
second row beside it, which is right for a new client and wrong for a run that went wrong — the
wrong client name three stages back, a Phase 2 leg built on the wrong parent.

**The scope is the whole point.** A chat carries both legs (see `setPhase`), and the ordinary shape
of the work is a finished Phase 1 run with a Phase 2 leg on top of it. An operator restarting the
sub-service is not asking for the fifteen assets it inherits from to be thrown away, so the other
leg's cards, its parked `phaseSlot`, its run and its approved documents are all left where they are.

- **The button names what it would clear.** `selectOtherPhaseHasWork` decides: with work in the
  other leg it reads "Clear Phase 2" and the dialog says Phase 1 stays; only when this leg is the
  whole of the chat does it read "Clear chat", because only then is that what it does. A control
  labelled "Clear chat" that clears one phase is the mislabel `smoke/clear.tsx` exists to prevent —
  and the first version of this feature shipped the opposite bug, clearing both.
- **`selectCanClearPhase` is read per phase.** A chat whose Phase 1 is complete offers nothing to
  clear the moment the operator switches to a Phase 2 leg that has not started. Offered there, the
  control would warn about destroying nothing — and what it would have destroyed was Phase 1.
- **The leg's own stale slot is dropped too.** `setPhase` parks the outgoing leg without removing
  the incoming one, so the phase on screen can still hold a slot from an earlier visit; left behind,
  it restores the pre-clear cursor the moment the operator switches away and back.
- **A cleared leg restarts immediately**, at Stage 01 — `beginStage(0)`, or `beginPhase2` for Phase
  2, which relinks to this chat's Phase 1 run and copies its context exactly as a freshly entered
  Phase 2 leg does. It cannot go back to the welcome screen while the other leg holds work:
  `started` is chat-level, and `setPhase`'s `!started` branch does not consult `phaseSlots`, so
  blanking it would strand the parked leg.
- **The client survives, the sub-service does not.** The client is a property of the chat and the
  other leg is still working on it; `sub_service` is Phase 2's own fact. Clearing the *last* leg
  drops the profile as well — "the client name was wrong" is one of the reasons to clear, and
  keeping it would auto-answer the next run with it.
- **In-flight requests are aborted first**, while `messages` still holds the ids the controllers are
  keyed by, and only this leg's. Same reasoning as `stopStage`, one level up: a clear that only
  emptied the pane would leave the stream running, still billing.
- **Nothing is deleted from the database**, and the chat's history row is never removed — deleting a
  chat is the sidebar's job, behind its own warning. `persistNow` therefore writes a chat that
  already has a row even once it is no longer `started`, so an emptied chat does not come back on
  the next reload.
- **Withdrawn while the sample transcript is up.** What is on screen then is fixtures and the run it
  would clear is the operator's real one — the one confusion `demoMode` exists to prevent.

Deleting is always a modal — `ConfirmDialog`, used by the nav's clear and by the sidebar's trash
icon, which used to delete on the first click. It names the chat or the phase, says what stays, and
focuses **Cancel**, so Enter on a dialog that appeared under the operator's fingers backs out. The
inline confirm on "Stop this asset" stays inline on purpose: that one abandons work in flight and
touches nothing approved.

`ChatDeleteDialog` is mounted at the app root rather than inside `ChatHistorySidebar`, and
`pendingChatDelete` lives in `uiStore` for that reason: below `xl` the sidebar is a drawer animating
on `transform`, which makes it the containing block for any `position: fixed` descendant — the
warning would be laid out inside an 85vw panel instead of over the app.

### "Other: specify" is a question, not an answer

Two enum fields offer a choice that asks the operator to name something — ICP's `company_type`
("Other: specify") and the compliance field every long-form asset carries ("Other regulated field:
specify"). Clicking either used to answer the question with those literal words: "Other: specify"
was filed as the client's company type and carried into the prompt's INPUTS block. There was nowhere
to type the real answer, because the free-text bar is hidden for `enum_choice` — right for a question
with four buttons under it, wrong for the fourth one.

`lib/specifyChoice.ts` detects them on the trailing word (the prompts own the phrasing, so it is not
ours to normalise) and `QuestionWidget` opens an inline box under the pills instead of answering.

- **The prefix is kept in the answer** — "Other regulated field: Gambling", not "Gambling". The
  answer is read by people who cannot see the widget: in the transcript, where the bare words read
  as a fifth option nobody offered, and in INPUTS, where the compliance field needs "this client is
  regulated" as much as it needs "under gambling law".
- **The box is under the pills, not instead of them.** "Other" is a choice operators back out of,
  and the other three still answer on one click.
- **`choices` has exactly one consumer**, this widget — nothing validates an answer against the
  list, on either side of the wire, so a typed value needs no schema change. The sweep in
  `smoke/smoke.tsx` is what notices a new choice worded "Other — please specify" that the rule
  misses, since there is no second place to catch it.

- **"Done" in the pipeline diagram comes from the transcript, not from `currentIndex`.** The two
  were the same thing only while the run was strictly sequential; `approvedAssetIds` is the
  authority now. Jumping from stage 02 to stage 09 used to mark seven unbuilt stages "✓ Saved", with
  the progress bar agreeing.
