import { useRef, useState } from "react";
import { ATTACH_ACCEPT, localRefusal } from "../lib/attachedFile";
import { extractFileText, type ExtractedFile } from "./pipelineApi";

/** The clip button on a file question. Reads the file's text on the server and hands it to the
 * input box, where the operator sees it before sending; the answer stays text. */
export function AttachFileButton({ onText, onError }: { onText: (file: ExtractedFile) => void; onError: (message: string | null) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState(false);

  async function read(file: File) {
    const refusal = localRefusal(file);
    if (refusal) return onError(refusal);
    onError(null);
    setReading(true);
    try {
      onText(await extractFileText(file));
    } catch (err) {
      onError(err instanceof Error ? err.message : "This file could not be read.");
    } finally {
      setReading(false);
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={ATTACH_ACCEPT}
        className="hidden"
        data-attach-input
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void read(file);
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={reading}
        className="mb-0.5 flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-full text-[var(--fg-muted)] hover:bg-[var(--bg)] hover:text-[var(--fg)] disabled:cursor-wait disabled:opacity-50 sm:h-8 sm:w-8"
        aria-label={reading ? "Reading file…" : "Attach a file (PDF, Word, Markdown or text)"}
        title="Attach a file (PDF, Word, Markdown or text)"
      >
        {reading ? (
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M21 11.5l-8.6 8.6a5.5 5.5 0 01-7.8-7.8l8.6-8.6a3.7 3.7 0 015.2 5.2l-8.6 8.6a1.8 1.8 0 01-2.6-2.6l7.9-7.9"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>
    </>
  );
}
