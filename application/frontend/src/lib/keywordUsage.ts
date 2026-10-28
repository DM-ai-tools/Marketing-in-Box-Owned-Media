import type { HeadlineCandidate, KeywordGroup, KeywordReportResult, KeywordTargets, RankedKeyword } from "../pipeline/pipelineApi";
import type { PipelineMessage, PlanDiscoveryState } from "../pipeline/pipelineStore";
import { topicGatesFor } from "./topicSuggestions";

/** The "Keyword data" section, last in an asset's document: what DataForSEO measured for it and
 * how that data was used.
 *
 * DataForSEO reaches an asset in up to four ways, and each one is read back here from the transcript:
 *   - the run's **search demand map** — the keyword prepass, recorded on its announcement
 *     (`keywordReport`) with the service, the country and the clusters;
 *   - the **topics** the asset was built on — a headline gate's picks carry the keyword, volume and
 *     difficulty they were grounded in;
 *   - the Plan of Action's **sub-service sizing** — the confirmed sub-services and their volumes;
 *   - the **business check**, when its virality score was benchmarked against those volumes.
 *
 * Same contract as `competitorSources.ts` and `businessCheck.ts`: shown in the reader and in every
 * exported file, never written into `message.text`, so the next stage reads only what the stage
 * wrote. It costs no request and works on a reloaded chat. Only data DataForSEO actually returned
 * counts — the stub provider's placeholder keywords never appear here — and an asset DataForSEO did not
 * touch gets no section at all.
 */

export const KEYWORD_USAGE_LABEL = "Keyword data";
const KEYWORD_USAGE_HEADING = `## ${KEYWORD_USAGE_LABEL}`;

export function isKeywordUsageHeading(line: string): boolean {
  return line.trim() === KEYWORD_USAGE_HEADING;
}

/** Clusters listed in the demand map. The full set lives in the run's keyword report. */
const MAX_CLUSTERS = 10;

export interface KeywordTopic {
  headline: string;
  keyword: string;
  volume: number | null;
  difficulty: number | null;
  cluster: string | null;
}

export interface KeywordUsage {
  demand: KeywordReportResult | null;
  topics: KeywordTopic[];
  plan: PlanDiscoveryState | null;
  checkBenchmarked: boolean;
  /** The primary and secondary keywords this asset's generation was told to write with. */
  targets: KeywordTargets | null;
  /** The asset's own text, which the targeted keywords are counted in. */
  text: string;
}

/** Ranked keywords listed in the section. The full ranking lives in the run's keyword report. */
const MAX_RANKED = 15;

function sameLeg(a: PipelineMessage, b: PipelineMessage): boolean {
  return (a.phase ?? "phase1") === (b.phase ?? "phase1") && (a.trackId ?? null) === (b.trackId ?? null);
}

/** What DataForSEO contributed to one generation, or null when it contributed nothing. */
export function keywordUsageFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): KeywordUsage | null {
  if (!generation || generation.kind !== "generation") return null;
  const end = messages.findIndex((m) => m.id === generation.id);
  if (end < 0) return null;
  const before = messages.slice(0, end);

  // The latest demand map of this leg that DataForSEO actually answered.
  const demand =
    [...before]
      .reverse()
      .find((m) => sameLeg(m, generation) && m.keywordReport?.provider === "dataforseo" && m.keywordReport.status !== "skipped")
      ?.keywordReport ?? null;

  const topics: KeywordTopic[] = [];
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
  const measuredPlan = plan?.volumeSource === "dataforseo" ? plan : null;

  const checkBenchmarked = Boolean(demand) && generation.check?.report?.virality_basis === "benchmarked";

  const targets = generation.keywordTargets?.groups?.length ? generation.keywordTargets : null;

  if (!demand && !topics.length && !measuredPlan && !targets) return null;
  return { demand, topics, plan: measuredPlan, checkBenchmarked, targets, text: generation.text ?? "" };
}

/** The asset's words as a reader sees them: tags and entities out, so a keyword in a built page is
 * counted in its copy and not in its markup. */
function readableText(text: string): string {
  return text
    .replace(/<(script|style|svg|head)\b[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;|&#160;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/[’‘]/g, "'");
}

const escapeRe = (w: string) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The last word in its singular and plural forms: "accessories" also matches "accessory",
 * "search" also matches "searches". */
function eitherNumber(word: string): string {
  if (word.length > 4 && word.endsWith("ies")) return `${escapeRe(word.slice(0, -3))}(?:y|ies)`;
  if (word.length > 4 && /(?:ch|sh|ss|x)es$/.test(word)) return `${escapeRe(word.slice(0, -2))}(?:es)?`;
  if (word.length > 3 && word.endsWith("s") && !word.endsWith("ss")) return `${escapeRe(word.slice(0, -1))}s?`;
  if (word.endsWith("y")) return `${escapeRe(word.slice(0, -1))}(?:y|ies)`;
  return `${escapeRe(word)}(?:e?s)?`;
}

/** How many times `keyword` appears in `text`: whole words, any case, any spacing or hyphen, the
 * last word singular or plural. Measured, never judged. */
export function keywordMentions(text: string, keyword: string): number {
  const words = keyword.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return 0;
  const last = words.length - 1;
  const pattern = words.map((w, i) => (i === last ? eitherNumber(w) : escapeRe(w))).join("[\\s-]+");
  return (readableText(text).toLowerCase().match(new RegExp(`(?<![a-z0-9])${pattern}(?![a-z0-9])`, "g")) ?? []).length;
}

const momentum = (trend: number | null | undefined): string =>
  typeof trend === "number" ? `${trend > 0 ? "+" : ""}${trend}%` : "—";

function topicOf(c: HeadlineCandidate): KeywordTopic {
  return {
    headline: c.headline,
    keyword: c.primary_keyword ?? "",
    volume: c.search_volume,
    difficulty: c.difficulty,
    cluster: c.source_cluster,
  };
}

/** Every targeted keyword, primaries first within each group, once each. */
function targetRows(targets: KeywordTargets): { keyword: RankedKeyword; type: "Primary" | "Secondary" }[] {
  const seen = new Set<string>();
  const rows: { keyword: RankedKeyword; type: "Primary" | "Secondary" }[] = [];
  for (const group of targets.groups) {
    for (const [keyword, type] of [[group.primary, "Primary"] as const, ...group.secondary.map((k) => [k, "Secondary"] as const)]) {
      const key = keyword.keyword.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push({ keyword, type });
    }
  }
  return rows;
}

function secondaryList(group: KeywordGroup): string {
  return group.secondary.map((k) => `${k.keyword} (${num(k.volume)})`).join(", ");
}

const cell = (text: string | null | undefined): string => (text ?? "").replace(/\|/g, "\\|").replace(/\s+/g, " ").trim() || "—";
const num = (value: number | null | undefined): string => (typeof value === "number" ? value.toLocaleString("en-US") : "—");

/** The keywords in one glance, ahead of the detail: one primary and its secondaries per block
 * (`Primary keyword: …` / `Secondary keywords: …`). The asset's own targets lead, else the run's
 * demand-map groups, else the keywords of the topics it was built on (primaries only). */
function glanceLines(groups: KeywordGroup[], topics: KeywordTopic[]): string[] {
  const blocks = groups.length
    ? groups.map((g) =>
        [
          `**Primary keyword:** ${g.primary.keyword}`,
          ...(g.secondary.length ? [`**Secondary keywords:** ${g.secondary.map((k) => k.keyword).join(", ")}`] : []),
        ].join("\n"),
      )
    : [...new Set(topics.map((t) => t.keyword).filter(Boolean))].map((k) => `**Primary keyword:** ${k}`);
  if (!blocks.length) return [];
  return ["### Keywords at a glance", "", blocks.join("\n\n"), ""];
}

/** The whole section, or "" when DataForSEO contributed nothing to this asset. */
export function renderKeywordUsage(usage: KeywordUsage | null): string {
  if (!usage) return "";
  const { demand, topics, plan, checkBenchmarked, targets, text } = usage;
  const country = demand?.location || plan?.location || targets?.location || "";
  const synonyms = Object.entries(demand?.synonyms ?? targets?.synonyms ?? {}).filter(([, names]) => names.length);
  const targeted = targets ? targetRows(targets) : [];
  const used = targeted.filter((row) => keywordMentions(text, row.keyword.keyword) > 0);

  const how: string[] = [];
  if (synonyms.length) {
    how.push(
      `- **Alternate names.** People search for the same service in different words, so the keywords were also ` +
        `fetched for ${synonyms.map(([service, names]) => `${service}: ${names.join(", ")}`).join("; ")}.`,
    );
  }
  if (demand?.ranked?.length) {
    how.push(
      "- **Ranking.** Every keyword was ranked by monthly searches, estimated traffic (searches × the click-through " +
        "rate at the position its difficulty usually allows) and search momentum (the % change in searches), then " +
        "grouped as primary keywords with their secondary keywords.",
    );
  }
  if (targets) {
    how.push(
      `- **Writing.** This asset was written with the top ${targets.groups.length} primary ` +
        `${targets.groups.length === 1 ? "keyword" : "keywords"} and their secondary keywords. ` +
        `${used.length} of the ${targeted.length} appear in it.`,
    );
  }
  if (demand) {
    const clusters = demand.clusters.length;
    how.push(
      `- **Search demand map.** For ${demand.service ?? "this run's service"}, the seed terms were measured with ` +
        "DataForSEO `keyword_overview` and expanded with `keyword_suggestions`, `related_keywords` and `keyword_ideas`. " +
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
      `- **Sub-service sizing.** One DataForSEO \`keyword_overview\` request measured ${measured} ` +
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
    KEYWORD_USAGE_HEADING,
    "",
    `Search data for this asset came from DataForSEO Labs: monthly Google searches${country ? ` in ${country}` : ""}. ` +
      "Volumes are DataForSEO's estimates; — means DataForSEO had no figure for that keyword.",
    "",
    ...glanceLines(targets?.groups?.length ? targets.groups : demand?.groups ?? [], topics),
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

  if (demand?.seeds?.length) {
    parts.push(
      "",
      "### Keywords fetched",
      "",
      `Seed terms sent to DataForSEO: ${demand.seeds.map(cell).join(", ")}. ` +
        `${num(demand.total_keywords)} keywords were kept after cleaning.`,
    );
  }

  const groups = demand?.groups?.length ? demand.groups : targets?.groups ?? [];
  if (groups.length) {
    parts.push(
      "",
      "### Primary and secondary keywords",
      "",
      "A primary keyword is a core search term; its secondary keywords are the same search with a modifier " +
        "(a place, buy, best, online).",
      "",
      "| Primary keyword | Searches/mo | Est. traffic/mo | Momentum | Secondary keywords |",
      "| --- | --- | --- | --- | --- |",
      groups.map((g) => `| ${cell(g.primary.keyword)} | ${num(g.primary.volume)} | ${num(g.primary.est_traffic)} | ` +
        `${momentum(g.primary.trend)} | ${cell(secondaryList(g))} |`).join("\n"),
    );
  }

  if (demand?.ranked?.length) {
    const ranked = demand.ranked.slice(0, MAX_RANKED);
    parts.push(
      "",
      `### Top ${ranked.length} keywords, ranked`,
      "",
      "| Rank | Keyword | Searches/mo | KD | Est. traffic/mo | Momentum | Score |",
      "| --- | --- | --- | --- | --- | --- | --- |",
      ranked
        .map((k) => `| ${k.rank} | ${cell(k.keyword)} | ${num(k.volume)} | ${num(k.difficulty)} | ${num(k.est_traffic)} | ` +
          `${momentum(k.trend)} | ${num(k.score)} |`)
        .join("\n"),
    );
  }

  if (targeted.length) {
    parts.push(
      "",
      "### Keywords used in this asset",
      "",
      "Counted in this asset's own text (whole words, any case).",
      "",
      "| Keyword | Type | Searches/mo | In this asset |",
      "| --- | --- | --- | --- |",
      targeted
        .map(({ keyword, type }) => {
          const n = keywordMentions(text, keyword.keyword);
          return `| ${cell(keyword.keyword)} | ${type} | ${num(keyword.volume)} | ${n ? `✓ ${n}×` : "not used"} |`;
        })
        .join("\n"),
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
export function keywordAppendixFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): string {
  return renderKeywordUsage(keywordUsageFor(messages, generation));
}
