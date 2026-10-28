/* The Plan of Action is Phase 1's last stage, and it is where Phase 2's sub-services are chosen.
 * Not shipped; see smoke/README.md.
 *
 * Proven here, driving the store's real actions with `fetch` stubbed:
 *   - Phase 1 builds the plan last and Phase 2 has no plan stage,
 *   - the plan's Phase 1 input reads the server-composed delivery record, and nothing asks about
 *     Phase 2 work already built,
 *   - reaching the sub-service field opens the discovery card (not a text question), with the
 *     client's site, primary service and region, and every suggestion pre-ticked,
 *   - confirming writes the answer in `composePlanSubServicesAnswer`'s shape and files the same text
 *     on the run as `plan_sub_services`; skipping tells the plan to infer them,
 *   - a Phase 2 run built on that run opens its sub-service question on the planned names first.
 */
import { ASSET_BY_ID, PLAN_COMPOSED_KEYS } from "../src/data/assetCatalog";
import {
  PLAN_INFER_SUB_SERVICES,
  PLAN_SUB_SERVICES_KEY,
  PLAN_SUB_SERVICE_OPTION,
  SUB_SERVICE_FIELD,
  composePlanSubServicesAnswer,
  planSubServiceNames,
  stagesFor,
} from "../src/pipeline/pipelineData";
import type { PlanSubServiceCandidate } from "../src/pipeline/pipelineApi";
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
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
};

const CANDIDATES: PlanSubServiceCandidate[] = [
  {
    name: "Meta Ads",
    status: "existing",
    url: "https://trafficradius.com.au/meta-ads/",
    slug: "/meta-ads/",
    parent: "Social Media Marketing",
    why: "",
    keywords: [
      { keyword: "meta ads agency", volume: 880 },
      { keyword: "facebook ads agency", volume: 590 },
    ],
    combined_volume: 1470,
  },
  {
    name: "LinkedIn Ads",
    status: "existing",
    url: "https://trafficradius.com.au/linkedin-ads/",
    slug: "/linkedin-ads/",
    parent: "Social Media Marketing",
    why: "",
    keywords: [{ keyword: "linkedin ads agency", volume: 210 }],
    combined_volume: 210,
  },
  {
    name: "TikTok Ads",
    status: "new",
    url: "",
    slug: "/tiktok-ads/",
    parent: "",
    why: "Two competitors have a page.",
    keywords: [{ keyword: "tiktok ads agency", volume: null }],
    combined_volume: null,
  },
];

const discoverCalls: Record<string, unknown>[] = [];
let volumeError = "";
const filed: { url: string; body: Record<string, unknown> }[] = [];
let storedPlan = "";
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  if (url.endsWith("/api/pipeline/plan/sub-services/discover")) {
    discoverCalls.push(body);
    return new Response(
      JSON.stringify({
        source_url: body.url,
        primary_service: body.primary_service,
        candidates: CANDIDATES,
        volume_source: volumeError ? "unavailable" : "dataforseo",
        location: "Australia",
        volume_error: volumeError,
        notes: [],
      }),
      { status: 200 },
    );
  }
  if (url.endsWith("/plan-sub-services")) {
    filed.push({ url, body });
    return new Response(JSON.stringify({ run_id: "run-p1", names: [] }), { status: 200 });
  }
  if (url.endsWith(`/runs/run-p1/context/${PLAN_SUB_SERVICES_KEY}`)) {
    if (!storedPlan) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify({ run_id: "run-p1", context_key: PLAN_SUB_SERVICES_KEY, version: 1, content: storedPlan }), { status: 200 });
  }
  if (url.endsWith("/api/pipeline/runs") && init?.method === "POST") {
    return new Response(JSON.stringify({ run_id: "run-p2", status: "created" }), { status: 200 });
  }
  return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 });
}) as typeof fetch;

const PLAN = ASSET_BY_ID.plan_of_action;

/** Every plan field before the sub-services, answered, so the walk's next stop is the picker. */
function seedPlanIntake() {
  const answers: Record<string, unknown> = {
    client_name: "Traffic Radius",
    client_website_url: "https://trafficradius.com.au/",
    industry_niche: "Digital marketing",
    region_country: "Australia",
    prepared_by_agency_name_optional: "Dotmappers",
    plan_date_optional: "October 2026",
    primary_service_being_expanded: "Social Media Marketing",
    existing_phase_1_assets_optional: "Built and approved in Phase 1 …",
  };
  const full = {
    started: true,
    isLoadingSession: false,
    phase: "phase1" as const,
    runId: "run-p1",
    sessionId: null,
    messages: [],
    context: {},
    currentIndex: stagesFor("phase1").length - 1,
    intake: { asset: PLAN, answers, awaitingFieldId: "primary_pillar_page_existing_or_target_url" },
    subStep: null,
    activeStatus: "running",
    progress: 50,
    navStatus: "Awaiting Input" as const,
    clientProfile: { client_name: "Traffic Radius", website_url: "https://trafficradius.com.au/", region: "Australia" },
    editSeed: null,
    fault: null,
  };
  usePipelineStore.setState(full as never);
}

const last = (kind: PipelineMessage["kind"]) =>
  [...usePipelineStore.getState().messages].reverse().find((m) => m.kind === kind && !m.superseded);
const answers = () => (usePipelineStore.getState().intake?.answers ?? {}) as Record<string, unknown>;
const awaiting = () => usePipelineStore.getState().intake?.awaitingFieldId;

async function main() {
  // ---------- where the plan sits ----------
  const phase1 = stagesFor("phase1").map((s) => s.asset.asset_id);
  ok("Phase 1 builds the plan last", phase1[phase1.length - 1] === "plan_of_action", phase1.join(","));
  ok("Phase 2 has no plan stage", !stagesFor("phase2").some((s) => s.asset.asset_id === "plan_of_action"));

  // ---------- what the plan reads ----------
  const phase1Field = PLAN.fields.find((f) => f.field_id === "existing_phase_1_assets_optional");
  ok("Phase 1's work is the composed delivery record", phase1Field?.context_key === "plan_phase1_assets" && !!PLAN_COMPOSED_KEYS.plan_phase1_assets);
  ok("nothing asks about Phase 2 work already built", !PLAN.fields.some((f) => f.field_id === "phase_2_sub_services_already_built_optional"));
  ok("no composed key reads sub-service runs", !("plan_phase2_assets" in PLAN_COMPOSED_KEYS));

  // ---------- the answer's shape ----------
  const composed = composePlanSubServicesAnswer(CANDIDATES.slice(0, 1).concat(CANDIDATES[2]), "Pinterest Ads", {
    volumeSource: "dataforseo",
    location: "Australia",
  });
  ok(
    "the answer states the source, then one line per sub-service",
    composed ===
      "Volume source: DataForSEO, monthly Google searches in Australia.\n" +
        "- Meta Ads — EXISTING PAGE — enhance: https://trafficradius.com.au/meta-ads/ — keywords: meta ads agency (880/mo), facebook ads agency (590/mo) — combined 1,470/mo\n" +
        "- TikTok Ads — NEW PAGE — proposed slug: /tiktok-ads/ — keywords: tiktok ads agency (not measured)\n" +
        "- Pinterest Ads — NEW PAGE (typed by the operator; keywords not measured)",
    composed,
  );
  ok("the names read back in order", JSON.stringify(planSubServiceNames(composed)) === JSON.stringify(["Meta Ads", "TikTok Ads", "Pinterest Ads"]));
  ok(
    "unmeasured volumes say so",
    composePlanSubServicesAnswer(CANDIDATES.slice(2), "", { volumeSource: "unavailable", location: "" }).startsWith("Volume source: not measured."),
  );

  // ---------- the walk reaches the picker ----------
  seedPlanIntake();
  usePipelineStore.getState().submitAnswer("https://trafficradius.com.au/social-media-marketing/");
  await flush();
  const card = last("service-choice");
  ok("the sub-service field opens the discovery card", !!card?.serviceScan?.plan && awaiting() === PLAN_SUB_SERVICE_OPTION.servicesFieldId, String(awaiting()));
  ok("no text question is asked for it", !usePipelineStore.getState().messages.some((m) => m.kind === "question" && m.field?.field_id === PLAN_SUB_SERVICE_OPTION.servicesFieldId));
  ok(
    "discovery is asked about the client's site, service and region",
    discoverCalls[0]?.url === "https://trafficradius.com.au/" &&
      discoverCalls[0]?.primary_service === "Social Media Marketing" &&
      discoverCalls[0]?.region === "Australia" &&
      discoverCalls[0]?.run_id === "run-p1",
    JSON.stringify(discoverCalls[0]),
  );
  ok("the suggestions are on the card", card?.serviceScan?.status === "ready" && card.serviceScan.plan?.candidates.length === 3, card?.serviceScan?.status);
  ok("every suggestion starts ticked", JSON.stringify(card?.serviceScan?.preselected) === JSON.stringify(CANDIDATES.map((c) => c.name)));
  ok("the volumes are marked measured", card?.serviceScan?.plan?.volumeSource === "dataforseo" && card.serviceScan.plan.location === "Australia");

  // ---------- confirming ----------
  usePipelineStore.getState().confirmPlanSubServices(card!.id, ["Meta Ads", "TikTok Ads"], "Pinterest Ads");
  await flush();
  const answer = String(answers()[PLAN_SUB_SERVICE_OPTION.servicesFieldId] ?? "");
  ok("the confirmed set is the answer", answer === composed, answer);
  ok("the same text is filed on the run", filed.length === 1 && filed[0].url.endsWith("/runs/run-p1/plan-sub-services") && filed[0].body.content === answer);
  ok("the card becomes history", last("service-choice")?.serviceScan?.status === "done");
  ok(
    "history lists what was planned",
    JSON.stringify(last("service-choice")?.serviceScan?.plan?.chosenNames) === JSON.stringify(["Meta Ads", "TikTok Ads", "Pinterest Ads"]),
  );
  ok("the walk moves on", awaiting() !== PLAN_SUB_SERVICE_OPTION.servicesFieldId, String(awaiting()));

  // ---------- skipping ----------
  seedPlanIntake();
  usePipelineStore.getState().submitAnswer("https://trafficradius.com.au/social-media-marketing/");
  await flush();
  usePipelineStore.getState().skipServices(last("service-choice")!.id);
  await flush();
  ok("skipping tells the plan to infer them", answers()[PLAN_SUB_SERVICE_OPTION.servicesFieldId] === PLAN_INFER_SUB_SERVICES);
  ok("a skip files nothing", filed.length === 1);

  // ---------- a provider failure stays on the card, with no notice raised elsewhere ----------
  volumeError = "DataForSEO could not fetch search volumes: DataForSEO rejected the login (401). The plan will label every volume as an estimate.";
  const before = usePipelineStore.getState().messages.length;
  seedPlanIntake();
  usePipelineStore.getState().submitAnswer("https://trafficradius.com.au/social-media-marketing/");
  await flush();
  const failed = last("service-choice");
  ok("the card keeps the reason", failed?.serviceScan?.plan?.volumeError === volumeError, JSON.stringify(failed?.serviceScan?.plan?.volumeError));
  ok("the list still stands", failed?.serviceScan?.status === "ready" && (failed.serviceScan.plan?.candidates.length ?? 0) === 3);
  ok(
    "no provider message is posted to the transcript",
    !usePipelineStore.getState().messages.slice(before).some((m) => m.kind === "text" && /could not fetch|unavailable/i.test(m.text ?? "")),
  );
  volumeError = "";

  // ---------- Phase 2 opens on the planned sub-services ----------
  storedPlan = answer;
  usePipelineStore.setState({
    started: false,
    phase: "phase2",
    runId: null,
    sourceRunId: "run-p1",
    messages: [],
    intake: null,
    clientProfile: { client_name: "Traffic Radius" },
  } as never);
  usePipelineStore.getState().start();
  await flush();
  const question = last("question");
  const choices = question?.field?.choices ?? [];
  ok("Phase 2 asks the sub-service", question?.field?.field_id === SUB_SERVICE_FIELD.field_id);
  ok(
    "the planned sub-services come first, in the plan's order",
    JSON.stringify(choices.slice(0, 3)) === JSON.stringify(["Meta Ads", "TikTok Ads", "Pinterest Ads"]),
    JSON.stringify(choices),
  );
  ok("the generic choices still follow", choices.includes("Google Ads") && choices.filter((c) => c === "Meta Ads").length === 1);
  ok("the hint says where they came from", /Plan of Action/.test(question?.field?.helpText ?? ""));

  console.log(`planPhase1: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
