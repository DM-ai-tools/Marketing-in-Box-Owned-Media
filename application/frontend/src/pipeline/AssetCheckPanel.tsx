import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import type { AssetCheckReport, CheckFinding, CheckName, CheckResult } from "./pipelineApi";
import { earlierCheckReport, usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";

const CHECK_LABEL: Record<CheckName, string> = {
  offer_relevance: "Offers",
  funnel_relevance: "Funnel",
  business_logic: "Business rules",
  virality: "Virality",
};

/** What each score measures, in the operator's words — a number with no question behind it is
 * a number nobody can act on. */
const CHECK_QUESTION: Record<CheckName, string> = {
  offer_relevance: "Does it sell only what's on the client's offer ladder, at prices on record?",
  funnel_relevance: "Does it lead the buyer to the next real step on the ladder, matched to where they are?",
  business_logic: "Are its claims, prices, testimonials and wording allowed for this client?",
  virality: "How likely it is to be shared: hook, curiosity, specificity, emotion, platform fit.",
};

type Band = { label: string; color: string };

const BANDS = {
  good: { label: "Good", color: "var(--color-signal-green)" },
  work: { label: "Needs work", color: "var(--color-signal-amber)" },
  risk: { label: "At risk", color: "var(--color-signal-orange)" },
  none: { label: "Not scored", color: "var(--border-strong)" },
} satisfies Record<string, Band>;

/** A check's band: an error finding always makes it at risk, whatever the score says. */
function bandOf(result: CheckResult): Band {
  if (result.findings.some((f) => f.severity === "error")) return BANDS.risk;
  if (result.score === null) return result.findings.some((f) => f.severity === "warn") ? BANDS.work : BANDS.none;
  if (result.score >= 80) return BANDS.good;
  if (result.score >= 60) return BANDS.work;
  return BANDS.risk;
}

const SEVERITY = {
  error: { label: "Must fix", color: "var(--color-signal-orange)" },
  warn: { label: "Should fix", color: "var(--color-signal-amber)" },
  info: { label: "Note", color: "var(--fg-faint)" },
} as const;

const RANK = { error: 0, warn: 1, info: 2 } as const;

function findingKey(f: CheckFinding, i: number): string {
  return `${f.check}:${i}:${f.quote.slice(0, 40)}`;
}

// --------------------------------------------------------------------------------------
// Scanning
// --------------------------------------------------------------------------------------

/** The stages the check actually runs, in order: the facts are loaded, the free rule checks run,
 * then one review call judges offers, funnel and virality together. The step shown as current is
 * paced by elapsed time — the request reports no progress of its own — so the list says what runs,
 * never how many findings exist yet. */
const SCAN_STEPS = [
  { title: "Read the client's records", detail: "Offer ladder, pricing, claim tier, words to avoid", at: 0 },
  { title: "Rule checks", detail: "Prices, testimonials, absolute claims", at: 1500 },
  { title: "Reviewing offers and funnel", detail: "Every finding must quote the draft", at: 4000 },
  { title: "Predicting virality", detail: "Against this client's real top posts, where there are any", at: 12000 },
];

function useElapsed(running: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!running) return;
    const start = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed(Date.now() - start), 400);
    return () => window.clearInterval(timer);
  }, [running]);
  return elapsed;
}

function CheckMark() {
  return (
    <span className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full" style={{ backgroundColor: "var(--color-signal-green)" }}>
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M5 12.5l4.5 4.5L19 7.5" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

function Scanning() {
  const elapsed = useElapsed(true);
  const active = SCAN_STEPS.reduce((n, s, i) => (elapsed >= s.at ? i : n), 0);
  const outline = [62, 92, 78, 88, 55, 0, 84, 70];

  return (
    <section
      aria-label="Business check"
      aria-busy="true"
      className="mb-2.5 flex flex-col gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] p-3"
    >
      <div className="flex items-center gap-2">
        <svg className="bc-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
          <circle cx="12" cy="12" r="9" stroke="var(--border-strong)" strokeWidth="3" />
          <path d="M21 12a9 9 0 0 0-9-9" stroke="var(--color-electric-blue)" strokeWidth="3" strokeLinecap="round" />
        </svg>
        <span className="text-[0.8rem] font-semibold">Business check · scanning this draft</span>
        <span className="ml-auto text-[0.7rem] text-[var(--fg-muted)]">about 20 seconds</span>
      </div>

      <div className="h-1 overflow-hidden rounded-full bg-[var(--border)]" aria-hidden>
        <div className="bc-bar h-1 w-[38%] rounded-full" style={{ backgroundColor: "var(--color-electric-blue)" }} />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {/* An outline of a page being read — decoration, so hidden from assistive tech; the step list
            beside it says the same thing in words. */}
        <div aria-hidden className="relative hidden h-[168px] flex-col gap-2 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg)] p-3 sm:flex">
          {outline.map((w, i) =>
            w === 0 ? (
              <div key={i} className="mt-0.5 flex gap-1.5">
                <div className="h-5 flex-1 rounded-md bg-[var(--bg-sunken)]" />
                <div className="bc-read h-5 flex-1 rounded-md bg-[var(--border)]" />
                <div className="h-5 flex-1 rounded-md bg-[var(--bg-sunken)]" />
              </div>
            ) : (
              <div key={i} className={`${i === 2 ? "bc-read " : ""}rounded bg-[var(--border)] ${i === 0 ? "h-2.5" : "h-2"}`} style={{ width: `${w}%` }} />
            ),
          )}
          <div
            className="bc-beam absolute inset-x-0 top-0 h-9 border-b-2"
            style={{
              borderColor: "var(--color-electric-blue)",
              background: "linear-gradient(180deg, transparent 0%, color-mix(in srgb, var(--color-electric-blue) 16%, transparent) 70%, color-mix(in srgb, var(--color-electric-blue) 45%, transparent) 100%)",
            }}
          />
        </div>

        <ol className="m-0 flex list-none flex-col gap-2.5 p-0 text-[0.76rem]">
          {SCAN_STEPS.map((step, i) => {
            const state = i < active ? "done" : i === active ? "active" : "pending";
            return (
              <li key={step.title} className={`flex items-start gap-2.5 ${state === "pending" ? "text-[var(--fg-faint)]" : ""}`}>
                {state === "done" ? (
                  <CheckMark />
                ) : state === "active" ? (
                  <span className="bc-pulse h-[18px] w-[18px] shrink-0 rounded-full" style={{ backgroundColor: "var(--color-electric-blue)" }} aria-hidden />
                ) : (
                  <span className="m-[2px] h-[14px] w-[14px] shrink-0 rounded-full border-2 border-[var(--border-strong)]" aria-hidden />
                )}
                <span>
                  <span className="font-semibold">{step.title}</span>
                  <span className="sr-only"> ({state === "done" ? "done" : state === "active" ? "in progress" : "next"})</span>
                  <span className={`block ${state === "pending" ? "" : "text-[var(--fg-muted)]"}`}>{step.detail}</span>
                </span>
              </li>
            );
          })}
        </ol>
      </div>

      <p className="m-0 text-[0.7rem] text-[var(--fg-muted)]">
        You can keep reading or approve the draft while this runs. The check never blocks <strong className="text-[var(--fg)]">Save It</strong>.
      </p>
    </section>
  );
}

// --------------------------------------------------------------------------------------
// Results
// --------------------------------------------------------------------------------------

/** The report of the draft this one was refined from, when it had one — so a re-check after "Fix
 * with Refine" can show the scores moving rather than a new set of numbers with no reference. */
function useEarlierReport(message: PipelineMessage): AssetCheckReport | undefined {
  return usePipelineStore((s) => earlierCheckReport(s.messages, message));
}

function ScoreCard({ result, earlier, basis }: { result: CheckResult; earlier?: CheckResult; basis: AssetCheckReport["virality_basis"] }) {
  const band = bandOf(result);
  const label = CHECK_LABEL[result.check] ?? result.check;
  const isVirality = result.check === "virality";
  const counts = result.findings.length;
  const status = result.verdict || (counts ? `${counts} finding${counts === 1 ? "" : "s"}` : "nothing material");
  return (
    <div
      className="flex min-w-0 flex-col gap-1.5 rounded-lg bg-[var(--bg-raised)] p-2.5"
      style={{
        border: band === BANDS.risk ? "2px solid var(--color-signal-orange)" : isVirality ? "1px dashed var(--border-strong)" : "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-1.5">
        <span className="text-[0.78rem] font-semibold">{label}</span>
        {isVirality && (
          <span className="rounded-full border border-[var(--border-strong)] px-1.5 text-[0.58rem] font-semibold uppercase tracking-wide text-[var(--fg-muted)]">
            prediction
          </span>
        )}
        <span className="ml-auto text-[1.05rem] font-bold tabular-nums">
          {result.score !== null ? ` ${result.score}` : " —"}
          {result.score !== null && <span className="text-[0.68rem] font-medium text-[var(--fg-muted)]">/100</span>}
        </span>
      </div>
      {/* No bar without a score: an empty track beside "—" reads as a score of 0. */}
      {result.score !== null ? (
        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--border)]" aria-hidden>
          <motion.div
            className="h-1.5 rounded-full"
            style={{ backgroundColor: band.color }}
            initial={{ width: 0 }}
            animate={{ width: `${result.score}%` }}
            transition={{ duration: 0.6, ease: "easeOut" }}
          />
        </div>
      ) : (
        <div className="h-1.5 rounded-full border border-dashed border-[var(--border-strong)]" aria-hidden />
      )}
      <p className="m-0 text-[0.7rem] leading-snug text-[var(--fg-muted)]">
        {CHECK_QUESTION[result.check]}
        {isVirality &&
          (basis === "benchmarked"
            ? " Compared with this client's and competitors' real post engagement and keyword demand."
            : " Rubric only: no real engagement data on this run yet (Stage 10 collects it).")}
      </p>
      <p className="m-0 text-[0.7rem] font-semibold" style={{ color: band === BANDS.none ? "var(--fg-muted)" : band.color }}>
        {band.label} · {status}
        {earlier && earlier.score !== null && result.score !== null && earlier.score !== result.score && (
          <span className="font-normal text-[var(--fg-muted)]"> · was {earlier.score}</span>
        )}
      </p>
    </div>
  );
}

/** The check of one draft against the client's business: offers, funnel, business rules and, for
 * content, predicted virality.
 *
 * Advisory. It sits above the Approve row and never disables it: a static rule will be wrong for
 * some draft, and the operator is the one who knows which. What it offers instead is the one-click
 * way to act on it — the chosen findings go to Refine as a single note, and the refined draft is
 * checked again, so the scores can be seen to move.
 *
 * The findings open by default only when something must be fixed; the score cards always show. */
export function AssetCheckPanel({ message }: { message: PipelineMessage }) {
  const recheckAsset = usePipelineStore((s) => s.recheckAsset);
  const fixCheckFindings = usePipelineStore((s) => s.fixCheckFindings);
  const earlier = useEarlierReport(message);
  const check = message.check;
  const report = check?.report;
  const all = report?.checks.flatMap((c) => c.findings) ?? [];
  const errors = all.filter((f) => f.severity === "error").length;
  const [open, setOpen] = useState(errors > 0);
  // Errors and warnings are selected for fixing by default; information (placeholders, unsourced
  // figures in strategist notes) is left for the operator to opt into.
  const [skip, setSkip] = useState<Set<string>>(
    () => new Set(all.map((f, i) => (f.severity === "info" ? findingKey(f, i) : "")).filter(Boolean)),
  );

  if (!check) return null;

  if (check.status === "running") return <Scanning />;

  if (check.status === "error" || !report) {
    return (
      <div className="mb-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-2 text-[0.74rem] text-[var(--fg-muted)]">
        The business check couldn't run{check.error ? ` (${check.error})` : ""}. The draft is unaffected.{" "}
        <button type="button" onClick={() => recheckAsset(message.id)} className="cursor-pointer font-semibold underline">
          Check again
        </button>
      </div>
    );
  }

  const selected = all.filter((f, i) => !skip.has(findingKey(f, i)));
  const canFix = message.savePhase !== "saved" && !message.superseded && !message.refineSubmitted;

  return (
    <motion.section
      aria-label="Business check"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="mb-2.5 flex flex-col gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] p-3"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h3 className="m-0 text-[0.84rem] font-bold">Business check</h3>
        <span className="text-[0.7rem] text-[var(--fg-muted)]">This draft, compared with what's on record for the client. Each score is out of 100.</span>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[0.64rem] text-[var(--fg-muted)]" aria-label="Score bands">
        {[
          [BANDS.good, "80–100 Good · nothing material"],
          [BANDS.work, "60–79 Needs work · fix before publishing"],
          [BANDS.risk, "0–59 At risk · likely wrong for this client"],
        ].map(([band, text]) => (
          <span key={text as string} className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: (band as Band).color }} aria-hidden />
            {text as string}
          </span>
        ))}
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {report.checks.map((c) => (
          <ScoreCard key={c.check} result={c} earlier={earlier?.checks.find((e) => e.check === c.check)} basis={report.virality_basis} />
        ))}
      </div>

      <div>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="cursor-pointer text-[0.74rem] font-semibold text-[var(--fg)]"
        >
          {all.length ? `What to fix — ${all.length} finding${all.length === 1 ? "" : "s"}${errors ? `, ${errors} must fix` : ""}` : "No issues found"}
          {all.length ? <span className="font-normal text-[var(--fg-muted)] underline underline-offset-2">{open ? " · hide" : " · show"}</span> : null}
        </button>

        {open && all.length > 0 && (
          <ul className="m-0 mt-2 flex list-none flex-col gap-1.5 p-0">
            {/* Must-fix first, then should-fix, then notes: the order a reader should act in. */}
            {report.checks
              .flatMap((c) => c.findings.map((f) => ({ c, f })))
              .sort((x, y) => RANK[x.f.severity] - RANK[y.f.severity])
              .map(({ c, f }) => {
                const i = all.indexOf(f);
                const key = findingKey(f, i);
                const sev = SEVERITY[f.severity];
                const body = (
                  <span className="min-w-0">
                    <span className="text-[0.6rem] font-bold uppercase tracking-wider" style={{ color: sev.color }}>
                      {sev.label} · {CHECK_LABEL[c.check] ?? c.check}
                    </span>
                    <span className="block">{f.why}</span>
                    {f.quote ? <span className="block italic text-[var(--fg-muted)]">“{f.quote}”</span> : null}
                    {f.fix ? <span className="block text-[var(--fg-muted)]">→ {f.fix}</span> : null}
                  </span>
                );
                return (
                  <li key={key}>
                    {canFix ? (
                      <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] p-2 text-[0.72rem] leading-snug">
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 shrink-0"
                          style={{ accentColor: "var(--color-electric-blue)" }}
                          aria-label="Include in fix"
                          checked={!skip.has(key)}
                          onChange={() => {
                            const next = new Set(skip);
                            if (next.has(key)) next.delete(key);
                            else next.add(key);
                            setSkip(next);
                          }}
                        />
                        {body}
                      </label>
                    ) : (
                      <div className="flex items-start gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] p-2 text-[0.72rem] leading-snug">{body}</div>
                    )}
                  </li>
                );
              })}
          </ul>
        )}
      </div>

      {canFix && selected.length > 0 && (
        <div className="flex flex-col gap-2.5 rounded-lg p-3" style={{ backgroundColor: "var(--accent)", color: "var(--accent-fg)" }}>
          <div className="flex flex-wrap items-center gap-3">
            <motion.button
              type="button"
              onClick={() => void fixCheckFindings(message.id, selected)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full px-4 text-[0.8rem] font-semibold text-white"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              Fix {selected.length} with Refine
            </motion.button>
            <span className="text-[0.7rem] opacity-75">One click. Nothing is approved or overwritten.</span>
          </div>
          <ol className="m-0 grid list-none gap-2 p-0 text-[0.68rem] leading-snug sm:grid-cols-3">
            <li>
              <span className="block font-bold">1 · Sends a note</span>
              <span className="opacity-75">The {selected.length} ticked finding{selected.length === 1 ? "" : "s"}, each quoted, go to Refine as one request.</span>
            </li>
            <li>
              <span className="block font-bold">2 · Writes the next draft</span>
              <span className="opacity-75">Only those lines change. This draft stays in the transcript for comparison.</span>
            </li>
            <li>
              <span className="block font-bold">3 · Checks it again</span>
              <span className="opacity-75">The new draft is re-scanned, so you can see the scores move.</span>
            </li>
          </ol>
        </div>
      )}

      {report.judge === "unavailable" && (
        <p className="m-0 text-[0.68rem] text-[var(--fg-faint)]">
          Only the rule checks ran; the review step was unavailable.{" "}
          <button type="button" onClick={() => recheckAsset(message.id)} className="cursor-pointer underline">
            Check again
          </button>
        </p>
      )}
      <p className="m-0 text-[0.66rem] text-[var(--fg-faint)]">
        Advisory: approving is still your call.
        {report.facts_missing.length > 0 && <> Not on record, so not checked: {report.facts_missing.join(", ")}.</>}
      </p>
    </motion.section>
  );
}
