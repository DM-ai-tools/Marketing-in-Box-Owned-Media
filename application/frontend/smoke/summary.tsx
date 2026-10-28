/* The reader's Summary tab. Not shipped; see smoke/README.md.
 *
 * Proven here, driving the store's real action with `fetch` stubbed:
 *   - opening the tab asks the server once per draft, and reopening it asks nothing;
 *   - a changed draft is summarised again; an error is kept until "Try again";
 *   - a "loading" saved by a closed tab is asked again rather than spinning forever;
 *   - the tab renders its loading, error and done states, says it was written by AI and checked,
 *     and a point with a real section offers a jump to it.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { parseAssetDocument } from "../src/lib/assetDocument";
import { usePipelineStore } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import { SummaryContent } from "../src/pipeline/SummaryView";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};
const flush = async () => {
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
};

const DOC = "# ICP\n\n## Buyer\n\nA clinic owner in Melbourne.\n\n## Triggers\n\nFlat bookings for a quarter.";
let calls: { url: string; body: Record<string, unknown> }[] = [];
let failNext = false;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = JSON.parse(String(init?.body ?? "{}"));
  calls.push({ url, body });
  if (failNext) {
    failNext = false;
    return new Response(JSON.stringify({ detail: "The summary could not be made: overloaded" }), { status: 422 });
  }
  return new Response(
    JSON.stringify({
      points: [
        { text: "The buyer is a clinic owner in Melbourne.", section: "Buyer" },
        { text: "Covers what makes them switch.", section: "" },
      ],
    }),
    { status: 200 },
  );
}) as typeof fetch;

const generation = (text: string, extra: Partial<PipelineMessage> = {}): PipelineMessage =>
  ({ id: "g1", role: "assistant", kind: "generation", assetId: "icp", text, phase: "phase1", createdAt: 1, savePhase: "saved", ...extra }) as PipelineMessage;

const seed = (message: PipelineMessage) =>
  usePipelineStore.setState({ messages: [message], runId: "run-1", sessionId: null, phase: "phase1" } as never);
const summary = () => usePipelineStore.getState().messages[0].docSummary;
const load = (force?: boolean) => usePipelineStore.getState().loadDocSummary("g1", force);
const render = () =>
  renderToStaticMarkup(
    <SummaryContent
      summary={summary()}
      label="ICP — Ideal Customer Profile"
      sections={parseAssetDocument(DOC).sections}
      onJump={() => {}}
      onRetry={() => {}}
    />,
  );

async function main() {
  seed(generation(DOC));
  ok("before anything arrives, the tab says it is summarising", render().includes("Summarising this document"));

  await load();
  await flush();
  ok("one request, to the summary route for this asset", calls.length === 1 && calls[0].url.endsWith("/api/pipeline/summary/icp"), JSON.stringify(calls));
  ok("it sends the document and its label", calls[0].body.text === DOC && calls[0].body.label === "ICP — Ideal Customer Profile", JSON.stringify(calls[0].body));
  ok("the points are kept on the message", summary()?.status === "done" && summary()?.points?.length === 2);

  const html = render();
  ok("the points are listed", html.includes("The buyer is a clinic owner in Melbourne.") && html.includes("Covers what makes them switch."));
  ok("a point with a real section offers a jump", html.includes("Buyer →"));
  ok("it says how it was made", html.includes("Written by AI from this document and checked against it"));
  ok("points, not paragraphs", (html.match(/<li /g) ?? []).length === 2 && !html.includes("<p>The buyer"));

  await load();
  await flush();
  ok("reopening the same draft asks nothing", calls.length === 1);

  seed(generation(DOC + "\n\nA refined line.", { docSummary: summary() }));
  await load();
  await flush();
  ok("a changed draft is summarised again", calls.length === 2);

  failNext = true;
  seed(generation(DOC + " v3", { docSummary: summary() }));
  await load();
  await flush();
  ok("an error is kept with its sentence", summary()?.status === "error" && !!summary()?.error?.includes("overloaded"), JSON.stringify(summary()));
  ok("the error renders with a retry", render().includes("The summary could not be made.") && render().includes("Try again"));
  await load();
  await flush();
  ok("an error is not retried by itself", calls.length === 3);
  await load(true);
  await flush();
  ok("Try again asks again", calls.length === 4 && summary()?.status === "done");

  calls = [];
  const stale = generation(DOC, { docSummary: { status: "loading", textHash: "" } });
  seed(stale);
  await load();
  await flush();
  const hash = summary()?.textHash ?? "";
  seed(generation(DOC, { docSummary: { status: "loading", textHash: hash } }));
  calls = [];
  await load();
  await flush();
  ok("a loading state saved by a closed tab is asked again", calls.length === 1 && summary()?.status === "done");

  console.log(`summary: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
