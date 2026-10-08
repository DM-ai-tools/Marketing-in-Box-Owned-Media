# Page Build Standards

The quality bar for a page this pipeline delivers as HTML: structure, responsiveness,
accessibility, performance and finish. It carries **no aesthetic of its own** — no palette, no
typeface, no motion style. Every visual value comes from the client's DESIGN.md, and where anything
here seems to conflict with DESIGN.md, DESIGN.md wins.

## 1. Composition — a page, not a stack of templates

- **One idea per section.** Each section has one job (prove, explain, compare, ask) and one heading
  that states it. Split a section that is doing two jobs; merge two that are doing one.
- **Hierarchy is visible at a glance.** Per section: an optional eyebrow, a heading, at most two
  sentences of lead-in, then the body. The eye lands on the heading first, the CTA second.
- **Vary the rhythm.** Alternate dense sections (card grids, tables) with open ones (a single
  statement, a quote, a split image/text). Never three identical card grids in a row; never three
  consecutive sections on the same background unless the reference does it.
- **Match the component to the content's shape.** A sequence is steps; parallel options are cards;
  a comparison is a table; a list of questions is an accordion; one strong claim is a full-width
  statement. Do not force five unequal items into a three-card grid — use a grid that wraps, or a
  list.
- **Whitespace is the reference's.** Use the spacing scale given; when content is short, keep the
  section's padding rather than shrinking the section.
- **Avoid template tells:** centred-everything layouts, gradient blobs, emoji as icons, generic
  stock-style hero copy, identical icon-title-text cards for every section, "Lorem", "Learn more"
  on every link.

## 2. Content fidelity

- Every word of copy comes from the supplied content. Headings may be split into eyebrow + heading,
  never reworded.
- Long lists stay complete. If a section has twelve items, it shows twelve (wrap the grid, or use a
  two-column list) — never "and more".
- Placeholders the content carries (`[CLIENT TO CONFIRM]`, `[TESTIMONIAL PLACEHOLDER]`) stay visible
  and styled as notes, so they are caught before publishing.

## 3. Responsive behaviour

- **Mobile-first.** Base CSS targets a 360px-wide phone; `min-width` media queries add columns at
  the breakpoints DESIGN.md gives.
- **Grids that flex:** `grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr))` or
  wrapping flex. A card grid's column count comes from the viewport and the item count, not a fixed
  number.
- **Fluid type:** `font-size: clamp(min, preferred, max)` with min/max taken from DESIGN.md's type
  scale. Headings never exceed the reference's largest size and never break mid-word on a phone
  (`overflow-wrap: anywhere` on long words only).
- **No fixed heights** on anything holding text. Use `min-height` where a hero needs presence.
- **Media:** `img, video { max-width: 100%; height: auto; }`; set `aspect-ratio` on image wrappers
  so space is reserved before load.
- **Navigation:** on small screens the menu collapses behind a `<button aria-expanded="false"
  aria-controls="...">` toggle; sub-menus become expandable lists; tap targets at least 44×44px.
- **Sub-menus are closed until asked for.** On desktop a sub-menu is hidden by default and opens on
  hover or keyboard focus of its parent item, and closes when the pointer or focus leaves. Two rules
  make that hold:
  - Style the top-level list with a child selector (`nav > ul`, `.main-nav > ul`), **never** a
    descendant one (`nav ul`). A descendant rule setting `display:flex` also matches every nested
    sub-menu and outranks `.sub-menu{display:none}`, so every dropdown renders permanently open.
  - Never put `tabindex` on the `<li>`. The parent link is already focusable; with `:focus-within`, a
    focusable `<li>` keeps its sub-menu open after a click until something else takes focus. A parent
    with no page of its own is a `<button aria-expanded>`.

  In the mobile panel, each sub-menu is a `<details>`/`<summary>` (or a button with
  `aria-expanded`), closed by default.
- **Tables** scroll horizontally inside their own wrapper (`overflow-x: auto`) instead of widening
  the page. Nothing else may cause horizontal scroll.

## 4. Accessibility (WCAG 2.2 AA)

- Landmarks: `header`, `nav` (with `aria-label` when there is more than one), `main`, `footer`.
- A "Skip to content" link as the first focusable element.
- Exactly one `h1`; headings descend without skipping levels.
- Text contrast at least 4.5:1 (3:1 for large text and UI borders), checked against DESIGN.md's
  actual colours. When a reference combination fails, use the nearest palette colour that passes
  and note it in a comment.
- Visible `:focus-visible` styles on every link and control, in a palette colour.
- Every image has `alt`; purely decorative images use `alt=""`. Icon-only links and buttons have an
  `aria-label`.
- FAQ accordions use `<details>/<summary>`, or buttons with `aria-expanded`/`aria-controls`.
- Respect `prefers-reduced-motion`: no animation that cannot be turned off.
- Form fields have visible `<label>`s; errors are not signalled by colour alone.

## 5. Performance

- One inline `<style>`; no CSS or JS frameworks; JavaScript only for the menu toggle and anything
  `<details>` cannot do, and the page works without it.
- Webfonts via the links DESIGN.md gives, with `display=swap`; at most the weights actually used.
- Images: explicit `width` and `height`, `loading="lazy"` and `decoding="async"` below the fold,
  `fetchpriority="high"` on the hero image only.
- Inline SVG icons are reused, not duplicated with different markup.

## 6. SEO and sharing

- `<title>` (≤ 60 characters) and meta description (≤ 155) from the content's own words.
- `<link rel="canonical">`, Open Graph (`og:title`, `og:description`, `og:type`, `og:url`,
  `og:image` when a hero image exists) and `twitter:card`.
- Every H2 has an `id` so the in-page navigation can link to it.
- Structured data as JSON-LD: `FAQPage` for an FAQ, `BreadcrumbList` when there is a parent page,
  `Organization` / `LocalBusiness` only from details the page actually shows.

## 7. Links

- Header, footer, social, contact and legal links come from DESIGN.md's Links section, href for
  href. Internal anchors (`#section-id`) must match an `id` on the page.
- External links open in the same tab unless they are documents; links that do open a new tab carry
  `rel="noopener"`.
- Link text describes the destination — no "click here", no bare "Learn more" without context
  (add a visually hidden span naming the target when the design calls for a short label).

## 8. Final check before output

Run through this list mentally and fix anything that fails:

1. Every header, footer and social link matches DESIGN.md's Links section exactly.
2. No `href="#"`, no empty `src`, no lorem ipsum, no invented URLs.
3. At 360px: no horizontal scroll, menu collapses, grids are one column, buttons full-width where
   the reference does that. On desktop, every sub-menu is closed on load.
4. One `h1`; every image has `alt`; every interactive element is reachable and visibly focused.
5. Only DESIGN.md colours and fonts are used.
6. Every piece of supplied copy appears on the page.
