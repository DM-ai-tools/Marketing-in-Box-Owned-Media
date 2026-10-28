# Competitor Analysis Prompt — Book

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or a substitutable service to the same kind of customer, in the same market.
The founder's book is the lens this listing is judged through, never the service the competitor
has to sell. Do NOT include publishers, ghostwriters or book-coaching businesses unless that is
what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service whose founder has
  authored a genuine published book on the topic
  "full_stack_niche" = a full-service company in the same industry whose founder/staff has
  authored a genuine published book on the topic
  If missing or empty, include both types.

service: {SERVICE} — the service the target sells (inferred from {TARGET_URL} if not specified).
  The qualifying artefact is a genuine, published, full-length BOOK (paperback and/or ebook sold via Amazon,
Booktopia, or another retail channel; not a short gated PDF/ebook lead magnet, which is a
distinct asset category) authored by the company's founder or the company itself.
  (Only include companies where a real book can be verified — ideally with a retail listing
  confirming title, author, and format. Prefer books where the topical match is direct and
  explicit in the title/description, not incidental. Note whether the author's underlying
  business is a genuine multi-person service company versus a solo consultant/coach — both can
  qualify, but this distinction should be flagged, not hidden.)

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} companies/consultants offering the target's core service AND with a
  founder-authored, genuinely published book on the topic
- Apply competitor_type and niche filtering if provided
- Exclude companies where the closest match is a short downloadable ebook/PDF lead magnet, a
  template, or an unpublished/self-only distributed document
- Exclude claims of authorship that cannot be verified against an actual retail listing or clear
  publication record
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate directly (book retail listing plus the company's own site) rather than
  relying on a single secondary source's claim of "author"

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: have a dedicated "the book" page linking the book to the company's
  service offering, show real client testimonials tied to the book or the business, and
  demonstrate direct topical alignment between the book and the target service
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites,
  aggregators, book-marketing/ghostwriting service providers, and generic industry textbooks by
  non-{LOCATION} based authors
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified) based
  on whether the book AND the company/service relationship were both directly confirmed

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Directness of topical match between the book and the target service (highest weight)
- Strength of the business as a genuine service-providing competitor (multi-person company
  scores higher than solo consultant/coach, though both qualify)
- Verification confidence
- Business model similarity and geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object with this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "book_page_url": "https://competitor1.com.au/the-book/",
      "verification_confidence": "Verified",
      "offering_summary": "A 190-page trade paperback on positioning for founder-led agencies, self-published in 2024 and used as the front door to a consulting practice: the book funnels to a paid workshop and a retainer offering.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 5 of 10. Three near-misses were excluded: two are 30-page PDFs marketed as books but gated as lead magnets, and one is a single chapter in a multi-author anthology with no business attached."
}

Field rules:
- `book_page_url` — the page the book is presented or sold on — the author's own book page where one exists,
  otherwise the retailer listing.
- `offering_summary` — one or two sentences on the book **and the business underneath it**: what the book is about,
  its length and format, roughly when it was published, and what it sells — the practice, the
  course, the speaking, the retainer. A book that exists to sell nothing is a different
  competitor from one that is the top of a funnel, and that is the distinction worth capturing. Base it only on what the fetched page shows — never infer
  or embellish. It must stand on its own without the URL being opened, since this is what a reader
  sees next to the competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and name every
  near-miss excluded and why (a self-published lead magnet posing as a book, an anthology
  chapter, a book by an author with no business behind it). Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
