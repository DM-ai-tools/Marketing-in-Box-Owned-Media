import { useState } from "react";
import { motion } from "framer-motion";
import type { CheckFinding, CheckName, CheckResult } from "./pipelineApi";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";

const CHECK_LABEL: Record<CheckName, string> = {
  offer_relevance: "Offers",
  funnel_relevance: "Funnel",
  business_logic: "Business rules",
  virality: "Virality",
};

const SEVERITY_COLOR = {
  error: "var(--color-signal-orange)",
  warn: "var(--color-electric-blue)",
  info: "var(--fg-faint)",
} as const;

function dotColor(result: CheckResult): string {
  if (result.findings.some((f) => f.severity === "error")) return "var(--color-signal-orange)";
  if (result.score === null) return result.findings.some((f) => f.severity === "warn") ? "#d9a400" : "var(--border-strong)";
  if (result.score >= 80) return "var(--color-signal-green)";
  if (result.score >= 60) return "#d9a400";
  return "var(--color-signal-orange)";
}

function findingKey(f: CheckFinding, i: number): string {
  return `${f.check}:${i}:${f.quote.slice(0, 40)}`;
}

/** The check of one draft against the client's business: offers, funnel, business rules and, for
 * content, predicted virality.
 *
 * Advisory. It sits above the Approve row and never disables it: a static rule will be wrong for
 * some draft, and the operator is the one who knows which. What it offers instead is the one-click
 * way to act on it — the chosen findings go to Refine as a single note.
 *
 * Open by default only when something is an error; a clean or warnings-only draft shows its scores
 * in one line and stays out of the way. */
export function AssetCheckPanel({ message }: { message: PipelineMessage }) {
  const recheckAsset = usePipelineStore((s) => s.recheckAsset);
  const fixCheckFindings = usePipelineStore((s) => s.fixCheckFindings);
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

  if (check.status === "running") {
    return (
      <div className="mb-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-2 text-[0.74rem] text-[var(--fg-muted)]">
        Checking this draft against the client's offers, funnel and business rules…
      </div>
    );
  }

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
    <div className="mb-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-0.5 text-[0.72rem] font-semibold text-[var(--fg-muted)]">Business check</span>
        {report.checks.map((c) => (
          <span
            key={c.check}
            title={c.verdict || undefined}
            className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--bg-raised)] px-2 py-0.5 text-[0.68rem] font-medium"
          >
            <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: dotColor(c) }} aria-hidden />
            {CHECK_LABEL[c.check] ?? c.check}
            {c.score !== null ? ` ${c.score}` : ""}
            {c.check === "virality" ? <span className="text-[var(--fg-faint)]"> · prediction</span> : null}
          </span>
        ))}
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="ml-auto cursor-pointer text-[0.7rem] font-medium text-[var(--fg-muted)] underline underline-offset-2"
        >
          {all.length ? `${all.length} finding${all.length === 1 ? "" : "s"}${errors ? `, ${errors} to fix` : ""}` : "No issues found"}
          {all.length ? (open ? " · hide" : " · show") : ""}
        </button>
      </div>

      {open && (
        <div className="mt-2 space-y-2">
          {report.checks.map((c) => (
            <div key={c.check}>
              <div className="text-[0.72rem] font-semibold">
                {CHECK_LABEL[c.check] ?? c.check}
                {c.verdict ? <span className="font-normal text-[var(--fg-muted)]"> — {c.verdict}</span> : null}
              </div>
              {c.check === "virality" && (
                <div className="text-[0.66rem] text-[var(--fg-faint)]">
                  A prediction, not a measurement.{" "}
                  {report.virality_basis === "benchmarked"
                    ? "Compared against this client's and competitors' real post engagement and keyword demand."
                    : "Rubric only: no real engagement data on this run yet (Stage 10 collects it)."}
                </div>
              )}
              <ul className="mt-1 space-y-1">
                {c.findings.map((f) => {
                  const i = all.indexOf(f);
                  const key = findingKey(f, i);
                  return (
                    <li key={key} className="flex gap-1.5 text-[0.72rem] leading-snug">
                      {canFix && (
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          aria-label="Include in fix"
                          checked={!skip.has(key)}
                          onChange={() => {
                            const next = new Set(skip);
                            if (next.has(key)) next.delete(key);
                            else next.add(key);
                            setSkip(next);
                          }}
                        />
                      )}
                      <span>
                        <span className="font-semibold uppercase" style={{ color: SEVERITY_COLOR[f.severity] }}>
                          {f.severity === "warn" ? "check" : f.severity === "info" ? "note" : "fix"}
                        </span>{" "}
                        {f.why}
                        {f.quote ? <span className="block italic text-[var(--fg-muted)]">“{f.quote}”</span> : null}
                        {f.fix ? <span className="block text-[var(--fg-faint)]">→ {f.fix}</span> : null}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
          {report.facts_missing.length > 0 && (
            <p className="text-[0.68rem] text-[var(--fg-faint)]">
              Not on record, so not checked: {report.facts_missing.join(", ")}.
            </p>
          )}
          {report.judge === "unavailable" && (
            <p className="text-[0.68rem] text-[var(--fg-faint)]">
              Only the rule checks ran; the review step was unavailable.{" "}
              <button type="button" onClick={() => recheckAsset(message.id)} className="cursor-pointer underline">
                Check again
              </button>
            </p>
          )}
          {canFix && selected.length > 0 && (
            <motion.button
              type="button"
              onClick={() => void fixCheckFindings(message.id, selected)}
              whileTap={{ scale: 0.97 }}
              className="min-h-9 cursor-pointer rounded-full px-3 py-1 text-[0.74rem] font-semibold text-white sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              Fix {selected.length} with Refine
            </motion.button>
          )}
          <p className="text-[0.66rem] text-[var(--fg-faint)]">Advisory: approving is still your call.</p>
        </div>
      )}
    </div>
  );
}
