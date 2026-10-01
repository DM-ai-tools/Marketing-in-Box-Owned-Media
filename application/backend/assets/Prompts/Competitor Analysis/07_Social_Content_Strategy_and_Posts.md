# Competitor Analysis Prompt — Social Content Strategy and Social Media Post Creation

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL}, whose
social media presence will be audited and benchmarked against the target's own, post for post.
Use the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or substitutable services/products to the same kind of customer, in the same
market. It is NOT a company that sells social media management, content creation or marketing
services, unless that is what the target itself sells. A mortgage broker's competitors are other
lenders and brokers; a roofer's are other roofers; only a marketing agency's are marketing
agencies. The audit that consumes this list compares each competitor's posts, cadence and
service coverage with the target's own, so a competitor that does not sell what the target sells
makes every comparison in it meaningless.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service as its main offer
  "full_stack_niche" = a broader company in the same industry that offers the target's core
  service as a distinct line of business
  If missing or empty, include both types.

service: the target's own core services/products, inferred from {TARGET_URL} — what the target
actually sells, and therefore what a competitor must also sell.

niche: {NICHE} — leave open if not specified. Where given, it describes the target's own
industry and the buyers it serves; competitors must be in it.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Determine what the target sells and to whom from {TARGET_URL} (and {NICHE} where given) first;
  then find {LOCATION} companies selling the same or substitutable services to the same buyers
- Apply competitor_type and niche filtering if provided
- REQUIRED: an active public social presence. Each competitor must have at least one Facebook,
  Instagram or LinkedIn account that is linked from its own website (header, footer, contact
  page) and has posted within roughly the last six months. Name the platforms found in
  `offering_summary`. A strong competitor with no findable or no active social account cannot be
  audited, so exclude it and say so in `notes`
- Prefer competitors whose social activity is visibly planned — recurring themes or pillars, a
  steady cadence, consistent formats, posts that lead back to a service or offer — over accounts
  that post sporadically, since a planned feed is the more useful benchmark
- Exclude marketing, social media and content agencies that merely serve the target's industry
  (e.g. "social media for mortgage brokers") — they are suppliers to the target's market, not
  competitors in it — unless the target is itself such an agency
- CRITICAL: verify genuine {LOCATION} presence for every candidate — check for an {LOCATION}
  phone number, office address, or other locale-specific evidence. Businesses running
  auto-localised international SEO pages (e.g. a /au/ URL path on a site headquartered
  elsewhere, with leftover pricing in foreign currency or references to non-{LOCATION} retail
  events/dates) must be excluded even if the page itself reads as a strong topical match.
- Verify each candidate by fetching the actual page where possible

CRITICAL REQUIREMENTS:
- ONLY include genuine, established competitors with a real presence in {LOCATION} — strong
  organic search visibility for the target's services is a good signal of one
- Exclude: directories, marketplaces, comparison/aggregator sites, freelancer platforms, and
  low-quality or inactive businesses
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified)

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Service and buyer overlap with the target (highest weight)
- Strength and activity of the social presence available to audit
- Verification confidence, including confirmed genuine {LOCATION} presence
- Niche match
- Geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object with this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "service_page_url": "https://competitor1.com.au/social-media-strategy/",
      "verification_confidence": "Verified",
      "offering_summary": "Publishes a documented pillar-and-cluster social strategy on the service page, and the live profiles visibly follow it: three named content pillars, a fixed weekly cadence per platform, and every post closing on the same audit CTA.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 7 of 10. Three false positives were excluded: two run profiles that have not posted in over a year, and one posts only recycled motivational quotes with no connection to any service."
}

Field rules:
- `service_page_url` — the page evidencing the social offering or the strategy behind it — the service page, or
  the page the social profiles link back to.
- `offering_summary` — one or two sentences describing the **strategy-to-post pipeline observed**: whether a
  documented strategy exists, and whether the live posts actually follow it — pillars, cadence,
  formats, and the call to action posts converge on. A stated strategy the posts ignore is the
  most useful observation here, so say so when that is what the pages show. Base it only on what the fetched page shows — never infer
  or embellish. It must stand on its own without the URL being opened, since this is what a reader
  sees next to the competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and name every
  false positive excluded and why (dead profiles, agencies posting only about themselves,
  scheduled-quote accounts with no strategy behind them). Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
