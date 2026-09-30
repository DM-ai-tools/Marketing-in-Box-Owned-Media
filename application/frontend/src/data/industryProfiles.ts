/** Which stages each industry bucket is advised against, and what the operator sees for each bucket.
 *
 * The client's industry is inferred at the end of the first intake, confirmed by the operator, and
 * stored on the run (`app/services/industry_voice.py` owns the taxonomy, the classifier and the
 * voice packs). This table is the UI half: the labels the confirm card offers, and the stages a
 * bucket does not usually need.
 *
 * **Advice, never removal.** A listed stage stays in the pipeline, in the diagram, and behind
 * "Start here →". What changes is that the forward walk stops in front of it and offers a skip as
 * the one-click default — the `NEW_PAGE_OPTIONS` pattern — because a static industry rule will be
 * wrong for somebody, and the operator is the one who knows it is them.
 *
 * `marketing_agency` lists nothing, so a run that never answers the question — or answers it with
 * the client type this pipeline was built around — walks exactly as it always has.
 *
 * Three files have to agree: `BUCKETS` in `industry_voice.py`, one voice pack per bucket in
 * `assets/voice_packs/`, and this table. `tests/test_industry_profiles_agree.py` parses this file and
 * also checks that no listed stage is one a still-recommended stage depends on — skipping Webinar
 * while recommending Book would leave Book asking for a webinar that was never built.
 */
import { ASSET_BY_ID } from "./assetCatalog";

export type IndustryBucket =
  | "marketing_agency"
  | "regulated_finance"
  | "healthcare"
  | "legal_professional"
  | "b2b_saas"
  | "e_commerce"
  | "local_service"
  | "general";

export interface IndustryProfileDef {
  label: string;
  /** Shown under the choice, so the operator can place a client that doesn't name itself this way. */
  examples: string;
  /** asset_id -> why this bucket rarely needs it. Shown on the advisory card and the diagram tag. */
  notRecommended: Readonly<Record<string, string>>;
}

export const DEFAULT_INDUSTRY_BUCKET: IndustryBucket = "marketing_agency";

export const INDUSTRY_PROFILES: Record<IndustryBucket, IndustryProfileDef> = {
  marketing_agency: {
    label: "Marketing / Creative Agency",
    examples: "digital marketing, SEO, paid media, design and creative studios",
    notRecommended: {},
  },
  regulated_finance: {
    label: "Regulated Finance",
    examples: "lending, mortgage and finance broking, financial advice, insurance, wealth",
    notRecommended: {
      blog: "Regulated finance content usually goes through compliance review, so a high-volume blog is rarely where these clients win work.",
      webinar: "A live webinar about financial products is a financial promotion with its own compliance burden. Most regulated finance clients skip it.",
      book: "Built from the webinar, so it goes with it. A published book is also a long-lived financial promotion.",
      podcast: "Rarely a lead channel for regulated finance, and every episode would need compliance sign-off.",
    },
  },
  healthcare: {
    label: "Healthcare",
    examples: "clinics, dental, allied health, specialists, aged care",
    notRecommended: {
      webinar: "Patients seldom register for webinars, and health-advertising rules restrict what a live session can claim.",
      book: "Built from the webinar, so it goes with it.",
      podcast: "Rarely drives patient bookings for a local practice.",
    },
  },
  legal_professional: {
    label: "Legal & Professional Services",
    examples: "law firms, accountants, consultants, engineers, architects",
    notRecommended: {
      podcast: "Seldom a source of engagements for most professional practices.",
      sms_sequence: "Clients of professional firms rarely expect marketing texts, and it can read as unprofessional.",
    },
  },
  b2b_saas: {
    label: "B2B SaaS / Software",
    examples: "SaaS platforms, developer tools, B2B software vendors",
    notRecommended: {
      sms_sequence: "B2B software buyers are reached by email and in-product, not by SMS.",
    },
  },
  e_commerce: {
    label: "E-commerce / Retail",
    examples: "online stores, DTC brands, consumer products",
    notRecommended: {
      webinar: "Shoppers buy from product pages and offers, not webinars.",
      book: "Built from the webinar, so it goes with it.",
      podcast: "Rarely moves product sales for a store.",
    },
  },
  local_service: {
    label: "Local Service Business",
    examples: "trades, home services, cleaning, automotive, salons, fitness studios",
    notRecommended: {
      webinar: "Local customers call or book. They don't attend webinars.",
      book: "Built from the webinar, so it goes with it.",
      podcast: "Rarely brings in local jobs.",
    },
  },
  general: {
    label: "Other industry",
    examples: "anything the options above don't describe. Type it in your own words.",
    notRecommended: {},
  },
};

export const INDUSTRY_BUCKETS = Object.keys(INDUSTRY_PROFILES) as IndustryBucket[];

export function isIndustryBucket(value: string | undefined | null): value is IndustryBucket {
  return !!value && value in INDUSTRY_PROFILES;
}

/** Why `bucket` advises against `assetId`, or undefined when it doesn't. */
export function notRecommendedReason(bucket: string | undefined | null, assetId: string): string | undefined {
  return isIndustryBucket(bucket) ? INDUSTRY_PROFILES[bucket].notRecommended[assetId] : undefined;
}

// A misspelt asset id here has no symptom: the stage is simply never advised against. Fail at load.
for (const [bucket, def] of Object.entries(INDUSTRY_PROFILES)) {
  for (const assetId of Object.keys(def.notRecommended)) {
    if (!ASSET_BY_ID[assetId]) throw new Error(`industryProfiles: "${bucket}" names unknown asset_id "${assetId}"`);
  }
}
