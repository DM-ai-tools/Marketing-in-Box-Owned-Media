/* The "Competitors scanned" section at the end of an asset.
 *
 * Pure — no rendering. Asserts which competitor listing a generation is paired with, that every
 * competitor's domain and page link reach the section, that the prepass prose `to_prompt_text`
 * writes is read back, and that it lands in `.md` exports only. */
import { buildAssetExport } from "../src/lib/exportAsset";
import {
  competitorAppendixFor,
  competitorSourcesFor,
  parseCompetitorProse,
  renderCompetitorSources,
} from "../src/lib/competitorSources";
import { parseAssetDocument } from "../src/lib/assetDocument";
import type { CompetitorAnalysisResult } from "../src/pipeline/pipelineApi";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const listing = (over: Partial<CompetitorAnalysisResult> = {}): CompetitorAnalysisResult => ({
  asset_id: "competitor_analysis_cro",
  target_url: "https://trafficradius.com.au",
  requested_count: 2,
  returned_count: 2,
  competitors: [
    { rank: 1, domain: "webfx.com", name: "WebFX", page_url: "https://www.webfx.com/cro/", verification_confidence: "Verified" },
    { rank: 2, domain: "disruptive.com", name: "Disruptive", page_url: null, verification_confidence: "Unverified" },
  ],
  ...over,
});

const competitorCard = (id: string, over: Partial<PipelineMessage> = {}): PipelineMessage => ({
  id,
  role: "assistant",
  kind: "competitor",
  assetId: "competitor_analysis_cro",
  phase: "phase1",
  savePhase: "saved",
  competitor: listing(),
  ...over,
});

const generation = (id: string, over: Partial<PipelineMessage> = {}): PipelineMessage => ({
  id,
  role: "assistant",
  kind: "generation",
  assetId: "cro",
  phase: "phase1",
  savePhase: "saved",
  text: "# PART 1 — Audit\n\nBody one.\n\n# PART 2 — Rewrite\n\nBody two.\n\n# PART 3 — Notes\n\nBody three.",
  ...over,
});

// --- pairing
{
  const gen = generation("g1");
  const sources = competitorSourcesFor([competitorCard("c1"), gen], gen);
  ok("paired listing found", sources?.competitors.length === 2, JSON.stringify(sources));
  ok("target carried", sources?.target === "https://trafficradius.com.au");

  const other = competitorCard("c2", { assetId: "competitor_analysis_offers" });
  ok("another stage's listing is not used", competitorSourcesFor([other, gen], gen) === null);

  const unsaved = competitorCard("c3", { savePhase: "idle" });
  ok("an unapproved listing is not used", competitorSourcesFor([unsaved, gen], gen) === null);

  const after = competitorCard("c4");
  ok("a listing after the document is not used", competitorSourcesFor([gen, after], gen) === null);

  const phase2 = competitorCard("c5", { phase: "phase2" });
  ok("the other leg's listing is not used", competitorSourcesFor([phase2, gen], gen) === null);

  const older = competitorCard("c6", {
    competitor: listing({ competitors: [{ rank: 1, domain: "old.com", name: "Old", verification_confidence: "Verified" }] }),
  });
  const latest = competitorSourcesFor([older, competitorCard("c7"), gen], gen);
  ok("the latest listing before the document wins", latest?.competitors[0].domain === "webfx.com");

  ok("a stage with no competitor pass gets nothing", competitorAppendixFor([gen], generation("g2", { assetId: "icp" })) === "");
}

// --- the prepass prose, as `to_prompt_text` writes it
{
  const prose = [
    "Competitor analysis — benchmarked against https://trafficradius.com.au",
    "",
    "1. Hootsuite Academy (hootsuite.com) — Verified",
    "   Page: https://hootsuite.com/academy",
    "   Offering: Free courses",
    "",
    "2. Later (later.com) — Partially verified",
    "   Metrics: similarity 0.8",
  ].join("\n");
  const parsed = parseCompetitorProse(prose);
  ok("prose: both entries", parsed.competitors.length === 2, JSON.stringify(parsed));
  ok("prose: page attached to its entry", parsed.competitors[0].pageUrl === "https://hootsuite.com/academy");
  ok("prose: missing page stays null", parsed.competitors[1].pageUrl === null);
  ok("prose: target read", parsed.target === "https://trafficradius.com.au");

  const gen = generation("g3", {
    assetId: "funnel",
    prepass: { assetId: "competitor_analysis_funnel", label: "Funnel competitors", status: "done", content: prose },
  });
  const sources = competitorSourcesFor([gen], gen);
  ok("prepass used when no card", sources?.competitors.length === 2 && sources.label === "Funnel competitors");
}

// --- the rendered section
{
  const gen = generation("g4");
  const section = competitorAppendixFor([competitorCard("c1"), gen], gen);
  ok("section has its heading", section.includes("# Competitors scanned"));
  ok("domain linked", section.includes("[webfx.com](https://webfx.com)"));
  ok("page linked", section.includes("[www.webfx.com/cro/](https://www.webfx.com/cro/)"));
  ok("missing page stated, not dropped", section.includes("**Disruptive**") && section.includes("no page recorded"));
  ok("empty listing renders nothing", renderCompetitorSources({ label: "x", target: null, competitors: [] }) === "");

  const doc = parseAssetDocument(gen.text! + section);
  ok(
    "reader outline gets its own section",
    doc.sections.some((s) => s.label === "Competitors scanned"),
    doc.sections.map((s) => s.label).join(" | "),
  );

  const md = buildAssetExport({ text: gen.text!, label: "CRO", appendix: section });
  ok("md export carries the section at the end", md.content.endsWith(section) && md.content.startsWith("# PART 1"));

  // Long enough to count as a page (`isPreviewableHtml` ignores anything under 200 characters).
  const html = "```html\n<!doctype html><html><body>" + "<p>Page copy that runs on.</p>".repeat(10) + "</body></html>\n```";
  const htmlExport = buildAssetExport({ text: html, label: "Pillar", appendix: section });
  ok("html export untouched", htmlExport.filename.endsWith(".html") && !htmlExport.content.includes("Competitors scanned"));
}

console.log(`sources: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exit(1);
