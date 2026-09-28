import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AssetExportButtons } from "../components/AssetExportButtons";
import { SectionBody } from "../components/AssetDocumentView";
import { Markdown } from "../components/Markdown";
import { OverflowItem, OverflowMenu } from "../components/OverflowMenu";
import { parseAssetDocument } from "../lib/assetDocument";
import { topicPreambleFor, withTopicSuggestions } from "../lib/topicSuggestions";
import { useUiStore } from "../store/uiStore";
import { stagesFor } from "./pipelineData";
import { usePipelineStore } from "./pipelineStore";

/**
 * The reading surface, as a right-hand sheet over the working panes.
 *
 * The transcript is for *flow* — questions, decisions, what happened next. A six-thousand-word
 * deliverable is for *reading*, and making one column do both is what made the app feel like a
 * wall of text: to reach the implementation pack you scrolled through the whole rewritten page,
 * and doing so moved you away from the stage you were reviewing.
 *
 * So the document gets its own scroll container and its own outline, and opening Part 3 no longer
 * moves the transcript behind it. A sheet rather than a route, for the reason `UsageOverlay` is
 * one: this is something you open, read and dismiss, and a route would lose the transcript's
 * position on the way back.
 *
 * The whole document is in the DOM — every section rendered, nothing lazily mounted — which keeps
 * the browser's own Ctrl+F working across the entire deliverable. The search box here narrows the
 * *outline*; it deliberately does not try to highlight inside rendered Markdown, which would mean
 * walking and rewriting React's output.
 */
export function AssetReader() {
  const readerMessageId = useUiStore((s) => s.readerMessageId);
  const closeReader = useUiStore((s) => s.closeReader);
  const messages = usePipelineStore((s) => s.messages);
  const phase = usePipelineStore((s) => s.phase);
  const reduceMotion = useReducedMotion();

  const message = readerMessageId ? messages.find((m) => m.id === readerMessageId) : undefined;

  // A message can vanish under the reader — the chat was switched, or a re-run replaced the draft.
  // Closing is the honest response; leaving an empty sheet open is not.
  useEffect(() => {
    if (readerMessageId && !message) closeReader();
  }, [readerMessageId, message, closeReader]);

  useEffect(() => {
    if (!readerMessageId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeReader();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readerMessageId, closeReader]);

  return (
    <AnimatePresence>
      {readerMessageId && message && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <motion.button
            type="button"
            aria-label="Close document"
            onClick={closeReader}
            className="absolute inset-0 cursor-default bg-black/45"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Generated document"
            className="relative flex h-full w-full flex-col bg-[var(--bg)] sm:w-[min(94vw,76rem)] sm:border-l sm:border-[var(--border-strong)]"
            initial={reduceMotion ? { opacity: 0 } : { x: "100%" }}
            animate={reduceMotion ? { opacity: 1 } : { x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { x: "100%" }}
            transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 34, mass: 0.8 }}
          >
            <ReaderContents
              text={message.text ?? ""}
              assetId={message.assetId}
              phase={phase}
              // The topics this asset was built on and the ones passed over — shown in the reader
              // (and so from Deliverables) and carried into the exported file.
              preamble={topicPreambleFor(messages, message)}
              onClose={closeReader}
            />
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

function ReaderContents({
  text,
  assetId,
  phase,
  preamble,
  onClose,
}: {
  text: string;
  assetId?: string;
  phase: ReturnType<typeof usePipelineStore.getState>["phase"];
  preamble: string;
  onClose: () => void;
}) {
  const stage = stagesFor(phase).find((s) => s.asset.asset_id === assetId);
  const label = stage?.asset.label ?? "Generated asset";
  // What the reader shows: the stage's own text with the topic suggestions placed in it (before the
  // lead magnet's PART 2 scorecard, at the top elsewhere). Display only — the saved text, which the
  // next stage reads, is untouched, and the export places the section the same way.
  const shown = useMemo(() => withTopicSuggestions(text, preamble, assetId), [text, preamble, assetId]);
  const doc = useMemo(() => parseAssetDocument(shown), [shown]);

  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState<string | null>(doc.sections[0]?.id ?? null);
  const scroller = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef(new Map<string, HTMLElement>());

  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!needle) return null;
    const out = new Map<string, number>();
    for (const section of doc.sections) {
      const haystack = `${section.label}\n${section.body}`.toLowerCase();
      let n = 0;
      let at = haystack.indexOf(needle);
      while (at !== -1) {
        n++;
        at = haystack.indexOf(needle, at + needle.length);
      }
      if (n > 0) out.set(section.id, n);
    }
    return out;
  }, [needle, doc.sections]);

  // Scroll-spy: whichever section's heading is nearest the top of the viewport is the active one.
  useEffect(() => {
    const root = scroller.current;
    if (!root || !doc.sections.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]?.target.id) setActiveId(visible[0].target.id);
      },
      // A band across the upper third: a section counts as "current" once its heading reaches the
      // top area, which is where a reader's eye is, rather than when it is centred.
      { root, rootMargin: "0px 0px -66% 0px", threshold: 0 },
    );

    sectionRefs.current.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [doc.sections]);

  const jump = (id: string) => {
    setActiveId(id);
    sectionRefs.current.get(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <>
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-2.5 sm:px-4">
        {stage && <span aria-hidden>{stage.emoji}</span>}
        <span className="text-[0.9rem] font-semibold">{label}</span>
        {stage && (
          <span className="rounded-full border border-[var(--border-strong)] px-1.5 py-[1px] text-[0.62rem] font-semibold text-[var(--fg-muted)]">
            Stage {String(stage.stageNumber).padStart(2, "0")}
          </span>
        )}
        <span className="flex-1" />

        {/* On a phone the outline rail has nowhere to go, so the contents become a menu. */}
        {doc.structured && (
          <span className="md:hidden">
            <OverflowMenu label="Contents">
              {(close) =>
                doc.sections.map((section) => (
                  <OverflowItem
                    key={section.id}
                    onClick={() => {
                      jump(section.id);
                      close();
                    }}
                  >
                    <span className="truncate">{section.label}</span>
                  </OverflowItem>
                ))
              }
            </OverflowMenu>
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3 py-1.5 text-[0.78rem] font-semibold hover:bg-[var(--hover)] sm:min-h-0"
        >
          Close
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        {doc.structured && (
          <nav className="hidden w-[15rem] shrink-0 overflow-y-auto border-r border-[var(--border)] bg-[var(--bg-sunken)] px-2 py-3 md:block">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find in document…"
              aria-label="Find in document"
              className="mb-2 w-full rounded-full border border-[var(--border-strong)] bg-[var(--bg)] px-2.5 py-1 text-[0.75rem] outline-none focus:border-[var(--color-electric-blue)]"
            />
            {matches && matches.size === 0 && (
              <p className="px-2 py-1 text-[0.72rem] text-[var(--fg-faint)]">No section contains that.</p>
            )}
            {doc.sections.map((section) => {
              const hits = matches?.get(section.id);
              if (matches && !hits) return null;
              const active = activeId === section.id;
              return (
                <div key={section.id}>
                  <button
                    type="button"
                    onClick={() => jump(section.id)}
                    className={`block w-full cursor-pointer rounded-lg border-l-2 px-2 py-1.5 text-left text-[0.76rem] leading-tight transition-colors ${
                      active
                        ? "border-l-[var(--accent)] bg-[var(--bg-raised)] font-semibold text-[var(--fg)]"
                        : "border-l-transparent text-[var(--fg-muted)] hover:bg-[var(--hover)] hover:text-[var(--fg)]"
                    }`}
                  >
                    <span className="block">{section.label}</span>
                    <span className="mt-[1px] block text-[0.66rem] font-normal text-[var(--fg-faint)]">
                      {hits ? `${hits} match${hits === 1 ? "" : "es"}` : `${section.words.toLocaleString()} words`}
                    </span>
                  </button>
                  {/* Sub-headings only under the section being read, so the rail stays a map rather
                      than becoming the document's table of contents in full. */}
                  {active && !matches && section.subheads.length > 1 && (
                    <ul className="mb-1 ml-3 border-l border-[var(--border)] pl-2">
                      {section.subheads.map((sub) => (
                        <li
                          key={sub.id}
                          className="truncate py-[2px] text-[0.7rem] text-[var(--fg-faint)]"
                          title={sub.label}
                        >
                          {sub.label}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </nav>
        )}

        <div ref={scroller} className="pane-scroll min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6"
          /* The surface behind any table in here, for `.md-scroll`'s scroll shadows. */
          style={{ "--md-cover": "var(--bg)" } as React.CSSProperties}>
          <div className="doc-measure mx-auto w-full">
            {doc.structured ? (
              doc.sections.map((section, i) => (
                <section
                  key={section.id}
                  id={section.id}
                  ref={(node) => {
                    if (node) sectionRefs.current.set(section.id, node);
                    else sectionRefs.current.delete(section.id);
                  }}
                  className={`scroll-mt-3 ${i > 0 ? "mt-7 border-t border-[var(--border)] pt-6" : ""}`}
                >
                  <h2 className="mb-2 text-balance text-[1.02rem] font-semibold">{section.label}</h2>
                  <SectionBody body={section.body} label={`${label} — ${section.label}`} />
                </section>
              ))
            ) : (
              /* No usable structure — the document renders exactly as the card always rendered it. */
              <Markdown text={shown} />
            )}
            <div className="mt-8 border-t border-[var(--border)] pt-4">
              <AssetExportButtons text={text} label={label} stageNumber={stage?.stageNumber} preamble={preamble} assetId={assetId} />
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
