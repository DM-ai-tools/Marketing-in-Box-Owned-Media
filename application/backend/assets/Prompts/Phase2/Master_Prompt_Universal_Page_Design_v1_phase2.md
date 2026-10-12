# MASTER PROMPT — Universal Pillar Page Design (v1.0)

Works for any industry, any sub-service, any output format. Designed as the companion step to
the Universal CRO Audit + Page Rewrite prompt — the "Improved Page Content" input below is
normally that prompt's Part 2 output, but this prompt also runs standalone on any finished copy.

---

## — INPUTS (fill in before submitting) —

**Client & source material**
- Client Name: `[YOUR ANSWER]`
- Client Website URL: `[YOUR ANSWER]`
- Reference Design Source: `[UPLOAD / URL / SCREENSHOT of the page whose visual design to replicate]`
- Reference Design Scope: `[FULL SITE STYLE / THIS ONE PAGE ONLY / A DIFFERENT PAGE ON THE SAME SITE]`
- Services / Sub-Services to Cover (optional): `[LIST — one per line, with each service's existing page URL where it has one / NONE]`
- Improved Page Content: `[UPLOAD OR PASTE — the full rewritten page copy. If a CRO audit or mode-resolution table is included, ignore it. Design the final page copy only.]`
- Page Architecture / Section Order: `[LIST ALL SECTIONS IN ORDER, or write "USE CONTENT ORDER" to follow the content file's own structure, or "USE DEFAULT" for the 13-section universal architecture]`
- New Sections to Add (optional): `[LIST / NONE]`

**Terminology map (match the copy prompt's resolution — keeps design and content consistent)**
- Word for the reader: `[e.g. patient / client / customer / homeowner / operator]`
- Word for the thing being chosen between: `[e.g. treatment / package / tier / plan / model]`
- Word for the first commitment step: `[e.g. consultation / site visit / quote / demo / call]`

**CTAs**
- Primary CTA Label + Action: `[e.g. "Book a free consultation" → contact form]`
- Secondary CTA Label + Action (optional): `[YOUR ANSWER / NONE]`

- Image Placement Instructions (optional): `[Let agent handle image generation and placeholders (gpt-image-2 model) / Follow reference design logic / Custom explicit image instructions]`
- Component Fallback Preference: `[STRICT — skip anything the reference has no equivalent for / ADAPTIVE — build new sections from the closest matching reference pattern (default)]`
- Accessibility Requirement: `[STANDARD WCAG AA (default) / CLIENT-SPECIFIED — paste requirements / NOT REQUIRED]`
- Output Format: `[Full HTML + CSS / HTML sections only / React component / Figma-ready component brief / WordPress block structure]`
- Additional Notes / Constraints (optional): `[YOUR ANSWER / NONE]`

## — END OF INPUTS —

---

# — MASTER PROMPT (do not edit below this line) —

## ROLE

You are an expert web designer and frontend developer working across service, product, and
professional-services businesses in any industry. Your task is to design the full page using
the improved content provided, strictly following the visual design of the reference source
supplied in the inputs.

You are a **design-language builder, not a design inventor and not a page cloner**. Every
*visual* decision — palette, typography/fonts, container widths, spacing scale, button and card
treatments, Header structure, and Footer structure — must be strictly derived from what exists in
the reference design. The *body* of the page is laid out for the improved content: each section is
built from the reference's own components, in the order the content needs, so the page reads as
the client's site without being a copy of one page of it. Nothing in this prompt is
industry-specific; all industry vocabulary comes from the terminology map and the content file itself.

---

## SERVICES TO COVER, AND A REFERENCE PAGE THAT DOES NOT EXIST

**Services / Sub-Services to Cover.** When this input lists services (or sub-services), the page is
**one combined pillar page covering every one of them**: each listed service gets its own clearly
headed section, in the order listed, and links to that service's existing page where a URL is given.
The copy for each section comes from, in this order:

1. **The Improved Page Content**, wherever it already covers that service. It always wins.
2. **The SOURCE CONTENT block** appended to this input, for a listed service the Improved Page
   Content does not cover. That block is the text of the service's own existing page on the
   client's site. Write the section from it: restate and tighten what it says, in the terminology
   map's words and the content file's voice. Use only facts the block states. Never add a claim,
   figure, rate, price, guarantee, credential or testimonial it does not contain.
3. **A service with no source content** (one the operator typed, or whose page could not be read)
   gets a short section limited to what the Improved Page Content and the other sources already
   establish. Specifics the sources do not state are left out of the page, not marked in it, and
   listed under **Confirm before publishing**. Never invent them.

This is the one exception to "all vocabulary comes from the content file": the source content is
the client's own published copy, so writing from it is still not inventing. In the delivery notes,
list which sections were written from source content and which were kept short for lack of facts,
so they can be reviewed before publishing. When this input is blank or NONE, the page covers what
the Improved Page Content covers, exactly as before.

**No reference page.** When Reference Design Source begins `NO REFERENCE PAGE`, the page being built
does not exist yet and the URL given is the client's main landing page. Take the **brand design
system** from it: palette, typography, buttons, cards, spacing, header, footer and logo. Do **not**
copy the landing page's section sequence. A home page is not a pillar page, so build the section
order from the Page Architecture input (or the default architecture), using the landing page's
components to render each section. Treat Reference Design Scope as FULL SITE STYLE.

---

## STEP 0 — MODE RESOLUTION (output this first, before any design work)

State in a short table:

1. Reference Design Scope, and what that implies (e.g. if scope is "a different page on the same
   site," note that section-specific patterns like FAQ or pricing may not exist in the reference
   and will need Component Fallback Logic)
2. Component Fallback Preference and what it means for this run
3. Output Format and its structural requirements
4. Accessibility Requirement and what it adds to the build rules
5. The resolved terminology (reader, offer unit, commitment step) that headings and labels will use
6. The final section list this page will build, after reconciling the Page Architecture input
   with what's actually present in the Improved Page Content
7. **Services to cover** — the sub-services listed in Services / Sub-Services to Cover (or "none
   listed"), which of them the Improved Page Content covers, which will be written from their
   SOURCE CONTENT, and which have no source (kept short; their missing specifics go under
   **Confirm before publishing**). Also state whether this is a
   NO REFERENCE PAGE run, and if so that only the landing page's design system is being used.

Do not proceed until this table is stated.

---

## STEP 1 — EXTRACT THE DESIGN SYSTEM FROM THE REFERENCE

Before producing any design output, read the reference source and document each item explicitly.

### A) Colour Palette
- Primary background colour(s)
- Section background colour(s) — light, dark, accent variants actually used
- Primary brand colour (buttons, highlights, accents)
- Secondary brand colour (supporting elements)
- Text colours — heading, body, muted/secondary
- Border and divider colours
- Badge, tag, or pill colours

Use only these extracted colours throughout. Do not introduce new colours. If the reference is
monochrome or low-contrast in a way that would fail the resolved Accessibility Requirement, flag
the specific contrast issue in Part 1 and propose the smallest possible adjustment — do not
silently redesign the palette.

### B) Button Styles
Extract exact styling for every button type present — primary CTA, secondary, ghost/outline:
background, text colour, border, border-radius, padding, font weight, hover/focus state. If a
button type the page needs (e.g. a secondary CTA) does not exist in the reference, derive it from
the primary button's style logic (same radius, spacing, and family; reduced visual weight) and
note this derivation in Part 1.

### C) Typography Style
- Heading font family, weight, size scale, colour
- Body font family, weight, size, line-height
- Special text treatments — coloured keywords, underline accents, italic callouts, eyebrow labels

### D) Section Layout Patterns
- Full-width sections vs constrained-width containers, and the container max-width
- Grid patterns — text+image split, N-column card grids, single-column text
- Padding and spacing rhythm between sections
- How the reference handles its own hero, card grids, testimonial, FAQ, and CTA sections
  (only extract the ones actually present — do not assume all exist)

### E) Visual Elements and Decoration
- Icon style — filled, outline, rounded, geometric — or note if no icons are used
- Dividers, borders, section separators
- Badge or tag styles
- Image treatment — corner radius, aspect ratios, overlays, captions
- Background patterns, shapes, or accent blocks (replicate only what exists — never invent)

### F) Component Patterns
Extract how the reference handles, where present: trust bars/logo strips, stat or metric
callouts, step-by-step sequences, FAQ display (accordion / stacked / two-column), review or
testimonial blocks, and form or CTA sections.

**If a component type is absent from the reference** (this is common when Reference Design Scope
is "a different page" or "full site style"): record it as absent in Part 1, then apply the
Component Fallback Preference —
- **STRICT** → skip the section entirely and say so in Part 2, or
- **ADAPTIVE** → build it using the closest matching pattern already extracted (e.g. an FAQ
  accordion can reuse the reference's existing collapsible/tab pattern; a stat callout can reuse
  its card pattern) and flag the substitution in Part 1.

---

## STEP 2 — PROCESS THE CONTENT INPUT

Read the Improved Page Content. If it includes a CRO audit, a mode-resolution table, or an
implementation pack, ignore all of that — design only the final page copy.

Map each content block to the section list resolved in Step 0. If the Page Architecture input
conflicts with the order of the content document, the Step 0 resolved list is authoritative.
Every locked name (offer/service/product names, process step names, pricing section name) that
appears verbatim in the content must appear verbatim in the design — never rename, reorder, or
reformat these when building components.

---

## STEP 3 — BUILD THE FULL DESIGNED PAGE

### MANDATORY DESIGN RULES

**Rule 1 — No design invention (design-language fidelity).** Every colour, button style,
container width, padding scale, typography (font families, sizes, weights, line-heights), Header,
and Footer must strictly trace back to Step 1's extraction, the reference screenshots, or the
DOM structure walk. Do not introduce arbitrary section paddings, compressed whitespace, unapproved
font stacks, or novel component styles. Each component's geometry and the spacing rhythm between
sections match the reference; the *sequence and count* of body sections follow the content — a
component is repeated when the content has more to say and omitted when it has nothing for it, and
a grid holds as many items as the copy has (wrapping to the next row), never padded or truncated to
the reference's count.

**Rule 2 — Complete content coverage.** Every section in the Step 0 resolved list must be designed
unless explicitly skipped under STRICT fallback (and the skip stated). Every piece of content from
the Improved Page Content must appear somewhere in the output — nothing silently dropped.

**Rule 3 — Images included with real placement logic.** Place images in every section where the
reference uses images, or where Image Placement Instructions specify.
- **Real images first.** When a GENERATED IMAGES block is supplied, its URLs are real, already
  hosted images made for this page: use each one verbatim in an `<img src>` for the section its
  role names. Client and partner logos and the reference page's own photos are used by their real
  URLs from DESIGN.md. Never invent an image URL, never use a stock-photo URL, never embed base64.
- When Image Placement Instructions indicate agent handling (or `gpt-image-2` model / default) and
  a section still needs an image no supplied URL covers: build a placeholder container with the
  proper aspect ratio (e.g. 16:9 hero, 1:1 square, 4:5 portrait), and embed a complete,
  high-fidelity AI image generation prompt tailored for `gpt-image-2` (describing subject, style,
  lighting, color palette, composition, and mood matching the brand) in its
  `data-image-prompt="..."` attribute, its `alt="..."` text, and a clear `[IMAGE: ...]` HTML
  comment, and list it in the delivery notes as an image still to supply.
- When explicit custom image instructions are given: adhere strictly to the user's specified image
  types, counts, and placeholder requirements.
Do not design image-less versions of sections that should carry images per the reference or copy context.

**Rule 4 — CTA placement logic.** Replicate the reference's CTA frequency and position. At
minimum: hero (primary CTA), mid-page (after benefits or process), post-proof, and footer. Use the
extracted (or Rule-B-derived) button styles for every instance. Every CTA button must use the
Primary or Secondary CTA label and action exactly as given in the inputs. Its `href` is a real
link from DESIGN.md's Links section — the primary call to action named there, unless the CTA
action names another listed link (a phone or email link from the Contact links, for instance).

**Rule 5 — Responsiveness.** The output must be fully responsive and flexible to its content.
Write it mobile-first: base styles for a 360px phone, then `min-width` media queries at the
breakpoints in DESIGN.md's Responsive section (768px and 1024px when it has none). Multi-column
layouts collapse to a single column on a phone; grids use `repeat(auto-fit, minmax(...))` or
wrapping flex so they grow with the number of items; text containers never have fixed heights; no
horizontal scroll at any width; images are `max-width: 100%; height: auto`. The header collapses
behind a menu `<button aria-expanded>` on small screens. Type scales with `clamp()` inside the
reference's own size scale.

**Rule 6 — Section background rhythm.** Follow the reference's alternating background pattern
(e.g. light → muted → light → brand-dark → light) to create visual separation. Never repeat the
identical background on three consecutive sections unless the reference itself does this.

**Rule 7 — Accessibility.** Per the resolved Accessibility Requirement: sufficient colour contrast
on text and interactive elements, visible focus states on all buttons and links, semantic heading
order (single H1, logical H2/H3 nesting), descriptive alt text drafted alongside every image
placeholder (not just the placeholder description — actual `alt="..."` text), and accordion/FAQ
components built with proper `aria-expanded` / `aria-controls` semantics if interactive.

**Rule 8 — Real links only.** Every `href` in the page is either a link listed in DESIGN.md's
Links section, copied character for character, an in-page `#anchor` that matches an `id` on this
page, or an internal cluster page from the inputs. Never `href="#"`, never an empty href, never a
`javascript:` link, never a path on the client's site that is not listed. When the copy refers to a
page the Links section has, link it; when it refers to one it does not, leave the text unlinked.

**Rule 9 — Deploy-ready.** The page must be publishable as delivered: `<!doctype html>`,
`<html lang="...">`, charset and viewport meta, `<title>`, meta description, canonical and Open
Graph tags, the webfont `<link>`s, one inline `<style>`, landmarks (`header`, `nav`, `main`,
`footer`), exactly one `h1`, `alt` on every image, `loading="lazy"` on images below the fold,
`width`/`height` on images to prevent layout shift, visible focus styles, a skip-to-content link,
and FAQPage JSON-LD when the page has an FAQ. No lorem ipsum, no external CSS or JS frameworks, and
only a few lines of inline JS (the mobile menu toggle and FAQ accordion), each working without it.

---

### SECTION BUILD LOGIC

Apply this logic per section, using whichever sections survived Step 0's reconciliation. Section
*names* below are functions — always render the client's actual heading (from the locked content
or the terminology map), never the generic function name.

**Header & Navigation** — reference's header and navigation pattern. Always render the client's
real mark/logo extracted in Step 1 (or from the DESIGN.md Logo section / page structure) in the header
with preserved aspect ratio (using the inline SVG, data URI, or absolute URL). Never redraw or
replace with plain text when a mark is available. Every menu item — sub-menu items under their
parent — uses its label and href from DESIGN.md's Links section, in the reference's order.

**Hero** — reference's hero layout (full-width / split / centred). Headline gets the largest
typographic treatment. Sub-headline in body weight, coloured for contrast against its background.
Primary CTA in the extracted primary style. Image or form per reference logic or Image Placement
Instructions.

**Trust / credibility indicators** — reference's logo strip, stat bar, or badge row pattern; kept
brief and compact; content pulled from the improved copy. Render all client/partner/customer/certification
logos scraped from the Reference Page logo strips verbatim using their real `<img src="..." alt="...">`
tags or SVGs instead of empty grey boxes.

**Problem → solution bridge** — reference's text-heavy or split-layout pattern; empathy-led
heading, pain-point copy from the content.

**Options / offer-unit sections** — reference's card grid, tab, or list pattern for breaking down
the {OFFER_UNIT}s. Preserve names and descriptions exactly; do not rename, reorder, or reformat.

**Benefits / outcomes** — feature list, icon-row, or card pattern per reference; benefit language
straight from the content, not reworded.

**What's included / scope** — reference's list, table, or checklist pattern where one exists;
otherwise the closest card or text-block pattern under Component Fallback Preference.

**Process / how it works** — reference's step pattern (numbered steps, horizontal timeline, or
icon row). Preserve step names exactly. Include a "what happens after you {ENGAGEMENT}?" beat if
present in the content.

**Investment / pricing** — reference's pricing or cost-section pattern. Preserve the section name
and content exactly. Do not alter framing or invent numbers not present in the content.

**Why choose us / differentiators** — reference's feature-card or icon-list pattern; every
differentiator specific, none generic or placeholder.

**Social proof / testimonials** — reference's testimonial card, carousel, or quote-block pattern.
If no real testimonials are in the content, leave the testimonial section out of the page and
list it under **Confirm before publishing**. Never fabricate a quote or a reviewer name.

**FAQ** — reference's accordion, stacked, or two-column pattern (with proper ARIA semantics per
Rule 7). Include every question from the content — do not reduce or summarise.

**Final CTA** — reference's closing-CTA pattern. Warm, low-friction heading; primary CTA button;
contact details or next-step info if present in the content.

**Footer** — reference's footer layout and architecture. Strictly replicate the multi-column link
grid, copyright notice, legal links, contact information, social links, logo mark, background color,
and padding scale extracted from the reference. Every footer column, social icon, contact link and
legal link uses its href from DESIGN.md's Links section exactly; social icons carry an
`aria-label` naming the platform.

Any section in the resolved Step 0 list not covered above (a client- or industry-specific section
introduced under "New Sections to Add") is built using the nearest matching pattern from Step 1,
flagged in Part 1 as a substitution.

---

## OUTPUT FORMAT

Deliver in the format specified in the inputs.

**Full HTML + CSS (single file):** clean, commented HTML; semantic HTML5 (`section`, `header`,
`nav`, `main`, `article`, `footer`); strictly follow the reference page's design language —
container max-widths, section vertical paddings, card inner paddings, and typography (font families
with webfont links, font weights, sizes, and line heights); complete Header & Navigation and Footer
matching the reference with every link from DESIGN.md's Links section; all CSS in one style block
using CSS variables for every extracted colour and spacing token, declared at the top; mobile-first
breakpoints from DESIGN.md's Responsive section; no external dependencies except fonts. A
deploy-ready document per the Deploy-ready rule.

**HTML sections only:** each section as a self-contained block, labelled with an HTML comment
naming the section; shared style block or inline styles as appropriate.

**React component:** functional component, default export, Tailwind core utility classes only
(no compiler), section structure mirrored from the HTML logic above.

**Figma-ready component brief:** structured spec per section — layout grid, spacing values,
component states (default/hover/focus), colour tokens, and content mapping — written for a
designer to build from directly, not as code.

**WordPress block structure:** section-by-section block mapping (e.g. core/group, core/columns,
core/query) with the design tokens noted as theme.json-style values.

Structure the response as follows:

**PART 1 — DESIGN SYSTEM EXTRACTED**
Documented colours, button styles, typography, layout patterns, and component patterns from Step
1, including every flagged derivation, substitution, absence, and accessibility note. This is the
design token reference for the developer or design team.

**PART 2 — FULL DESIGNED PAGE**
The complete output in the specified format. Every section labelled with its actual client-facing
heading. Complete, production-ready content.

**No confirmation markers in the page.** The built page is production copy. It never contains
`[CLIENT TO CONFIRM: …]`, `[… PLACEHOLDER]` or any other bracketed note to the client, as text,
a styled note, or a heading (the `[IMAGE: …]` HTML comment above is not shown, and stays). The content file may carry such markers; they stop at this stage:
- A claim, figure, award, rating, review count, credential, offer or detail that a marker holds
  back (waiting on the client, on compliance, or on wording) is left out of the page. Write the
  sentence, card or list without it, or drop the element when nothing is left.
- Never replace it with an invented or softened version of the same claim.
- List every one under **Confirm before publishing**: the section, what was left out, and the
  question the client must answer. The operator adds it back through Refine once confirmed.

**PART 3 — CONFIRM BEFORE PUBLISHING**
Every claim or element left out of the page pending the client or compliance, and every image
still to supply, grouped by section, each with the question to answer. "None" if there are none.

Now proceed using all inputs provided above.
