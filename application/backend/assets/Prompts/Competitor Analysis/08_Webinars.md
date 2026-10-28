# Competitor Analysis Prompt — Webinars

Find UP TO 10 direct competitors in the {LOCATION} market for the company: {TARGET_URL},
using the optional targeting inputs below to control how narrow/broad the competitor set should be.
If fewer than 10 genuinely qualifying competitors can be found and verified, return fewer rather
than padding the list with weak or unverified matches — flag any lower-confidence entries instead
of silently including them.

WHO COUNTS AS A COMPETITOR HERE: a business that competes with the target for the same buyers —
it sells the same or a substitutable service to the same kind of customer, in the same market.
The webinar programme is the lens this listing is judged through, never the service the
competitor has to sell. Do NOT include companies that sell webinar production, event marketing
or virtual-event services unless that is what the target itself sells.

Additional inputs (all optional unless stated):

competitor_type:
  "niche_specialist" = a company specialised in the target's core service that also runs its own
  educational webinar program
  "full_stack_niche" = a full-service company in the same industry that prominently uses webinars
  as a lead magnet or educational resource
  If missing or empty, include both types.

service: {SERVICE} — the service the target sells (inferred from {TARGET_URL} if not specified).
  Look for webinars these same-service rivals run about that service.
  IMPORTANT CLARIFICATION (strict): only include companies that explicitly use webinars — hosted,
  branded, and offered BY THE COMPANY ITSELF — as lead magnets, offers, or educational resources
  covering the target topic. Do NOT include:
    - companies whose founder/staff appear as a guest presenter at a webinar hosted and branded
      by a third party (e.g. a local council, government small-business program, industry
      association, or another company's webinar series) — the offer belongs to the host, not
      the competitor
    - companies offering paid, one-off corporate training that happens to be delivered in
      "webinar style" but is not a marketing lead magnet or free educational resource
    - companies where "webinar" appears only in a client testimonial, a stray mention, or
      third-party coverage, with no corresponding webinar program described on the company's own
      site
    - in-person-only workshops/masterclasses mislabeled as webinars

niche: {NICHE} — leave open if not specified.

location: {LOCATION}, if not specified analyze it from {TARGET_URL}, boosting local leaders if provided.

excluded_competitors: [list any domains already sourced in prior runs]

Competitor Selection Logic:
- Find {LOCATION} companies that offer the target's core service AND run their own branded
  webinar program (live and/or on-demand) as a lead generation or educational asset
- Apply competitor_type and niche filtering if provided
- Exclude companies per the strict clarification above — verify ownership of the webinar offer,
  not just proximity to the word "webinar"
- MUST have strong organic visibility (ranking pages, consistent SEO presence)
- Verify each candidate by fetching the actual page where possible; do not rely on titles or
  search snippets alone to confirm the webinar is self-hosted and ongoing

CRITICAL REQUIREMENTS:
- ONLY include high-quality competitors with strong organic traffic/SEO presence in {LOCATION}
- Prefer companies that: have a dedicated webinar or "free webinars" page/nav item, show an
  active on-demand library or upcoming-session signup, and cover the target topic
- Exclude: directories, marketplaces, freelancer platforms, low-quality or inactive sites,
  aggregators, government/council-hosted training programs, and any company whose only webinar
  presence is as a third-party guest speaker
- Mark verification_confidence for each entry (Verified / Partially verified / Unverified)

Ranking & Scoring Guidance:
similarity_score (0–1) should reflect:
- Confirmed ownership of a self-hosted, ongoing webinar program (highest weight)
- Verification confidence
- Service and niche match
- Geographic relevance (boost if location provided)
- Organic search competitiveness overlap with the target

Output Requirements (STRICT):
Return ONLY a valid JSON object with this structure:
{
  "competitors": [
    {
      "domain": "competitor1.com.au",
      "name": "Competitor Inc",
      "webinar_page_url": "https://competitor1.com.au/webinars/",
      "verification_confidence": "Verified",
      "offering_summary": "Runs a monthly 45-minute live webinar with two named presenters, gated behind name, email and company size, replays kept on a library page, and each session closing on a free-audit offer.",
      "similarity_score": 0.92,
      "avg_position": 12.5,
      "intersections": 450
    }
  ],
  "notes": "Returned 6 of 10. agencyx.com.au was excluded because its only webinar page is a 2023 one-off with a dead registration form; agencyy.com.au runs webinars but only as paid conference sessions, not as a lead offer; agencyz.com.au hosts on a third-party platform with no page of its own."
}

Field rules:
- `webinar_page_url` — the webinar registration or replay page, not a blog post announcing it.
- `offering_summary` — one or two sentences on the webinar offering: live or evergreen, cadence, length, who
  presents, what registration asks for, whether replays are kept, and what the session sells
  at the end. Base it only on what the fetched page shows — never infer
  or embellish. It must stand on its own without the URL being opened, since this is what a reader
  sees next to the competitor.
- `verification_confidence` — exactly one of "Verified", "Partially verified", or "Unverified",
  reflecting whether the page itself was opened and confirmed rather than inferred from a search
  snippet.
- `avg_position` / `intersections` — use null when unavailable.
- `notes` — required, always present even when 10 results are returned. State how many were returned against the requested 10, explain any gap, and give the
  **specific reason for each notable near-miss excluded** — name the candidate and what
  disqualified it, rather than summarising the exclusions as a group. Write it as
  prose for a human reader, not as a data structure.

Do NOT include any explanation, markdown, or extra text outside the JSON object itself. The
competitor listing and the notes section are rendered from this object for an operator to approve —
so every field above must be populated rather than described elsewhere in prose.
