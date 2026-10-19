# Competitor Analysis Prompt — Funnel

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL}, and for
each one map the lead-generation funnel it actually runs for the service below. This listing feeds
a funnel design stage that compares funnel types, entry offers and follow-up against the market, so
what matters is the funnel each competitor operates, read off their own pages — not their brand,
their blog, or their rankings alone. If fewer than 10 genuinely qualifying competitors can be found
and verified, return fewer rather than padding the list with weak or unverified matches — flag any
lower-confidence entries instead of silently including them.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service/industry
  "full_stack_niche" = a full-service company in the same industry prominently offering the
  target service
  If missing or empty, include both types.

service: {SERVICE} — the target's core service/product (inferred from {TARGET_URL} if not
specified).
  (Only include companies that sell this service AND run an identifiable conversion path for it: an
  entry offer a visitor can take (a gated resource, a free audit or assessment, a quiz or calculator,
  a webinar or workshop, a free trial, a booked strategy call with a stated deliverable), a page or
  form that captures the lead, and a visible next step after it. A site whose only path is a generic
  "contact us" form is still a competitor, but rank it lower and say so — "contact form only" is a
  funnel type, and the absence of anything better is exactly the gap the funnel stage looks for.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

What to read on each competitor's site (open the pages; do not rely on titles or search snippets):
- The page for the service, and every call to action on it.
- Where each CTA leads: the landing page, the gate (which fields it asks for), the thank-you page.
- What is offered on the thank-you page or next step: a booking link, a tripwire or front-end offer,
  a second resource, a sales call.
- Any stated follow-up (an email course, a "we'll be in touch within 24 hours", a nurture promise).
- A published price for the front-end or core offer, if the funnel shows one.

Competitor Selection Logic:
- Find {LOCATION} companies that offer the target's core service
- Apply competitor_type and niche filtering if provided
- Prefer companies whose conversion path can actually be traced past the first click
- MUST have strong organic visibility (ranking pages, consistent SEO presence)

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Describe only the funnel steps you saw on the fetched pages. A step you could not reach (behind a
  form submission, a login, a payment) is named as unverified in `offering_summary`, never filled in
  from what such funnels usually do
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Funnel depth and clarity (highest weight — a traceable entry offer, capture and next step >
  a single gated offer with no visible next step > a consultation booking > contact form only)
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
      "funnel_entry_url": "https://competitor1.com.au/free-seo-audit/",
      "verification_confidence": "Verified",
      "funnel_type": "Free audit → strategy call",
      "starting_price": null,
      "offering_summary": "Service page CTA 'Get your free audit' leads to a 5-field form (name, email, phone, website, monthly budget); the thank-you page books a 30-minute strategy call via Calendly. No email follow-up is stated and no price is shown before the call.",
      "similarity_score": 0.88,
      "avg_position": 9.4,
      "intersections": 380
    }
  ],
  "notes": "Returned 8 of 10. Two candidates were excluded: one is a directory, and one's only path is a phone number with no page to read. Webinar and quiz funnels appear nowhere in this market; free audits are the dominant entry offer (5 of 8)."
}

Field rules:
- `funnel_entry_url` — the page where the funnel starts for this service: the landing page or the
  service page whose CTA opens it, not the home page unless the funnel genuinely starts there.
- `funnel_type` — the path in a few words, entry offer first: "Free audit → strategy call",
  "Gated guide → email course → call", "Quiz → personalised report", "Webinar → consult",
  "Contact form only". This is the column an operator scans to see which funnel types the market
  runs and which nobody runs, so keep it under about 60 characters — a sentence here stops being
  a column.
- `starting_price` — the published price of the first paid step the funnel shows (a tripwire, a
  front-end package), verbatim as published ("From $497"). null when the funnel shows no price,
  which is the common case — never estimate one.
- `offering_summary` — two or three sentences tracing the path: what the entry offer promises, which
  fields the gate asks for, what the next step is, and any stated follow-up. Base it only on what the
  fetched pages show — never infer or embellish — and name any step you could not reach. It must
  stand on its own without the URL being opened, since this is what a reader sees next to the
  competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the pages themselves were opened and the path confirmed rather than inferred
  from a search snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned
  against the requested 10, explain any gap, and name near-misses or false positives that were
  excluded and why. Then, in a sentence or two, say which funnel types dominate this market and which
  appear nowhere — the funnel stage reads this as its starting point. Write it as prose for a human
  reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
