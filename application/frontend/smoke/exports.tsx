/* Exports that are files rather than the document: the plan map, the webinar's slide decks, and
 * CRO's assembled page. Not shipped; see smoke/README.md.
 *
 *   - the plan map and the deck are offered on the generation card's menu and in the reader, by the
 *     one shared component, and only when the file can actually be built;
 *   - a webinar with several decks lists each deck (by its own index) plus one "all" row (no index,
 *     so the server's .zip); one deck is a single .pptx row; no parsable brief, no row;
 *   - the replica events reach the card: a report with a download once an assembly exists, and a
 *     `replica_error` that leaves the stage complete rather than failed;
 *   - image-generation failures arrive as their own event and show on the card.
 *
 * Server rendering never runs effects, so the deck probe is primed through its cache with a stubbed
 * fetch first — the same probe the component runs on mount.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { AssetFileDownloads } from "../src/components/AssetFileDownloads";
import { deckRows, probeSlideDecks } from "../src/components/SlideDeckMenuItem";
import { buildPlanMindMapHtml } from "../src/lib/planMindMapHtml";
import { AssetReader } from "../src/pipeline/AssetReader";
import { GenerationStream } from "../src/pipeline/GenerationStream";
import { fetchSlideDeck, streamGenerateStage, streamRefineStage, type ReplicaStreamEvent, type RevisionEditReport, type SlideDecksResponse } from "../src/pipeline/pipelineApi";
import { nextReplicaState, usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import { ImageNotes, ReplicaPanel, RevisionNotes } from "../src/pipeline/ReplicaPanel";
import { useUiStore } from "../src/store/uiStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const SAMPLES = join(process.cwd(), "..", "..", "manual_execution");
const PLAN = readFileSync(join(SAMPLES, "Plan-of-Action_TrafficRadius-SMM.md"), "utf8");
const WEBINAR = readFileSync(join(SAMPLES, "Webinar-Package_TrafficRadius_CompetitorSynthesis.md"), "utf8");
const RUN_ID = "11111111-2222-4333-8444-555555555555";

/* ---------- a stubbed backend: the deck probe, the deck itself, and a generation stream ---------- */
const TWO_DECKS: SlideDecksResponse = {
  decks: [
    { index: 0, title: "Why Social Isn't Working", slide_count: 14, filename: "why-social-isnt-working.pptx" },
    { index: 1, title: "The Partner Voice Playbook", slide_count: 11, filename: "the-partner-voice-playbook.pptx" },
  ],
  bundle_filename: "webinar-slide-decks.zip",
};
const ONE_DECK: SlideDecksResponse = { decks: [TWO_DECKS.decks[0]], bundle_filename: "webinar-slide-decks.zip" };

const deckUrls: string[] = [];
let sseBody = "";
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/pipeline/slides") {
    const { text } = JSON.parse(String(init?.body)) as { text: string };
    if (text.startsWith("ONE DECK")) return Response.json(ONE_DECK);
    if (text === WEBINAR) return Response.json(TWO_DECKS);
    return new Response(JSON.stringify({ detail: "no brief" }), { status: 422 });
  }
  if (url.startsWith("/api/pipeline/slides/pptx")) {
    deckUrls.push(url);
    return new Response("PK", {
      headers: { "Content-Disposition": `attachment; filename="${url.includes("index=") ? "one.pptx" : "all.zip"}"` },
    });
  }
  if (url.includes("/stream")) {
    return new Response(sseBody, { headers: { "Content-Type": "text/event-stream" } });
  }
  throw new Error(`unexpected fetch ${url}`);
}) as typeof fetch;

const sse = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");

function seed(messages: PipelineMessage[]) {
  const state = { started: true, isLoadingSession: false, phase: "phase1" as const, messages, currentIndex: 1, intake: null, runId: RUN_ID };
  usePipelineStore.setState(state);
  Object.assign(usePipelineStore.getInitialState(), state);
}

function openReader(messageId: string | null): string {
  Object.assign(useUiStore.getInitialState(), { readerMessageId: messageId, readerView: "visual" });
  useUiStore.setState({ readerMessageId: messageId });
  return renderToStaticMarkup(<AssetReader />);
}

const gen = (over: Partial<PipelineMessage>): PipelineMessage => ({
  id: "g1",
  role: "assistant",
  kind: "generation",
  text: "",
  createdAt: Date.now(),
  savePhase: "idle",
  ...over,
});

await (async () => {
  /* ---------- plan map ---------- */
  ok("the real plan builds a map", !!buildPlanMindMapHtml(PLAN, "Plan of Action"));
  const planMenu = renderToStaticMarkup(
    <AssetFileDownloads assetId="plan_of_action" text={PLAN} label="Plan of Action" runId={RUN_ID} variant="menu" />,
  );
  ok("card menu: plan map row", planMenu.includes("Download plan map (.html)") && planMenu.includes('role="menuitem"'));
  const planPill = renderToStaticMarkup(
    <AssetFileDownloads assetId="plan_of_action" text={PLAN} label="Plan of Action" runId={RUN_ID} variant="pill" />,
  );
  ok("reader: plan map pill", planPill.includes("Plan map (.html)") && !planPill.includes("menuitem"));
  const noMap = "A plan with no structure at all.";
  ok("a plan with no map has no row", buildPlanMindMapHtml(noMap, "x") === null &&
    renderToStaticMarkup(<AssetFileDownloads assetId="plan_of_action" text={noMap} label="x" runId={null} variant="menu" />) === "");

  seed([gen({ id: "plan", assetId: "plan_of_action", text: PLAN, savePhase: "saved" })]);
  const planReader = openReader("plan");
  ok("reader: the plan map is beside Markdown / Report", planReader.includes("Plan map (.html)") && planReader.includes("Markdown"));

  /* ---------- slide decks ---------- */
  ok("no brief: probe says none", (await probeSlideDecks("no slides here", RUN_ID)) === null);
  ok(
    "no brief: no deck row",
    renderToStaticMarkup(<AssetFileDownloads assetId="webinar" text="no slides here" label="Webinar" runId={RUN_ID} variant="menu" />) === "",
  );

  ok("several decks: probe finds two", (await probeSlideDecks(WEBINAR, RUN_ID))?.decks.length === 2);
  const rows = deckRows(TWO_DECKS);
  ok("several decks: one row per deck plus all", rows.length === 3, JSON.stringify(rows));
  ok("several decks: each deck row names its own index", rows[0].index === 0 && rows[1].index === 1);
  ok("several decks: the all row omits the index", rows[2].index === undefined && rows[2].label.includes(".zip"));
  const multiMenu = renderToStaticMarkup(
    <AssetFileDownloads assetId="webinar" text={WEBINAR} label="Webinar" runId={RUN_ID} variant="menu" />,
  );
  ok(
    "card menu: each deck and the zip",
    multiMenu.includes("Why Social Isn") && multiMenu.includes("The Partner Voice Playbook") && multiMenu.includes("Download all slide decks (.zip)"),
    multiMenu.slice(0, 400),
  );
  ok("card menu: per-deck rows carry the deck's filename", multiMenu.includes("why-social-isnt-working.pptx"));

  seed([gen({ id: "web", assetId: "webinar", text: WEBINAR, savePhase: "saved" })]);
  const webReader = openReader("web");
  ok("reader: the decks are offered too", webReader.includes("The Partner Voice Playbook") && webReader.includes("(.zip)"));

  const single = deckRows(ONE_DECK);
  ok("one deck: a single .pptx row, no zip", single.length === 1 && single[0].label.includes(".pptx") && single[0].index === undefined);
  await probeSlideDecks("ONE DECK webinar", RUN_ID);
  const oneMenu = renderToStaticMarkup(<AssetFileDownloads assetId="webinar" text="ONE DECK webinar" label="Webinar" runId={RUN_ID} variant="menu" />);
  ok("one deck: rendered as one row", oneMenu.includes("Download slide deck (.pptx)") && !oneMenu.includes(".zip"));

  const picked = await fetchSlideDeck(WEBINAR, RUN_ID, 1);
  const all = await fetchSlideDeck(WEBINAR, RUN_ID);
  ok("download by index asks for that deck", deckUrls[0] === "/api/pipeline/slides/pptx?index=1" && picked.filename === "one.pptx");
  ok("download all omits the index", deckUrls[1] === "/api/pipeline/slides/pptx" && all.filename === "all.zip");

  /* ---------- the replica events ---------- */
  const seen: ReplicaStreamEvent[] = [];
  const imageNotes: string[] = [];
  const report = {
    asset_id: "cro",
    source_url: "https://client.example/service/",
    filled: 93,
    kept: 7,
    intact: true,
    rejected: [],
    warnings: [["s12", "longer than the original"]],
    checks: [["contact links", true, ""]],
    structural: [],
  };
  sseBody = sse(
    { type: "image_notes", notes: ["The proof image could not be generated: content policy"] },
    { type: "replica_template", available: true, source_url: report.source_url, source: "context.dev", open_slots: 100, locked_slots: 12, notes: [] },
    { type: "delta", text: "The document." },
    { type: "replica_start" },
    { type: "replica", ...report },
    { type: "done", stop_reason: "end_turn", truncated: false },
  );
  let chunks = "";
  await streamGenerateStage("cro", {}, (c) => (chunks += c), {
    onReplica: (e) => seen.push(e),
    onImageNotes: (n) => imageNotes.push(...n),
  });
  ok("stream: every replica event reaches the handler", seen.map((e) => e.type).join(",") === "replica_template,replica_start,replica");
  ok("stream: the report carries no HTML", !("html" in (seen[2] as object)));
  ok("stream: image notes arrive as their own event", imageNotes.length === 1 && imageNotes[0].includes("proof image"));
  ok("stream: the document still streamed", chunks === "The document.");

  let state = undefined as ReturnType<typeof nextReplicaState> | undefined;
  for (const e of seen) state = nextReplicaState(state, e, RUN_ID);
  ok("fold: done with the report and the run", state?.status === "done" && state.report?.filled === 93 && state.runId === RUN_ID);

  const donePanel = renderToStaticMarkup(<ReplicaPanel message={gen({ assetId: "cro", replica: state })} label="CRO" />);
  ok("panel: the report", donePanel.includes("93") && donePanel.includes("lines filled") && donePanel.includes("Structure intact"));
  ok("panel: a download once an assembly exists", donePanel.includes("Download assembled page (.html)"));
  ok("panel: says the rendered reader answered", donePanel.includes("read via Context.dev"));

  const assembling = nextReplicaState(nextReplicaState(undefined, seen[0], RUN_ID), seen[1], RUN_ID);
  const assemblingPanel = renderToStaticMarkup(<ReplicaPanel message={gen({ assetId: "cro", replica: assembling })} label="CRO" />);
  ok("panel: no download before an assembly exists", !assemblingPanel.includes("Download assembled page"));

  // A failed assembly: the stream finishes normally, the card is not failed, and nothing is offered.
  const errors: ReplicaStreamEvent[] = [];
  sseBody = sse(
    { type: "delta", text: "Whole document." },
    { type: "replica_start" },
    { type: "replica_error", message: "the template could not be read", code: "upstream" },
    { type: "done", stop_reason: "end_turn", truncated: false },
  );
  let threw = false;
  try {
    await streamGenerateStage("cro", {}, () => {}, { onReplica: (e) => errors.push(e) });
  } catch {
    threw = true;
  }
  ok("replica_error: the stream does not throw", !threw && errors.some((e) => e.type === "replica_error"));
  const failed = errors.reduce<ReturnType<typeof nextReplicaState> | undefined>((s, e) => nextReplicaState(s, e, RUN_ID), undefined);
  seed([gen({ id: "cro1", assetId: "cro", text: "**PART 1 — AUDIT**\nWhole document.", replica: failed })]);
  const card = renderToStaticMarkup(<GenerationStream />);
  ok("replica_error: the card shows the document, not a failure", card.includes("Whole document.") && !/Generation failed|Something went wrong/i.test(card));
  ok("replica_error: the note says the document is complete", card.includes("could not be assembled") && card.includes("document above is complete"));
  ok("replica_error: no download", !card.includes("Download assembled page"));

  /* ---------- image notes on the card ---------- */
  const notes = renderToStaticMarkup(<ImageNotes notes={imageNotes} />);
  ok("image notes: shown", notes.includes("Some images were not generated") && notes.includes("proof image"));
  ok("image notes: nothing when none failed", renderToStaticMarkup(<ImageNotes notes={[]} />) === "");
  seed([gen({ id: "pp", assetId: "pillar_page", text: "A page.", imageNotes })]);
  ok("image notes: on the generation card", renderToStaticMarkup(<GenerationStream />).includes("Some images were not generated"));

  /* ---------- a page Refine is made of edits, and says which landed ---------- */
  const realFetch = globalThis.fetch;
  let refineBody: Record<string, unknown> = {};
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/refine/")) {
      refineBody = JSON.parse(String(init?.body));
      return new Response(
        sse(
          { type: "revision_edits", applied: 2, rejected: [["<p>Old proof</p>", "Not applied: the text to change is not in the draft."]], unreadable: false },
          { type: "delta", text: "The page, edited in place." },
          { type: "done", stop_reason: "end_turn", truncated: false },
        ),
      );
    }
    return realFetch(input, init);
  }) as typeof fetch;
  const reports: RevisionEditReport[] = [];
  let revised = "";
  await streamRefineStage("pillar_page", "PAGE", "Fix these", (c) => (revised += c), undefined, "phase1", {}, undefined, {
    onRevisionEdits: (r) => reports.push(r),
    sourceCopy: "THE CRO COPY",
  });
  globalThis.fetch = realFetch;
  ok("refine: sends the copy the page was built from", refineBody.source_copy === "THE CRO COPY");
  ok("refine: the edit report reaches the handler", reports.length === 1 && reports[0].applied === 2 && reports[0].rejected.length === 1);
  ok("refine: the edited page still arrives as the draft", revised === "The page, edited in place.");
  const partial = renderToStaticMarkup(<RevisionNotes report={reports[0]} />);
  ok("refine: an edit that did not land is named", partial.includes("2 edits applied, 1 not applied") && partial.includes("Old proof"));
  const clean = renderToStaticMarkup(<RevisionNotes report={{ applied: 3, rejected: [], unreadable: false }} />);
  ok("refine: a clean refine says the rest is unchanged", clean.includes("3 edits applied") && clean.includes("everything else is unchanged"));
  const locked = renderToStaticMarkup(
    <RevisionNotes
      report={{
        applied: 4,
        rejected: [[".logo{width:160px}", "Not applied: it would change the page's layout (a section, image, icon, logo, class, style or the CSS)."]],
        skipped: [["Fixed width 1200px on .container", "needs a CSS change"]],
        unreadable: false,
        layout_locked: true,
      }}
    />,
  );
  ok("fix with refine: says the layout was locked", locked.includes("Layout locked: only copy and links were changed."));
  ok("fix with refine: names the edit refused for touching layout", locked.includes("would change the page") && locked.includes(".logo{width:160px}"));
  ok("fix with refine: lists what it left for a layout change", locked.includes("1 left for a layout change") && locked.includes("Fixed width 1200px"));
  seed([gen({ id: "rf", assetId: "pillar_page", text: "A page.", revisionReport: reports[0] })]);
  ok("refine: the report is on the card", renderToStaticMarkup(<GenerationStream />).includes("not applied"));
})();

console.log(`exports: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
