import { motion } from "framer-motion";
import { comparisonRows } from "../lib/pageVariants";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage, VariantCompareState } from "./pipelineStore";

/** The Pillar Page variants side by side: every number counted off each draft, the hosted page and
 * the competitor median beside them where a variant measured them. Approve on each column. */
export function VariantCompareCard({ message }: { message: PipelineMessage }) {
  const messages = usePipelineStore((s) => s.messages);
  const save = usePipelineStore((s) => s.saveStage);
  const remeasure = usePipelineStore((s) => s.remeasureVariants);
  const state = message.variantCompare;
  if (!state) return null;
  const drafts = state.messageIds.map((id) => messages.find((m) => m.id === id));
  // The run's pillar page — not an alternate kept only in Deliverables.
  const approvedId =
    drafts.find((m) => m?.savePhase === "saved" && !m.variantAlternate && !m.variantNotChosen)?.id ?? null;
  return (
    <VariantCompareView
      state={state}
      draftIds={state.messageIds}
      approvedId={approvedId}
      closed={drafts.map(
        (m) =>
          !m ||
          Boolean(m.superseded) ||
          Boolean(m.refineSubmitted) ||
          Boolean(m.variantAlternate) ||
          m.savePhase === "saving" ||
          m.savePhase === "saved",
      )}
      onApprove={(id) => void save(id)}
      onRemeasure={() => void remeasure(message.id)}
    />
  );
}

export function VariantCompareView({
  state,
  draftIds,
  approvedId,
  closed,
  onApprove,
  onRemeasure,
}: {
  state: VariantCompareState;
  draftIds: string[];
  approvedId: string | null;
  closed: boolean[];
  onApprove: (messageId: string) => void;
  onRemeasure: () => void;
}) {
  const table = state.pages ? comparisonRows(state.pages, { current: state.current, competitorMedianWords: state.competitorMedianWords }) : null;
  const drafted = state.pages?.length ?? 0;
  return (
    <div
      data-variant-compare={state.status}
      className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
    >
      <div className="text-[0.92rem] font-medium">Compare the variants</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        Counted off each page, not judged. Approve one for the rest of the run; every drafted variant stays in Deliverables. A highlighted
        cell leads on a measure where more helps the buyer.
      </p>

      {state.status === "loading" && <p className="mt-2 text-[0.78rem] text-[var(--fg-muted)]" role="status">Measuring the drafts…</p>}
      {state.status === "error" && (
        <div className="mt-2 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">Couldn't measure them: {state.error}</p>
          <button type="button" onClick={onRemeasure} className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2">
            Try again
          </button>
        </div>
      )}

      {table && (
        <div className="mt-2.5 overflow-x-auto">
          <table className="w-full min-w-[28rem] border-collapse text-[0.74rem]">
            <thead>
              <tr>
                <th className="py-1 pr-2 text-left font-medium text-[var(--fg-faint)]" />
                {table.columns.map((col, i) => (
                  <th
                    key={col + i}
                    className="px-2 py-1 text-left align-bottom font-semibold"
                    style={i >= drafted ? { color: "var(--fg-faint)", fontWeight: 500 } : undefined}
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row) => (
                <tr key={row.label} className="border-t border-[var(--border)] align-top">
                  <td className="py-1 pr-2 text-[var(--fg-muted)]">{row.label}</td>
                  {row.cells.map((cell, i) => (
                    <td
                      key={i}
                      data-best={row.best === i || undefined}
                      className="px-2 py-1 tabular-nums"
                      style={
                        row.best === i
                          ? { fontWeight: 600, color: "var(--color-signal-green)" }
                          : i >= drafted
                            ? { color: "var(--fg-faint)" }
                            : undefined
                      }
                    >
                      {cell}
                      {row.best === i && <span className="sr-only"> (leads)</span>}
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="border-t border-[var(--border)]">
                <td />
                {draftIds.slice(0, drafted).map((id, i) => (
                  <td key={id} className="px-2 pb-0.5 pt-2">
                    {approvedId === id ? (
                      <span className="text-[0.74rem] font-semibold text-[var(--color-signal-green)]">✓ For the run</span>
                    ) : approvedId ? (
                      <span className="text-[0.72rem] text-[var(--fg-faint)]">Kept in Deliverables</span>
                    ) : (
                      <motion.button
                        type="button"
                        disabled={closed[i]}
                        onClick={() => onApprove(id)}
                        whileTap={closed[i] ? undefined : { scale: 0.97 }}
                        className="min-h-9 cursor-pointer rounded-full px-3 py-1 text-[0.74rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
                        style={{ backgroundColor: "var(--color-electric-blue)" }}
                      >
                        Approve this one
                      </motion.button>
                    )}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
      {state.status === "done" && state.current?.error && (
        <p data-current-unread className="mt-2 text-[0.72rem] text-[var(--color-signal-amber)]">
          The current page{state.current.url ? ` (${state.current.url})` : ""} couldn't be read, so its column is empty:{" "}
          {state.current.error}.
        </p>
      )}
      {state.status === "done" && state.current === null && (
        <p className="mt-2 text-[0.72rem] text-[var(--fg-faint)]">No current page to compare: this page doesn't exist yet.</p>
      )}
      {state.status === "done" && (
        <button type="button" onClick={onRemeasure} className="mt-2 cursor-pointer text-[0.72rem] text-[var(--fg-muted)] underline underline-offset-2">
          Measure again (after a Refine)
        </button>
      )}
    </div>
  );
}
