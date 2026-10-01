import type { AssetDefinition, FieldDef } from "./types";

const COMPLIANCE_CHOICES = [
  "Healthcare",
  "Financial",
  "Legal",
  "Other regulated field: specify",
  "None",
];

function complianceField(): FieldDef {
  return {
    field_id: "compliance_sensitivity",
    label: "Compliance Sensitivity",
    kind: "enum_choice",
    required: true,
    source: "user_input",
    choices: COMPLIANCE_CHOICES,
    helpText:
      "Sets how cautiously claims get worded — hedged language, no absolute outcomes, disclaimers where the field requires them. Pick the regime the client actually advertises under; if two apply, pick the stricter.",
  };
}

function notesField(id = "additional_notes_optional", label = "Additional Notes (optional)"): FieldDef {
  return {
    field_id: id,
    label,
    kind: "text",
    required: false,
    source: "user_input",
    helpText:
      "Anything that must shape the output and no earlier question covered — constraints, banned angles, facts that have to appear.",
    placeholder:
      'e.g. Don\'t mention the Perth office — it closes in March. Always "paid social", never "social ads".',
  };
}

/** All ten Competitor Analysis stages share one input shape (per DAG_SOURCE_MAP.md):
 * target_url (required) + service/niche/location/competitor_type (optional) + excluded_competitors
 * (auto-deduped, never asked). Only the human-facing description of "service" differs per stage. */
function competitorAnalysisFields(serviceHint: string): FieldDef[] {
  return [
    {
      field_id: "target_url",
      label: "Target URL",
      kind: "text",
      required: true,
      source: "user_input",
      helpText: `The company/page you're benchmarking against for ${serviceHint}.`,
      placeholder: "e.g. https://competitor-agency.com.au/social-media-marketing",
    },
    {
      field_id: "service",
      label: "Service",
      kind: "text",
      required: false,
      source: "user_input",
      helpText: "The target's core service/product. Inferred from Target URL if left blank.",
      placeholder: "e.g. Social media marketing",
    },
    {
      field_id: "niche",
      label: "Niche",
      kind: "text",
      required: false,
      source: "user_input",
      helpText: "Leave open if not specified.",
      placeholder: "e.g. Full-arch and All-on-4 cases",
    },
    {
      field_id: "location",
      label: "Location",
      kind: "text",
      required: false,
      source: "user_input",
      default: "Australia-wide",
      helpText: "Defaults to Australia-wide if not specified, boosting local leaders if provided.",
      placeholder: "e.g. Melbourne, VIC",
    },
    {
      field_id: "competitor_type",
      label: "Competitor Type",
      kind: "enum_choice",
      required: false,
      source: "user_input",
      choices: ["niche_specialist", "full_stack_niche", "both"],
      helpText:
        "niche_specialist = specialised in the core service. full_stack_niche = full-service company prominently offering it.",
    },
    {
      field_id: "excluded_competitors",
      label: "Excluded Competitors",
      kind: "context_reference",
      required: false,
      source: "auto_from_context",
      fallback: "treat_as_empty_if_missing",
    },
  ];
}

function competitorAsset(
  asset_id: string,
  label: string,
  pairedMain: string,
  serviceHint: string,
): AssetDefinition {
  return {
    asset_id,
    label,
    category: "Competitor Research",
    description: `Benchmark competitors' ${serviceHint} so the paired "${pairedMain}" asset builds on real gaps, not guesses.`,
    live: false,
    writesContextKeys: [asset_id],
    pairedCompetitorAssetId: pairedMain,
    fields: competitorAnalysisFields(serviceHint),
  };
}

const ctx = (
  field_id: string,
  label: string,
  context_key: string,
  opts: Partial<FieldDef> = {},
): FieldDef => ({
  field_id,
  label,
  kind: "context_reference",
  required: opts.required ?? true,
  source: "auto_from_context",
  context_key,
  fallback: opts.fallback ?? "ask_user_if_missing",
  helpText: opts.helpText,
  placeholder: opts.placeholder,
  sub_key: opts.sub_key,
  overridable: opts.overridable,
});

const txt = (field_id: string, label: string, opts: Partial<FieldDef> = {}): FieldDef => ({
  field_id,
  label,
  kind: "text",
  required: opts.required ?? true,
  source: "user_input",
  default: opts.default,
  helpText: opts.helpText,
  placeholder: opts.placeholder,
});

/** A topic the operator picks from suggestions instead of typing cold.
 *
 * Same answer shape as `txt` — a string, rendered into the stage's INPUTS block identically — so
 * this is purely a change of *how the operator arrives at it*. `helpText` and `placeholder` still
 * matter: the card always offers a "write my own" field, and those are what guide it.
 *
 * `slot` must name a slot the backend knows (`SLOTS` in `app/services/headlines.py`). A typo would
 * surface as a 404 on the suggestion call at exactly the point in a run where the operator is
 * blocked on it, so `PHASE1_STAGES`/`PHASE2_STAGES` construction is not where it gets caught —
 * `tests/test_headlines.py` pins the slot table against the stage table instead.
 */
const topic = (field_id: string, label: string, slot: string, opts: Partial<FieldDef> = {}): FieldDef => ({
  field_id,
  label,
  kind: "headline_choice",
  headlineSlot: slot,
  required: opts.required ?? true,
  source: "user_input",
  helpText: opts.helpText,
  placeholder: opts.placeholder,
});

const num = (field_id: string, label: string, opts: Partial<FieldDef> = {}): FieldDef => ({
  field_id,
  label,
  kind: "number",
  required: opts.required ?? true,
  source: "user_input",
  default: opts.default,
  helpText: opts.helpText,
  placeholder: opts.placeholder,
});

const bool = (field_id: string, label: string, opts: Partial<FieldDef> = {}): FieldDef => ({
  field_id,
  label,
  kind: "boolean_flag",
  required: opts.required ?? true,
  source: "user_input",
  helpText: opts.helpText,
  conditional_children: opts.conditional_children,
});

const choice = (
  field_id: string,
  label: string,
  choices: string[],
  opts: Partial<FieldDef> = {},
): FieldDef => ({
  field_id,
  label,
  kind: "enum_choice",
  required: opts.required ?? true,
  source: "user_input",
  choices,
  default: opts.default,
  helpText: opts.helpText,
});

const file = (field_id: string, label: string, opts: Partial<FieldDef> = {}): FieldDef => ({
  field_id,
  label,
  kind: "file_attach",
  required: opts.required ?? true,
  source: "user_input",
  helpText: opts.helpText ?? "Paste the content here, or type \"skip\".",
  placeholder: opts.placeholder,
});

/** Context keys the *server* composes at save time, rather than the model writing them.
 *
 * Only `cro_client_settings` so far: `app/services/cro_settings.py` builds it from the CRO stage's
 * own intake answers when the stage is approved. It is declared on the producing asset like any
 * other write key — a reader should not have to care where a document came from — but the one place
 * the difference bites is the in-session context fan-out after a save, which points every declared
 * key at the document the stage just produced. That is right for the four keys the rewrite really
 * does state and wrong for this one, so `saveStage` skips these and lets them be read back from the
 * database when a stage actually wants one.
 */
export const SERVER_COMPOSED_CONTEXT_KEYS: ReadonlySet<string> = new Set(["cro_client_settings", "industry_profile"]);

export const ASSET_CATALOG: AssetDefinition[] = [
  // ---------------------------------------------------------------- Foundation
  {
    asset_id: "icp",
    label: "ICP — Ideal Customer Profile",
    category: "Foundation",
    description: "2-4 buyer personas with pains, goals, objections, and firmographic targeting — the root input every other asset builds on.",
    live: true,
    writesContextKeys: ["icp"],
    fields: [
      txt("company_name", "Company Name"),
      txt("website_url", "Website URL"),
      choice(
        "company_type",
        "Company Type",
        ["Agency / Service provider", "Business selling direct", "Product manufacturer", "Other: specify"],
        {
          helpText:
            "Shapes how procurement and operations get described. It does not decide who the profile is about — that's the next question.",
        },
      ),
      choice(
        "audience_type_icp_orientation",
        "Audience Type (ICP Orientation)",
        ["Direct Buyer of the Offer", "Client's Target Customer", "Channel / Partner Buyer"],
        {
          helpText:
            "Who the profile is about:\n" +
            "Direct Buyer — the person who pays for the offer\n" +
            "Client's Target Customer — your client's customer, when you're building this for an agency's client\n" +
            "Channel / Partner Buyer — a referrer, reseller, or distributor\n" +
            "Whichever you pick is held for the whole profile; the two psychologies are never mixed.",
        },
      ),
      choice("maturity_tier", "Maturity Tier", ["Newbie", "Intermediate", "Advanced"], {
        helpText:
          "How sophisticated this buyer already is about the category. A Newbie needs the problem explained; an Advanced buyer is comparing vendors on specifics and resents the basics.",
      }),
      txt("industry", "Industry (of the ICP you are targeting)"),
      txt("offer_type", "Offer Type", {
        helpText: "What is actually being sold, in category terms rather than pitch terms.",
        placeholder: "e.g. Fixed-fee monthly social media retainers",
      }),
      txt("service_product_price_terms", "Service/Product + Price/Terms", {
        helpText: `If not specified, we'll write "not specified (quote required)" rather than leaving it blank.`,
        placeholder:
          "e.g. Social retainer $2,500/mo including strategy, content and reporting. 3-month minimum, then monthly. Strategy sprint $3,500, credited if you continue.",
      }),
      txt("market_region_country", "Market / Region / Country"),
      choice("business_model", "Business Model", ["B2C", "B2B"], {
        helpText:
          "B2B when a business signs off and someone else may be the user — that adds procurement, budget cycles, and committee objections. B2C when the buyer is the user.",
      }),
      choice(
        "awareness_level",
        "Awareness Level",
        ["Unaware", "Only Problem-Aware", "Only Solution-Aware", "Product/Service-Aware", "Most Aware"],
        {
          helpText:
            "Where the buyer starts. Every asset downstream is pitched to this stage, so the wrong pick either patronises a ready buyer or overshoots a cold one:\n" +
            "Unaware — doesn't yet know they have the problem\n" +
            "Only Problem-Aware — feels the pain, doesn't know solutions exist\n" +
            "Only Solution-Aware — knows the category, not this business\n" +
            "Product/Service-Aware — knows this business, isn't convinced\n" +
            "Most Aware — ready; needs the offer and a reason to move now",
        },
      ),
      txt("company_size_revenue_or_household_income", "Company Size / Revenue Band (B2B) or Household Income Reality (B2C)", {
        helpText: "The money reality behind the decision — what they can actually spend, not what you'd like them to.",
        placeholder:
          "e.g. B2B: 20-100 staff, $5-20M revenue, owner signs off.\nB2C: household income $120-200k, mortgage on one income.",
      }),
      notesField("notes_constraints_optional", "Notes / Constraints (optional)"),
    ],
  },

  // ---------------------------------------------------------------- Pages & Conversion
  {
    asset_id: "cro",
    label: "CRO — Page Rewrite",
    category: "Pages & Conversion",
    description: "Conversion-rate-optimised rewrite of an existing (or new) page: audit findings, rewritten copy, locked sections, and a locked terminology map downstream assets reuse.",
    live: false,
    // `cro_client_settings` is the one key here the model does not write: the server composes it
    // from this stage's own intake answers when the stage is approved (`app/services/cro_settings.py`),
    // so a later run for another service inherits how this client sells instead of re-asking.
    writesContextKeys: [
      "cro_audit_findings",
      "cro_rewritten_copy",
      "cro_locked_sections",
      "cro_terminology_map",
      "cro_client_settings",
    ],
    pairedCompetitorAssetId: "competitor_analysis_cro",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      choice("page_scope", "Page Scope", ["PILLAR", "SUB-SERVICE", "LOCATION", "PRODUCT-CATEGORY", "COMPARISON"], {
        helpText:
          "Where this page sits in the site's hierarchy. It sets how broad the copy goes and what it must not compete with:\n" +
          "PILLAR — the main page for the whole service\n" +
          "SUB-SERVICE — one procedure or product beneath a pillar\n" +
          "LOCATION — the same service written for one suburb or city\n" +
          "PRODUCT-CATEGORY — a range rather than a single item\n" +
          "COMPARISON — us versus them, or option A versus option B",
      }),
      txt("target_service_or_sub_service", "Target Service or Sub-Service (exact name to use)", {
        helpText:
          "The exact wording to use throughout. It gets locked here and reused verbatim by every asset downstream, so spell it the way the client does.",
        placeholder: "e.g. Social Media Marketing",
      }),
      txt("parent_pillar_page_url", "Parent Pillar Page URL", {
        required: false,
        helpText: "Required only when Page Scope is SUB-SERVICE or LOCATION — it's what this page links up to.",
        placeholder: "e.g. https://northpathdigital.com.au/social-media-marketing",
      }),
      txt("sibling_pages_not_to_cannibalise", "Sibling Pages That Must Not Be Cannibalised", {
        required: false,
        helpText:
          "Pages already ranking for near-identical terms. Listing them keeps this rewrite from competing with the client's own results.",
        placeholder: "e.g. /seo-services\n/google-ads\n/content-marketing",
      }),
      txt("existing_page_url", "Existing Page URL", {
        // The page behind this URL is read automatically at the next question — see
        // `SCRAPE_SOURCES` in `pipeline/pipelineData.ts`.
        helpText:
          `The page being rewritten. I'll read it and fill in its copy at the next step, so you don't have to paste it. Or type "NEW PAGE" if there is no existing URL.`,
        placeholder: "e.g. https://northpathdigital.com.au/social-media-marketing",
      }),
      txt("existing_page_content", "Existing Page Content", {
        helpText:
          `Normally read straight from the URL above — you only see this if the page couldn't be read. Paste the full copy, or write "NEW PAGE — NO EXISTING COPY".`,
        placeholder:
          "e.g. paste everything on the page today — H1, every heading and paragraph, button labels, FAQ answers",
      }),
      txt("existing_ranking_keywords_or_gsc_queries", "Existing Ranking Keywords or GSC Queries", {
        required: false,
        helpText:
          "Straight out of Search Console if you have it. It's what stops the rewrite quietly dropping terms that already work.",
        placeholder: "e.g. social media marketing melbourne (390/mo, pos 9)\nsocial media agency cost (210/mo, pos 15)",
      }),
      txt("locked_offer_service_product_names", "Locked Offer / Service / Product Names", {
        required: false,
        helpText: "Names that must appear verbatim — trademarks, package names, anything contractually fixed.",
        placeholder: "e.g. GrowthLoop™ Retainer, Always-On Social®",
      }),
      txt("locked_section_names_or_headings", "Locked Section Names or Headings", {
        required: false,
        helpText: "Headings that can't be reworded — usually because something else links or anchors to them.",
        placeholder: "e.g. Our Guarantee\nPayment Options",
      }),
      txt("locked_content_blocks", "Locked Content Blocks", {
        required: false,
        helpText: "Copy that must survive word-for-word. Paste the block itself, not a description of it.",
        placeholder:
          "e.g. All results shown are from client accounts we have written permission to share.",
      }),
      txt("locked_legal_compliance_text", "Locked Legal / Compliance Text", {
        required: false,
        helpText: "Mandated wording and disclaimers. These get placed, never edited.",
        placeholder:
          "e.g. Results vary by market, budget and offer. Past campaign performance is not a guarantee of future results.",
      }),
      txt("client_industry", "Client Industry"),
      txt("sub_vertical_niche", "Sub-vertical / Niche", {
        helpText: "One level below the industry — the specific thing this page is about.",
        placeholder: "e.g. Social media marketing (sitting under Digital marketing)",
      }),
      choice("buyer_type", "Buyer Type", ["B2C", "B2B", "B2B2C"], {
        helpText: "B2B2C where you sell to a business that resells to consumers — the page then has to satisfy both readers.",
      }),
      choice("sales_motion", "Sales Motion", ["SELF-SERVE", "ENQUIRY-LED", "QUOTE-LED", "CONSULT-LED"], {
        helpText:
          "How the sale actually closes — it decides what the CTA can honestly ask for:\n" +
          "SELF-SERVE — they buy online, unattended\n" +
          "ENQUIRY-LED — they send a message, someone replies\n" +
          "QUOTE-LED — price has to be scoped first\n" +
          "CONSULT-LED — an appointment is the front door to the product",
      }),
      choice("geo_mode", "Geo Mode", ["SINGLE LOCATION", "MULTI-LOCATION", "NATIONAL", "ONLINE-REMOTE"], {
        helpText: "Decides whether the copy names places at all, and how many it can name before it stops reading naturally.",
      }),
      txt("region_location", "Region / Location(s)"),
      choice(
        "claim_substantiation_tier",
        "Claim Substantiation Tier",
        ["0 GENERAL", "1 CONSUMER-LAW", "2 PROFESSIONALLY REGULATED", "3 HEALTH-THERAPEUTIC"],
        {
          helpText:
            "Sets how far claims can be pushed. Pick by industry:\n" +
            "0 — retail, hospitality, trades, most B2B services\n" +
            "1 — anything advertised to consumers, or any comparative claim\n" +
            "2 — legal, financial, accounting, migration, insurance, real estate, education\n" +
            "3 — medical, dental, allied health, cosmetic, veterinary, supplements\n" +
            "Each tier inherits the constraints below it, so if the business straddles two, pick the higher one.",
        },
      ),
      choice(
        "pricing_disclosure_mode",
        "Pricing Disclosure Mode",
        ["A PUBLISHED", "B FROM-PRICE", "C RANGE", "D NO-PRICE-COST-DRIVERS", "E QUOTE-ONLY"],
        {
          helpText:
            "Decides how the pricing section gets written. Pick by how the price actually behaves — not by how much detail you happen to have:\n" +
            "A — one fixed, comparable price\n" +
            "B — a real entry price that scope pushes upward\n" +
            "C — genuinely variable within a band you can state\n" +
            "D — too bespoke to band, but you can name what drives it\n" +
            "E — tendered or regulated, so the quote process is the answer",
        },
      ),
      txt("pricing_facts", "Pricing Facts (only what the client has confirmed)", {
        required: false,
        helpText:
          "The only place a number can enter the page. Give the figures, what each one covers, what moves it, and what it excludes — matching the mode above (a band for C, the entry price and its assumptions for B, the cost drivers for D, the process and turnaround for E).\n" +
          "Skip this rather than guessing: anything missing becomes a visible [CLIENT TO CONFIRM: …] marker in the draft, never an invented price.",
        placeholder:
          "e.g. Social retainers: $2,500–$6,000/mo (strategy, content, community management, reporting). Moves on channel count, ad spend under management, and video production. Strategy sprint $3,500, credited against the first month. Excludes ad spend, billed at cost.",
      }),
      choice(
        "testimonials_before_after_permitted",
        "Testimonials & Before/After Permitted in This Jurisdiction",
        ["YES", "NO", "UNSURE"],
        {
          helpText:
            "Some regulated fields ban testimonials or before/after imagery outright, consent or not (health is the common one). Pick UNSURE and they're left out with a note rather than risked.",
        },
      ),
      // The terminology map. Picked once here, locked, and reused by every downstream asset — which
      // is why each one asks for a single word rather than a list of acceptable ones.
      txt("word_for_the_reader", "Word for the reader", {
        helpText: "One noun for the person reading. Mixing client and customer on one page reads as careless.",
        placeholder: "e.g. client — or business owner / marketing manager / founder",
      }),
      txt("word_for_the_thing_being_chosen_between", "Word for the thing being chosen between", {
        helpText: "What the options on the page are called collectively.",
        placeholder: "e.g. treatment — or package / tier / plan",
      }),
      txt("word_for_the_first_commitment_step", "Word for the first commitment step", {
        helpText: "What the first thing they book or buy is called.",
        placeholder: "e.g. consultation — or site visit / quote / demo",
      }),
      txt("word_for_the_business", "Word for the business", {
        helpText: "What the business calls itself in its own copy.",
        placeholder: "e.g. agency — or studio / team / partner",
      }),
      txt("words_the_buyer_uses_for_the_outcome", "Words the buyer uses for the outcome", {
        helpText: "Their words for what they want, not the industry's. Lift these from reviews, call notes, or enquiry emails.",
        placeholder: "e.g. more qualified leads, less wasted ad spend, a pipeline I can forecast",
      }),
      txt("words_to_avoid", "Words to avoid (internal jargon, banned phrases)", {
        required: false,
        helpText: "Internal jargon, competitor names, and anything the client has explicitly banned.",
        placeholder: 'e.g. cheap, "guaranteed rankings", "growth hacking", the word "boost" for paid ads',
      }),
      // Both offered as accept-or-replace rather than filled in silently: an operator may hold a
      // real ICP research document, or a hand-curated competitor list, that beats what the
      // pipeline produced. See `overridable` in data/types.ts.
      ctx("icp_document", "ICP Document", "icp_*", {
        overridable: true,
        helpText: "Uses the ICP generated in this run. Paste or attach a different one to override it.",
      }),
      ctx("competitor_analysis", "Competitor Analysis", "competitor_analysis_cro", {
        overridable: true,
        helpText:
          "Uses the competitor analysis approved in the sub-step above. Paste your own competitor list to override it.",
      }),
      txt("cro_framework", "CRO Framework", {
        default: "USE DEFAULT",
        helpText: 'Write "USE DEFAULT" to use the built-in 5-layer conversion framework, or name the one the client already works to.',
        placeholder: "e.g. USE DEFAULT",
      }),
      txt("primary_conversion_goal", "Primary Conversion Goal"),
      txt("secondary_conversion_goal", "Secondary Conversion Goal", { required: false }),
      txt("proof_assets_available", "Proof Assets Available", {
        required: false,
        helpText:
          "Only what actually exists — reviews, case studies, stats, accreditations, client logos. Nothing listed here is nothing that can be claimed.",
        placeholder: "e.g. 312 Google reviews at 4.9, two written case studies, ADA member, 18 years trading",
      }),
      txt("tone_of_voice", "Tone of Voice", {
        required: false,
        helpText: "Defaults to matching the existing page's tone if left blank.",
        placeholder: "e.g. Calm and plain-spoken. Explains rather than hypes. No exclamation marks.",
      }),
      notesField("additional_notes_constraints", "Additional Notes / Constraints"),
    ],
  },
  {
    // One stage, not two. The old "SEO Pillar Page (Variant)" asset re-ran this same prompt file
    // with a "now do the SEO version" note appended — so the SEO pass never saw the design system
    // this pass extracts, and the competitor pillar-page benchmark never reached the page that
    // actually got built. `Master_Prompt_Universal_Page_Design_v1.md` is now v2.0 and does both
    // jobs in one run (Step 2 benchmarks the competitor pillar pages, Rules 8-9 make the page
    // out-structure them, PART 4 is the SEO pack), so the variant asset is gone and its competitor
    // prepass belongs to this stage.
    asset_id: "pillar_page",
    label: "Pillar Page Design",
    category: "Pages & Conversion",
    description: "Full page design brief/build replicating a reference visual style around the CRO-approved copy — benchmarked against competitors' pillar pages so its architecture beats theirs, and delivered with an SEO implementation pack and a reusable design-token set.",
    live: false,
    // `seo_pillar_page_copy` is kept in the write list on purpose: the merged output *is* what
    // the retired SEO variant stage used to file under that key, so sessions and any future
    // reader of it still resolve instead of dead-ending.
    writesContextKeys: ["pillar_page_html", "design_tokens", "seo_pillar_page_copy"],
    pairedCompetitorAssetId: "competitor_analysis_seo_pillar_page",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      file("reference_design_source", "Reference Design Source", {
        helpText: "URL / description of the page whose visual design to replicate.",
        placeholder: "e.g. https://stripe.com/pricing — take the spacing and card treatment, not the palette",
      }),
      choice(
        "reference_design_scope",
        "Reference Design Scope",
        ["FULL SITE STYLE", "THIS ONE PAGE ONLY", "A DIFFERENT PAGE ON THE SAME SITE"],
        {
          helpText:
            "How much of the reference to take: its whole design system, only that one page's layout, or a different page on the same site.",
        },
      ),
      txt("services_covered", "Services / Sub-Services to Cover", {
        required: false,
        helpText:
          "Picked from the services the reference page links to, plus any you type. One combined pillar page covers them all; each one's existing page is read for its section's content.",
        placeholder: "e.g. Commercial Loans, Asset Finance, Property Development Finance",
      }),
      ctx("improved_page_content", "Improved Page Content", "cro_rewritten_copy"),
      ctx("cro_locked_sections", "Locked Sections (from CRO)", "cro_locked_sections"),
      txt("page_architecture_section_order", "Page Architecture / Section Order", { default: "USE DEFAULT" }),
      txt("new_sections_to_add_optional", "New Sections to Add (optional)", { required: false }),
      topic("primary_keyword_head_term", "Primary Keyword / Head Term", "pillar_head_term"),
      txt("secondary_cluster_terms_optional", "Secondary / Cluster Terms (optional)", { required: false }),
      txt("internal_cluster_pages_to_link_optional", "Internal Cluster Pages to Link (optional)", { required: false }),
      ctx("competitor_analysis_pillar_page", "Competitor Analysis — Pillar Pages", "competitor_analysis_seo_pillar_page", {
        required: false,
        helpText:
          "Offered as a search first — I look for competitors with a genuine pillar page on your topic and show you what I find. Paste your own list instead if you already have one: domain, pillar page URL, and what each covers.",
      }),
      ctx("word_for_the_reader", "Word for the reader", "cro_terminology_map", { sub_key: "word_for_the_reader" }),
      ctx("word_for_the_thing_being_chosen_between", "Word for the thing being chosen between", "cro_terminology_map", { sub_key: "word_for_the_thing_being_chosen_between" }),
      ctx("word_for_the_first_commitment_step", "Word for the first commitment step", "cro_terminology_map", { sub_key: "word_for_the_first_commitment_step" }),
      txt("primary_cta_label_and_action", "Primary CTA Label + Action"),
      txt("secondary_cta_label_and_action_optional", "Secondary CTA Label + Action (optional)", { required: false }),
      choice(
        "image_placement_instructions_optional",
        "Image Placement Instructions (optional)",
        [
          "Let agent handle image generation and placeholders (gpt-image-2 model)",
          "Follow reference design logic",
          "Specify custom images and placeholders: specify",
        ],
        {
          required: false,
          default: "Let agent handle image generation and placeholders (gpt-image-2 model)",
          helpText:
            "Choose whether to let the agent create gpt-image-2 generation prompts and placeholders, replicate the reference page's image positions, or specify custom image requirements.",
        },
      ),
      choice("component_fallback_preference", "Component Fallback Preference", ["STRICT", "ADAPTIVE"], { default: "ADAPTIVE" }),
      choice("accessibility_requirement", "Accessibility Requirement", ["STANDARD WCAG AA", "CLIENT-SPECIFIED", "NOT REQUIRED"], { default: "STANDARD WCAG AA" }),
      choice("output_format", "Output Format", ["Full HTML + CSS", "HTML sections only", "React component", "Figma-ready component brief", "WordPress block structure"]),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
    ],
  },

  // ---------------------------------------------------------------- Funnels & Offers
  {
    asset_id: "funnel",
    label: "Funnel",
    category: "Funnels & Offers",
    description: "End-to-end funnel structure built on the established ICP, pillar page copy, and design system.",
    live: false,
    writesContextKeys: ["funnel_stages"],
    fields: [
      txt("funnel_type", "Funnel Type", {
        helpText: "The funnel's shape — what actually happens between the click and the conversion.",
        placeholder: "e.g. Lead magnet → 5-email nurture sequence → consultation booking",
      }),
      txt("lead_magnet_mechanic", "Lead Magnet Mechanic", {
        helpText: "Describe what the visitor receives, and how fast they receive it.",
        placeholder: "e.g. A 12-page social media pricing guide with a per-channel cost breakdown, emailed instantly",
      }),
      txt("target_service_if_different_from_pillar_page", "Target Service (if different from pillar page)", {
        required: false,
        helpText: "Leave blank if this funnel sells the same thing the pillar page sells.",
        placeholder: "e.g. Google Ads management",
      }),
      txt("funnel_entry_points_optional", "Funnel Entry Points (optional)", {
        required: false,
        helpText: "Where the traffic arrives from. Each one named here gets its own entry copy.",
        placeholder: "e.g. Google Ads, the pillar page hero, Instagram bio link",
      }),
      txt("primary_conversion_goal_optional", "Primary Conversion Goal (optional)", {
        required: false,
        helpText: "Defaults to the pillar page's existing CTA if left blank.",
        placeholder: "e.g. A confirmed, attended consultation",
      }),
      notesField("additional_notes_optional", "Additional Notes (optional)"),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_rewritten_copy", "Pillar Page Content (from CRO)", "cro_rewritten_copy"),
      ctx("cro_locked_sections", "Locked Sections (from CRO)", "cro_locked_sections"),
      ctx("design_tokens", "Design Tokens", "design_tokens"),
      // Step 1E of `Funnel_Prompt.md` browses the client's site to fill gaps in the context it was
      // given, so the prompt renders this input in both phases. Without it here the operator was
      // never asked and the line arrived as "(not specified)" every run — while the URL itself was
      // sitting in the run profile from ICP. Auto-filled from there rather than asked
      // (`CLIENT_PROFILE_SOURCES`), and optional, so a run that never captured a URL still proceeds.
      txt("client_website_url_live_reference", "Client Website URL (live reference)", {
        required: false,
        helpText: "The live site, browsed only to fill gaps in the context above — never to invent facts.",
        placeholder: "e.g. https://northpathdigital.com.au",
      }),
      // No competitor field here on purpose. Funnel has no paired Competitor Analysis stage, so this
      // one resolved from `unresolved_context_key` — it could never auto-fill, and all it did was ask
      // the operator to paste a document nothing in the run produces. `Funnel_Prompt.md`'s own
      // competitor steps now state their absence rather than being handed a blank.
    ],
  },
  {
    asset_id: "funnel_hub_media",
    label: "Funnel Hub Media",
    category: "Funnels & Offers",
    description: "Media/hub architecture mapped from the client's existing strategy documents and knowledge base — distinct from the general Funnel asset.",
    live: false,
    writesContextKeys: ["funnel_hub_media"],
    fields: [
      txt("client_brand", "Client / Brand"),
      txt("industry_vertical", "Industry / Vertical"),
      txt("service_or_product_line_being_funnel_mapped", "Service or Product Line Being Funnel-Mapped", {
        helpText: "One line per run. Mapping several at once produces a hub that serves none of them properly.",
        placeholder: "e.g. Commercial solar installation, 10-100kW systems",
      }),
      ctx("reference_folder_knowledge_base", "Reference Folder / Knowledge Base", "unresolved_context_key", {
        helpText: "The client's existing strategy docs, personas, topic clusters, and keyword research.",
        placeholder: "e.g. paste the persona doc and topic-cluster sheet, or summarise what each one says",
      }),
      txt("established_buyer_personas_if_any", "Established Buyer Personas (if any)", {
        required: false,
        helpText: "Personas the client already uses. Named here, their names get reused instead of new ones being invented.",
        placeholder: 'e.g. "Facilities Frank" — operations manager, 3-site portfolio, judged on payback period',
      }),
      ctx("icp_document", "ICP Document", "icp_*", { required: false }),
      ctx("design_tokens", "Design Tokens", "design_tokens"),
      ctx("existing_funnel_document", "Existing Funnel Document", "funnel_stages", { required: false }),
      txt("primary_conversion_action_if_one_already_exists", "Primary Conversion Action (if one already exists)", {
        helpText: "What the client already converts on. If there isn't one yet, say so and one gets proposed.",
        placeholder: 'e.g. Free site assessment booking — or "none yet"',
      }),
    ],
  },
  {
    asset_id: "offers",
    label: "Offers / Value Ladder",
    category: "Funnels & Offers",
    description: "Full value-ladder / offer suite generated by the Value Ladder Genie (v2) — from free lead-gen through premium.",
    live: false,
    writesContextKeys: ["offer_ladder"],
    pairedCompetitorAssetId: "competitor_analysis_offers",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      txt("industry", "Industry"),
      txt("region_location", "Region / Location", { helpText: "City/state/country — note if local-only, national, or global." }),
      choice(
        "business_model_type",
        "Business Model Type",
        [
          "PRACTITIONER / PERSONAL BRAND",
          "AGENCY / B2B SERVICES",
          "LOCAL TRADE / FIELD SERVICE",
          "PRODUCT / RETAIL / MANUFACTURING",
          "SAAS / SOFTWARE PLATFORM",
          "PROFESSIONAL REGULATED FIRM",
          "FRANCHISE / MULTI-LOCATION NETWORK",
        ],
        {
          helpText:
            "Sets which rungs are even possible. A local trade can't sell a $50 digital product the way a personal brand can, and a franchise can't price outside the network.",
        },
      ),
      txt("delivery_capacity", "Delivery Capacity", { helpText: "Solo operator, small team, agency, product line, platform, or franchise network — and rough capacity." }),
      txt("team_credentials_licences_certifications", "Team Credentials / Licences / Certifications", {
        required: false,
        helpText: "Leave blank if none — nothing will be assumed.",
        placeholder: "e.g. Google Premier Partner, Meta Business Partner, 6 certified specialists",
      }),
      choice(
        "claim_substantiation_tier",
        "Claim Substantiation Tier",
        [
          "0 GENERAL COMMERCIAL",
          "1 CONSUMER-LAW SENSITIVE",
          "2 PROFESSIONALLY REGULATED",
          "3 HEALTH/THERAPEUTIC",
          "UNSURE",
        ],
        {
          helpText:
            "Sets how far offer claims and guarantees can be pushed. Pick by industry:\n" +
            "0 — retail, hospitality, trades, most B2B services\n" +
            "1 — anything advertised to consumers, or any comparative claim\n" +
            "2 — legal, financial, accounting, migration, insurance, real estate, education\n" +
            "3 — medical, dental, allied health, cosmetic, veterinary, supplements\n" +
            "Each tier inherits the constraints below it. Pick UNSURE only if the business genuinely doesn't map — it will be treated conservatively.",
        },
      ),
      txt("target_service_offer_to_ladder", "Target Service / Offer to Ladder", {
        required: false,
        helpText: "Required only for Wish 6 (Single Offer Type Laddered); optional otherwise.",
        placeholder: "e.g. Social media marketing",
      }),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_messaging_framework", "CRO / Messaging Framework", "cro_rewritten_copy", { required: false }),
      ctx("competitor_analysis", "Competitor Analysis", "competitor_analysis_offers", { required: false }),
      txt("existing_offers_already_in_market", "Existing Offers Already in Market", {
        required: false,
        helpText: "What's already being sold, and at what price. The ladder gets built around these rather than on top of them.",
        placeholder: "e.g. Free strategy call; $3,500 strategy sprint; social retainers from $2,500/mo; full-funnel from $12,000/mo",
      }),
      ctx("word_for_the_buyer", "Word for the buyer", "cro_terminology_map", { sub_key: "word_for_the_reader", required: false }),
      ctx("word_for_the_offer_unit", "Word for the offer unit", "cro_terminology_map", { sub_key: "word_for_the_thing_being_chosen_between", required: false }),
      ctx("word_for_the_first_commitment_step", "Word for the first commitment step", "cro_terminology_map", { sub_key: "word_for_the_first_commitment_step", required: false }),
      txt("annual_value_of_the_outcome_to_the_customer", "Annual Value of the Outcome to the Customer", {
        required: false,
        helpText: "What solving this is worth to them per year — it's what justifies the top of the ladder. Leave blank and an assumption gets stated.",
        placeholder: "e.g. ~$8,000/yr in avoided repairs and lost work time",
      }),
      txt("customer_s_typical_budget_or_spend_norms_for_this_category", "Customer's Typical Budget / Spend Norms", {
        required: false,
        helpText: "What buyers in this market are used to paying. It's what keeps the ladder's pricing credible.",
        placeholder: "e.g. Most budget $3-6k and expect a payment plan",
      }),
      txt("currency", "Currency", {
        required: false,
        default: "USD",
        helpText: "Every price in the ladder is written in this. Defaults to USD.",
        placeholder: "e.g. AUD",
      }),
      txt("local_market_price_norms_if_known", "Local Market Price Norms (if known)", {
        required: false,
        helpText: "The going local rate. Competitors' published prices count, and beat guesswork.",
        placeholder: "e.g. Melbourne social retainers advertised $1,800-$5,000/mo",
      }),
      choice(
        "wish_mode",
        "Wish / Mode",
        [
          "Wish 1 (Free/Lead Gen)",
          "Wish 2 (Low-Ticket)",
          "Wish 3 (Mid-Range)",
          "Wish 4 (High-Ticket)",
          "Wish 5 (Premium)",
          "Wish 6 (Single Offer Type Laddered)",
          "Wish 7 (Full Ladder Blueprint)",
        ],
        {
          helpText:
            "Which part of the ladder to build. Wishes 1-5 build one rung at a time, Wish 6 ladders a single offer type end to end, and Wish 7 builds the whole ladder in one pass.",
        },
      ),
      num("number_of_items_optional_override", "Number of Items (optional override)", {
        required: false,
        helpText: "How many offers to produce. Left blank, the count is chosen to fit the wish above.",
        placeholder: "e.g. 5",
      }),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
    ],
  },

  // ---------------------------------------------------------------- Content
  {
    asset_id: "lead_magnet",
    label: "Lead Magnet",
    category: "Content",
    description: "A gated lead magnet designed to feed a specific downstream service, grounded in the ICP and existing brand/funnel assets.",
    live: false,
    writesContextKeys: ["lead_magnet"],
    pairedCompetitorAssetId: "competitor_analysis_lead_magnet",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      txt("industry", "Industry"),
      txt("region_location", "Region / Location"),
      txt("delivery_capacity", "Delivery Capacity", { helpText: "Solo, small team, agency, or platform/software — what can realistically be built and fulfilled." }),
      txt("target_service_offer", "Target Service / Offer", {
        helpText:
          "The specific service or product this lead magnet must feed into. A magnet that doesn't lead anywhere collects the wrong people.",
        placeholder: "e.g. Social media strategy calls",
      }),
      topic("selected_lead_magnet_concepts", "Lead Magnet Concepts", "lead_magnet_concept", {
        helpText:
          "Pick every concept you want built — each one becomes its own finished lead magnet with a full brief and a working HTML file. Around ten gives you a real spread of formats and funnel stages.",
        placeholder: "e.g. The 12-minute social audit that shows where your reach died",
      }),
      txt("funnel_entry_point_optional", "Funnel Entry Point (optional)", {
        required: false,
        helpText: "Where people meet this magnet — pillar page, ad, organic search, referral. It sets how much context the copy has to establish.",
        placeholder: "e.g. Google Ads landing page",
      }),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_messaging_framework", "CRO / Messaging Framework", "cro_rewritten_copy", { required: false }),
      ctx("pillar_page", "Pillar Page", "pillar_page_html", { required: false }),
      ctx("funnel_document", "Funnel Document", "funnel_stages", { required: false }),
      ctx("offers_value_ladder_document", "Offers / Value Ladder Document", "offer_ladder", { required: false }),
      ctx("competitor_lead_magnet_list", "Competitor Lead Magnet List", "competitor_analysis_lead_magnet"),
      ctx("brand_design_reference", "Brand Design Reference", "design_tokens"),
      bool("regulated_field_flag", "Is this a regulated field?", { conditional_children: ["regulation_name"] }),
      txt("regulation_name", "Regulation Name", {
        required: false,
        conditional_on: { field: "regulated_field_flag", equals: true },
        helpText: "Medical, legal, financial, insurance, immigration, childcare, real estate, etc.",
      }),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
    ],
  },
  {
    asset_id: "blog",
    label: "Blog Post",
    category: "Content",
    description: "SEO-aware blog posts — one per topic chosen — that link back to the pillar page, to each other, and match established brand voice.",
    live: false,
    writesContextKeys: ["blog"],
    pairedCompetitorAssetId: "competitor_analysis_blog",
    fields: [
      topic("blog_topic_working_title", "Blog Topics / Working Titles", "blog_topic", {
        helpText:
          "Pick every topic you want written — each one becomes its own full post with its own keyword plan, outline and content brief. Around five is a month of publishing.",
        placeholder: "e.g. What social media marketing actually costs in Melbourne in 2026",
      }),
      txt("primary_keyword", "Primary Keyword", {
        helpText:
          "The head term the whole set supports — usually the pillar page's. Each topic you picked already carries its own keyword; this one anchors them together.",
        placeholder: "e.g. social media marketing cost melbourne",
      }),
      txt("secondary_supporting_keywords_optional", "Secondary / Supporting Keywords (optional)", {
        required: false,
        helpText: "Related phrases to work in naturally. Left blank, 3-5 get derived from the primary keyword.",
        placeholder: "e.g. agency vs in-house cost, is TikTok worth it for B2B",
      }),
      txt("blog_type", "Blog Type", {
        helpText: "The post's format. It fixes the structure before a word is written.",
        placeholder: "e.g. Cost breakdown — or how-to, listicle, comparison, case study, FAQ, ultimate guide",
      }),
      txt("target_awareness_level", "Target Awareness Level", {
        helpText: "How much the reader already knows. Pitch too late and a cold reader bounces; too early and a ready buyer feels patronised.",
        placeholder: "e.g. Solution-aware — knows they need social, comparing agencies against hiring in-house",
      }),
      txt("target_word_count", "Target Word Count", {
        helpText: "A number or a range. Long enough to beat what already ranks, not padded to hit a figure.",
        placeholder: "e.g. 1,800-2,200",
      }),
      txt("internal_links_to_include_optional", "Internal Links to Include (optional)", {
        required: false,
        helpText: "Pages this post must link to. The pillar page is linked whether or not you list it.",
        placeholder: "e.g. /social-media-marketing, /pricing",
      }),
      txt("cta_goal_for_this_blog_optional", "CTA Goal for This Blog (optional)", {
        required: false,
        helpText: "What the post asks for at the end. Match it to the awareness level above — a cold reader won't book.",
        placeholder: "e.g. Download the social media pricing guide",
      }),
      complianceField(),
      notesField(),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_rewritten_copy", "CRO / Brand Voice Framework", "cro_rewritten_copy"),
      ctx("pillar_page_html", "Pillar Page", "pillar_page_html"),
      ctx("competitor_analysis_blog", "Competitor Analysis — Blog", "competitor_analysis_blog", { required: false }),
    ],
  },
  {
    asset_id: "content_marketing_strategy",
    label: "Content Marketing Strategy",
    category: "Content",
    description: "Full content programme built around one hub page: topic clusters, editorial calendar, distribution, and KPIs.",
    live: false,
    writesContextKeys: ["content_marketing_strategy"],
    pairedCompetitorAssetId: "competitor_analysis_content_marketing",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      txt("industry_niche", "Industry / Niche"),
      txt("region_country", "Region / Country", { helpText: "Determines currency, compliance body, and localisation." }),
      txt("prepared_by_agency_name_optional", "Prepared By / Agency Name (optional)", { required: false }),
      txt("plan_date_version_optional", "Plan Date / Version (optional)", {
        required: false,
        helpText: "Appears on the cover. Defaults to today, v1.",
        placeholder: "e.g. March 2026 — v1",
      }),
      txt("primary_service_pillar_page_being_supported", "Primary Service / Pillar Page Being Supported", {
        helpText: "The one hub page the whole programme feeds. Everything produced links back to it.",
        placeholder: "e.g. Social Media Marketing — /social-media-marketing",
      }),
      ctx("existing_content_assets_optional", "Existing Content Assets (optional)", "unresolved_context_key", {
        required: false,
        helpText: 'Any Plan of Action, funnel, lead magnet, ROI calculator, or ICP doc already built. If nothing exists, say "greenfield".',
        placeholder: "e.g. 14 blog posts, one lead magnet, no calculator — or just: greenfield",
      }),
      ctx("icp_buyer_personas", "ICP / Buyer Personas", "icp_*"),
      ctx("cro_brand_voice_framework_optional", "CRO / Brand Voice Framework (optional)", "cro_rewritten_copy", { required: false }),
      ctx("competitor_list", "Competitor List", "competitor_analysis_content_marketing"),
      file("keyword_search_volume_data_source_optional", "Keyword / Search Volume Data Source (optional)", { required: false }),
      txt("regulated_field_advertising_compliance_body", "Regulated Field / Advertising Compliance Body", {
        helpText: 'e.g. ACCC (Australia), FTC (US), ASA (UK), or "None".',
      }),
      txt("content_production_capacity", "Content Production Capacity", {
        required: false,
        helpText: "How much can genuinely be produced per week. The calendar is built to this number, so overstating it produces a plan nobody keeps up with.",
        placeholder: "e.g. 2 long-form + 2 mid-form + 1 video + 3 social posts per week",
      }),
      num("editorial_calendar_length", "Editorial Calendar Length (weeks)", {
        default: 12,
        helpText: "How many weeks of dated calendar to lay out. Past roughly 26 the later weeks stop being actionable.",
        placeholder: "e.g. 12",
      }),
      num("kpi_horizon", "KPI Horizon (months)", {
        default: 12,
        helpText: "How far out to project traffic, leads, and rankings.",
        placeholder: "e.g. 12",
      }),
      txt("team_roles_available", "Team Roles Available", {
        helpText: "Who actually exists to do the work. The calendar is sized to this, so an honest answer produces a plan that ships.",
        placeholder: "e.g. 1 writer (2 days/wk), designer ad hoc, practice manager approves everything",
      }),
      txt("distribution_channels_available", "Distribution Channels Available", {
        helpText: "Where published work goes. Channels not listed here don't get planned for.",
        placeholder: "e.g. Blog, Google Business Profile, email list (4,200), Instagram, LinkedIn",
      }),
      txt("flagship_signature_asset_optional", "Flagship Signature Asset (optional)", {
        required: false,
        helpText: "The one big recurring asset the programme is built around, if there is one.",
        placeholder: "e.g. The Melbourne Social Media Pricing Report, refreshed each year",
      }),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
    ],
  },
  {
    asset_id: "social_content_strategy_audit",
    label: "Social Content Strategy Audit",
    category: "Content",
    description: "Audits the client's own social presence against competitors across platforms and produces sample on-brand posts.",
    live: false,
    writesContextKeys: ["social_content_strategy_audit"],
    pairedCompetitorAssetId: "competitor_analysis_social_content_strategy",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      txt("industry_niche", "Industry / Niche"),
      txt("region_country", "Region / Country"),
      txt("client_s_own_social_pages_handles", "Client's Own Social Pages/Handles", {
        helpText: "Every account, dormant ones included — an abandoned profile is itself a finding.",
        placeholder: "e.g. instagram.com/northpathdigital, linkedin.com/company/northpathdigital, @northpath (TikTok)",
      }),
      ctx("competitor_list", "Competitor List", "competitor_analysis_social_content_strategy"),
      num("number_of_competitors_to_audit", "Number of Competitors to Audit", {
        default: 5,
        helpText: "Each one adds depth and time. Five is usually enough for the pattern to show.",
        placeholder: "e.g. 5",
      }),
      txt("platforms_in_scope", "Platforms in Scope", {
        helpText: "Only platforms the client will actually post on — auditing one they'll never use burns a slot.",
        placeholder: "e.g. Instagram, Facebook, LinkedIn, YouTube, TikTok",
      }),
      file("raw_post_data_source", "Raw Post Data Source", {
        helpText: 'Attach an export, or type "research live" to sample recent posts per platform.',
        placeholder: 'e.g. research live — or paste an export with date, platform, caption, and engagement per row',
      }),
      txt("time_window", "Time Window", {
        helpText: "How far back to sample. Too short and seasonal patterns vanish; too long and a since-abandoned strategy pollutes the read.",
        placeholder: "e.g. last 6 months",
      }),
      ctx("client_s_own_service_offering_list", "Client's Own Service / Offering List", "unresolved_context_key", {
        required: false,
        helpText: "What the client sells, so posts can be coded against real offerings rather than invented themes.",
        placeholder: "e.g. SEO, Google Ads, paid social, content marketing, email, CRO",
      }),
      txt("content_format_taxonomy_optional", "Content Format Taxonomy (optional)", {
        required: false,
        helpText: "The buckets every post gets sorted into. Leave blank for a standard set.",
        placeholder: "e.g. Reel, carousel, static, story, long-form video, live",
      }),
      txt("content_purpose_taxonomy_optional", "Content Purpose Taxonomy (optional)", {
        required: false,
        helpText: "Why each post exists — this is what exposes a feed that only ever promotes. Leave blank for a standard set.",
        placeholder: "e.g. Educate, build trust, promote, entertain, recruit",
      }),
      topic("topic_theme_taxonomy_optional", "Topic/Theme Taxonomy (optional)", "social_theme_taxonomy", {
        required: false,
        helpText: "Subject areas to code posts against. Leave blank and they get derived from what's found.",
        placeholder: "e.g. SEO, paid social, client results, behind the scenes, team",
      }),
      ctx("icp_brand_voice_reference_optional", "ICP / Brand Voice Reference (optional)", "icp_*", { required: false }),
      txt("regulated_field_advertising_compliance_body_optional", "Regulated Field / Advertising Compliance Body (optional)", { required: false }),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
    ],
  },

  // ---------------------------------------------------------------- Long-form
  {
    asset_id: "webinar",
    label: "Webinar",
    category: "Long-form",
    description: "Full webinar packages — one per topic chosen — synthesised from competitor webinars plus established brand voice, each with slides, script, and follow-up emails.",
    live: false,
    writesContextKeys: ["webinar_script"],
    pairedCompetitorAssetId: "competitor_analysis_webinars",
    fields: [
      topic("webinar_topic_working_title", "Webinar Topics / Working Titles", "webinar_topic", {
        helpText:
          "The promise, not the final title — what makes someone give up an hour. Pick every webinar you want built; each one becomes its own package with a script, slide brief, registration page and email sequence. Around three is a quarter's programme.",
        placeholder: "e.g. What agencies don't tell you about pricing, lock-in contracts, and reporting",
      }),
      file("competitor_webinar_sources", "Competitor Webinar Sources", {
        helpText: "Transcripts, slide content, or URLs of 2-5 competitor webinars.",
        placeholder: "e.g. paste 2-5 transcripts or slide outlines, or their registration-page URLs",
      }),
      txt("webinar_format", "Webinar Format", {
        helpText:
          "Live, evergreen, or hybrid — and whether it's a presentation, a workshop, or a panel. Live earns real Q&A; evergreen has to work without it.",
        placeholder: "e.g. Live masterclass with Q&A, replay available 7 days",
      }),
      txt("target_duration", "Target Duration", {
        helpText: "Total runtime including Q&A. It decides how many modules fit.",
        placeholder: "e.g. 45 minutes plus 15 minutes Q&A",
      }),
      txt("primary_conversion_goal", "Primary Conversion Goal"),
      txt("presenters_optional", "Presenter(s) (optional)", {
        required: false,
        helpText: "Name, role, and the credibility that matters to this audience — not a full bio.",
        placeholder: "e.g. Dana Whitfield, paid social lead, $14M in ad spend managed over 9 years",
      }),
      bool("registration_page_needed", "Registration Page Needed?", {
        helpText: "Yes adds a full registration page — headline, bullets, and a reason to attend live rather than catch the replay.",
      }),
      bool("follow_up_emails_needed", "Follow-Up Emails Needed?", {
        helpText: "Yes adds the reminder sequence before, and the follow-ups after — split by attended versus no-show.",
      }),
      complianceField(),
      notesField(),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_rewritten_copy", "Brand Voice / Proof Points (from CRO)", "cro_rewritten_copy"),
      ctx("design_tokens", "Design Tokens", "design_tokens", { required: false }),
      ctx("competitor_analysis_webinars", "Competitor Analysis — Webinars", "competitor_analysis_webinars", { required: false }),
    ],
  },
  {
    asset_id: "book",
    label: "Book (from Webinar)",
    category: "Long-form",
    description: "Converts delivered webinar transcripts into lead-magnet ebooks, full-length books, or workbooks — one manuscript per topic chosen.",
    live: false,
    writesContextKeys: ["book"],
    pairedCompetitorAssetId: "competitor_analysis_book",
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      txt("industry_niche", "Industry / Niche"),
      txt("region_country", "Region / Country"),
      ctx("source_transcript_recording", "Source Transcript / Recording", "webinar_script", {
        helpText:
          'Mandatory — or say "no transcript — build from outline/bullet points" and supply those in Additional Notes. Where the webinar stage built a programme, each book draws on one webinar from it.',
      }),
      topic("book_topic_working_title", "Book Topics / Working Titles", "book_topic", {
        required: false,
        helpText:
          "Pick every book you want written — each becomes its own manuscript, paired with one webinar from the transcript. Two is realistic; a full-length format is often one. Leave blank to derive a single title from the transcript.",
        placeholder: "e.g. The Honest Guide to Social Media Marketing",
      }),
      txt("book_format", "Book Format", {
        helpText: "Lead-magnet ebook (3-6k words), full-length book (25-50k+), or workbook/guide.",
        placeholder: "e.g. Lead-magnet ebook, around 5,000 words",
      }),
      num("target_length", "Target Length (words, optional)", {
        required: false,
        helpText: "Leave blank and the chosen format's usual range is used.",
        placeholder: "e.g. 5000",
      }),
      ctx("icp_reader_profile", "ICP / Reader Profile", "icp_*"),
      ctx("brand_voice_tone_reference_optional", "Brand Voice / Tone Reference (optional)", "cro_rewritten_copy", { required: false }),
      file("approved_proof_points_optional", "Approved Proof Points (optional)", {
        required: false,
        placeholder: "e.g. 312 Google reviews at 4.9, $14M ad spend managed, two case studies with written consent",
      }),
      txt("primary_conversion_goal", "Primary Conversion Goal", { helpText: "What the reader should do after finishing the book." }),
      txt("author_presenter_attribution", "Author / Presenter Attribution", {
        helpText: "Whose voice and name the book is written in — it sets the whole first-person register.",
        placeholder: "e.g. Dana Whitfield, Northpath Digital",
      }),
      txt("regulated_field_advertising_compliance_body_optional", "Regulated Field / Advertising Compliance Body (optional)", { required: false }),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
      ctx("content_marketing_strategy_optional", "Content Marketing Strategy (optional)", "content_marketing_strategy", { required: false }),
      ctx("plan_of_action_summary_optional", "Plan of Action Summary (optional)", "plan_of_action_summary", { required: false }),
      ctx("competitor_analysis_book", "Competitor Analysis — Book", "competitor_analysis_book", { required: false }),
    ],
  },
  {
    asset_id: "podcast",
    label: "Podcast Episode",
    category: "Long-form",
    description: "A full episode package synthesised from competitor podcasts plus established brand voice — script, show notes, and repurposing plan.",
    live: false,
    writesContextKeys: ["podcast"],
    pairedCompetitorAssetId: "competitor_analysis_podcast",
    fields: [
      topic("episode_topic_working_title", "Episode Topic / Working Title", "podcast_episode_topic", {
        helpText: "The angle for this one episode, not the show's overall theme.",
        placeholder: "e.g. Why two agency quotes for the same brief can differ by $40,000",
      }),
      file("competitor_podcast_sources", "Competitor Podcast Sources", {
        helpText: "Transcripts, show notes, or episode descriptions from 2-5 competitor podcasts.",
        placeholder: "e.g. paste 2-5 transcripts or sets of show notes, or their episode URLs",
      }),
      txt("podcast_format", "Podcast Format", {
        helpText: "Solo, guest interview, co-hosted, or panel. It decides whether you get a word-for-word script or an interview guide.",
        placeholder: "e.g. Guest interview, one guest",
      }),
      txt("target_duration", "Target Duration", {
        helpText: "Total runtime. Match it to when this audience listens — a commute is 25 minutes, not 60.",
        placeholder: "e.g. 30-35 minutes",
      }),
      txt("guest_details_optional", "Guest Details (optional)", {
        required: false,
        helpText: "Name, what they're known for, and why this audience should care.",
        placeholder: "e.g. Dana Whitfield — paid social lead, $14M managed, guest lecturer at RMIT",
      }),
      choice("is_this_new_or_existing_podcast", "Is This New or Existing Podcast", ["New", "Existing"], {
        helpText: "New also gets show-level scaffolding — name, format, intro and outro. Existing slots into whatever is already running.",
      }),
      txt("primary_cta_for_this_episode_optional", "Primary CTA for This Episode (optional)", {
        required: false,
        helpText: "One ask, spoken aloud — it has to be memorable without a clickable link.",
        placeholder: 'e.g. Search "Northpath social pricing guide" to get the PDF',
      }),
      txt("repurposing_assets_needed_optional", "Repurposing Assets Needed (optional)", {
        required: false,
        helpText: "What gets cut out of the episode afterwards.",
        placeholder: "e.g. 3 short clips, quote graphics, show notes, one LinkedIn post",
      }),
      complianceField(),
      notesField(),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_rewritten_copy", "Brand Voice / Proof Points (from CRO)", "cro_rewritten_copy"),
      ctx("pillar_page_html", "Pillar Page", "pillar_page_html", { required: false }),
      ctx("webinar_script_optional", "Webinar Script (optional)", "webinar_script", { required: false }),
      ctx("competitor_analysis_podcast", "Competitor Analysis — Podcast", "competitor_analysis_podcast", { required: false }),
    ],
  },

  // ---------------------------------------------------------------- Outreach
  {
    asset_id: "sms_sequence",
    label: "SMS Sequence",
    category: "Outreach",
    description: "A triggered SMS sequence that complements, rather than duplicates, the existing funnel and email flow.",
    live: false,
    writesContextKeys: ["sms_sequence"],
    fields: [
      txt("sms_purpose", "SMS Purpose", {
        helpText: "One job for the sequence. A sequence that nurtures and reactivates at the same time does neither.",
        placeholder: "e.g. Recover consultation enquiries that never booked",
      }),
      num("number_of_sms_messages", "Number of SMS Messages", {
        helpText: "Kept short deliberately — tolerance for SMS is far lower than for email. Three to five is typical.",
        placeholder: "e.g. 4",
      }),
      txt("trigger_point", "Trigger Point", {
        helpText: "The event that starts the sequence, and how long after it the first message goes out.",
        placeholder: "e.g. Enquiry form submitted, no booking within 48 hours",
      }),
      complianceField(),
      notesField("additional_notes_optional", "Additional Notes (optional)"),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("funnel_stages", "Funnel Structure", "funnel_stages"),
      ctx("email_sequence_copy", "Email Sequence Copy", "email_sequence_copy", { required: false, fallback: "skip_silently_if_missing" }),
      ctx("cro_rewritten_copy", "Brand Voice (from CRO)", "cro_rewritten_copy"),
      ctx("offer_ladder_optional", "Offer Ladder (optional)", "offer_ladder", { required: false }),
    ],
  },

  // ---------------------------------------------------------------- Planning
  {
    asset_id: "plan_of_action",
    label: "Plan of Action",
    category: "Planning",
    description: "A phased, multi-month roadmap expanding one primary service into sub-services, verticals, and locations.",
    live: false,
    writesContextKeys: ["plan_of_action_summary"],
    fields: [
      txt("client_name", "Client Name"),
      txt("client_website_url", "Client Website URL"),
      txt("industry_niche", "Industry / Niche"),
      txt("region_country", "Region / Country", { helpText: "For search-volume estimates, competitor selection, and compliance framing." }),
      txt("prepared_by_agency_name_optional", "Prepared By / Agency Name (optional)", { required: false }),
      txt("plan_date_optional", "Plan Date (optional)", {
        required: false,
        helpText: "Defaults to today if left blank.",
        placeholder: "e.g. March 2026",
      }),
      txt("primary_service_being_expanded", "Primary Service Being Expanded", {
        helpText: "The service the whole roadmap grows out of — Phase 2 splits it into sub-services, Phase 3 takes it into verticals and locations.",
        placeholder: "e.g. Social media marketing",
      }),
      txt("primary_pillar_page_existing_or_target_url", "Primary Pillar Page (existing or target URL)", {
        helpText: "The hub page the expansion hangs off — the live URL, or the one it will eventually live at.",
        placeholder: "e.g. https://northpathdigital.com.au/social-media-marketing",
      }),
      file("existing_phase_1_assets_optional", "Existing Phase 1 Assets (optional)", {
        required: false,
        placeholder: "e.g. ICP done, pillar page live, funnel built, no lead magnet yet",
      }),
      txt("sub_services_to_expand_into_phase_2", "Sub-Services to Expand Into (Phase 2)", {
        helpText: 'Say "infer 6-10 sub-services" to have them inferred instead.',
        placeholder: "e.g. Organic social, paid social, community management, influencer campaigns",
      }),
      txt("industry_verticals_to_target_phase_3_optional", "Industry Verticals to Target (Phase 3, optional)", {
        required: false,
        helpText: "Buyer segments worth a page of their own. Leave blank to have them inferred.",
        placeholder: "e.g. Retirees, FIFO workers, private health fund members",
      }),
      txt("locations_to_target_phase_3_optional", "Locations to Target (Phase 3, optional)", {
        required: false,
        helpText: "Suburbs or cities that each get their own location page.",
        placeholder: "e.g. Richmond, Hawthorn, Box Hill, Geelong",
      }),
      ctx("icp_document", "ICP Document", "icp_*"),
      ctx("cro_messaging_framework_optional", "CRO / Messaging Framework (optional)", "cro_rewritten_copy", { required: false }),
      ctx("existing_funnels_lead_magnets_optional", "Existing Funnels / Lead Magnets (optional)", "funnel_stages", { required: false }),
      ctx("existing_value_ladder_offers_optional", "Existing Value Ladder / Offers (optional)", "offer_ladder", { required: false }),
      ctx("brand_design_reference_optional", "Brand Design Reference (optional)", "design_tokens", { required: false }),
      file("competitor_list", "Competitor List", { required: false, helpText: "3-6 direct competitors. If none supplied, well-known ones will be named and flagged as an assumption." }),
      file("keyword_search_volume_data_source_optional", "Keyword / Search Volume Data Source (optional)", { required: false }),
      num("plan_length", "Plan Length (months)", {
        default: 12,
        helpText: "Months. Longer plans get vaguer at the tail — 12 is about the limit for something anyone actually follows.",
        placeholder: "e.g. 12",
      }),
      num("number_of_phases", "Number of Phases", {
        default: 3,
        helpText: "How many phases to split the plan into. Three is the standard shape: consolidate, expand, scale.",
        placeholder: "e.g. 3",
      }),
      txt("delivery_capacity", "Delivery Capacity", { helpText: "Solo operator, small team, or full agency." }),
      bool("regulated_field_flag", "Is this a regulated field?", { conditional_children: ["regulation_name"] }),
      txt("regulation_name", "Regulation Name", {
        required: false,
        conditional_on: { field: "regulated_field_flag", equals: true },
      }),
      notesField("additional_notes_constraints_optional", "Additional Notes / Constraints (optional)"),
    ],
  },

  // ---------------------------------------------------------------- Competitor Research
  competitorAsset("competitor_analysis_cro", "Competitor Analysis — CRO", "cro", "page structure and conversion patterns"),
  competitorAsset("competitor_analysis_offers", "Competitor Analysis — Offers", "offers", "pricing and offer-ladder structure"),
  competitorAsset("competitor_analysis_lead_magnet", "Competitor Analysis — Lead Magnet", "lead_magnet", "lead magnets"),
  competitorAsset("competitor_analysis_blog", "Competitor Analysis — Blog", "blog", "blog content"),
  // asset_id keeps its `_seo_` spelling: it is the file name of the prompt it runs
  // (Competitor Analysis/05_SEO_Pillar_Page.md) and the context_key already written by past runs.
  // Its paired main asset is `pillar_page` since the SEO variant stage was merged into it.
  competitorAsset("competitor_analysis_seo_pillar_page", "Competitor Analysis — Pillar Page", "pillar_page", "pillar-page structure"),
  competitorAsset("competitor_analysis_content_marketing", "Competitor Analysis — Content Marketing", "content_marketing_strategy", "content marketing programmes"),
  competitorAsset("competitor_analysis_social_content_strategy", "Competitor Analysis — Social Content", "social_content_strategy_audit", "social content strategy"),
  competitorAsset("competitor_analysis_webinars", "Competitor Analysis — Webinars", "webinar", "webinar programmes"),
  competitorAsset("competitor_analysis_book", "Competitor Analysis — Book", "book", "published books"),
  competitorAsset("competitor_analysis_podcast", "Competitor Analysis — Podcast", "podcast", "podcasts"),
];

export const ASSET_BY_ID: Record<string, AssetDefinition> = Object.fromEntries(
  ASSET_CATALOG.map((a) => [a.asset_id, a]),
);

/** context key -> the asset_id whose output is stored under it.
 *
 * Load-bearing for reading approved work back out of the database. One stage files its output under
 * several context keys — the CRO rewrite answers `cro`, `cro_rewritten_copy`, `cro_audit_findings`,
 * `cro_locked_sections` and `cro_terminology_map` — but `context_entries` holds one row per stage,
 * keyed by `asset_id` (see `save_stage` in `app/routers/pipeline.py`). So a field asking for
 * `cro_rewritten_copy` has to be turned into a request for `cro` before it can be fetched. In a live
 * session it never comes up, because the in-session store is keyed the field's way; it comes up the
 * moment context is read back — a resumed chat, or a Phase 2 run reading its parent Phase 1 run.
 *
 * Derived from the catalog's own `writesContextKeys`, so a new key on an asset is fetchable without
 * anything here being updated. */
export const PRODUCER_ASSET_ID_BY_CONTEXT_KEY: Record<string, string> = Object.fromEntries(
  ASSET_CATALOG.flatMap((asset) => asset.writesContextKeys.map((key) => [key, asset.asset_id])),
);

/** The asset_id to fetch in order to satisfy `contextKey`, or undefined when nothing writes it.
 * Handles `icp_*`-style wildcards by matching the family. */
export function producerAssetIdFor(contextKey: string): string | undefined {
  if (!contextKey || contextKey === "unresolved_context_key") return undefined;
  const exact = PRODUCER_ASSET_ID_BY_CONTEXT_KEY[contextKey];
  if (exact) return exact;
  if (contextKey.endsWith("_*")) {
    const prefix = contextKey.slice(0, -2);
    const match = Object.keys(PRODUCER_ASSET_ID_BY_CONTEXT_KEY).find(
      (k) => k === prefix || k.startsWith(prefix),
    );
    if (match) return PRODUCER_ASSET_ID_BY_CONTEXT_KEY[match];
  }
  return undefined;
}

/** Assets that declare a context_reference field pointing (directly, or via an "x_*"
 * wildcard) at something the given asset just produced — used to suggest sensible "what's
 * next" picks after a generation completes. */
export function getDownstreamAssets(producedAssetId: string): AssetDefinition[] {
  const producer = ASSET_BY_ID[producedAssetId];
  if (!producer) return [];
  const producedKeys = new Set([producer.asset_id, ...producer.writesContextKeys]);

  return ASSET_CATALOG.filter((candidate) => {
    if (candidate.asset_id === producedAssetId) return false;
    return candidate.fields.some((field) => {
      if (!field.context_key || field.context_key === "unresolved_context_key") return false;
      if (field.context_key.endsWith("_*")) {
        const prefix = field.context_key.slice(0, -2);
        return [...producedKeys].some((k) => k === prefix || k.startsWith(`${prefix}_`) || k.startsWith(prefix));
      }
      return producedKeys.has(field.context_key);
    });
  });
}

export const CATEGORY_ORDER: AssetDefinition["category"][] = [
  "Foundation",
  "Pages & Conversion",
  "Funnels & Offers",
  "Content",
  "Long-form",
  "Outreach",
  "Planning",
  "Competitor Research",
];
