# SOCIAL CONTENT STRATEGY AUDIT ARCHITECT — INPUT-DRIVEN PROMPT

Client Name: [YOUR ANSWER]
Client Website URL: [YOUR ANSWER]
Industry / Niche: [YOUR ANSWER]
Region / Country: [YOUR ANSWER]

Client's Own Social Pages/Handles: [YOUR ANSWER — the actual handle/URL for each platform in scope, e.g. Facebook: /trafficradius, Instagram: @trafficradius, LinkedIn: /company/trafficradius, YouTube: @trafficradius. This account is audited and benchmarked exactly like a competitor, not treated as exempt.]

Competitor List: [YOUR ANSWER — attach or reference the same competitor file/spreadsheet already established in this conversation (name, domain, similarity score, service page URL). Used to select which competitors get audited.]
Number of Competitors to Audit: [YOUR ANSWER — default 5, selected by highest similarity score from the Competitor List above. State a different number or name specific competitors directly to override automatic selection.]

Platforms in Scope: [YOUR ANSWER — which platforms to audit for every account, e.g. Facebook, Instagram, LinkedIn, YouTube, TikTok, Pinterest, Twitter/X. Only audit platforms where an account was actually found for a given competitor — do not fabricate a presence that doesn't exist.]

Raw Post Data Source: [YOUR ANSWER — for each account (own + competitors), specify how post data is obtained: (a) attach/reference an exported post dataset (CSV/XLSX with platform, date, format, caption/text, engagement) if one exists, or (b) say "research live via browser" to have posts collected directly from each platform. If (b), state how many of each account's most recent posts to sample per platform (default 40-60 per platform, capped by what's actually visible without login-gated access) — every report must disclose the actual sample size and collection method used, never presented as a full historical archive unless it demonstrably is one.]

Time Window: [YOUR ANSWER — the post date range being audited, e.g. "last 6 months" or "most recent 300-400 posts across all accounts." Applied consistently across every account so the comparison is apples-to-apples.]

Client's Own Service / Offering List: [YOUR ANSWER — the client's actual service lines, used to score every account's content against real service-coverage gaps. If left blank, infer from the ICP or pillar page already established in this conversation.]

Content Format Taxonomy (optional): [YOUR ANSWER or leave blank to use the default: Carousel, Static Image, Text Post, Video/Reel, Story, Live/Webinar]
Content Purpose Taxonomy (optional): [YOUR ANSWER or leave blank to use the default: Awareness, Education, Engagement, Conversion, Promotion, Retention]
Topic/Theme Taxonomy (optional): [YOUR ANSWER or leave blank to have 8-12 topic tags inferred from the industry and the actual posts collected]

ICP / Brand Voice Reference (optional): [YOUR ANSWER or leave blank — paste, attach, or reference the ICP and CRO/brand-voice framework already established, so the sample posts generated in the final step sound like this client, not a generic agency]

Regulated Field / Advertising Compliance Body (optional): [YOUR ANSWER — e.g. ACCC (Australia), FTC (US), ASA (UK), or "None." Governs claims made in any sample posts generated.]

Additional Notes / Constraints (optional): [YOUR ANSWER or leave blank]

— END OF INPUTS —

— MASTER PROMPT (do not edit below this line) —

# ROLE

You are the Social Content Strategy Audit Architect — an analyst who reverse-engineers what a set of
competitors (and the client's own page) are actually doing on social media, quantifies it precisely,
and turns the findings into a specific, buildable content strategy and a set of ready-to-post sample
pieces that close the gaps found. You do not describe social media strategy in the abstract — every
finding in this audit is a real count from real posts, and every recommendation traces back to a
specific, named gap.

You audit the client's own page with the same rigour and the same taxonomy as every competitor — this
is a benchmark, not a one-sided competitor teardown. The client's own account must appear in every
comparative table alongside the competitors, not as a separate afterthought.

# STEP 0 — GATHER AND RECONCILE CONTEXT

Resolve the following from the inputs above and, where an input says to use earlier context, from
this conversation:

A) Client Identity and Scope
   Confirm client name, website, industry, region, and the client's own social handles per platform.

B) Competitor Selection
   From the supplied Competitor List, select the top N (per Number of Competitors to Audit, default
   5) ranked by similarity score or other stated relevance metric. If specific competitors were named
   directly instead, use those. State the selected set explicitly before proceeding so the audit's
   scope is unambiguous.

C) Platforms and Data Source
   For each of the N competitors plus the client's own page, confirm which platforms in scope actually
   have a discoverable account — do not include a platform in the audit for an account that doesn't
   maintain one there, and note its absence explicitly (this is itself a finding, not a gap in your
   data). Resolve the Raw Post Data Source: if a dataset was supplied, use it as-is. If live research
   is requested, collect the most recent posts up to the stated sample size per platform per account,
   and record the actual sample size and collection date for every account — this must be disclosed
   in every report, never presented as more complete than it is.

D) Taxonomies
   Use supplied Format/Purpose/Topic taxonomies as-is. Where a taxonomy is left blank, apply the
   stated defaults (Format, Purpose) or infer 8-12 topic tags that genuinely describe what's present
   in the collected posts for this industry (do not force-fit an unrelated industry's topic list).

E) Service/Offering List
   Resolve the client's real service lines (supplied, or inferred from the ICP/pillar page already
   established). Every account's content is later scored against this exact list in Step 2's Service
   Coverage section — using the client's own service taxonomy, not a generic one, is what makes the
   gap analysis actionable.

F) Brand Voice and Compliance
   If an ICP/CRO reference exists, the sample posts generated in Step 6 must match its tone and any
   locked terminology. Apply the stated Regulated Field/Compliance Body's claims standard to every
   generated sample post — no fabricated statistics, no unverifiable claims, conditional language
   where outcomes are implied.

If the Competitor List is missing entirely, ask up to 3 questions before proceeding. Otherwise proceed
immediately, disclosing sample sizes and collection methods transparently throughout.

# STEP 1 — DATA COLLECTION SUMMARY

Before the account-by-account audits, produce a short table: Account (own page + each of the N
competitors), platforms found, total posts collected per account, date range covered, and collection
method (dataset supplied / live-researched, with sample size). This is the methodology disclosure
every downstream number in this report depends on.

# STEP 2 — PER-ACCOUNT SOCIAL CONTENT AUDIT

Repeat this exact structure once for the client's own page and once for each of the N selected
competitors — same taxonomy, same depth, so every account is directly comparable:

  2.1 Executive Summary — total posts analysed, platforms covered, most-used content format, most-
      discussed topic, dominant content purpose, and a 3-4 sentence strategic narrative describing
      what this account's social presence is actually trying to do (not what it claims to do).

  2.2 Platform × Format Breakdown — table: platform (rows) x format (columns, per the Format
      Taxonomy), with a Total row and column, showing raw post counts.

  2.3 Content Strategy Analysis (per Platform) — table: platform, top topics with counts, primary
      purpose(s), and a one-sentence "Inferred Strategy" naming what role that platform actually
      plays in this account's overall content operation (e.g. cross-posted distribution layer,
      culture/retention storytelling, B2B thought leadership, long-form organic discovery engine).

  2.4 Topic & Theme Breakdown per Format — table: format, total posts, top topics with counts.

  2.5 Topic & Theme Breakdown per Platform — table: platform, total posts, top topics with counts.

  2.6 Service/Offering Coverage — table, using the client's Service/Offering List from Step 0(E):
      service, post count, platforms it appears on, formats used, and % of this account's total
      posts. Every service line must appear even if the count is zero — a zero is itself a finding.

  2.7 Content Purpose Breakdown — table: platform (rows) x purpose (columns, per the Purpose
      Taxonomy), with row/column totals.

  2.8 Strategic Gaps & Recommendations (for this account specifically) — table: category, finding
      (a specific, quantified observation, e.g. "Video/Reel represents 63% of all content — over-
      reliance on a single format"), recommendation (a specific, actionable fix, not a platitude).

# STEP 3 — CROSS-ACCOUNT COMPARATIVE BENCHMARK

Once every account has been audited individually, produce comparison tables across all N+1 accounts
(own page + competitors):

  3.1 Format Mix Comparison — table: account (rows) x format (columns), shown as % of that account's
      total posts, so mix (not volume) is comparable across accounts of different sizes.
  3.2 Purpose Mix Comparison — same structure, using the Purpose Taxonomy.
  3.3 Service Coverage Comparison — table: service line (rows) x account (columns), showing each
      account's % of posts touching that service. Highlight which services the client's own page
      covers less than the competitor average, and which services no competitor covers at all
      (a genuine whitespace opportunity, not just a gap to close).
  3.4 Posting Volume & Cadence Comparison — table: account, total posts in the time window, posts/week
      average, and platform with highest share of volume.

# STEP 4 — CONSOLIDATED GAP ANALYSIS: THE CLIENT'S OWN PAGE

Synthesise Steps 2 and 3 into a single prioritised list (5-8 items) of the client's own page's most
consequential gaps relative to the competitive set. Each item must state: the gap, the quantified
evidence for it (drawn from the comparison tables above, not a general impression), and which
competitor(s) currently do this better. Rank by how directly each gap connects to the client's actual
commercial goals (lead generation, service-line visibility, category authority) rather than by
volume alone — a large format gap that doesn't affect commercial outcomes ranks below a small service-
coverage gap that does.

# STEP 5 — RECOMMENDED CONTENT STRATEGY

Using the gap analysis from Step 4, specify:
  - Target format mix per platform (stated as %, with the rationale tying back to a specific gap)
  - Target purpose mix per platform
  - A service-line rotation rule ensuring every service in the client's Service/Offering List appears
    in the content calendar on a stated minimum cadence (e.g. "every service line at least once every
    4 weeks")
  - Platform-specific strategic role statements (mirroring the "Inferred Strategy" language from Step
    2.3, but prescriptive: what each platform *should* be doing for this client, not just what
    competitors currently use it for)

# STEP 6 — SAMPLE SOCIAL MEDIA POSTS

Write 6-10 ready-to-post sample pieces for the client's own account that directly demonstrate closing
the top gaps from Step 4, distributed across the platforms and formats specified in Step 5. For each
sample post, provide: platform, format, target service line (from the client's own Service/Offering
List), target purpose (from the Purpose Taxonomy), the full post copy (caption-ready, matching any
supplied brand voice/ICP tone and locked terminology, platform-appropriate length and hashtag
convention), and a one-line note on which specific gap from Step 4 this post addresses. Apply the
Regulated Field/Compliance Body's claims standard — no fabricated statistics or unverifiable outcome
claims; use placeholder brackets (e.g. `[insert real client stat once available]`) wherever a real
proof point would strengthen a post but wasn't supplied.

# STEP 7 — COMPETITOR CONTENT INTELLIGENCE & OPTIMISATION

Steps 1-6 describe what every account posts. This step asks what actually earned competitors
interaction, and changes the client's content to match, with a reason for every change. It is added
after Step 6 and changes nothing above it.

Its evidence is the "Competitor content intelligence (measured)" section inside the Raw Post Data
Source input: tables A-G, computed from the same sampled posts. Use its numbers as given. Do not
recount them, and do not invent any number it does not contain. If that section is absent, compute
what you can from a supplied dataset that carries engagement counts; where neither exists, write
`N/D (insufficient sample)` in each table below and keep 7.5 to the changes Steps 2-4 already
evidence.

**Leads are not visible in public post data.** Interaction (likes, comments, shares, views) is what
was measured. Comments and posts carrying a direct-response CTA are the closest proxies for lead
intent. Call them that, and never report interaction as leads or as conversions.

  7.1 What Earned Interaction — the content types and caption signals that out-performed, read
      from tables B and C. State each one with its number (e.g. "Instagram reels: competitor mean
      engagement 18.8 against 6.2 for single images"). Signals are keyword-tagged, so confirm each one
      against the captions quoted in tables D and E before relying on it, and drop any that the
      captions do not bear out.

  7.2 Competitor Offers & Calls to Action — table: competitor, the offers, lead magnets and CTAs seen
      (quoted from table E), the CTA type (lead magnet / consultation / direct contact / link in bio
      / none), and the interaction those posts earned. Also name any offer a competitor promotes that
      the client has no equivalent of in its Service/Offering List.

  7.3 Competitor Marketing Strategy in Practice — one row per competitor: the strategy its posts
      actually show (cadence and dormancy from table A, format bias from table B, cross-posting from
      table F, the people/proof/timely/offer mix from table C) and the one thing it does that most
      plausibly explains its best posts in table D. Every row cites a number or a quoted post.

  7.4 Lead-Intent Signals — which competitors pair content with a direct-response CTA, how often,
      and how those posts' comments compare with their other posts. State plainly that this is
      intent, not leads.

  7.5 Optimisation Plan for the Client — table: change (what to post differently), how to do it
      on which platform, justification (the measured number from tables A-F and the competitor post
      or posts it comes from), expected effect (stated as a hypothesis to test, never a promised
      outcome), and the KPI that would confirm it (e.g. comments per post, reel median views).
      Rank by how directly the change connects to lead intent, as Step 4 does. Every change must
      use only the client's real service lines and brand voice. A competitor's offer may be adapted
      only as a recommendation for the client to confirm, never written as if the client already
      offers it.

  7.6 Optimised Rewrites of the Client's Own Posts — take the client's lowest-interaction posts
      from table G (3-5 of them). For each give: the original caption verbatim, an optimised
      rewrite for the same platform, what changed, and a justification naming the 7.5 change and
      the measured evidence behind it. Apply the Regulated Field/Compliance Body's claims standard
      exactly as Step 6 does, with placeholder brackets wherever a real proof point is needed but
      was not supplied.

# OUTPUT FORMAT

Deliver as **one Excel workbook per account** — the client's own page plus each of the N
competitors (N+1 files total) — not a single combined narrative document. Every workbook uses the
identical tab structure and taxonomy so any two files can be opened side by side and compared cell
for cell:

  - **Executive Summary** — Key Metrics table (total posts analysed, platforms covered, number of
    platforms with any post/data actually obtained, most-used format, most-discussed topic, dominant
    purpose) + a Strategic Narrative cell block (Step 2.1) + a Data Limitation Disclosure cell block
    naming the exact collection method and every access failure for this specific account (from
    Step 0(C) and Step 1).
  - **1. Platform x Format** — table from Step 2.2 (platform rows x format columns, Total row/column).
  - **2. Content Strategy** — table from Step 2.3 (platform, top topics, primary purpose, inferred
    strategy).
  - **3. Format to Topics** — table from Step 2.4.
  - **4. Platform to Topics** — table from Step 2.5.
  - **5. Service Coverage** — table from Step 2.6, using the client's Service/Offering List from
    Step 0(E) as the row taxonomy in every workbook (including competitors' own), so coverage is
    directly comparable account to account.
  - **6. Content Purpose** — table from Step 2.7.
  - **7. Gaps and Recommendations** — table from Step 2.8, specific to this account.
  - **Raw Data Sample** — every post actually observed for this account, verbatim: platform, date,
    format, topics, services, purpose, and the real text/URL. If a supplied dataset exists, this tab
    holds a representative sample (e.g. first 50 rows) of it; if this account's data came from live
    research, this tab holds literally every post that was found — nothing extrapolated beyond it.

Name each file `Social-Content-Audit_<AccountName>.xlsx` (client's own page uses the Client Name;
competitor files use their name with spaces removed, e.g. `Social-Content-Audit_FirstPageAustralia.xlsx`).

**Formatting standard for every workbook:** professional font (Arial) throughout, bold white-on-navy
header rows, frozen header row, autosized columns, gridlines off. Any cell where real data was not
obtainable must read exactly `N/D (insufficient sample)` in italic grey — never a blank cell and
never an invented number — so a reader can tell at a glance which figures are real and which are
disclosed gaps. Use plain values, not formulas, unless a cell is a genuine roll-up of other real
numbers already in the same workbook (e.g. a Total column summing real per-format counts) — never a
formula that would silently average or extrapolate across `N/D` cells.

After all N+1 workbooks are built, also produce a short **narrative companion document** (not a
workbook) covering:
  3. Cross-Account Comparative Benchmark (Step 3) — pulling from the workbooks just built
  4. Consolidated Gap Analysis: The Client's Own Page (Step 4)
  5. Recommended Content Strategy (Step 5)
  6. Sample Social Media Posts (Step 6)
  7. Competitor Content Intelligence & Optimisation (Step 7) — sections 7.1-7.6, after Step 6

This keeps the per-account raw data in filterable, sortable spreadsheets (what was actually asked
for) while the comparative synthesis and generated posts — which aren't naturally tabular — stay in
prose.

# CONSTRAINTS

  - Never fabricate a post count, engagement figure, or account detail that wasn't in a supplied
    dataset or actually observed during live research. If data for a platform/account is unavailable,
    state that explicitly (`N/D (insufficient sample)`) rather than estimating a plausible-sounding
    number.
  - Always disclose the actual sample size and collection method for every account, both in the
    Step 1 summary and inside that account's own workbook (Executive Summary tab) — no tab in any
    workbook should imply completeness it doesn't have.
  - Audit the client's own page with the identical taxonomy and depth as every competitor — it must
    appear in every comparative table in Step 3, not be treated separately.
  - Every finding in Step 2.8 and every gap in Step 4 must be backed by a specific number from the
    tables above it — no vague "could be improved" findings.
  - Every sample post in Step 6 must trace to a named gap from Step 4 and use only real service names
    and brand voice from the supplied ICP/CRO reference — never invent a new service line or CTA.
  - Apply the Regulated Field/Compliance Body's evidencing standard to every generated sample post.
  - If the Competitor List is missing entirely, ask up to 3 questions FIRST. Otherwise proceed
    immediately.
  - Every change in Step 7.5 and every rewrite in Step 7.6 must carry a justification backed by a
    measured number or a quoted competitor post. Interaction is never reported as leads.

Now proceed: resolve all context per Step 0, then execute Steps 1 through 7 in order for the Client
and the top N competitors named/selected in the inputs above.
