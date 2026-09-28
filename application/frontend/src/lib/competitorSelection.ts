import type { CompetitorAnalysisResult, CompetitorRow } from "../pipeline/pipelineApi";

/** How the operator wants a competitor listing used by the stage it feeds.
 *
 * - `all` — every competitor the search found. The default, and what approving meant before the
 *   choice existed.
 * - `select` — only the rows the operator ticked.
 * - `own` — the search's result is set aside and the operator's own list is used instead. Also the
 *   way out of a failed or empty search, where there is nothing to select from.
 */
export type CompetitorSelectionMode = "all" | "select" | "own";

export interface ParsedOwnList {
  rows: CompetitorRow[];
  /** Lines with no website in them. Reported rather than dropped, because a competitor the
   * operator typed and then never saw again is worse than being asked to add its domain. */
  unreadable: string[];
}

/** Stated on every operator-supplied listing, because the rows carry `Unverified` — the one
 * confidence value the database accepts that does not claim research was done — and the stage
 * reading them should know why. */
export const OWN_LIST_NOTE = "Competitor list supplied by the operator; not sourced or verified by web research.";

// A URL, or a bare host with at least one dot and a TLD of two or more letters. Deliberately not
// matched inside an email address — "sales@acme.com" names a contact, not a competitor site.
const URL_OR_DOMAIN = /(?:https?:\/\/)?(?<![@\w.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(\/[^\s,;)]*)?/i;

/** Read a pasted competitor list, one competitor per line.
 *
 * Accepts the shapes an operator actually types: `acme.com`, `https://www.acme.com/services`,
 * `Acme Digital — acme.com`, `Acme Digital (acme.com)`, `1. Acme Digital, https://acme.com`. A
 * website is required on every line: the domain is what identifies a competitor everywhere
 * downstream (the `competitors` table is unique on it, and the social audit resolves handles from
 * it), which is also why a search result with no domain is dropped by `parse_analysis`.
 */
export function parseOwnCompetitorList(text: string): ParsedOwnList {
  const rows: CompetitorRow[] = [];
  const unreadable: string[] = [];
  const seen = new Set<string>();

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/^\s*(?:\d+[.)]|[-*•])\s*/, "").trim();
    if (!line) continue;

    const match = URL_OR_DOMAIN.exec(line);
    if (!match) {
      unreadable.push(line);
      continue;
    }

    const domain = match[1].toLowerCase().replace(/^www\./, "");
    if (seen.has(domain)) continue;
    seen.add(domain);

    // A specific page is worth keeping as the row's page link; a bare domain is already the domain.
    const typed = match[0];
    const hasPath = Boolean(match[2] && match[2] !== "/");
    const pageUrl = /^https?:\/\//i.test(typed) ? typed : hasPath ? `https://${typed}` : null;

    // Whatever is left once the website is taken out, minus the punctuation that joined the two.
    const name = (line.slice(0, match.index) + " " + line.slice(match.index + match[0].length))
      .replace(/[()[\]]/g, " ")
      .replace(/\s+/g, " ")
      .replace(/^[\s,;:|—–-]+|[\s,;:|—–-]+$/g, "")
      .trim();

    rows.push({
      rank: rows.length + 1,
      domain,
      name: name || domain,
      page_url: pageUrl,
      verification_confidence: "Unverified",
      offering_summary: null,
    });
  }

  return { rows, unreadable };
}

/** The listing the operator approved, in the shape `saveCompetitorAnalysis` sends.
 *
 * Ranks are renumbered so a subset reads 1..n in the prompt rather than "2, 5, 9", which would
 * read as though the gaps were meaningful. `raw_output` is kept for a selection — it is the audit
 * trail of what the search actually returned — and cleared for an operator's own list, which the
 * search did not produce.
 */
export function buildApprovedResult(
  result: CompetitorAnalysisResult | undefined,
  mode: CompetitorSelectionMode,
  selectedDomains: ReadonlySet<string>,
  ownRows: CompetitorRow[],
  fallback: { target_url?: string; niche?: string; location?: string; service?: string } = {},
): CompetitorAnalysisResult {
  if (mode === "own") {
    return {
      asset_id: result?.asset_id ?? "",
      target_url: result?.target_url ?? fallback.target_url ?? "",
      raw_output: "",
      service: result?.service ?? fallback.service ?? null,
      niche: result?.niche ?? fallback.niche ?? null,
      location: result?.location ?? fallback.location ?? null,
      requested_count: ownRows.length,
      returned_count: ownRows.length,
      competitors: ownRows.map((row, i) => ({ ...row, rank: i + 1 })),
      notes: OWN_LIST_NOTE,
    };
  }

  if (!result) throw new Error("There is no search result to approve.");
  if (mode === "all") return result;

  const picked = result.competitors.filter((c) => selectedDomains.has(c.domain));
  const selectionNote = `Operator selected ${picked.length} of ${result.competitors.length} competitors found.`;
  return {
    ...result,
    returned_count: picked.length,
    competitors: picked.map((c, i) => ({ ...c, rank: i + 1 })),
    notes: result.notes ? `${selectionNote} ${result.notes}` : selectionNote,
  };
}
