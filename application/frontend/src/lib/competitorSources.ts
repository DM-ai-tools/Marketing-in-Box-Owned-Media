import { ASSET_CATALOG } from "../data/assetCatalog";
import { PHASE2_ASSETS } from "../data/phase2Catalog";
import type { CompetitorRow } from "../pipeline/pipelineApi";
import type { PipelineMessage } from "../pipeline/pipelineStore";

/** The "Competitors scanned" section of an asset: every competitor its research pass found, with the
 * page that was read.
 *
 * The listing reaches the stage's prompt (`to_prompt_text` writes a `Page:` line per competitor), but
 * no stage prompt asks for the sources back, so the finished document benchmarks against "the
 * market" with nothing a reader can click to check it. This is that record.
 *
 * Same contract as `topicSuggestions.ts`: shown in the reader and in the downloaded Markdown, never
 * written into `message.text` — the saved text is what the next stage reads out of the Context
 * Store, and it already had the listing in its own INPUTS. Everything comes from the transcript, so
 * it costs no request and works on a reloaded chat.
 *
 * Two sources, in order: the approved listing card the stage was gated on (or asked about mid-
 * intake), and failing that the generation's own invisible prepass, whose content is the same prose
 * `to_prompt_text` writes. */

export interface CompetitorSource {
  name: string;
  domain: string;
  pageUrl: string | null;
  confidence: string | null;
}

export interface CompetitorSources {
  label: string;
  target: string | null;
  competitors: CompetitorSource[];
}

/** Main asset id -> the competitor stage it is paired with, per phase. Phase 2 dropped Pillar Page's
 * pairing, so it reads its own definitions rather than Phase 1's. */
function pairedCompetitorId(phase: string | undefined, assetId: string): string | undefined {
  const def = phase === "phase2" ? PHASE2_ASSETS[assetId] : ASSET_CATALOG.find((a) => a.asset_id === assetId);
  if (!def || def.category === "Competitor Research") return undefined;
  return def.pairedCompetitorAssetId;
}

function labelFor(competitorAssetId: string): string {
  return ASSET_CATALOG.find((a) => a.asset_id === competitorAssetId)?.label ?? "Competitor research";
}

function fromRows(rows: CompetitorRow[]): CompetitorSource[] {
  return rows.map((r) => ({
    name: r.name || r.domain,
    domain: r.domain,
    pageUrl: r.page_url?.trim() || null,
    confidence: r.verification_confidence || null,
  }));
}

// The entry and page lines `to_prompt_text` writes (backend `competitor.py`), read back.
const PROSE_HEADER = /^\s*Competitor analysis\s*[—-]\s*benchmarked against\s+(\S+)/i;
const PROSE_ENTRY = /^\s*\d+\.\s+(.+?)\s+\(([^()\s]+)\)\s+[—-]\s+(.+?)\s*$/;
const PROSE_PAGE = /^\s+Page:\s*(\S+)\s*$/;

/** Read the prepass prose back into rows. Exported for the smoke test. */
export function parseCompetitorProse(text: string): { target: string | null; competitors: CompetitorSource[] } {
  let target: string | null = null;
  const competitors: CompetitorSource[] = [];
  for (const line of text.split("\n")) {
    const header = PROSE_HEADER.exec(line);
    if (header) {
      target = header[1];
      continue;
    }
    const entry = PROSE_ENTRY.exec(line);
    if (entry) {
      competitors.push({ name: entry[1], domain: entry[2], pageUrl: null, confidence: entry[3] });
      continue;
    }
    const page = PROSE_PAGE.exec(line);
    if (page && competitors.length) competitors[competitors.length - 1].pageUrl = page[1];
  }
  return { target, competitors };
}

/** The competitor listing this generation was built on, or null when its stage had none. */
export function competitorSourcesFor(messages: PipelineMessage[], generation: PipelineMessage): CompetitorSources | null {
  if (!generation.assetId) return null;
  const competitorId = pairedCompetitorId(generation.phase, generation.assetId);
  const end = messages.findIndex((m) => m.id === generation.id);

  // The latest approved listing before this document, in the same leg. Superseded ones count for
  // the same reason as in `topicGatesFor`: an older document was still built on the older listing.
  if (competitorId && end > 0) {
    for (let i = end - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.kind !== "competitor" || m.assetId !== competitorId) continue;
      if (m.phase !== generation.phase || m.trackId !== generation.trackId) continue;
      if (m.savePhase !== "saved" || !m.competitor) continue;
      return {
        label: labelFor(competitorId),
        target: m.competitor.target_url || null,
        competitors: fromRows(m.competitor.competitors),
      };
    }
  }

  const prepass = generation.prepass;
  if (prepass?.status === "done" && prepass.content) {
    const parsed = parseCompetitorProse(prepass.content);
    if (parsed.competitors.length) return { label: prepass.label, ...parsed };
  }
  return null;
}

export const COMPETITOR_SOURCES_HEADING = "# Competitors scanned";
export const COMPETITOR_SOURCES_LABEL = "Competitors scanned";

export function isCompetitorSourcesHeading(line: string): boolean {
  return line.trim() === COMPETITOR_SOURCES_HEADING;
}

function link(url: string): string {
  const href = /^https?:\/\//i.test(url) ? url : `https://${url}`;
  return `[${url.replace(/^https?:\/\//i, "")}](${href})`;
}

/** The whole section, or "" when there is nothing to list. Placed at the end of the document: it is
 * the evidence the asset was benchmarked on, read after the asset rather than before it. */
export function renderCompetitorSources(sources: CompetitorSources | null): string {
  if (!sources?.competitors.length) return "";
  const rows = sources.competitors.map((c, i) => {
    const page = c.pageUrl ? link(c.pageUrl) : "no page recorded";
    const confidence = c.confidence ? ` (${c.confidence})` : "";
    return `${i + 1}. **${c.name}** · ${link(c.domain)} · ${page}${confidence}`;
  });
  const against = sources.target ? `, benchmarked against ${link(sources.target)}` : "";
  return [
    "",
    "---",
    "",
    COMPETITOR_SOURCES_HEADING,
    "",
    `The ${sources.competitors.length} competitors found by ${sources.label}${against}. This asset was written against this list.`,
    "",
    rows.join("\n"),
    "",
  ].join("\n");
}

/** Convenience for the reader and export buttons: the section for one generation message, or "". */
export function competitorAppendixFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): string {
  return generation ? renderCompetitorSources(competitorSourcesFor(messages, generation)) : "";
}
