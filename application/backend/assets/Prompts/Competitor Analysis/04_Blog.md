# Competitor Analysis Prompt — Blog

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or a substitutable service to the same kind of customer, in the same market.
The blog is the lens this listing is judged through, never the service the competitor has to
sell. Do NOT include companies that sell content writing, blogging or SEO-content services
unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service/industry
  "full_stack_niche" = a full-service company in the same industry prominently offering the
  target service
  If missing or empty, include both types.

service: {SERVICE} — the target's core service/product (inferred from {TARGET_URL} if not
specified).
  (Only include companies with an identifiable, actively-maintained BLOG or content hub covering
  this topic — i.e. a dedicated blog section or category, not a single orphan article.
  Prioritise blogs with genuine topical depth (guides, strategy breakdowns, case studies,
  downloadable templates) over agencies whose blog only mentions the topic in passing among
  broader content. Check for recency signals — a blog with no visible recent posts should be
  scored lower or flagged, not silently excluded.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} companies that offer the target's core service AND maintain a real content
  hub/blog with meaningful coverage of that topic
- Apply competitor_type and niche filtering if provided
- Exclude companies with no blog, or a blog that is clearly abandoned/stale, or one where the
  topic is only a minor, incidental subject
- MUST have strong organic visibility — a blog's own search visibility is itself a strong signal

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: publish frequently, cover the topic in genuine depth, include original
  data/case studies, and show third-party citation or ranking signals
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites, and
  aggregators
- Exclude content-writing and SEO-content agencies unless the target itself sells those

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Blog content depth/quality on the target topic specifically (highest weight)
- Publishing recency/cadence (penalise clearly stale blogs)
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
      "blog_url": "https://competitor1.com.au/blog/",
      "verification_confidence": "Verified",
      "content_focus": "Local SEO and Google Business Profile",
      "offering_summary": "Roughly 2,000-word posts published fortnightly, each with original screenshots and a named author; strong on technical how-to, thin on pricing or comparison topics. Most recent post is three weeks old.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 8 of 10. Two candidates were excluded as dormant — neither has published since 2024 — and one was a syndicated feed republishing vendor content rather than an owned blog."
}

Field rules:
- `blog_url` — the blog index or hub, not one individual post — the operator is judging the programme,
  not a single article.
- `content_focus` — the two or three topics this blog actually keeps returning to, in the
  blog's own terms. Not a genre label like "marketing": the point is to see which topics the
  market has saturated and which it has left alone. Keep it under about 60 characters: three short
  phrases at most, not a description.
- `offering_summary` — one or two sentences on the blog's quality and depth — typical post length, publishing
  cadence, whether posts carry original research, screenshots or named authors, and where they
  are thin. **State any recency concern explicitly**, including the date or age of the most
  recent post when it is visible: a blog whose last post is two years old is a very different
  competitor from one publishing weekly, and that difference must not be buried. Base it only on what the fetched page shows — never infer
  or embellish. It must stand on its own without the URL being opened, since this is what a reader
  sees next to the competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and name
  near-misses or false positives that were excluded and why (a news feed, a syndicated feed, a
  blog abandoned years ago). Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
