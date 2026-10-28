# Competitor Analysis Prompt — SEO-Focused Pillar Page

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or a substitutable service to the same kind of customer, in the same market.
The pillar page is the lens this listing is judged through, never the service the competitor
has to sell. Do NOT include companies that sell SEO, content strategy or pillar-page builds
unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service/industry
  "full_stack_niche" = a full-service company in the same industry prominently offering the
  target service
  If missing or empty, include both types.

service: {SERVICE} — the target's core service/product (inferred from {TARGET_URL} if not
specified).
  (Only include companies with an identifiable SEO PILLAR PAGE for this specific topic — a
  standalone, comprehensive cornerstone page (not a blog post, not a thin commercial service
  page) built around the head term for the target service. Genuine pillar page signals include:
  a dedicated URL distinct from both the blog and the service page, an on-page table of contents
  or jump-link navigation, multiple clearly structured sections/H2s, substantial word count
  (1,500+ words), internal links out to related cluster/sub-topic articles, and recent or
  ongoing updates. A page that only mentions the topic as one section within a broader pillar
  does NOT count unless the target topic is the clear primary subject.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, or if not specified ask explicitly to the user, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} companies that offer the target's core service AND maintain a genuine pillar
  page specifically on that topic
- Apply competitor_type and niche filtering if provided
- Exclude companies with no dedicated pillar page, or where the closest match is clearly a
  different content type (thin service page, single blog post, listicle/roundup article)
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate by fetching the actual page where possible; do not rely on titles or
  search snippets alone to confirm pillar-page status

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: have a clearly dedicated, well-structured pillar page, show internal
  linking to supporting cluster content, and demonstrate topical depth beyond generic advice
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators
- Exclude SEO and content-strategy agencies unless the target itself sells those
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified) based
  on whether the pillar page structure was directly confirmed

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Pillar page structural quality and topical focus (highest weight)
- Verification confidence (penalise unverified/snippet-only matches)
- Service and niche match
- Business model similarity
- Geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object with this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "pillar_page_url": "https://competitor1.com.au/complete-guide/",
      "verification_confidence": "Verified",
      "offering_summary": "A 4,000-word cornerstone guide with a sticky table of contents, 11 H2 sections covering strategy, channel selection, content calendars and reporting, a pricing-drivers section, an 8-question FAQ, and internal links out to six cluster articles.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 3 of 10. Australian agencies overwhelmingly publish thin service pages on this topic rather than pillar pages: four strong candidates were excluded for having no dedicated cornerstone page, and two more had guides that were listicle round-ups without cluster linking."
}

Field rules:
- `offering_summary` — one or two sentences describing the **pillar-page signals actually observed
  on that page**: approximate length, the H2-level sections it covers, whether it has a table of
  contents or jump navigation, any tables/checklists/calculators, its FAQ, and how many cluster
  pages it links out to. This is what the paired Pillar Page stage benchmarks its own architecture
  against, so describe structure and coverage rather than marketing impressions, and base it only
  on what the fetched page shows — never infer or embellish. It must stand on its own without the
  URL being opened.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified".
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned
  against the requested 10, explain any gap, and name near-misses or false positives that were
  excluded and why (a thin service page, a single blog post, a listicle, a directory). Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing (each competitor with its pillar page URL, verification confidence, and observed
pillar-page signals) and the notes section are rendered from this object for an operator to approve
— so every field above must be populated rather than described elsewhere in prose.
