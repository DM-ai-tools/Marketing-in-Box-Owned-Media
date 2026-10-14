/* The "Ahrefs keyword data" section at the end of an asset. Not shipped; see smoke/README.md.
 *
 * Proven here, from transcripts built the way the store builds them:
 *   - an asset gets the section when Ahrefs fed it (the run's demand map, its chosen topics, the
 *     plan's sub-service sizing) and no section when it did not, or when the stub provider answered,
 *   - the section says how each piece was used and tables what was measured, with the country,
 *   - a Phase 2 track never picks up Phase 1's demand map,
 *   - it is the last thing in the appendix, and the document parser sees it as its own section,
 *     which the at-a-glance picture skips.
 */
import { AHREFS_USAGE_LABEL, ahrefsAppendixFor, ahrefsUsageFor } from "../src/lib/ahrefsUsage";
import { assetAppendixFor } from "../src/lib/assetAppendix";
import { parseAssetDocument } from "../src/lib/assetDocument";
import type { HeadlineCandidate, KeywordReportResult, PlanSubServiceCandidate } from "../src/pipeline/pipelineApi";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

let seq = 0;
const msg = (m: Partial<PipelineMessage>): PipelineMessage =>
  ({ id: `m${++seq}`, role: "assistant", kind: "text", createdAt: seq, phase: "phase1", ...m }) as PipelineMessage;

const report = (provider: string, extra: Partial<KeywordReportResult> = {}): KeywordReportResult => ({
  run_id: "r",
  status: "built",
  phase: "phase1",
  service: "Social Media Marketing",
  provider,
  version: 1,
  total_keywords: 213,
  clusters: [
    { name: "Meta ads agency", intent: "commercial", funnel: "BOFU", content_type: null, recommended_content: null, recommended_url: null, primary_keyword: "meta ads agency", keyword_count: 14, total_volume: 1470 },
    { name: "Social media agency", intent: "commercial", funnel: "MOFU", content_type: null, recommended_content: null, recommended_url: null, primary_keyword: "social media marketing agency", keyword_count: 22, total_volume: 2400 },
  ],
  warnings: [],
  location: "Australia",
  ...extra,
});

const candidate = (id: string, headline: string, keyword: string, volume: number | null): HeadlineCandidate =>
  ({
    id,
    headline,
    primary_keyword: keyword,
    source_cluster: "Meta ads agency",
    intent: "commercial",
    funnel: "BOFU",
    traffic_temperature: null,
    framework_formula: null,
    curiosity_elements: [],
    specificity: null,
    why_it_works: null,
    trend_evidence: null,
    char_count: headline.length,
    channel_limit_ok: true,
    checklist_pass: true,
    checklist_notes: "",
    search_volume: volume,
    difficulty: 0,
    grounded: true,
    extras: {},
  }) as HeadlineCandidate;

const gate = (assetId: string, grounded: boolean) =>
  msg({
    kind: "headline-choice",
    assetId,
    field: { field_id: "topic", label: "Topic", kind: "headline_choice", required: true, source: "user_input" },
    headlines: {
      slot: "topic",
      status: "chosen",
      groundedInKeywords: grounded,
      candidates: [
        candidate("a", "How Much Should You Spend on Meta Ads?", "meta ads agency", 400),
        candidate("b", "Meta Ads vs Google Ads", "meta ads vs google ads", 90),
      ],
      chosenIds: ["a"],
    },
  });

const generation = (assetId: string, extra: Partial<PipelineMessage> = {}) =>
  msg({ kind: "generation", assetId, text: "# A deliverable\n\n## Part one\n\nBody.\n\n## Part two\n\nMore body.", savePhase: "saved", ...extra });

// ---------- a blog built on the demand map and a grounded topic ----------
{
  const blog = generation("blog");
  const messages = [msg({ text: "Mapped search demand…", keywordReport: report("ahrefs") }), gate("blog", true), blog];
  const section = ahrefsAppendixFor(messages, blog);
  ok("the section is added", section.includes(`## ${AHREFS_USAGE_LABEL}`), section.slice(0, 200));
  ok("it names the country", section.includes("monthly Google searches in Australia"));
  ok("it says how the demand map was made", section.includes("`matching-terms`, `related-terms` and `search-suggestions`") && section.includes("213 keywords were grouped into 2 topic clusters"));
  ok("it says the topic came from that map", section.includes("**Topic choice.** The topic this asset is built on was suggested from that map"));
  ok("clusters are listed by volume", section.indexOf("Social media agency") < section.indexOf("Meta ads agency |"), section);
  ok("the chosen topic is tabled with its keyword and volume", section.includes("| How Much Should You Spend on Meta Ads? | meta ads agency | 400 | 0 | Meta ads agency |"));
  ok("an unchosen topic is not", !section.includes("Meta Ads vs Google Ads"));

  const appendix = assetAppendixFor(messages, blog);
  ok("it is the last section of the appendix", appendix.trimEnd().endsWith(section.trimEnd()));
  const doc = parseAssetDocument(blog.text + appendix);
  ok("the parser sees it as its own section", doc.sections.some((s) => s.label === AHREFS_USAGE_LABEL), doc.sections.map((s) => s.label).join(" / "));
}

// ---------- no Ahrefs, no section ----------
{
  const blog = generation("blog");
  ok("the stub provider's data is never reported", ahrefsAppendixFor([msg({ keywordReport: report("stub") }), gate("blog", false), blog], blog) === "");
  ok("an asset with nothing from Ahrefs gets nothing", ahrefsAppendixFor([blog], blog) === "");
  ok("a skipped prepass is not a demand map", ahrefsAppendixFor([msg({ keywordReport: report("ahrefs", { status: "skipped" }) }), blog], blog) === "");
  const later = msg({ keywordReport: report("ahrefs") });
  ok("a map made after the asset is not claimed by it", ahrefsAppendixFor([blog, later], blog) === "");
}

// ---------- Phase 2 does not inherit Phase 1's map ----------
{
  const p2 = generation("blog", { phase: "phase2", trackId: "run-b" });
  const messages = [msg({ keywordReport: report("ahrefs") }), p2];
  ok("a Phase 2 asset ignores Phase 1's demand map", ahrefsUsageFor(messages, p2) === null);
  const own = msg({ phase: "phase2", trackId: "run-b", keywordReport: report("ahrefs", { service: "Meta Ads" }) });
  ok("it uses its own track's", ahrefsUsageFor([messages[0], own, p2], p2)?.demand?.service === "Meta Ads");
}

// ---------- the Plan of Action's sub-service sizing ----------
{
  const candidates: PlanSubServiceCandidate[] = [
    { name: "Meta Ads", status: "existing", url: "https://trafficradius.com.au/meta-ads/", slug: "/meta-ads/", parent: "", why: "", keywords: [{ keyword: "meta ads agency", volume: 400 }, { keyword: "facebook ads agency", volume: 900 }], combined_volume: 1300 },
    { name: "TikTok Ads", status: "new", url: "", slug: "/tiktok-ads/", parent: "", why: "", keywords: [{ keyword: "tiktok ads agency", volume: null }], combined_volume: null },
    { name: "Snapchat Ads", status: "new", url: "", slug: "/snapchat-ads/", parent: "", why: "", keywords: [{ keyword: "snapchat ads", volume: 50 }], combined_volume: 50 },
  ];
  const card = msg({
    kind: "service-choice",
    assetId: "plan_of_action",
    serviceScan: {
      status: "done",
      url: "https://trafficradius.com.au/",
      services: [],
      preselected: [],
      notes: [],
      plan: { primaryService: "Social Media Marketing", region: "Australia", candidates, volumeSource: "ahrefs", location: "Australia", chosenNames: ["Meta Ads", "TikTok Ads"] },
    },
  });
  const plan = generation("plan_of_action");
  const section = ahrefsAppendixFor([card, plan], plan);
  ok("the plan gets the sizing", section.includes("**Sub-service sizing.** One Ahrefs `overview` request measured 2 keywords across the 2 sub-services"), section);
  ok("each confirmed sub-service is tabled", section.includes("| Meta Ads | existing page (https://trafficradius.com.au/meta-ads/) | meta ads agency (400), facebook ads agency (900) | 1,300 |"));
  ok("an unmeasured volume shows a dash, not 0", section.includes("| TikTok Ads | new (/tiktok-ads/) | tiktok ads agency (—) | — |"));
  ok("an unconfirmed one is not", !section.includes("Snapchat"));
  const unmeasured = msg({ ...card, id: "x", serviceScan: { ...card.serviceScan!, plan: { ...card.serviceScan!.plan!, volumeSource: "unavailable" } } });
  ok("a plan whose volumes failed claims no sizing", ahrefsAppendixFor([unmeasured, plan], plan) === "");
  ok("only the plan reads the sub-service card", ahrefsAppendixFor([card, generation("blog")], generation("blog")) === "");
}

console.log(`ahrefsUsage: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL", f);
if (fails.length) process.exit(1);
