/* Phase 2's own ICP. Not shipped; see smoke/README.md.
 *
 * Phase 2 builds its ICP for the sub-service instead of inheriting Phase 1's. Three things are
 * proven here:
 *   - a Phase 2 leg starts without Phase 1's audience documents in its context,
 *   - the ICP asks the audience's industry fresh, even when Phase 1 answered it, and files the answer
 *     against this run only,
 *   - later Phase 2 stages reuse that answer, not Phase 1's, and each track keeps its own.
 * The ICP walk goes through the store's real actions, with `fetch` stubbed.
 */
import { findNextAskable } from "../src/lib/fieldResolution";
import { PHASE2_ASSETS, phase2StartingContext } from "../src/data/phase2Catalog";
import {
  FIELD_TO_FACT_BY_PHASE,
  INDUSTRY_BUCKET_FACT,
  PHASE2_AUDIENCE_INDUSTRY_FACT,
  phase2AudienceIndustryKey,
  stagesFor,
} from "../src/pipeline/pipelineData";
import { usePipelineStore } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};
globalThis.fetch = (async () =>
  new Response(JSON.stringify({ detail: "not stubbed" }), { status: 500 })) as typeof fetch;
const flush = async () => {
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
};

const ICP = PHASE2_ASSETS.icp;
const LEAD = PHASE2_ASSETS.lead_magnet;
const PHASE1_PROFILE = {
  client_name: "Acme",
  website_url: "acme.com",
  region: "Australia",
  industry: "Construction", // Phase 1's audience
  sub_service: "Meta Ads",
  [INDUSTRY_BUCKET_FACT]: "marketing_agency", // so the client-industry question does not interject
};

function seed(patch: Record<string, unknown>) {
  const base = {
    started: true,
    isLoadingSession: false,
    phase: "phase2" as const,
    runId: "run-meta",
    sessionId: null,
    messages: [],
    context: {},
    currentIndex: 0,
    intake: null,
    subStep: null,
    activeStatus: "running",
    progress: 5,
    navStatus: "Awaiting Input" as const,
    clientProfile: { ...PHASE1_PROFILE },
    editSeed: null,
    fault: null,
  };
  const full = { ...base, ...patch };
  usePipelineStore.setState(full as never);
  Object.assign(usePipelineStore.getInitialState(), full);
}

async function main() {
  // ---------- the leg starts without Phase 1's audience ----------
  ok("icp is Phase 2's stage 01", stagesFor("phase2")[0].asset.asset_id === "icp");
  const start = phase2StartingContext({ icp: 1, offers: 2, offer_ladder: 3, cro: 4, brand_design_tokens: 5 });
  ok("Phase 1's ICP and value ladder are not carried in", !("icp" in start) && !("offers" in start) && !("offer_ladder" in start), JSON.stringify(start));
  ok("everything else is", "cro" in start && "brand_design_tokens" in start);

  // ---------- the ICP asks the audience's industry fresh ----------
  const industryIndex = ICP.fields.findIndex((f) => f.field_id === "industry");
  const before = ICP.fields[industryIndex - 1];
  seed({ intake: { asset: ICP, answers: {}, awaitingFieldId: before.field_id } });
  usePipelineStore.getState().submitAnswer(before.choices?.[0] ?? "answer");
  await flush();
  let s = usePipelineStore.getState();
  // The walk may stop earlier on a field it had not reached; answer through until it moves on.
  for (let guard = 0; guard < 20 && s.intake?.awaitingFieldId && s.intake.awaitingFieldId !== "industry"; guard++) {
    const f = ICP.fields.find((x) => x.field_id === s.intake?.awaitingFieldId);
    usePipelineStore.getState().submitAnswer(f?.choices?.[0] ?? "answer");
    await flush();
    s = usePipelineStore.getState();
  }
  ok("the Phase 2 ICP asks the audience industry despite Phase 1's answer", s.intake?.awaitingFieldId === "industry", String(s.intake?.awaitingFieldId));
  ok("the sub-service is not asked", !s.messages.some((m) => m.kind === "question" && m.field?.field_id === "service_product_price_terms"));

  usePipelineStore.getState().submitAnswer("E-commerce retailers");
  await flush();
  for (let guard = 0; guard < 20 && usePipelineStore.getState().intake?.awaitingFieldId; guard++) {
    const f = ICP.fields.find((x) => x.field_id === usePipelineStore.getState().intake?.awaitingFieldId);
    usePipelineStore.getState().submitAnswer(f?.choices?.[0] ?? "answer");
    await flush();
  }
  s = usePipelineStore.getState();
  ok("the answer is filed against this run", s.clientProfile[phase2AudienceIndustryKey("run-meta")] === "E-commerce retailers", JSON.stringify(s.clientProfile));
  ok("Phase 1's audience industry is left alone", s.clientProfile.industry === "Construction");
  ok("another track's key is untouched", s.clientProfile[phase2AudienceIndustryKey("run-google")] === undefined);

  // ---------- later stages reuse this run's answer, not Phase 1's ----------
  const facts = (audience?: string) => ({
    values: { ...PHASE1_PROFILE, ...(audience ? { [PHASE2_AUDIENCE_INDUSTRY_FACT]: audience } : {}) },
    fieldToFact: FIELD_TO_FACT_BY_PHASE.phase2,
  });
  const leadIndustry = findNextAskable(LEAD, {}, {}, 0, facts("E-commerce retailers"));
  ok(
    "Lead Magnet reuses the Phase 2 audience",
    leadIndustry.autoKnownFields.some((f) => f.fieldId === "industry"),
    JSON.stringify(leadIndustry.autoKnownFields),
  );
  const answersSeen: Record<string, unknown> = {};
  const walked = findNextAskable(LEAD, {}, answersSeen, 0, facts());
  ok(
    "Lead Magnet never falls back to Phase 1's audience",
    !walked.autoKnownFields.some((f) => f.fieldId === "industry"),
    JSON.stringify(walked.autoKnownFields),
  );

  console.log(`phase2icp: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log(`  FAIL ${f}`);
  if (fails.length) process.exitCode = 1;
  setTimeout(() => process.exit(process.exitCode ?? 0), 0);
}

void main();
