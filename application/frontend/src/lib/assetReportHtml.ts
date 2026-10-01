import { createElement } from "react";
import { AssetReport } from "../pipeline/visual/AssetReport";
import { slugify, type AssetExport } from "./exportAsset";
import { splitHtmlBlocks } from "./htmlBlocks";
import { stripPromptEchoes } from "./promptEcho";
import { withTopicSuggestions } from "./topicSuggestions";

/** A deliverable as a designed, self-contained HTML file — the reader's Visual view, on its own.
 *
 * The same `AssetReport` component the reader's layout is built from, rendered to static markup, with
 * the app's own compiled stylesheet inlined so every class it uses resolves offline. Nothing is
 * summarised: `smoke/report.tsx` checks every word of every real output is in the file.
 *
 * The Markdown download stays. It is what goes into a CMS or the next tool; this is what goes to a
 * client. `react-dom/server` is loaded on click, so the app's first load does not carry it.
 */

export interface ReportOptions {
  text: string;
  label: string;
  stageNumber?: number;
  preamble?: string;
  appendix?: string;
  assetId?: string;
  client?: string;
  /** The stylesheet to inline. Defaults to the running page's own (`collectPageCss`). */
  css?: string;
  /** Printed in the hero and footer. Defaults to today. */
  date?: string;
}

/** True when the document is nothing but one HTML page — its download is already that page. */
export function isHtmlOnly(text: string): boolean {
  const segments = splitHtmlBlocks(text);
  return segments.length === 1 && segments[0].kind === "html";
}

/** Every rule the running app has loaded, as one stylesheet. A cross-origin sheet (a web font's
 * CSS) cannot be read, so it is referenced instead of inlined. */
export function collectPageCss(): string {
  if (typeof document === "undefined") return "";
  const out: string[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      out.push(Array.from(sheet.cssRules, (rule) => rule.cssText).join("\n"));
    } catch {
      if (sheet.href) out.unshift(`@import url("${sheet.href}");`);
    }
  }
  return out.join("\n");
}

/** What the app's layout rules would get wrong in a plain document: the app pins its root to the
 * viewport and scrolls panes inside it, where a report scrolls the page. */
const REPORT_CSS = `
html, body, #root { height: auto !important; overflow: visible !important; }
body { margin: 0; background: var(--bg); color: var(--fg); font-family: var(--font-sans, Inter, system-ui, sans-serif); }
summary::-webkit-details-marker { display: none; }
@media print {
  body { background: #fff; }
  .report { max-width: none !important; padding: 0 !important; }
  section { break-inside: auto; }
  iframe { display: none; }
}
`;

/** Folded sections are opened before printing, so a printout carries the whole document. */
const PRINT_SCRIPT = `window.addEventListener("beforeprint",function(){document.querySelectorAll("details").forEach(function(d){d.open=true})});`;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export async function buildAssetReportHtml(opts: ReportOptions): Promise<string> {
  const { renderToStaticMarkup } = await import("react-dom/server");
  const text = withTopicSuggestions(stripPromptEchoes(opts.text), opts.preamble ?? "", opts.assetId) + (opts.appendix ?? "");
  const date = opts.date ?? new Date().toLocaleDateString("en-AU", { day: "numeric", month: "long", year: "numeric" });
  const body = renderToStaticMarkup(
    createElement(AssetReport, {
      text,
      assetId: opts.assetId,
      label: opts.label,
      stageNumber: opts.stageNumber,
      client: opts.client,
      date,
    }),
  );
  const title = [opts.label, opts.client].filter(Boolean).join(" — ");
  const css = opts.css ?? collectPageCss();
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>${css}</style>
<style>${REPORT_CSS}</style>
</head>
<body>
${body}
<script>${PRINT_SCRIPT}</script>
</body>
</html>
`;
}

export async function buildAssetReportExport(opts: ReportOptions): Promise<AssetExport> {
  const prefix = typeof opts.stageNumber === "number" ? `${String(opts.stageNumber).padStart(2, "0")}-` : "";
  return {
    filename: `${prefix}${slugify(opts.label) || "asset"}-report.html`,
    mime: "text/html",
    content: await buildAssetReportHtml(opts),
  };
}
