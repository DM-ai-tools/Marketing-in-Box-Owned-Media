# Competitor Analysis Prompt — Lead Magnet

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers of
this sub-service — it sells {SERVICE} to the same kind of customer, in the same market. The lead
magnet is the lens this listing is judged through, never the service the competitor has to sell.
Do NOT include companies that sell lead-generation, funnel-building or magnet-design services
unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service/industry
  "full_stack_niche" = a full-service company in the same industry prominently offering the
  target service
  If missing or empty, include both types.

service: {SERVICE} — the target's core service/product (inferred from {TARGET_URL} if not
specified).
  (Only include companies with an identifiable LEAD MAGNET tied to this service — a gated
  downloadable resource (ebook, checklist, template, guide, white paper), a quantified free
  audit/report with a stated dollar value, or a free workshop/masterclass used as a top-of-funnel
  conversion asset. A generic "contact us" or "book a call" CTA with no distinct resource does
  NOT count as a lead magnet and should be scored lower or excluded in favour of companies with
  real content-gated offers.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} companies that offer the target's core service AND run a distinct, promoted
  lead magnet for it
- Apply competitor_type and niche filtering if provided
- Exclude companies with no identifiable lead magnet beyond a generic contact form
- MUST have strong organic visibility (ranking pages, consistent SEO presence)

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: publish a real gated asset (not just a promise of one), quantify the
  value of a free audit/report, or run recurring workshops/masterclasses as lead generation
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Lead magnet strength/specificity (highest weight — real downloadable asset > quantified free
  audit > free consultation/strategy call > no distinct offer)
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
      "lead_magnet_url": "https://competitor1.com.au/free-guide/",
      "verification_confidence": "Verified",
      "lead_magnet_type": "Gated ebook",
      "offering_summary": "A 24-page PDF benchmark report gated behind name, email and company size, promising industry conversion rates by channel; delivered instantly and followed by a five-email nurture sequence.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 7 of 10. Three otherwise-strong candidates publish no gated asset at all — only ungated blog content behind a newsletter footer — and were excluded rather than counted as lead magnets."
}

Field rules:
- `lead_magnet_url` — the page where the lead magnet is actually claimed — the landing page or the gate itself,
  not the blog post that links to it.
- `lead_magnet_type` — the format in two or three words: "Gated ebook", "Email course",
  "ROI calculator", "Free audit", "Template pack", "Webinar replay". This is the column an
  operator scans to find the formats nobody in the market is using, so keep it under about 60
  characters — a sentence here stops being a column.
- `offering_summary` — one or two sentences on what the magnet actually promises, what it asks for in exchange
  (which fields are gated), and what happens immediately after the download. Base it only on what the fetched page shows — never infer
  or embellish. It must stand on its own without the URL being opened, since this is what a reader
  sees next to the competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and name
  near-misses or false positives that were excluded and why (an ungated blog post, a contact
  form with no deliverable, a directory). Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
