/* Downloaded pages carry their images. Not shipped; see smoke/README.md.
 *
 * A pillar page shared from a local run named `127.0.0.1:8001/media/...` for every image — the
 * recipient's own machine. `lib/embedImages.ts` sends each page to `POST /pipeline/html/standalone`
 * before it is saved. This checks, against a stubbed `fetch`:
 *
 *   - a page-only download is the server's embedded page;
 *   - a document with a ```html page (the report) has that page replaced, and nothing else touched;
 *   - an unreachable backend still downloads the page as it was, and the label says images are linked;
 *   - the label counts images the server could not read.
 */
import { embedImages, embedImagesInDocument, embeddedLabel } from "../src/lib/embedImages";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const PAGE =
  '<!doctype html><html><head><title>p</title></head><body><main><h1>Hi</h1><p>Copy</p>' +
  '<img src="http://127.0.0.1:8001/media/c5b2e15f-c047-4c5d-95bd-4a83e9404db0" alt="hero">' +
  '<img src="https://walled.example/x.png" alt="x"></main></body></html>';
const EMBEDDED = PAGE.replace("http://127.0.0.1:8001/media/c5b2e15f-c047-4c5d-95bd-4a83e9404db0", "data:image/png;base64,AAAA");

const requests: { url: string; html: string }[] = [];
let backendUp = true;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = JSON.parse(String(init?.body ?? "{}")) as { html: string };
  requests.push({ url, html: body.html });
  if (!backendUp) throw new TypeError("Failed to fetch");
  const html = body.html === PAGE ? EMBEDDED : body.html;
  return new Response(
    JSON.stringify({ html, inlined: 1, bytes: 3, failed: [{ url: "https://walled.example/x.png", reason: "403" }] }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}) as typeof fetch;

const page = await embedImages(PAGE);
ok("the page is the server's embedded page", page.html === EMBEDDED);
ok("it is posted to the standalone route", requests[0]?.url === "/api/pipeline/html/standalone");
ok("an unread image is counted", page.embedded && page.missing === 1);
ok("the label says so", embeddedLabel(page) === "Downloaded (1 image linked)");

const document = `# Pillar page\n\nIntro.\n\n\`\`\`html\n${PAGE}\n\`\`\`\n\n## Notes\n\nDone.`;
const report = await embedImagesInDocument(document);
ok("the report's page is embedded in place", report.text === document.replace(PAGE, EMBEDDED), report.text.slice(0, 120));
ok("prose is not sent", requests.every((r) => r.html.startsWith("<!doctype")));

backendUp = false;
const offline = await embedImages(PAGE);
ok("an unreachable backend still gives the page", offline.html === PAGE && !offline.embedded);
ok("and the label says images are linked", embeddedLabel(offline) === "Downloaded (images linked)");
ok("a clean embed says just Downloaded", embeddedLabel({ embedded: true, missing: 0 }) === "Downloaded");

console.log(`embedImages: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
