/** A section of an asset document, split into blocks the reader can show as visuals.
 *
 * The Deliverables reader used to render every asset as Markdown, which made a 58/100 CRO score, a
 * 52-offer value ladder or a five-message SMS sequence look exactly like prose. The structure is in
 * the documents: every prompt writes its own tables, scores and item formats. This module
 * recognises that structure so the reader can draw it. It stays **a re-layout, never a summary**:
 *
 *   - **Partition.** Each block records the exact text it consumed (`source`), and the sources of a
 *     section's blocks, joined in order, are the section body byte for byte. Anything no recogniser
 *     claims is a `markdown` block and renders exactly as it always did.
 *   - **Containment.** Each visual renders every word of its source. `smoke/visual.tsx` checks both
 *     over every real output in `manual_execution/`, not over fixtures written to match this file.
 *
 * Pure: no DOM, no React. Exports never read this; they use the raw text.
 */

export type TableVisual = "kanban" | "score" | "timeline" | "flow" | "kpi" | "data";

export interface ParsedTable {
  headers: string[];
  rows: string[][];
}

export interface OfferItem {
  title: string;
  format: string;
  /** `- Key: value` lines under the item, in order. */
  fields: { key: string; value: string }[];
  /** Anything under the item that is not a `- Key: value` line. */
  extra: string;
}

export interface SequenceStep {
  heading: string;
  timing: string;
  /** `**Key:** value` lines, in order (Timing included). */
  fields: { key: string; value: string }[];
  /** The rest of the step, verbatim Markdown. */
  body: string;
}

export interface ShowSlot {
  start: string;
  end: string;
  label: string;
  minutes: number;
}

export type VisualBlock =
  | { kind: "markdown"; source: string }
  | { kind: "gauge"; source: string; label: string; value: number; max: number }
  | {
      kind: "table";
      source: string;
      visual: TableVisual;
      table: ParsedTable;
      scoreColumns: number[];
      keyColumn: number;
      /** column -> the maximum its bare numbers are out of, for a rubric ("Total /25" over five 1-5
       * criteria). Absent for columns whose cells carry their own maximum ("4/10"). */
      columnMax: Record<number, number>;
      /** The column whose highest value marks the winning row, when the table ranks candidates. */
      totalColumn: number | null;
    }
  | { kind: "offers"; source: string; offers: OfferItem[] }
  | { kind: "cards"; source: string; cards: { title: string; body: string }[] }
  | { kind: "sequence"; source: string; level: number; steps: SequenceStep[] }
  | { kind: "runOfShow"; source: string; slots: ShowSlot[] }
  // The shapes below are the ones that used to fall through to plain Markdown in most real outputs:
  // `**Key:** value` runs, numbered findings with evidence under them, headings that carry a score,
  // a "**Working:**" lead over a list, lists of verbatim quotes, long prose and ASCII diagrams.
  | { kind: "fields"; source: string; rows: FieldRow[] }
  | { kind: "findings"; source: string; items: FindingItem[] }
  | { kind: "scoredHeading"; source: string; level: number; text: string; value: number; max: number }
  | { kind: "checklist"; source: string; title: string; body: string; tone: Tone }
  | { kind: "quotes"; source: string; items: string[] }
  | { kind: "prose"; source: string; lead: string; rest: string; restWords: number }
  | { kind: "diagram"; source: string; code: string };

/** A status read off the words of a label — never off colour alone; the label is always rendered. */
export type Tone = "good" | "warn" | "bad" | "neutral";

export interface FieldRow {
  key: string;
  value: string;
  /** Indented lines under the row (sub-bullets, a wrapped value), dedented Markdown. */
  extra: string;
}

export interface FindingItem {
  number: string;
  lead: string;
  /** The indented evidence under the item, dedented Markdown. */
  body: string;
  tone: Tone;
}

// --------------------------------------------------------------------------------------
// Cells and scores
// --------------------------------------------------------------------------------------

/** Markdown emphasis and code stripped, for classifying and grouping — never for display. */
export function plainCell(cell: string): string {
  return cell.replace(/[*_`]/g, "").trim();
}

const RATIO = /^(\d+(?:\.\d+)?)\s*\/\s*(5|10|20|25|50|100)$/;
const PERCENT = /^(\d{1,3}(?:\.\d+)?)\s?%$/;
const STARS = /^[★☆]{2,}$/;
const HEAT = /^(HIGH|MED|MEDIUM|LOW|NONE)$/i;
const STATUS = /^(✓|✗|✅|❌|⚠️?|✔️?)/;

export type ScoreValue =
  | { type: "ratio"; value: number; max: number }
  | { type: "stars"; value: number; max: number }
  | { type: "heat"; level: 0 | 1 | 2 | 3; label: string }
  | { type: "status"; state: "yes" | "partial" | "no" };

/** A score, if this cell is one. "58/100", "4/10", "80%", "★★★★☆", "HIGH", "✓ present" — or, in a
 * rubric column whose maximum is known, a bare "4". */
export function scoreOf(cell: string, max?: number): ScoreValue | null {
  const text = plainCell(cell);
  if (max !== undefined && /^\d+(?:\.\d+)?$/.test(text)) return { type: "ratio", value: Number(text), max };
  let m = RATIO.exec(text);
  if (m) return { type: "ratio", value: Number(m[1]), max: Number(m[2]) };
  m = PERCENT.exec(text);
  if (m) return { type: "ratio", value: Number(m[1]), max: 100 };
  if (STARS.test(text)) return { type: "stars", value: [...text].filter((c) => c === "★").length, max: [...text].length };
  m = HEAT.exec(text);
  if (m) {
    const w = m[1].toUpperCase();
    return { type: "heat", level: w === "HIGH" ? 3 : w.startsWith("MED") ? 2 : w === "LOW" ? 1 : 0, label: w };
  }
  m = STATUS.exec(text);
  if (m) {
    const c = m[1];
    return { type: "status", state: c.startsWith("✓") || c.startsWith("✅") || c.startsWith("✔") ? "yes" : c.startsWith("⚠") ? "partial" : "no" };
  }
  return null;
}

// --------------------------------------------------------------------------------------
// Tables
// --------------------------------------------------------------------------------------

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let current = "";
  let inCode = false;
  for (let i = 0; i < row.length; i++) {
    const ch = row[i];
    if (ch === "`") inCode = !inCode;
    if (ch === "\\" && row[i + 1] === "|") {
      current += "|";
      i++;
      continue;
    }
    if (ch === "|" && !inCode) {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  cells.push(current.trim());
  return cells;
}

const SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

export function parseTable(lines: string[]): ParsedTable | null {
  if (lines.length < 3 || !SEPARATOR.test(lines[1])) return null;
  const headers = splitRow(lines[0]);
  const rows = lines.slice(2).map(splitRow);
  if (headers.length < 2) return null;
  // Pad or trim to the header width so a ragged row never drops or invents a column's worth of
  // cells in the visual. Overflow cells are folded into the last one rather than discarded.
  const fixed = rows.map((r) => {
    if (r.length === headers.length) return r;
    if (r.length < headers.length) return [...r, ...Array(headers.length - r.length).fill("")];
    return [...r.slice(0, headers.length - 1), r.slice(headers.length - 1).join(" | ")];
  });
  return { headers, rows: fixed };
}

const TIME_HEADER = /^(timing|day|days|week|month|when|send(?: time)?|timeline|schedule|duration|time|offset)$/i;
const FLOW_FIRST = /^(stage|page|step|funnel stage)$/i;
const FLOW_OTHER = /goal|source|purpose|action|slug/i;
const KPI_HEADER = /^(metric|kpi|kpis|measure)$/i;
const KPI_OTHER = /target|baseline|month|benchmark|goal/i;
const PRIORITY_HEADER = /^(priority|tier)$/i;

/** Which visual a table gets, from its header signature and its cells. Order matters and is the
 * specification: a priority table with a "Timeline" column is a kanban, and a trigger map whose
 * cells are HIGH/MED/LOW is a heat table even though its first column is "Funnel stage". */
export interface TableClass {
  visual: TableVisual;
  scoreColumns: number[];
  keyColumn: number;
  columnMax: Record<number, number>;
  totalColumn: number | null;
}

const NUMBER = /^\d+(?:\.\d+)?$/;

/** A candidate scorecard: a "Total /25" column over bare-number criteria. Each criterion's maximum is
 * the total's maximum shared across them (25 over five criteria is /5), or failing that the highest
 * value written, rounded up to 5 or 10 — never a guess larger than what the table itself implies. */
function rubric(table: ParsedTable): TableClass | null {
  const headers = table.headers.map(plainCell);
  const total = headers.findIndex((h) => /\/\s*\d+\s*$/.test(h) || /^total/i.test(h));
  if (total <= 0) return null;
  const numeric = (col: number) => {
    const cells = table.rows.map((r) => plainCell(r[col] ?? "")).filter(Boolean);
    return cells.length >= 2 && cells.every((c) => NUMBER.test(c));
  };
  if (!numeric(total)) return null;
  const criteria = headers.map((_, col) => col).filter((col) => col > 0 && col !== total && numeric(col));
  const totalMax = Number(/\/\s*(\d+)\s*$/.exec(headers[total])?.[1] ?? NaN);
  const observed = (col: number) => Math.max(...table.rows.map((r) => Number(plainCell(r[col] ?? "0")) || 0));
  const roundUp = (n: number) => (n <= 5 ? 5 : n <= 10 ? 10 : Math.ceil(n / 10) * 10);
  const columnMax: Record<number, number> = {};
  columnMax[total] = Number.isFinite(totalMax) ? totalMax : roundUp(observed(total));
  const each = criteria.length && Number.isFinite(totalMax) && totalMax % criteria.length === 0 ? totalMax / criteria.length : null;
  for (const col of criteria) columnMax[col] = each ?? roundUp(observed(col));
  return { visual: "score", scoreColumns: [...criteria, total], keyColumn: 0, columnMax, totalColumn: total };
}

export function classifyTable(table: ParsedTable): TableClass {
  const headers = table.headers.map(plainCell);
  const rows = table.rows;
  const none = { scoreColumns: [], columnMax: {}, totalColumn: null };

  const priority = headers.findIndex((h) => PRIORITY_HEADER.test(h));
  // Most rows, not every row: "Ongoing" or "Later" is a real group too, and gets its own column.
  const ranked = priority >= 0 ? rows.filter((r) => /^(p[0-4]|tier\s*\d+|\d)$/i.test(plainCell(r[priority] ?? ""))).length : 0;
  if (priority >= 0 && rows.length >= 2 && ranked / rows.length >= 0.7) {
    return { visual: "kanban", keyColumn: priority, ...none };
  }

  const scored = rubric(table);
  if (scored) return scored;

  const scoreColumns = headers
    .map((_, col) => {
      const cells = rows.map((r) => r[col] ?? "").filter((c) => plainCell(c));
      if (!cells.length) return -1;
      const scored = cells.filter((c) => scoreOf(c)).length;
      return scored / cells.length >= 0.6 && scored >= 2 ? col : -1;
    })
    .filter((col) => col > 0);
  if (scoreColumns.length && rows.length >= 2) {
    const total = scoreColumns.find((col) => /total|overall/i.test(headers[col])) ?? null;
    return { visual: "score", scoreColumns, keyColumn: 0, columnMax: {}, totalColumn: total };
  }

  const time = headers.findIndex((h) => TIME_HEADER.test(h));
  if (time >= 0 && rows.length >= 3) return { visual: "timeline", keyColumn: time, ...none };

  if (FLOW_FIRST.test(headers[0]) && rows.length >= 3 && headers.slice(1).some((h) => FLOW_OTHER.test(h))) {
    return { visual: "flow", keyColumn: 0, ...none };
  }

  if (KPI_HEADER.test(headers[0]) && headers.slice(1).some((h) => KPI_OTHER.test(h)) && rows.length >= 2) {
    return { visual: "kpi", keyColumn: 0, ...none };
  }

  return { visual: "data", keyColumn: 0, ...none };
}

// --------------------------------------------------------------------------------------
// Line recognisers
// --------------------------------------------------------------------------------------

const GAUGE = /^\s*\**\s*(?:overall\s+)?(score|total score|overall(?: score)?|total)\s*:?\s*\**\s*:?\s*(\d+(?:\.\d+)?)\s*\/\s*(10|25|50|100)\b\s*\**\s*\.?\s*$/i;
const OFFER_LEAD = /^\s*\d{1,2}\.\s+\**\s*\[([^\]]+)\]\s*:?\s*[“"]([^”"]+)[”"]/;
const OFFER_FIELD = /^\s+[-*]\s*([A-Z][A-Za-z /&()-]{1,40}?)\s*:\s*(.*)$/;
const BOLD_LINE = /^\*\*([^*][^*]*?)\*\*\s*$/;
const HEADING = /^(#{2,6})\s+(.*?)\s*#*\s*$/;
const SHOW = /^\s*[-*]\s*(\d{1,2}:\d{2})\s*[–—-]\s*(\d{1,2}:\d{2})\s*[—–-]+\s*(.+)$/;
const TIMING_TEXT = /\b(day\s*\d+|\+\s*\d+\s*(?:days?|hours?|hrs?|weeks?)|immediate(?:ly)?|week\s*\d+|month\s*\d+|hour\s*\d+)\b/i;
const FIELD_LINE = /^\*\*([^*:]{1,40}):\*\*\s*(.*)$/;

function minutesOf(clock: string): number {
  const [h, m] = clock.split(":").map(Number);
  // "0:03" in a run of show is hours:minutes; "59:00" would be minutes:seconds, which no prompt
  // writes. Treated as h:mm throughout.
  return h * 60 + m;
}

/** Lines of `lines[from..]` belonging to one numbered offer: the lead, then indented or blank lines
 * up to the next unindented line. Trailing blank lines are left for whatever follows. */
function offerExtent(lines: string[], from: number): number {
  let end = from + 1;
  let lastContent = from;
  while (end < lines.length) {
    const line = lines[end];
    if (!line.trim()) {
      end++;
      continue;
    }
    if (/^\s/.test(line) && !OFFER_LEAD.test(line)) {
      lastContent = end;
      end++;
      continue;
    }
    break;
  }
  return lastContent + 1;
}

function isFence(line: string): boolean {
  return /^\s*(```|~~~)/.test(line);
}

// The lower-priority recognisers. They run only where none of the ones above claimed the line, so
// adding them cannot change how an existing block is recognised.

/** `**Key:** value` / `**Key**: value` / `- **Key.** value`. A period lead is a short label only, so
 * a bolded opening sentence ("**The form has no labels.** It…") is not mistaken for a key. */
const FIELD_IN = /^\s{0,3}(?:[-*+]\s+)?\*\*([^*\n]{1,60}?)\s*:\s*\*\*\s*(\S.*)$/;
const FIELD_OUT = /^\s{0,3}(?:[-*+]\s+)?\*\*([^*\n]{1,60}?)\*\*\s*[:—–]\s*(\S.*)$/;
const FIELD_DOT = /^\s{0,3}(?:[-*+]\s+)?\*\*([^*\n]{1,40}?)\.\*\*\s*(\S.*)$/;
const NUMBERED = /^(\s{0,3})(\d{1,3})[.)]\s+(\S.*)$/;
const BULLET = /^\s{0,3}[-*+]\s+(\S.*)$/;
const LIST_ITEM = /^\s*(?:[-*+]|\d{1,3}[.)])\s+\S/;
const COLON_LEAD = /^\s{0,3}\*\*([^*\n]+?:)\*\*\s*$|^\s{0,3}\*\*([^*\n]+?)\*\*:\s*$/;
const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const HEADING_ANY = /^\s{0,3}#{1,6}\s/;
const SCORE_IN_TEXT = /(\d+(?:\.\d+)?)\s*\/\s*(5|10|20|25|50|100)\b/;
const DIAGRAM_CHARS = /[─│┌┐└┘├┤┬┴┼═║╔╗╚╝▼▲►◄▶◀→←↓↑]|-->|==>|<--|\+-{2,}\+/;
const DIAGRAM_LANGS = new Set(["", "text", "txt", "plaintext", "ascii", "diagram"]);

function fieldOf(line: string): { key: string; value: string } | null {
  const m = FIELD_IN.exec(line) ?? FIELD_OUT.exec(line) ?? FIELD_DOT.exec(line);
  return m ? { key: m[1].trim(), value: m[2].trim() } : null;
}

export function toneOf(label: string): Tone {
  const t = plainCell(label).toLowerCase();
  if (/^(✗|❌)|\b(fail(?:ing|s|ed)?|broken|missing|problems?|faults?|critical|blocker|wrong)\b/.test(t)) return "bad";
  if (/^⚠|\b(partial(?:ly)?|risks?|weak(?:est)?|caution|watch|fix(?:es)?|gaps?|issues?)\b/.test(t)) return "warn";
  if (/^(✓|✅|✔)|\b(working|works|strengths?|strong(?:est)?|keep|pass(?:es|ed)?|wins?)\b/.test(t)) return "good";
  return "neutral";
}

function wordCount(text: string): number {
  return text.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length;
}

/** Remove the common leading indent, so nested bullets parse as a list rather than as code. */
function dedent(lines: string[]): string {
  const indents = lines.filter((l) => l.trim()).map((l) => /^\s*/.exec(l)![0].length);
  const min = indents.length ? Math.min(...indents) : 0;
  return lines.map((l) => l.slice(Math.min(min, /^\s*/.exec(l)![0].length))).join("\n").trim();
}

/** A line of ordinary paragraph text — nothing any other rule reads as structure. */
function isParagraphLine(line: string): boolean {
  const t = line.trim();
  if (!t) return false;
  if (/^\s{4,}/.test(line) || HEADING_ANY.test(line) || LIST_ITEM.test(line) || RULE.test(line)) return false;
  if (t.startsWith("|") || t.startsWith(">") || t.startsWith("<") || isFence(line)) return false;
  if (BOLD_LINE.test(t) || COLON_LEAD.test(line) || fieldOf(line) || GAUGE.test(line)) return false;
  return true;
}

/** The index past the last non-blank line in `[from, to)` — trailing blank lines are left for
 * whatever follows, like every other block here. */
function trimEnd(lines: string[], from: number, to: number): number {
  let end = to;
  while (end > from && !lines[end - 1].trim()) end--;
  return end;
}

/** Where a numbered item's indented continuation ends. */
function itemExtent(lines: string[], from: number, indent: number): number {
  let end = from + 1;
  while (end < lines.length) {
    const line = lines[end];
    if (!line.trim()) {
      end++;
      continue;
    }
    // Continuation is anything indented past the item's own marker; the next item or any
    // unindented line ends it.
    if (/^\s*/.exec(line)![0].length >= indent + 2) {
      end++;
      continue;
    }
    break;
  }
  return trimEnd(lines, from + 1, end);
}

// --------------------------------------------------------------------------------------
// The partition
// --------------------------------------------------------------------------------------

/** A block before its source is attached. Distributive, so each member keeps its own fields. */
type Unsourced = VisualBlock extends infer B ? (B extends VisualBlock ? Omit<B, "source"> : never) : never;

/** Split one section body into blocks. `joinSources(parseVisualBlocks(body)) === body`, always. */
export function parseVisualBlocks(body: string): VisualBlock[] {
  const lines = body.split("\n");
  const claimed: { start: number; end: number; block: Unsourced }[] = [];
  const text = (start: number, end: number) => lines.slice(start, end).join("\n");

  let i = 0;
  let inFence = false;
  while (i < lines.length) {
    const line = lines[i];
    if (isFence(line)) {
      // An ASCII diagram (box-drawing or arrows in a plain fence) is drawn as a panel. Every other
      // fenced block is never parsed — code stays exactly as written.
      const open = /^\s*(`{3,}|~{3,})\s*([\w-]*)\s*$/.exec(line);
      if (!inFence && open && DIAGRAM_LANGS.has(open[2].toLowerCase())) {
        let close = i + 1;
        while (close < lines.length && !lines[close].trim().startsWith(open[1])) close++;
        const code = lines.slice(i + 1, close).join("\n");
        if (close < lines.length && DIAGRAM_CHARS.test(code)) {
          claimed.push({ start: i, end: close + 1, block: { kind: "diagram", code } });
          i = close + 1;
          continue;
        }
      }
      inFence = !inFence;
      i++;
      continue;
    }
    if (inFence) {
      i++;
      continue;
    }

    // Table.
    if (line.trim().startsWith("|") && i + 1 < lines.length && SEPARATOR.test(lines[i + 1])) {
      let end = i + 2;
      while (end < lines.length && lines[end].trim().startsWith("|")) end++;
      const table = parseTable(lines.slice(i, end));
      if (table && table.rows.length) {
        const c = classifyTable(table);
        claimed.push({ start: i, end, block: { kind: "table", table, ...c } });
        i = end;
        continue;
      }
    }

    // Gauge.
    const gauge = GAUGE.exec(line);
    if (gauge) {
      claimed.push({
        start: i,
        end: i + 1,
        block: { kind: "gauge", label: gauge[1], value: Number(gauge[2]), max: Number(gauge[3]) },
      });
      i++;
      continue;
    }

    // Offer items (a run of them, blank lines between allowed).
    if (OFFER_LEAD.test(line)) {
      const offers: OfferItem[] = [];
      let end = i;
      let cursor = i;
      while (cursor < lines.length && OFFER_LEAD.test(lines[cursor])) {
        const stop = offerExtent(lines, cursor);
        const lead = OFFER_LEAD.exec(lines[cursor])!;
        const fields: { key: string; value: string }[] = [];
        const extra: string[] = [];
        // Anything on the lead line after the title — "(3-part)", "(already live)" — is kept.
        const after = lines[cursor].slice(lead.index + lead[0].length).replace(/^\s*\**\s*/, "").trim();
        if (after) extra.push(after);
        for (const sub of lines.slice(cursor + 1, stop)) {
          const f = OFFER_FIELD.exec(sub);
          if (f) fields.push({ key: f[1].trim(), value: f[2].trim() });
          else if (sub.trim()) extra.push(sub.trim());
        }
        offers.push({ title: lead[2].trim(), format: lead[1].trim(), fields, extra: extra.join("\n") });
        end = stop;
        cursor = stop;
        while (cursor < lines.length && !lines[cursor].trim()) cursor++;
      }
      if (offers.length) {
        claimed.push({ start: i, end, block: { kind: "offers", offers } });
        i = end;
        continue;
      }
    }

    // Run of show: three or more timed slots in a row.
    if (SHOW.test(line)) {
      let end = i;
      while (end < lines.length && SHOW.test(lines[end])) end++;
      if (end - i >= 3) {
        const slots = lines.slice(i, end).map((l) => {
          const m = SHOW.exec(l)!;
          return { start: m[1], end: m[2], label: m[3].trim(), minutes: Math.max(0, minutesOf(m[2]) - minutesOf(m[1])) };
        });
        claimed.push({ start: i, end, block: { kind: "runOfShow", slots } });
        i = end;
        continue;
      }
    }

    // Timed headings: a run of same-level headings, each of which is timed.
    const heading = HEADING.exec(line);
    if (heading) {
      const level = heading[1].length;
      const starts: number[] = [];
      let cursor = i;
      let end = i;
      while (cursor < lines.length) {
        const h = HEADING.exec(lines[cursor]);
        if (!h || h[1].length !== level) break;
        let stop = cursor + 1;
        let fence = false;
        while (stop < lines.length) {
          if (isFence(lines[stop])) fence = !fence;
          const next = fence ? null : HEADING.exec(lines[stop]);
          if (next && next[1].length <= level) break;
          stop++;
        }
        const chunk = text(cursor, stop);
        const timed = TIMING_TEXT.test(h[2]) || /\*\*Timing:\*\*/i.test(chunk);
        if (!timed) break;
        starts.push(cursor);
        end = stop;
        cursor = stop;
      }
      if (starts.length >= 2) {
        const steps = starts.map((s, k) => {
          const stop = k + 1 < starts.length ? starts[k + 1] : end;
          const h = HEADING.exec(lines[s])!;
          const fields: { key: string; value: string }[] = [];
          const rest: string[] = [];
          for (const sub of lines.slice(s + 1, stop)) {
            const f = FIELD_LINE.exec(sub.trim());
            if (f && f[2].trim()) fields.push({ key: f[1].trim(), value: f[2].trim() });
            else rest.push(sub);
          }
          const timing = fields.find((f) => /^timing$/i.test(f.key))?.value ?? TIMING_TEXT.exec(h[2])?.[0] ?? "";
          return { heading: h[2].trim(), timing, fields, body: rest.join("\n").trim() };
        });
        // Trailing blank lines and a closing rule are left as Markdown for the next block.
        let tail = end;
        while (tail > starts[0] && /^\s*(---+)?\s*$/.test(lines[tail - 1])) tail--;
        claimed.push({ start: i, end: tail, block: { kind: "sequence", level, steps } });
        i = tail;
        continue;
      }
    }

    // Bold-lead cards: three or more whole-line bold leads, each followed by content.
    if (BOLD_LINE.test(line) && !/:\s*\**\s*$/.test(line)) {
      const leads: number[] = [];
      let cursor = i;
      let end = i;
      while (cursor < lines.length) {
        if (!BOLD_LINE.test(lines[cursor]) || /:\s*\**\s*$/.test(lines[cursor])) break;
        let stop = cursor + 1;
        while (
          stop < lines.length &&
          !(BOLD_LINE.test(lines[stop]) && !/:\s*\**\s*$/.test(lines[stop])) &&
          !HEADING.test(lines[stop]) &&
          !/^\s*---+\s*$/.test(lines[stop]) &&
          !lines[stop].trim().startsWith("|") &&
          !isFence(lines[stop])
        ) {
          stop++;
        }
        if (!lines.slice(cursor + 1, stop).some((l) => l.trim())) break; // a lead with nothing under it is a subhead
        leads.push(cursor);
        end = stop;
        cursor = stop;
      }
      if (leads.length >= 3) {
        let tail = end;
        while (tail > leads[0] && !lines[tail - 1].trim()) tail--;
        const cards = leads.map((s, k) => {
          const stop = k + 1 < leads.length ? leads[k + 1] : tail;
          return { title: BOLD_LINE.exec(lines[s])![1].trim(), body: text(s + 1, stop).trim() };
        });
        claimed.push({ start: i, end: tail, block: { kind: "cards", cards } });
        i = tail;
        continue;
      }
    }

    // A heading that carries a score: "### Layer 2 — Oxytocin: 4/10 — weakest layer".
    if (heading) {
      const s = SCORE_IN_TEXT.exec(heading[2]);
      if (s) {
        claimed.push({
          start: i,
          end: i + 1,
          block: { kind: "scoredHeading", level: heading[1].length, text: heading[2].trim(), value: Number(s[1]), max: Number(s[2]) },
        });
        i++;
        continue;
      }
    }

    // `**Key:** value` rows: two or more, blank lines between allowed, indented lines under a row kept
    // with it.
    if (fieldOf(line)) {
      const rows: FieldRow[] = [];
      let cursor = i;
      let end = i;
      while (cursor < lines.length) {
        const f = fieldOf(lines[cursor]);
        if (!f || GAUGE.test(lines[cursor])) break;
        let stop = cursor + 1;
        while (stop < lines.length && (!lines[stop].trim() || /^\s{2,}\S/.test(lines[stop])) && !fieldOf(lines[stop])) stop++;
        stop = trimEnd(lines, cursor + 1, stop);
        rows.push({ ...f, extra: dedent(lines.slice(cursor + 1, stop)) });
        end = stop;
        cursor = stop;
        while (cursor < lines.length && !lines[cursor].trim()) cursor++;
      }
      if (rows.length >= 2) {
        claimed.push({ start: i, end, block: { kind: "fields", rows } });
        i = end;
        continue;
      }
    }

    // "**Working:**" / "**Failing — five faults:**" over a list.
    const colon = COLON_LEAD.exec(line);
    if (colon) {
      let start = i + 1;
      while (start < lines.length && !lines[start].trim()) start++;
      if (start < lines.length && LIST_ITEM.test(lines[start]) && start - i <= 2) {
        let end = start;
        while (end < lines.length) {
          const l = lines[end];
          if (LIST_ITEM.test(l) || /^\s{2,}\S/.test(l)) end++;
          else if (!l.trim() && end + 1 < lines.length && (LIST_ITEM.test(lines[end + 1]) || /^\s{2,}\S/.test(lines[end + 1]))) end++;
          else break;
        }
        const title = (colon[1] ?? colon[2]).trim();
        claimed.push({ start: i, end, block: { kind: "checklist", title, body: dedent(lines.slice(start, end)), tone: toneOf(title) } });
        i = end;
        continue;
      }
    }

    // Numbered findings: items with a bold lead or indented evidence under them.
    const numbered = NUMBERED.exec(line);
    if (numbered && !OFFER_LEAD.test(line)) {
      const indent = numbered[1].length;
      const items: FindingItem[] = [];
      let cursor = i;
      let end = i;
      while (cursor < lines.length) {
        const n = NUMBERED.exec(lines[cursor]);
        if (!n || n[1].length !== indent || OFFER_LEAD.test(lines[cursor])) break;
        const stop = itemExtent(lines, cursor, indent);
        const body = dedent(lines.slice(cursor + 1, stop));
        if (!n[3].startsWith("**") && !body) break;
        items.push({ number: n[2], lead: n[3].trim(), body, tone: toneOf(n[3]) });
        end = stop;
        cursor = stop;
        while (cursor < lines.length && !lines[cursor].trim()) cursor++;
      }
      if (items.length >= 2) {
        claimed.push({ start: i, end, block: { kind: "findings", items } });
        i = end;
        continue;
      }
    }

    // A list of verbatim quotes.
    if (BULLET.test(line)) {
      let end = i;
      while (end < lines.length && BULLET.test(lines[end])) end++;
      const items = lines.slice(i, end).map((l) => BULLET.exec(l)![1].trim());
      const quoted = items.filter((t) => /^[*_]*\s*["“‘']/.test(t)).length;
      if (items.length >= 3 && quoted / items.length >= 0.6) {
        claimed.push({ start: i, end, block: { kind: "quotes", items } });
        i = end;
        continue;
      }
    }

    // Long prose: two or more paragraphs, collapsed after the first once the rest is long enough to
    // be worth folding.
    if (isParagraphLine(line)) {
      const paragraphs: number[] = [i];
      let cursor = i;
      let end = i;
      while (cursor < lines.length) {
        if (isParagraphLine(lines[cursor])) {
          end = cursor + 1;
          cursor++;
          continue;
        }
        if (!lines[cursor].trim()) {
          let next = cursor;
          while (next < lines.length && !lines[next].trim()) next++;
          if (next < lines.length && isParagraphLine(lines[next])) {
            paragraphs.push(next);
            cursor = next;
            continue;
          }
        }
        break;
      }
      if (paragraphs.length >= 2) {
        const rest = text(paragraphs[1], end);
        const restWords = wordCount(rest);
        if (restWords >= 80) {
          claimed.push({ start: i, end, block: { kind: "prose", lead: text(i, paragraphs[1]).trim(), rest, restWords } });
          i = end;
          continue;
        }
      }
      i = end > i ? end : i + 1;
      continue;
    }

    i++;
  }

  // Everything unclaimed is Markdown, so the sources rejoin to the body exactly.
  const blocks: VisualBlock[] = [];
  let cursor = 0;
  for (const c of claimed) {
    if (c.start > cursor) blocks.push({ kind: "markdown", source: text(cursor, c.start) });
    blocks.push({ ...c.block, source: text(c.start, c.end) } as VisualBlock);
    cursor = c.end;
  }
  if (cursor < lines.length || !blocks.length) blocks.push({ kind: "markdown", source: text(cursor, lines.length) });
  return blocks;
}

/** The inverse of the partition — what `smoke/visual.tsx` compares against the body. */
export function joinSources(blocks: VisualBlock[]): string {
  return blocks.map((b) => b.source).join("\n");
}

/** True when the section holds anything worth drawing — the reader shows plain text otherwise. */
export function hasVisuals(blocks: VisualBlock[]): boolean {
  return blocks.some((b) => b.kind !== "markdown");
}
