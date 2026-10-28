import { useEffect } from "react";
import { motion } from "framer-motion";
import type { DocSection } from "../lib/assetDocument";
import { usePipelineStore, type PipelineMessage } from "./pipelineStore";

/** The reader's Summary tab: what the deliverable contains, as a few points.
 *
 * The only reader view that is not the document itself, so it says how it was made. The points come
 * from one model call (`app/services/asset_summary.py`) and are checked there: a point quoting a
 * number the document does not contain is removed, and a point's section must be a real heading.
 * Each point with a section jumps to it.
 *
 * Made once per draft and kept on the message (`message.docSummary`), so reopening a deliverable
 * costs nothing; a refined draft has different text and is summarised again. */
export function SummaryView({
  messageId,
  label,
  sections,
  onJump,
}: {
  messageId: string;
  label: string;
  sections: DocSection[];
  onJump: (sectionId: string) => void;
}) {
  const summary = usePipelineStore((s) => s.messages.find((m) => m.id === messageId)?.docSummary);
  const load = usePipelineStore((s) => s.loadDocSummary);

  useEffect(() => {
    void load(messageId);
  }, [load, messageId]);

  return (
    <SummaryContent
      summary={summary}
      label={label}
      sections={sections}
      onJump={onJump}
      onRetry={() => void load(messageId, true)}
    />
  );
}

/** The tab's content for one state of `message.docSummary`. Pure, so it renders the same anywhere. */
export function SummaryContent({
  summary,
  label,
  sections,
  onJump,
  onRetry,
}: {
  summary: PipelineMessage["docSummary"];
  label: string;
  sections: DocSection[];
  onJump: (sectionId: string) => void;
  onRetry: () => void;
}) {

  /** The outline section a heading belongs to: the section itself, or the one holding it as a sub-heading. */
  const sectionFor = (heading: string): DocSection | undefined => {
    if (!heading) return undefined;
    const want = heading.toLowerCase();
    return (
      sections.find((s) => s.label.toLowerCase() === want) ??
      sections.find((s) => s.subheads.some((sub) => sub.label.toLowerCase() === want))
    );
  };

  return (
    <div data-summary-view className="mx-auto w-full max-w-[44rem]">
      <h2 className="text-balance text-[1.15rem] font-semibold">{label} — summary</h2>
      <p className="mt-1 text-[0.74rem] leading-relaxed text-[var(--fg-faint)]">
        Written by AI from this document and checked against it: a point quoting a number the document
        does not contain is removed. Open Visual or Text for the full detail.
      </p>

      {(!summary || summary.status === "loading") && (
        <div aria-live="polite" className="mt-5">
          <p className="text-[0.82rem] text-[var(--fg-muted)]">Summarising this document…</p>
          <ul className="mt-3 space-y-2.5" aria-hidden>
            {[88, 72, 80, 64, 76].map((w, i) => (
              <li key={i} className="h-3 animate-pulse rounded-full bg-[var(--bg-sunken)] motion-reduce:animate-none" style={{ width: `${w}%` }} />
            ))}
          </ul>
        </div>
      )}

      {summary?.status === "error" && (
        <div className="mt-5 rounded-xl border px-3 py-2.5 text-[0.8rem]" style={{ borderColor: "var(--color-signal-amber)" }}>
          <p className="font-semibold">The summary could not be made.</p>
          <p className="mt-0.5 text-[var(--fg-muted)]">{summary.error}</p>
          <motion.button
            type="button"
            onClick={onRetry}
            whileTap={{ scale: 0.97 }}
            className="mt-2 cursor-pointer rounded-full border border-[var(--border-strong)] px-3 py-1 text-[0.76rem] font-semibold hover:bg-[var(--hover)]"
          >
            Try again
          </motion.button>
        </div>
      )}

      {summary?.status === "done" && summary.points && (
        <ul className="mt-5 space-y-2.5">
          {summary.points.map((point, i) => {
            const target = sectionFor(point.section);
            return (
              <li key={i} className="flex gap-2.5 text-[0.9rem] leading-relaxed">
                <span aria-hidden className="mt-[0.55em] size-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
                <span className="min-w-0">
                  {point.text}
                  {target && (
                    <button
                      type="button"
                      onClick={() => onJump(target.id)}
                      className="ml-1.5 cursor-pointer whitespace-nowrap rounded-full border border-[var(--border)] px-1.5 py-[1px] align-[0.1em] text-[0.66rem] font-semibold text-[var(--fg-muted)] hover:bg-[var(--hover)] hover:text-[var(--fg)]"
                    >
                      {point.section} →
                    </button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
