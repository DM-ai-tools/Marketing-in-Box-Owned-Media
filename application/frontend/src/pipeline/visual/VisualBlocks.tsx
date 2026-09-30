import { Fragment, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Markdown } from "../../components/Markdown";
import { HtmlPreview } from "../../components/HtmlPreview";
import { splitHtmlBlocks } from "../../lib/htmlBlocks";
import {
  parseVisualBlocks,
  plainCell,
  scoreOf,
  type OfferItem,
  type ScoreValue,
  type SequenceStep,
  type ShowSlot,
  type VisualBlock,
} from "../../lib/visualBlocks";

/* The reader's Visual view. Every component here draws one block from `lib/visualBlocks.ts` and
 * renders **every word of its source** — the bar next to "6/10" is added, never substituted for it,
 * so meaning is never carried by colour alone and nothing is lost when a table becomes a timeline.
 * `smoke/visual.tsx` checks that over every real output. Tokens only (`var(--…)`), so dark mode
 * needs nothing of its own. */

// --------------------------------------------------------------------------------------
// Primitives
// --------------------------------------------------------------------------------------

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

function tone(ratio: number): string {
  if (ratio >= 0.75) return "var(--color-signal-green)";
  if (ratio >= 0.5) return "var(--color-electric-blue)";
  return "var(--color-signal-orange)";
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
  const color = score.state === "yes" ? "var(--color-signal-green)" : score.state === "partial" ? "#d9a400" : "var(--color-signal-orange)";
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
      // A table with no recognisable shape is still a table — rendered exactly as the text view does.
      return <Markdown text={block.source} />;
  }
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
  }
}

/** A section in the Visual view: HTML blocks exactly as the text view renders them, everything else
 * split into visual blocks. */
export function VisualSectionBody({ body, label }: { body: string; label: string }) {
  const segments = useMemo(
    () => splitHtmlBlocks(body).map((s) => (s.kind === "html" ? s : { ...s, blocks: parseVisualBlocks(s.text) })),
    [body],
  );
  return (
    <>
      {segments.map((segment, i) =>
        segment.kind === "html" ? (
          <HtmlPreview key={i} html={segment.html} label={label} />
        ) : (
          <Fragment key={i}>
            {"blocks" in segment && segment.blocks.map((b, bi) => <VisualBlockView key={bi} block={b} />)}
          </Fragment>
        ),
      )}
    </>
  );
}
