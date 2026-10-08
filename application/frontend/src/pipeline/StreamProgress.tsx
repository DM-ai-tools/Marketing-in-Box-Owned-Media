import { useEffect, useRef, useState } from "react";
import { Markdown } from "../components/Markdown";
import { TypingIndicator } from "../components/TypingIndicator";
import { scanStreamHeadings } from "../lib/assetDocument";

/**
 * What is happening, while it happens.
 *
 * Before this, a running stage showed a typing indicator and then flooded thousands of words into
 * the transcript, and the operator's only signal of progress was the scrollbar getting smaller.
 * The generation is the longest thing they wait on and it was the least legible.
 *
 * The fix needs no backend change, because the information is already in the stream: every large
 * prompt writes its own `PART n` / `STEP n` headings as it goes, so the step the model is on
 * arrives in the deltas ahead of that step's content. This reads them back out.
 *
 * Two deliberate restraints:
 *
 *   * **No denominator.** It says "Step 4 · Full presenter script", not "Step 4 of 8". The expected
 *     count differs per asset and per run — a webinar set runs Steps 3-8 once per topic — and the
 *     only way to show "of 8" would be to hardcode a table that goes stale the first time a prompt
 *     is edited. A number that is sometimes a lie is worse than no number, so the bar is
 *     indeterminate (`progress-shimmer`) rather than falsely precise.
 *   * **The stream is collapsed, not hidden.** Three lines by default, expandable to the whole
 *     thing. What was on screen is still reachable; it is just no longer the only thing on screen.
 */

/** Re-scan the accumulated text only once every this many new characters.
 *
 * The scan is a line walk over everything received so far, and deltas arrive many times a second,
 * so scanning per delta is quadratic in the length of a document that can reach 100KB. Headings
 * are hundreds of characters apart at minimum, so a 512-character granularity never misses one for
 * longer than it takes the next chunk to arrive. */
const SCAN_EVERY = 512;

/** How much of the tail to look at for the ticker. Three lines never needs more. */
const TAIL_CHARS = 1500;

const TICKER_LINES = 3;

function tailLines(text: string): string[] {
  const tail = text.length > TAIL_CHARS ? text.slice(-TAIL_CHARS) : text;
  const lines = tail
    .split("\n")
    .map((l) => l.trim())
    // Fence markers and table rules are noise in a three-line window.
    .filter((l) => l.length > 0 && !/^(`{3,}|~{3,}|\|?[\s|:-]+\|?)$/.test(l))
    .map((l) => l.replace(/^#+\s*/, "").replace(/\*\*/g, ""));
  return lines.slice(-TICKER_LINES);
}

export function StreamProgress({
  text,
  label,
  /** Set while a continuation is appending to a draft that was cut off at the output ceiling. Only
   * changes the wording: the operator is watching the *end* of a document they have already been
   * reading, and "Starting CRO — Page Rewrite…" over it reads as the stage having restarted and
   * thrown their draft away. */
  continuing,
  /** A stream that produced nothing is being restarted — see `streamWithRetry` in `pipelineApi.ts`.
   * Named plainly, because a silent 3-second gap before a retry looks like the app has hung. */
  reconnecting,
}: {
  text: string;
  label: string;
  continuing?: boolean;
  reconnecting?: { attempt: number; of: number };
}) {
  const [steps, setSteps] = useState<string[]>([]);
  const [expanded, setExpanded] = useState(false);
  const bucket = useRef(-1);

  useEffect(() => {
    const next = Math.floor(text.length / SCAN_EVERY);
    if (next === bucket.current) return;
    bucket.current = next;
    setSteps(scanStreamHeadings(text).labels);
  }, [text]);

  const lines = tailLines(text);
  const current = steps.length ? steps[steps.length - 1] : null;
  const done = steps.slice(0, -1);

  return (
    <div>
      {/* The headline: which step, named. Falls back to an honest "starting" state, because before
          the first heading arrives there is genuinely nothing to report but that it began. */}
      <div className="flex items-center gap-2 text-[0.78rem]">
        {current ? (
          <>
            <span className="font-semibold" style={{ color: "var(--color-electric-blue)" }}>
              Step {steps.length}
            </span>
            <span className="min-w-0 flex-1 truncate font-medium">{current}</span>
          </>
        ) : (
          <>
            <TypingIndicator />
            <span className="text-[var(--fg-muted)]">
              {continuing ? `Finishing ${label} — picking up where it stopped…` : `Starting ${label}…`}
            </span>
          </>
        )}
      </div>

      {reconnecting && (
        <div
          className="mt-1.5 rounded-lg border px-2.5 py-1.5 text-[0.72rem]"
          style={{ borderColor: "var(--color-signal-orange)" }}
          aria-live="polite"
        >
          <span style={{ color: "var(--color-signal-orange)" }}>Reconnecting</span>
          <span className="text-[var(--fg-muted)]">
            {" "}
            — the connection dropped before anything was generated. Attempt {reconnecting.attempt} of{" "}
            {reconnecting.of}.
          </span>
        </div>
      )}

      <div className="progress-shimmer relative mt-1.5 h-[3px] overflow-hidden rounded-full bg-[var(--bg-sunken)]">
        <span
          className="block h-full rounded-full"
          style={{ width: "38%", backgroundColor: "var(--color-electric-blue)" }}
        />
      </div>

      {/* The steps already finished. Named, so the operator can see the shape of what has been
          produced before any of it is readable. */}
      {done.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {done.map((step, i) => (
            <li key={`${i}-${step}`} className="flex items-center gap-1.5 text-[0.74rem] text-[var(--fg-muted)]">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0" style={{ color: "var(--color-signal-green)" }}>
                <path d="M5 12l5 5L20 7" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="min-w-0 truncate">{step}</span>
            </li>
          ))}
        </ul>
      )}

      {/* The stream itself. Collapsed to a monospace three-line window: enough to see that real
          words are arriving and roughly what about, without the card growing to ten thousand. */}
      {expanded ? (
        <div className="pane-scroll mt-2 max-h-[45vh] overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-2">
          <Markdown text={text} />
          <span className="stream-cursor text-[var(--fg)]">▍</span>
        </div>
      ) : (
        <div
          aria-live="polite"
          className="mt-2 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5 font-mono text-[0.68rem] leading-relaxed text-[var(--fg-muted)]"
        >
          {lines.length === 0 ? (
            <span className="text-[var(--fg-faint)]">waiting for the first tokens…</span>
          ) : (
            lines.map((line, i) => (
              <div key={i} className={i === lines.length - 1 ? "truncate text-[var(--fg)]" : "truncate"}>
                {line}
                {i === lines.length - 1 && <span className="stream-cursor">▍</span>}
              </div>
            ))
          )}
        </div>
      )}

      <div className="mt-1.5 flex items-center gap-2 text-[0.7rem] text-[var(--fg-faint)]">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="cursor-pointer rounded-full border border-[var(--border)] px-2 py-[2px] font-medium text-[var(--fg-muted)] hover:bg-[var(--hover)]"
        >
          {expanded ? "Collapse stream" : "Show full stream"}
        </button>
        <span className="tabular-nums">{text.trim() ? `${text.trim().split(/\s+/).length.toLocaleString()} words` : ""}</span>
      </div>
    </div>
  );
}
