import type { Tone } from "../../lib/visualBlocks";

/* The colours the Visual view encodes state with. One place, so a score bar in a table, the glance
 * strip and a finding card's edge can never disagree about what "good" looks like. Every use sits
 * beside the words that carry the same meaning — colour is never the only signal. */

/** A score's colour from how far along its scale it is. */
export function ratioTone(ratio: number): string {
  if (ratio >= 0.75) return "var(--color-signal-green)";
  if (ratio >= 0.5) return "var(--color-electric-blue)";
  return "var(--color-signal-orange)";
}

/** A labelled status's colour ("Working", "Fix", "Failing"). */
export function toneColor(tone: Tone): string {
  switch (tone) {
    case "good":
      return "var(--color-signal-green)";
    case "warn":
      return "var(--color-signal-amber)";
    case "bad":
      return "var(--color-signal-orange)";
    default:
      return "var(--border-strong)";
  }
}
