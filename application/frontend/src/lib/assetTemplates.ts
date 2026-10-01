/** How each asset is laid out in the Visual view and the HTML report.
 *
 * A template decides *framing*, never content: what the hero says the document is, whether long
 * prose folds, and which sections start folded. It never reorders, drops or rewrites a section —
 * the reader and the report are a re-layout of the document, and `smoke/report.tsx` checks every
 * word of every real output survives into the exported file.
 *
 * One table, keyed by asset id, with a default. Phase 2 reuses Phase 1's ids, so it needs nothing
 * of its own.
 */

export interface AssetTemplate {
  /** One line under the title saying what kind of document this is. */
  kicker: string;
  /** Read straight through (a blog, a book): prose never folds, since folding an article's
   * paragraphs is hiding the article. */
  longForm?: boolean;
  /** Sections that start folded, matched on their label: reference material a reader opens when
   * they need it rather than reads on the way through. */
  folded?: RegExp[];
}

/** Folded on every asset: what the document was built from, not the document itself. */
const ALWAYS_FOLDED: RegExp[] = [/^Topic suggestions$/i, /^Competitors scanned$/i];

const DEFAULT: AssetTemplate = { kicker: "Marketing deliverable" };

const TEMPLATES: Record<string, AssetTemplate> = {
  icp: { kicker: "Ideal customer profile" },
  cro: { kicker: "Conversion audit and page rewrite", folded: [/implementation|handoff|developer notes/i] },
  offers: { kicker: "Offer suite and value ladder" },
  pillar_page: { kicker: "Pillar page", folded: [/build notes|implementation/i] },
  funnel: { kicker: "Funnel architecture" },
  lead_magnet: { kicker: "Lead magnet brief" },
  sms_sequence: { kicker: "SMS sequence" },
  funnel_hub_media: { kicker: "Funnel hub media" },
  webinar: { kicker: "Webinar package" },
  book: { kicker: "Book", longForm: true },
  blog: { kicker: "Blog article", longForm: true },
  podcast: { kicker: "Podcast package" },
  content_marketing_strategy: { kicker: "Content marketing strategy" },
  social_content_strategy_audit: { kicker: "Social content audit" },
  plan_of_action: { kicker: "Plan of action" },
};

export function templateFor(assetId: string | undefined): AssetTemplate {
  return (assetId && TEMPLATES[assetId]) || DEFAULT;
}

export function isFolded(template: AssetTemplate, sectionLabel: string): boolean {
  return [...ALWAYS_FOLDED, ...(template.folded ?? [])].some((r) => r.test(sectionLabel.trim()));
}

/** Reading time at 230 words a minute, never under one. */
export function readingMinutes(words: number): number {
  return Math.max(1, Math.round(words / 230));
}
