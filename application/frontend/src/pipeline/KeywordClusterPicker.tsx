import { useState } from "react";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";

const num = (n: number) => n.toLocaleString("en-US");

/** The run's keyword clusters as a checklist under the message that announced them.
 *
 * What is ticked here is what every stage that runs from now on is written against: the keyword
 * targets, the SEO map, headline suggestions, the plan's keyword list and the business check all read
 * only the kept clusters. A stage already built keeps what it was written with, and the card says so.
 * Saving is free (nothing is fetched). At least one cluster stays ticked, because a stage with no
 * topic to write about is worse than one with too many.
 */
export function KeywordClusterPicker({ message }: { message: PipelineMessage }) {
  const save = usePipelineStore((s) => s.selectKeywordClusters);
  const report = message.keywordReport;
  const clusters = report?.clusters ?? [];

  const saved = clusters.filter((c) => c.selected !== false).map((c) => c.name);
  const [picked, setPicked] = useState<string[]>(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justSaved, setJustSaved] = useState(false);

  if (!report || report.provider !== "dataforseo" || clusters.length === 0) return null;

  const unchanged = picked.length === saved.length && picked.every((n) => saved.includes(n));
  const volume = clusters.filter((c) => picked.includes(c.name)).reduce((sum, c) => sum + c.total_volume, 0);

  function toggle(name: string) {
    setJustSaved(false);
    setPicked((current) => (current.includes(name) ? current.filter((n) => n !== name) : [...current, name]));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    const failure = await save(message.id, picked);
    setBusy(false);
    if (failure) setError(failure);
    else setJustSaved(true);
  }

  return (
    <details
      open
      data-keyword-clusters
      className="w-full min-w-0 max-w-[34rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2.5 msg-rise @[30rem]:px-4"
    >
      <summary className="cursor-pointer select-none text-[0.88rem] font-medium">
        Topic clusters to use
        <span className="ml-2 text-[0.75rem] font-normal text-[var(--fg-muted)]">
          {saved.length} of {clusters.length} in use
        </span>
      </summary>
      <p className="mt-1.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        Untick any topic you do not want pages written about. Stages you run from now on use only the ticked clusters;
        stages already built keep what they were written with.
      </p>

      <div className="mt-2 flex items-center gap-3 text-[0.75rem]">
        <button
          type="button"
          className="cursor-pointer font-medium underline underline-offset-2"
          onClick={() => {
            setJustSaved(false);
            setPicked(clusters.map((c) => c.name));
          }}
        >
          Select all
        </button>
        <button
          type="button"
          className="cursor-pointer font-medium underline underline-offset-2"
          onClick={() => {
            setJustSaved(false);
            setPicked([]);
          }}
        >
          Clear
        </button>
      </div>

      <ul className="m-0 mt-2 grid list-none gap-1.5 p-0">
        {clusters.map((c) => {
          const on = picked.includes(c.name);
          return (
            <li key={c.name}>
              <label
                className={`flex cursor-pointer items-start gap-2.5 rounded-xl border px-2.5 py-2 transition-colors ${
                  on ? "border-[var(--color-electric-blue)] bg-[var(--bg-sunken)]" : "border-[var(--border)] opacity-70"
                }`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(c.name)}
                  className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer"
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-[0.85rem] font-medium leading-snug">{c.name}</span>
                  <span className="block text-[0.72rem] leading-relaxed text-[var(--fg-muted)]">
                    {c.primary_keyword ? `${c.primary_keyword} · ` : ""}
                    {num(c.keyword_count)} {c.keyword_count === 1 ? "keyword" : "keywords"} · {num(c.total_volume)}/mo
                    {c.intent ? ` · ${c.intent}` : ""}
                    {c.funnel ? ` · ${c.funnel}` : ""}
                  </span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
        <button
          type="button"
          disabled={busy || unchanged || picked.length === 0}
          onClick={() => void submit()}
          className="cursor-pointer rounded-full bg-[var(--accent)] px-3.5 py-1.5 text-[0.78rem] font-medium text-[var(--accent-fg)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {busy ? "Saving…" : "Save selection"}
        </button>
        <span className="text-[0.72rem] text-[var(--fg-muted)]" role="status">
          {picked.length === 0
            ? "Keep at least one cluster."
            : `${picked.length} selected · ${num(volume)} searches/mo combined`}
        </span>
      </div>

      {justSaved && (
        <p role="status" data-clusters-saved className="mt-1.5 text-[0.75rem] text-[var(--fg-muted)]">
          Saved. Stages from here on use these {saved.length} {saved.length === 1 ? "cluster" : "clusters"}.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1.5 text-[0.75rem] text-[var(--color-signal-red)]">
          {error}
        </p>
      )}
    </details>
  );
}
