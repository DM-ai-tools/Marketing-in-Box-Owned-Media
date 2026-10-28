# Competitor Analysis Prompt — Content Marketing (how rivals use content to sell the same sub-service)

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL}, whose
content programme (resource hub, blog, gated reports, newsletter) is what the target's own
content strategy for this sub-service will be built against. If fewer than 10 genuinely
qualifying competitors can be found and verified, return fewer rather than padding the list
with weak or unverified matches — flag any lower-confidence entries instead of silently
including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers of
this sub-service — it sells {SERVICE} (or a substitutable offering) to the same kind of customer,
in the same market. Content marketing is the lens: how these rivals use content to attract and
convert buyers of that sub-service. It is NOT a company that sells content marketing, content
strategy or copywriting as a service, unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's sub-service, whose content programme
  is its main demand-generation surface
  "full_stack_niche" = a broader company in the same industry that sells the target's sub-service
  and runs a distinct content programme feeding it
  If missing or empty, include both types.

sub-service: {SERVICE} — the sub-service the target sells (inferred from {TARGET_URL} if not specified).
  (Only include companies that sell this same sub-service AND run a real content programme that
  feeds it: a resource hub, an actively maintained blog with topical depth, gated reports, or
  a newsletter that closes on the sub-service. A company that sells "content marketing" to the
  target's industry is a supplier, not a competitor, and does not count.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- First establish what the target sells, then find {LOCATION} companies selling that same or a
  substitutable sub-service to the same buyers
- Prefer those whose content programme is visibly stronger than the target's: more specific
  topical depth, a clearer path from article to sub-service, a real case study tying content to
  leads or rankings
- Apply competitor_type and niche filtering if provided
- Exclude content-marketing agencies, copywriting shops and content tools unless the target
  itself sells content marketing
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate by fetching the actual page where possible; do not rely on titles or
  search snippets alone to confirm the content-to-sub-service pipeline

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: run a dedicated resource hub or content library, show explicit
  language linking content to the sub-service's outcomes, and cite a real case study
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified) based
  on whether the content-to-sub-service pipeline was directly confirmed on the page

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Sub-service match ({SERVICE} specifically) — highest weight
- Strength/explicitness of the content-to-sub-service pipeline
- Verification confidence
- Presence of a real case study demonstrating content-driven results
- Geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object with this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "content_marketing_page_url": "https://competitor1.com.au/resources/",
      "verification_confidence": "Verified",
      "offering_summary": "Sells the same sub-service; runs a resource hub of gated reports and webinars feeding a newsletter, with every asset closing on a strategy-call CTA and case studies attached to the same sub-service pages the content links into.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 6 of 10. Four near-misses were excluded: two publish content with no route into any sub-service page, one is a content-marketing agency serving the industry rather than competing in it, and one had no content published since 2023."
}

Field rules:
- `content_marketing_page_url` — the page that best evidences the content programme — the
  resource hub, the content library, or the sub-service page it feeds, whichever actually shows
  how content is produced and used to sell the same sub-service.
- `offering_summary` — one or two sentences: what the competitor sells and to whom, then the
  **content-to-sub-service pipeline observed** (what they publish, what the content asks the reader
  to do next, how it connects to the sub-service). The client's own strategy is built against this
  pipeline, so describe the mechanism rather than the volume. Base it only on what the fetched
  page shows — never infer or embellish. It must stand on its own without the URL being opened.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and name every
  notable near-miss excluded and why. Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
