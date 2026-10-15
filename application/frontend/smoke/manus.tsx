/* The Pillar Page built by Manus, on the card. Not shipped; see smoke/README.md.
 *
 * Proven here:
 *   - while it builds, the card says Manus is working, with the agent's own words, the elapsed time
 *     and the task link (a build sends no text for minutes, so this is what shows it is not stuck),
 *   - once done it is one line with the credits and the link,
 *   - a fallback says why, and that Claude built the page instead,
 *   - no build, no panel.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ManusPanel } from "../src/pipeline/ReplicaPanel";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const link = "https://manus.im/app/t1";

{
  const html = renderToStaticMarkup(
    <ManusPanel build={{ status: "running", task_url: link, brief: "Writing the hero section", elapsed: 190 }} streaming />,
  );
  ok("building state", html.includes('data-manus-panel="building"'), html);
  ok("it is announced to assistive tech", html.includes('role="status"'));
  ok("the agent's own words", html.includes("Writing the hero section"));
  ok("elapsed time", html.includes("3 min"), html);
  ok("the task link", html.includes(`href="${link}"`));
}

{
  const html = renderToStaticMarkup(<ManusPanel build={{ status: "done", task_url: link, credits: 42 }} streaming={false} />);
  ok("done state", html.includes('data-manus-panel="done"') && html.includes("Page built by Manus · 42 Manus credits"), html);
  ok("the link stays", html.includes(`href="${link}"`));
}

{
  const html = renderToStaticMarkup(
    <ManusPanel
      build={{ status: "fallback", task_url: link, message: "Manus could not build the page: boom Building the page with Claude instead." }}
      streaming
    />,
  );
  ok("fallback state", html.includes('data-manus-panel="fallback"') && html.includes("Manus did not build this page"), html);
  ok("the reason and the fallback are said", html.includes("boom") && html.includes("with Claude instead"));
}

{
  // A card reloaded after the build: no longer streaming, last event was progress, not done.
  const html = renderToStaticMarkup(<ManusPanel build={{ status: "running", task_url: link }} streaming={false} />);
  ok("a finished card never claims to be building", !html.includes('data-manus-panel="building"'), html);
}

ok("no build, no panel", renderToStaticMarkup(<ManusPanel />) === "");

console.log(`manus: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL", f);
if (fails.length) process.exit(1);
