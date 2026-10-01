/* The business check on a draft. Not shipped; see smoke/README.md.
 *
 * Driven through the store's real actions with `fetch` stubbed — the generation stream, the check
 * route and the refine stream all answer from here. What is proven:
 *   - a draft that streams to a clean finish is checked with no click,
 *   - the panel paints its scores, the virality "prediction" label and the findings,
 *   - Approve stays on offer whatever the check found,
 *   - "Fix … with Refine" sends the chosen findings, quoted, as the Refine note,
 *   - a check that fails says so and offers a retry, and a failed or truncated draft is not checked.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { AssetCheckPanel } from "../src/pipeline/AssetCheckPanel";
import { GenerationStream } from "../src/pipeline/GenerationStream";
import type { AssetCheckReport } from "../src/pipeline/pipelineApi";
import { composeFixNote, usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import { useUiStore } from "../src/store/uiStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};

const REPORT: AssetCheckReport = {
  asset_id: "blog",
  checks: [
    {
      check: "business_logic",
      score: 58,
      verdict: "One forbidden claim.",
      findings: [
        { check: "business_logic", severity: "error", quote: "Guaranteed results in 30 days.", why: '"Guaranteed" is an absolute claim; this client\'s claim tier is regulated.', fix: "Qualify it.", source: "rule" },
        { check: "business_logic", severity: "info", quote: "[PLACEHOLDER — phone]", why: "A placeholder left for the client to complete.", fix: "Fill it in.", source: "rule" },
      ],
    },
    { check: "virality", score: 71, verdict: "Strong hook, weak shareability.", findings: [] },
  ],
  facts_used: ["claim substantiation tier"],
  facts_missing: ["the offer ladder"],
  judge: "ok",
  dropped_quotes: 0,
  virality_basis: "rubric only",
  duration_ms: 1200,
};

interface Call {
  url: string;
  body: Record<string, unknown> | null;
}
const calls: Call[] = [];
let checkReply: () => Response = () => json(REPORT);

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const sse = (text: string) =>
  new Response(
    `data: ${JSON.stringify({ type: "delta", text })}\n\ndata: ${JSON.stringify({ type: "done" })}\n\n`,
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
  calls.push({ url, body });
  if (url.includes("/api/pipeline/check/")) return checkReply();
  if (url.includes("/api/pipeline/generate/")) return sse("Guaranteed results in 30 days. Call [PLACEHOLDER — phone].");
  if (url.includes("/api/pipeline/refine/")) return sse("Results vary. Call us on 1300 000 000.");
  return json({ detail: "not stubbed" }, 500);
}) as typeof fetch;

const flush = async () => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

function seed(messages: PipelineMessage[]) {
  const full = {
    started: true,
    isLoadingSession: false,
    phase: "phase1" as const,
    runId: "run-1",
    sessionId: null,
    messages,
    context: {},
    currentIndex: 7,
    intake: null,
    subStep: null,
    activeStatus: "hitl",
    progress: 100,
    navStatus: "Awaiting Review" as const,
    clientProfile: { client_name: "Acme", industry_label: "Regulated Finance" },
    editSeed: null,
    fault: null,
  };
  usePipelineStore.setState(full as never);
  Object.assign(usePipelineStore.getInitialState(), full);
  calls.length = 0;
}
useUiStore.setState({ workTab: "transcript" } as never);
const html = () => {
  Object.assign(usePipelineStore.getInitialState(), usePipelineStore.getState());
  return renderToStaticMarkup(<GenerationStream />);
};
const card = (id: string): PipelineMessage | undefined => usePipelineStore.getState().messages.find((m) => m.id === id);

const failedDraft: PipelineMessage = {
  id: "g1",
  role: "assistant",
  kind: "generation",
  assetId: "blog",
  text: "",
  savePhase: "idle",
  answers: { blog_topic_working_title: "Why social goes flat" },
  generationError: "Earlier failure",
  phase: "phase1",
  createdAt: 1,
};

async function main() {
  // ---------- a clean finish is checked on its own ----------
  seed([failedDraft]);
  await usePipelineStore.getState().retryGeneration("g1");
  await flush();
  const checkCall = calls.find((c) => c.url.endsWith("/api/pipeline/check/blog"));
  ok("a finished draft is checked without a click", !!checkCall);
  ok("the check reads the draft as streamed", String(checkCall?.body?.text).startsWith("Guaranteed results"));
  ok("the check is attributed to the run", checkCall?.body?.run_id === "run-1");
  ok("the check knows the client", (checkCall?.body?.client_profile as Record<string, string>)?.industry_label === "Regulated Finance");
  ok("the report lands on the card", card("g1")?.check?.status === "done");

  const page = html();
  ok("panel shows each check's score", page.includes("Business rules") && page.includes(" 58") && page.includes("Virality"));
  ok("virality is labelled a prediction", page.includes("prediction"));
  ok("an error opens the findings", page.includes("Guaranteed results in 30 days."));
  ok("missing facts are stated", page.includes("Not on record, so not checked: the offer ladder"));
  ok("fix is offered for the chosen findings", page.includes("Fix 1 with Refine"), "info notes are opt-in");
  ok("approve is still offered", page.includes("Save It"));

  // ---------- "Fix" goes to Refine, quoted ----------
  const note = composeFixNote(REPORT.checks[0].findings.slice(0, 1));
  ok("the note quotes the finding", note.includes('Where: "Guaranteed results in 30 days."') && note.includes("Fix: Qualify it."));
  await usePipelineStore.getState().fixCheckFindings("g1", REPORT.checks[0].findings.slice(0, 1));
  await flush();
  const refine = calls.find((c) => c.url.includes("/api/pipeline/refine/blog"));
  ok("fix sends the findings to Refine", String(refine?.body?.note ?? "").includes("Guaranteed results in 30 days."), JSON.stringify(refine?.body));
  const revised = usePipelineStore.getState().messages.filter((m) => m.kind === "generation").pop();
  ok("the refined draft is checked too", revised?.id !== "g1" && revised?.check?.status === "done");

  // ---------- a failed check is a retry, not a failed stage ----------
  checkReply = () => json({ detail: "overloaded" }, 503);
  seed([{ ...failedDraft, id: "g2" }]);
  await usePipelineStore.getState().retryGeneration("g2");
  await flush();
  ok("a failed check is recorded on the card", card("g2")?.check?.status === "error");
  ok("a failed check offers a retry", html().includes("Check again"));
  ok("a failed check does not fail the draft", !card("g2")?.generationError && !!card("g2")?.text);

  // ---------- a failed draft is not checked ----------
  checkReply = () => json(REPORT);
  seed([{ ...failedDraft, id: "g3", answers: undefined }]);
  await usePipelineStore.getState().retryGeneration("g3");
  await flush();
  ok("nothing is checked without a finished draft", !calls.some((c) => c.url.includes("/check/")));

  // ---------- the scan, while the check runs ----------
  const scanning = renderToStaticMarkup(
    <AssetCheckPanel message={{ ...failedDraft, id: "g4", text: "Draft", generationError: undefined, check: { status: "running" } }} />,
  );
  ok("a running check shows the scan, marked busy", scanning.includes('aria-busy="true"') && scanning.includes("scanning this draft"));
  ok("the scan animates (beam, bar, current step)", ["bc-beam", "bc-bar", "bc-pulse"].every((c) => scanning.includes(c)));
  ok("the scan says what runs, in words", scanning.includes("Rule checks") && scanning.includes("Predicting virality") && scanning.includes("in progress"));
  ok("the scan says approval is not blocked", scanning.includes("never blocks"));

  // ---------- the results explain their scores ----------
  const results = renderToStaticMarkup(
    <AssetCheckPanel message={{ ...failedDraft, id: "g5", text: "Draft", generationError: undefined, check: { status: "done", report: REPORT } }} />,
  );
  ok("each score says what it measures", results.includes("Are its claims, prices, testimonials and wording allowed"));
  ok("scores are banded, in words", results.includes("80–100 Good") && results.includes("At risk"));
  ok("an error is labelled must fix", results.includes("Must fix"));
  ok("fixing is explained before the click", results.includes("Sends a note") && results.includes("Checks it again"));

  console.log(`check: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log(`  FAIL ${f}`);
  if (fails.length) process.exitCode = 1;
  setTimeout(() => process.exit(process.exitCode ?? 0), 0);
}

void main();
