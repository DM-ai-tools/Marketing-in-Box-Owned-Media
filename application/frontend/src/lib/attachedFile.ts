import type { ExtractedFile } from "../pipeline/pipelineApi";

/** What the clip button accepts. `.doc` is offered so the server can say what to do with it
 * (save as .docx or PDF) rather than the picker hiding the file without a word. */
export const ATTACH_ACCEPT = ".pdf,.docx,.doc,.md,.markdown,.txt";

/** Same cap as the server's `MAX_UPLOAD_BYTES`, checked first so a 40 MB file is never sent. */
export const ATTACH_MAX_BYTES = 10 * 1024 * 1024;

export const ATTACHED_MARKER = "Attached file:";

const num = (n: number) => n.toLocaleString("en-US");

/** The header line naming the file, so the transcript and INPUTS say where the text came from. */
export function attachedHeader(file: ExtractedFile): string {
  const parts = [file.pages != null ? `${file.pages} page${file.pages === 1 ? "" : "s"}` : null, `${num(file.chars)} characters`];
  const cut = file.truncated ? `, first ${num(file.text.length)} kept` : "";
  return `${ATTACHED_MARKER} ${file.filename} (${parts.filter(Boolean).join(", ")}${cut})`;
}

/** The answer box's new value: anything already typed, then the file's header and text. */
export function withAttachedText(current: string, file: ExtractedFile): string {
  const block = `${attachedHeader(file)}\n\n${file.text}`;
  const typed = current.trimEnd();
  return typed ? `${typed}\n\n${block}` : block;
}

/** A local refusal before any upload, or null when the file may be sent. */
export function localRefusal(file: { name: string; size: number }): string | null {
  if (file.size > ATTACH_MAX_BYTES) return "This file is over 10 MB. Upload a smaller one.";
  const ok = ATTACH_ACCEPT.split(",").some((ext) => file.name.toLowerCase().endsWith(ext));
  return ok ? null : "Upload a PDF, Word (.docx), Markdown or text file.";
}
