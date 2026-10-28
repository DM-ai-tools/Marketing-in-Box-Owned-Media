import type { CompetitorRow, CompetitorValidation, ValidatedCompetitor, ValidationVerdict } from "../pipeline/pipelineApi";

/** The words the card, the badges and the deliverable use for each verdict. The server's
 * `VERDICT_LABEL` (`app/services/competitor_validation.py`) says the same; change one, change both. */
export const VERDICT_LABEL: Record<ValidationVerdict, string> = {
  implement_now: "Implement now",
  test_first: "Test first",
  differentiate: "Differentiate",
  avoid: "Avoid",
  not_a_fit: "Not a fit",
};

/** Restated from `BUSINESS_LEVEL_LABEL` in `app/services/competitor_validation.py`. */
export const BUSINESS_LEVEL_LABEL: Record<"above" | "peer" | "below", string> = {
  above: "Above this client",
  peer: "Same level",
  below: "Below this client",
};

export const VERDICT_HINT: Record<ValidationVerdict, string> = {
  implement_now: "Proven for them and close to this client's service and buyer.",
  test_first: "Close to this client, but the proof is thin. Run it as an experiment.",
  differentiate: "Most of the market does this. Match it and do it better.",
  avoid: "The client can't run it as it stands.",
  not_a_fit: "Little overlap with this client's service, keywords or buyer.",
};

/** What each column on "Plays across the market" means. The card's info icon shows this on hover. */
export const PLAY_COLUMN_HINTS: { label: string; hint: string }[] = [
  { label: "Play", hint: "The conversion tactic on the rival's page — how they convert, not who they are." },
  { label: "Used by", hint: "How many rivals in this listing run that tactic." },
  { label: "Their traffic/mo", hint: "Combined estimated Google organic visits per month for those rivals. Not leads or conversions." },
];

/** The ranking handed to the stage's prompt, limited to the competitors the operator approved.
 *
 * The server renders it for the whole listing. Narrowed to a ticked subset, the rows and bullets
 * naming a competitor that was left out are dropped, so the stage never builds on a competitor the
 * operator excluded. The market-level lines (plays, gaps) stay. "" for the operator's own list, which
 * was never scored. */
export function validationMarkdownFor(report: CompetitorValidation, approved: CompetitorRow[], picks?: string[]): string {
  const kept = new Set(approved.map((c) => c.domain.toLowerCase()));
  const scored = report.competitors.filter((c) => kept.has(c.domain.toLowerCase()));
  if (!scored.length) return "";
  let body = report.markdown;
  if (scored.length < report.competitors.length) {
    const dropped = report.competitors.filter((c) => !kept.has(c.domain.toLowerCase())).map((c) => c.name);
    body = body
      .split("\n")
      .filter((line) => !dropped.some((name) => line.startsWith(`- **${name} — `) || new RegExp(`^\\| \\d+ \\| ${escape(name)} \\|`).test(line)))
      .join("\n");
  }
  const selected = picks ? selectedPlaysMarkdown(scored, picks) : "";
  return selected ? `${body}\n\n${selected}` : body;
}

/** The plays worth applying, pre-ticked: proven for the competitor and a fit for this client, with
 * something concrete to take. The operator unticks or adds from there. */
export function defaultPicks(report: CompetitorValidation): string[] {
  return report.competitors
    .filter((c) => (c.verdict === "implement_now" || c.verdict === "test_first") && c.what_to_take)
    .map((c) => c.domain);
}

/** Whether a play can be picked at all: avoided or off-topic ones can't be run, so they have no box. */
export const isPickable = (c: ValidatedCompetitor): boolean =>
  c.verdict !== "avoid" && c.verdict !== "not_a_fit" && Boolean(c.what_to_take || c.how_to_apply);

/** The operator's picked plays, as the reference the stage builds from. Ranked as the report is
 * (best verdict first). "" when nothing is picked. */
export function selectedPlaysMarkdown(competitors: ValidatedCompetitor[], picks: string[]): string {
  const chosen = new Set(picks.map((d) => d.toLowerCase()));
  const rows = competitors.filter((c) => chosen.has(c.domain.toLowerCase()) && isPickable(c));
  if (!rows.length) return "";
  return [
    "## Plays selected by the operator — build from these",
    "These lead magnets, offers and funnel entries are the ones the operator chose to apply. Use them " +
      "as the reference for this asset, in the order listed: take the mechanism, never a competitor's " +
      "wording, and follow each play's steps for this client. Plays not listed here were not chosen.",
    "",
    ...rows.map(
      (c, i) =>
        `${i + 1}. **${c.name} — ${VERDICT_LABEL[c.verdict]}.** Brings them leads: ${c.what_to_take || "—"}` +
        ` How to apply: ${c.how_to_apply || "—"}`,
    ),
  ].join("\n");
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
