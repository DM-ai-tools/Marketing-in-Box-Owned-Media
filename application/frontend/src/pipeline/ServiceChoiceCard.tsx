import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";
import type { ScannedService } from "./pipelineApi";

/** The services (Phase 1) or sub-services (Phase 2) the reference page links to, to pick from.
 *
 * The Pillar Page builds one combined page for whatever is ticked here. Each ticked service's own
 * page is read when the stage generates, so the card says so: an operator choosing between "tick it"
 * and "type it" should know that only the first comes with that service's real copy.
 *
 * Three exits, always: tick and continue, type services the scan missed, or skip. The text box is
 * there during loading and after a failure too, so a site whose menu the scan cannot read never
 * blocks the stage.
 */
export function ServiceChoiceCard({ message }: { message: PipelineMessage }) {
  const confirmServices = usePipelineStore((s) => s.confirmServices);
  const skipServices = usePipelineStore((s) => s.skipServices);
  const retryServiceScan = usePipelineStore((s) => s.retryServiceScan);
  const phase = usePipelineStore((s) => s.phase);

  const scan = message.serviceScan;
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState("");

  // The pre-ticked services arrive with the scan, after the card has mounted.
  const status = scan?.status;
  const preselected = scan?.preselected;
  useEffect(() => {
    if (status === "ready" && preselected?.length) setPicked((current) => (current.length ? current : preselected));
  }, [status, preselected]);

  if (!scan) return null;

  const noun = phase === "phase2" ? "sub-services" : "services";
  const superseded = Boolean(message.superseded);
  const open = !superseded && (scan.status === "ready" || scan.status === "error" || scan.status === "loading");
  const host = scan.url ? safeHost(scan.url) : null;
  const groups = groupByParent(scan.services);
  const chosen = scan.services.filter((s) => picked.includes(s.url));
  const canConfirm = chosen.length > 0 || other.trim().length > 0;

  function toggle(url: string) {
    setPicked((current) => (current.includes(url) ? current.filter((u) => u !== url) : [...current, url]));
  }

  function confirm() {
    if (!canConfirm) return;
    confirmServices(
      message.id,
      chosen.map((s) => ({ name: s.name, url: s.url })),
      other,
    );
  }

  return (
    <div
      className="w-full min-w-0 max-w-[42rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">Which {noun} should this pillar page cover?</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        One combined page covers everything you pick. Each ticked {noun.slice(0, -1)}'s own page is
        read for its section's content, so nothing is invented for it.
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
          Reading the {noun} linked from {host ?? "the page"}…
        </div>
      )}

      {scan.status === "error" && !superseded && (
        <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">
            Couldn't read the {noun} from {host ?? "the page"}: {scan.error}
          </p>
          {scan.url && (
            <button
              type="button"
              onClick={() => retryServiceScan(message.id)}
              className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2"
            >
              Try again
            </button>
          )}
        </div>
      )}

      {scan.status === "ready" && !superseded && (
        <>
          {scan.services.length > 0 ? (
            <div className="mt-2.5 space-y-2.5">
              {groups.map(([parent, services]) => (
                <div key={parent || "_"}>
                  {parent && (
                    <div className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
                      {parent}
                    </div>
                  )}
                  <ul className="m-0 grid list-none gap-1.5 p-0 @[30rem]:grid-cols-2">
                    {services.map((service) => {
                      const selected = picked.includes(service.url);
                      return (
                        <li key={service.url}>
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
                              onChange={() => toggle(service.url)}
                              className="mt-0.5 shrink-0 accent-[var(--color-electric-blue)]"
                            />
                            <span className="min-w-0">
                              <span className="block font-medium">{service.name}</span>
                              <span className="block truncate text-[0.68rem] text-[var(--fg-faint)]">{pathOf(service.url)}</span>
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[0.75rem] text-[var(--fg-muted)]">
              {scan.notes[0] ?? `No ${noun} were found on ${host ?? "the page"}.`}
            </p>
          )}
        </>
      )}

      {open && (
        <div className="mt-3 border-t border-[var(--border)] pt-2.5">
          <label className="text-[0.75rem] text-[var(--fg-muted)]" htmlFor={`${message.id}-other`}>
            Any other {noun} to cover? (optional, comma-separated)
          </label>
          <textarea
            id={`${message.id}-other`}
            value={other}
            onChange={(e) => setOther(e.target.value)}
            rows={2}
            placeholder={phase === "phase2" ? "e.g. Equipment Finance, Invoice Finance" : "e.g. Asset Finance, Property Development Finance"}
            className="mt-1 w-full min-w-0 resize-y rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2 text-[0.82rem] outline-none focus:border-[var(--border-strong)]"
          />
          <p className="mt-0.5 text-[0.68rem] text-[var(--fg-faint)]">
            Typed ones have no page to read, so their sections stay brief and flag what to confirm.
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              disabled={!canConfirm || scan.status === "loading"}
              onClick={confirm}
              whileTap={canConfirm ? { scale: 0.97 } : undefined}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              {coverLabel(chosen.length, other, noun)}
            </motion.button>
            <motion.button
              type="button"
              onClick={() => skipServices(message.id)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
            >
              Skip: use the page copy as it is
            </motion.button>
          </div>
        </div>
      )}

      {scan.status === "done" && (
        <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5">
          <div className="text-[0.72rem] text-[var(--fg-faint)]">
            Covering {(scan.chosen?.length ?? 0) + (scan.other?.trim() ? 1 : 0) > 1 ? "these" : "this"}
          </div>
          <ul className="m-0 mt-1 list-none space-y-0.5 p-0 text-[0.82rem]">
            {(scan.chosen ?? []).map((s) => (
              <li key={s.url ?? s.name}>{s.name}</li>
            ))}
            {scan.other?.trim() && <li>{scan.other.trim()} <span className="text-[var(--fg-faint)]">(typed)</span></li>}
          </ul>
        </div>
      )}
      {scan.status === "skipped" && (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">skipped: the page covers what its copy covers</p>
      )}
      {superseded && scan.status !== "done" && scan.status !== "skipped" && (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">asked again below</p>
      )}
    </div>
  );
}

function coverLabel(count: number, other: string, noun: string): string {
  const typed = other.split(/[,;\n]/).filter((s) => s.trim()).length;
  const total = count + typed;
  if (!total) return `Pick ${noun} to cover`;
  return `Cover ${total} ${total === 1 ? noun.slice(0, -1) : noun}`;
}

function groupByParent(services: ScannedService[]): [string, ScannedService[]][] {
  const groups = new Map<string, ScannedService[]>();
  for (const service of services) {
    const key = service.parent ?? "";
    groups.set(key, [...(groups.get(key) ?? []), service]);
  }
  return [...groups.entries()];
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
