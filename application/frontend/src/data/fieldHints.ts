import { ASSET_CATALOG } from "./assetCatalog";
import type { FieldDef } from "./types";

/** What the UI shows above/inside the answer box for the field it is currently asking.
 *
 * `helpText` says what the field means; `example` shows the shape of a good answer. Every field the
 * operator can type into should end up with at least one of the two — a bare label like "Delivery
 * Capacity" or "Wish / Mode" is guessable by whoever wrote the source prompt and nobody else. */
export interface ResolvedFieldHint {
  helpText?: string;
  example?: string;
}

/** Generic example for a competitor list, whether it arrives as an upstream analysis or is typed
 * in. All ten competitor-analysis keys produce the same shape, so they are matched by prefix rather
 * than listed one by one. */
const COMPETITOR_EXAMPLE =
  "List 3-6 competitors with what each does well and where the gaps are — URLs help.";

/** Hints for field_ids that carry *exactly* the same meaning on every asset that asks them.
 *
 * Six of the fifteen schemas ask for the client's name and URL, five ask for the region — each
 * transcribed from its own source prompt, so the labels differ slightly ("Client Name", "Client /
 * Brand") while the question is identical. Keeping the hint here instead of on each `txt()` call
 * means one edit rather than eight, and no chance of the eight drifting apart.
 *
 * The same caution as `CLIENT_PROFILE_SOURCES` in `pipeline/pipelineData.ts` applies: only put a
 * field_id here when every asset means the same thing by it. A field like
 * `target_service_or_sub_service` legitimately differs per asset and belongs inline in the
 * catalog, where its hint can be specific to the stage asking it. */
const SHARED_FIELD_HINTS: Record<string, ResolvedFieldHint> = {
  client_name: {
    helpText: "The trading name as it should appear in the copy — not the legal entity name.",
    example: "e.g. Northpath Digital",
  },
  company_name: {
    helpText: "The trading name as it should appear in the copy — not the legal entity name.",
    example: "e.g. Northpath Digital",
  },
  client_brand: {
    helpText: "The trading name as it should appear in the copy — not the legal entity name.",
    example: "e.g. Northpath Digital",
  },
  client_website_url: {
    helpText: "The live site. It gets read for proof points, service names, and existing tone.",
    example: "e.g. https://northpathdigital.com.au",
  },
  website_url: {
    helpText: "The live site. It gets read for proof points, service names, and existing tone.",
    example: "e.g. https://northpathdigital.com.au",
  },
  industry: {
    helpText: "Plain words, not a category code — specific beats broad.",
    example: "e.g. Digital marketing — SEO and paid social",
  },
  industry_niche: {
    helpText: "Plain words, not a category code — specific beats broad.",
    example: "e.g. Digital marketing — SEO and paid social",
  },
  client_industry: {
    helpText: "Plain words, not a category code — specific beats broad.",
    example: "e.g. Digital marketing — SEO and paid social",
  },
  industry_vertical: {
    helpText: "Plain words, not a category code — specific beats broad.",
    example: "e.g. Digital marketing — SEO and paid social",
  },
  region_location: {
    helpText:
      "Sets spelling, currency, and which compliance regime applies. Name the city or state, not just the country, if the business is local.",
    example: "e.g. Melbourne, VIC, Australia",
  },
  region_country: {
    helpText:
      "Sets spelling, currency, and which compliance regime applies. Name the city or state, not just the country, if the business is local.",
    example: "e.g. Melbourne, VIC, Australia",
  },
  market_region_country: {
    helpText:
      "Sets spelling, currency, and which compliance regime applies. Name the city or state, not just the country, if the business is local.",
    example: "e.g. Melbourne, VIC, Australia",
  },
  delivery_capacity: {
    example: "e.g. Small team — 6 specialists, ~12 retainer clients before capacity",
  },
  primary_conversion_goal: {
    helpText: "The one action the asset is built to produce. One goal, not a list.",
    example: "e.g. Book a free strategy call",
  },
  secondary_conversion_goal: {
    helpText: "The fallback for readers not ready for the primary action.",
    example: "e.g. Download the social media pricing guide",
  },
  prepared_by_agency_name_optional: {
    helpText: "Appears on the cover of the delivered document.",
    example: "e.g. Traffic Radius",
  },
  regulated_field_advertising_compliance_body: {
    helpText: "Names the rules the copy has to stay inside.",
    example: 'e.g. Australian Consumer Law / ACCC (Australia) — or "None"',
  },
  regulated_field_advertising_compliance_body_optional: {
    helpText: "Names the rules the copy has to stay inside. Leave blank if unregulated.",
    example: "e.g. Australian Consumer Law / ACCC (Australia)",
  },
  regulation_name: {
    helpText: "The specific regime, so the copy can be written to it rather than around it.",
    example: "e.g. Australian Consumer Law — misleading or deceptive conduct, plus Google Ads and Meta ad policies",
  },
  regulated_field_flag: {
    helpText:
      "Regulated fields cap what may be claimed or promised. Saying yes adds those constraints — it doesn't shrink the asset.",
  },
  competitor_analysis: { example: COMPETITOR_EXAMPLE },
  competitor_list: { example: COMPETITOR_EXAMPLE },
  competitor_analysis_pillar_page: {
    helpText:
      "Competitors' pillar pages on this topic. The stage reads their architecture, depth and coverage to decide what to adopt and what to beat — never their copy, claims or branding.",
    example: COMPETITOR_EXAMPLE,
  },
  competitor_lead_magnet_list: {
    example: "List what competitors are giving away to capture leads, and what nobody is offering.",
  },
  keyword_search_volume_data_source_optional: {
    helpText: "Real volumes if you have them; otherwise estimates get used and flagged as estimates.",
    example: "e.g. paste a Search Console or keyword tool export — one row per keyword, with volume",
  },

  // ---- Page design and the SEO/competitive pass, both asked by the merged Pillar Page stage.
  primary_keyword_head_term: {
    helpText:
      "The one term this pillar page has to own. It sets the H1's intent and the heading structure, and it's what the competitor search looks for pillar pages about.",
    example: "e.g. social media marketing Melbourne",
  },
  secondary_cluster_terms_optional: {
    helpText:
      "Supporting terms this page should cover in passing. Each one gets placed in the section that actually describes it, or left out and flagged — never stuffed in.",
    example: "e.g. social media management cost, Meta ads agency, LinkedIn ads, organic social strategy",
  },
  internal_cluster_pages_to_link_optional: {
    helpText:
      "The sub-topic pages this pillar should link down to, with what each covers. A pillar page that links to nothing is just a long page.",
    example: "e.g. /social-media-pricing — pricing guide; /meta-ads — paid social service page",
  },
  page_architecture_section_order: {
    helpText:
      'List the sections in the order they should appear, or "USE CONTENT ORDER" to follow the approved copy, or "USE DEFAULT" for the 13-section universal architecture.',
    example: "e.g. Hero → Trust bar → Problem → Options → Pricing → Process → Proof → FAQs → CTA",
  },
  new_sections_to_add_optional: {
    helpText: "Sections the page needs that the approved copy doesn't cover yet.",
    example: "e.g. ROI calculator, Meet the strategists, Client results gallery",
  },
  primary_cta_label_and_action: {
    helpText: "The button text and where it goes — both halves matter.",
    example: 'e.g. "Book a free consultation" → /book (enquiry form)',
  },
  secondary_cta_label_and_action_optional: {
    helpText: "The lower-commitment path for readers not ready for the primary action.",
    example: 'e.g. "Download the cost guide" → lead-magnet form',
  },
  image_placement_instructions_optional: {
    helpText:
      "Where images go and what they show. Anywhere you don't specify gets a marked placeholder rather than an invented asset.",
    example: "e.g. Hero: real team photo, not stock. One results screenshot per package card. No fake dashboards.",
  },
  component_fallback_preference: {
    helpText:
      "What happens when the reference design has no component for a section this page needs.\nSTRICT — reuse only components the reference actually has\nADAPTIVE — build a new one in the reference's style",
  },
  accessibility_requirement: {
    helpText:
      "WCAG AA is the safe default — it constrains contrast, focus states, and heading order. Pick CLIENT-SPECIFIED only if they've given you their own standard.",
  },
  output_format: {
    helpText: "Pick by what happens to this next: pasted into a builder, handed to a dev, or handed to a designer.",
  },

  // ---- The CRO terminology map. Example only, no helpText: the CRO stage that first asks these
  // explains what each one is for, and the downstream stages that inherit them are better served by
  // the "carried over from CRO" hint that `contextHint` derives.
  word_for_the_reader: { example: "e.g. client" },
  word_for_the_thing_being_chosen_between: { example: "e.g. package" },
  word_for_the_first_commitment_step: { example: "e.g. strategy call" },
  word_for_the_business: { example: "e.g. agency" },
  words_the_buyer_uses_for_the_outcome: { example: "e.g. more qualified leads, a pipeline that does not dry up" },
  word_for_the_buyer: { example: "e.g. client" },
  word_for_the_offer_unit: { example: "e.g. retainer package" },
};

/** context_key -> what to paste, for the case where the pipeline has nothing to auto-fill from and
 * falls back to asking. The "where it normally comes from" half of the hint is derived from
 * `writesContextKeys` (see `producerLabelFor`) rather than repeated here, so renaming an asset
 * cannot leave a stale stage name in a hint.
 *
 * Keys may be suffixed `:sub_key` to describe one entry of a map-shaped output (the CRO
 * terminology map), which is what the pillar-page and offers stages actually ask for. */
const CONTEXT_KEY_EXAMPLES: Record<string, string> = {
  "icp_*": "Paste the persona: who they are, their pains, goals, objections, and awareness level.",
  cro_rewritten_copy:
    "Paste the approved page copy in full — H1, every section heading and body, and the CTAs.",
  cro_locked_sections:
    "List the sections that must survive the rebuild word-for-word, one per line.",
  cro_audit_findings: "Paste the audit: what is losing conversions on the page today, and why.",
  "cro_terminology_map:word_for_the_reader": "e.g. client",
  "cro_terminology_map:word_for_the_thing_being_chosen_between": "e.g. package",
  "cro_terminology_map:word_for_the_first_commitment_step": "e.g. strategy call",
  design_tokens:
    "Paste the palette, type scale, spacing, and radii — or the URL of the page to pull them from.",
  pillar_page_html: "Paste the built page — markup, or the section-by-section content.",
  funnel_stages: "Paste the funnel: each stage, the page at it, and the email sequence.",
  funnel_hub_media: "Paste the hub/media architecture already mapped for this client.",
  offer_ladder: "List the offers rung by rung — name, price, and what each includes.",
  lead_magnet: "Paste the lead magnet: title, promise, format, and contents.",
  blog: "Paste the post.",
  content_marketing_strategy: "Paste the strategy: topic clusters, calendar, channels, KPIs.",
  social_content_strategy_audit: "Paste the social audit findings.",
  webinar_script: "Paste the transcript or the full script, start to finish.",
  book: "Paste the manuscript or its chapter outline.",
  podcast: "Paste the episode script or show notes.",
  sms_sequence: "Paste the message sequence.",
  plan_of_action_summary: "Paste the roadmap: phases, months, and deliverables per phase.",
  email_sequence_copy: "Paste the email sequence so the SMS complements it instead of repeating it.",
};

/** context_key -> the label of the asset that writes it, from the catalog's own
 * `writesContextKeys`. Derived rather than hand-listed so it cannot drift from `assetCatalog.ts`.
 * A wildcard field key like `icp_*` matches by prefix. */
const PRODUCER_LABEL_BY_CONTEXT_KEY: Record<string, string> = Object.fromEntries(
  ASSET_CATALOG.flatMap((asset) => asset.writesContextKeys.map((key) => [key, asset.label])),
);

function producerLabelFor(contextKey: string): string | undefined {
  if (contextKey === "unresolved_context_key") return undefined;
  const exact = PRODUCER_LABEL_BY_CONTEXT_KEY[contextKey];
  if (exact) return exact;
  if (contextKey.endsWith("_*")) {
    const prefix = contextKey.slice(0, -2);
    const match = Object.keys(PRODUCER_LABEL_BY_CONTEXT_KEY).find(
      (k) => k === prefix || k.startsWith(prefix),
    );
    if (match) return PRODUCER_LABEL_BY_CONTEXT_KEY[match];
  }
  return undefined;
}

function contextHint(field: FieldDef): ResolvedFieldHint {
  const contextKey = field.context_key;
  if (!contextKey) return {};

  const producer = producerLabelFor(contextKey);
  const helpText = producer
    ? `Normally carried over from “${producer}”. Paste your own to use that instead.`
    : "Not produced anywhere in this run — supply it here, or skip it.";

  const example =
    (field.sub_key && CONTEXT_KEY_EXAMPLES[`${contextKey}:${field.sub_key}`]) ||
    CONTEXT_KEY_EXAMPLES[contextKey] ||
    (contextKey.startsWith("competitor_analysis") ? COMPETITOR_EXAMPLE : undefined);

  return { helpText, example };
}

/** The hint to show for one field, most specific source first: whatever the catalog states on the
 * field itself, then the shared per-field_id hint, then — for `context_reference` fields — one
 * derived from the upstream key it reads.
 *
 * `helpText` and `example` resolve independently: a field that states its own `helpText` but no
 * `placeholder` still picks up the shared example, rather than losing it. */
export function resolveFieldHint(field: FieldDef): ResolvedFieldHint {
  const shared = SHARED_FIELD_HINTS[field.field_id] ?? {};
  const derived = field.kind === "context_reference" ? contextHint(field) : {};

  return {
    helpText: field.helpText ?? shared.helpText ?? derived.helpText,
    example: field.placeholder ?? shared.example ?? derived.example,
  };
}

/** The example shown inside the answer box. Enum and boolean fields are answered by pill button,
 * so there is no box to put one in. */
export function inputPlaceholderFor(field: FieldDef | undefined): string | undefined {
  if (!field || field.kind === "enum_choice" || field.kind === "boolean_flag") return undefined;
  return resolveFieldHint(field).example;
}
