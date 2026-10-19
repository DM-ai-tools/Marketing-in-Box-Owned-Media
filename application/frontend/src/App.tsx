import { useEffect } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AssetReader } from "./pipeline/AssetReader";
import { ChatDeleteDialog } from "./pipeline/ChatDeleteDialog";
import { GenerationDeleteDialog } from "./pipeline/GenerationDeleteDialog";
import { ChatHistorySidebar } from "./pipeline/ChatHistorySidebar";
import { ProviderNoticeToast } from "./pipeline/ProviderNoticeToast";
import { TimedToast } from "./pipeline/TimedToast";
import { AhrefsActivity } from "./pipeline/AhrefsActivity";
import { FaultDialog } from "./pipeline/FaultDialog";
import { GenerationStream } from "./pipeline/GenerationStream";
import { PipelineDiagram } from "./pipeline/PipelineDiagram";
import { ViewBar } from "./pipeline/ViewBar";
import { TopNav } from "./pipeline/TopNav";
import { UsagePanel } from "./pipeline/UsagePanel";
import { useUiStore } from "./store/uiStore";

/** The chat-history sidebar below `xl`, where there is no room to keep it permanently on screen.
 *
 * Same component as the docked one — a second implementation of the list would be a second thing to
 * keep in step — wrapped in a scrim and slid in from the edge. */
function SidebarDrawer() {
  const sidebarOpen = useUiStore((s) => s.sidebarOpen);
  const closeSidebar = useUiStore((s) => s.closeSidebar);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSidebar();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen, closeSidebar]);

  return (
    <AnimatePresence>
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 flex xl:hidden">
          <motion.button
            type="button"
            aria-label="Close chat history"
            onClick={closeSidebar}
            className="absolute inset-0 cursor-default bg-black/45"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Chat history"
            className="relative h-full max-w-[85vw]"
            initial={reduceMotion ? { opacity: 0 } : { x: "-100%" }}
            animate={reduceMotion ? { opacity: 1 } : { x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { x: "-100%" }}
            transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 34, mass: 0.8 }}
          >
            <ChatHistorySidebar />
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

/** The usage monitor, as an overlay over the working panes.
 *
 * A sheet rather than a route or a third column: spend is something an operator checks between
 * stages and dismisses, and a permanent column would take width from the transcript on every
 * screen to show a figure that matters occasionally. Sized as a right-hand sheet from `sm` up and
 * full-screen below it, where anything narrower cannot hold a row of per-call figures. */
function UsageOverlay() {
  const usageOpen = useUiStore((s) => s.usageOpen);
  const closeUsage = useUiStore((s) => s.closeUsage);
  const reduceMotion = useReducedMotion();

  return (
    <AnimatePresence>
      {usageOpen && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <motion.button
            type="button"
            aria-label="Close usage monitor"
            onClick={closeUsage}
            className="absolute inset-0 cursor-default bg-black/45"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="API usage and cost"
            className="relative h-full w-full @container sm:w-[34rem] sm:max-w-[92vw]"
            initial={reduceMotion ? { opacity: 0 } : { x: "100%" }}
            animate={reduceMotion ? { opacity: 1 } : { x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { x: "100%" }}
            transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 34, mass: 0.8 }}
          >
            <UsagePanel />
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

function App() {
  const mobilePane = useUiStore((s) => s.mobilePane);

  return (
    <div className="flex h-[100dvh] w-full overflow-hidden bg-[var(--bg)] text-[var(--fg)]">
      {/* Mounted at the root so an account-level failure covers the whole app, not one pane. */}
      <FaultDialog />
      {/* Root-mounted for a different reason: the sidebar that raises it is a drawer below `xl`,
          and a transformed ancestor would become the containing block for the fixed overlay. */}
      <ChatDeleteDialog />
      {/* Raised from a generation card's ⋯ menu; root-mounted because the card animates on transform. */}
      <GenerationDeleteDialog />
      {/* Raised from the pipeline store when Ahrefs keyword data could not be fetched. */}
      <ProviderNoticeToast />
      <TimedToast />
      {/* Ahrefs at work: an animated chip while keyword data is fetched, and what it measured. */}
      <AhrefsActivity />

      {/* Docked from `xl` up, where a third column still leaves the transcript a workable width;
          below that the same sidebar is reachable from the nav as a drawer. */}
      <div className="hidden xl:block">
        <ChatHistorySidebar />
      </div>
      <SidebarDrawer />
      <UsageOverlay />
      {/* At the root for the same reason the usage sheet is: it is an overlay over both working
          panes, and mounting it inside the transcript would clip it to that column's width. */}
      <AssetReader />

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ViewBar />
        <TopNav />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
          {/* The chat carries the actual deliverables (a CRO rewrite is a full page of markdown),
              while the diagram is a list of stage rows that gains nothing from extra space — so the
              split favours the chat — and the chat is the one that grows, so once the diagram hits
              its 30rem ceiling the extra width of a wide monitor goes to the deliverables rather
              than to a dead strip at the edge.

              Below `lg` these are two panes of one view, switched from the nav: side by side there is
              no room for, and stacked leaves neither tall enough to read. Both stay mounted and the
              inactive one is hidden rather than unmounted, so switching panes never loses the
              transcript's scroll position or a half-typed answer. */}
          <div
            className={`min-h-0 flex-1 overflow-hidden lg:block lg:min-w-0 ${
              mobilePane === "chat" ? "block" : "hidden"
            }`}
          >
            <GenerationStream />
          </div>

          <div
            className="hidden shrink-0 lg:block lg:w-px"
            style={{ backgroundColor: "color-mix(in srgb, var(--color-electric-blue) 45%, transparent)" }}
          />

          <div
            className={`min-h-0 flex-1 overflow-hidden lg:block lg:w-[36%] lg:min-w-[19rem] lg:max-w-[30rem] lg:flex-none ${
              mobilePane === "pipeline" ? "block" : "hidden"
            }`}
          >
            <PipelineDiagram />
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
