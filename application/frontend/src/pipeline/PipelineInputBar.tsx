import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { inputPlaceholderFor } from "../data/fieldHints";
import { withAttachedText } from "../lib/attachedFile";
import { AttachFileButton } from "./AttachFileButton";
import { fieldBeingAsked, usePipelineStore } from "./pipelineStore";

/** Free-text answer bar for whichever field the intake flow is currently asking — mirrors
 * `components/InputBar.tsx`'s behavior (Enter to submit, typing "skip" on an optional field
 * skips it), scoped to the pipeline's question-and-answer flow instead of free-form chat. Only
 * rendered while the active field's kind needs typed input (not enum/boolean pill buttons). */
export function PipelineInputBar() {
  const [value, setValue] = useState("");
  const submitFreeform = usePipelineStore((s) => s.submitFreeform);
  // Every field resolves to an example answer (its own, a shared one for facts several assets ask
  // for, or one derived from the upstream output it normally reads) — for fields where the shape of
  // a good answer isn't obvious (Pricing Facts), showing it here puts the example in the box the
  // operator is about to type into, not only in the hint above.
  const fieldPlaceholder = usePipelineStore((s) => inputPlaceholderFor(fieldBeingAsked(s)));
  // File questions get the clip button: the file's text lands in the box, to read before sending.
  const acceptsFile = usePipelineStore((s) => fieldBeingAsked(s)?.kind === "file_attach");
  const [attachError, setAttachError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // When the operator is editing an earlier answer, start from what they said before rather than
  // from an empty box — fixing a typo in a URL shouldn't mean retyping it. Keyed on the field so a
  // seed left over from an abandoned edit can never land in the wrong question.
  const awaitingFieldId = usePipelineStore((s) => s.intake?.awaitingFieldId ?? null);
  const seed = usePipelineStore((s) => (s.editSeed?.fieldId === awaitingFieldId ? s.editSeed.value : null));
  useEffect(() => {
    setValue(seed ?? "");
    setAttachError(null);
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    if (seed) el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [awaitingFieldId, seed]);

  function submit() {
    if (!value.trim()) return;
    submitFreeform(value);
    setValue("");
    setAttachError(null);
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }

  function grow() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  return (
    <div className="border-t border-[var(--border)] bg-[var(--bg)] px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 sm:px-6 lg:px-8">
      {attachError && (
        <p role="alert" className="mx-auto mb-1.5 max-w-[55rem] px-1 text-[0.78rem] text-[var(--color-signal-amber)]">
          {attachError}
        </p>
      )}
      <div className="mx-auto flex max-w-[55rem] items-end gap-2 rounded-2xl border border-[var(--border-strong)] bg-[var(--bg-raised)] px-2.5 py-2 sm:px-3">
        {acceptsFile && (
          <AttachFileButton
            onError={setAttachError}
            onText={(file) => {
              setValue((current) => withAttachedText(current, file));
              requestAnimationFrame(grow);
              textareaRef.current?.focus();
            }}
          />
        )}
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          autoFocus
          placeholder={fieldPlaceholder ?? "Type your answer and press Enter…"}
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          className="min-w-0 flex-1 resize-none bg-transparent py-1.5 text-[1rem] leading-relaxed outline-none placeholder:text-[var(--fg-faint)] sm:text-[0.92rem]"
        />
        <motion.button
          type="button"
          onClick={submit}
          disabled={!value.trim()}
          whileHover={!value.trim() ? undefined : { scale: 1.06 }}
          whileTap={!value.trim() ? undefined : { scale: 0.94 }}
          className="mb-0.5 flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-full text-white disabled:cursor-not-allowed disabled:opacity-30 sm:h-8 sm:w-8"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
          aria-label="Send answer"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
            <path d="M4 12h15M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </motion.button>
      </div>
    </div>
  );
}
