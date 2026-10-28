# Competitor Analysis Prompt — CRO (Conversion Rate Optimisation), sub-service pages

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers of
this sub-service — it sells {SERVICE} (or a substitutable offering) to the same kind of customer,
in the same market. Conversion of their {SERVICE} page is the lens this listing is judged through,
never the service the competitor has to sell. It is NOT a company that sells conversion rate
optimisation, CRO audits, A/B testing or landing page services, unless that is what the target
itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core sub-service/industry, whose page
  for that sub-service is its own conversion surface
  "full_stack_niche" = a full-service company in the same industry as the target that publishes a
  distinct, dedicated page for the same sub-service
  If missing or empty, include both types.

sub-service: {SERVICE} — the target's core sub-service/product (inferred from {TARGET_URL} if not
specified).
  (Only include companies that publish a genuine, dedicated page selling {SERVICE} as its own
  offering — a real sub-service page with its own proposition, described process, proof and call to
  action. A company that sells {SERVICE} only as one line item inside a broader services page, with
  no page of its own, does not count: this listing is what the client's {SERVICE} page is audited
  and rewritten against, so every entry must be a page a visitor could actually land on and convert
  from.)

niche: {NICHE} — the target's industry/vertical (e.g. the same industry as {TARGET_URL}). If not
specified, infer the target's core industry from the site and match competitors within it. If
explicitly left open, do not restrict by niche.

location: {LOCATION}, prioritise competitors based in or explicitly serving that location, and rank local leaders higher.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- If BOTH sub-service and niche are provided: find companies in {LOCATION} publishing a dedicated
  {SERVICE} page AND clearly operating in the specified niche. Apply competitor_type filtering.
  Exclude generalists with no niche alignment.
- If ONLY the sub-service is provided: find strong {LOCATION} competitors with a dedicated
  {SERVICE} page, regardless of niche, prioritising strong SEO presence and organic traffic. Boost
  local relevance if location is provided.
- If the sub-service is not clearly identifiable from the target: infer the target's core
  sub-services from {TARGET_URL} and find direct competitors offering a comparable mix, matching
  business model (agency, specialist, in-house consultancy, SaaS-adjacent, etc.).
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate by fetching the actual page where possible; do not rely on titles or
  search snippets alone to confirm the {SERVICE} page is genuine and specific

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies whose {SERVICE} page: states a specific proposition, describes a concrete
  process or methodology, carries real proof (case studies, numbers, named clients), and closes on
  a clear conversion action
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators. Ensure competitors are true service providers, not aggregators.
- Exclude CRO / conversion-optimisation agencies and tools unless the target itself sells CRO.
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified) based
  on whether the {SERVICE} page was directly confirmed

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Sub-service match ({SERVICE} specifically) — highest weight if the sub-service was specified
- Niche/industry match — highest weight if niche was specified
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
      "cro_page_url": "https://competitor1.com.au/meta-ads-management/",
      "verification_confidence": "Verified",
      "offering_summary": "Dedicated page for the sub-service opening on a named outcome, a four-step process and three case studies with spend and return figures, closing on a booked audit.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 7 of 10. Three candidates were excluded for having no page of their own for the sub-service — it appeared only as a line item on a combined services page; two more had a page with no proof or described process."
}

Field rules:
- `cro_page_url` — the competitor's own page for the sub-service, not their home page and not a
  combined services index. This is the page the client's own page is being benchmarked against.
- `offering_summary` — one or two sentences, in plain prose, describing what this competitor's
  {SERVICE} page actually does as a conversion surface: the proposition it leads with, the process
  it describes, and any concrete proof it cites. Base it only on what the verified page states;
  never infer or embellish. This is the summary a reader sees next to the competitor, so it must
  stand on its own without the URL being opened.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified".
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned
  against the requested 10, explain any gap, and name near-misses or false positives that were
  excluded and why. Write it as prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing (each competitor with its page URL, verification confidence, and offering
summary) and the notes section are rendered from this object — so every field above must be
populated rather than described elsewhere in prose.
