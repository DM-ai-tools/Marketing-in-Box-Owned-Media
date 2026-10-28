# Competitor Analysis Prompt — Podcast

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or a substitutable service to the same kind of customer, in the same market.
The podcast is the lens this listing is judged through, never the service the competitor has to
sell. Do NOT include podcast-production studios, media companies or show-hosting agencies
unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service whose founder/principal
  hosts the company's own podcast
  "full_stack_niche" = a full-service company in the same industry whose founder/principal hosts
  the company's own podcast, with the target service among its listed offerings
  If missing or empty, include both types.

service: {SERVICE} — the service the target sells (inferred from {TARGET_URL} if not specified).
  The qualifying artefact is a genuine, ongoing, self-hosted PODCAST produced and hosted by the company or
its founder/principal, distinct from guest appearances on someone else's show.
  (Only include companies where: (1) the company CURRENTLY operates as a service provider for
  the target service — not a business that has pivoted entirely into courses, coaching, or
  digital products, even if it started as a service company; and (2) the podcast is confirmed
  self-hosted/company-branded via direct verification, not just a directory listing. Note
  whether the podcast's topical focus is the target service specifically (strongest match) or a
  broader/adjacent topic like general industry news, leadership, or career advice (weaker match,
  but still qualifying) — flag this distinction rather than treating all matches as equally
  on-topic.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}. excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} based companies currently offering the target's core service AND with a confirmed,
  ongoing, self-hosted podcast
- Apply competitor_type and niche filtering if provided
- Exclude podcasts hosted by media companies, magazines, academics, or unrelated agencies with
  no genuine offering of the target service
- Exclude companies whose original service-business roots have been fully superseded by a
  course/coaching/digital-product model, even if the podcast itself remains topically relevant —
  flag as a caveat rather than silently excluding if the historical connection is strong
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate by fetching the actual company site and podcast page/listing directly,
  confirming both current service offering and podcast ownership

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: have a podcast explicitly branded as the company's own (not just hosted
  by an employee independently), cover the target topic as a primary or major subject, and can
  point to real client testimonials or case studies confirming active service delivery
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites,
  aggregators, and podcast/media companies that are not themselves providers of the target service
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified) and
  separately flag topical_focus (Primary: target service / Secondary or adjacent topic)

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Confirmed company ownership of the podcast (highest weight)
- Topical directness (topic-specific podcasts score higher than general industry/leadership/
  career podcasts)
- Confirmation that the company currently operates as a service provider, not solely a course/
  coaching business
- Business model similarity and geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object with this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "podcast_page_url": "https://competitor1.com.au/podcast/",
      "verification_confidence": "Verified",
      "topical_focus": "Agency operations and pricing",
      "offering_summary": "Weekly 30-minute interview show, 140 episodes since 2022, hosted by the founder and used to book strategy calls; the company currently sells SEO retainers and a paid audit, both pitched in the mid-roll.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 4 of 10: few agencies in this niche run a podcast at all. To close the gap, next steps are to search Apple Podcasts by the two adjacent niches (B2B SaaS marketing, ecommerce growth), check the three industry associations' member directories for member-run shows, and widen from Australia to New Zealand."
}

Field rules:
- `podcast_page_url` — the show's own page on the company site where one exists, otherwise the primary platform
  listing (Apple, Spotify).
- `topical_focus` — what the show is actually about, in a few words, in its own terms. This is
  the column that shows which subjects the market's shows already cover. Keep it under about 60
  characters.
- `offering_summary` — one or two sentences on the podcast **and the company's current service offering**: format,
  episode length, cadence, roughly how many episodes and how recent, who hosts — and what the
  company sells today, since the show is judged by what it feeds. Base it only on what the fetched page shows — never infer
  or embellish. It must stand on its own without the URL being opened, since this is what a reader
  sees next to the competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and give
  **specific next steps for closing it** — the searches, platforms or adjacent niches worth
  trying next, named concretely enough to act on rather than as general advice. Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
