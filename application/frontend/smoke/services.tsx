/* The Pillar Page's service picker and its no-reference path. Not shipped; see smoke/README.md.
 *
 * Driven through the store's real actions with `fetch` stubbed, in both phases:
 *   - the reference question offers "this page doesn't exist yet", which asks for the main landing
 *     page and writes the no-reference sentinel and FULL SITE STYLE without asking either again,
 *   - the walk then scans the reference (or landing) page and offers its services as a checklist,
 *     pre-ticking the run's own service (Phase 1) or sub-service (Phase 2),
 *   - the selection, plus any typed extras, becomes the `services_covered` answer in the exact
 *     shape the backend's `service_scan.parse_selection` reads,
 *   - Phase 1 offers the selected pages as the internal cluster links; Phase 2 has no such field.
 */
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { PHASE2_ASSETS } from "../src/data/phase2Catalog";
import {
  INDUSTRY_BUCKET_FACT,
  NO_REFERENCE_MARKER,
  NO_REFERENCE_SCOPE,
  composeServicesAnswer,
  noReferenceAnswer,
} from "../src/pipeline/pipelineData";
import { usePipelineStore } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};
const flush = async () => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

const SERVICES = [
  { name: "Commercial Loans", url: "https://www.example.com.au/commercial-loans", parent: "" },
  { name: "Asset Finance", url: "https://www.example.com.au/asset-finance", parent: "Business Loans" },
  { name: "Truck Loans", url: "https://www.example.com.au/truck-loans", parent: "Business Loans" },
];
const scanCalls: Record<string, unknown>[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.endsWith("/api/pipeline/services/scan")) {
    const body = JSON.parse(String(init?.body ?? "{}"));
    scanCalls.push(body);
    return new Response(JSON.stringify({ source_url: body.url, services: SERVICES, notes: [] }), { status: 200 });
  }
  return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 500 });
}) as typeof fetch;

function seed(phase: "phase1" | "phase2", asset: (typeof ASSET_BY_ID)[string], profile: Record<string, string>) {
  const full = {
    started: true,
    isLoadingSession: false,
    phase,
    runId: `run-${phase}`,
    sessionId: null,
    messages: [],
    context: {},
    currentIndex: 0,
    intake: { asset, answers: {}, awaitingFieldId: "client_name" },
    subStep: null,
    activeStatus: "running",
    progress: 5,
    navStatus: "Awaiting Input" as const,
    clientProfile: { [INDUSTRY_BUCKET_FACT]: "regulated_finance", ...profile },
    editSeed: null,
    fault: null,
  };
  usePipelineStore.setState(full as never);
  Object.assign(usePipelineStore.getInitialState(), full);
}

const last = (kind: PipelineMessage["kind"]) =>
  [...usePipelineStore.getState().messages].reverse().find((m) => m.kind === kind && !m.superseded);
const answers = () => (usePipelineStore.getState().intake?.answers ?? {}) as Record<string, unknown>;
const awaiting = () => usePipelineStore.getState().intake?.awaitingFieldId;

async function main() {
  // ---------- the answer's shape is the one the backend reads ----------
  const composed = composeServicesAnswer(
    [{ name: "Commercial Loans", url: "https://www.example.com.au/commercial-loans" }, { name: "Asset Finance", url: "https://www.example.com.au/asset-finance" }],
    "Invoice Finance, Truck Loans",
  );
  ok(
    "the answer matches backend/tests/test_service_scan.py's CARD_ANSWER",
    composed ===
      "- Commercial Loans — https://www.example.com.au/commercial-loans\n- Asset Finance — https://www.example.com.au/asset-finance\n- Other (typed by the operator): Invoice Finance, Truck Loans",
    composed,
  );
  ok("the no-reference answer starts with the marker", noReferenceAnswer("https://x.com.au/").startsWith(NO_REFERENCE_MARKER));

  // ---------- Phase 1: no reference page ----------
  const PILLAR = ASSET_BY_ID.pillar_page;
  seed("phase1", PILLAR, { website_url: "https://www.example.com.au/", region: "Australia", target_service: "Asset Finance" });
  usePipelineStore.getState().submitAnswer("Example Finance");
  await flush();
  ok("the walk reaches the reference question", awaiting() === "reference_design_source", String(awaiting()));
  const reference = last("question");
  ok("the reference question offers the no-reference path", !!reference?.referenceSource && !reference.referenceSource.landing);

  usePipelineStore.getState().declareNoReferencePage(reference!.id);
  const landing = last("question");
  ok("it asks for the main landing page", landing?.field?.label === "Main landing page URL" && !!landing.referenceSource?.landing);
  ok("the client's own site is offered in one click", landing?.referenceSource?.suggestedUrl === "https://www.example.com.au/", landing?.referenceSource?.suggestedUrl);

  usePipelineStore.getState().submitAnswer("not a url");
  await flush();
  ok("a non-URL is refused and the question stays open", awaiting() === "reference_design_source" && !last("question")?.answered);

  usePipelineStore.getState().chooseLandingPage(landing!.id, "https://www.example.com.au/");
  await flush();
  const ref = String(answers().reference_design_source ?? "");
  ok("the reference is the no-reference sentinel naming the landing page", ref.startsWith(NO_REFERENCE_MARKER) && ref.endsWith("https://www.example.com.au/"), ref);
  ok("the scope is answered without asking", answers().reference_design_scope === NO_REFERENCE_SCOPE);
  ok("the scope question was never shown", !usePipelineStore.getState().messages.some((m) => m.kind === "question" && m.field?.field_id === "reference_design_scope"));

  const card = last("service-choice");
  ok("the service picker follows", !!card && awaiting() === "services_covered", String(awaiting()));
  ok("the landing page is what was scanned", scanCalls[0]?.url === "https://www.example.com.au/" && scanCalls[0]?.phase === "phase1", JSON.stringify(scanCalls[0]));
  ok("Phase 1 focuses the scan on the run's service", scanCalls[0]?.focus === "Asset Finance");
  ok("the scan's services are on the card", card?.serviceScan?.status === "ready" && card.serviceScan.services.length === 3, card?.serviceScan?.status);
  ok("the run's own service is pre-ticked", JSON.stringify(card?.serviceScan?.preselected) === JSON.stringify(["https://www.example.com.au/asset-finance"]));

  usePipelineStore.getState().confirmServices(card!.id, [SERVICES[0], SERVICES[1]], "Invoice Finance");
  await flush();
  const covered = String(answers().services_covered ?? "");
  ok("the selection is the answer", covered.includes("- Commercial Loans — https://www.example.com.au/commercial-loans") && covered.includes("Other (typed by the operator): Invoice Finance"), covered);
  ok("the card becomes history", last("service-choice")?.serviceScan?.status === "done");
  ok("the walk moves past the services", awaiting() !== "services_covered", String(awaiting()));
  ok(
    "Phase 1 offers the selected pages as the internal cluster links",
    String(answers().internal_cluster_pages_to_link_optional ?? "").includes("https://www.example.com.au/asset-finance — Asset Finance"),
    String(answers().internal_cluster_pages_to_link_optional),
  );

  // ---------- Phase 2: a real reference page, sub-services ----------
  scanCalls.length = 0;
  const PILLAR2 = PHASE2_ASSETS.pillar_page;
  ok("Phase 2's pillar page carries the field", PILLAR2.fields.some((f) => f.field_id === "services_covered"));
  seed("phase2", PILLAR2, { website_url: "https://www.example.com.au/", region: "Australia", target_service: "Business Loans", sub_service: "Truck Loans" });
  usePipelineStore.getState().submitAnswer("Example Finance");
  await flush();
  usePipelineStore.getState().submitAnswer("https://www.example.com.au/business-loans/");
  await flush();
  ok("a typed URL is kept as the reference", answers().reference_design_source === "https://www.example.com.au/business-loans/");
  ok("and the scope is still asked", awaiting() === "reference_design_scope", String(awaiting()));
  usePipelineStore.getState().submitAnswer("THIS ONE PAGE ONLY");
  await flush();
  const card2 = last("service-choice");
  ok("Phase 2 scans the reference page for sub-services", scanCalls[0]?.url === "https://www.example.com.au/business-loans/" && scanCalls[0]?.phase === "phase2", JSON.stringify(scanCalls[0]));
  ok("Phase 2 focuses on the sub-service", scanCalls[0]?.focus === "Truck Loans");
  ok("the sub-service is pre-ticked", JSON.stringify(card2?.serviceScan?.preselected) === JSON.stringify(["https://www.example.com.au/truck-loans"]));

  usePipelineStore.getState().skipServices(card2!.id);
  await flush();
  ok("skipping answers NONE and moves on", answers().services_covered === "NONE" && awaiting() !== "services_covered");
  ok("Phase 2 has no cluster-links field to fill", !("internal_cluster_pages_to_link_optional" in answers()));

  console.log(`services: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
