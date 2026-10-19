/* The deployable funnel: offered once Funnel and Funnel Hub & Media are approved, built, shown, and
 * reopened from the card menu. Not shipped; see smoke/README.md.
 *
 * Drives the store with `fetch` stubbed (the build's SSE stream is a canned body) and renders the
 * card. Checks:
 *
 *   - approving the Funnel alone offers nothing; approving Funnel Hub & Media after it offers the build;
 *   - building streams each page and email onto the card, in the funnel's own order;
 *   - the finished card shows every page's deploy check, Preview and Rebuild, and the zip link;
 *   - "Not now" says how to get back to it, and the menu entry reopens a stored build without
 *     building again.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { FunnelBuildCard } from "../src/pipeline/FunnelBuildCard";
import { maybeOfferFunnelBuild, usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const plan = {
  title: "Social Media Funnel",
  files: [
    { key: "social-media-audit", title: "Free audit landing page", url_path: "/social-media-audit/", file_path: "social-media-audit/index.html", screens: 1 },
    { key: "book-strategy-consultation", title: "Book a consultation", url_path: "/book-strategy-consultation/", file_path: "book-strategy-consultation/index.html", screens: 1 },
  ],
  emails: [{ key: "email-1", title: "Email 1 — Immediate", subject: "Your audit is underway" }],
};
const item = (key: string, kind: "page" | "email", title: string, score: number | null) => ({
  key, kind, title, file_path: `${key}/index.html`, url_path: kind === "page" ? `/${key}/` : "", subject: "",
  notes: "", score, verdict: score === null ? "" : "Deployable after reviewing 1 issue(s).", issues: [], error: "",
});
const built = [
  item("book-strategy-consultation", "page", "Book a consultation", 92),
  item("social-media-audit", "page", "Free audit landing page", 88),
  item("email-1", "email", "Email 1 — Immediate", null),
];
const sse = [
  { type: "plan", plan, hub: true },
  { type: "inputs", forms: [{ action: "https://trafficradius.com.au/contact/#wpcf7-f1-o1", page_url: "https://trafficradius.com.au/contact/" }], notes: [], branded: true },
  { type: "item_start", key: "social-media-audit", kind: "page" },
  { type: "manus", status: "started", task_url: "https://manus.im/app/t1", elapsed: 0, brief: "Manus is reading the brief" },
  { type: "manus", status: "running", task_url: "https://manus.im/app/t1", elapsed: 754, brief: "Measuring the reference page's scroll reveals", credits: 40 },
  { type: "manus", status: "done", task_url: "https://manus.im/app/t1", credits: 120 },
  { type: "item_done", item: built[1] },
  { type: "item_done", item: built[0] },
  { type: "item_done", item: built[2] },
  { type: "done", version: 1, items: built, engine: { pages: "manus", task_url: "https://manus.im/app/t1", credits: 120 } },
]
  .map((e) => `data: ${JSON.stringify(e)}\n\n`)
  .join(": keepalive\n\n");

const calls: string[] = [];
const bodies: Record<string, unknown>[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  calls.push(url);
  if (init?.body) bodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
  if (url.endsWith("/funnel-build/stream")) return new Response(sse, { status: 200 });
  if (url.endsWith("/funnel-build/ready")) {
    return new Response(JSON.stringify({ funnel: true, hub: true, buildable: true, plan, built: true, built_at: "2026-10-19T10:00:00Z" }), { status: 200 });
  }
  if (url.endsWith("/funnel-build")) {
    return new Response(JSON.stringify({ items: built.map((b) => ({ ...b, html: "<html></html>" })), built_at: "2026-10-19T10:00:00Z" }), { status: 200 });
  }
  if (url.includes("/chat-sessions")) return new Response("{}", { status: 200 }); // autosave
  return new Response("{}", { status: 404 });
}) as typeof fetch;

const saved = (assetId: string, id: string): PipelineMessage =>
  ({ id, role: "assistant", kind: "generation", assetId, savePhase: "saved", text: "x", phase: "phase1", createdAt: 1 }) as PipelineMessage;

function seed(messages: PipelineMessage[]) {
  usePipelineStore.setState({ started: true, phase: "phase1", runId: "run-1", sessionId: "s-1", messages, clientProfile: {} } as never);
}
const store = () => usePipelineStore.getState();
const cards = () => store().messages.filter((m) => m.kind === "funnel-build" && !m.superseded);
const render = (m: PipelineMessage) => {
  Object.assign(usePipelineStore.getInitialState(), { runId: "run-1" });
  return renderToStaticMarkup(<FunnelBuildCard message={m} />);
};

// 1. Offered only once both are approved.
seed([saved("funnel", "f")]);
maybeOfferFunnelBuild(store, usePipelineStore.setState, "funnel");
ok("the Funnel alone offers nothing", cards().length === 0);
seed([saved("funnel", "f"), saved("funnel_hub_media", "h")]);
maybeOfferFunnelBuild(store, usePipelineStore.setState, "funnel_hub_media");
ok("both approved offers the build", cards().length === 1 && cards()[0].funnelBuild?.status === "offer");
maybeOfferFunnelBuild(store, usePipelineStore.setState, "funnel");
ok("a second approval replaces the untaken offer", cards().length === 1, String(cards().length));
const offerHtml = render(cards()[0]);
ok("the offer asks to build, and can be declined", offerHtml.includes("Build deployable files") && offerHtml.includes("Not now"));

// 2. Build: streamed onto the card, in plan order.
const id = cards()[0].id;
await store().startFunnelBuild(id);
let card = store().messages.find((m) => m.id === id)!;
ok("the build finishes", card.funnelBuild?.status === "done", card.funnelBuild?.error);
ok(
  "items are in the funnel's order",
  card.funnelBuild?.items.map((i) => i.key).join(",") === "social-media-audit,book-strategy-consultation,email-1",
  card.funnelBuild?.items.map((i) => i.key).join(","),
);
ok("the stream was posted", calls.some((u) => u === "/api/pipeline/runs/run-1/funnel-build/stream"));
const doneHtml = render(card);
ok("each page shows its deploy check", doneHtml.includes("88/100") && doneHtml.includes("92/100"));
ok("pages can be previewed and rebuilt", doneHtml.includes(">Preview<") && doneHtml.includes(">Rebuild<"));
ok("the zip is offered", doneHtml.includes('href="/api/pipeline/runs/run-1/funnel-build/zip"'));
ok("it says where the forms post", doneHtml.includes("https://trafficradius.com.au/contact/#wpcf7-f1-o1"));
ok("it says Manus built the pages, with credits and the task", doneHtml.includes("Pages built by Manus from the reference page · 120 credits") && doneHtml.includes('href="https://manus.im/app/t1"'));
ok("the offer says Manus builds from the reference page", offerHtml.includes("Manus builds the pages") && offerHtml.includes("scroll"));

// While Manus works, the card shows its own words and the elapsed time.
{
  const building = { ...card, funnelBuild: { ...card.funnelBuild!, status: "building" as const, manus: { type: "manus" as const, status: "running", brief: "Measuring the reference page's scroll reveals", elapsed: 754, task_url: "https://manus.im/app/t1" } } };
  const html = render(building as PipelineMessage);
  ok("building shows Manus's own progress", html.includes("Manus is building the pages") && html.includes("Measuring the reference page") && html.includes("12m 34s"), html.slice(0, 300));
  const fallback = { ...card, funnelBuild: { ...card.funnelBuild!, manus: { type: "manus" as const, status: "fallback", reason: "Manus kept stopping to ask for input" } } };
  ok("a fallback says why Claude built the pages", render(fallback as PipelineMessage).includes("Manus could not build the pages (Manus kept stopping to ask for input)"));
}

// Rebuild of one page goes through the same stream, for that page only.
bodies.length = 0;
await store().rebuildFunnelBuildItem(id, "social-media-audit");
ok("a rebuild streams that page only", bodies.some((b) => JSON.stringify(b.only) === JSON.stringify(["social-media-audit"])), JSON.stringify(bodies));
ok("and ends with the card still done", store().messages.find((m) => m.id === id)?.funnelBuild?.status === "done" && !store().messages.find((m) => m.id === id)?.funnelBuild?.rebuilding);

// 3. Not now, then the menu entry: reopened from the stored build, never rebuilt.
seed([saved("funnel", "f"), saved("funnel_hub_media", "h")]);
maybeOfferFunnelBuild(store, usePipelineStore.setState, "funnel");
store().declineFunnelBuild(cards()[0].id);
ok("Not now says how to get back to it", render(cards()[0]).includes("Build deployable funnel"));
calls.length = 0;
await store().offerFunnelBuild();
card = cards()[cards().length - 1];
ok("the menu entry opens the stored build", card.funnelBuild?.status === "done" && card.funnelBuild.items.length === 3);
ok("without building again", !calls.some((u) => u.endsWith("/stream")), calls.join(" "));

// 4. Requested changes: offered on every finished file, applied to that file only, and reported.
ok("each finished file offers Request changes", (render(card).match(/>Request changes</g) ?? []).length === 3);
const revised = { ...built[0], revisions: [{ note: "Change the button", applied: 1 }] };
const reviseCalls: { url: string; body: Record<string, unknown> }[] = [];
const baseFetch = globalThis.fetch;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.endsWith("/revise")) {
    reviseCalls.push({ url, body: JSON.parse(String(init?.body)) as Record<string, unknown> });
    const nothing = reviseCalls.length > 1;
    return new Response(
      JSON.stringify({
        item: revised,
        applied: nothing ? 0 : 1,
        not_applied: nothing ? [{ excerpt: "x", why: "Not applied: the text to change was not found." }] : [],
        unreadable: false,
      }),
      { status: 200 },
    );
  }
  return baseFetch(input, init);
}) as typeof fetch;

const applied = await store().reviseFunnelBuildItem(card.id, "book-strategy-consultation", "Change the button to Book my call");
card = store().messages.find((m) => m.id === card.id)!;
ok("a requested change is sent for that file only", applied && reviseCalls[0]?.url.endsWith("/funnel-build/items/book-strategy-consultation/revise"));
ok("with the operator's own words", reviseCalls[0]?.body.note === "Change the button to Book my call");
ok("the row says what landed", render(card).includes("Applied 1 change. Everything else on the page is as built."));
ok("and keeps the history", render(card).includes("1 requested change applied to this file"));
await store().reviseFunnelBuildItem(card.id, "book-strategy-consultation", "Something impossible");
card = store().messages.find((m) => m.id === card.id)!;
const nothingHtml = render(card);
ok("a change that did not land says nothing changed, and why", nothingHtml.includes("Nothing was changed.") && nothingHtml.includes("was not found"));
ok("a blank note is never sent", !(await store().reviseFunnelBuildItem(card.id, "email-1", "   ")) && reviseCalls.length === 2);

// 5. Save: into Deliverables, then on to the next asset — or a 3-second notice when none is left.
{
  const { stagesFor } = await import("../src/pipeline/pipelineData");
  const { useUiStore } = await import("../src/store/uiStore");
  const { DeliverablesGrid } = await import("../src/pipeline/DeliverablesGrid");
  const stages = stagesFor("phase1");
  const offersIndex = stages.findIndex((s) => s.asset.asset_id === "offers");
  const approvedExcept = (skip: string[]) =>
    stages.filter((s) => !skip.includes(s.asset.asset_id)).map((s, i) => saved(s.asset.asset_id, `g-${i}`));
  const doneCard = (id: string): PipelineMessage =>
    ({
      id, role: "assistant", kind: "funnel-build", phase: "phase1", createdAt: 2,
      funnelBuild: { status: "done", plan, items: built, building: [] },
    }) as PipelineMessage;

  const ok200: typeof fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.includes("/context/")) return new Response("{}", { status: 404 });
    return new Response("{}", { status: 200 });
  }) as typeof fetch;
  globalThis.fetch = ok200;

  // Offers is the next stage still to build.
  seed([...approvedExcept(["offers"]), doneCard("fb")]);
  usePipelineStore.setState({ currentIndex: offersIndex, intake: null, subStep: null, activeStatus: "hitl" } as never);
  store().saveFunnelBuild("fb");
  await new Promise((r) => setTimeout(r, 20));
  let after = store();
  ok("Save marks the build saved", after.messages.find((m) => m.id === "fb")?.funnelBuild?.saved === true);
  ok("and moves on to the next asset", after.currentIndex === offersIndex && after.subStep === "competitor", `${after.currentIndex} ${after.subStep}`);
  ok("a second Save does nothing", (store().saveFunnelBuild("fb"), store().messages.filter((m) => m.kind === "competitor").length === 1));

  // Deliverables shows it.
  Object.assign(usePipelineStore.getInitialState(), { messages: after.messages, phase: "phase1", runId: "run-1", activePhase2TrackId: null });
  const grid = renderToStaticMarkup(<DeliverablesGrid />);
  ok("Deliverables shows the deployable funnel", grid.includes("Deployable funnel") && grid.includes("2 pages · 1 email"));
  ok("with its zip", grid.includes('href="/api/pipeline/runs/run-1/funnel-build/zip"'));
  ok("and counts it as an approved asset", grid.includes(`${stages.length - 1 + 1} approved assets`), grid.slice(0, 200));

  // Nothing left: a notice that clears itself after 3 seconds.
  seed([...approvedExcept([]), doneCard("fb2")]);
  usePipelineStore.setState({ currentIndex: stages.length - 1, intake: null, subStep: null } as never);
  store().saveFunnelBuild("fb2");
  ok("with no assets left, a notice says so", useUiStore.getState().toast?.text === "There are no more assets to run in Phase 1.", useUiStore.getState().toast?.text);
  await new Promise((r) => setTimeout(r, 3100));
  ok("and it clears after 3 seconds", useUiStore.getState().toast === null);

  // A forward approval that offers the build waits on the card instead of starting the next stage.
  const hub = { ...saved("funnel_hub_media", "hub"), savePhase: "idle" } as PipelineMessage;
  seed([...approvedExcept(["funnel_hub_media", "offers"]), hub]);
  usePipelineStore.setState({ currentIndex: stages.findIndex((s) => s.asset.asset_id === "funnel_hub_media"), rerunReturnIndex: null } as never);
  await store().saveStage("hub");
  after = store();
  const last = after.messages[after.messages.length - 1];
  ok("approving Funnel Hub & Media parks the walk on the offer", last.kind === "funnel-build" && last.funnelBuild?.status === "offer", last.kind);
  ok("no next stage was started under it", !after.messages.some((m) => m.kind === "competitor") && after.navStatus === "Awaiting Review", after.navStatus);
  store().declineFunnelBuild(last.id);
  await new Promise((r) => setTimeout(r, 20));
  ok("Not now moves on to the next asset", store().subStep === "competitor" && store().currentIndex === offersIndex);
}

console.log(`funnelBuild: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
