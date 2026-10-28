/* The Funnel researches its competitors; it never asks the operator to paste a list. Not shipped;
 * see smoke/README.md.
 *
 * The funnel gained a competitor search (`competitor_analysis_funnel`) after runs already had a
 * funnel. Re-running one went straight to the intake — a re-run assumed the stage's search had been
 * approved before — so the intake reached the empty "Competitor Funnel Analysis" input and asked for a
 * pasted list. This drives the store, with `fetch` stubbed, and checks:
 *
 *   - a re-run with no listing anywhere runs the search, on the run's own service;
 *   - a re-run whose listing is stored for the run reuses it (no second search, no second charge);
 *   - if the walk ever reaches the input with no listing, it offers to research rather than asking.
 */
import { stagesFor } from "../src/pipeline/pipelineData";
import { usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const calls: { url: string; body: Record<string, unknown> | null }[] = [];
let storedListing: string | null = null;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  calls.push({ url, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null });
  if (url.includes("/context/competitor_analysis_funnel")) {
    return storedListing
      ? new Response(JSON.stringify({ content: storedListing }), { status: 200 })
      : new Response("{}", { status: 404 });
  }
  // The intake's own context reads (ICP, CRO copy …): nothing stored for this test run.
  if (url.includes("/context/")) return new Response("{}", { status: 404 });
  // Suggested answers (`smoke/contextSearch.tsx` covers them): none, so the intake asks as usual.
  if (url.includes("/prefill/")) return new Response("{}", { status: 404 });
  // Every other request (the competitor search itself) stays pending: what is checked is that it
  // was made, and with what.
  return new Promise<Response>(() => {});
}) as typeof fetch;

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
const funnelIndex = stagesFor("phase1").findIndex((s) => s.asset.asset_id === "funnel");

function seed(context: Record<string, unknown> = {}) {
  const saved: PipelineMessage = {
    id: "funnel-v1",
    role: "assistant",
    kind: "generation",
    assetId: "funnel",
    savePhase: "saved",
    text: "PART 2: Competitor Funnel Analysis\nSTEP 2 SKIPPED — no competitor analysis supplied for this stage.",
    phase: "phase1",
    createdAt: 1,
  } as PipelineMessage;
  usePipelineStore.setState({
    started: true,
    phase: "phase1",
    runId: "run-1",
    currentIndex: funnelIndex + 1,
    messages: [saved],
    context,
    intake: null,
    subStep: null,
    clientProfile: {
      website_url: "https://clicktrends.com.au/",
      industry: "Digital marketing",
      region: "Melbourne",
      target_service: "SEO",
    },
  } as never);
}

const searches = () => calls.filter((c) => c.url.includes("/competitor/competitor_analysis_funnel/run"));

// 1. Re-run with no listing anywhere: the search runs, on this run's service.
seed();
usePipelineStore.getState().rerunStage("funnel-v1");
await settle();
let state = usePipelineStore.getState();
ok("re-run with no listing runs the funnel competitor search", searches().length === 1, JSON.stringify(calls.map((c) => c.url)));
ok("the search is on the run's own service", searches()[0]?.body?.service === "SEO", JSON.stringify(searches()[0]?.body));
ok(
  "a competitor card is on screen, not a question",
  state.messages.some((m) => m.kind === "competitor" && m.assetId === "competitor_analysis_funnel") &&
    !state.messages.some((m) => m.kind === "question" && m.field?.field_id === "competitor_funnel_analysis"),
);

// 2. Re-run with the listing stored for the run: reused, never searched again.
calls.length = 0;
storedListing = "Competitor analysis — benchmarked against https://clicktrends.com.au/\n\n1. Rival (rival.com.au) — Verified";
seed();
usePipelineStore.getState().rerunStage("funnel-v1");
await settle();
state = usePipelineStore.getState();
ok("a stored listing is not searched for again", searches().length === 0, JSON.stringify(calls.map((c) => c.url)));
ok("the stored listing is loaded into the session", !!state.context["competitor_analysis_funnel"]);
ok("the funnel's own intake starts", state.intake?.asset.asset_id === "funnel", String(state.intake?.asset.asset_id));
ok(
  "the competitor input is never asked",
  !state.messages.some((m) => m.kind === "question" && m.field?.field_id === "competitor_funnel_analysis"),
);

// 3. The safety net: reaching the input with no listing offers research, on the run's service.
calls.length = 0;
storedListing = null;
seed({ competitor_analysis_funnel: undefined });
const funnel = stagesFor("phase1")[funnelIndex].asset;
const answers: Record<string, unknown> = {
  funnel_type: "Lead magnet → nurture → call",
  lead_magnet_mechanic: "A checklist",
  target_service_if_different_from_pillar_page: "",
};
usePipelineStore.setState({ currentIndex: funnelIndex, intake: { asset: funnel, answers, awaitingFieldId: null } } as never);
// Walk to the competitor input the way `editField` does: it re-offers research for a consent field.
usePipelineStore.getState().editField("competitor_funnel_analysis");
await settle();
state = usePipelineStore.getState();
const consent = state.messages.find((m) => m.kind === "competitor-consent");
ok("the input offers to research instead of asking for a list", !!consent, state.messages.map((m) => m.kind).join(","));
ok("and offers it on the run's service", consent?.consent?.topic === "SEO", String(consent?.consent?.topic));

console.log(`funnelCompetitors: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
process.exit();
