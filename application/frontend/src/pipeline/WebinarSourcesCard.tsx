import { motion, useReducedMotion } from "framer-motion";
import { useState } from "react";
import type { WebinarSource } from "./pipelineApi";
import { usePipelineStore, type PipelineMessage } from "./pipelineStore";

const STATUS: Record<WebinarSource["status"], { label: string; tone: string }> = {
  read: { label: "Read", tone: "var(--color-signal-green)" },
  failed: { label: "Couldn't read", tone: "var(--color-signal-amber)" },
  not_read: { label: "Not read", tone: "var(--fg-faint)" },
};

/** The Webinar's competitor sources (`WEBINAR_SOURCES_OPTION`): each competitor webinar page the
 * approved research found, read for free, with why it was chosen and what came back. The operator
 * uses them as they are or adds transcripts of their own; nothing is typed cold. */
export function WebinarSourcesCard({ message }: { message: PipelineMessage }) {
  const confirm = usePipelineStore((s) => s.confirmWebinarSources);
  const retry = usePipelineStore((s) => s.retryServiceScan);
  const still = Boolean(useReducedMotion());
  const [own, setOwn] = useState("");

  const scan = message.serviceScan;
  const webinar = scan?.webinar;
  if (!scan || !webinar) return null;
  const result = webinar.result;
  const superseded = Boolean(message.superseded);
  const readCount = result?.sources.filter((s) => s.status === "read").length ?? 0;

  if (scan.status === "done" || scan.status === "skipped") {
    return (
      <div className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 @[30rem]:px-4" style={{ opacity: 0.75 }}>
        <div className="text-[0.88rem] font-medium">Competitor webinar sources</div>
        <p className="mt-0.5 text-[0.76rem] text-[var(--fg-muted)]">
          {webinar.usedCount ?? 0} competitor webinar page{webinar.usedCount === 1 ? "" : "s"} used
          {webinar.addedOwn ? ", plus sources you added" : ""}.
        </p>
        {result && <SourceList sources={result.sources} />}
      </div>
    );
  }

  return (
    <div
      data-webinar-sources
      className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">Competitor webinar sources</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        The webinar pages the competitor research found, read automatically. Step 2 of the webinar synthesises from these.
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
          Reading each competitor's webinar page…
        </div>
      )}

      {scan.status === "error" && !superseded && (
        <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">Couldn't read the webinar pages: {scan.error}</p>
          <button type="button" onClick={() => retry(message.id)} className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2">
            Try again
          </button>
        </div>
      )}

      {scan.status === "ready" && result && !superseded && (
        <>
          <SourceList sources={result.sources} />
          {result.notes.map((note, i) => (
            <p key={i} className="mt-1.5 text-[0.72rem] text-[var(--color-signal-amber)]">{note}</p>
          ))}
          <textarea
            value={own}
            onChange={(e) => setOwn(e.target.value)}
            rows={3}
            placeholder="Optional: paste transcripts, slide outlines or more webinar URLs of your own"
            aria-label="Your own competitor webinar sources"
            className="mt-2.5 w-full rounded-lg border border-[var(--border-strong)] bg-[var(--bg)] px-2.5 py-1.5 text-[0.8rem] outline-none focus:border-[var(--color-electric-blue)]"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              disabled={!readCount && !own.trim()}
              onClick={() => confirm(message.id, own)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              {readCount ? `Use ${readCount} source${readCount === 1 ? "" : "s"}${own.trim() ? " + mine" : ""}` : "Use my sources"}
            </motion.button>
            {!readCount && !own.trim() && (
              <span className="text-[0.72rem] text-[var(--fg-muted)]">No page could be read. Paste sources above to continue.</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SourceList({ sources }: { sources: WebinarSource[] }) {
  if (!sources.length) return null;
  return (
    <ul className="mt-2.5 divide-y divide-[var(--border)]">
      {sources.map((s) => (
        <li key={`${s.domain}-${s.url ?? ""}`} data-webinar-source={s.status} className="py-2 text-[0.78rem]">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{s.name}</span>
            <span className="text-[0.7rem] font-semibold uppercase tracking-wide" style={{ color: STATUS[s.status].tone }}>
              {STATUS[s.status].label}
            </span>
            {s.status === "read" && <span className="text-[0.7rem] text-[var(--fg-faint)]">{s.chars.toLocaleString("en-US")} characters</span>}
          </div>
          {s.url && (
            <a href={s.url} target="_blank" rel="noreferrer" className="break-all text-[0.72rem] text-[var(--color-electric-blue)] underline-offset-2 hover:underline">
              {s.url}
            </a>
          )}
          <p className="mt-0.5 text-[0.74rem] text-[var(--fg-muted)]">
            <span className="font-medium text-[var(--fg)]">Why: </span>
            {s.reason}
          </p>
          {s.status === "read" && s.excerpt && (
            <p className="mt-0.5 text-[0.72rem] italic text-[var(--fg-muted)]">
              {s.title ? `“${s.title}” — ` : ""}
              {s.excerpt}
            </p>
          )}
          {s.error && <p className="mt-0.5 text-[0.72rem] text-[var(--fg-muted)]">{s.error}</p>}
        </li>
      ))}
    </ul>
  );
}
