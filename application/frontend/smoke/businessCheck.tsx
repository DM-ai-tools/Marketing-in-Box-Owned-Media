/* The "Business check" section at the end of an asset's deliverable.
 *
 * Pure — no store actions, no fetch. Asserts that the finished check reaches the reader and both
 * exports with its metrics and descriptions, that "fixed" is only ever a finding the earlier draft
 * had and this one lacks, that it never turns into the asset's own score, and that the Markdown and
 * HTML-report exports carry it while a standalone HTML page stays untouched. */
import { assetAppendixFor, businessCheckAppendixFor } from "../src/lib/assetAppendix";
import { parseAssetDocument } from "../src/lib/assetDocument";
import { assetGlance } from "../src/lib/assetGlance";
import { bandOf, checkAppliesTo, fixedFindings, renderBusinessCheck } from "../src/lib/businessCheck";
import { buildAssetReportHtml } from "../src/lib/assetReportHtml";
import { buildAssetExport } from "../src/lib/exportAsset";
import type { AssetCheckReport, CheckFinding, CheckResult } from "../src/pipeline/pipelineApi";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const finding = (over: Partial<CheckFinding> = {}): CheckFinding => ({
  check: "business_logic",
  severity: "error",
  quote: "Guaranteed results in 30 days.",
  why: "An absolute claim; this client's claim tier is regulated.",
  fix: "Qualify it.",
  source: "rule",
  ...over,
});

const result = (over: Partial<CheckResult> = {}): CheckResult => ({
  check: "business_logic",
  score: 58,
  verdict: "One forbidden claim.",
  findings: [finding()],
  ...over,
});

const report = (checks: CheckResult[], over: Partial<AssetCheckReport> = {}): AssetCheckReport => ({
  asset_id: "lead_magnet",
  checks,
  facts_used: [],
  facts_missing: [],
  judge: "ok",
  dropped_quotes: 0,
  virality_basis: "rubric only",
  duration_ms: 1000,
  ...over,
});

// The draft Refine started from: a must-fix claim, a price off the ladder, and a placeholder note.
const BEFORE = report([
  result(),
  result({
    check: "offer_relevance",
    score: 61,
    verdict: "One price is not on the ladder.",
    findings: [finding({ check: "offer_relevance", severity: "warn", quote: "Only $99 a month.", why: "$99 is not on the ladder.", fix: "Use a ladder price." })],
  }),
]);
// The delivered draft: the claim and the price are fixed, one note is new.
const AFTER = report(
  [
    result({ score: 92, verdict: "Nothing material.", findings: [finding({ severity: "info", quote: "[PLACEHOLDER — phone]", why: "A placeholder left for the client.", fix: "Fill it in." })] }),
    result({ check: "offer_relevance", score: 88, verdict: "On-ladder.", findings: [] }),
    result({ check: "virality", score: 71, verdict: "Strong hook, weak shareability.", findings: [] }),
  ],
  { facts_missing: ["the funnel"], virality_basis: "rubric only" },
);

const generation = (id: string, over: Partial<PipelineMessage> = {}): PipelineMessage => ({
  id,
  role: "assistant",
  kind: "generation",
  assetId: "lead_magnet",
  phase: "phase1",
  savePhase: "saved",
  text: "# PART 1 — Concept\n\nBody one.\n\n# PART 2 — Copy\n\nBody two.\n\n# PART 3 — Notes\n\nBody three.",
  ...over,
});

// --- bands: the panel's rule, not a score cut alone
{
  ok("band: 92 is good", bandOf(result({ score: 92, findings: [] })) === "Good");
  ok("band: 70 needs work", bandOf(result({ score: 70, findings: [] })) === "Needs work");
  ok("band: 40 is at risk", bandOf(result({ score: 40, findings: [] })) === "At risk");
  ok("band: a must-fix finding is at risk whatever the score", bandOf(result({ score: 95 })) === "At risk");
  ok("band: unscored with a should-fix needs work", bandOf(result({ score: null, findings: [finding({ severity: "warn" })] })) === "Needs work");
  ok("band: unscored and clean is not scored", bandOf(result({ score: null, findings: [] })) === "Not scored");
}

// --- stages that are not checked in a phase
{
  ok("phase 2 ICP is not checked", !checkAppliesTo("phase2", "icp"));
  ok("phase 1 ICP is checked", checkAppliesTo("phase1", "icp"));
  ok("an unstamped card is a phase 1 card", checkAppliesTo(undefined, "icp"));
  ok("other phase 2 stages are checked", checkAppliesTo("phase2", "cro") && checkAppliesTo("phase2", "blog"));
  ok("no asset, no check", !checkAppliesTo("phase1", undefined));

  // A report already stored on a phase 2 ICP card (from before this rule) must not reach the file.
  const stale = generation("g0", { assetId: "icp", phase: "phase2", check: { status: "done", report: AFTER } });
  ok("a stored phase 2 ICP report adds nothing to the deliverable", businessCheckAppendixFor([stale], stale) === "" && assetAppendixFor([stale], stale) === "");
  const phase1 = generation("g00", { assetId: "icp", phase: "phase1", check: { status: "done", report: AFTER } });
  ok("the same report on a phase 1 ICP still does", businessCheckAppendixFor([phase1], phase1).includes("# Business check"));
}

// --- nothing to show
{
  ok("no report renders nothing", renderBusinessCheck(null, { approved: true }) === "");
  ok("a report with no checks renders nothing", renderBusinessCheck(report([]), { approved: true }) === "");
}

// --- the section, first check (no earlier draft)
{
  const section = renderBusinessCheck(AFTER, { approved: true });
  ok("heading", section.includes("\n# Business check\n"));
  ok("separated from the asset", section.startsWith("\n---\n"));
  ok("approved wording", section.includes("The approved version of this asset was checked"));
  ok("advisory stated", section.includes("never blocked approval"));
  ok("score table row with a metric", /\| Offers \| 88\/100 \| Good \|/.test(section), section);
  ok("every check present", ["Offers", "Business rules", "Virality"].every((l) => section.includes(`**${l} —`)));
  ok("description of what each check asks", section.includes("Does it sell only what's on the client's offer ladder"));
  ok("verdict carried", section.includes("Verdict: Strong hook, weak shareability."));
  ok("finding counts", section.includes("0 must fix, 0 should fix, 1 note"));
  ok("approved: the count of open findings stays", section.includes("0 must fix, 0 should fix, 1 note"));
  ok("approved: no 'Still open' list", !section.includes("Still open") && !section.includes("PLACEHOLDER"), section);
  ok("virality is a prediction with its basis", section.includes("a prediction, not a measurement") && section.includes("Rubric only"));
  ok("facts that were not on record are named", section.includes("Not on record, so not checked: the funnel."));
  ok("no earlier draft: no 'fixed' claim", !section.includes("Fixed since") && !section.includes("| Fixed |"));
}

// --- re-checked after a Refine
{
  const fixed = fixedFindings(AFTER, BEFORE);
  ok("fixed: the claim and the price", fixed.length === 2, JSON.stringify(fixed.map((f) => f.quote)));
  ok("fixed: the new note is not counted as fixed", !fixed.some((f) => f.quote.includes("PLACEHOLDER")));
  ok("fixed: a finding still present is not fixed", fixedFindings(BEFORE, BEFORE).length === 0);
  ok("fixed: nothing without an earlier draft", fixedFindings(AFTER, undefined).length === 0);
  // Same claim, different spacing and case: still the same finding, so not fixed.
  const reworded = report([result({ findings: [finding({ quote: "  guaranteed   RESULTS in 30 days. " })] })]);
  ok("fixed: matched on the quote, not on spacing or case", fixedFindings(reworded, BEFORE).length === 0);
  // BEFORE also had an offers finding, but this report did not run that check, so it is neither
  // fixed nor open: it was not looked at.
  ok("fixed: a check this report did not run is not counted", !fixedFindings(reworded, BEFORE).some((f) => f.check === "offer_relevance"));

  const section = renderBusinessCheck(AFTER, { earlier: BEFORE, approved: true });
  ok("change column", /\| Business rules \| 92\/100 \| Good \| 58 → 92 \(\+34\) \|/.test(section), section);
  ok("fixed column", /\| Offers \| 88\/100 \| Good \| 61 → 88 \(\+27\) \| 0 \| 0 \| 0 \| 1 \|/.test(section), section);
  ok("a check with no earlier score has no change", /\| Virality \| 71\/100 \| Needs work \| — \|/.test(section), section);
  ok("re-check sentence, without what is left open", section.includes("2 earlier findings are fixed.") && !section.includes("remains open"), section);
  ok("draft re-check sentence states what is left open", renderBusinessCheck(AFTER, { earlier: BEFORE, approved: false }).includes("2 earlier findings are fixed and 1 remains open"));
  ok("fixed items listed with their words", section.includes("Fixed since the earlier draft: 1") && section.includes("“Only $99 a month.”"));
  ok("since the earlier draft, per check", section.includes("Since the earlier draft: was 58, now 92 (+34)"));
}

// --- an unapproved draft says so
{
  const section = renderBusinessCheck(BEFORE, { approved: false });
  ok("draft wording", section.includes("has not been approved yet"));
  ok("draft: must-fix is stated, not hidden", section.includes("1 must fix") && section.includes("Still open:") && section.includes("Must fix: “Guaranteed results in 30 days.”"));
}

// --- dropped quotes and an unavailable reviewer are reported
{
  const section = renderBusinessCheck(report([result({ findings: [] })], { judge: "unavailable", dropped_quotes: 2 }), { approved: true });
  ok("reviewer unavailable stated", section.includes("Only the rule checks ran"));
  ok("dropped quotes stated", section.includes("2 findings the reviewer could not quote from the draft were discarded."));
}

// --- from the transcript
{
  const earlierDraft = generation("g1", { refineSubmitted: true, savePhase: "idle", check: { status: "done", report: BEFORE } });
  const delivered = generation("g2", { check: { status: "done", report: AFTER } });
  const section = businessCheckAppendixFor([earlierDraft, delivered], delivered);
  ok("appendix reads the earlier draft's report", section.includes("58 → 92 (+34)"), section);

  const notRefined = generation("g3", { savePhase: "idle", check: { status: "done", report: BEFORE } });
  const other = generation("g4", { check: { status: "done", report: AFTER } });
  ok("a draft that was not refined from has no earlier report", !businessCheckAppendixFor([notRefined, other], other).includes("→"));

  ok("running check renders nothing", businessCheckAppendixFor([], generation("g5", { check: { status: "running" } })) === "");
  ok("failed check renders nothing", businessCheckAppendixFor([], generation("g6", { check: { status: "error", error: "x" } })) === "");
  ok("no check renders nothing", assetAppendixFor([], generation("g7")) === "");
  ok("a card that is not a generation renders nothing", businessCheckAppendixFor([], { ...delivered, kind: "text" }) === "");
  ok("undefined renders nothing", assetAppendixFor([], undefined) === "");
}

// --- in the reader's document
{
  const gen = generation("g8", { check: { status: "done", report: AFTER } });
  const section = businessCheckAppendixFor([gen], gen);
  const doc = parseAssetDocument(gen.text! + section);
  const labels = doc.sections.map((s) => s.label);
  ok("its own section in the outline", labels.includes("Business check"), labels.join(" | "));
  ok("the asset's sections are unchanged", labels.filter((l) => l !== "Business check").join("|") === parseAssetDocument(gen.text).sections.map((s) => s.label).join("|"));
  ok("it is last", labels[labels.length - 1] === "Business check");

  // No score of its own in the lead magnet text above: the glance must not borrow the check's.
  const glance = assetGlance("lead_magnet", doc);
  const checkId = doc.sections.find((s) => s.label === "Business check")!.id;
  ok("the glance never points at the check", !glance || (glance.kind === "score" ? glance.sectionId !== checkId : true), JSON.stringify(glance));
  ok("no score glance is invented from the check", !glance || glance.kind !== "score");
}

// --- the exports
{
  const gen = generation("g9", { check: { status: "done", report: AFTER } });
  const section = businessCheckAppendixFor([gen], gen);

  const md = buildAssetExport({ text: gen.text!, label: "Lead magnet", appendix: section });
  ok("markdown export ends with the section", md.content.endsWith(section) && md.content.startsWith("# PART 1"));
  ok("saved text is untouched", !gen.text!.includes("Business check"));

  const html = "```html\n<!doctype html><html><body>" + "<p>Page copy that runs on.</p>".repeat(10) + "</body></html>\n```";
  const htmlExport = buildAssetExport({ text: html, label: "Pillar", appendix: section });
  ok("a standalone html page is untouched", htmlExport.filename.endsWith(".html") && !htmlExport.content.includes("Business check"));
}

// --- the HTML report carries every word of it
{
  const gen = generation("g10", { check: { status: "done", report: AFTER } });
  const earlierDraft = generation("g11", { refineSubmitted: true, savePhase: "idle", check: { status: "done", report: BEFORE } });
  const section = businessCheckAppendixFor([earlierDraft, gen], gen);
  const out = await buildAssetReportHtml({ text: gen.text!, label: "Lead magnet", assetId: "lead_magnet", appendix: section, css: "", date: "1 January 2026" });
  const text = out.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&#39;/g, "'");
  const words = (s: string) => s.toLowerCase().match(/[a-z0-9$]+/g) ?? [];
  const have = new Set(words(text));
  const missing = [...new Set(words(section))].filter((w) => !have.has(w));
  ok("report holds the Business check heading", out.includes("Business check"));
  ok("report loses no word of the section", missing.length === 0, missing.slice(0, 12).join(", "));
}

console.log(`businessCheck: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exit(1);
