# Competitor Analysis Prompt — CRO (Conversion Rate Optimisation), service pages

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL}, whose
page for the same service converts better than the target's own. This listing is what the target's
service page is audited and rewritten against: the rewrite takes the conversion strategies these
pages use (proposition, proof, offer, friction, calls to action) and applies them to the target's
page. If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer
rather than padding the list with weak or unverified matches — flag any lower-confidence entries
instead of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers — it
sells the same or a substitutable service to the same kind of customer, in the same market. It is
NOT a company that sells conversion rate optimisation, CRO audits, A/B testing or landing page
services, unless that is what the target itself sells. "CRO" here is the lens the competitor's page
is judged through, never the service the competitor has to sell. An AI SEO agency's competitors are
other SEO and AI search agencies; a mortgage broker's are other brokers and lenders; a roofer's are
other roofers.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's service, whose page for that service
  is its main conversion surface
  "full_stack_niche" = a broader company in the same industry that sells the target's service as a
  distinct line of business, with its own dedicated page for it
  If missing or empty, include both types.

service: {SERVICE} — the service the target sells and whose page is being rewritten. If not
specified, infer it from {TARGET_URL}: what the target actually sells, and when it sells several,
the headline service its site leads with.
  (Only include companies that sell this same service AND publish a dedicated page selling it — a
  real service page with its own proposition, described process, proof and call to action. A
  company that sells the service only as one line item inside a broader services page, with no
  page of its own, does not count: every entry must be a page a visitor could land on and convert
  from.)

niche: {NICHE} — the target's industry/vertical (e.g. the same industry as {TARGET_URL}). If not
specified, infer the target's core industry from the site and match competitors within it. If
explicitly left open, do not restrict by niche.

location: {LOCATION}, prioritise competitors based in or explicitly serving that location, and rank local leaders higher.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- First establish what the target sells and to whom, from {TARGET_URL} (and {NICHE} where given).
  Then find {LOCATION} companies selling that same or a substitutable service to the same buyers.
- Open the target's own page for this service where you can find it, and prefer competitor pages
  that are visibly stronger on conversion than it: a sharper above-the-fold proposition, more
  specific proof (numbers, named clients, case studies), a lower-friction entry offer, clearer
  calls to action, objection handling, trust signals. A competitor whose page is weaker than the
  target's teaches the rewrite nothing and should be ranked lower or excluded.
- Cover the market's range of business sizes: include competitors at the target's own level and,
  where they exist, larger players the target is trying to win business from. Do not fill the list
  only with national leaders the target does not realistically compete with.
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate by fetching the actual page where possible; do not rely on titles or
  search snippets alone to confirm the service page is genuine and specific

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies whose service page: states a specific proposition, describes a concrete
  process or methodology, carries real proof (case studies, numbers, named clients), and closes on
  a clear conversion action
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators. Ensure competitors are true service providers, not aggregators.
- Exclude CRO / conversion-optimisation agencies and tools unless the target itself sells CRO.
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified) based
  on whether the service page was directly confirmed

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Service match (the same service the target sells) — highest weight
- Buyer and niche match
- How much stronger the page's conversion design is than the target's own page
- Business model similarity (agency vs specialist vs in-house-style consultancy)
- Geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object, with up to 10 competitors, in exactly this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "service_page_url": "https://competitor1.com.au/ai-seo-services/",
      "verification_confidence": "Verified",
      "category": "Free audit offer above the fold",
      "offering_summary": "Sells AI search optimisation to mid-size B2B firms; the page opens on a guaranteed-deliverable free visibility audit, shows three named case studies with traffic numbers above the fold, and repeats one CTA after every section.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 7 of 10. Three candidates were excluded as directories or aggregators; two CRO agencies were left out because the target does not sell CRO; one page was weaker than the target's own."
}

Field rules:
- `service_page_url` — the competitor's own page for the same service, the one a buyer of it lands on.
- `category` — the page's main conversion mechanism, in a few words ("Free audit offer", "Case-study
  led", "Published pricing", "Calculator / quiz", "Booked call with stated deliverable"). Name what
  the page actually does, not what it claims.
- `offering_summary` — one or two sentences, in plain prose: what the competitor sells and to whom,
  then the conversion elements on the page that are stronger than the target's (proposition, proof,
  offer, CTA, friction). Base it only on what the verified page states; never infer or embellish.
  This is the summary a reader sees next to the competitor, so it must stand on its own without the
  URL being opened.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified".
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned
  against the requested 10, explain any gap, and name near-misses or false positives that were
  excluded and why. Write it as prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing (each competitor with its service page URL, verification confidence, and
offering summary) and the notes section are rendered from this object — so every field above must
be populated rather than described elsewhere in prose.
