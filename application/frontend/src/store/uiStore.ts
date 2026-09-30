import { create } from "zustand";
import { readFlag, readPref, writePref } from "../lib/persisted";

/** Which of the two work surfaces the single-column layout is showing.
 *
 * Below `lg` the transcript and the pipeline diagram cannot both be on screen and still be usable —
 * splitting a 700px-tall phone in half leaves the transcript four lines deep. So on small screens
 * they become two panes of one view and this is which one is front. From `lg` up both are mounted
 * side by side and this value is ignored. */
export type MobilePane = "chat" | "pipeline";

/** How a generated document is presented on its card.
 *
 * `outline` is the default and the reason the transcript stopped being a wall of text: the
 * document's own declared sections as closed rows. `full` is the rendering the app had before that
 * — the entire document inline, always open — kept as a real choice rather than as a fallback,
 * because an operator proof-reading a finished asset end to end wants exactly that, and taking it
 * away would be replacing one imposed reading mode with another. */
export type DocView = "outline" | "full";

/** How the reader draws a document: its structure as visuals (scores, timelines, cards, flows), or
 * the text exactly as exported. A preference, remembered like `docView`. */
export type ReaderView = "visual" | "text";

/** Which transcript is on screen.
 *
 * `deliverables` is a grid of the assets this chat has approved. It reads the same messages the
 * transcript does rather than fetching anything, so it can never disagree with what is on the
 * cards. */
export type WorkTab = "transcript" | "deliverables";

interface UiState {
  /** The chat-history drawer. Only ever open below `lg`; from `lg` up the sidebar is permanent. */
  sidebarOpen: boolean;
  mobilePane: MobilePane;
  /** The API usage monitor. An overlay rather than a third pane: it is something you check, not
   * something you work alongside, and a permanent column would cost the transcript width on every
   * screen to show a number that only matters occasionally. */
  usageOpen: boolean;
  /**
   * The generation whose full document is open in the reader, or null.
   *
   * An id rather than the text: the reader has to keep showing the *current* draft of that message
   * while it is refined or re-run, and a copy taken at open time would silently go stale behind a
   * regeneration. Keeping the id means the reader re-reads from `pipelineStore` like every other
   * view of a message does, and a message that disappears from the transcript closes it.
   *
   * Here rather than in `pipelineStore` because it is layout, not run state: nothing about which
   * pane is open belongs in a chat's persisted history.
   */
  readerMessageId: string | null;
  /** Outline or full document, on every card. Remembered across sessions. */
  docView: DocView;
  /** Short in-product explanations of what each part of a card is. Remembered across sessions.
   *
   * On by default for the first run and switched off from the view bar. The elements it explains
   * are new and several of them are not self-evident — that a locked slot count means the client's
   * real details are being held, or that the amber number is every placeholder waiting on the
   * client. */
  hintsOn: boolean;
  workTab: WorkTab;
  /** Replaces the transcript with a self-contained sample one.
   *
   * Deliberately *not* remembered: it is a look at the interface, not a way of working, and a
   * reload landing back in it would be indistinguishable from the real transcript having lost its
   * contents. See `DemoTranscript` for why it reads fixtures instead of the store. */
  demoMode: boolean;
  /** The chat the delete confirmation is currently asking about, or null.
   *
   * Here rather than as component state in `ChatHistorySidebar` for the same reason `AssetReader`
   * is mounted at the root: below `xl` that sidebar is a drawer, and the drawer animates on
   * `transform` — which makes it the containing block for anything `position: fixed` inside it, so
   * a warning rendered there would be laid out inside a 85vw-wide panel instead of over the app.
   * The sidebar raises the question, `ChatDeleteDialog` at the root asks it. */
  pendingChatDelete: { id: string; title: string } | null;
  /** The reader's view: visuals by default, the exported text one click away. */
  readerView: ReaderView;
  setReaderView: (view: ReaderView) => void;
  openSidebar: () => void;
  closeSidebar: () => void;
  toggleSidebar: () => void;
  setMobilePane: (pane: MobilePane) => void;
  openUsage: () => void;
  closeUsage: () => void;
  openReader: (messageId: string) => void;
  closeReader: () => void;
  setDocView: (view: DocView) => void;
  toggleDocView: () => void;
  setHints: (on: boolean) => void;
  toggleHints: () => void;
  setWorkTab: (tab: WorkTab) => void;
  toggleDemoMode: () => void;
  askDeleteChat: (target: { id: string; title: string }) => void;
  cancelDeleteChat: () => void;
}

/**
 * Layout state for the responsive shell, kept out of `pipelineStore` on purpose: nothing here is
 * part of a run, none of it is persisted with a chat, and the components that read it
 * (`TopNav`, `ChatHistorySidebar`) are siblings of the pane they affect rather than children — so a
 * store beats threading props through `App`.
 */
export const useUiStore = create<UiState>((set) => ({
  sidebarOpen: false,
  mobilePane: "chat",
  usageOpen: false,
  readerMessageId: null,
  docView: readPref<DocView>("docView", ["outline", "full"], "outline"),
  readerView: readPref<ReaderView>("readerView", ["visual", "text"], "visual"),
  hintsOn: readFlag("hints", true),
  workTab: "transcript",
  demoMode: false,
  pendingChatDelete: null,
  openSidebar: () => set({ sidebarOpen: true }),
  closeSidebar: () => set({ sidebarOpen: false }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setMobilePane: (mobilePane) => set({ mobilePane }),
  // Closes the history drawer with it: below `xl` both are overlays, and leaving the drawer open
  // underneath would put the monitor's own scroll container behind a scrim.
  openUsage: () => set({ usageOpen: true, sidebarOpen: false }),
  closeUsage: () => set({ usageOpen: false }),
  // Same reasoning as `openUsage`, plus: the reader and the usage monitor are both right-hand
  // sheets, so two open at once would stack one scroll container on another.
  openReader: (messageId) => set({ readerMessageId: messageId, usageOpen: false, sidebarOpen: false }),
  closeReader: () => set({ readerMessageId: null }),
  setReaderView: (readerView) => {
    writePref("readerView", readerView);
    set({ readerView });
  },
  setDocView: (docView) => {
    writePref("docView", docView);
    set({ docView });
  },
  toggleDocView: () => set((s) => {
    const docView: DocView = s.docView === "outline" ? "full" : "outline";
    writePref("docView", docView);
    return { docView };
  }),
  setHints: (hintsOn) => {
    writePref("hints", hintsOn);
    set({ hintsOn });
  },
  toggleHints: () => set((s) => {
    writePref("hints", !s.hintsOn);
    return { hintsOn: !s.hintsOn };
  }),
  setWorkTab: (workTab) => set({ workTab }),
  // Closes the reader with it: the reader is pointed at a real message id, and a sample transcript
  // behind an open sheet showing a real document reads as the two being related.
  toggleDemoMode: () => set((s) => ({ demoMode: !s.demoMode, readerMessageId: null })),
  askDeleteChat: (pendingChatDelete) => set({ pendingChatDelete }),
  cancelDeleteChat: () => set({ pendingChatDelete: null }),
}));
