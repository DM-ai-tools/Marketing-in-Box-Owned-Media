import { splitHtmlBlocks } from "./htmlBlocks";
import { withTopicSuggestions } from "./topicSuggestions";

/** Taking a generated asset out of the app — as a file on disk, or through the OS share sheet.
 *
 * An approved asset is a deliverable: it goes to the client, into a CMS, or into a folder next to
 * the other fifteen. Until now the only way out was selecting the rendered Markdown by hand, which
 * loses the fenced blocks and the heading structure. */

export interface AssetExport {
  filename: string;
  mime: string;
  content: string;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/** Build the file for one generation.
 *
 * Markdown, verbatim, in every case but one: a generation that is *nothing but* a full HTML
 * document gets written as `.html`, since wrapping a standalone page in a code fence produces a
 * file no browser will open. Mixed output (a page-design stage's prose plus its ```html block)
 * stays Markdown — that keeps the whole deliverable in one file, and `HtmlPreview` already offers
 * a dedicated `.html` download for the page block on its own. */
export function buildAssetExport(opts: {
  text: string;
  label: string;
  /** Prefixed to the filename so a folder of exports sorts into pipeline order. */
  stageNumber?: number;
  /** Markdown put ahead of the asset in a `.md` export — the topic suggestions it was built on (see
   * `lib/topicSuggestions.ts`). Never added to an `.html` export: a standalone page with Markdown in
   * front of its doctype is a file no browser will open. */
  preamble?: string;
  /** Decides where the preamble goes — see `withTopicSuggestions`. */
  assetId?: string;
}): AssetExport {
  const text = opts.text;
  const segments = splitHtmlBlocks(text);
  const only = segments.length === 1 ? segments[0] : undefined;

  const prefix = typeof opts.stageNumber === "number" ? `${String(opts.stageNumber).padStart(2, "0")}-` : "";
  const base = `${prefix}${slugify(opts.label) || "asset"}`;

  return only?.kind === "html"
    ? { filename: `${base}.html`, mime: "text/html", content: only.html }
    : { filename: `${base}.md`, mime: "text/markdown", content: withTopicSuggestions(text, opts.preamble ?? "", opts.assetId) };
}

export function downloadExport(exported: AssetExport): void {
  const blob = new Blob([exported.content], { type: `${exported.mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = exported.filename;
  a.click();
  // Revoking in the same tick can cancel the download before the browser has read the blob.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export type ShareOutcome = "shared" | "copied" | "cancelled";

/** Hand the asset to the OS share sheet, falling back to the clipboard.
 *
 * `navigator.share` only exists on some platforms, and even where it does it refuses some file
 * types — so the clipboard is the path most desktop runs actually take, and the caller has to
 * report which one happened rather than claiming "Shared" either way.
 *
 * A cancelled share sheet rejects with `AbortError`. That's the user changing their mind, not a
 * failure, so it returns rather than falling through to a clipboard write they didn't ask for. */
export async function shareExport(exported: AssetExport, title: string): Promise<ShareOutcome> {
  const file = new File([exported.content], exported.filename, { type: exported.mime });

  if (typeof navigator.canShare === "function" && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title });
      return "shared";
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return "cancelled";
      // Anything else (a platform that advertises the API but rejects the payload) falls through.
    }
  }

  await navigator.clipboard.writeText(exported.content);
  return "copied";
}
