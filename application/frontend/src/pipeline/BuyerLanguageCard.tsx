import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";
import type { AvoidItem, OutcomeCluster } from "./pipelineApi";
import { BUYER_LANGUAGE_MAX_CHARS, composeOutcomeWordsAnswer, composeWordsToAvoidAnswer } from "./pipelineData";

const AVOID_GROUPS: { kind: AvoidItem["kind"]; title: string; hint: string }[] = [
  { kind: "jargon", title: "Jargon buyers don't search", hint: "On the landing page; buyers search the plain words instead." },
  { kind: "claim", title: "Claims to avoid", hint: "On the landing page, and hard to substantiate." },
];

const num = (value: number | null | undefined) => (typeof value === "number" ? value.toLocaleString("en-US") : "—");

/** CRO's "Words the buyer uses for the outcome" and "Words to avoid", as suggestions to pick from
 * (`BUYER_LANGUAGE_OPTION`, `app/services/buyer_language.py`).
 *
 * Nothing starts ticked: these are the client's words, and the operator decides. Each suggestion
 * shows its evidence: the real searches behind an outcome cluster, with volumes; the plain words
 * buyers search instead of a jargon term. The text box
 * takes the operator's own words (reviews, call notes), which lead the answer. */
export function BuyerLanguageCard({ message }: { message: PipelineMessage }) {
  const confirm = usePipelineStore((s) => s.confirmBuyerLanguage);
  const skip = usePipelineStore((s) => s.skipServices);
  const retry = usePipelineStore((s) => s.retryServiceScan);
  const regenerate = usePipelineStore((s) => s.regenerateBuyerLanguage);
  const still = Boolean(useReducedMotion());
  const [picked, setPicked] = useState<string[]>([]);
  const [typed, setTyped] = useState("");

  const scan = message.serviceScan;
  const buyer = scan?.buyerLanguage;
  if (!scan || !buyer) return null;

  const outcome = buyer.mode === "outcome";
  const result = buyer.result;
  const superseded = Boolean(message.superseded);
  const open = !superseded && (scan.status === "ready" || scan.status === "error" || scan.status === "loading");
  const preview = outcome
    ? composeOutcomeWordsAnswer((result?.clusters ?? []).filter((c) => picked.includes(c.name)), typed)
    : composeWordsToAvoidAnswer((result?.avoid ?? []).filter((a) => picked.includes(a.term)).map((a) => a.term), typed);

  const toggle = (key: string) =>
    setPicked((current) => (current.includes(key) ? current.filter((k) => k !== key) : [...current, key]));

  return (
    <div
      data-buyer-language={buyer.mode}
      className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">
        {outcome ? "Words the buyer uses for the outcome" : "Words to avoid (internal jargon, banned phrases)"}
        {!outcome && <span className="ml-1.5 text-[0.72rem] font-normal text-[var(--fg-faint)]">optional</span>}
      </div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        {outcome
          ? "Their words for what they want, not the industry's. Suggested from what buyers actually search around " +
            (buyer.services.length ? buyer.services.join(", ") : "this service") +
            ". Tick the clusters that sound like them, and add words from reviews, call notes or enquiry emails."
          : "Internal jargon and anything the client has explicitly banned. Suggested from the landing page. " +
            "Tick what to keep off the page."}
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
          {outcome ? "Reading what buyers search for, through DataForSEO…" : "Checking the landing page…"}
        </div>
      )}

      {scan.status === "error" && !superseded && (
        <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">Couldn't suggest any: {scan.error}</p>
          <button type="button" onClick={() => retry(message.id)} className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2">
            Try again
          </button>
        </div>
      )}

      {scan.status === "ready" && !superseded && result && (
        <>
          {outcome ? (
            <OutcomeClusters clusters={result.clusters} location={result.location} picked={picked} onToggle={toggle} />
          ) : (
            <AvoidGroups items={result.avoid} picked={picked} onToggle={toggle} />
          )}
          {(result.sources.length > 0 || result.notes.length > 0) && (
            <div className="mt-2 text-[0.68rem] leading-relaxed text-[var(--fg-faint)]">
              {result.sources.length > 0 && <p className="m-0">From: {result.sources.join(" · ")}</p>}
              {result.notes.map((note) => (
                <p key={note} className="m-0">{note}</p>
              ))}
            </div>
          )}
        </>
      )}

      {open && (
        <div className="mt-3 border-t border-[var(--border)] pt-2.5">
          <label className="text-[0.75rem] text-[var(--fg-muted)]" htmlFor={`${message.id}-typed`}>
            {outcome ? "Their own words (comma-separated)" : "Anything else to avoid (comma-separated)"}
          </label>
          <textarea
            id={`${message.id}-typed`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            rows={2}
            placeholder={outcome ? "e.g. more qualified leads, a pipeline I can forecast" : 'e.g. cheap, "guaranteed rankings", growth hacking'}
            className="mt-1 w-full min-w-0 resize-y rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2 text-[0.82rem] outline-none focus:border-[var(--border-strong)]"
          />
          {preview && (
            <p data-answer-preview className="mt-1.5 text-[0.72rem] leading-relaxed text-[var(--fg-muted)]">
              <span className="font-semibold">Answer:</span> {preview}
              {preview.length >= BUYER_LANGUAGE_MAX_CHARS - 40 && (
                <span className="text-[var(--fg-faint)]"> (kept short so it carries to Phase 2 whole)</span>
              )}
            </p>
          )}
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              disabled={!preview || scan.status === "loading"}
              onClick={() => preview && confirm(message.id, picked, typed)}
              whileTap={preview ? { scale: 0.97 } : undefined}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              {preview ? "Use these words" : outcome ? "Tick a cluster or type their words" : "Tick or type words to avoid"}
            </motion.button>
            {!outcome && (
              <motion.button
                type="button"
                onClick={() => skip(message.id)}
                whileTap={{ scale: 0.97 }}
                className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
              >
                Skip: none
              </motion.button>
            )}
            {outcome && (
              <motion.button
                type="button"
                data-regenerate
                disabled={scan.status === "loading"}
                onClick={() => {
                  setPicked([]);
                  regenerate(message.id);
                }}
                whileTap={scan.status === "loading" ? undefined : { scale: 0.97 }}
                title="Ask again: new outcome searches from DataForSEO, grouped afresh"
                className="ml-auto min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
              >
                {scan.status === "loading" ? "Regenerating…" : "↻ Regenerate"}
              </motion.button>
            )}
          </div>
        </div>
      )}

      {scan.status === "done" && (
        <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5 text-[0.82rem]">
          <div className="text-[0.72rem] text-[var(--fg-faint)]">Using</div>
          {outcome
            ? composeOutcomeWordsAnswer((result?.clusters ?? []).filter((c) => (buyer.picked ?? []).includes(c.name)), scan.other ?? "")
            : composeWordsToAvoidAnswer(buyer.picked ?? [], scan.other ?? "")}
        </div>
      )}
      {scan.status === "skipped" && <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">skipped: no words to avoid</p>}
      {superseded && scan.status !== "done" && scan.status !== "skipped" && (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">asked again below</p>
      )}
    </div>
  );
}

function Check({ selected, onToggle, children }: { selected: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <label
      className="flex min-h-10 cursor-pointer items-start gap-2 rounded-lg border px-2 py-1.5 text-[0.78rem] sm:min-h-0"
      style={{ borderColor: selected ? "var(--color-electric-blue)" : "var(--border)", backgroundColor: selected ? "var(--bg-sunken)" : undefined }}
    >
      <input type="checkbox" checked={selected} onChange={onToggle} className="mt-0.5 shrink-0 accent-[var(--color-electric-blue)]" />
      <span className="min-w-0 flex-1">{children}</span>
    </label>
  );
}

function OutcomeClusters({
  clusters,
  location,
  picked,
  onToggle,
}: {
  clusters: OutcomeCluster[];
  location: string;
  picked: string[];
  onToggle: (name: string) => void;
}) {
  if (!clusters.length) return null;
  return (
    <>
      <p className="mt-2 text-[0.7rem] text-[var(--fg-faint)]">
        Each cluster is an outcome named in buyers' words, with the real searches behind it (monthly Google searches
        {location ? ` in ${location}` : ""}).
      </p>
      <ul className="m-0 mt-1.5 grid list-none gap-1.5 p-0">
        {clusters.map((cluster) => (
          <li key={cluster.name}>
            <Check selected={picked.includes(cluster.name)} onToggle={() => onToggle(cluster.name)}>
              <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                <span className="font-semibold">{cluster.name}</span>
                {cluster.distinct && (
                  <span data-distinct className="ml-1.5 text-[0.66rem] font-medium text-[var(--color-electric-blue)]">
                    specific to this service
                  </span>
                )}
                <span className="text-[0.72rem] tabular-nums text-[var(--fg-muted)]">{num(cluster.combined_volume)}/mo combined</span>
              </span>
              <span className="mt-1 flex flex-wrap gap-1">
                {cluster.phrases.map((p) => (
                  <span key={p.keyword} className="rounded-full border border-[var(--border)] px-1.5 py-[1px] text-[0.68rem] text-[var(--fg-muted)]">
                    {p.keyword} <span className="tabular-nums text-[var(--fg-faint)]">{num(p.volume)}</span>
                  </span>
                ))}
              </span>
            </Check>
          </li>
        ))}
      </ul>
    </>
  );
}

function AvoidGroups({ items, picked, onToggle }: { items: AvoidItem[]; picked: string[]; onToggle: (term: string) => void }) {
  return (
    <div className="mt-2 grid gap-2">
      {AVOID_GROUPS.map((group) => {
        const rows = items.filter((i) => i.kind === group.kind);
        if (!rows.length) return null;
        return (
          <section key={group.kind} data-avoid-group={group.kind} className="min-w-0 rounded-xl border border-[var(--border)] px-2.5 py-2">
            <h4 className="m-0 text-[0.82rem] font-semibold">{group.title}</h4>
            <p className="m-0 text-[0.68rem] text-[var(--fg-faint)]">{group.hint}</p>
            <ul className="m-0 mt-1.5 grid list-none gap-1 p-0">
              {rows.map((item) => (
                <li key={item.term}>
                  <Check selected={picked.includes(item.term)} onToggle={() => onToggle(item.term)}>
                    <span className="font-medium">{item.term}</span>
                    {item.kind === "jargon" && item.alternative && (
                      <span className="mt-0.5 block text-[0.68rem] text-[var(--fg-faint)]">
                        Buyers say “{item.alternative}” ({num(item.alternative_volume)}/mo) — this term: {num(item.term_volume)}/mo
                      </span>
                    )}
                    {item.kind === "claim" && item.reason && (
                      <span className="mt-0.5 block text-[0.68rem] text-[var(--fg-faint)]">{item.reason}</span>
                    )}
                  </Check>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
