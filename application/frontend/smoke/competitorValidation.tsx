/* Competitor validation: every competitor listing scored for this client. Not shipped; see README.
 *
 * Drives the real store with `fetch` stubbed:
 *   - a listing asks for its validation with the client's site, region and service;
 *   - the card shows the verdicts, the three axes, what to take, how to prove it, the evidence with
 *     where each number came from, the plays across the market and how it was checked;
 *   - approving a ticked subset carries only those competitors' ranking into the stage's prompt;
 *   - the deliverable's "Competitors scanned" appendix carries each verdict.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { CompetitorCard } from "../src/pipeline/CompetitorCard";
import { usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import type { CompetitorAnalysisResult, CompetitorValidation } from "../src/pipeline/pipelineApi";
import { defaultPicks, validationMarkdownFor } from "../src/lib/competitorValidation";
import { competitorAppendixFor } from "../src/lib/competitorSources";
import { ScannedKeywords } from "../src/pipeline/ScannedKeywords";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const row = (rank: number, name: string, domain: string, category: string) => ({
  rank, name, domain, page_url: `https://${domain}/offer/`, verification_confidence: "Verified", offering_summary: `${name} offer`, category,
});
const LISTING: CompetitorAnalysisResult = {
  asset_id: "competitor_analysis_lead_magnet",
  target_url: "https://client.com.au/",
  location: "Melbourne, Australia",
  requested_count: 10,
  returned_count: 2,
  competitors: [row(1, "Alpha", "alpha.com.au", "Free audit"), row(2, "Beta", "beta.com.au", "Case study library")],
  notes: "Two qualified.",
};

const metric = (key: string, label: string, value: string, score: number | null, source: "measured" | "judged" | "unavailable", detail = "") =>
  ({ key, label, value, score, source, detail });
const REPORT: CompetitorValidation = {
  asset_id: "competitor_analysis_lead_magnet",
  location: "Australia",
  client_traffic: 40,
  client_top10: 1,
  client_page_count: 6,
  client_keyword_count: 1,
  client_keywords: [{ keyword: "client brand name", position: 1, volume: 40 }],
  scanned_limit: 50,
  competitors: [
    {
      rank: 1, name: "Alpha", domain: "alpha.com.au", page_url: "https://alpha.com.au/offer/", play: "Free audit",
      proof: 82, fit: 88, feasibility: 100, total: 88, verdict: "implement_now", verdict_reason: "Proven for them and close to this client's service and buyer.",
      metrics: [
        metric("traffic", "Organic visits / month", "1,520", 80, "measured", "vs the client's 40"),
        metric("level", "Business level", "Above this client", 70, "measured", "38.0× the client's organic visits"),
        metric("pages", "Number of pages", "42", 40, "measured", "client has 6"),
        metric("keywords", "Ranking keywords", "3", 20, "measured"),
        metric("buyer", "Fits the client's buyer", "9/10", 90, "judged", 'Speaks to the retail owner. — "Owners of Melbourne retail stores"'),
        metric("overlap", "Ranks for the client's keywords", "—", null, "unavailable"),
      ],
      blockers: [],
      what_to_take: "A free 10-point audit",
      how_to_prove: "Opt-in rate ≥ 25% within 30 days",
      top_keywords: [{ keyword: "social media marketing audit", position: 3, volume: 500 }],
      business_level: "above", page_count: 42, keyword_count: 3,
      scanned_keywords: [
        { keyword: "social media marketing audit", position: 3, volume: 500, url: "https://alpha.com.au/offer/" },
        { keyword: "free instagram audit", position: 14, volume: 90, url: "https://alpha.com.au/offer/" },
      ],
    },
    {
      rank: 2, name: "Beta", domain: "beta.com.au", page_url: "https://beta.com.au/offer/", play: "Case study library",
      proof: 40, fit: 50, feasibility: 60, total: 48, verdict: "avoid", verdict_reason: "The client's own settings rule it out (see blockers).",
      metrics: [metric("traffic", "Organic visits / month", "315", 63, "measured")],
      blockers: [{ reason: "Relies on case studies; the client's settings say testimonials are NO.", source: "rule", quote: "case studies" }],
      what_to_take: "", how_to_prove: "", top_keywords: [],
      business_level: "above", page_count: 12, keyword_count: 2,
    },
  ],
  plays: [
    { play: "Free audit", count: 1, share: 0.5, combined_traffic: 1520, average_fit: 88, verdict: "implement_now", competitors: ["Alpha"] },
    { play: "Case study library", count: 1, share: 0.5, combined_traffic: 315, average_fit: 50, verdict: "avoid", competitors: ["Beta"] },
  ],
  market_gaps: ["No competitor offers a post-by-post sales report."],
  sources: ["DataForSEO ranked keywords (Google, Australia), measured 2026-10-21"],
  notes: [],
  judge: "ok",
  dropped_quotes: 0,
  markdown: [
    "## Competitor validation — ranked for this client",
    "| # | Competitor | Play | Verdict | Fit score | Proof | Fit | Feasibility |",
    "| --- | --- | --- | --- | --- | --- | --- | --- |",
    "| 1 | Alpha | Free audit | Implement now | 88 | 82 | 88 | 100 |",
    "| 2 | Beta | Case study library | Avoid | 48 | 40 | 50 | 60 |",
    "- **Alpha — Implement now.** Take: A free 10-point audit Prove it: Opt-in rate ≥ 25% within 30 days",
    "- **Beta — blocker:** Relies on case studies; the client's settings say testimonials are NO.",
    "Gaps no competitor fills:",
    "- No competitor offers a post-by-post sales report.",
  ].join("\n"),
};

const requests: { url: string; body: Record<string, unknown> }[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
  requests.push({ url, body });
  if (url.endsWith("/validate")) return new Response(JSON.stringify(REPORT), { status: 200 });
  if (url.includes("/competitor/") && url.endsWith("/save")) {
    return new Response(JSON.stringify({ run_id: "run-1", asset_id: "competitor_analysis_lead_magnet", analysis_id: "a", competitor_count: 1, version: 1, saved_at: "", context_text: "saved" }), { status: 200 });
  }
  if (url.includes("/context/")) return new Response("{}", { status: 404 });
  return new Response("{}", { status: 200 });
}) as typeof fetch;

const card: PipelineMessage = {
  id: "comp-1",
  role: "assistant",
  kind: "competitor",
  assetId: "competitor_analysis_lead_magnet",
  phase: "phase1",
  createdAt: 1,
  savePhase: "idle",
  competitor: LISTING,
} as unknown as PipelineMessage;

usePipelineStore.setState({
  started: true,
  phase: "phase1",
  runId: "run-1",
  sessionId: "s-1",
  messages: [card],
  clientProfile: { region: "Melbourne, Australia", website_url: "https://client.com.au/", target_service: "Social Media Marketing" },
} as never);

// Before the check runs, the card offers it rather than showing nothing.
ok("an unchecked listing offers the check", renderToStaticMarkup(<CompetitorCard message={card} />).includes("Check which of these would work for this client"));

usePipelineStore.getState().validateCompetitorStep("comp-1");
await new Promise((r) => setTimeout(r, 20));

const req = requests.find((r) => r.url.endsWith("/competitor/competitor_analysis_lead_magnet/validate"));
ok("the listing is sent for validation", !!req && (req.body.competitors as unknown[]).length === 2, JSON.stringify(req?.body));
ok("with the client's site, region and run", req?.body.target_url === "https://client.com.au/" && req?.body.location === "Melbourne, Australia" && req?.body.run_id === "run-1");
let state = usePipelineStore.getState();
let msg = state.messages.find((m) => m.id === "comp-1")!;
ok("the report is on the card", msg.competitorValidation?.status === "ready" && msg.competitorValidation.report?.competitors.length === 2);

const html = renderToStaticMarkup(<CompetitorCard message={msg} />);
ok("the card asks the question", html.includes("Will it work for this client?"));
ok("verdict tags show, but Avoid is not tagged (operator's request)", html.includes("Implement now") && !html.includes(">Avoid<"));
ok("the fit score and the three axes are shown", html.includes("Fit score 88 of 100") && html.includes("Proof") && html.includes("Feasibility"));
ok("the client's own baseline is shown", html.includes("Baseline, the client&#x27;s own site"));
ok("business level, page count and keywords are on the listing", html.includes("Above this client") && html.includes("Number of pages: 42") && html.includes("social media marketing audit (#3)"));
ok(
  "hovering ranking keywords lists every keyword scanned, with position and searches",
  html.includes("2 keywords scanned of 3") && html.includes("free instagram audit") && html.includes("#14") && html.includes("The top 50 by estimated traffic"),
  html.slice(html.indexOf("keywords scanned") - 200, html.indexOf("keywords scanned") + 400),
);
ok("the evidence row's label opens the same list", (html.match(/data-scanned-keywords/g) ?? []).length >= 3);
ok("the client's baseline lists its own scanned keywords", html.includes("client brand name"));
ok(
  "a competitor scored before the list was kept shows plain text, no popover",
  renderToStaticMarkup(<ScannedKeywords keywords={undefined} total={2}>2 ranking keywords</ScannedKeywords>) === "2 ranking keywords" &&
    renderToStaticMarkup(<ScannedKeywords keywords={[]} total={2}>2 ranking keywords</ScannedKeywords>) === "2 ranking keywords",
);
ok("what to take and how to prove it", html.includes("A free 10-point audit") && html.includes("Opt-in rate ≥ 25% within 30 days"));
ok("a blocker says why", html.includes("testimonials are NO"));
ok("each number says where it came from", html.includes("Measured") && html.includes("Judged") && html.includes("Not available"));
ok("the judge's reason quotes its source", html.includes("Owners of Melbourne retail stores"));
ok("plays across the market", html.includes("Plays across the market") && html.includes("Case study library"));
ok(
  "an info icon explains the plays columns",
  html.includes("What these columns mean") && html.includes("Combined estimated Google organic visits"),
);
const plays = html.slice(html.indexOf("data-plays"), html.indexOf("</table>", html.indexOf("data-plays")));
ok("the plays table has no Avg fit or For this client column", !plays.includes("Avg fit") && !plays.includes("For this client") && plays.includes("Their traffic/mo"));
ok("the columns' info no longer explains the removed two", !html.includes("0–100: would this tactic work"));
ok("gaps", html.includes("post-by-post sales report"));
ok("how it was checked, with the limits stated", html.includes("How this was checked") && html.includes("Conversion rates and lead counts aren&#x27;t public"));

// The ranking reaches the prompt only for the competitors approved.
const subset = validationMarkdownFor(REPORT, [LISTING.competitors[0]]);
ok("a narrowed listing drops the left-out competitor's lines", !subset.includes("Beta") && subset.includes("| 1 | Alpha |"), subset);
ok("all approved: the whole ranking", validationMarkdownFor(REPORT, LISTING.competitors) === REPORT.markdown);
const alpha = { ...REPORT.competitors[0], what_to_take: "A free audit with a stated dollar value", how_to_apply: "Offer a free retail audit and gate it behind the booking form" };
const picked = validationMarkdownFor({ ...REPORT, competitors: [alpha, REPORT.competitors[1]] }, LISTING.competitors, ["alpha.com.au"]);
ok("a ticked play reaches the stage as its reference, with what and how", picked.includes("Plays selected by the operator") && picked.includes("Brings them leads: A free audit") && picked.includes("How to apply: Offer a free retail audit"), picked);
ok("an unticked play is not in the reference", !validationMarkdownFor({ ...REPORT, competitors: [alpha, REPORT.competitors[1]] }, LISTING.competitors, []).includes("Plays selected"));
ok("implement-now plays are ticked by default", defaultPicks({ ...REPORT, competitors: [alpha, REPORT.competitors[1]] }).includes("alpha.com.au"));
ok("the operator's own list carries no ranking", validationMarkdownFor(REPORT, [{ ...LISTING.competitors[0], domain: "mine.com" }]) === "");

await usePipelineStore.getState().saveCompetitorStep("comp-1", { ...LISTING, competitors: [LISTING.competitors[0]], returned_count: 1 });
const save = requests.find((r) => r.url.endsWith("/competitor/competitor_analysis_lead_magnet/save"));
ok("saving carries the ranking for the approved competitors", String(save?.body.validation_md).includes("Alpha") && !String(save?.body.validation_md).includes("Beta"), String(save?.body.validation_md));

// The deliverable's appendix carries each verdict.
state = usePipelineStore.getState();
msg = state.messages.find((m) => m.id === "comp-1")!;
const generation = { id: "gen-1", role: "assistant", kind: "generation", assetId: "lead_magnet", phase: "phase1", createdAt: 2 } as unknown as PipelineMessage;
const appendix = competitorAppendixFor([{ ...msg, competitor: LISTING }, generation], generation);
ok("the appendix carries each verdict", appendix.includes("Implement now · fit score 88") && appendix.includes("Avoid · fit score 48"), appendix);
ok("and how they were reached", appendix.includes("Verdicts are for this client. Sources: DataForSEO"));

console.log(`competitorValidation: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
process.exit();
