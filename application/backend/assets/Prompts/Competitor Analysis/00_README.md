# Competitor Analysis Prompt Library

11 standalone competitor-analysis prompts, one per asset type, generalized to work for
any industry (not just digital marketing agencies).

## Files
1. 01_CRO.md
2. 02_Offers.md
3. 03_Lead_Magnet.md
4. 04_Blog.md
5. 05_SEO_Pillar_Page.md
6. 06_Content_Marketing.md
7. 07_Social_Content_Strategy_and_Posts.md
8. 08_Webinars.md
9. 09_Book.md
10. 10_Podcast.md
11. 11_Funnel.md — each competitor's lead-generation funnel (entry offer → capture → next step), for the funnel stage's Step 2

## Placeholders to fill in before each run
- {TARGET_URL} — the company/page you're benchmarking against (required)
- {SERVICE} — the specific service/product/topic to focus on (optional; if left blank,
  the prompt infers it from {TARGET_URL})
- {NICHE} — the industry/vertical to restrict competitors to (optional; leave blank for
  no restriction)
- {LOCATION} — city/region/state to prioritise (optional; defaults to Australia-wide)

## Shared design principles across all 11 prompts
- Every prompt asks for UP TO 10 competitors, not exactly 10 — if fewer genuine, verified
  matches exist, the prompt explicitly instructs returning fewer rather than padding the
  list with weak matches.
- Each prompt requires a verification_confidence field (Verified / Partially verified /
  Unverified) so low-confidence matches are flagged, not hidden.
- Each prompt excludes directories, marketplaces, freelancer platforms, and aggregators,
  keeping the focus on genuine service-providing competitors.
- Each prompt requires an Excel deliverable with a Notes section explaining any gap below
  10 results and near-misses/false positives that were excluded and why.
- All output is strict JSON with no explanation or markdown outside the array itself.
