/* The competitor review card's selection: use all, choose some, or use the operator's own list.
 *
 * The parser and the approved-listing builder are pure, so they are asserted directly — they decide
 * what the stage downstream receives. The card is rendered for its first paint in each state; the
 * mode switch itself is a click handler, which server rendering cannot fire (see README). */
import { renderToStaticMarkup } from "react-dom/server";
import { CompetitorCard } from "../src/pipeline/CompetitorCard";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import type { CompetitorAnalysisResult } from "../src/pipeline/pipelineApi";
import { buildApprovedResult, OWN_LIST_NOTE, parseOwnCompetitorList } from "../src/lib/competitorSelection";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

// --- parseOwnCompetitorList -------------------------------------------------------------------

const typed = [
  "1. Acme Digital — acme.com",
  "- https://www.rivalagency.com.au/services",
  "Bright Growth (brightgrowth.co)",
  "Acme again, https://acme.com/", // duplicate domain
  "Some Agency Without A Site",
  "Contact sales@notacompetitor.com",
  "",
  "   ",
].join("\n");
const parsed = parseOwnCompetitorList(typed);

ok("parser reads three distinct competitors", parsed.rows.length === 3, JSON.stringify(parsed.rows));
ok("name and domain split on an em dash", parsed.rows[0]?.name === "Acme Digital" && parsed.rows[0]?.domain === "acme.com");
ok("bullet and number markers are stripped", !parsed.rows.some((r) => /^[\d.-]/.test(r.name)));
ok("www. is dropped from the domain", parsed.rows[1]?.domain === "rivalagency.com.au");
ok("a typed page URL is kept as the page link", parsed.rows[1]?.page_url === "https://www.rivalagency.com.au/services");
ok("a bare domain has no page link", parsed.rows[0]?.page_url === null);
ok("a URL-only line is named after its domain", parsed.rows[1]?.name === "rivalagency.com.au");
ok("parentheses around the domain are removed from the name", parsed.rows[2]?.name === "Bright Growth");
ok("a duplicate domain is read once", parsed.rows.filter((r) => r.domain === "acme.com").length === 1);
ok("operator rows are Unverified, never claimed as researched", parsed.rows.every((r) => r.verification_confidence === "Unverified"));
ok("ranks are 1..n", parsed.rows.map((r) => r.rank).join(",") === "1,2,3");
ok(
  "a line with no website is reported, not silently dropped",
  parsed.unreadable.includes("Some Agency Without A Site"),
  JSON.stringify(parsed.unreadable),
);
ok("an email address is not read as a competitor site", !parsed.rows.some((r) => r.domain === "notacompetitor.com"));

// --- buildApprovedResult ----------------------------------------------------------------------

const row = (rank: number, domain: string) => ({
  rank,
  domain,
  name: domain.split(".")[0],
  verification_confidence: "Verified",
  offering_summary: `${domain} offering`,
});
const found: CompetitorAnalysisResult = {
  asset_id: "competitor_analysis_cro",
  target_url: "https://client.example",
  raw_output: '{"competitors":[]}',
  location: "Melbourne",
  requested_count: 10,
  returned_count: 4,
  competitors: [row(1, "a.com"), row(2, "b.com"), row(3, "c.com"), row(4, "d.com")],
  notes: "Only four qualified.",
};

ok("'all' approves the search result unchanged", buildApprovedResult(found, "all", new Set(), []) === found);

const subset = buildApprovedResult(found, "select", new Set(["b.com", "d.com"]), []);
ok("'select' keeps only the ticked rows", subset.competitors.map((c) => c.domain).join(",") === "b.com,d.com");
ok("'select' renumbers ranks 1..n", subset.competitors.map((c) => c.rank).join(",") === "1,2");
ok("'select' reports the smaller count", subset.returned_count === 2);
ok("'select' states the selection in the notes", subset.notes?.startsWith("Operator selected 2 of 4") === true, subset.notes ?? "");
ok("'select' keeps the search's own notes", subset.notes?.includes("Only four qualified.") === true);
ok("'select' keeps raw_output as the audit trail", subset.raw_output === found.raw_output);
ok("'select' does not mutate the search result", found.competitors.length === 4 && found.competitors[1].rank === 2);

const mine = buildApprovedResult(found, "own", new Set(), parsed.rows);
ok("'own' uses the operator's rows", mine.competitors.map((c) => c.domain).join(",") === "acme.com,rivalagency.com.au,brightgrowth.co");
ok("'own' clears raw_output — the search did not produce this list", mine.raw_output === "");
ok("'own' says the list is operator-supplied", mine.notes === OWN_LIST_NOTE);
ok("'own' keeps the benchmark URL", mine.target_url === "https://client.example");

const afterFailure = buildApprovedResult(undefined, "own", new Set(), parsed.rows, { target_url: "https://client.example" });
ok("'own' works with no search result at all (a failed search)", afterFailure.competitors.length === 3 && afterFailure.target_url === "https://client.example");

// --- card first paint ---------------------------------------------------------------------------

const base: PipelineMessage = {
  id: "m1",
  role: "assistant",
  kind: "competitor",
  assetId: "competitor_analysis_cro",
  savePhase: "idle",
  competitorInputs: { target_url: "https://client.example" },
} as PipelineMessage;

const review = renderToStaticMarkup(<CompetitorCard message={{ ...base, competitor: found }} />);
ok("review offers all three choices", ["Use all 4", "Choose which to use", "Use my own list"].every((t) => review.includes(t)), review);
ok("'Use all' is the default choice", /aria-checked="true"[^>]*>Use all 4</.test(review));
ok("default save button approves everything", review.includes("Save &amp; Continue to"));
ok("no checkboxes until 'Choose which to use' is picked", !review.includes('type="checkbox"'));
ok("re-run is still offered", review.includes("Re-run Analysis"));

const empty = renderToStaticMarkup(
  <CompetitorCard message={{ ...base, competitor: { ...found, competitors: [], returned_count: 0 } }} />,
);
ok("an empty search offers only the own list", empty.includes("Use my own list") && !empty.includes("Choose which to use"));
ok("an empty search opens straight onto the list editor", empty.includes("<textarea"));
ok("the save button is disabled until something is typed", /disabled=""[^>]*>Use my 0 &amp;/.test(empty), empty);

const failed = renderToStaticMarkup(<CompetitorCard message={{ ...base, competitorError: "Search timed out" }} />);
ok("a failed search still offers retry", failed.includes("Retry Analysis"));
ok("a failed search offers the operator's own list", failed.includes("I have my own list"));

const saved = renderToStaticMarkup(
  <CompetitorCard message={{ ...base, savePhase: "saved", competitor: subset }} />,
);
ok("a saved card states how many were used", saved.includes("2 competitors") && saved.includes("used for this stage"));
ok("a saved card lists only what was saved", saved.includes("b.com") && !saved.includes("a.com offering"));
ok("a saved card offers no choices", !saved.includes("Choose which to use") && !saved.includes('type="checkbox"'));

// --- report ------------------------------------------------------------------------------------

console.log(`${pass} passed, ${fails.length} failed`);
if (fails.length) {
  for (const f of fails) console.log("  FAIL " + f.slice(0, 400));
  process.exit(1);
}
