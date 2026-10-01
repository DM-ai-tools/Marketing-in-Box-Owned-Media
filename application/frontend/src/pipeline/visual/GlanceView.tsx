import type { Glance } from "../../lib/assetGlance";
import { PlanMindMap } from "../PlanMindMap";
import { Gauge } from "./VisualBlocks";
import { ratioTone as tone } from "./tone";

/* The "at a glance" picture of an asset: large at the top of the reader, small on a Deliverables
 * card. Both draw the same `Glance`, so the card and the document it opens never disagree. Every
 * number is printed, not only drawn. */


/** "Free", "$297", or "$197 – $497": the lowest and highest headline prices on a rung. */
function priceRange(prices: string[]): string {
  const value = (p: string) => (/^free$/i.test(p) ? 0 : Number(p.replace(/[^\d.]/g, "")) * (/k/i.test(p) ? 1000 : 1));
  const sorted = [...prices].sort((a, b) => value(a) - value(b));
  const low = sorted[0];
  const high = sorted[sorted.length - 1];
  return low === high ? low : `${low} – ${high}`;
}

function JumpButton({ onJump, sectionId, children }: { onJump?: (id: string) => void; sectionId: string; children: React.ReactNode }) {
  if (!onJump) return <>{children}</>;
  return (
    <button type="button" onClick={() => onJump(sectionId)} className="w-full cursor-pointer text-left">
      {children}
    </button>
  );
}

/** The reader's glance strip. `text`/`label` are only used by the Plan of Action map. */
export function GlanceView({ glance, onJump, text, label }: { glance: Glance; onJump?: (id: string) => void; text: string; label: string }) {
  const frame = "mb-6 rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] p-3 sm:p-4";
  const title = (t: string) => <div className="mb-2 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">At a glance · {t}</div>;

  switch (glance.kind) {
    case "plan":
      return (
        <div className={frame}>
          {title("plan map")}
          <PlanMindMap text={text} label={label} />
        </div>
      );

    case "score":
      return (
        <div className={frame}>
          {title("score")}
          <JumpButton onJump={onJump} sectionId={glance.sectionId}>
            <div className="flex flex-wrap items-center gap-5">
              {Number.isFinite(glance.value) && <Gauge value={glance.value} max={glance.max} label={glance.label} />}
              {glance.bars.length > 0 && (
                <div className="min-w-[14rem] flex-1 space-y-1">
                  {glance.bars.map((b, i) => {
                    const ratio = b.max ? b.value / b.max : 0;
                    return (
                      <div key={i} className="grid grid-cols-[minmax(0,11rem)_1fr_auto] items-center gap-2 text-[0.76rem]">
                        <span className="truncate" title={b.label}>{b.label}</span>
                        <span className="h-2 overflow-hidden rounded-full bg-[var(--bg-sunken)]">
                          <span className="block h-full rounded-full" style={{ width: `${ratio * 100}%`, backgroundColor: tone(ratio) }} />
                        </span>
                        <span className="tabular-nums text-[var(--fg-muted)]">{`${b.value}/${b.max}`}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </JumpButton>
        </div>
      );

    case "ladder": {
      const most = Math.max(...glance.rungs.map((r) => r.count), 1);
      return (
        <div className={frame}>
          {title("value ladder")}
          <div className="flex items-end gap-2 overflow-x-auto pb-1">
            {glance.rungs.map((r, i) => (
              <button
                key={r.sectionId}
                type="button"
                onClick={() => onJump?.(r.sectionId)}
                className="flex w-[8.5rem] shrink-0 cursor-pointer flex-col justify-end rounded-xl border border-[var(--border)] p-2 text-left hover:bg-[var(--hover)]"
                style={{ minHeight: `${4.5 + i * 1.1}rem` }}
              >
                <span className="text-[0.62rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
                  {/\(Rung\s*\d+\)/i.exec(r.label)?.[0].slice(1, -1) ?? `Step ${i + 1}`}
                </span>
                <span className="line-clamp-2 text-[0.76rem] font-semibold leading-tight">{r.label}</span>
                <span className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--bg-sunken)]">
                  <span className="block h-full rounded-full" style={{ width: `${(r.count / most) * 100}%`, backgroundColor: "var(--color-electric-blue)" }} />
                </span>
                <span className="mt-1 text-[0.7rem] text-[var(--fg-muted)]">
                  {r.count} offer{r.count === 1 ? "" : "s"}
                </span>
                {r.prices.length > 0 && (
                  <span className="text-[0.74rem] font-semibold tabular-nums" style={{ color: "var(--color-electric-blue)" }}>
                    {priceRange(r.prices)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      );
    }

    case "flow":
      return (
        <div className={frame}>
          {title("funnel")}
          <JumpButton onJump={onJump} sectionId={glance.sectionId}>
            <div className="flex flex-wrap items-center gap-1.5">
              {glance.steps.map((s, i) => (
                <span key={i} className="flex items-center gap-1.5">
                  <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[0.74rem]">{s}</span>
                  {i < glance.steps.length - 1 && <span className="text-[var(--fg-faint)]" aria-hidden>→</span>}
                </span>
              ))}
            </div>
          </JumpButton>
        </div>
      );

    case "winner":
      return (
        <div className={frame}>
          {title("chosen concept")}
          <JumpButton onJump={onJump} sectionId={glance.sectionId}>
            <div className="flex flex-wrap items-center gap-4">
              <Gauge value={glance.value} max={glance.max} size={72} />
              <div className="min-w-0">
                <div className="text-[0.95rem] font-semibold">{glance.name}</div>
                <div className="text-[0.76rem] text-[var(--fg-muted)]">
                  Highest score of {glance.of} candidates — {`${glance.value}/${glance.max}`}
                </div>
              </div>
            </div>
          </JumpButton>
        </div>
      );

    case "sequence":
      return (
        <div className={frame}>
          {title("sequence")}
          <JumpButton onJump={onJump} sectionId={glance.sectionId}>
            <ol className="flex gap-0 overflow-x-auto pb-1">
              {glance.steps.map((s, i) => (
                <li key={i} className="relative flex w-[9rem] shrink-0 flex-col items-start pr-2">
                  <span className="flex w-full items-center">
                    <span className="h-3 w-3 shrink-0 rounded-full" style={{ backgroundColor: "var(--color-electric-blue)" }} aria-hidden />
                    {i < glance.steps.length - 1 && <span className="h-0.5 flex-1 bg-[var(--border-strong)]" aria-hidden />}
                  </span>
                  <span className="mt-1 text-[0.7rem] font-semibold text-[var(--fg-muted)]">{s.timing || `Step ${i + 1}`}</span>
                  <span className="line-clamp-2 text-[0.74rem] leading-tight">{s.label}</span>
                </li>
              ))}
            </ol>
          </JumpButton>
        </div>
      );

    case "show": {
      const total = glance.slots.reduce((n, s) => n + s.minutes, 0) || 1;
      return (
        <div className={frame}>
          {title(`run of show · ${total} min`)}
          <JumpButton onJump={onJump} sectionId={glance.sectionId}>
            <div className="flex h-9 w-full overflow-hidden rounded-lg">
              {glance.slots.map((s, i) => (
                <div
                  key={i}
                  title={`${s.start}–${s.end} ${s.label}`}
                  className="flex h-full items-center overflow-hidden border-r border-[var(--bg)] px-1 text-[0.62rem] font-medium text-white last:border-r-0"
                  style={{ width: `${(s.minutes / total) * 100}%`, backgroundColor: `color-mix(in srgb, var(--color-electric-blue) ${45 + ((i * 53) % 50)}%, var(--color-ink))` }}
                >
                  <span className="truncate">{s.label}</span>
                </div>
              ))}
            </div>
          </JumpButton>
        </div>
      );
    }
  }
}

/** The same glance, card-sized, for the Deliverables grid. Nothing clickable: the card is the
 * button. Renders nothing for the plan map, whose picture needs the full reader. */
export function MiniGlance({ glance }: { glance: Glance }) {
  const row = "mt-1.5 flex items-center gap-1.5 text-[0.66rem] text-[var(--fg-muted)]";
  switch (glance.kind) {
    case "score": {
      if (!Number.isFinite(glance.value)) return null;
      const ratio = glance.max ? glance.value / glance.max : 0;
      return (
        <span className={row}>
          <span className="h-1.5 w-14 overflow-hidden rounded-full bg-[var(--bg-sunken)]" aria-hidden>
            <span className="block h-full rounded-full" style={{ width: `${ratio * 100}%`, backgroundColor: tone(ratio) }} />
          </span>
          <span className="tabular-nums">Score {`${glance.value}/${glance.max}`}</span>
        </span>
      );
    }
    case "ladder": {
      const most = Math.max(...glance.rungs.map((r) => r.count), 1);
      const total = glance.rungs.reduce((n, r) => n + r.count, 0);
      return (
        <span className={row}>
          <span className="flex h-4 items-end gap-0.5" aria-hidden>
            {glance.rungs.map((r, i) => (
              <span key={i} className="w-1.5 rounded-sm" style={{ height: `${30 + (r.count / most) * 70}%`, backgroundColor: "var(--color-electric-blue)" }} />
            ))}
          </span>
          <span>
            {total} offers · {glance.rungs.length} rungs
          </span>
        </span>
      );
    }
    case "flow":
      return (
        <span className={row}>
          <span className="flex items-center gap-0.5" aria-hidden>
            {glance.steps.map((_, i) => (
              <span key={i} className="h-1.5 w-2.5 rounded-sm" style={{ backgroundColor: "var(--color-electric-blue)", opacity: 1 - i * (0.6 / glance.steps.length) }} />
            ))}
          </span>
          <span>{glance.steps.length}-step funnel</span>
        </span>
      );
    case "winner":
      return (
        <span className={row} title={glance.name}>
          <span className="shrink-0 rounded-full px-1.5 font-semibold text-white" style={{ backgroundColor: "var(--color-signal-green)" }}>
            {`${glance.value}/${glance.max}`}
          </span>
          <span className="truncate">{glance.name}</span>
        </span>
      );
    case "sequence":
      return (
        <span className={row}>
          <span className="flex items-center" aria-hidden>
            {glance.steps.map((_, i) => (
              <span key={i} className="flex items-center">
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: "var(--color-electric-blue)" }} />
                {i < glance.steps.length - 1 && <span className="h-px w-2 bg-[var(--border-strong)]" />}
              </span>
            ))}
          </span>
          <span>{glance.steps.length} steps</span>
        </span>
      );
    case "show": {
      const total = glance.slots.reduce((n, s) => n + s.minutes, 0);
      return (
        <span className={row}>
          <span className="flex h-1.5 w-14 overflow-hidden rounded-full" aria-hidden>
            {glance.slots.map((s, i) => (
              <span key={i} className="h-full" style={{ width: `${(s.minutes / (total || 1)) * 100}%`, backgroundColor: `color-mix(in srgb, var(--color-electric-blue) ${45 + ((i * 53) % 50)}%, var(--color-ink))` }} />
            ))}
          </span>
          <span>{total} min run of show</span>
        </span>
      );
    }
    case "plan":
      return null;
  }
}
