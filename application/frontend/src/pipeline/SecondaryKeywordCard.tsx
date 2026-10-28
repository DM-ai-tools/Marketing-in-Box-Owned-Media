import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { CountUp } from "../components/CountUp";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";
import type { KeywordProvider, ProviderSuggestions, SecondaryKeyword } from "./pipelineApi";
import { KEYWORD_PROVIDER_LABEL, type PickedSecondaryTerm } from "./pipelineData";

const PROVIDERS: KeywordProvider[] = ["dataforseo"];

const keyOf = (provider: KeywordProvider, keyword: string) => `${provider}|${keyword}`;

/** The Pillar Page's secondary / cluster terms, suggested from its head term by DataForSEO, as a
 * checklist with each term's measured volume, difficulty, CPC and intent.
 *
 * Optional, so nothing starts ticked and Skip is always there. The list says what was returned in
 * total (terms, combined volume, median difficulty) above it. When DataForSEO is unconfigured or
 * failed the card says so, and the text box still works.
 */
export function SecondaryKeywordCard({ message }: { message: PipelineMessage }) {
  const confirm = usePipelineStore((s) => s.confirmSecondaryKeywords);
  const skip = usePipelineStore((s) => s.skipServices);
  const retry = usePipelineStore((s) => s.retryServiceScan);
  const research = usePipelineStore((s) => s.researchSecondaryKeywords);
  const still = Boolean(useReducedMotion());
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState("");
  const [seed, setSeed] = useState("");

  const scan = message.serviceScan;
  const secondary = scan?.secondary;
  if (!scan || !secondary) return null;

  const result = secondary.result;
  const superseded = Boolean(message.superseded);
  const open = !superseded && (scan.status === "ready" || scan.status === "error" || scan.status === "loading");
  const columns = result ? { dataforseo: result.dataforseo } : null;
  const typedCount = other.split(/[,;\n]/).filter((s) => s.trim()).length;
  const uniqueTerms = new Set(picked.map((k) => k.split("|")[1]));
  const total = uniqueTerms.size + typedCount;

  function toggle(key: string) {
    setPicked((current) => (current.includes(key) ? current.filter((k) => k !== key) : [...current, key]));
  }
  function setColumn(provider: KeywordProvider, on: boolean) {
    const keys = (columns?.[provider].keywords ?? []).map((k) => keyOf(provider, k.keyword));
    setPicked((current) => (on ? [...new Set([...current, ...keys])] : current.filter((k) => !keys.includes(k))));
  }
  function submit() {
    if (!columns) {
      confirm(message.id, [], other);
      return;
    }
    const rows: PickedSecondaryTerm[] = [];
    for (const provider of PROVIDERS) {
      for (const k of columns[provider].keywords) {
        if (picked.includes(keyOf(provider, k.keyword))) rows.push({ provider, keyword: k.keyword, volume: k.volume });
      }
    }
    confirm(message.id, rows, other);
  }

  return (
    <div
      data-secondary-keywords
      className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">Secondary terms for “{secondary.headTerm}”</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        Supporting terms the page covers in passing, each placed in the section that actually describes it. Tick any
        DataForSEO suggests. Optional.
      </p>

      {scan.status === "loading" && (
        <div className="mt-2.5 flex items-center gap-2 text-[0.78rem] text-[var(--fg-muted)]" role="status">
          <span className="flex gap-1" aria-hidden>
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                className="h-1 w-1 rounded-full bg-[var(--color-electric-blue)]"
                animate={still ? undefined : { opacity: [0.25, 1, 0.25] }}
                transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15, ease: "easeInOut" }}
              />
            ))}
          </span>
          Asking DataForSEO for terms around “{secondary.headTerm}”…
        </div>
      )}

      {scan.status === "error" && !superseded && (
        <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">Couldn't fetch suggestions: {scan.error}</p>
          <button
            type="button"
            onClick={() => retry(message.id)}
            className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2"
          >
            Try again
          </button>
        </div>
      )}

      {scan.status === "ready" && !superseded && columns && (
        <>
          <p className="mt-2 text-[0.7rem] text-[var(--fg-faint)]">
            Monthly Google searches{result?.location ? ` in ${result.location}` : ""}. KD is keyword difficulty, 0–100.
          </p>
          {columns.dataforseo.status === "ok" && columns.dataforseo.keywords.length === 0 && (
            <p data-no-terms className="mt-1.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
              DataForSEO has no search data around “{secondary.headTerm}”.
              {secondary.headTerm.split(/\s+/).length > 4
                ? " It reads like a headline rather than something people search; try the 2–4 word term the page should rank for."
                : " Try a broader term below."}
            </p>
          )}
          <div className="mt-2 grid gap-3">
            {PROVIDERS.map((provider) => (
              <ProviderColumn
                key={provider}
                column={columns[provider]}
                picked={picked}
                onToggle={toggle}
                onAll={(on) => setColumn(provider, on)}
                still={still}
              />
            ))}
          </div>
        </>
      )}

      {(scan.status === "ready" || scan.status === "error") && !superseded && (
        <form
          className="mt-2.5 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!seed.trim()) return;
            setPicked([]);
            research(message.id, seed);
          }}
        >
          <label className="text-[0.72rem] text-[var(--fg-muted)]" htmlFor={`${message.id}-seed`}>
            Search a different term:
          </label>
          <input
            id={`${message.id}-seed`}
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="e.g. ai seo services"
            className="min-h-10 min-w-0 flex-1 rounded-full border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-1 text-[0.8rem] outline-none focus:border-[var(--border-strong)] sm:min-h-0"
          />
          <button
            type="submit"
            disabled={!seed.trim()}
            className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3 py-1 text-[0.78rem] font-medium disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
          >
            Search
          </button>
        </form>
      )}

      {open && (
        <div className="mt-3 border-t border-[var(--border)] pt-2.5">
          <label className="text-[0.75rem] text-[var(--fg-muted)]" htmlFor={`${message.id}-other`}>
            Any other terms? (optional, comma-separated)
          </label>
          <textarea
            id={`${message.id}-other`}
            value={other}
            onChange={(e) => setOther(e.target.value)}
            rows={2}
            placeholder="e.g. social media management cost, LinkedIn ads"
            className="mt-1 w-full min-w-0 resize-y rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2 text-[0.82rem] outline-none focus:border-[var(--border-strong)]"
          />
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              disabled={!total || scan.status === "loading"}
              onClick={() => total && submit()}
              whileTap={total ? { scale: 0.97 } : undefined}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              {total ? `Use ${total} term${total === 1 ? "" : "s"}` : "Tick terms to use"}
            </motion.button>
            <motion.button
              type="button"
              onClick={() => skip(message.id)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
            >
              Skip: no secondary terms
            </motion.button>
          </div>
        </div>
      )}

      {scan.status === "done" && (
        <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5">
          <div className="text-[0.72rem] text-[var(--fg-faint)]">Using</div>
          <ul className="m-0 mt-1 list-none space-y-0.5 p-0 text-[0.82rem]">
            {(secondary.picked ?? []).map((p) => (
              <li key={keyOf(p.provider, p.keyword)}>
                {p.keyword}{" "}
                <span className="text-[var(--fg-faint)]">
                  ({KEYWORD_PROVIDER_LABEL[p.provider]}
                  {p.volume != null ? `, ${p.volume.toLocaleString("en-US")}/mo` : ""})
                </span>
              </li>
            ))}
            {(scan.other ?? "")
              .split(/[,;\n]/)
              .map((s) => s.trim())
              .filter(Boolean)
              .map((term) => (
                <li key={`typed|${term}`}>
                  {term} <span className="text-[var(--fg-faint)]">(typed)</span>
                </li>
              ))}
          </ul>
        </div>
      )}
      {scan.status === "skipped" && <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">skipped: no secondary terms</p>}
      {superseded && scan.status !== "done" && scan.status !== "skipped" && (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">asked again below</p>
      )}
    </div>
  );
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

function ProviderColumn({
  column,
  picked,
  onToggle,
  onAll,
  still,
}: {
  column: ProviderSuggestions;
  picked: string[];
  onToggle: (key: string) => void;
  onAll: (on: boolean) => void;
  still: boolean;
}) {
  const label = KEYWORD_PROVIDER_LABEL[column.provider];
  const rows = column.keywords;
  const volumes = rows.map((r) => r.volume).filter((v): v is number => v !== null);
  const totalVolume = volumes.reduce((sum, v) => sum + v, 0);
  const kd = median(rows.map((r) => r.difficulty).filter((v): v is number => v !== null));
  const keys = rows.map((r) => keyOf(column.provider, r.keyword));
  const allOn = keys.length > 0 && keys.every((k) => picked.includes(k));

  return (
    <section data-provider={column.provider} className="min-w-0 rounded-xl border border-[var(--border)] px-2.5 py-2">
      <header className="flex flex-wrap items-baseline justify-between gap-x-2">
        <h4 className="m-0 text-[0.82rem] font-semibold">{label}</h4>
        {column.status === "ok" && rows.length > 0 && (
          <button type="button" onClick={() => onAll(!allOn)} className="cursor-pointer text-[0.7rem] underline underline-offset-2">
            {allOn ? "Clear" : `Tick all ${rows.length}`}
          </button>
        )}
      </header>

      {column.status !== "ok" ? (
        <p
          data-provider-status={column.status}
          className="mt-1.5 rounded-lg border px-2 py-1.5 text-[0.72rem] leading-relaxed"
          style={{ borderColor: column.status === "error" ? "var(--color-signal-amber)" : "var(--border)" }}
        >
          {column.status === "unconfigured" ? `${label} is not connected. ` : `${label} did not answer. `}
          {column.error}
        </p>
      ) : rows.length === 0 ? (
        <p className="mt-1.5 text-[0.72rem] text-[var(--fg-muted)]">No related terms passed the filter for this head term.</p>
      ) : (
        <>
          <p className="mt-0.5 text-[0.68rem] tabular-nums text-[var(--fg-faint)]">
            {rows.length} terms · <CountUp to={totalVolume} still={still} />
            /mo combined{kd !== null ? ` · median KD ${kd}` : ""}
          </p>
          <ul className="m-0 mt-1.5 grid list-none gap-1 p-0">
            {rows.map((row) => (
              <TermRow
                key={row.keyword}
                row={row}
                selected={picked.includes(keyOf(column.provider, row.keyword))}
                onToggle={() => onToggle(keyOf(column.provider, row.keyword))}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function TermRow({ row, selected, onToggle }: { row: SecondaryKeyword; selected: boolean; onToggle: () => void }) {
  return (
    <li>
      <label
        className="flex min-h-10 cursor-pointer items-start gap-2 rounded-lg border px-2 py-1.5 text-[0.78rem] sm:min-h-0"
        style={{
          borderColor: selected ? "var(--color-electric-blue)" : "var(--border)",
          backgroundColor: selected ? "var(--bg-sunken)" : undefined,
        }}
      >
        <input type="checkbox" checked={selected} onChange={onToggle} className="mt-0.5 shrink-0 accent-[var(--color-electric-blue)]" />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline justify-between gap-x-2">
            <span className="min-w-0 break-words font-medium">{row.keyword}</span>
            <span className="text-[0.72rem] tabular-nums text-[var(--fg-muted)]">
              {row.volume != null ? `${row.volume.toLocaleString("en-US")}/mo` : "—"}
            </span>
          </span>
          <span className="mt-0.5 flex flex-wrap gap-x-2 text-[0.66rem] text-[var(--fg-faint)]">
            <span>KD {row.difficulty ?? "—"}</span>
            {row.cpc != null && <span>CPC ${row.cpc.toFixed(2)}</span>}
            {row.intent && <span>{row.intent}</span>}
          </span>
        </span>
      </label>
    </li>
  );
}
