/* Builds and runs both render checks. See smoke/README.md.
 *
 * Vite's JS API rather than its binary, and `process.execPath` rather than a bare `node`, so this
 * works the same from cmd.exe, PowerShell and a POSIX shell — the repo is developed on Windows and
 * `node_modules/.bin/vite` is not a thing cmd.exe can run. */
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { build } from "vite";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const entries = ["smoke.tsx", "card.tsx", "shell.tsx", "plan.tsx", "gate.tsx", "stop.tsx", "clear.tsx", "phase2cro.tsx", "competitors.tsx", "topics.tsx", "industry.tsx", "phase2icp.tsx", "check.tsx", "visual.tsx", "sources.tsx", "report.tsx", "businessCheck.tsx", "services.tsx"];

let failed = false;

for (const entry of entries) {
  const name = entry.replace(".tsx", "");
  const outDir = `smoke/out-${name}`;

  console.log(`\n--- smoke/${entry}`);
  try {
    await build({
      root,
      logLevel: "warn",
      build: {
        ssr: path.join("smoke", entry),
        outDir,
        emptyOutDir: true,
        // Nothing here ships, so a minified bundle only makes a stack trace harder to read.
        minify: false,
      },
    });
    execFileSync(process.execPath, [path.join(root, outDir, `${name}.js`)], { stdio: "inherit" });
  } catch {
    failed = true;
  }
}

process.exit(failed ? 1 : 0);
