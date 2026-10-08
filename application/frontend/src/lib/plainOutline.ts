/** Giving a plain-text deliverable the Markdown its prompt's template only implied.
 *
 * Why this exists
 * ---------------
 * `ICP.md`'s OUTPUT STRUCTURE is written as a plain-text template: "Avatar Name:" on its own line,
 * "1. Identity Snapshot", "Functional Goals | Emotional Goals | …". A run that copies that template
 * literally — a real Phase 2 ICP for ARG Finance did — comes back with no `#`, no `**`, no `-`.
 * `parseAssetDocument` then finds no sections, the reader shows one wall of text, and every line
 * the model meant as its own row (the quotes, the objections, the goals) runs together, because a
 * single newline in Markdown is only a space.
 *
 * The prompt now asks for Markdown. This is for the runs already saved without it: it adds the
 * markup the document's own layout states, at read time, in the same places `stripPromptEchoes`
 * runs. The saved text, the Context Store and the `.md` download keep the stage's words verbatim.
 *
 * What it changes, and only on a document with no Markdown structure at all:
 *   - a run of three or more `N. Title` lines, numbered 1, 2, 3 … and each on its own paragraph,
 *     becomes `## N. Title` — the section boundaries;
 *   - the first line above them becomes the `#` title;
 *   - a `Label:` line over its value becomes `**Label:** value`, and a `Label: value` line gets its
 *     label bolded, one row per paragraph — the definition rows the Visual view draws;
 *   - two or more consecutive plain lines become a list, which is what consecutive lines in a
 *     plain-text document are (the quotes become quote cards).
 *
 * It only ever adds `#`, `**`, `- ` and blank lines, or joins a label to its value with a space, so
 * every word survives; `smoke/plainOutline.tsx` checks that on the real sample. A document that already
 * has any heading, bold or table is returned untouched, so no Markdown output can be rewritten.
 */

const SECTION = /^\s{0,3}(\d{1,2})\.\s+(\S.*?)\s*$/;
const LABEL_ONLY = /^([A-Z][^:.!?"“]{0,48}):\s*$/;
const LABEL_VALUE = /^([A-Z][A-Za-z0-9 /&'’()+-]{0,40}):\s+(\S.*)$/;
const LIST_ITEM = /^\s*(?:[-*+]|\d{1,2}[.)])\s+/;
const BULLET_GLYPH = /^\s*[•·▪◦]\s*/;
const FENCE = /^\s{0,3}(`{3,}|~{3,})/;

/** At least this many numbered sections before the document is treated as an outline. */
const MIN_SECTIONS = 3;
const MAX_TITLE_WORDS = 12;

const wordCount = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;

/** A section title, not a list item or a sentence: short, and not ending like a sentence. */
function isTitle(rest: string): boolean {
  return wordCount(rest) <= MAX_TITLE_WORDS && !/[.?!;:,]$/.test(rest);
}

/** Any Markdown structure at all means the document was written as Markdown; leave it alone. */
function hasMarkdown(lines: string[]): boolean {
  return lines.some((l) => /^\s{0,3}#{1,6}\s/.test(l) || /\*\*\S/.test(l) || /^\s*\|.*\|\s*$/.test(l) || FENCE.test(l));
}

/** Line indices of the numbered section headings, or [] when the document is not an outline. */
function sectionLines(lines: string[]): number[] {
  const found: number[] = [];
  let expected = 1;
  lines.forEach((line, i) => {
    const m = SECTION.exec(line);
    if (!m || Number(m[1]) !== expected || !isTitle(m[2])) return;
    // A heading opens its own paragraph; list items sit on consecutive lines.
    if (i > 0 && lines[i - 1].trim()) return;
    found.push(i);
    expected++;
  });
  return found.length >= MIN_SECTIONS ? found : [];
}

function labelRow(line: string): string | null {
  const m = LABEL_VALUE.exec(line.trim());
  if (!m || wordCount(m[1]) > 5) return null;
  return `**${m[1]}:** ${m[2]}`;
}

/** One paragraph's lines (no blank lines inside), returned as Markdown lines. */
function upgradeParagraph(para: string[]): string[] {
  const out: string[] = [];
  let i = 0;
  // `Avatar Name:` over `Marco Delic — …`: one row.
  while (i < para.length) {
    const label = LABEL_ONLY.exec(para[i].trim());
    if (label && i + 1 < para.length && !LABEL_ONLY.test(para[i + 1].trim())) {
      out.push(`**${label[1]}:** ${para[i + 1].trim()}`);
      i += 2;
      continue;
    }
    out.push(para[i]);
    i++;
  }

  const rows = out.map((l) => (l.startsWith("**") ? l : labelRow(l)));
  if (out.length > 1 && rows.every(Boolean)) {
    // Each row its own paragraph, so the Text view shows rows too, not one run-on line.
    return rows.flatMap((r, k) => (k ? ["", r!] : [r!]));
  }
  if (out.length === 1) return [rows[0] ?? out[0]];

  // Consecutive lines are separate items in plain text; in Markdown they would run together.
  return out.map((l) => {
    if (l.startsWith("**")) return l;
    if (LIST_ITEM.test(l)) return l;
    if (BULLET_GLYPH.test(l)) return l.replace(BULLET_GLYPH, "- ");
    return `- ${l.trim()}`;
  });
}

export function upgradePlainOutline(text: string): string {
  if (!text) return text;
  const lines = text.split("\n");
  if (hasMarkdown(lines)) return text;
  const sections = sectionLines(lines);
  if (!sections.length) return text;
  const isSection = new Set(sections);

  const titleLine = lines.findIndex((l) => l.trim());
  const hasTitle =
    titleLine >= 0 &&
    titleLine < sections[0] &&
    !LABEL_ONLY.test(lines[titleLine].trim()) &&
    !LABEL_VALUE.test(lines[titleLine].trim()) &&
    !/[.?!]$/.test(lines[titleLine].trim()) &&
    !lines[titleLine + 1]?.trim();

  const out: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push(...upgradeParagraph(para));
    para = [];
  };

  lines.forEach((line, i) => {
    if (hasTitle && i === titleLine) {
      out.push(`# ${line.trim()}`);
    } else if (isSection.has(i)) {
      flush();
      out.push(`## ${line.trim()}`, "");
    } else if (!line.trim()) {
      flush();
      out.push(line);
    } else {
      para.push(line);
    }
  });
  flush();
  return out.join("\n");
}
