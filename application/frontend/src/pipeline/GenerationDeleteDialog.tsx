import { useEffect } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUiStore } from "../store/uiStore";
import { selectDeleteGeneration, usePipelineStore } from "./pipelineStore";

/** The warning in front of deleting one generated asset card from the transcript.
 *
 * Mounted at the app root like `ChatDeleteDialog`, for the same reason: the card that raises it
 * animates in on `transform`, and a transformed ancestor becomes the containing block for the fixed
 * overlay. The card's `⋯` menu sets `pendingGenerationDelete`; this asks about it.
 *
 * A modal because nothing in this app has an undo, and the row sits one tap from Download.
 */
export function GenerationDeleteDialog() {
  const target = useUiStore((s) => s.pendingGenerationDelete);
  const cancel = useUiStore((s) => s.cancelDeleteGeneration);
  const deleteGeneration = usePipelineStore((s) => s.deleteGeneration);
  const allowed = usePipelineStore((s) => (target ? selectDeleteGeneration(s, target.messageId).ok : false));

  // The card can stop being deletable under the question — it started saving, or a chat switch
  // took it away. Asking about it then would delete nothing and say nothing.
  useEffect(() => {
    if (target && !allowed) cancel();
  }, [target, allowed, cancel]);

  return (
    <ConfirmDialog
      open={target !== null && allowed}
      title={`Delete this ${target?.label ?? "asset"} card?`}
      message={
        target?.approved
          ? "This approved version is removed from the chat. A newer approved version of the stage stays, and that is the one later stages use."
          : "This draft is removed from the chat, with its business check. If it came from a Refine, the note that asked for it goes too, and the draft it was refined from is open for review again."
      }
      detail="Approved versions in the Context Store are not deleted, and downloads you already saved are unaffected."
      confirmLabel="Delete"
      cancelLabel="Keep it"
      footnote="This cannot be undone."
      onCancel={cancel}
      onConfirm={() => {
        cancel();
        if (target) deleteGeneration(target.messageId);
      }}
    />
  );
}
