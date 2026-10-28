# Competitor Analysis Prompt — Offers (Best Published Pricing/Packages)

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or a substitutable service to the same kind of customer, in the same market.
Published pricing is the lens this listing is judged through, never the service the competitor
has to sell. Do NOT include companies that sell pricing strategy, packaging or offer-design
services unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service/industry
  "full_stack_niche" = a full-service company in the same industry prominently offering the
  target service with a clear packaged offer
  If missing or empty, include both types.

service: {SERVICE} — the specific service/product the target sells (inferred from {TARGET_URL}
if not specified).
  (Only include companies with a clearly published, comparable OFFER for this service — a
  dedicated packages/pricing page with visible tiers, a stated starting price, or itemised
  inclusions. Vague "contact us for a quote" pages without any published packaging should be
  deprioritised or excluded in favour of companies with transparent, structured offers.)

niche: {NICHE} — leave open if not specified (no industry restriction beyond matching the
target's core service).

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} companies that offer the target's core service AND publish a clear, comparable
  offer (tiered packages, starting price, itemised deliverables, add-ons)
- Apply competitor_type and niche filtering if provided
- Exclude generalists with no real offer structure (vague service blurbs with no packaging or
  pricing signal)
- MUST have strong organic visibility (ranking pages, consistent SEO presence)

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in Australia
- Prefer companies that: publish transparent pricing/package tiers, itemise deliverables, and
  show credible client results or reviews
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators
- Exclude pricing consultancies and offer-design agencies unless the target itself sells those

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Offer clarity/strength (highest weight — tiered pricing, itemised inclusions, add-ons)
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
      "offer_page_url": "https://competitor1.com.au/packages/",
      "starting_price": "From $1,500/mo",
      "verification_confidence": "Verified",
      "offering_summary": "Three published tiers (Starter / Growth / Scale) at $1,500, $3,200 and $6,500 per month, each itemising content volume, ad spend managed and reporting cadence. Setup fee $990, waived on annual. Add-ons for video and landing pages priced separately.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 6 of 10. Four otherwise-strong candidates were excluded for publishing no packaging at all (quote-only enquiry forms): agencyx.com.au, agencyy.com.au and two DesignRush listings. One near-miss, agencyz.com.au, shows tiers but no price of any kind, so it was included at reduced confidence rather than dropped."
}

Field rules:
- `offer_page_url` — the page where the offer is actually published (a packages, pricing or plans
  page). Not the homepage and not a generic service page: this is the page the value-ladder stage
  benchmarks against, so it has to be the one a buyer would compare.
- `starting_price` — the lowest published price, with its unit and any "from" qualifier kept:
  "From $1,500/mo", "$990 setup + $2,400/mo", "$4,500 one-off". Use null only when no price of any
  kind is published — never estimate one, and never convert currencies. An invented price would flow
  straight into this client's own pricing decisions.
  **Leave tax out of this field.** Report the figure the page states without any GST / VAT / sales-tax
  qualifier: a page advertising "$1,490 + GST per month" is reported as "From $1,490/mo". This field
  is a column operators scan down to compare offers, and tax notation makes near-identical prices
  look different. Do NOT do the arithmetic either way — never gross a price up to include tax, and
  never strip tax out of a tax-inclusive figure to derive a lower one; both would be invented
  numbers. Where the page states a tax basis, say so in `offering_summary` instead ("priced ex-GST",
  "$1,639/mo inc GST"), so the comparison stays honest without cluttering the price.
- `offering_summary` — one or two sentences describing the **shape of the offer**: how many tiers,
  what each costs, what is itemised inside them, setup fees, contract minimums, and priced add-ons.
  Base it only on what the page states. This is what a reader sees next to the competitor, so it
  must stand on its own without the URL being opened.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the published offer and its price were confirmed on the page itself.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned
  against the requested 10, explain any gap, and name near-misses or false positives that were
  excluded and why (quote-only pages, directories, aggregators, no packaging). Write it as prose for
  a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing (each competitor with its offer/packages page URL, starting price, verification
confidence, and offer summary) and the notes section are rendered from this object for an operator
to approve — so every field above must be populated rather than described elsewhere in prose.
