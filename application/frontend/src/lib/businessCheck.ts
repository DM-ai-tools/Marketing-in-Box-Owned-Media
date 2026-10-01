import type { AssetCheckReport, CheckFinding, CheckName, CheckResult } from "../pipeline/pipelineApi";

/** The "Business check" section of an asset: what the draft was checked against, how it scored, and
 * what was fixed on the way to the version being delivered.
 *
 * The panel above Approve (`pipeline/AssetCheckPanel.tsx`) is where the check is acted on; this is
 * the record of it. Same contract as `competitorSources.ts` and `topicSuggestions.ts`: shown in the
 * reader and in the downloaded Markdown and HTML report, never written into `message.text` — the
 * saved text is what the next stage reads out of the Context Store, and a score table is not
 * something a later prompt should treat as part of the asset.
 *
 * Pure — no store, no request. The caller supplies the report on the delivered draft and, when the
 * draft came out of a Refine, the report on the draft it was refined from (`assetAppendix.ts`).
 *
 * Nothing here is invented: every number is a count or a score already in the report, and "fixed"
 * means a finding the earlier draft had that this one no longer has. */

export const BUSINESS_CHECK_HEADING = "# Business check";
export const BUSINESS_CHECK_LABEL = "Business check";

export function isBusinessCheckHeading(line: string): boolean {
  return line.trim() === BUSINESS_CHECK_HEADING;
}

/** Stages that get no business check in a phase. Phase 2's ICP is built for one sub-service's buyer
 * (`Phase2/ICP_phase2.md`) and there is no business-side document yet to hold it against, so the
 * check had nothing real to compare it with. Keep in step with `asset_check.NO_CHECK`. */
const NO_CHECK: ReadonlySet<string> = new Set(["phase2:icp"]);

/** Whether a draft of `assetId` is checked at all. An unstamped card predates per-phase stamping
 * and belongs to a Phase 1 chat. One rule for the three places that need it: running the check,
 * showing its panel, and writing it into the deliverable. */
export function checkAppliesTo(phase: string | undefined, assetId: string | undefined): boolean {
  return !!assetId && !NO_CHECK.has(`${phase ?? "phase1"}:${assetId}`);
}

// The names, questions, bands and severity words below mirror `pipeline/AssetCheckPanel.tsx`, so
// the file a client receives uses the same words as the card the operator approved it from. That
// file exports only a component (Fast Refresh), so they are restated here rather than imported;
// `smoke/businessCheck.tsx` pins the values that matter.

export const CHECK_LABEL: Record<CheckName, string> = {
  offer_relevance: "Offers",
  funnel_relevance: "Funnel",
  business_logic: "Business rules",
  virality: "Virality",
};

/** What each score measures — a number with no question behind it is a number nobody can act on. */
export const CHECK_QUESTION: Record<CheckName, string> = {
  offer_relevance: "Does it sell only what's on the client's offer ladder, at prices on record?",
  funnel_relevance: "Does it lead the buyer to the next real step on the ladder, matched to where they are?",
  business_logic: "Are its claims, prices, testimonials and wording allowed for this client?",
  virality: "How likely it is to be shared: hook, curiosity, specificity, emotion, platform fit.",
};

export type CheckBand = "Good" | "Needs work" | "At risk" | "Not scored";

/** A check's band: a must-fix finding always makes it at risk, whatever the score says. */
export function bandOf(result: CheckResult): CheckBand {
  if (result.findings.some((f) => f.severity === "error")) return "At risk";
  if (result.score === null) return result.findings.some((f) => f.severity === "warn") ? "Needs work" : "Not scored";
  if (result.score >= 80) return "Good";
  if (result.score >= 60) return "Needs work";
  return "At risk";
}

const SEVERITY_WORD = { error: "Must fix", warn: "Should fix", info: "Note" } as const;

const squash = (text: string) => text.replace(/\s+/g, " ").trim();

/** What identifies a finding across two drafts: its check and the words it quotes. A finding about
 * the whole draft has no quote, so its reason stands in. */
function identity(f: CheckFinding): string {
  return `${f.check}|${squash(f.quote || f.why).toLowerCase()}`;
}

/** Findings the earlier draft had that this one no longer does, for the checks this report ran. */
export function fixedFindings(report: AssetCheckReport, earlier: AssetCheckReport | undefined): CheckFinding[] {
  if (!earlier) return [];
  const now = new Set(report.checks.flatMap((c) => c.findings.map(identity)));
  const ran = new Set(report.checks.map((c) => c.check));
  return earlier.checks
    .filter((c) => ran.has(c.check))
    .flatMap((c) => c.findings)
    .filter((f) => !now.has(identity(f)));
}

const count = (findings: CheckFinding[], severity: CheckFinding["severity"]) =>
  findings.filter((f) => f.severity === severity).length;

function scoreText(score: number | null): string {
  return score === null ? "not scored" : `${score}/100`;
}

function changeText(now: number | null, before: number | null | undefined): string {
  if (now === null || before === null || before === undefined) return "—";
  const d = now - before;
  return `${before} → ${now} (${d > 0 ? "+" : ""}${d})`;
}

function findingLine(f: CheckFinding): string {
  const quote = f.quote ? `“${squash(f.quote)}” — ` : "";
  const fix = f.fix ? ` Fix: ${squash(f.fix)}` : "";
  return `${SEVERITY_WORD[f.severity]}: ${quote}${squash(f.why)}${fix}`;
}

function checkBlock(
  result: CheckResult,
  report: AssetCheckReport,
  earlier: CheckResult | undefined,
  fixed: CheckFinding[],
  hasEarlier: boolean,
  approved: boolean,
): string[] {
  const lines = [`**${CHECK_LABEL[result.check] ?? result.check} — ${scoreText(result.score)} · ${bandOf(result)}**`];
  lines.push(`- What it checks: ${CHECK_QUESTION[result.check] ?? result.check}`);
  if (result.verdict) lines.push(`- Verdict: ${squash(result.verdict)}`);
  if (hasEarlier && earlier && result.score !== null && earlier.score !== null) {
    const d = result.score - earlier.score;
    lines.push(`- Since the earlier draft: was ${earlier.score}, now ${result.score}${d === 0 ? ", unchanged" : ` (${d > 0 ? "+" : ""}${d})`}`);
  }
  const notes = count(result.findings, "info");
  lines.push(
    `- Findings: ${count(result.findings, "error")} must fix, ${count(result.findings, "warn")} should fix, ${notes} ${notes === 1 ? "note" : "notes"}`,
  );
  if (result.check === "virality") {
    lines.push(
      `- Basis: a prediction, not a measurement. ${
        report.virality_basis === "benchmarked"
          ? "Compared against this client's and competitors' real post engagement and keyword demand."
          : "Rubric only: no real engagement data was on record for this run."
      }`,
    );
  }
  if (hasEarlier) {
    const mine = fixed.filter((f) => f.check === result.check);
    lines.push(`- Fixed since the earlier draft: ${mine.length}`);
    for (const f of mine) lines.push(`  - ${findingLine(f)}`);
  }
  // An approved deliverable carries the counts above but not the working list of what was left
  // open: that list is for whoever is still fixing the draft, not for the file that goes out.
  if (!approved && result.findings.length) {
    lines.push("- Still open:");
    for (const f of result.findings) lines.push(`  - ${findingLine(f)}`);
  }
  return lines;
}

/** The whole section, or "" when there is no finished report to show. Placed at the end of the
 * document, after the evidence it was written from. */
export function renderBusinessCheck(
  report: AssetCheckReport | null | undefined,
  opts: { earlier?: AssetCheckReport; approved: boolean },
): string {
  if (!report?.checks.length) return "";
  const earlier = opts.earlier;
  const hasEarlier = !!earlier;
  const fixed = fixedFindings(report, earlier);
  const all = report.checks.flatMap((c) => c.findings);
  const open = all.length;

  const intro = [
    opts.approved
      ? "The approved version of this asset was checked against the client's real offers, funnel and business rules."
      : "This draft was checked against the client's real offers, funnel and business rules. It has not been approved yet, so the result can change after a Refine.",
    // What was left open is a working list for the draft, so an approved file does not state it.
    hasEarlier
      ? `It was re-checked after a Refine: ${fixed.length} earlier ${fixed.length === 1 ? "finding is" : "findings are"} fixed${
          opts.approved ? "." : ` and ${open} ${open === 1 ? "remains" : "remain"} open.`
        }`
      : opts.approved
        ? ""
        : `${open} ${open === 1 ? "finding is" : "findings are"} open.`,
    "The check is advisory: it never blocked approval.",
  ]
    .filter(Boolean)
    .join(" ");

  const head = ["Check", "Score", "Band", ...(hasEarlier ? ["Since earlier draft"] : []), "Must fix", "Should fix", "Notes", ...(hasEarlier ? ["Fixed"] : [])];
  const rows = report.checks.map((c) => {
    const before = earlier?.checks.find((e) => e.check === c.check);
    return [
      CHECK_LABEL[c.check] ?? c.check,
      scoreText(c.score),
      bandOf(c),
      ...(hasEarlier ? [changeText(c.score, before?.score)] : []),
      String(count(c.findings, "error")),
      String(count(c.findings, "warn")),
      String(count(c.findings, "info")),
      ...(hasEarlier ? [String(fixed.filter((f) => f.check === c.check).length)] : []),
    ];
  });
  const table = [head, head.map(() => "---"), ...rows].map((r) => `| ${r.join(" | ")} |`);

  const blocks = report.checks.flatMap((c) => [
    "",
    ...checkBlock(c, report, earlier?.checks.find((e) => e.check === c.check), fixed, hasEarlier, opts.approved),
  ]);

  const notes: string[] = [];
  if (report.facts_missing.length) notes.push(`Not on record, so not checked: ${report.facts_missing.join(", ")}.`);
  if (report.judge === "unavailable") notes.push("Only the rule checks ran; the review step was unavailable.");
  if (report.dropped_quotes > 0) {
    notes.push(
      `${report.dropped_quotes} ${report.dropped_quotes === 1 ? "finding" : "findings"} the reviewer could not quote from the draft ${report.dropped_quotes === 1 ? "was" : "were"} discarded.`,
    );
  }

  return ["", "---", "", BUSINESS_CHECK_HEADING, "", intro, "", ...table, ...blocks, ...(notes.length ? ["", ...notes] : []), ""].join("\n");
}
