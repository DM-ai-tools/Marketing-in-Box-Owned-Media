import { createContext, Fragment, useContext, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Markdown } from "../../components/Markdown";
import { HtmlPreview } from "../../components/HtmlPreview";
import { splitHtmlBlocks } from "../../lib/htmlBlocks";
import {
  parseVisualBlocks,
  plainCell,
  scoreOf,
  type FieldRow,
  type FindingItem,
  type OfferItem,
  type ScoreValue,
  type SequenceStep,
  type ShowSlot,
  type Tone,
  type VisualBlock,
} from "../../lib/visualBlocks";
import { ratioTone as tone, toneColor } from "./tone";

/* The reader's Visual view. Every component here draws one block from `lib/visualBlocks.ts` and
 * renders **every word of its source** — the bar next to "6/10" is added, never substituted for it,
 * so meaning is never carried by colour alone and nothing is lost when a table becomes a timeline.
 * `smoke/visual.tsx` checks that over every real output. Tokens only (`var(--…)`), so dark mode
 * needs nothing of its own. */

// --------------------------------------------------------------------------------------
// Primitives
// --------------------------------------------------------------------------------------

/** How the blocks are being drawn: set by the asset's template (`lib/assetTemplates.ts`) and by the
 * HTML report, which has no JavaScript and no app around it. */
export interface VisualOptions {
  /** Long-form reading (a blog, a book): prose is shown in full rather than folded. */
  expandProse: boolean;
  /** A standalone file: HTML page blocks are embedded as a sandboxed frame plus their source,
   * rather than through the app's interactive preview. */
  staticHtml: boolean;
}

export const VisualOptionsContext = createContext<VisualOptions>({ expandProse: false, staticHtml: false });

/** One cell or title of Markdown, rendered inline — bold, code and links kept, no paragraph box. */
export function Inline({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <>{children}</>,
        code: ({ children }) => <code className="rounded bg-[var(--bg-sunken)] px-1 font-mono text-[0.85em]">{children}</code>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}


/** A score's picture: a bar for "6/10", pips for stars, a heat chip for HIGH/MED/LOW. The cell's own
 * text is rendered beside it by the caller. */
function ScoreMark({ score }: { score: ScoreValue }) {
  if (score.type === "ratio" || score.type === "stars") {
    const ratio = score.max ? Math.max(0, Math.min(1, score.value / score.max)) : 0;
    return (
      <span className="mr-1.5 inline-block h-1.5 w-12 overflow-hidden rounded-full bg-[var(--bg-sunken)] align-middle" aria-hidden>
        <span className="block h-full rounded-full" style={{ width: `${ratio * 100}%`, backgroundColor: tone(ratio) }} />
      </span>
    );
  }
  if (score.type === "heat") {
    const bg = ["var(--bg-sunken)", "color-mix(in srgb, var(--color-electric-blue) 18%, transparent)", "color-mix(in srgb, var(--color-electric-blue) 40%, transparent)", "var(--color-electric-blue)"][score.level];
    return <span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ backgroundColor: bg }} aria-hidden />;
  }
  const color = score.state === "yes" ? "var(--color-signal-green)" : score.state === "partial" ? "var(--color-signal-amber)" : "var(--color-signal-orange)";
  return <span className="mr-1 inline-block h-2 w-2 rounded-full align-middle" style={{ backgroundColor: color }} aria-hidden />;
}

export function Gauge({ value, max, label, size = 96 }: { value: number; max: number; label?: string; size?: number }) {
  const ratio = max ? Math.max(0, Math.min(1, value / max)) : 0;
  const r = 40;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-3">
      <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label={`${label ?? "Score"} ${value} out of ${max}`}>
        <circle cx="50" cy="50" r={r} fill="none" stroke="var(--bg-sunken)" strokeWidth="10" />
        <circle
          cx="50"
          cy="50"
          r={r}
          fill="none"
          stroke={tone(ratio)}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${c * ratio} ${c}`}
          transform="rotate(-90 50 50)"
        />
        <text x="50" y="55" textAnchor="middle" fontSize="22" fontWeight="700" fill="var(--fg)">
          {value}
        </text>
      </svg>
      {label !== undefined && (
        <div>
          <div className="text-[0.7rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">{label}</div>
          {/* One string, as the document wrote it: "58/100", not "58" beside "/100". */}
          <div className="text-[1.3rem] font-semibold tabular-nums">{`${value}/${max}`}</div>
        </div>
      )}
    </div>
  );
}

function Card({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <div
      className="min-w-0 rounded-xl border bg-[var(--bg-raised)] px-3 py-2.5 text-[0.84rem] leading-snug"
      style={{ borderColor: accent ? "var(--color-electric-blue)" : "var(--border)" }}
    >
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  if (!plainCell(value) && !plainCell(label)) return null;
  return (
    <div className="mt-1 min-w-0">
      <span className="text-[0.66rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
        <Inline text={label} />
      </span>{" "}
      <span className="break-words">
        <Inline text={value || "—"} />
      </span>
    </div>
  );
}

// --------------------------------------------------------------------------------------
// Tables
// --------------------------------------------------------------------------------------

type TableBlock = Extract<VisualBlock, { kind: "table" }>;

function ScoreTable({ block }: { block: TableBlock }) {
  const { headers, rows } = block.table;
  const cols = new Set(block.scoreColumns);
  const winner = useMemo(() => {
    if (block.totalColumn === null) return -1;
    let best = -1;
    let bestValue = -Infinity;
    rows.forEach((r, i) => {
      const s = scoreOf(r[block.totalColumn!] ?? "", block.columnMax[block.totalColumn!]);
      const v = s && (s.type === "ratio" || s.type === "stars") ? s.value / (s.max || 1) : NaN;
      if (v > bestValue) {
        bestValue = v;
        best = i;
      }
    });
    return best;
  }, [block, rows]);

  return (
    <div className="md-scroll my-2">
      <table className="w-full border-collapse text-[0.82rem]">
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th key={i} className="border-b border-[var(--border)] py-1.5 pr-3 text-left font-semibold">
                <Inline text={h} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} style={ri === winner ? { backgroundColor: "color-mix(in srgb, var(--color-signal-green) 10%, transparent)" } : undefined}>
              {r.map((cell, ci) => {
                const score = cols.has(ci) ? scoreOf(cell, block.columnMax[ci]) : null;
                return (
                  <td key={ci} className="border-b border-[var(--border)] py-1.5 pr-3 align-top">
                    {score && <ScoreMark score={score} />}
                    <Inline text={cell} />
                    {ci === 0 && ri === winner && (
                      <span className="ml-1.5 rounded-full px-1.5 py-0.5 text-[0.62rem] font-semibold text-white" style={{ backgroundColor: "var(--color-signal-green)" }}>
                        Top
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Timeline({ block }: { block: TableBlock }) {
  const { headers, rows } = block.table;
  const key = block.keyColumn;
  const titleCol = headers.findIndex((_, i) => i !== key);
  return (
    <ol className="relative my-3 ml-2 border-l-2 border-[var(--border)] pl-4">
      {rows.map((r, ri) => (
        <li key={ri} className="relative mb-3 last:mb-0">
          <span className="absolute -left-[1.4rem] top-1 h-3 w-3 rounded-full border-2 border-[var(--bg)]" style={{ backgroundColor: "var(--color-electric-blue)" }} aria-hidden />
          <div className="text-[0.72rem] font-semibold text-[var(--fg-muted)]">
            <Inline text={headers[key]} />: <Inline text={r[key] || "—"} />
          </div>
          <Card>
            <div className="font-semibold">
              <span className="sr-only">
                <Inline text={headers[titleCol]} />:{" "}
              </span>
              <Inline text={r[titleCol] ?? ""} />
            </div>
            {headers.map((h, ci) => (ci === key || ci === titleCol ? null : <Field key={ci} label={h} value={r[ci] ?? ""} />))}
          </Card>
        </li>
      ))}
    </ol>
  );
}

function FlowStrip({ block }: { block: TableBlock }) {
  const { headers, rows } = block.table;
  return (
    <div className="my-3 flex gap-2 overflow-x-auto pb-2" role="list">
      {rows.map((r, ri) => (
        <Fragment key={ri}>
          <div role="listitem" className="w-[15rem] shrink-0">
            <Card accent={ri === 0}>
              <div className="text-[0.64rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
                <Inline text={headers[0]} /> {ri + 1}
              </div>
              <div className="font-semibold">
                <Inline text={r[0]} />
              </div>
              {headers.slice(1).map((h, ci) => (
                <Field key={ci} label={h} value={r[ci + 1] ?? ""} />
              ))}
            </Card>
          </div>
          {ri < rows.length - 1 && (
            <div className="flex shrink-0 items-center text-[var(--fg-faint)]" aria-hidden>
              →
            </div>
          )}
        </Fragment>
      ))}
    </div>
  );
}

function Kanban({ block }: { block: TableBlock }) {
  const { headers, rows } = block.table;
  const key = block.keyColumn;
  const groups = useMemo(() => {
    const order: string[] = [];
    const byKey = new Map<string, string[][]>();
    for (const r of rows) {
      const k = plainCell(r[key] ?? "");
      if (!byKey.has(k)) {
        byKey.set(k, []);
        order.push(k);
      }
      byKey.get(k)!.push(r);
    }
    return order.map((k) => ({ key: k, rows: byKey.get(k)! }));
  }, [rows, key]);
  return (
    <div className="my-3 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(13rem, 1fr))" }}>
      {groups.map((g) => (
        <div key={g.key} className="min-w-0 rounded-xl bg-[var(--bg-sunken)] p-2">
          <div className="mb-1.5 flex items-center justify-between px-1 text-[0.74rem] font-semibold">
            <span>
              <Inline text={headers[key]} /> {g.key}
            </span>
            <span className="text-[var(--fg-faint)]">{g.rows.length}</span>
          </div>
          <div className="space-y-1.5">
            {g.rows.map((r, ri) => (
              <Card key={ri}>
                {headers.map((h, ci) =>
                  ci === key ? null : ci === (key === 0 ? 1 : 0) ? (
                    <div key={ci} className="font-medium">
                      <span className="sr-only">
                        <Inline text={h} />:{" "}
                      </span>
                      <Inline text={r[ci] ?? ""} />
                    </div>
                  ) : (
                    <Field key={ci} label={h} value={r[ci] ?? ""} />
                  ),
                )}
                <span className="sr-only">
                  <Inline text={r[key] ?? ""} />
                </span>
              </Card>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function KpiTiles({ block }: { block: TableBlock }) {
  const { headers, rows } = block.table;
  return (
    <div className="my-3 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(12rem, 1fr))" }}>
      {rows.map((r, ri) => (
        <Card key={ri}>
          <div className="text-[0.64rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
            <Inline text={headers[0]} />
          </div>
          <div className="font-semibold">
            <Inline text={r[0]} />
          </div>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
            {headers.slice(1).map((h, ci) => (
              <Fragment key={ci}>
                {ci > 0 && <span className="text-[var(--fg-faint)]" aria-hidden>→</span>}
                <span className="min-w-0">
                  <span className="block text-[0.62rem] font-semibold uppercase text-[var(--fg-faint)]">
                    <Inline text={h} />
                  </span>
                  <span className="text-[0.95rem] font-semibold tabular-nums">
                    <Inline text={r[ci + 1] || "—"} />
                  </span>
                </span>
              </Fragment>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

function TableView({ block }: { block: TableBlock }) {
  switch (block.visual) {
    case "score":
      return <ScoreTable block={block} />;
    case "timeline":
      return <Timeline block={block} />;
    case "flow":
      return <FlowStrip block={block} />;
    case "kanban":
      return <Kanban block={block} />;
    case "kpi":
      return <KpiTiles block={block} />;
    default:
      return <DataTable block={block} />;
  }
}

/** A table with no recognisable shape: still a table, with a banded body and the first column
 * carrying the row's name. */
function DataTable({ block }: { block: TableBlock }) {
  const { headers, rows } = block.table;
  return (
    <div className="md-scroll my-3 rounded-xl border border-[var(--border)]">
      <table className="w-full border-collapse text-[0.82rem]">
        <thead className="bg-[var(--bg-sunken)]">
          <tr>
            {headers.map((h, i) => (
              <th key={i} className="border-b border-[var(--border-strong)] px-3 py-2 text-left align-bottom text-[0.72rem] font-semibold uppercase tracking-wide text-[var(--fg-muted)]">
                <Inline text={h} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className="even:bg-[var(--bg-sunken)]">
              {r.map((cell, ci) => (
                <td key={ci} className={`border-b border-[var(--border)] px-3 py-2 align-top ${ci === 0 ? "font-semibold" : ""}`}>
                  <Inline text={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------------------
// Line blocks
// --------------------------------------------------------------------------------------

function fieldValue(offer: OfferItem, key: RegExp): string | undefined {
  return offer.fields.find((f) => key.test(f.key))?.value;
}

function OfferGrid({ offers }: { offers: OfferItem[] }) {
  return (
    <div className="my-3 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(15rem, 1fr))" }}>
      {offers.map((o, i) => {
        const price = fieldValue(o, /^price$/i);
        return (
          <Card key={i}>
            <div className="flex items-start justify-between gap-2">
              <span className="rounded-full bg-[var(--bg-sunken)] px-1.5 py-0.5 text-[0.62rem] font-semibold uppercase tracking-wide text-[var(--fg-muted)]">
                {o.format}
              </span>
              {price && (
                <span className="text-right text-[0.92rem] font-semibold tabular-nums" style={{ color: "var(--color-electric-blue)" }}>
                  <Inline text={price.split(/\s[—–-]\s/)[0]} />
                </span>
              )}
            </div>
            <div className="mt-1 font-semibold">“{o.title}”</div>
            {o.extra && (
              <div className="text-[0.76rem] text-[var(--fg-muted)]">
                <Inline text={o.extra} />
              </div>
            )}
            {o.fields.map((f, fi) => (
              <Field key={fi} label={f.key} value={f.value} />
            ))}
          </Card>
        );
      })}
    </div>
  );
}

function CardGrid({ cards }: { cards: { title: string; body: string }[] }) {
  return (
    <div className="my-3 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(16rem, 1fr))" }}>
      {cards.map((c, i) => (
        <Card key={i}>
          <div className="font-semibold">
            <Inline text={c.title} />
          </div>
          <div className="mt-1 text-[0.82rem] [&_.prose-block]:text-[0.82rem]">
            <Markdown text={c.body} />
          </div>
        </Card>
      ))}
    </div>
  );
}

function Sequence({ steps }: { steps: SequenceStep[] }) {
  return (
    <ol className="relative my-3 ml-2 border-l-2 border-[var(--border)] pl-4">
      {steps.map((s, i) => (
        <li key={i} className="relative mb-3 last:mb-0">
          <span className="absolute -left-[1.4rem] top-1 h-3 w-3 rounded-full border-2 border-[var(--bg)]" style={{ backgroundColor: "var(--color-electric-blue)" }} aria-hidden />
          {s.timing && <div className="text-[0.72rem] font-semibold text-[var(--fg-muted)]">{s.timing}</div>}
          <Card>
            <div className="font-semibold">
              <Inline text={s.heading} />
            </div>
            {/* Short facts (timing, layer, character count) as chips; a sentence-long one as a row. */}
            <div className="mt-0.5 flex flex-wrap gap-1">
              {s.fields
                .filter((f) => f.value.length <= 90)
                .map((f, fi) => (
                  <span key={fi} className="rounded-md bg-[var(--bg-sunken)] px-1.5 py-0.5 text-[0.7rem]">
                    <span className="font-semibold">{f.key}:</span> <Inline text={f.value} />
                  </span>
                ))}
            </div>
            {s.fields
              .filter((f) => f.value.length > 90)
              .map((f, fi) => (
                <Field key={fi} label={f.key} value={f.value} />
              ))}
            {s.body && (
              <div className="mt-1.5">
                <Markdown text={s.body} />
              </div>
            )}
          </Card>
        </li>
      ))}
    </ol>
  );
}

function RunOfShow({ slots }: { slots: ShowSlot[] }) {
  const total = slots.reduce((n, s) => n + s.minutes, 0) || 1;
  return (
    <div className="my-3">
      <div className="flex h-7 w-full overflow-hidden rounded-lg" aria-hidden>
        {slots.map((s, i) => (
          <div
            key={i}
            title={`${s.start}–${s.end} ${s.label}`}
            className="h-full border-r border-[var(--bg)] last:border-r-0"
            style={{
              width: `${(s.minutes / total) * 100}%`,
              backgroundColor: `color-mix(in srgb, var(--color-electric-blue) ${25 + ((i * 53) % 60)}%, var(--bg-sunken))`,
            }}
          />
        ))}
      </div>
      <ul className="mt-2 grid gap-x-4 gap-y-1 text-[0.8rem]" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(15rem, 1fr))" }}>
        {slots.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="shrink-0 tabular-nums text-[var(--fg-muted)]">
              {s.start}–{s.end}
            </span>
            <span className="min-w-0">
              <Inline text={s.label} />
              <span className="text-[var(--fg-faint)]"> · {s.minutes} min</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ToneDot({ tone: t }: { tone: Tone }) {
  if (t === "neutral") return null;
  return <span className="mr-1.5 inline-block h-2 w-2 shrink-0 rounded-full align-middle" style={{ backgroundColor: toneColor(t) }} aria-hidden />;
}

/** `**Key:** value` rows as a definition grid: labels in a column, values beside them. */
function FieldGrid({ rows }: { rows: FieldRow[] }) {
  return (
    <dl className="my-3 grid grid-cols-1 overflow-hidden rounded-xl border border-[var(--border)] sm:grid-cols-[minmax(8rem,15rem)_1fr]">
      {rows.map((row, i) => (
        <Fragment key={i}>
          <dt className={`bg-[var(--bg-sunken)] px-3 pt-2 text-[0.74rem] font-semibold text-[var(--fg-muted)] sm:py-2 ${i > 0 ? "border-t border-[var(--border)]" : ""}`}>
            {/* The document's own separator, kept: "(Section 10):" is not "(Section 10)". */}
            <Inline text={`${row.key}:`} />
          </dt>
          <dd className={`m-0 min-w-0 break-words px-3 pb-2 pt-0.5 text-[0.84rem] leading-snug sm:py-2 ${i > 0 ? "sm:border-t sm:border-[var(--border)]" : ""}`}>
            <Inline text={row.value} />
            {row.extra && (
              <div className="mt-1 text-[0.8rem]">
                <Markdown text={row.extra} />
              </div>
            )}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

/** Numbered findings as cards: the number, the lead as a title, the evidence under it. */
function Findings({ items }: { items: FindingItem[] }) {
  return (
    <ol className="my-3 space-y-2">
      {items.map((item, i) => (
        <li
          key={i}
          className="flex gap-3 rounded-xl border border-l-4 border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2.5"
          style={{ borderLeftColor: toneColor(item.tone) }}
        >
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--bg-sunken)] text-[0.72rem] font-semibold tabular-nums text-[var(--fg-muted)]">
            {item.number}
          </span>
          <div className="min-w-0 flex-1 text-[0.84rem] leading-snug">
            <div className="font-medium">
              <Inline text={item.lead} />
            </div>
            {item.body && (
              <div className="mt-1 text-[0.82rem] text-[var(--fg-muted)]">
                <Markdown text={item.body} />
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function ScoredHeading({ level, text, value, max }: { level: number; text: string; value: number; max: number }) {
  const Tag = `h${Math.min(6, Math.max(3, level))}` as "h3";
  const ratio = max ? Math.max(0, Math.min(1, value / max)) : 0;
  return (
    <div className="mb-2 mt-5">
      <Tag className="text-[0.95rem] font-semibold">
        <Inline text={text} />
      </Tag>
      <span className="mt-1 block h-1.5 w-full max-w-[18rem] overflow-hidden rounded-full bg-[var(--bg-sunken)]" aria-hidden>
        <span className="block h-full rounded-full" style={{ width: `${ratio * 100}%`, backgroundColor: tone(ratio) }} />
      </span>
    </div>
  );
}

function Checklist({ title, body, tone: t }: { title: string; body: string; tone: Tone }) {
  return (
    <div className="my-3 rounded-xl border border-t-4 border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2.5" style={{ borderTopColor: toneColor(t) }}>
      <div className="flex items-center text-[0.84rem] font-semibold">
        <ToneDot tone={t} />
        <Inline text={title} />
      </div>
      <div className="mt-1 text-[0.82rem]">
        <Markdown text={body} />
      </div>
    </div>
  );
}

function Quotes({ items }: { items: string[] }) {
  return (
    <ul className="my-3 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(16rem, 1fr))" }}>
      {items.map((q, i) => (
        <li key={i} className="rounded-xl border-l-4 bg-[var(--bg-sunken)] px-3 py-2 text-[0.84rem] italic leading-snug" style={{ borderLeftColor: "var(--color-electric-blue)" }}>
          <Inline text={q} />
        </li>
      ))}
    </ul>
  );
}

/** Long prose: the opening paragraph in full, the rest one click away. Still in the DOM, so the
 * browser's find, print and a screen reader all reach it. */
function Prose({ lead, rest, restWords }: { lead: string; rest: string; restWords: number }) {
  const { expandProse } = useContext(VisualOptionsContext);
  if (expandProse) {
    return (
      <div className="my-2">
        <Markdown text={lead} />
        <Markdown text={rest} />
      </div>
    );
  }
  return (
    <div className="my-2">
      <Markdown text={lead} />
      <details className="group mt-1">
        <summary className="cursor-pointer list-none text-[0.78rem] font-semibold text-[var(--color-electric-blue)] group-open:mb-2">
          <span className="group-open:hidden">Continue reading · {restWords.toLocaleString("en-US")} more words</span>
          <span className="hidden group-open:inline">Show less</span>
        </summary>
        <Markdown text={rest} />
      </details>
    </div>
  );
}

function Diagram({ code }: { code: string }) {
  return (
    <pre className="md-scroll my-3 overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] p-3 font-mono text-[0.76rem] leading-snug">
      {code}
    </pre>
  );
}

// --------------------------------------------------------------------------------------
// A block, and a section
// --------------------------------------------------------------------------------------

export function VisualBlockView({ block }: { block: VisualBlock }) {
  switch (block.kind) {
    case "markdown":
      return block.source.trim() ? <Markdown text={block.source} /> : null;
    case "gauge":
      return (
        <div className="my-3">
          <Gauge value={block.value} max={block.max} label={block.label} />
        </div>
      );
    case "table":
      return <TableView block={block} />;
    case "offers":
      return <OfferGrid offers={block.offers} />;
    case "cards":
      return <CardGrid cards={block.cards} />;
    case "sequence":
      return <Sequence steps={block.steps} />;
    case "runOfShow":
      return <RunOfShow slots={block.slots} />;
    case "fields":
      return <FieldGrid rows={block.rows} />;
    case "findings":
      return <Findings items={block.items} />;
    case "scoredHeading":
      return <ScoredHeading level={block.level} text={block.text} value={block.value} max={block.max} />;
    case "checklist":
      return <Checklist title={block.title} body={block.body} tone={block.tone} />;
    case "quotes":
      return <Quotes items={block.items} />;
    case "prose":
      return <Prose lead={block.lead} rest={block.rest} restWords={block.restWords} />;
    case "diagram":
      return <Diagram code={block.code} />;
  }
}

/** A section in the Visual view: HTML blocks exactly as the text view renders them, everything else
 * split into visual blocks. */
export function VisualSectionBody({ body, label }: { body: string; label: string }) {
  const { staticHtml } = useContext(VisualOptionsContext);
  const segments = useMemo(
    () => splitHtmlBlocks(body).map((s) => (s.kind === "html" ? s : { ...s, blocks: parseVisualBlocks(s.text) })),
    [body],
  );
  return (
    <>
      {segments.map((segment, i) =>
        segment.kind === "html" ? (
          staticHtml ? (
            <StaticHtmlBlock key={i} html={segment.html} label={label} />
          ) : (
            <HtmlPreview key={i} html={segment.html} label={label} />
          )
        ) : (
          <Fragment key={i}>
            {"blocks" in segment && segment.blocks.map((b, bi) => <VisualBlockView key={bi} block={b} />)}
          </Fragment>
        ),
      )}
    </>
  );
}

/** A generated page inside the HTML report: shown in a sandboxed frame (no scripts reach the report),
 * with the page's own source one click below it, so the file carries the page as well as a picture
 * of it. */
function StaticHtmlBlock({ html, label }: { html: string; label: string }) {
  return (
    <figure className="my-4">
      <iframe
        title={label}
        srcDoc={html}
        sandbox=""
        loading="lazy"
        className="h-[40rem] w-full rounded-xl border border-[var(--border)] bg-white"
      />
      <details className="mt-2">
        <summary className="cursor-pointer text-[0.78rem] font-semibold text-[var(--color-electric-blue)]">Page source (HTML)</summary>
        <pre className="md-scroll mt-2 max-h-[30rem] overflow-auto rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] p-3 font-mono text-[0.72rem]">
          {html}
        </pre>
      </details>
    </figure>
  );
}
