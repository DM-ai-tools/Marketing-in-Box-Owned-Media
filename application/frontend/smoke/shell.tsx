/* The view bar, the sample transcript, the deliverables grid and the nav pills.
 * Not shipped — see smoke/README.md.
 *
 * These four are where the second round of UI work landed, and three of them are reachable without
 * a run in progress, so they are exactly the parts a first paint can meaningfully prove. */
import { renderToStaticMarkup } from "react-dom/server";
import { DeliverablesGrid } from "../src/pipeline/DeliverablesGrid";
import { DemoTranscript } from "../src/pipeline/DemoTranscript";
import { TopNav } from "../src/pipeline/TopNav";
import { ViewBar } from "../src/pipeline/ViewBar";
import { DEMO_CRO, DEMO_PILLAR } from "../src/pipeline/demoFixtures";
import { parseAssetDocument } from "../src/lib/assetDocument";
import { summariseAsset } from "../src/lib/assetSummary";
import { usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import { useUiStore } from "../src/store/uiStore";
import { useThemeStore } from "../src/store/themeStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

/** See the note above `render()` in card.tsx for why the initial state is mutated. */
function seedUi(patch: Record<string, unknown>) {
  useUiStore.setState(patch as never);
  Object.assign(useUiStore.getInitialState(), patch);
}
function seedPipeline(patch: Record<string, unknown>) {
  usePipelineStore.setState(patch as never);
  Object.assign(usePipelineStore.getInitialState(), patch);
}

function message(over: Partial<PipelineMessage>): PipelineMessage {
  return {
    id: "m1",
    role: "assistant",
    kind: "generation",
    assetId: "cro",
    text: DEMO_CRO,
    createdAt: Date.now(),
    savePhase: "saved",
    ...over,
  };
}

/* ---------- the fixtures must exercise the real extractors ---------- */
{
  const cro = parseAssetDocument(DEMO_CRO);
  ok("fixture cro: parses into parts", cro.structured && cro.sections.length === 4, String(cro.sections.length));
  const croSummary = summariseAsset("cro", cro, DEMO_CRO);
  ok("fixture cro: audit findings counted", croSummary.chips.some((c) => c.label.includes("audit")), JSON.stringify(croSummary.chips));
  ok("fixture cro: placeholders found", croSummary.flags.some((f) => f.text.includes("to confirm")), JSON.stringify(croSummary.flags));
  ok("fixture cro: three placeholders", cro.placeholders.length === 3, String(cro.placeholders.length));

  const pillar = parseAssetDocument(DEMO_PILLAR);
  const pillarSummary = summariseAsset("pillar_page", pillar, DEMO_PILLAR);
  ok("fixture pillar: has a built page", pillarSummary.previews.length === 1, String(pillarSummary.previews.length));
  ok("fixture pillar: swatches read from prose", pillarSummary.swatches.length >= 4, JSON.stringify(pillarSummary.swatches));
  ok("fixture pillar: brand green is one of them", pillarSummary.swatches.includes("#a4d36b"), JSON.stringify(pillarSummary.swatches));
  // The fenced page's own stylesheet names greys and borders; those must not reach the palette.
  ok("fixture pillar: stylesheet colours excluded", !pillarSummary.swatches.includes("#ece7da"), JSON.stringify(pillarSummary.swatches));
  ok("fixture pillar: swatches capped", pillarSummary.swatches.length <= 8, String(pillarSummary.swatches.length));
}

/* ---------- the view bar ---------- */
{
  seedUi({ demoMode: false, hintsOn: true, docView: "outline" });
  useThemeStore.setState({ theme: "system" });
  Object.assign(useThemeStore.getInitialState(), { theme: "system" });

  const bar = renderToStaticMarkup(<ViewBar />);
  ok("view bar: names the theme", bar.includes("System"), bar.slice(0, 300));
  ok("view bar: offers the outline/full switch", bar.includes("Outline"), bar);
  ok("view bar: hints reflected as on", bar.includes("Hints on"));
  ok("view bar: offers the sample transcript", bar.includes("Sample transcript"));
  ok("view bar: does not shout DEMO when off", !bar.includes("Sample</span>"));

  seedUi({ hintsOn: false, docView: "full" });
  const bar2 = renderToStaticMarkup(<ViewBar />);
  ok("view bar: hints reflected as off", bar2.includes("Hints off"), bar2.slice(0, 200));
  ok("view bar: full document reflected", bar2.includes("Full document"));

  seedUi({ demoMode: true });
  const bar3 = renderToStaticMarkup(<ViewBar />);
  ok("view bar: sample mode warns unmistakably", bar3.includes("sample transcript") && bar3.includes("Sample"), bar3.slice(0, 400));
  ok("view bar: sample mode offers a way back", bar3.includes("Back to my transcript"));
  ok("view bar: sample mode hides the other controls", !bar3.includes("Hints"));
}

/* ---------- the sample transcript ---------- */
{
  seedUi({ demoMode: true, hintsOn: true, docView: "outline" });
  const demo = renderToStaticMarkup(<DemoTranscript />);

  ok("sample: renders", demo.length > 2000);
  ok("sample: uses the real outline", demo.includes("PART 2 — REWRITTEN PAGE"), demo.slice(0, 300));
  ok("sample: rows closed like the real ones", demo.includes('aria-expanded="false"'));
  ok("sample: the placeholder flag is real", demo.includes("to confirm"));
  ok("sample: renders a page thumbnail", demo.includes("<iframe"));
  ok("sample: swatches present", demo.includes("#a4d36b"), "expected the brand hex as a swatch title");
  ok("sample: blog blocks became sections", demo.includes("Blog 2"), demo.slice(0, 400));
  ok("sample: actions are marked inactive", demo.includes("inactive on sample data"));
  ok("sample: no live Save button", !demo.includes("Save It"));
  ok("sample: names itself as sample", demo.includes("sample documents"));
  ok("sample: hints shown when on", demo.includes("drawn by the same components"));

  seedUi({ hintsOn: false });
  const noHints = renderToStaticMarkup(<DemoTranscript />);
  ok("sample: hints gone when off", !noHints.includes("drawn by the same components"));
  ok("sample: cards still render without hints", noHints.includes("PART 2 — REWRITTEN PAGE"));
}

/* ---------- the deliverables grid ---------- */
{
  seedUi({ demoMode: false });
  seedPipeline({ phase: "phase1", messages: [] });
  const empty = renderToStaticMarkup(<DeliverablesGrid />);
  ok("deliverables: empty state explains itself", empty.includes("Nothing approved yet"), empty.slice(0, 200));

  seedPipeline({
    phase: "phase1",
    messages: [
      message({ id: "a", assetId: "cro", text: DEMO_CRO }),
      message({ id: "b", assetId: "pillar_page", text: DEMO_PILLAR }),
      message({ id: "c", assetId: "icp", text: DEMO_CRO, savePhase: undefined }),
      message({ id: "d", assetId: "blog", text: DEMO_CRO, superseded: true }),
    ],
  });
  const grid = renderToStaticMarkup(<DeliverablesGrid />);

  ok("deliverables: counts only approved, current assets", grid.includes("2 approved assets"), grid.slice(0, 260));
  ok("deliverables: names the assets", grid.includes("CRO — Page Rewrite") && grid.includes("Pillar Page Design"));
  ok("deliverables: unsaved asset excluded", !grid.includes("Ideal Customer Profile"));
  ok("deliverables: superseded asset excluded", !grid.includes("Blog Post"));
  ok("deliverables: a built page gets a thumbnail", grid.includes("<iframe"));
  ok("deliverables: carries the placeholder flag", grid.includes("to confirm"));
  ok("deliverables: total words shown", /words total/.test(grid), grid.slice(0, 300));
}

/* ---------- the nav pills ---------- */
{
  seedPipeline({
    phase: "phase1",
    clientProfile: { client_name: "Sample Dental" },
    navStatus: "Ready",
    currentIndex: 2,
    messages: [message({ id: "a" }), message({ id: "b", assetId: "icp" })],
  });
  const nav = renderToStaticMarkup(<TopNav />);
  ok("nav: client name shown", nav.includes("Sample Dental"), nav.slice(0, 500));
  ok("nav: saved counter shown", nav.includes("2/15"), nav.slice(0, 700));

  // The wrong key compiles because the profile is a loose record; an empty pill must not appear.
  seedPipeline({ clientProfile: {} });
  const bare = renderToStaticMarkup(<TopNav />);
  ok("nav: no empty client pill when unknown", !bare.includes("Client:"), bare.slice(0, 400));
  ok("nav: counter still renders", bare.includes("/15"));
}

console.log(`\n${pass} passed, ${fails.length} failed`);
if (fails.length) {
  console.log("\nFAILURES:");
  fails.forEach((f) => console.log("  x " + f));
  process.exitCode = 1;
}
