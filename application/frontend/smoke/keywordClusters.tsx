/* Choosing which keyword clusters later stages use. Not shipped; see smoke/README.md.
 *
 * Proven here, driving the store's real action with `fetch` stubbed:
 *   - the picker lists every cluster with its tick, and says how many are in use,
 *   - saving sends the kept names and the client's site, and every announcement of that run's report
 *     then shows the selection, keeping its own `built`/`reused` status,
 *   - a refusal (nothing kept, an unknown name) comes back as a sentence and changes nothing,
 *   - the deliverable's "Keyword data" section counts and tables only the kept clusters,
 *   - a stub provider's placeholder clusters are never offered for choosing.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { keywordAppendixFor } from "../src/lib/keywordUsage";
import type { KeywordReportResult } from "../src/pipeline/pipelineApi";
import { usePipelineStore } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import { KeywordClusterPicker } from "../src/pipeline/KeywordClusterPicker";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};

const cluster = (name: string, volume: number, selected?: boolean) => ({
  name,
  intent: "commercial",
  funnel: "BOFU",
  content_type: null,
  recommended_content: null,
  recommended_url: null,
  primary_keyword: `${name.toLowerCase()} agency`,
  keyword_count: 10,
  total_volume: volume,
  ...(selected === undefined ? {} : { selected }),
});

const report = (provider = "dataforseo", status: KeywordReportResult["status"] = "built"): KeywordReportResult => ({
  run_id: "run-1",
  status,
  phase: "phase1",
  service: "Social Media Marketing",
  provider,
  version: 1,
  total_keywords: 30,
  clusters: [cluster("Meta ads", 1000), cluster("SEO", 2000), cluster("Email", 500)],
  warnings: [],
  location: "Australia",
});

let seq = 0;
const msg = (m: Partial<PipelineMessage>): PipelineMessage =>
  ({ id: `m${++seq}`, role: "assistant", kind: "text", createdAt: seq, phase: "phase1", ...m }) as PipelineMessage;

const calls: { method: string; body: Record<string, unknown> }[] = [];
let reply: { status: number; body: unknown } | null = null;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  if (String(input).endsWith("/api/pipeline/runs/run-1/keywords/clusters")) {
    calls.push({ method: String(init?.method), body: JSON.parse(String(init?.body ?? "{}")) });
    const r = reply!;
    return new Response(JSON.stringify(r.body), { status: r.status });
  }
  return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 });
}) as typeof fetch;

const render = (m: PipelineMessage) => renderToStaticMarkup(<KeywordClusterPicker message={m} />);

async function main() {
  const first = msg({ text: "Mapped search demand…", keywordReport: report("dataforseo", "built") });
  const again = msg({ text: "Mapped search demand…", keywordReport: report("dataforseo", "reused") });
  usePipelineStore.setState({ messages: [first, again], clientProfile: { website_url: "https://client.example" } });

  // ---------- the picker ----------
  const html = render(first);
  ok("every cluster is listed", ["Meta ads", "SEO", "Email"].every((n) => html.includes(n)));
  ok("all start ticked", (html.match(/checked=""/g) ?? []).length === 3, html);
  ok("it says how many are in use", html.includes("3 of 3 in use"));
  ok("Save is off until something changes", /<button[^>]*disabled=""[^>]*>Save selection/.test(html));
  const stub = msg({ keywordReport: report("stub") });
  ok("a stub provider's clusters are not offered", render(stub) === "");

  // ---------- saving ----------
  reply = {
    status: 200,
    body: {
      ...report("dataforseo", "reused"),
      version: 2,
      total_keywords: 10,
      clusters: [cluster("Meta ads", 1000, false), cluster("SEO", 2000, true), cluster("Email", 500, false)],
    },
  };
  const failure = await usePipelineStore.getState().selectKeywordClusters(first.id, ["SEO"]);
  ok("saving succeeds", failure === null, String(failure));
  ok("it PUTs the kept names and the client's site", calls.length === 1 && calls[0]!.method === "PUT" &&
    JSON.stringify(calls[0]!.body) === JSON.stringify({ selected: ["SEO"], website: "https://client.example" }), JSON.stringify(calls));
  const after = usePipelineStore.getState().messages;
  ok("every announcement of the run shows the selection", after.every((m) => m.keywordReport?.version === 2));
  ok("each keeps its own status", after[0]!.keywordReport?.status === "built" && after[1]!.keywordReport?.status === "reused");
  const picked = render(after[0]!);
  ok("the picker now says one is in use", picked.includes("1 of 3 in use"), picked);
  ok("only the kept cluster is ticked", (picked.match(/checked=""/g) ?? []).length === 1);

  // ---------- a refusal changes nothing ----------
  reply = { status: 422, body: { detail: "Keep at least one cluster; later stages need a topic to write about." } };
  const refused = await usePipelineStore.getState().selectKeywordClusters(first.id, []);
  ok("a refusal comes back as a sentence", typeof refused === "string" && refused.includes("at least one cluster"), String(refused));
  ok("and leaves the report as it was", usePipelineStore.getState().messages[0]!.keywordReport?.version === 2);
  ok("a message with no report is refused without a request", (await usePipelineStore.getState().selectKeywordClusters("nope", ["SEO"])) !== null && calls.length === 2);

  // ---------- the deliverable lists only the kept clusters ----------
  const blog = msg({ kind: "generation", assetId: "blog", text: "# Post\n\n## One\n\nBody.", savePhase: "saved" });
  const section = keywordAppendixFor([usePipelineStore.getState().messages[0]!, blog], blog);
  ok("the section counts the kept cluster only", section.includes("grouped into 1 topic cluster, the 1 the operator kept of 3 found"), section);
  ok("the table lists the kept cluster", /\| SEO \|/.test(section));
  ok("and not the others", !/\| Meta ads \|/.test(section) && !/\| Email \|/.test(section));

  console.log(`keywordClusters: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
