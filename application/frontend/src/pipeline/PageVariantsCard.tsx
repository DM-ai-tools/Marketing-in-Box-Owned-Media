import { useState } from "react";
import { motion } from "framer-motion";
import { usePipelineStore } from "./pipelineStore";
import type { PageVariantsState, PipelineMessage } from "./pipelineStore";
import type { PageVariantId } from "./pipelineApi";

/** The Pillar Page's variant picker (`lib/pageVariants.ts`): which takes on the page to draft.
 * Each is a full draft, so each costs one generation; unavailable ones say what to supply. */
export function PageVariantsCard({ message }: { message: PipelineMessage }) {
  const start = usePipelineStore((s) => s.startPageVariants);
  const messages = usePipelineStore((s) => s.messages);
  if (!message.pageVariants) return null;
  const draftStatus = (id: string | undefined) => {
    const draft = id ? messages.find((m) => m.id === id) : undefined;
    if (!draft) return undefined;
    if (draft.generationError) return "failed";
    if (draft.streaming) return "drafting";
    if (draft.variantAlternate) return "kept";
    if (draft.variantNotChosen) return "not chosen";
    return draft.savePhase === "saved" ? "approved" : "ready";
  };
  return (
    <PageVariantsView
      state={message.pageVariants}
      superseded={Boolean(message.superseded)}
      draftStatus={draftStatus}
      onStart={(chosen) => void start(message.id, chosen)}
    />
  );
}

export function PageVariantsView({
  state,
  superseded = false,
  draftStatus = () => undefined,
  onStart,
}: {
  state: PageVariantsState;
  superseded?: boolean;
  draftStatus?: (messageId: string | undefined) => string | undefined;
  onStart: (chosen: PageVariantId[]) => void;
}) {
  const [picked, setPicked] = useState<PageVariantId[]>(() => state.options.filter((o) => o.defaultOn).map((o) => o.id));
  const choosing = state.status === "choosing" && !superseded;
  const toggle = (id: PageVariantId) => setPicked((now) => (now.includes(id) ? now.filter((x) => x !== id) : [...now, id]));
  const ordered = state.options.filter((o) => picked.includes(o.id)).map((o) => o.id);

  return (
    <div
      data-page-variants={state.status}
      className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">Pillar page variants</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        {choosing
          ? "Each variant is a full draft of the page, built a different way. They are compared side by side; you approve one for the rest of the run, and every draft stays in Deliverables."
          : state.status === "building"
            ? "Drafting the variants below, side by side."
            : "The variants are drafted below. Approve one for the rest of the run — the others stay in Deliverables."}
      </p>
      <ul className="m-0 mt-2.5 grid list-none gap-1.5 p-0">
        {state.options.map((option) => {
          const selected = picked.includes(option.id);
          const status = choosing ? undefined : state.chosen.includes(option.id) ? draftStatus(state.drafts[option.id]) ?? "queued" : "not drafted";
          return (
            <li key={option.id}>
              <label
                data-variant={option.id}
                data-available={option.available}
                className="flex min-h-10 items-start gap-2 rounded-lg border px-2.5 py-2 text-[0.8rem] sm:min-h-0"
                style={{
                  borderColor: choosing && selected ? "var(--color-electric-blue)" : "var(--border)",
                  backgroundColor: choosing && selected ? "var(--bg-sunken)" : undefined,
                  cursor: choosing && option.available ? "pointer" : "default",
                  opacity: option.available ? 1 : 0.65,
                }}
              >
                {choosing && (
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!option.available}
                    onChange={() => toggle(option.id)}
                    className="mt-0.5 shrink-0 accent-[var(--color-electric-blue)]"
                  />
                )}
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{option.label}</span>
                  {status && <span className="ml-1.5 text-[0.7rem] text-[var(--fg-faint)]">· {status}</span>}
                  <span className="block text-[0.72rem] leading-relaxed text-[var(--fg-muted)]">{option.available ? option.blurb : option.reason}</span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      {choosing && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <motion.button
            type="button"
            disabled={!ordered.length}
            onClick={() => ordered.length && onStart(ordered)}
            whileTap={ordered.length ? { scale: 0.97 } : undefined}
            className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
            style={{ backgroundColor: "var(--color-electric-blue)" }}
          >
            {ordered.length ? `Draft ${ordered.length} variant${ordered.length === 1 ? "" : "s"}` : "Tick at least one variant"}
          </motion.button>
          <span className="text-[0.7rem] text-[var(--fg-faint)]">
            One generation per variant, drafted by Claude. Build the one you choose with Manus afterwards.
          </span>
        </div>
      )}
    </div>
  );
}
