/* The Deliverables reader's Visual view. Not shipped; see smoke/README.md.
 *
 * The promise is that visuals are a re-layout of the document, never a summary of it. Two checks
 * hold it, both run over every real output in `manual_execution/` rather than over fixtures written
 * to match the parser:
 *
 *   - partition: a section's blocks, their sources rejoined, are the section body byte for byte;
 *   - containment: every word of a recognised block's source is in what that block renders (the
 *     word-multiset check `plan.tsx` uses for the mind map).
 *
 * Then: the structure is actually recognised on the real samples, each table rule has a positive
 * and a negative case, the reader opens in Visual and its Text view is the old markup, and the
 * Deliverables cards carry the glance.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { parseAssetDocument } from "../src/lib/assetDocument";
import { assetGlance } from "../src/lib/assetGlance";
import { classifyTable, joinSources, parseTable, parseVisualBlocks, type VisualBlock } from "../src/lib/visualBlocks";
import { AssetReader } from "../src/pipeline/AssetReader";
import { DeliverablesGrid } from "../src/pipeline/DeliverablesGrid";
import { usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import { VisualBlockView } from "../src/pipeline/visual/VisualBlocks";
import { useUiStore } from "../src/store/uiStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const SAMPLES = join(process.cwd(), "..", "..", "manual_execution");
const sample = (name: string) => readFileSync(join(SAMPLES, name), "utf8");
const blocksOf = (text: string): VisualBlock[] => parseAssetDocument(text).sections.flatMap((s) => parseVisualBlocks(s.body));

// ---------- the word check (same rules as plan.tsx) ----------
const tokens = (s: string) =>
  s
    .toLowerCase()
    .replace(/[*#|>`_~]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .filter((w) => !/^\d{1,2}[.)]$/.test(w) && !/^[a-h][.)]$/.test(w))
    .map((w) => w.replace(/^[^a-z0-9~$]+|[^a-z0-9%)]+$/g, ""))
    .filter((w) => /[a-z0-9]/.test(w));
const tally = (ws: string[]) => {
  const m = new Map<string, number>();
  for (const w of ws) m.set(w, (m.get(w) ?? 0) + 1);
  return m;
};
const visibleText = (html: string) =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&nbsp;/g, " ");

/* ---------- partition and containment, over every real output ---------- */
if (!existsSync(SAMPLES)) {
  fails.push(`samples not found at ${SAMPLES}`);
} else {
  const files = readdirSync(SAMPLES).filter((f) => f.endsWith(".md"));
  let sections = 0;
  let visuals = 0;
  const broken: string[] = [];
  const lossy: string[] = [];
  for (const f of files) {
    for (const s of parseAssetDocument(sample(f)).sections) {
      sections++;
      const blocks = parseVisualBlocks(s.body);
      if (joinSources(blocks) !== s.body) broken.push(`${f} › ${s.label}`);
      for (const b of blocks) {
        if (b.kind === "markdown") continue;
        visuals++;
        const out = tally(tokens(visibleText(renderToStaticMarkup(<VisualBlockView block={b} />))));
        const missing = [...tally(tokens(b.source))].filter(([w, n]) => (out.get(w) ?? 0) < n).map(([w]) => w);
        if (missing.length) lossy.push(`${f} › ${s.label} › ${b.kind}: ${missing.slice(0, 8).join(" ")}`);
      }
    }
  }
  ok(`partition holds for all ${sections} sections of ${files.length} real outputs`, !broken.length, broken.slice(0, 5).join(" | "));
  ok(`all ${visuals} visual blocks render every word of their source`, !lossy.length, lossy.slice(0, 5).join(" | "));
  ok("the corpus is large enough to mean something", files.length >= 20 && visuals >= 100, `${files.length} files, ${visuals} visuals`);
}

/* ---------- recognition on the real samples ---------- */
{
  const cro = blocksOf(sample("PART-1_CRO-Audit-Report_Social-Media-Marketing.md"));
  const gauge = cro.find((b) => b.kind === "gauge");
  ok("CRO: the page score is a gauge", gauge?.kind === "gauge" && gauge.value === 58 && gauge.max === 100);
  const glance = assetGlance("cro", parseAssetDocument(sample("PART-1_CRO-Audit-Report_Social-Media-Marketing.md")));
  ok("CRO: glance is the score with layer bars", glance?.kind === "score" && glance.bars.length >= 5, JSON.stringify(glance)?.slice(0, 200));

  const ladderDoc = parseAssetDocument(sample("Value-Ladder_TrafficRadius-SMM_All-Wishes.md"));
  const offers = ladderDoc.sections.flatMap((s) => parseVisualBlocks(s.body)).flatMap((b) => (b.kind === "offers" ? b.offers : []));
  ok("Offers: every ladder item becomes a card", offers.length >= 50, String(offers.length));
  ok("Offers: a card keeps its price and next step", offers.some((o) => o.title === "The Partner-Voice Content Capture Kit" && o.fields.some((f) => f.key === "Price" && f.value.startsWith("$297")) && o.fields.some((f) => f.key === "Ascends To")));
  const ladder = assetGlance("offers", ladderDoc);
  ok("Offers: glance is the rungs", ladder?.kind === "ladder" && ladder.rungs.length >= 5, String(ladder?.kind));

  const sms = blocksOf(sample("SMS-Sequence_TrafficRadius-LeadMagnet.md"));
  ok("SMS: the messages are a sequence", sms.some((b) => b.kind === "sequence" && b.steps.length === 5 && b.steps[0].timing.startsWith("Immediate")));
  ok("SMS: the summary table is a timeline", sms.some((b) => b.kind === "table" && b.visual === "timeline"));

  const magnet = assetGlance("lead_magnet", parseAssetDocument(sample("Lead-Magnet-Brief_TrafficRadius-SMM.md")));
  ok("Lead magnet: glance names the winning concept", magnet?.kind === "winner" && magnet.name.startsWith("A. The Four Causes Scorecard") && magnet.value === 24 && magnet.max === 25, JSON.stringify(magnet));

  const funnel = blocksOf(sample("Funnel-Document_Social-Media-Marketing.md"));
  ok("Funnel: the stage table is a flow", funnel.some((b) => b.kind === "table" && b.visual === "flow"));
  ok("Funnel: the trigger map is a heat table", funnel.some((b) => b.kind === "table" && b.visual === "score" && /Dopamine/.test(b.table.headers.join(" "))));
  ok("Funnel: the P0-P3 list is a kanban", funnel.some((b) => b.kind === "table" && b.visual === "kanban"));
  ok("Funnel: the email run is a sequence", funnel.some((b) => b.kind === "sequence"));

  const webinar = blocksOf(sample("Webinar-Package_TrafficRadius_LLM-Only.md"));
  const show = webinar.find((b) => b.kind === "runOfShow");
  ok("Webinar: the run of show is timed", show?.kind === "runOfShow" && show.slots.length === 8 && show.slots[3].minutes === 33, JSON.stringify(show?.kind === "runOfShow" ? show.slots[3] : null));

  const icp = blocksOf(sample("ICP-TrafficRadius-ProblemAware-Melbourne-ProfessionalServices.md"));
  ok("ICP: objections and opportunities are cards", icp.filter((b) => b.kind === "cards").length >= 3);
}

/* ---------- table rules, positive and negative ---------- */
{
  const t = (md: string) => classifyTable(parseTable(md.trim().split("\n"))!).visual;
  ok("score: N/10 cells", t("| Layer | Score |\n|---|---|\n| A | 4/10 |\n| B | 8/10 |") === "score");
  ok("score: HIGH/MED/LOW cells", t("| Stage | D | O |\n|---|---|---|\n| 1 | HIGH | LOW |\n| 2 | MED | HIGH |") === "score");
  ok("score: a /25 rubric", t("| Candidate | Fit | Brand | Total /25 |\n|---|---|---|---|\n| A | 5 | 4 | 24 |\n| B | 3 | 2 | 15 |") === "score");
  ok("not score: prose cells", t("| Item | Note |\n|---|---|\n| A | fine |\n| B | good |") === "data");
  ok("timeline: a Timing column", t("| SMS | Timing | Purpose |\n|---|---|---|\n| 1 | Now | a |\n| 2 | +1 day | b |\n| 3 | +3 days | c |") === "timeline");
  ok("not timeline: two rows", t("| SMS | Timing |\n|---|---|\n| 1 | Now |\n| 2 | +1 day |") === "data");
  ok("flow: Stage + goals", t("| Stage | Primary goal |\n|---|---|\n| One | a |\n| Two | b |\n| Three | c |") === "flow");
  ok("not flow: Stage alone", t("| Stage | Owner |\n|---|---|\n| One | a |\n| Two | b |\n| Three | c |") === "data");
  ok("kanban: P0-P3 beats a Timeline column", t("| Priority | Task | Timeline |\n|---|---|---|\n| **P0** | a | Week 1 |\n| P1 | b | Week 2 |\n| P2 | c | Week 3 |") === "kanban");
  ok("not kanban: free-text priority", t("| Priority | Task |\n|---|---|\n| urgent | a |\n| later | b |") === "data");
  ok("kpi: Metric + Target", t("| Metric | Target |\n|---|---|\n| Open rate | 40% |\n| CTR | 3% |") !== "data");
  ok("not kpi: Metric + Notes", t("| Metric | Notes |\n|---|---|\n| Open rate | fine |\n| CTR | ok |") === "data");
}

/* ---------- the reader and the grid ---------- */
{
  const cro = sample("PART-1_CRO-Audit-Report_Social-Media-Marketing.md");
  const message: PipelineMessage = {
    id: "g1",
    role: "assistant",
    kind: "generation",
    assetId: "cro",
    text: cro,
    savePhase: "saved",
    phase: "phase1",
    createdAt: 1,
  };
  const state = { started: true, phase: "phase1" as const, messages: [message], activePhase2TrackId: null };
  usePipelineStore.setState(state as never);
  Object.assign(usePipelineStore.getInitialState(), state);

  const render = (view: "visual" | "text") => {
    const ui = { readerMessageId: "g1", readerView: view, workTab: "deliverables" };
    useUiStore.setState(ui as never);
    Object.assign(useUiStore.getInitialState(), ui);
    return renderToStaticMarkup(<AssetReader />);
  };

  ok("the reader defaults to Visual", useUiStore.getInitialState().readerView === "visual");
  const visual = render("visual");
  ok("Visual: the toggle is offered, Visual pressed", /aria-pressed="true"[^>]*>Visual</.test(visual));
  ok("Visual: the glance leads", visual.includes("At a glance") && visual.includes("58/100"));
  ok("Visual: every section is still there", parseAssetDocument(cro).sections.every((s) => visual.includes(`id="${s.id}"`)));
  ok("Visual: export still offered", visual.includes("Download"));

  const text = render("text");
  ok("Text: no glance", !text.includes("At a glance"));
  ok("Text: the document as markdown", text.includes("md-scroll") && text.includes("doc-measure"));

  const grid = renderToStaticMarkup(<DeliverablesGrid />);
  ok("Grid: the card carries the glance", grid.includes("Score 58/100"), grid.slice(0, 300));
  ok("Grid: the card still says words", grid.includes("words"));
}

console.log(`visual: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
