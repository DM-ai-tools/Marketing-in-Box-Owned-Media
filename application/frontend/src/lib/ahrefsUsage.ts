import type { HeadlineCandidate, KeywordReportResult } from "../pipeline/pipelineApi";
import type { PipelineMessage, PlanDiscoveryState } from "../pipeline/pipelineStore";
import { topicGatesFor } from "./topicSuggestions";

/** The "Ahrefs keyword data" section, last in an asset's document: what Ahrefs measured for it and
 * how that data was used.
 *
 * Ahrefs reaches an asset in up to four ways, and each one is read back here from the transcript:
 *   - the run's **search demand map** — the keyword prepass, recorded on its announcement
 *     (`keywordReport`) with the service, the country and the clusters;
 *   - the **topics** the asset was built on — a headline gate's picks carry the keyword, volume and
 *     difficulty they were grounded in;
 *   - the Plan of Action's **sub-service sizing** — the confirmed sub-services and their volumes;
 *   - the **business check**, when its virality score was benchmarked against those volumes.
 *
 * Same contract as `competitorSources.ts` and `businessCheck.ts`: shown in the reader and in every
 * exported file, never written into `message.text`, so the next stage reads only what the stage
 * wrote. It costs no request and works on a reloaded chat. Only data Ahrefs actually returned
 * counts — the stub provider's placeholder keywords never appear here — and an asset Ahrefs did not
 * touch gets no section at all.
 */

export const AHREFS_USAGE_LABEL = "Ahrefs keyword data";
const AHREFS_USAGE_HEADING = `## ${AHREFS_USAGE_LABEL}`;

export function isAhrefsUsageHeading(line: string): boolean {
  return line.trim() === AHREFS_USAGE_HEADING;
}

/** Clusters listed in the demand map. The full set lives in the run's keyword report. */
const MAX_CLUSTERS = 10;

export interface AhrefsTopic {
  headline: string;
  keyword: string;
  volume: number | null;
  difficulty: number | null;
  cluster: string | null;
}

export interface AhrefsUsage {
  demand: KeywordReportResult | null;
  topics: AhrefsTopic[];
  plan: PlanDiscoveryState | null;
  checkBenchmarked: boolean;
}

function sameLeg(a: PipelineMessage, b: PipelineMessage): boolean {
  return (a.phase ?? "phase1") === (b.phase ?? "phase1") && (a.trackId ?? null) === (b.trackId ?? null);
}

/** What Ahrefs contributed to one generation, or null when it contributed nothing. */
export function ahrefsUsageFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): AhrefsUsage | null {
  if (!generation || generation.kind !== "generation") return null;
  const end = messages.findIndex((m) => m.id === generation.id);
  if (end < 0) return null;
  const before = messages.slice(0, end);

  // The latest demand map of this leg that Ahrefs actually answered.
  const demand =
    [...before]
      .reverse()
      .find((m) => sameLeg(m, generation) && m.keywordReport?.provider === "ahrefs" && m.keywordReport.status !== "skipped")
      ?.keywordReport ?? null;

  const topics: AhrefsTopic[] = [];
  for (const gate of topicGatesFor(messages, generation)) {
    if (!gate.headlines.groundedInKeywords) continue;
    const chosen = new Set(gate.headlines.chosenIds ?? []);
    for (const c of gate.headlines.candidates ?? []) {
      if (chosen.has(c.id) && c.grounded && c.primary_keyword) topics.push(topicOf(c));
    }
  }

  const plan =
    generation.assetId === "plan_of_action"
      ? ([...before]
          .reverse()
          .find((m) => sameLeg(m, generation) && m.serviceScan?.plan && m.serviceScan.status === "done")
          ?.serviceScan?.plan ?? null)
      : null;
  const measuredPlan = plan?.volumeSource === "ahrefs" ? plan : null;

  const checkBenchmarked = Boolean(demand) && generation.check?.report?.virality_basis === "benchmarked";

  if (!demand && !topics.length && !measuredPlan) return null;
  return { demand, topics, plan: measuredPlan, checkBenchmarked };
}

function topicOf(c: HeadlineCandidate): AhrefsTopic {
  return {
    headline: c.headline,
    keyword: c.primary_keyword ?? "",
    volume: c.search_volume,
    difficulty: c.difficulty,
    cluster: c.source_cluster,
  };
}

const cell = (text: string | null | undefined): string => (text ?? "").replace(/\|/g, "\\|").replace(/\s+/g, " ").trim() || "—";
const num = (value: number | null | undefined): string => (typeof value === "number" ? value.toLocaleString("en-US") : "—");

/** The whole section, or "" when Ahrefs contributed nothing to this asset. */
export function renderAhrefsUsage(usage: AhrefsUsage | null): string {
  if (!usage) return "";
  const { demand, topics, plan, checkBenchmarked } = usage;
  const country = demand?.location || plan?.location || "";

  const how: string[] = [];
  if (demand) {
    const clusters = demand.clusters.length;
    how.push(
      `- **Search demand map.** For ${demand.service ?? "this run's service"}, the seed terms were measured with ` +
        "Ahrefs `overview` and expanded with `matching-terms`, `related-terms` and `search-suggestions`. " +
        `After cleaning, ${num(demand.total_keywords)} keywords were grouped into ${clusters} topic ` +
        `${clusters === 1 ? "cluster" : "clusters"}` +
        (demand.status === "reused" ? " (measured earlier in this run and reused)." : "."),
    );
  }
  if (topics.length) {
    how.push(
      `- **Topic choice.** The ${topics.length === 1 ? "topic" : `${topics.length} topics`} this asset is built on ` +
        `${topics.length === 1 ? "was" : "were"} suggested from that map, each tied to a measured keyword.`,
    );
  }
  if (plan) {
    const chosen = plan.candidates.filter((c) => (plan.chosenNames ?? []).includes(c.name));
    const measured = chosen.flatMap((c) => c.keywords).filter((k) => k.volume !== null).length;
    how.push(
      `- **Sub-service sizing.** One Ahrefs \`overview\` request measured ${measured} ` +
        `${measured === 1 ? "keyword" : "keywords"} across the ${chosen.length} sub-services confirmed for Phase 2, ` +
        "which set their priority and the volumes quoted in the plan.",
    );
  }
  if (checkBenchmarked) {
    how.push("- **Business check.** The virality score below was benchmarked against these keyword volumes.");
  }

  const parts = [
    "",
    "---",
    "",
    AHREFS_USAGE_HEADING,
    "",
    `Search data for this asset came from Ahrefs Keywords Explorer: monthly Google searches${country ? ` in ${country}` : ""}. ` +
      "Volumes are Ahrefs' estimates; — means Ahrefs had no figure for that keyword.",
    "",
    "### How it was used",
    "",
    how.join("\n"),
  ];

  if (demand?.clusters.length) {
    const rows = [...demand.clusters]
      .sort((a, b) => b.total_volume - a.total_volume)
      .slice(0, MAX_CLUSTERS)
      .map(
        (c) =>
          `| ${cell(c.name)} | ${cell(c.primary_keyword)} | ${num(c.keyword_count)} | ${num(c.total_volume)} | ${cell(c.intent)} | ${cell(c.funnel)} |`,
      );
    const more = demand.clusters.length > MAX_CLUSTERS ? `\n\nTop ${MAX_CLUSTERS} of ${demand.clusters.length} clusters by volume.` : "";
    parts.push(
      "",
      `### Search demand map — ${cell(demand.service)}`,
      "",
      "| Cluster | Primary keyword | Keywords | Total volume/mo | Intent | Funnel |",
      "| --- | --- | --- | --- | --- | --- |",
      rows.join("\n") + more,
    );
  }

  if (topics.length) {
    parts.push(
      "",
      "### Topics this asset was built on",
      "",
      "| Topic | Keyword | Volume/mo | Difficulty | Cluster |",
      "| --- | --- | --- | --- | --- |",
      topics.map((t) => `| ${cell(t.headline)} | ${cell(t.keyword)} | ${num(t.volume)} | ${num(t.difficulty)} | ${cell(t.cluster)} |`).join("\n"),
    );
  }

  if (plan) {
    const chosen = plan.candidates.filter((c) => (plan.chosenNames ?? []).includes(c.name));
    if (chosen.length) {
      parts.push(
        "",
        "### Phase 2 sub-services, sized",
        "",
        "| Sub-service | Status | Keywords (volume/mo) | Combined/mo |",
        "| --- | --- | --- | --- |",
        chosen
          .map((c) => {
            const status = c.status === "existing" ? `existing page (${cell(c.url)})` : `new (${cell(c.slug)})`;
            const keywords = c.keywords.map((k) => `${k.keyword} (${num(k.volume)})`).join(", ");
            return `| ${cell(c.name)} | ${status} | ${cell(keywords)} | ${num(c.combined_volume)} |`;
          })
          .join("\n"),
      );
    }
  }

  parts.push("");
  return parts.join("\n");
}

/** Convenience for the reader and export buttons: the section for one generation message, or "". */
export function ahrefsAppendixFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): string {
  return renderAhrefsUsage(ahrefsUsageFor(messages, generation));
}
