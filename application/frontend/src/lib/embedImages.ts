import { makeStandaloneHtml } from "../pipeline/pipelineApi";
import { splitHtmlBlocks } from "./htmlBlocks";

/**
 * Images written into a downloaded page, so the file renders on any device.
 *
 * A generated page names its images by URL: the run's `/media/{id}` images (on `127.0.0.1:8001`
 * when the run was local, which is the recipient's own machine), and sometimes the client's own
 * site, which may refuse them to anyone else. Opened where it was made, the file looks fine;
 * shared, it shows broken images. Every HTML download goes through the backend's
 * `POST /pipeline/html/standalone`, which writes each image in as a `data:` URI.
 *
 * Never withholds the file: if the backend cannot be reached, the page downloads as it is, and
 * `embedded` says so for the caller's label.
 */

export interface Embedded {
  html: string;
  /** False when the images could not be written in, so the file still links them. */
  embedded: boolean;
  /** Images left as URLs, because they could not be read. */
  missing: number;
}

export async function embedImages(html: string): Promise<Embedded> {
  try {
    const result = await makeStandaloneHtml(html);
    return { html: result.html, embedded: true, missing: result.failed.length };
  } catch {
    return { html, embedded: false, missing: 0 };
  }
}

/** Every ```html page inside a document, embedded. For the report, whose pages are shown in
 * `<iframe srcdoc>` and would otherwise carry the same dead links. */
export async function embedImagesInDocument(text: string): Promise<{ text: string; embedded: boolean; missing: number }> {
  const pages = splitHtmlBlocks(text).flatMap((segment) => (segment.kind === "html" ? [segment.html] : []));
  let out = text;
  let embedded = true;
  let missing = 0;
  for (const page of pages) {
    const result = await embedImages(page);
    out = out.replace(page, () => result.html);
    embedded &&= result.embedded;
    missing += result.missing;
  }
  return { text: out, embedded, missing };
}

/** The label a download button flashes once the file is saved. */
export function embeddedLabel(result: { embedded: boolean; missing: number }): string {
  if (!result.embedded) return "Downloaded (images linked)";
  return result.missing ? `Downloaded (${result.missing} image${result.missing === 1 ? "" : "s"} linked)` : "Downloaded";
}
