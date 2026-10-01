/** Remove a generation's echoes of the prompt's own fenced blocks.
 *
 * The backend frames private guidance as `===== BEGIN INDUSTRY_VOICE ===== … ===== END … =====`
 * (likewise the brand tokens and the generated-images list). A master prompt that asks for "the
 * context you used" to be stated first — the Value Ladder's Step 0 — once had the model print that
 * frame into a client deliverable with "(applied — not reproduced here)" between the markers. The
 * block itself now tells the model not to; this is the backstop, so a model that does it anyway
 * still cannot put prompt plumbing in front of a client.
 *
 * Deliberately narrow. Only the block names this backend emits, and only an *acknowledgement* — a
 * pair whose content is a few short lines — is removed; anything longer is real text and stays.
 * Fenced code is never touched. Returns `text` itself when there is nothing to remove, so a clean
 * document is not even copied.
 */

const NAMES = "INDUSTRY_VOICE|BRAND_DESIGN_TOKENS|GENERATED IMAGES|GENERATED DELIVERABLE";
const MARK = String.raw`={3,}\s*(?:BEGIN|END)\s+(?:${NAMES})\s*={3,}`;
const ANY_MARK = new RegExp(MARK, "i");
/** BEGIN … END on one line, with a short acknowledgement between. */
const INLINE_PAIR = new RegExp(String.raw`={3,}\s*BEGIN\s+(${NAMES})\s*={3,}(.{0,160}?)={3,}\s*END\s+\1\s*={3,}`, "gi");
const MARK_LINE = new RegExp(String.raw`^[\s>*_\-|]*${MARK}[\s*_|]*$`, "i");
const BEGIN_LINE = new RegExp(String.raw`^[\s>*_\-|]*={3,}\s*BEGIN\s+(${NAMES})\s*={3,}[\s*_|]*$`, "i");

/** A line left holding nothing but a label once the echo is gone: an empty table row, a bare
 * bullet, "Industry voice:" with no value. */
function isHusk(line: string): boolean {
  const t = line.trim();
  if (!t) return true;
  if (t.startsWith("|")) {
    const cells = t.replace(/^\||\|$/g, "").split("|").map((c) => c.replace(/[*_`\s]/g, ""));
    return cells.slice(1).every((c) => !c);
  }
  return /^([-*+]|\d+[.)])?\s*(\*\*[^*]*\*\*|[^:]{0,40}:)?\s*[-—–:]?\s*$/.test(t);
}

export function stripPromptEchoes(text: string): string {
  if (!text || !ANY_MARK.test(text)) return text;
  const lines = text.split("\n");
  const out: string[] = [];
  let fence: string | null = null;
  // Where an echo was dropped, a blank line that would now double up the one before it goes too.
  let dropped = false;
  const push = (line: string) => {
    if (dropped && !line.trim() && !(out[out.length - 1] ?? "").trim()) return;
    dropped = false;
    out.push(line);
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const open = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (open) {
      fence = fence === null ? open[1][0] : fence === open[1][0] ? null : fence;
      push(line);
      continue;
    }
    if (fence !== null) {
      push(line);
      continue;
    }

    // A BEGIN line whose END follows within a few short lines: the whole pair is an echo.
    const begin = BEGIN_LINE.exec(line);
    if (begin) {
      const endMark = new RegExp(String.raw`={3,}\s*END\s+${begin[1]}\s*={3,}`, "i");
      const end = lines.findIndex((l, j) => j > i && j <= i + 4 && endMark.test(l));
      if (end !== -1 && lines.slice(i + 1, end).join(" ").trim().length <= 200) {
        i = end;
        dropped = true;
        continue;
      }
    }
    if (MARK_LINE.test(line)) {
      dropped = true;
      continue;
    }

    if (ANY_MARK.test(line)) {
      const cleaned = line.replace(INLINE_PAIR, "").replace(new RegExp(MARK, "gi"), "");
      if (isHusk(cleaned)) {
        dropped = true;
        continue;
      }
      push(cleaned.replace(/\s{2,}$/, ""));
      continue;
    }
    push(line);
  }
  return out.join("\n");
}
