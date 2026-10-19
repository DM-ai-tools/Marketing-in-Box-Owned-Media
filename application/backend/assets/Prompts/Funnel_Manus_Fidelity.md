# DESIGN FIDELITY — THE REFERENCE PAGE IS THE DESIGN

This section overrides Rule 2 of the page build rules wherever the two differ. You are not designing
these pages. You are reproducing the client's existing website — its look, its layout system and its
motion — around new copy. A visitor who knows the client's site must not be able to tell these pages
apart from it. There is no design language of your own here: no new colours, no new fonts, no new
component styles, no new animation ideas, no "improvements".

## 1. Study the reference page before you write any HTML

Open the REFERENCE PAGE URL in your browser. Do all of the following and keep notes; the build
notes must summarise what you measured.

- **Screenshots.** Take full-page screenshots at 1440px and at 390px wide, and a 1440×900 screenshot
  of the first screen. Use them, and the attached reference screenshots, as the visual target.
  Compare your pages against them side by side before you deliver.
- **Computed styles.** Read the real values with the browser's developer tools, never by eye: page
  and section backgrounds, text colours, the brand and accent colours, font families and their
  webfont files, the type scale (size, weight, line height, letter spacing, text transform) of h1-h4,
  body and buttons, container max-width, section padding, grid gaps, border radii, borders and box
  shadows. DESIGN.md (below) holds measured values; where the live page and DESIGN.md disagree, the
  live page wins.
- **Header and footer.** Logo placement and size, nav items and sub-menus, the sticky or fixed
  behaviour and how the header changes on scroll (background, height, shadow), the mobile menu and
  its open/close animation, and the footer's columns, links and legal row.
- **Components.** Buttons in every state (default, hover, focus, active), cards, icon lists,
  testimonials, forms and inputs (borders, focus rings, placeholder colour), section dividers and
  background treatments (gradients, overlays, shapes, images).
- **Motion — this is required, not optional.** Find every animation and transition on the page:
  - CSS `transition`s and `@keyframes` (durations, delays, easings);
  - entrance and scroll-triggered animations — reveal on scroll, fade or slide up, staggered items,
    counters, parallax backgrounds, sticky sections — including those driven by a library (AOS,
    WOW.js + animate.css, GSAP/ScrollTrigger, Elementor motion effects, Swiper); read their classes,
    data attributes and settings to get the exact effect, offset, duration and easing;
  - hover effects on cards, images and links; smooth scrolling; carousels and their timing.

## 2. Reproduce it

- Use only the colours, fonts, sizes, spacing, radii, shadows and components you measured. Declare
  them as CSS custom properties at the top of the stylesheet and reference them throughout.
- Rebuild the header and footer exactly, using the hrefs in DESIGN.md's Links section.
- Lay out each page's sections with the reference's own section types and rhythm: pick, for every
  section of the copy, the reference section that serves the same purpose (hero, feature grid,
  process steps, testimonials, FAQ, CTA band, form) and build it the same way. The number and order
  of sections follow the copy; how each one looks follows the reference.
- Re-create every kind of motion you found, with the same timing and easing, in vanilla CSS and one
  small inline script (an `IntersectionObserver` that adds a class for scroll reveals; no libraries,
  no CDN scripts). Content must be visible without JavaScript (reveal by adding motion, never by
  starting hidden in the static HTML), and everything must respect `prefers-reduced-motion: reduce`
  by showing the final state without animation.
- Use the reference page's own images only where they are the client's brand assets (logo, icons,
  client logos); for photography use the GENERATED IMAGES supplied. Never hotlink an image the brief
  does not list.

## 3. Check before you deliver

Open every page you built at 1440px and 390px, scroll through it, and compare it with the reference
screenshots: colours, type, spacing, header, footer, buttons and motion. Fix anything that reads as
a different site. In the build notes, under **Design measured**, list the palette, fonts, container
width, and every animation you reproduced (what it is, its trigger, duration and easing).
