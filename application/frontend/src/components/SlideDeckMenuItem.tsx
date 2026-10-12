import { useEffect, useRef, useState } from "react";
import { fetchSlideDeck, listSlideDecks, type SlideDecksResponse } from "../pipeline/pipelineApi";
import { MENU_ROW, PILL } from "./PlanMapDownload";

/** "Download slide deck (.pptx)" — the webinar stage's Step 5 brief as a file somebody can open.
 *
 * Step 5 writes a slide-by-slide brief "for the designer or presenter", and that is where it used
 * to stop: the operator read a description of fourteen slides and then built those fourteen slides
 * by hand. The backend (`app/services/slide_deck.py`) does the transcription; this is the button.
 *
 * **It asks the server whether there is anything to download, rather than deciding for itself.**
 * A webinar run whose inputs skipped Step 5 has no brief, and a menu row that 422s when pressed is
 * worse than no row. The obvious shortcut — grep the text for the heading here — would put the
 * detection rule in two places, and the one that matters (can it actually be *parsed* into slides)
 * is the server's. So the row renders nothing until the probe comes back with decks. The probe is
 * cheap and happens once per menu-open, because `OverflowMenu` only renders its children while
 * open.
 */
/** The probe's answer per document, so reopening a menu (or opening the reader after the card) does
 * not ask again for a document that has not changed. `null` is "no brief", which is also worth
 * remembering. Bounded, oldest out first. */
const probeCache = new Map<string, SlideDecksResponse | null>();
const PROBE_CACHE_MAX = 24;
const probeKey = (text: string, runId: string | null) => `${runId ?? ""}\u0000${text}`;

/** Ask the server which decks `text` holds, remembering the answer. Exported so the smoke checks
 * can prime it — server rendering never runs the effect that would otherwise call this. */
export async function probeSlideDecks(text: string, runId: string | null): Promise<SlideDecksResponse | null> {
  const key = probeKey(text, runId);
  if (probeCache.has(key)) return probeCache.get(key) ?? null;
  const found = await listSlideDecks(text, runId);
  const usable = found && found.decks.length ? found : null;
  probeCache.set(key, usable);
  if (probeCache.size > PROBE_CACHE_MAX) probeCache.delete(probeCache.keys().next().value as string);
  return usable;
}

export interface DeckRow {
  key: string;
  /** The deck to request; omitted for "all", so the server sends its bundle. */
  index?: number;
  label: string;
  slides: number;
}

/** The rows a probe result is offered as. One deck is one `.pptx` row. Several get a row each —
 * one webinar at a time is the usual need — plus a single "all" row that omits the index and so
 * arrives as the server's `.zip`. */
export function deckRows(decks: SlideDecksResponse): DeckRow[] {
  const total = decks.decks.reduce((sum, deck) => sum + deck.slide_count, 0);
  if (decks.decks.length <= 1) return [{ key: "one", label: "Download slide deck (.pptx)", slides: total }];
  return [
    ...decks.decks.map((deck) => ({
      key: `deck-${deck.index}`,
      index: deck.index,
      label: `Download “${deck.title}” (.pptx)`,
      slides: deck.slide_count,
    })),
    { key: "all", label: "Download all slide decks (.zip)", slides: total },
  ];
}

export function SlideDeckMenuItem({
  text,
  runId,
  onDone,
  variant = "menu",
}: {
  text: string;
  runId: string | null;
  onDone?: () => void;
  /** A row in the card's `OverflowMenu`, or a pill in the reader's export row. */
  variant?: "menu" | "pill";
}) {
  const cached = probeCache.get(probeKey(text, runId));
  const [decks, setDecks] = useState<SlideDecksResponse | null>(cached ?? null);
  const [status, setStatus] = useState<"probing" | "ready" | "none">(
    cached === undefined ? "probing" : cached ? "ready" : "none",
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void probeSlideDecks(text, runId)
      .then((found) => {
        if (cancelled) return;
        // `null` is the server's ordinary "no brief in this document" answer, not a failure.
        if (!found) {
          setStatus("none");
          return;
        }
        setDecks(found);
        setStatus("ready");
      })
      .catch(() => {
        // A probe that failed for any other reason (offline, backend down) hides the row rather
        // than showing one that cannot work. Download and Share below it are unaffected.
        if (!cancelled) setStatus("none");
      });
    return () => {
      cancelled = true;
    };
  }, [text, runId]);

  if (status !== "ready" || !decks) return null;

  const several = decks.decks.length > 1;

  /** `index` names one deck; omitted, the server sends every deck (a `.zip` when there are
   * several). The filename comes back off `Content-Disposition` either way. */
  const run = (key: string, index?: number) => {
    setBusy(key);
    setError(null);
    void fetchSlideDeck(text, runId, index)
      .then(({ blob, filename }) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        // Revoking in the same tick can cancel the download before the browser has read the blob —
        // the same window `exportAsset.downloadExport` leaves.
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
        if (live.current) setBusy(null);
        onDone?.();
      })
      .catch((err: unknown) => {
        if (!live.current) return;
        setBusy(null);
        setError(err instanceof Error ? err.message : "Couldn't build the deck");
      });
  };

  const rows = deckRows(decks);

  return (
    <>
      {rows.map((row) => (
        <button
          key={row.key}
          type="button"
          role={variant === "menu" ? "menuitem" : undefined}
          disabled={busy !== null}
          onClick={() => run(row.key, row.index)}
          title={several && row.index !== undefined ? decks.decks.find((d) => d.index === row.index)?.filename : undefined}
          className={variant === "menu" ? MENU_ROW : PILL}
        >
          <SlidesIcon />
          <span className="min-w-0">
            {busy === row.key ? "Building…" : row.label}
            <span className="ml-1 text-[var(--fg-faint)]">· {row.slides} slides</span>
          </span>
        </button>
      ))}
      {error && (
        <p className="px-2.5 pb-1.5 text-[0.72rem]" style={{ color: "var(--color-signal-orange)" }}>
          {error}
        </p>
      )}
    </>
  );
}

function SlidesIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="4" width="18" height="12" rx="1.6" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 16v4m-3 0h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
