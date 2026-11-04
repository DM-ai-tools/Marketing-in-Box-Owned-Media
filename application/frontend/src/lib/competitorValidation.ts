import type {
  CompetitorRow,
  CompetitorValidation,
  ValidatedCompetitor,
  ValidationVerdict,
} from "../pipeline/pipelineApi";

/** The words the card, the badges and the deliverable use for each verdict. The server's
 * `VERDICT_LABEL` (`app/services/competitor_validation.py`) says the same; change one, change both. */
export const VERDICT_LABEL: Record<ValidationVerdict, string> = {
  implement_now: "Implement now",
  test_first: "Test first",
  differentiate: "Differentiate",
  avoid: "Avoid",
  not_a_fit: "Not a fit",
};

/** Restated from `BUSINESS_LEVEL_LABEL` in `app/services/competitor_validation.py`. */
export const BUSINESS_LEVEL_LABEL: Record<"above" | "peer" | "below", string> = {
  above: "Above this client",
  peer: "Same level",
  below: "Below this client",
};

export const VERDICT_HINT: Record<ValidationVerdict, string> = {
  implement_now: "Proven for them and close to this client's service and buyer.",
  test_first: "Close to this client, but the proof is thin. Run it as an experiment.",
  differentiate: "Most of the market does this. Match it and do it better.",
  avoid: "The client can't run it as it stands.",
  not_a_fit: "Little overlap with this client's service, keywords or buyer.",
};

/** What each column on "Plays across the market" means. The card's info icon shows this on hover. */
export const PLAY_COLUMN_HINTS: { label: string; hint: string }[] = [
  { label: "Play", hint: "The conversion tactic on the rival's page — how they convert, not who they are." },
  { label: "Used by", hint: "How many rivals in this listing run that tactic." },
  { label: "Their traffic/mo", hint: "Combined estimated Google organic visits per month for those rivals. Not leads or conversions." },
];

/** The ranking handed to the stage's prompt, limited to the competitors the operator approved.
 *
 * The server renders it for the whole listing. Narrowed to a ticked subset, the rows and bullets
 * naming a competitor that was left out are dropped, so the stage never builds on a competitor the
 * operator excluded. The market-level lines (plays, gaps) stay. "" for the operator's own list, which
 * was never scored. */
export function validationMarkdownFor(
  report: CompetitorValidation,
  approved: CompetitorRow[],
  picks?: string[],
  keywordPicks?: string[],
): string {
  const kept = new Set(approved.map((c) => c.domain.toLowerCase()));
  const scored = report.competitors.filter((c) => kept.has(c.domain.toLowerCase()));
  if (!scored.length) return "";
  let body = report.markdown;
  if (scored.length < report.competitors.length) {
    const dropped = report.competitors.filter((c) => !kept.has(c.domain.toLowerCase())).map((c) => c.name);
    body = body
      .split("\n")
      .filter((line) => !dropped.some((name) => line.startsWith(`- **${name} — `) || new RegExp(`^\\| \\d+ \\| ${escape(name)} \\|`).test(line)))
      .join("\n");
  }
  const selected = picks ? selectedPlaysMarkdown(scored, picks) : "";
  // Always emit a keyword constraint when the table had rows — empty ticks means "use none of
  // these", not "no opinion". Unticked terms must not reach the stage via this listing. Scoped to
  // the approved competitors so a left-out rival's keywords are not offered or banned by name.
  const keywords = keywordConstraintMarkdown(scored, keywordPicks ?? []);
  return [body, selected, keywords].filter(Boolean).join("\n\n");
}

/** The plays worth applying, pre-ticked: proven for the competitor and a fit for this client, with
 * something concrete to take. The operator unticks or adds from there. */
export function defaultPicks(report: CompetitorValidation): string[] {
  return report.competitors
    .filter((c) => (c.verdict === "implement_now" || c.verdict === "test_first") && c.what_to_take)
    .map((c) => c.domain);
}

/** Whether a play can be picked at all: avoided or off-topic ones can't be run, so they have no box. */
export const isPickable = (c: ValidatedCompetitor): boolean =>
  c.verdict !== "avoid" && c.verdict !== "not_a_fit" && Boolean(c.what_to_take || c.how_to_apply);

/** The operator's picked plays, as the reference the stage builds from. Ranked as the report is
 * (best verdict first). "" when nothing is picked. */
export function selectedPlaysMarkdown(competitors: ValidatedCompetitor[], picks: string[]): string {
  const chosen = new Set(picks.map((d) => d.toLowerCase()));
  const rows = competitors.filter((c) => chosen.has(c.domain.toLowerCase()) && isPickable(c));
  if (!rows.length) return "";
  return [
    "## Plays selected by the operator — build from these",
    "These lead magnets, offers and funnel entries are the ones the operator chose to apply. Use them " +
      "as the reference for this asset, in the order listed: take the mechanism, never a competitor's " +
      "wording, and follow each play's steps for this client. Plays not listed here were not chosen.",
    "",
    ...rows.map(
      (c, i) =>
        `${i + 1}. **${c.name} — ${VERDICT_LABEL[c.verdict]}.** Brings them leads: ${c.what_to_take || "—"}` +
        ` How to apply: ${c.how_to_apply || "—"}`,
    ),
  ].join("\n");
}

/** One ranking keyword fetched for the listing, with duplicates across competitors already collapsed. */
export interface FetchedKeyword {
  keyword: string;
  /** Best (lowest) Google position among the competitors who rank for it. */
  position: number | null;
  volume: number | null;
  /** Competitor names, best position first. */
  competitors: string[];
  /** Set when a comparison against the client exists. Null then means the client is not ranking. */
  clientPosition: number | null;
  clientKnown: boolean;
}

/** Every keyword DataForSEO returned for these competitors, one row per term.
 *
 * The same search is fetched once per domain, so "seo company" from three rivals is one row. Match
 * is case-insensitive. The best position and the highest reported volume are kept. */
export function uniqueFetchedKeywords(competitors: ValidatedCompetitor[]): FetchedKeyword[] {
  type Acc = {
    keyword: string;
    position: number | null;
    volume: number | null;
    byName: Map<string, number | null>;
    clientPosition: number | null;
    clientKnown: boolean;
  };
  const map = new Map<string, Acc>();

  const touch = (
    raw: string,
    competitor: string,
    position: number | null,
    volume: number | null,
    client?: { position: number | null },
  ) => {
    const keyword = raw.trim();
    const key = keyword.toLowerCase();
    if (!key || !competitor) return;
    let row = map.get(key);
    if (!row) {
      row = { keyword, position: null, volume: null, byName: new Map(), clientPosition: null, clientKnown: false };
      map.set(key, row);
    }
    const prev = row.byName.get(competitor);
    if (prev === undefined || (position != null && (prev == null || position < prev))) {
      row.byName.set(competitor, position ?? null);
    }
    if (position != null && (row.position == null || position < row.position)) row.position = position;
    if (volume != null && (row.volume == null || volume > row.volume)) row.volume = volume;
    if (client) {
      row.clientKnown = true;
      if (client.position != null && (row.clientPosition == null || client.position < row.clientPosition)) {
        row.clientPosition = client.position;
      }
    }
  };

  for (const c of competitors) {
    const scanned = c.scanned_keywords ?? [];
    const source = scanned.length ? scanned : (c.top_keywords ?? []);
    for (const k of source) touch(k.keyword, c.name, k.position, k.volume);
    for (const k of c.keyword_comparisons ?? []) {
      touch(k.keyword, c.name, k.competitor_position, k.volume, { position: k.client_position });
    }
  }

  const rows: FetchedKeyword[] = [...map.values()].map((row) => ({
    keyword: row.keyword,
    position: row.position,
    volume: row.volume,
    competitors: [...row.byName.entries()]
      .sort((a, b) => (a[1] ?? 9999) - (b[1] ?? 9999) || a[0].localeCompare(b[0]))
      .map(([name]) => name),
    clientPosition: row.clientPosition,
    clientKnown: row.clientKnown,
  }));
  rows.sort(
    (a, b) =>
      (b.volume ?? -1) - (a.volume ?? -1) ||
      (a.position ?? 9999) - (b.position ?? 9999) ||
      a.keyword.localeCompare(b.keyword),
  );
  return rows;
}

/** The keywords the operator ticked for this stage's draft. "" when nothing is ticked. */
export function selectedKeywordsMarkdown(competitors: ValidatedCompetitor[], picks: string[]): string {
  const chosen = new Set(picks.map((k) => k.trim().toLowerCase()).filter(Boolean));
  if (!chosen.size) return "";
  const rows = uniqueFetchedKeywords(competitors).filter((r) => chosen.has(r.keyword.toLowerCase()));
  if (!rows.length) return "";
  const vol = (v: number | null) => (typeof v === "number" ? `${v.toLocaleString("en-US")}/mo` : "not measured");
  const pos = (v: number | null) => (typeof v === "number" ? `#${v}` : "not ranking");
  return [
    "## Competitor keywords selected by the operator — target these",
    "These measured keywords are the ones the operator chose for this asset. Use them as target terms " +
      "in headings, body and FAQ where they fit naturally. Do not invent volumes; the figures below " +
      "are DataForSEO's. Prefer the client's locked terminology when it conflicts. Keywords fetched " +
      "for these competitors and not listed here must not be used as target terms — even if they " +
      "also appear in the run's demand map.",
    "",
    ...rows.map((r, i) => {
      const who = r.competitors.length ? r.competitors.join(", ") : "a competitor";
      const client = r.clientKnown ? `, client ${pos(r.clientPosition)}` : "";
      return `${i + 1}. **${r.keyword}** — ${pos(r.position)} (${who})${client}, ${vol(r.volume)}`;
    }),
  ].join("\n");
}

/** What the stage may do with the competitor ranking keywords the card offered.
 *
 * Ticked → the selected list. Unticked everything (or nothing ticked) → an explicit ban on using
 * those rivals' ranking keywords. "" when the listing had no fetched keywords to choose from. */
export function keywordConstraintMarkdown(competitors: ValidatedCompetitor[], picks: string[]): string {
  const offered = uniqueFetchedKeywords(competitors);
  if (!offered.length) return "";
  const selected = selectedKeywordsMarkdown(competitors, picks);
  if (selected) return selected;
  return [
    "## Competitor keywords — none selected",
    "The operator reviewed every ranking keyword fetched for these competitors and selected none. " +
      "Do not use those competitors' ranking keywords as target terms in this asset — including any " +
      "that also appear in the run's demand map. Write only with the remaining keyword targets the " +
      "pipeline supplies.",
  ].join("\n");
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
