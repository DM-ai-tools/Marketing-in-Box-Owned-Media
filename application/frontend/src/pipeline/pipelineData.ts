import { ASSET_CATALOG } from "../data/assetCatalog";
import {
  PHASE2_ASSETS,
  PHASE2_FIELD_TO_SUB_SERVICE,
  SUB_SERVICE_CHOICES,
  SUB_SERVICE_FACT,
} from "../data/phase2Catalog";
import type { AssetDefinition, FieldDef } from "../data/types";
import { INDUSTRY_BUCKETS, INDUSTRY_PROFILES } from "../data/industryProfiles";
import type { IndustryBucket } from "../data/industryProfiles";

/** The three foundation stages that are gated (human-in-the-loop) in the real orchestrator's
 * `asset_definitions.is_gated` column (see backend `scripts/seed_asset_definitions.py`). Every
 * stage in this UI still pauses for an explicit Save/Refine decision — this only drives the
 * "HITL" vs "Auto-Chain" label shown on each node. */
const HITL_ASSET_IDS = new Set(["icp", "cro", "pillar_page"]);

const EMOJI_BY_ASSET_ID: Record<string, string> = {
  icp: "🎯",
  cro: "🔍",
  pillar_page: "🏛️",
  funnel: "🔀",
  funnel_hub_media: "🎬",
  offers: "💰",
  lead_magnet: "🧲",
  blog: "📝",
  content_marketing_strategy: "🗺️",
  social_content_strategy_audit: "📊",
  webinar: "🎥",
  book: "📖",
  podcast: "🎙️",
  sms_sequence: "💬",
  plan_of_action: "✅",
};

export interface PipelineStageDef {
  stageNumber: number;
  asset: AssetDefinition;
  emoji: string;
  hitl: boolean;
}

/** Which pipeline a chat is building.
 *
 * `phase1` builds the full stack for a client's *headline* service — the one they lead with
 * ("Social Media Marketing"). It starts from ICP and CRO because at that point nothing about the
 * client is known yet, and everything downstream is built on those two.
 *
 * `phase2` builds the same kind of stack one level down, for a single *sub-service* (LinkedIn,
 * Meta Ads, Google Ads). It is shorter than Phase 1 and inherits the *client* from the parent run
 * (brand design, CRO client settings, industry), but not the *audience*: it opens with its own ICP,
 * because the buyer of one sub-service is often not the buyer of the headline service. Phase 1's ICP
 * and value ladder never cross over (`PHASE1_ONLY_CONTEXT_KEYS`). */
export type PipelinePhase = "phase1" | "phase2";

/** The Phase 2 sequence, listed explicitly rather than filtered out of the catalog.
 *
 * Phase 2 is not a subset of Phase 1 in catalog order — it is its own running order (Lead Magnet
 * before Blog, Funnel Hub last as the piece that assembles the designs), so the order here IS the
 * specification and must not be re-derived from `ASSET_CATALOG`. */
const PHASE2_ASSET_IDS = [
  // Its own ICP, for the sub-service. Phase 1's profiles the buyer of the headline service, who is
  // often not the buyer of one sub-service — see `PHASE2_OVERRIDES["icp"]` in the backend.
  "icp",
  "cro",
  "pillar_page",
  "funnel",
  "lead_magnet",
  "blog",
  "sms_sequence",
  "content_marketing_strategy",
  "funnel_hub_media",
] as const;

/** Number a run of assets 1..n and attach its display metadata.
 *
 * `stageNumber` is the position in *this* sequence, not a catalog id: Blog is stage 08 in Phase 1
 * and stage 04 in Phase 2. Deriving it here rather than storing it on the asset is what lets one
 * asset appear at a different point in each phase without duplicating its definition. */
function buildStages(assets: AssetDefinition[]): PipelineStageDef[] {
  return assets.map((asset, i) => ({
    stageNumber: i + 1,
    asset,
    emoji: EMOJI_BY_ASSET_ID[asset.asset_id] ?? "✨",
    hitl: HITL_ASSET_IDS.has(asset.asset_id),
  }));
}

/** Every Phase 1 asset in the order it's built, excluding the auxiliary Competitor Research
 * assets. Those are not steps in the sequential 15-stage flow: each one runs automatically as a
 * *prepass* inside its paired main stage (see `_run_competitor_prepass` in the backend's
 * `pipeline.py`), so it never gets its own review gate — the operator reviews the main asset
 * that consumed it. */
export const PHASE1_STAGES: PipelineStageDef[] = buildStages(
  ASSET_CATALOG.filter((a) => a.category !== "Competitor Research"),
);

/** The Phase 2 stages, in Phase 2's own order, using Phase 2's own intake.
 *
 * The definitions come from `data/phase2Catalog.ts`, not from `ASSET_CATALOG`: they are the Phase 1
 * assets with Phase 2's delta applied — the inputs its prompt files do not have removed, the ones
 * they word differently relabelled, and the documents inherited from the parent Phase 1 run marked
 * as needing the operator's say-so before being reused. */
export const PHASE2_STAGES: PipelineStageDef[] = buildStages(
  PHASE2_ASSET_IDS.map((id) => {
    const asset = PHASE2_ASSETS[id];
    // A typo here would otherwise surface as a stage that renders blank and generates nothing, at
    // whatever point in the run it happens to sit.
    if (!asset) throw new Error(`Phase 2 references unknown asset_id "${id}"`);
    return asset;
  }),
);

/** Everything the UI needs to describe a phase, so no component hard-codes "Phase 1" or a count. */
export const PHASE_META: Record<
  PipelinePhase,
  { id: PipelinePhase; label: string; short: string; scope: string; stages: PipelineStageDef[] }
> = {
  phase1: {
    id: "phase1",
    label: "Phase 1",
    short: "P1",
    scope: "Core service",
    stages: PHASE1_STAGES,
  },
  phase2: {
    id: "phase2",
    label: "Phase 2",
    short: "P2",
    scope: "Sub-service",
    stages: PHASE2_STAGES,
  },
};

/** Ordered for the toggle, so the control iterates this rather than restating the phase list. */
export const PHASE_ORDER: PipelinePhase[] = ["phase1", "phase2"];

export function stagesFor(phase: PipelinePhase): PipelineStageDef[] {
  return PHASE_META[phase].stages;
}

export function totalStagesFor(phase: PipelinePhase): number {
  return PHASE_META[phase].stages.length;
}

/** The stage at `index` in `phase`, clamped — a restored chat can carry an index one past the end
 * (the run finished) and callers that only want the label should not have to special-case that. */
export function stageAt(phase: PipelinePhase, index: number): PipelineStageDef {
  const stages = stagesFor(phase);
  return stages[Math.min(Math.max(index, 0), stages.length - 1)];
}

/** main asset_id -> its competitor-analysis prepass asset, for the 10 stages that have one.
 * Derived from the catalog's own `pairedCompetitorAssetId` links rather than re-listed here, so
 * it cannot drift from `assetCatalog.ts`. */
export const PREPASS_BY_MAIN_ASSET: Record<string, { assetId: string; label: string }> =
  Object.fromEntries(
    ASSET_CATALOG.filter((a) => a.category !== "Competitor Research" && a.pairedCompetitorAssetId).map((a) => {
      const competitorId = a.pairedCompetitorAssetId as string;
      const competitor = ASSET_CATALOG.find((c) => c.asset_id === competitorId);
      return [a.asset_id, { assetId: competitorId, label: competitor?.label ?? competitorId }];
    }),
  );

/** Field ids across the 15 main schemas that identify the client, folded into one run-level
 * profile. The same fact is named differently per schema (each was transcribed from its own
 * source prompt's wording), so several field_ids map to the same profile key. */
export type ClientFactKey =
  | "client_name"
  | "website_url"
  | "industry"
  | "region"
  /** The service the run is actually selling, as opposed to the client's industry.
   *
   * These are not the same thing, and conflating them was a real bug: topic suggestions were
   * anchored on `industry` (from ICP's "industry / niche" answer), so a client whose ICP answer
   * leaned SEO got SEO topics on every gate no matter what they had typed into Target Service.
   *
   * Needed at the run level because four of the eight topic gates have no service field of their
   * own, and three of those (blog, webinar, podcast) ask for the topic as their *first* question —
   * so there is no stage answer to read and the fact has to come from earlier in the run. */
  | "target_service";

/** field_id -> the run-level fact it carries.
 *
 * Serves two jobs. It builds the client profile the competitor sub-step needs, and it is what lets
 * a stage skip questions the operator has already answered: `client_name` and `client_website_url`
 * appear on six of the fifteen assets, so without this the same two questions get asked six times.
 *
 * Only put a field here when every asset that uses it means *exactly* the same thing. `industry` is
 * safe (one client, one industry); a field like `target_service_or_sub_service` is not — it
 * legitimately differs per asset, and reusing it would silently answer the wrong question. */
export const CLIENT_PROFILE_SOURCES: Record<string, ClientFactKey> = {
  // ICP's own field names. These matter most: ICP is stage 01, so its answers are the only client
  // facts available when the gated competitor sub-step runs immediately after it.
  company_name: "client_name",
  website_url: "website_url",
  market_region_country: "region",
  // Later stages name the same facts differently (each schema was transcribed from its own source
  // prompt's wording), so several field_ids map onto one profile key.
  client_name: "client_name",
  client_website_url: "website_url",
  client_website_url_live_reference: "website_url",
  existing_page_url: "website_url",
  industry: "industry",
  industry_niche: "industry",
  client_industry: "industry",
  sub_vertical_niche: "industry",
  region_location: "region",
  region_country: "region",
  // Every per-asset name for "the service this deliverable is for". Write-only (see below):
  // the stages word them differently because they mean subtly different things, so the fact is
  // collected for run-level use and never auto-fills another stage's question.
  target_service_or_sub_service: "target_service",
  target_service_offer: "target_service",
  target_service_offer_to_ladder: "target_service",
  target_service_if_different_from_pillar_page: "target_service",
  service_or_product_line_being_funnel_mapped: "target_service",
  primary_service_pillar_page_being_supported: "target_service",
};

/** Fields that feed the profile but must never be auto-filled *from* it, because they are a
 * narrower thing than the fact they contribute:
 * - `existing_page_url` — the specific page being rewritten, not the client's site. Filling it
 *   from the home URL would rewrite the wrong page.
 * - `sub_vertical_niche` — sits one level below `client_industry` ("social media marketing" under
 *   "Digital marketing"). Both map to `industry` so either can seed the competitor search, but reusing one
 *   value for both would collapse a distinction the CRO prompt deliberately asks for twice.
 * - the six `target_service` sources — each stage means something subtly different by it (the
 *   service a lead magnet feeds into is not the service a value ladder is built from), so they
 *   all contribute the run-level fact that anchors topic suggestions while none of them answers
 *   another stage's question. Reading in that direction would put the CRO page's service into
 *   the Lead Magnet's "Target Service / Offer" unasked. */
const WRITE_ONLY_PROFILE_FIELDS = new Set([
  "existing_page_url",
  "sub_vertical_niche",
  "target_service_or_sub_service",
  "target_service_offer",
  "target_service_offer_to_ladder",
  "target_service_if_different_from_pillar_page",
  "service_or_product_line_being_funnel_mapped",
  "primary_service_pillar_page_being_supported",
]);

/** The read direction of `CLIENT_PROFILE_SOURCES`, for auto-filling repeated questions. */
export const FIELD_TO_CLIENT_FACT: Record<string, ClientFactKey> = Object.fromEntries(
  Object.entries(CLIENT_PROFILE_SOURCES).filter(([fieldId]) => !WRITE_ONLY_PROFILE_FIELDS.has(fieldId)),
) as Record<string, ClientFactKey>;

/** Intake fields that ask for a whole page of copy, mapped to the field that already holds that
 * page's URL. When both are on the same asset and the URL answer is usable, the pipeline reads the
 * page itself (`POST /api/pipeline/scrape`) instead of asking the operator to paste it.
 *
 * Keyed by field_id, so it applies to every asset that asks the same question. Only pair fields
 * where the URL genuinely *is* the source of that content: the CRO stage's "Existing Page Content"
 * is exactly the page at "Existing Page URL". Pillar Page's "Reference Design Source" is not a
 * candidate — that field wants a page's visual design, which extracted text cannot carry.
 *
 * The URL field must sit earlier in the asset's field list than the content field, since the read
 * uses the answer the intake has already collected. If it doesn't, nothing breaks — the content
 * field is simply asked the old way. */
export const SCRAPE_SOURCES: Record<string, string> = {
  existing_page_content: "existing_page_url",
};

/** Stages whose page-source question has "there is no such page yet" as a real answer.
 *
 * `cro` audits an existing page, and a run routinely has none to audit: the ICP is for Social Media
 * Marketing for e-commerce and the client has never had a page for that service. Nothing in the UI
 * said so, and the field is `required` — so the operator's only ways forward were to nominate a
 * page the audit is not about (usually the home page, which then also became the DESIGN.md source)
 * or to type something the prompt does not recognise.
 *
 * The capability was already there. `Master_Prompt_Universal_Page_Rewrite_v1.md` reads:
 *
 * > Default execution mode is **Quick Win (Spear Gun)** … If the inputs state "NEW PAGE", switch to
 * > build-from-scratch mode and skip Step 2's before/after comparisons, retaining every other
 * > instruction.
 *
 * and both fields' own instructions offer the sentinel (`YOUR ANSWER / NEW PAGE — no existing URL`).
 * So this is not a new mode; it is the existing one made reachable by something other than knowing
 * the exact phrase to type. `backend/tests/test_new_page_mode.py` connects the three files that
 * have to agree — the prompt's clause, the schema's field instructions, and this table — so the
 * mode cannot be edited out from under the button that offers it.
 *
 * The answers written are the field instructions verbatim rather than a bare "NEW PAGE", because
 * they land in the INPUTS block an operator may later read back.
 */
export interface NewPageOption {
  /** The field asking for the page — where the offer appears. */
  urlFieldId: string;
  /** The field asking for that page's copy, answered at the same moment: asking for the content of
   * a page that does not exist is the same question twice. */
  contentFieldId: string;
  urlAnswer: string;
  contentAnswer: string;
  /** Names what the page would be *for*, so the question is about a service rather than abstract.
   * Must sit earlier in the asset's field list than `urlFieldId`. */
  subjectFieldId: string;
  /** The stage that builds a page from nothing, for an operator who would rather skip this one.
   * It is the second option and not the first: this stage is what *writes the copy* that stage
   * builds from, so skipping it moves the copy problem rather than solving it. */
  insteadAssetId: string;
}

export const NEW_PAGE_OPTIONS: Record<string, NewPageOption> = {
  cro: {
    urlFieldId: "existing_page_url",
    contentFieldId: "existing_page_content",
    urlAnswer: "NEW PAGE — no existing URL",
    contentAnswer: "NEW PAGE — NO EXISTING COPY",
    subjectFieldId: "target_service_or_sub_service",
    insteadAssetId: "pillar_page",
  },
};

/** The sentinel the prompt switches on. Both answers above must contain it. */
export const NEW_PAGE_MARKER = "NEW PAGE";

/** Main assets whose competitor analysis runs as its own reviewable step *before* the stage's
 * intake — everything it needs (the client's URL, industry, region) is already in the run-level
 * profile from ICP, so there is nothing to wait for.
 *
 * `offers` is here because its competitor set is the thing the value ladder is priced against: the
 * operator needs to see who publishes what, and at what starting price, before a ladder is built on
 * top of it. Running it invisibly inside generation would price the client's offers against a list
 * nobody checked. */
export const GATED_COMPETITOR_MAIN_ASSET_IDS = new Set<string>([
  "cro",
  "offers",
  "lead_magnet",
  "content_marketing_strategy",
  "social_content_strategy_audit",
  "book",
]);

/** Competitor research that can only run *during* intake, because what it searches for is an intake
 * answer. Keyed by the field that receives the analysis.
 *
 * Pillar Page is the case: the competitor set has to be pillar pages on *this page's topic* ("Social
 * Media Marketing"), and that topic is `primary_keyword_head_term`, asked three questions earlier.
 * Running before intake — the way CRO's does — would mean searching before knowing what to search
 * for. So when the walk reaches the competitor field, the operator is asked whether to research it,
 * rather than being handed either an unasked-for search or a paste-it-yourself question.
 *
 * `topicFieldId` must sit earlier in the asset's field list than the competitor field itself. */
export const COMPETITOR_CONSENT_FIELDS: Record<
  string,
  { competitorAssetId: string; topicFieldId: string; subject: string }
> = {
  competitor_analysis_pillar_page: {
    competitorAssetId: "competitor_analysis_seo_pillar_page",
    topicFieldId: "primary_keyword_head_term",
    subject: "pillar pages",
  },
  competitor_analysis_blog: {
    competitorAssetId: "competitor_analysis_blog",
    topicFieldId: "blog_topic_working_title",
    subject: "blogs",
  },
  competitor_analysis_webinars: {
    competitorAssetId: "competitor_analysis_webinars",
    topicFieldId: "webinar_topic_working_title",
    subject: "webinars",
  },
  competitor_analysis_podcast: {
    competitorAssetId: "competitor_analysis_podcast",
    topicFieldId: "episode_topic_working_title",
    subject: "podcasts",
  },
};

/** Every main asset whose competitor analysis the UI drives explicitly, by either route above.
 *
 * Must stay in sync with `GATED_COMPETITOR_MAIN_ASSET_IDS` in the backend's
 * `app/services/competitor.py`: the backend excludes exactly these from its automatic prepass, so an
 * asset listed there but not here loses its competitor input entirely, and one listed here but not
 * there gets its analysis run twice — once by the UI, once again inside generation. */
export const UI_DRIVEN_COMPETITOR_MAIN_ASSET_IDS = new Set<string>([
  ...GATED_COMPETITOR_MAIN_ASSET_IDS,
  ...Object.values(COMPETITOR_CONSENT_FIELDS).flatMap((c) =>
    ASSET_CATALOG.filter((a) => a.pairedCompetitorAssetId === c.competitorAssetId).map((a) => a.asset_id),
  ),
]);


// --------------------------------------------------------------------------------------
// Phase 2 wiring
// --------------------------------------------------------------------------------------

/** The one question a Phase 2 run opens with, after the parent run has been chosen.
 *
 * A real `FieldDef` rather than a bespoke card so it reuses everything a stage question already has:
 * the choice pills, the free-text answer bar for anything not listed, the hint line, and the edit
 * affordance. It belongs to no asset — it is a run-level fact, like the client's name in Phase 1 —
 * so it is asked once, before stage 01, and every field listed in `PHASE2_FIELD_TO_SUB_SERVICE` is
 * then answered from it instead of being asked again.
 */
export const SUB_SERVICE_FIELD: FieldDef = {
  field_id: SUB_SERVICE_FACT,
  label: "Sub-service",
  kind: "multi_select",
  required: true,
  source: "user_input",
  choices: [...SUB_SERVICE_CHOICES],
  helpText:
    "Select one or more sub-services, or type a custom service below. Each selected service gets its own linked Phase 2 child run, while this chat and the Phase 1 context are shared.",
  placeholder: "e.g. Google Ads, Meta Ads, TikTok",
};

// --------------------------------------------------------------------------------------
// The client's industry — a run-level question, like the sub-service
// --------------------------------------------------------------------------------------

/** The run-level facts the confirmed industry is filed under in `clientProfile`. Not a
 * `ClientFactKey`: nothing auto-fills a stage field from them. They drive the voice pack (server
 * side, off the run's `industry_profile` entry) and the stage advisories (here). */
export const INDUSTRY_BUCKET_FACT = "industry_bucket";
export const INDUSTRY_LABEL_FACT = "industry_label";

/** The typed-answer choice. Worded so `specifyPrefix` recognises it and `QuestionWidget` opens its
 * inline box rather than filing the words as an answer. */
export const INDUSTRY_OTHER_CHOICE = "Other: specify";

/** Asked when the classifier had a confident guess: confirm it, pick another bucket, or type one.
 *
 * `general` is not offered as a pill. It is what a typed answer that fits nothing becomes, and a
 * bare "Other industry" with no words attached would give the voice pack nothing to name. */
export const INDUSTRY_CONFIRM_FIELD: FieldDef = {
  field_id: "__industry_confirm",
  label: "Client's industry",
  kind: "enum_choice",
  required: true,
  source: "user_input",
  choices: [
    ...INDUSTRY_BUCKETS.filter((b) => b !== "general").map((b) => INDUSTRY_PROFILES[b].label),
    INDUSTRY_OTHER_CHOICE,
  ],
  helpText:
    "This is the client's own industry, not the industry of the customers they sell to. It sets the voice every stage writes in (tone, compliance caution, vocabulary) and which stages are recommended. You can change it later.",
};

/** Asked when there was no confident guess: the operator names the industry in their own words, and
 * the server maps it to a bucket (or to `general`, keeping the words). */
export const INDUSTRY_TEXT_FIELD: FieldDef = {
  field_id: "__industry_text",
  label: "What industry is the client in?",
  kind: "text",
  required: true,
  source: "user_input",
  placeholder: "e.g. Mortgage broking, Dental clinic, B2B payroll software, Plumbing",
  helpText:
    "The client's own industry, not their customers'. It sets the voice every stage writes in and which stages are recommended.",
};

export const INDUSTRY_FIELD_IDS = new Set([INDUSTRY_CONFIRM_FIELD.field_id, INDUSTRY_TEXT_FIELD.field_id]);

/** Which bucket a confirm-card answer names, by label. Undefined for a typed ("Other: …") answer. */
export function industryBucketForLabel(answer: string): IndustryBucket | undefined {
  const text = answer.trim();
  return INDUSTRY_BUCKETS.find((b) => INDUSTRY_PROFILES[b].label === text);
}

/** field_id -> the sub-service fact that answers it, for Phase 2's intake walk. */
export const PHASE2_FIELD_TO_FACT: Record<string, string> = PHASE2_FIELD_TO_SUB_SERVICE;

/** Phase 2's competitor research, all of it gated before the stage it feeds.
 *
 * Three of the seven stages research competitors, and each one is reviewed on its own card first —
 * including Blog, which in Phase 1 is a mid-intake consent step keyed on the blog's topic. In Phase 2
 * it runs *before* the intake and searches on the sub-service instead, because the whole point of it
 * here is to inform the topic, keyword and awareness answers the operator is about to give. Must stay
 * in sync with `GATED_COMPETITOR_BY_MAIN_ASSET_BY_PHASE["phase2"]` in the backend's
 * `app/services/competitor.py`, which is what keeps these out of the invisible prepass. */
export const PHASE2_GATED_COMPETITOR_MAIN_ASSET_IDS = new Set<string>([
  "cro",
  "lead_magnet",
  "blog",
  "content_marketing_strategy",
]);

/** Answers a Phase 2 stage gives itself, so the walk never asks the question.
 *
 * Stronger than a `default`, which the walk only reaches on a field it decided to skip: these are
 * seeded into the intake before the walk starts, so `planField` sees an answer already present and
 * passes the field over as `already-answered`. Still ordinary answers, though — `editField` can
 * change one, which is what keeps this from being a hard-coded value the operator cannot reach.
 *
 * Phase 2's CRO stage is the only user, and the two sentinels are the interesting half. This phase
 * exists *because* the client has no page for the sub-service yet, so "is there an existing page to
 * audit?" has one answer for every run there will ever be, and asking it is asking a question whose
 * answer the run already knows. They are read off `NEW_PAGE_OPTIONS` rather than retyped, because
 * the exact strings are what `Master_Prompt_Universal_Page_Rewrite_v1.md` switches build-from-
 * scratch mode on, and `backend/tests/test_new_page_mode.py` is what holds the prompt, the schema
 * and that table to the same wording.
 *
 * `page_scope` is the same kind of fact one level down: a Phase 2 run builds the page for one
 * sub-service, which is what the prompt's SUB-SERVICE scope means. */
export const PHASE2_CONSTANT_ANSWERS: Record<string, Readonly<Record<string, string>>> = {
  cro: {
    page_scope: "SUB-SERVICE",
    [NEW_PAGE_OPTIONS.cro.urlFieldId]: NEW_PAGE_OPTIONS.cro.urlAnswer,
    [NEW_PAGE_OPTIONS.cro.contentFieldId]: NEW_PAGE_OPTIONS.cro.contentAnswer,
  },
};

const CONSTANT_ANSWERS_BY_PHASE: Record<PipelinePhase, Record<string, Readonly<Record<string, string>>>> = {
  phase1: {},
  phase2: PHASE2_CONSTANT_ANSWERS,
};

/** The answers this stage starts its intake already holding, as a fresh mutable object. */
export function constantAnswersFor(phase: PipelinePhase, assetId: string): Record<string, string> {
  return { ...(CONSTANT_ANSWERS_BY_PHASE[phase][assetId] ?? {}) };
}

// A constant naming a field the stage does not have is the one failure here with no symptom: the
// answer is seeded, no question is skipped because none matched, and the stage asks the operator for
// a page that does not exist — which is the exact bug this table was added to prevent.
for (const [assetId, answers] of Object.entries(PHASE2_CONSTANT_ANSWERS)) {
  const asset = PHASE2_ASSETS[assetId];
  if (!asset) throw new Error(`Phase 2 constant answers name unknown asset_id "${assetId}"`);
  for (const fieldId of Object.keys(answers)) {
    if (!asset.fields.some((f) => f.field_id === fieldId)) {
      throw new Error(`Phase 2 constant answer for "${assetId}" names unknown field "${fieldId}"`);
    }
  }
}

/** Stages whose approved competitor listing is summarised for the operator before their own intake
 * starts, and what that briefing is about.
 *
 * Mirrors `_BRIEFS` in the backend's `app/services/insights.py` — a stage listed here with no
 * briefing defined there gets a 404 where the operator expects a summary. Blog needs it because the
 * next four questions it asks (topic, primary keyword, supporting keywords, awareness level) are
 * exactly what the briefing reports on; Content Marketing because its cluster architecture is
 * designed against the market's existing coverage. */
export const COMPETITOR_BRIEFING_STAGES: Record<string, { title: string; blurb: string }> = {
  blog: {
    title: "What this market publishes",
    blurb:
      "Read from the competitor blogs just approved — the content types, awareness levels and keyword territory in play, and what nobody has taken.",
  },
  content_marketing_strategy: {
    title: "What this market's content programmes look like",
    blurb:
      "Read from the competitor set just approved — formats, cadence, cluster structure and depth, and the gaps left open.",
  },
};

/** Per-phase lookups, so a caller asks "for this phase" instead of branching on the phase itself. */
export const GATED_COMPETITOR_IDS_BY_PHASE: Record<PipelinePhase, ReadonlySet<string>> = {
  phase1: GATED_COMPETITOR_MAIN_ASSET_IDS,
  phase2: PHASE2_GATED_COMPETITOR_MAIN_ASSET_IDS,
};

/** Phase 2 has no mid-intake competitor consent step: its Pillar Page has no competitor input at
 * all, and its Blog researches before intake rather than during it. */
export const COMPETITOR_CONSENT_FIELDS_BY_PHASE: Record<
  PipelinePhase,
  Record<string, { competitorAssetId: string; topicFieldId: string; subject: string }>
> = {
  phase1: COMPETITOR_CONSENT_FIELDS,
  phase2: {},
};

/** The industry of the audience a Phase 2 run sells its sub-service to — its own fact, not Phase 1's.
 *
 * ICP's `industry` ("of the ICP you are targeting"), Lead Magnet's `industry` and Content
 * Marketing's `industry_niche` all describe the *audience*, and in Phase 1 they share the run-level
 * `industry` fact. Phase 2 builds its own ICP for the sub-service, whose buyers can sit in a
 * different industry from the headline service's, so reusing Phase 1's answer there would hand the
 * sub-service ICP the parent's audience with an edit chip on it. So in Phase 2 these fields map to
 * this fact instead: empty when the Phase 2 ICP asks, so it is asked fresh, and then reused by the
 * later stages of the same run rather than asked three times.
 *
 * Stored per run (`phase2AudienceIndustryKey`), because a chat can hold several Phase 2 tracks — one
 * per sub-service — and each has its own ICP and its own audience. */
export const PHASE2_AUDIENCE_INDUSTRY_FACT = "phase2_audience_industry";
export const PHASE2_AUDIENCE_INDUSTRY_FIELDS: readonly string[] = ["industry", "industry_niche"];

/** The client-profile key one Phase 2 run's audience industry is stored under. */
export function phase2AudienceIndustryKey(runId: string | null): string {
  return `${PHASE2_AUDIENCE_INDUSTRY_FACT}:${runId ?? "unsaved"}`;
}

export const FIELD_TO_FACT_BY_PHASE: Record<PipelinePhase, Record<string, string>> = {
  phase1: FIELD_TO_CLIENT_FACT,
  phase2: {
    ...FIELD_TO_CLIENT_FACT,
    ...Object.fromEntries(PHASE2_AUDIENCE_INDUSTRY_FIELDS.map((id) => [id, PHASE2_AUDIENCE_INDUSTRY_FACT])),
    ...PHASE2_FIELD_TO_FACT,
  },
};

/** `PREPASS_BY_MAIN_ASSET`, per phase. Empty for Phase 2: every competitor stage it runs is gated,
 * so nothing is folded invisibly into a generation call. */
export const PREPASS_BY_MAIN_ASSET_BY_PHASE: Record<PipelinePhase, Record<string, { assetId: string; label: string }>> =
  {
    phase1: PREPASS_BY_MAIN_ASSET,
    phase2: {},
  };

/** The competitor stage each Phase 2 stage gates on, derived from the Phase 2 definitions so it
 * cannot name a pairing Phase 2 dropped (Pillar Page's). */
export const PHASE2_COMPETITOR_BY_MAIN_ASSET: Record<string, { assetId: string; label: string }> = Object.fromEntries(
  Object.values(PHASE2_ASSETS)
    .filter((a) => a.pairedCompetitorAssetId && PHASE2_GATED_COMPETITOR_MAIN_ASSET_IDS.has(a.asset_id))
    .map((a) => {
      const competitorId = a.pairedCompetitorAssetId as string;
      const competitor = ASSET_CATALOG.find((c) => c.asset_id === competitorId);
      return [a.asset_id, { assetId: competitorId, label: competitor?.label ?? competitorId }];
    }),
);

/** The competitor stage to run for `assetId` in `phase`, or undefined when it has none. */
export function competitorStageFor(
  phase: PipelinePhase,
  assetId: string,
): { assetId: string; label: string } | undefined {
  return phase === "phase2" ? PHASE2_COMPETITOR_BY_MAIN_ASSET[assetId] : PREPASS_BY_MAIN_ASSET[assetId];
}
