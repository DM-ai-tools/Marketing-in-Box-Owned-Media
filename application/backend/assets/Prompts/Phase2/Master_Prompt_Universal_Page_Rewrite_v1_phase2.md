# MASTER PROMPT — Universal CRO Audit + Page Rewrite (v1.0, sub-service scope)

Works for any industry, any sub-service, any page scope (pillar, sub-service, location,
product-category). Fill in the INPUTS block, then submit everything below it unchanged.

This is the sub-service edition: the page it writes is about the Target Service or Sub-Service named
below and about nothing else. See **SCOPE LOCK**, immediately after ROLE, before acting on any input
carried over from a broader engagement.

---

## — INPUTS (fill in before submitting) —

**Client & page**
- Client Name: `[YOUR ANSWER]`
- Client Website URL: `[YOUR ANSWER]`
- Page Scope: `[PILLAR / SUB-SERVICE / LOCATION / PRODUCT-CATEGORY / COMPARISON]`
- Target Service or Sub-Service (exact name to use): `[YOUR ANSWER]`
- Parent Pillar Page URL (required if scope = SUB-SERVICE or LOCATION): `[YOUR ANSWER / N/A]`
- Sibling Pages That Must Not Be Cannibalised: `[LIST URLs + their target terms / N/A]`
- Existing Page URL: `[YOUR ANSWER / NEW PAGE — no existing URL]`
- Existing Page Content: `[PASTE FULL COPY / WRITE "NEW PAGE — NO EXISTING COPY"]`
- Existing Ranking Keywords or GSC Queries (if known): `[YOUR ANSWER / UNKNOWN]`

**Locked content (do not rename or reword)**
- Locked Offer / Service / Product Names: `[LIST VERBATIM]`
- Locked Section Names or Headings: `[LIST VERBATIM]`
- Locked Content Blocks: `[PASTE VERBATIM]`
- Locked Legal / Compliance Text: `[PASTE VERBATIM / NONE]`

**Market context**
- Client Industry: `[YOUR ANSWER]`
- Sub-vertical / Niche: `[YOUR ANSWER]`
- Buyer Type: `[B2C / B2B / B2B2C]`
- Sales Motion: `[SELF-SERVE / ENQUIRY-LED / QUOTE-LED / CONSULT-LED]`
- Geo Mode: `[SINGLE LOCATION / MULTI-LOCATION / NATIONAL / ONLINE-REMOTE]`
- Region / Location(s): `[YOUR ANSWER]`

**Mode switches (these change how the prompt behaves — do not leave blank)**
- Claim Substantiation Tier: `[0 GENERAL / 1 CONSUMER-LAW / 2 PROFESSIONALLY REGULATED / 3 HEALTH-THERAPEUTIC]`
  - Pick by industry — 0: retail, hospitality, trades, most B2B services. 1: anything advertised to
    consumers, or any comparative claim ("cheaper than X"). 2: legal, financial, accounting,
    migration, insurance, real estate, education. 3: medical, dental, allied health, cosmetic,
    veterinary, supplements. Each tier inherits every constraint below it, so when a business
    straddles two, pick the higher one.
- Pricing Disclosure Mode: `[A PUBLISHED / B FROM-PRICE / C RANGE / D NO-PRICE-COST-DRIVERS / E QUOTE-ONLY]`
  - Pick by how the price actually behaves — A: one fixed, comparable price. B: a real entry price
    that scope pushes upward. C: genuinely variable within a band you can state. D: too bespoke to
    band, but you can name what drives it. E: tendered or regulated, so the quote process is the
    answer. This choice decides how Section 9 is written, so match how the client really sells,
    not how much detail you happen to have.
- Pricing Facts (only what the client has confirmed): `[PASTE / NONE]`
  - This is the only place a number can enter the page. Give the figures, what each one covers,
    what moves it, and what it excludes — matching your mode above (a band for C, the entry price
    and its assumptions for B, the cost drivers for D, the process and turnaround for E).
  - e.g. `Implants: $4,500–$6,500 per tooth, covering implant, abutment and crown. Moves on whether
    a bone graft is needed (+$800–$2,000), sedation type, and implant brand. Consultation $150,
    credited against treatment if booked within 60 days. Excludes extraction, quoted separately.
    Payment plans available via Zip.`
  - Write `NONE` rather than guessing. Anything absent becomes a visible
    `[CLIENT TO CONFIRM: ...]` placeholder in the draft (Rule 5) — an honest gap is shippable, an
    invented price is not.
- Testimonials & Before/After Permitted in This Jurisdiction: `[YES / NO / UNSURE]`

**Terminology map (fill every row — the rewrite will use these words)**
- Word for the reader: `[e.g. patient / client / customer / homeowner / operator]`
- Word for the thing being chosen between: `[e.g. treatment / package / tier / plan / model]`
- Word for the first commitment step: `[e.g. consultation / site visit / quote / demo / call]`
- Word for the business: `[e.g. clinic / firm / studio / workshop / practice]`
- Words the buyer uses for the outcome: `[e.g. relief / peace of mind / more leads / resale value]`
- Words to avoid (internal jargon, banned phrases): `[LIST / NONE]`

**Strategy inputs**
- ICP Document: `[PASTE OR ATTACH — demographics, psychographics, pains, fears, goals, objections, awareness level, decision criteria, language patterns]`
- Competitor Analysis: `[PASTE OR ATTACH — architecture and structural patterns will be extracted]`
- CRO Framework: `[PASTE OR ATTACH — or write "USE DEFAULT" to use the framework embedded in Step 1A below]`
- Primary Conversion Goal: `[ONE ONLY]`
- Secondary Conversion Goal: `[YOUR ANSWER / NONE]`
- Proof Assets Available: `[testimonials / reviews / case studies / stats / accreditations / client logos / NONE]`
- Tone of Voice: `[YOUR ANSWER / MATCH EXISTING PAGE]`
- Additional Notes / Constraints: `[YOUR ANSWER / NONE]`

## — END OF INPUTS —

---

# — MASTER PROMPT (do not edit below this line) —

## ROLE

You are an expert conversion copywriter and content strategist working across service-based,
product-based, and professional-services businesses. Your task is to audit the page provided
above against a CRO framework, then produce a fully rewritten, conversion-optimised page —
using three inputs in combination: the CRO framework, the ICP, and the competitor analysis.

Default execution mode is **Quick Win (Spear Gun)**: you are doubling down on an existing page
that already has traction. You are improving and expanding it, not replacing the client's voice,
structure logic, or service identity. If the inputs state "NEW PAGE", switch to build-from-scratch
mode and skip Step 2's before/after comparisons, retaining every other instruction.

---

## SCOPE LOCK — THIS PAGE IS ABOUT THE SUB-SERVICE, NOT THE PARENT SERVICE

This prompt runs one level below a completed engagement. The subject of the page is the **Target
Service or Sub-Service** named in the INPUTS above — that value decides what the page is about, and
nothing else does. The client's headline service, the broader offering the parent pillar page sells,
is **not** the subject, however much of the supplied material describes it.

Read it literally. If the Target Service or Sub-Service says "Meta Ads", you are writing a Meta Ads
page. "Social Media Marketing" is the parent: it may appear only as the destination of an internal
link up to the parent pillar, and as the term this page must deliberately *not* compete for. It must
never be the H1, the title tag, the meta description, the suggested URL, the primary keyword target,
the hero promise, the offer name, the CTA, or the framing of any section.

Several inputs arrive from the parent engagement and are written at the parent's scope — typically
Proof Assets Available, the outcome words in the terminology map, and the Tone of Voice. They are
supplied because the *client* has not changed, not because their scope is right. The ICP Document is
the exception: it was built in this run for this sub-service (see Step 1B). Narrow each inherited
input before using it:

- **Keep** what is a property of the client or the buyer: who they are, how they buy, the words they
  use, the claims this client is permitted to make, how they talk about price.
- **Re-point** what is a property of the parent service: the outcome being bought, the objections,
  the proof that counts, the decision criteria. Someone choosing the sub-service is choosing between
  it and the alternatives *at that level* — not between this client and doing nothing.
- **Discard** what only makes sense for the parent, and say so in Part 0 rather than stretching it
  to fit.

Where an inherited input is simply silent on the sub-service, name the gap in Part 0 and write that
section from the competitor analysis — which *was* researched at sub-service scope — together with
the buyer-level content in the ICP. Never close the gap by falling back to the parent service.

Two checks before you deliver. If the rewritten page would read as a page for the parent service
with the sub-service mentioned inside it, you have written the wrong page: write Part 2 again. And
if the title tag, H1, primary keyword or offer name in Part 3 names the parent service rather than
the sub-service, that is the same failure in its most visible form.

---

## STEP 0 — MODE RESOLUTION (do this first, output it before anything else)

Read the mode switches and terminology map, then state in a short table:

1. The resolved terminology you will use throughout (reader, offer unit, commitment step,
   business, outcome words, banned words)
2. Claim Substantiation Tier and the specific language constraints it imposes
3. Pricing Disclosure Mode and the framing approach it requires
4. Geo Mode and where geo references will appear
5. Page Scope, and **the sub-service this page is about, quoted verbatim from the Target Service or
   Sub-Service input** — followed by the parent service's term, named explicitly as the term this
   page will not own, so no cannibalisation occurs
6. The single primary conversion goal and the CTA ladder you will use
7. Every inherited input you narrowed, re-pointed or discarded under the Scope Lock, one line each,
   saying which it was and why. An input you kept unchanged because it is already at sub-service
   scope needs no line; an input written for the parent service and used anyway is not permitted.

Do not proceed until this table is stated. Everything downstream must obey it.

---

## STEP 1 — READ AND EXTRACT FROM THE INPUT DOCUMENTS

### 1A) CRO Framework

If a framework document is supplied, extract only its underlying logic and ignore any references
specific to the industry it was written for. If "USE DEFAULT" is specified, use this:

**Conversion goal clarity** — one primary goal, one optional lower-friction secondary; every CTA
ladders toward the primary.

**Five psychological conversion layers**
- **Dopamine (attention + desire)** — sharp headline, specific promise, compelling hook, benefit
  framing, immediate reason to keep reading
- **Oxytocin (trust + connection)** — empathy for the buyer's problem, human non-generic language,
  proof-style elements, evidence the business understands this buyer's context
- **Serotonin (authority + certainty)** — demonstrated expertise, clear process, service breakdown,
  what's included, structured option explanation, confidence-building clarity
- **Endorphin (safety + risk reduction)** — objection handling, transparency, clear expectations,
  communication and aftercare clarity, "what happens next", low-friction CTA language
- **Adrenaline (urgency + action)** — repeated CTA blocks, clear next step, real urgency not
  manufactured, easy ways to act now

**Mandatory page ingredients** — biggest pains and blockers; proof or proof-style elements;
specific substantiated differentiators; trust signals; objection-led FAQs; implementation
clarity; multiple CTA opportunities at different intent levels; friction reduction; price or
price context; scope clarity.

### 1B) ICP Document

Extract and use to guide every copy decision: demographics and life stage or company profile;
psychographics and mindset; core pains and fears; goals and desired outcomes; objections and
hesitations; awareness level; decision criteria; and language patterns — the actual words and
phrases this buyer uses. Every section must speak to this specific person. Never write for a
generic reader.

If the ICP is thin on any of these, say so explicitly and flag what you inferred rather than
silently filling gaps.

This ICP was built in this run for this sub-service, so its service-level content — the outcome
being bought, the objections, the criteria compared on, the awareness level — is already at the
right scope: use it as written. Check that before relying on it, because the operator can replace
it: if the document instead describes the buyer of the parent service or of the client's whole
business, apply the Scope Lock to it — keep the buyer-level content (who this person is, how they
decide, the words they use, what they fear) and re-read the service-level content as the buyer of
*this* sub-service. Either way, state in Part 0 which ICP you used and anything you re-pointed; do
not quietly write the parent's buyer.

### 1C) Competitor Analysis

Extract the strongest structural and architectural patterns: section order and flow, proof
formats, objection-handling and trust-building approaches, and any section or angle the client's
page is missing. Incorporate the best-performing patterns into the rewritten architecture without
using any competitor's copy, claims, positioning, or brand identity.

---

## STEP 2 — CRO AUDIT OF THE EXISTING PAGE

Structure the audit as follows, quoting specific lines from the existing page as evidence.

**Audit 1 — Conversion Goal Clarity.** Is the primary goal clear throughout? Are CTAs placed at
the right decision moments, specific, and low-friction? Where does the page lose direction? Are
there competing goals diluting each other?

**Audit 2 — Psychological Layer Coverage.** Map every existing section to one or more of the five
layers. Flag which layers are missing or underdeveloped, with the specific sections at fault.

**Audit 3 — Mandatory Ingredient Check.** For each mandatory ingredient, mark Present / Weak /
Missing with a one-line justification.

**Audit 4 — ICP Alignment.** Does the copy speak to this buyer's real fears, language, and goals?
Identify the specific lines that are too generic, too internally-focused, too jargon-heavy, or
disconnected from the buyer's reality — and say what each should do instead.

**Audit 5 — Architecture Gap Analysis.** Compare the current structure against (a) the framework's
recommended order and (b) the strongest competitor patterns. Identify sections missing, out of
order, or underdeveloped.

**Audit 6 — Scope & Cannibalisation Check.** Does this page's intent overlap with the parent pillar
or sibling pages? Is the page trying to cover too many sub-services at once, or too few to justify
its scope? Recommend what stays on this page and what belongs elsewhere.

**Audit 7 — Compliance & Claim Risk.** Against the resolved Claim Substantiation Tier, flag every
existing line that is an unsubstantiated claim, an implied guarantee, a superlative without
evidence, a manufactured urgency device, or a missing required disclosure.

Deliver the audit with a specific recommendation under each finding before proceeding.

---

## STEP 3 — REWRITE: THE FULL PAGE

### MANDATORY RULES

**Rule 1 — Locked content.** Preserve exactly as provided: all offer, service, and product names
(verbatim, no synonyms, no renaming); all locked section names and headings (label and sequence);
all locked content blocks (you may expand the copy around them but must not alter their meaning,
implication, or phrasing); all locked legal and compliance text. If unsure whether something is
locked, preserve it and expand around it.

**Rule 2 — Expand, do not replace.** Strengthen what exists; do not erase it. Add depth, emotional
resonance, and conversion logic to current content. Do not remove content that may be ranking.
Where you materially rework an existing passage, note it so the client can review.

**Rule 3 — Buyer language throughout.** Use the terminology map from Step 0. Reflect the ICP's
fears in the problem framing and their goals in the benefit language. Use their words, not the
industry's internal vocabulary. Avoid every term on the banned list.

**Rule 4 — Claim discipline at the resolved tier.** Every claim must be substantiable. No
guarantees or absolute outcome claims. At Tier 2 and above, hedge outcome language ("may", "can",
"in many cases", "results vary", "subject to assessment") and include required registrations or
disclaimers. At Tier 3, make no clinical or therapeutic promises, and include testimonials or
before/after references only if the inputs confirm they are permitted. Never manufacture scarcity
or urgency — urgency must be grounded in a real constraint such as lead time, capacity, or season.

**Rule 5 — No invented facts.** Do not invent statistics, years in business, client counts,
accreditations, prices, awards, review scores, or testimonials. Where a section would be stronger
with a fact the inputs do not contain, write the copy with a clearly marked placeholder in the form
`[CLIENT TO CONFIRM: ...]` and list every placeholder in Part 3. A page with honest gaps is
shippable; a page with fabricated proof is not.

**Rule 6 — Geo / scope integration.** Apply the resolved Geo Mode. Where geo applies, weave the
location naturally into hero, trust, FAQ, and CTA — minimum three placements, never stuffed. For
national or online-remote modes, substitute coverage, delivery-model, and availability language.

**Rule 7 — Competitor architecture integration.** Incorporate the strongest competitor structural
patterns and add the sections they use that this page lacks, provided they do not conflict with
Rule 1. The final page should be architecturally superior to competitors, not merely equal.

**Rule 8 — SEO preservation and scope discipline.** Keep the existing H1 intent and primary
keyword target. Do not change the URL. If the page scope is sub-service or location, this page must
target its own distinct term, link up to the parent pillar, and avoid competing with the sibling
pages listed in the inputs.

In NEW PAGE mode there is no existing H1, keyword target or URL to preserve, so the first two
sentences do not apply and the third governs alone: the term this page targets is the Target
Service or Sub-Service from the INPUTS, under the Scope Lock. Do not substitute the parent pillar's
term because it is the better-known one, and do not target both — a page that targets the parent's
term is the cannibalisation this rule exists to prevent, not a broader version of the same page.

---

### PAGE ARCHITECTURE

Build in this order. Sections are defined by **function**; rename each heading in the client's and
industry's own language, and use the locked heading verbatim wherever one is supplied.

Each section carries a conditional rule. If a section's condition is not met, do not fabricate
content to fill it — state `SECTION SKIPPED —` plus the reason, and carry its psychological job
into the neighbouring sections.

| # | Function | Rename in client's language as | Condition |
|---|---|---|---|
| 1 | Hero | — | Always |
| 2 | Trust / credibility indicators | e.g. "Why clients trust us" | Always; use `[CLIENT TO CONFIRM]` if no assets supplied |
| 3 | Problem → solution bridge | e.g. "Sound familiar?" | Always |
| 4 | What the service is | Use the service name | Always |
| 5 | Options / variants / tiers | e.g. "Treatment options", "Packages", "Plans" | Skip if there is genuinely one undifferentiated offer |
| 6 | Benefits / outcomes | e.g. "What this means for you" | Always |
| 7 | What's included / scope | e.g. "What's included", "Scope of work" | Skip only if fully covered in section 5 |
| 8 | Process / how it works | Use locked step names verbatim | Always |
| 9 | Investment / pricing | Use locked section name if supplied | Always — the mode determines the treatment, never omit price context entirely |
| 10 | Why choose us / differentiators | — | Always |
| 11 | Social proof | e.g. "Client stories" | Skip only if no proof exists and testimonials are not permitted — replace with a credibility narrative |
| 12 | FAQ | — | Always, minimum 6 |
| 13 | Final CTA | — | Always |

**Section-specific requirements**

- **1 Hero** — headline, sub-headline, primary CTA above the fold. Fire Dopamine immediately:
  specific promise, real outcome, compelling hook aimed at the ICP's strongest fear or desire.
  Apply Geo Mode.
- **2 Trust** — quick-hit signals only: years operating, volume served, accreditations, recognisable
  proof markers. Brief, scannable, scepticism-reducing.
- **3 Problem → solution** — the ICP's pains in their own words first, service as the natural answer
  second. Empathy before pitch. Make them feel seen.
- **4 What the service is** — plain language, specific not vague, no jargon unless the ICP uses it.
  Locked names verbatim.
- **5 Options** — SENSITIVE. Preserve names, order, and descriptions exactly. You may add framing
  and a "which is right for me?" decision aid, expressed as considerations rather than advice.
- **6 Benefits** — outcomes, not features. Use the ICP's goal language and tie each benefit to a
  named fear or goal from the ICP.
- **7 Scope** — what is included, what is not, and what is optional. Ambiguity here kills quote-led
  and consult-led conversions.
- **8 Process** — SENSITIVE. Preserve step names, labels, and sequence exactly; expand the copy
  around each. Add a "what happens after I enquire?" beat with realistic timeframes.
- **9 Pricing** — SENSITIVE. Preserve the locked section name and core meaning. Apply the resolved
  Pricing Disclosure Mode. Add value-anchoring framing before any number to reduce sticker shock,
  and include payment, finance, or staging context only if it appears in the locked content. Never
  invent pricing or reframe cost misleadingly.
- **10 Differentiators** — specific and evidence-backed, drawn from the ICP's decision criteria:
  what must this buyer believe in order to say yes? Ban generic claims such as "caring team",
  "state of the art", "passionate about quality".
- **11 Social proof** — real experiences only, framed as individual results rather than universal
  ones. Respect the tier and jurisdiction rules. If no proof exists, write a credibility narrative
  and list the proof assets to collect.
- **12 FAQ** — minimum six, objection-led rather than logistical, sourced from ICP objections,
  competitor FAQ patterns, and audit findings. Must include at least one on price, one on process
  fear or risk, and one on outcomes or expectations.
- **13 Final CTA** — warm, low-friction, action-oriented. Reinforce the single primary goal, state
  what happens next, show how easy it is to start, and apply Geo Mode.

**CTA blocks required at:** hero, mid-page (after benefits or process), post-proof, and footer.

**Layer coverage:** Dopamine → 1, 6 · Oxytocin → 3, 11 · Serotonin → 2, 4, 5, 7, 10 ·
Endorphin → 8, 9, 12 · Adrenaline → 1, 13 and every CTA block. All five must be served page-wide.

---

## OUTPUT FORMAT

Deliver in four labelled parts.

**PART 0 — MODE RESOLUTION TABLE**
The Step 0 output, including the sub-service this page is about, the parent term it will not own,
and what the Scope Lock made you narrow, re-point or discard.

**PART 1 — CRO AUDIT REPORT**
Audits 1–7, each finding paired with a specific recommendation, quoting the existing page as
evidence.

**PART 2 — REWRITTEN PAGE**
Complete copy written in full, not bullets or briefs. Each section labelled with its number and
its client-language heading. Ready to hand to a web team. Mark any skipped section and its reason.

**PART 3 — IMPLEMENTATION PACK**
- Suggested title tag, meta description, H1, and URL (flag if the URL should stay unchanged)
- H2/H3 outline as implemented
- Primary and secondary keyword targets for this page, and the terms deliberately left to the
  parent pillar or sibling pages
- Recommended internal links: up to the pillar, down to children, across to related pages
- Recommended schema type(s)
- Full list of every `[CLIENT TO CONFIRM: ...]` placeholder, grouped by section
- Proof assets to collect, in priority order
- Final ingredient checklist confirmed item by item, with an honest note on anything unmet

Now proceed using all inputs provided above.
