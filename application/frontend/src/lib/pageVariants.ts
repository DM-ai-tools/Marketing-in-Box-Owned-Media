import type { PageVariantId, VariantPageRow } from "../pipeline/pipelineApi";

/** The Pillar Page's variants: up to four takes on the one page, compared; one approved for the
 * rest of the run, and every drafted variant kept in Deliverables.
 *
 * Restates `app/services/page_variants.py`'s ids, labels and fields. The server is the authority on
 * whether a variant can be built (it reads the pages); this decides what the picker offers and
 * explains why an option is off, so the operator is not asked to pay for a draft that will fail. */

export interface PageVariantDef {
  id: PageVariantId;
  label: string;
  /** One line on the picker: what this take does. */
  blurb: string;
}

export const PAGE_VARIANTS: PageVariantDef[] = [
  { id: "reference", label: "Beat your current page", blurb: "Measured against the client's hosted page, fixing what the CRO audit found." },
  { id: "competitor", label: "Outperform competitors", blurb: "Built to cover every topic the competitors' pages cover, better, with a beat sheet." },
  { id: "skills", label: "Built with your skill", blurb: "Follows the skill document you supplied as its method." },
  { id: "framework", label: "Built with your framework", blurb: "Structured by the framework you supplied, part by part." },
];

export const VARIANT_LABEL: Record<PageVariantId, string> = Object.fromEntries(PAGE_VARIANTS.map((v) => [v.id, v.label])) as Record<
  PageVariantId,
  string
>;

export const USE_SKILL_FIELD = "use_skill_document";
export const SKILL_FIELD = "skill_document";
export const USE_FRAMEWORK_FIELD = "use_framework_document";
export const FRAMEWORK_FIELD = "framework_document";

/** Same as the server's `service_scan.NO_REFERENCE_MARKER`. */
const NO_REFERENCE = "NO REFERENCE PAGE";

export interface VariantOption extends PageVariantDef {
  available: boolean;
  /** Why it is off, in one sentence saying what to supply. */
  reason?: string;
  /** Ticked when the picker opens. */
  defaultOn: boolean;
}

const yes = (value: string | undefined) => /^(y|yes|true)$/i.test((value ?? "").trim());
const hasDocument = (value: string | undefined) => {
  const text = (value ?? "").trim();
  return Boolean(text) && !/^(skip|n\/a|na|none)$/i.test(text);
};
const hasUrl = (value: string | undefined) => /https?:\/\/|^[\w-]+(\.[\w-]+)+/i.test((value ?? "").trim());

/** What the picker offers, from the stage's answers and whether the run holds a competitor listing. */
export function variantOptions(answers: Record<string, string>, hasCompetitorListing: boolean): VariantOption[] {
  const reference = (answers.reference_design_source ?? "").trim();
  const hostedPage =
    hasUrl(answers.parent_pillar_page_url) || hasUrl(answers.existing_page_url) || (hasUrl(reference) && !reference.toUpperCase().startsWith(NO_REFERENCE));
  const listing = hasCompetitorListing || hasDocument(answers.competitor_analysis_pillar_page);
  const skill = yes(answers[USE_SKILL_FIELD]) && hasDocument(answers[SKILL_FIELD]);
  const framework = yes(answers[USE_FRAMEWORK_FIELD]) && hasDocument(answers[FRAMEWORK_FIELD]);
  const state: Record<PageVariantId, [boolean, string]> = {
    reference: [hostedPage, "There is no hosted page to beat: this page doesn't exist yet."],
    competitor: [listing, "No competitor pages on this run: approve a competitor search first."],
    skills: [skill, "No skill document: answer Yes to “Use a Skill Document” and attach one."],
    framework: [framework, "No framework document: answer Yes to “Use a Framework Document” and attach one."],
  };
  return PAGE_VARIANTS.map((v) => {
    const [available, reason] = state[v.id];
    return { ...v, available, reason: available ? undefined : reason, defaultOn: available };
  });
}

const num = (n: number) => n.toLocaleString("en-US");

export interface ComparisonRow {
  /** The measure's name. */
  label: string;
  /** One cell per column, in column order. */
  cells: string[];
  /** Column index of the best value, when "more is better" applies and one column leads. */
  best: number | null;
}

/** The comparison card's rows: the drafts, then the hosted page and the competitor median when the
 * variants measured them. Every value is a count off the page; "best" is only marked where more is
 * plainly better for the buyer (topics, keywords, CTAs present, FAQ, structured data). */
export function comparisonRows(
  pages: VariantPageRow[],
  extra: { current?: VariantPageRow | null; competitorMedianWords?: number | null } = {},
): { columns: string[]; rows: ComparisonRow[] } {
  const cols = [...pages];
  if (extra.current) cols.push(extra.current);
  const columns = cols.map((p) => (p.variant === "current" ? "Current page (live)" : p.label));
  // A live page that could not be read keeps its column, with nothing measured in it.
  const unread = cols.map((p) => Boolean(p.error));
  const best = (values: (number | null)[]) => {
    const real = values.map((v, i) => [v, i] as const).filter(([v]) => v !== null && v > 0) as [number, number][];
    if (real.length < 2) return null;
    const top = Math.max(...real.map(([v]) => v));
    const leaders = real.filter(([v]) => v === top);
    return leaders.length === 1 && leaders[0][1] < pages.length ? leaders[0][1] : null;
  };
  const rows: ComparisonRow[] = [];
  const words = cols.map((p) => p.words);
  rows.push({
    label: extra.competitorMedianWords ? `Words (competitor median ${num(extra.competitorMedianWords)})` : "Words",
    cells: words.map((w) => (w ? num(w) : "—")),
    best: null,
  });
  rows.push({ label: "Sections (H2)", cells: cols.map((p) => String(p.sections.length)), best: null });
  const topics = cols.map((p) => (p.topics_covered ? p.topics_covered[0] : null));
  if (topics.some((t) => t !== null)) {
    rows.push({
      label: "Competitor topics covered",
      cells: cols.map((p) => (p.topics_covered ? `${p.topics_covered[0]}/${p.topics_covered[1]}` : "—")),
      best: best(topics),
    });
  }
  const keywords = cols.map((p) => (p.keywords_checked ? p.keywords_covered.length : null));
  if (keywords.some((k) => k !== null)) {
    rows.push({
      label: "Target keywords used",
      cells: cols.map((p) => (p.keywords_checked ? `${p.keywords_covered.length}/${p.keywords_checked}` : "—")),
      best: best(keywords),
    });
  }
  rows.push({ label: "CTAs", cells: cols.map((p) => String(p.ctas)), best: null });
  rows.push({ label: "Internal links", cells: cols.map((p) => String(p.internal_links)), best: best(cols.map((p) => p.internal_links)) });
  rows.push({ label: "Proof mentions", cells: cols.map((p) => String(p.proof_mentions)), best: best(cols.map((p) => p.proof_mentions)) });
  rows.push({ label: "FAQ", cells: cols.map((p) => (p.faq ? "Yes" : "No")), best: null });
  rows.push({ label: "Structured data", cells: cols.map((p) => p.schema_types.join(", ") || "None"), best: null });
  for (const row of rows) row.cells = row.cells.map((cell, i) => (unread[i] ? "—" : cell));
  return { columns, rows };
}

/** The beat sheet's topics, for the comparison request: plain strings in its order. */
export function beatSheetTopics(sheet: { topics: { topic: string }[] } | null | undefined): string[] {
  return (sheet?.topics ?? []).map((t) => t.topic);
}
