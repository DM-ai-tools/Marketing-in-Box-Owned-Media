import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { CountUp } from "../components/CountUp";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";
import type { PlanSubServiceCandidate } from "./pipelineApi";

/** The Plan of Action's sub-services, to confirm before the plan is written.
 *
 * Two groups: what the client's site already has a page for under the primary service (read off
 * its own menu, each with its real URL), and what it should add (suggested from the approved
 * competitor research and the ICP). Every row carries its keyword cluster and, when DataForSEO is
 * configured, the measured monthly volume — the card says plainly when it is not, because the plan
 * then labels every volume as an estimate.
 *
 * Everything starts ticked. Three exits, always: confirm the ticked set (plus any typed), type
 * sub-services the discovery missed, or skip and let the plan infer them. The text box is there
 * during loading and after a failure too, so discovery never blocks the plan.
 */
export function PlanSubServiceCard({ message }: { message: PipelineMessage }) {
  const confirm = usePipelineStore((s) => s.confirmPlanSubServices);
  const skip = usePipelineStore((s) => s.skipServices);
  const retry = usePipelineStore((s) => s.retryServiceScan);
  const reduceMotion = useReducedMotion();

  const scan = message.serviceScan;
  const plan = scan?.plan;
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState("");

  const status = scan?.status;
  const preselected = scan?.preselected;
  useEffect(() => {
    if (status === "ready" && preselected?.length) setPicked((current) => (current.length ? current : preselected));
  }, [status, preselected]);

  if (!scan || !plan) return null;

  const superseded = Boolean(message.superseded);
  const open = !superseded && (scan.status === "ready" || scan.status === "error" || scan.status === "loading");
  const host = scan.url ? safeHost(scan.url) : null;
  const existing = plan.candidates.filter((c) => c.status === "existing");
  const suggested = plan.candidates.filter((c) => c.status !== "existing");
  const typedCount = other.split(/[,;\n]/).filter((s) => s.trim()).length;
  const total = picked.length + typedCount;
  const measured = plan.volumeSource === "dataforseo";

  function toggle(name: string) {
    setPicked((current) => (current.includes(name) ? current.filter((n) => n !== name) : [...current, name]));
  }

  return (
    <div
      className="w-full min-w-0 max-w-[46rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">Which sub-services should Phase 2 build?</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        Sub-services of {plan.primaryService || "the primary service"}: the ones {host ?? "the client's site"} already has
        a page for, and the ones competitors cover that it does not. The plan briefs each one you keep, in priority order.
      </p>

      {scan.status === "loading" && (
        <div className="mt-2.5 flex items-center gap-2 text-[0.78rem] text-[var(--fg-muted)]" role="status">
          <span className="flex gap-1" aria-hidden>
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                className="h-1 w-1 rounded-full bg-[var(--color-electric-blue)]"
                animate={{ opacity: [0.25, 1, 0.25] }}
                transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15, ease: "easeInOut" }}
              />
            ))}
          </span>
          Reading {host ?? "the site"}'s menu, comparing it with the competitor research, and measuring search volumes…
        </div>
      )}

      {scan.status === "error" && !superseded && (
        <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">Couldn't suggest sub-services: {scan.error}</p>
          <button
            type="button"
            onClick={() => retry(message.id)}
            className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2"
          >
            Try again
          </button>
        </div>
      )}

      {scan.status === "ready" && !superseded && (
        <>
          <p className="mt-2 flex flex-wrap items-center gap-1.5 text-[0.7rem] text-[var(--fg-faint)]">
            {measured && <SourceBadge still={!!reduceMotion} />}
            {measured
              ? `Monthly Google searches${plan.location ? `, ${plan.location}` : ""}.`
              : "Search volumes not measured: the plan will label its volumes as estimates."}
          </p>
          {plan.volumeError && (
            <p
              role="alert"
              className="mt-1.5 rounded-lg border px-2.5 py-1.5 text-[0.72rem] leading-relaxed"
              style={{ borderColor: "var(--color-signal-amber)", color: "var(--fg)" }}
            >
              {plan.volumeError}
            </p>
          )}
          <CandidateGroup title="Already on the client's site" rows={existing} picked={picked} onToggle={toggle} still={!!reduceMotion} />
          <CandidateGroup title="Suggested to add" rows={suggested} picked={picked} onToggle={toggle} still={!!reduceMotion} />
          {!plan.candidates.length && (
            <p className="mt-2 text-[0.75rem] text-[var(--fg-muted)]">{scan.notes[0] ?? "No sub-services were found."}</p>
          )}
          {plan.candidates.length > 0 && scan.notes.length > 0 && (
            <ul className="m-0 mt-2 list-none space-y-0.5 p-0 text-[0.7rem] text-[var(--fg-faint)]">
              {scan.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </>
      )}

      {open && (
        <div className="mt-3 border-t border-[var(--border)] pt-2.5">
          <label className="text-[0.75rem] text-[var(--fg-muted)]" htmlFor={`${message.id}-other`}>
            Any other sub-services to plan? (optional, comma-separated)
          </label>
          <textarea
            id={`${message.id}-other`}
            value={other}
            onChange={(e) => setOther(e.target.value)}
            rows={2}
            placeholder="e.g. Pinterest Ads, Influencer Campaigns"
            className="mt-1 w-full min-w-0 resize-y rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2 text-[0.82rem] outline-none focus:border-[var(--border-strong)]"
          />
          <p className="mt-0.5 text-[0.68rem] text-[var(--fg-faint)]">
            Typed ones are planned as new pages, with their volumes estimated.
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              disabled={!total || scan.status === "loading"}
              onClick={() => total && confirm(message.id, picked, other)}
              whileTap={total ? { scale: 0.97 } : undefined}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              {total ? `Plan ${total} sub-service${total === 1 ? "" : "s"}` : "Pick sub-services to plan"}
            </motion.button>
            <motion.button
              type="button"
              onClick={() => skip(message.id)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
            >
              Skip: let the plan infer them
            </motion.button>
          </div>
        </div>
      )}

      {scan.status === "done" && (
        <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5">
          <div className="text-[0.72rem] text-[var(--fg-faint)]">Planning</div>
          <ul className="m-0 mt-1 list-none space-y-0.5 p-0 text-[0.82rem]">
            {(plan.chosenNames ?? []).map((name) => {
              const row = plan.candidates.find((c) => c.name === name);
              return (
                <li key={name}>
                  {name}{" "}
                  <span className="text-[var(--fg-faint)]">
                    ({row ? (row.status === "existing" ? "existing page" : "new page") : "typed"}
                    {row?.combined_volume != null ? `, ${row.combined_volume.toLocaleString("en-US")}/mo` : ""})
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {scan.status === "skipped" && (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">skipped: the plan infers the sub-services</p>
      )}
      {superseded && scan.status !== "done" && scan.status !== "skipped" && (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">asked again below</p>
      )}
    </div>
  );
}

function CandidateGroup({
  title,
  rows,
  picked,
  onToggle,
  still,
}: {
  title: string;
  rows: PlanSubServiceCandidate[];
  picked: string[];
  onToggle: (name: string) => void;
  still: boolean;
}) {
  if (!rows.length) return null;
  return (
    <div className="mt-2.5">
      <div className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">{title}</div>
      <ul className="m-0 grid list-none gap-1.5 p-0">
        {rows.map((row) => {
          const selected = picked.includes(row.name);
          return (
            <li key={row.name}>
              <label
                className="flex min-h-10 cursor-pointer items-start gap-2 rounded-xl border px-2.5 py-2 text-[0.8rem] sm:min-h-0"
                style={{
                  borderColor: selected ? "var(--color-electric-blue)" : "var(--border)",
                  backgroundColor: selected ? "var(--bg-sunken)" : undefined,
                }}
              >
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => onToggle(row.name)}
                  className="mt-0.5 shrink-0 accent-[var(--color-electric-blue)]"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                    <span className="font-medium">{row.name}</span>
                    <span className="text-[0.72rem] tabular-nums text-[var(--fg-muted)]">
                      {row.combined_volume != null ? (
                        <>
                          <CountUp to={row.combined_volume} still={still} />
                          /mo
                        </>
                      ) : (
                        "volume not measured"
                      )}
                    </span>
                  </span>
                  <span className="block truncate text-[0.68rem] text-[var(--fg-faint)]">
                    {row.status === "existing" ? pathOf(row.url) : `new · ${row.slug}`}
                  </span>
                  {row.keywords.length > 0 && (
                    <span className="mt-0.5 block text-[0.68rem] text-[var(--fg-muted)]">
                      {row.keywords
                        .map((k) => (k.volume != null ? `${k.keyword} (${k.volume.toLocaleString("en-US")})` : k.keyword))
                        .join(" · ")}
                    </span>
                  )}
                  {row.why && <span className="mt-0.5 block text-[0.68rem] italic text-[var(--fg-faint)]">{row.why}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function safeHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname === "/" ? parsed.hostname : parsed.pathname;
  } catch {
    return url;
  }
}

/** "DataForSEO" with a soft pulse ring, marking volumes that were measured rather than estimated. */
function SourceBadge({ still }: { still: boolean }) {
  return (
    <span className="relative inline-flex items-center gap-1 rounded-full border border-[var(--border)] px-1.5 py-[1px] font-semibold text-[var(--color-electric-blue)]">
      <span className="relative flex h-1.5 w-1.5">
        {!still && (
          <motion.span
            aria-hidden
            className="absolute inset-0 rounded-full bg-[var(--color-electric-blue)]"
            initial={{ scale: 1, opacity: 0.6 }}
            animate={{ scale: 2.6, opacity: 0 }}
            transition={{ duration: 1.4, repeat: 2, ease: "easeOut" }}
          />
        )}
        <span className="relative h-1.5 w-1.5 rounded-full bg-[var(--color-electric-blue)]" />
      </span>
      DataForSEO
    </span>
  );
}
