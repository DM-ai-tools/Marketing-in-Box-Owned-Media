import { assetGlance } from "../lib/assetGlance";
import { MiniGlance } from "./visual/GlanceView";
import { useMemo } from "react";
import { countWords, parseAssetDocument } from "../lib/assetDocument";
import { summariseAsset } from "../lib/assetSummary";
import { PreviewThumb } from "../components/PreviewThumb";
import { SummaryFlags } from "../components/AssetDocumentView";
import { useUiStore } from "../store/uiStore";
import { stagesFor } from "./pipelineData";
import { messagesInPhase, usePipelineStore } from "./pipelineStore";

/**
 * Every asset this chat has approved, as a grid.
 *
 * The point is that the transcript stops being the only route to output. Fifteen deliverables
 * reachable only by scrolling a conversation is fine on the run that produced them and useless a
 * week later, when the question is "where is the pillar page" rather than "what happened next".
 *
 * Derived from the same messages the transcript renders rather than fetched, which is a deliberate
 * limit as much as a convenience: it can never disagree with what the cards say, and it is scoped
 * to *this chat*. A library spanning every run the client has ever had needs the backend to list
 * `context_entries` across runs, and inventing a cross-run view out of one chat's messages would
 * look like that feature while quietly being something much smaller.
 */
export function DeliverablesGrid() {
  const allMessages = usePipelineStore((s) => s.messages);
  const phase = usePipelineStore((s) => s.phase);
  const activePhase2TrackId = usePipelineStore((s) => s.activePhase2TrackId);
  const openReader = useUiStore((s) => s.openReader);

  const stages = stagesFor(phase);
  const items = useMemo(() => {
    return messagesInPhase(allMessages, phase, activePhase2TrackId)
      .filter((m) => m.kind === "generation" && m.savePhase === "saved" && !m.superseded && m.text)
      .map((message) => {
        const text = message.text ?? "";
        const stage = stages.find((s) => s.asset.asset_id === message.assetId);
        const doc = parseAssetDocument(text);
        return {
          id: message.id,
          label: stage?.asset.label ?? message.assetId ?? "Asset",
          emoji: stage?.emoji ?? "✨",
          stageNumber: stage?.stageNumber,
          words: doc.words || countWords(text),
          summary: summariseAsset(message.assetId, doc, text),
          // The same picture the reader opens on, card-sized: a score, the ladder's rungs, a funnel.
          glance: assetGlance(message.assetId, doc),
          sections: doc.sections.length,
          structured: doc.structured,
        };
      });
  }, [allMessages, phase, stages, activePhase2TrackId]);

  if (!items.length) {
    return (
      <div className="mx-auto max-w-[34rem] py-10 text-center">
        <p className="text-[0.92rem] font-medium">Nothing approved yet</p>
        <p className="mt-1.5 text-[0.84rem] leading-relaxed text-[var(--fg-muted)]">
          Assets appear here once you save them to the Context Store. Everything you approve in this
          chat is collected on this tab, so you do not have to scroll the conversation to find it
          again.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[56rem]">
      <p className="mb-3 text-[0.78rem] text-[var(--fg-muted)]">
        {items.length} approved {items.length === 1 ? "asset" : "assets"} in this chat ·{" "}
        {items.reduce((sum, i) => sum + i.words, 0).toLocaleString()} words total
      </p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(11.5rem,1fr))] gap-2.5">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => openReader(item.id)}
            className="cursor-pointer overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-raised)] text-left transition-colors hover:border-[var(--border-strong)]"
          >
            {/* A page thumbnail when the asset built one, the stage's emoji when it did not. A
                lead-magnet set shows its first file, which is the one that identifies it. */}
            <span className="flex h-[4.9rem] items-center justify-center border-b border-[var(--border)] bg-[var(--bg-sunken)]">
              {item.summary.previews.length ? (
                <PreviewThumb
                  html={item.summary.previews[0].html}
                  label={item.summary.previews[0].label}
                  width={116}
                />
              ) : (
                <span aria-hidden className="text-[1.5rem]">
                  {item.emoji}
                </span>
              )}
            </span>
            <span className="block px-2.5 py-2">
              <span className="flex items-center gap-1.5">
                {item.stageNumber !== undefined && (
                  <span className="shrink-0 rounded-full border border-[var(--border-strong)] px-1 text-[0.58rem] font-semibold text-[var(--fg-faint)]">
                    {String(item.stageNumber).padStart(2, "0")}
                  </span>
                )}
                <span className="min-w-0 truncate text-[0.8rem] font-semibold">{item.label}</span>
              </span>
              <span className="mt-0.5 block text-[0.68rem] text-[var(--fg-faint)]">
                {item.structured ? `${item.sections} parts · ` : ""}
                {item.words.toLocaleString()} words
              </span>
              {item.glance && <MiniGlance glance={item.glance} />}
              {item.summary.flags.length > 0 && (
                <span className="mt-1.5 flex flex-wrap gap-1">
                  <SummaryFlags flags={item.summary.flags} />
                </span>
              )}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
