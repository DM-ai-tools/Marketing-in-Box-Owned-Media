import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { embedImages } from "../lib/embedImages";
import { toPreviewDocument } from "../lib/htmlBlocks";

/** Live preview of a generated page (Pillar Page's "PART 3 — FULL DESIGNED PAGE" and friends).
 *
 * Rendered in a sandboxed iframe **without** `allow-same-origin`. That combination puts the
 * generated markup in an opaque origin: its scripts still run, so accordions, tabs and hover
 * states behave as designed, but they cannot reach this app's DOM, storage, or `/api/*` routes.
 * That matters more than it looks — competitor-analysis text scraped from third-party pages flows
 * into these prompts, so the markup is not strictly first-party content.
 *
 * For the same reason there is no "open in a new tab": a `blob:` tab would run the page on this
 * app's own origin, which is exactly the property the sandbox exists to deny. Copy and Download
 * cover that need without it — a downloaded file opens from `file://`, not from here.
 */

const DEVICES = [
  { id: "desktop", label: "Desktop", short: "🖥", width: 1280 },
  { id: "tablet", label: "Tablet", short: "▭", width: 768 },
  { id: "mobile", label: "Mobile", short: "▯", width: 390 },
] as const;

type DeviceId = (typeof DEVICES)[number]["id"];

const SANDBOX = "allow-scripts allow-popups allow-forms allow-modals";

/** The device the preview opens on.
 *
 * Desktop is the right default at a desk — it is the layout the asset is judged on. On a phone it is
 * the wrong one twice over: a 1280px page scaled into a 340px column renders at 0.27, which is
 * illegible, and the operator holding the phone is the reader most likely to be checking the mobile
 * layout anyway. So the initial device is picked from the screen rather than fixed.
 *
 * Read once, not tracked: re-deciding on every resize would yank the device out from under an
 * operator who had deliberately chosen one. */
function initialDevice(): DeviceId {
  if (typeof window === "undefined") return "desktop";
  if (window.innerWidth < 640) return "mobile";
  if (window.innerWidth < 1024) return "tablet";
  return "desktop";
}

/** Viewport height, tracked, for sizing the frame against the screen rather than a fixed 520px —
 * which is taller than the visible area of a landscape phone. */
function useViewportHeight() {
  const [height, setHeight] = useState(() => (typeof window === "undefined" ? 800 : window.innerHeight));

  useEffect(() => {
    const onResize = () => setHeight(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return height;
}

function useMeasuredWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, width] as const;
}

function DeviceToggle({ value, onChange }: { value: DeviceId; onChange: (id: DeviceId) => void }) {
  return (
    <div className="flex items-center gap-0.5 rounded-full border border-[var(--border-strong)] p-0.5">
      {DEVICES.map((d) => (
        <button
          key={d.id}
          type="button"
          onClick={() => onChange(d.id)}
          aria-pressed={value === d.id}
          title={d.label}
          className={`min-h-8 cursor-pointer rounded-full px-2.5 py-0.5 text-[0.7rem] font-medium transition-colors sm:min-h-0 ${
            value === d.id ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "text-[var(--fg-muted)] hover:bg-[var(--hover)]"
          }`}
        >
          {/* Three device names plus four toolbar buttons do not fit a phone's width. The glyphs
              carry the same choice in a third of the space; `title` keeps the wording available. */}
          <span aria-hidden className="@[26rem]:hidden">
            {d.short}
          </span>
          <span className="hidden @[26rem]:inline">{d.label}</span>
          <span className="sr-only">{d.label}</span>
        </button>
      ))}
    </div>
  );
}

function ToolbarButton({
  onClick,
  children,
  active,
}: {
  onClick: () => void;
  children: React.ReactNode;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-h-8 cursor-pointer rounded-full border px-2.5 py-1 text-[0.7rem] font-medium transition-colors sm:min-h-0 ${
        active
          ? "border-[var(--border-strong)] bg-[var(--active)]"
          : "border-[var(--border-strong)] text-[var(--fg-muted)] hover:bg-[var(--hover)]"
      }`}
    >
      {children}
    </button>
  );
}

/** The frame itself, scaled to fit whatever width it has been given.
 *
 * A 1280px page squeezed into the ~780px chat column would trip its own 768px breakpoint and show
 * the tablet layout while claiming to be desktop. So the frame is always laid out at the device's
 * real width and scaled down visually — what you see is the true desktop rendering, just smaller. */
function PreviewFrame({
  document: srcDoc,
  deviceWidth,
  viewportHeight,
  title,
}: {
  document: string;
  deviceWidth: number;
  /** On-screen height of the frame. Held fixed across devices so the card doesn't jump when the
   * width changes; the *logical* height grows as the page is scaled down, so a shrunken desktop
   * view shows proportionally more of the page rather than the same sliver, smaller. */
  viewportHeight: number;
  title: string;
}) {
  const [containerRef, containerWidth] = useMeasuredWidth<HTMLDivElement>();
  const scale = containerWidth ? Math.min(1, containerWidth / deviceWidth) : 1;
  const logicalHeight = viewportHeight / scale;

  return (
    <div ref={containerRef} className="w-full overflow-hidden">
      <div
        className="relative mx-auto overflow-hidden rounded-xl border border-[var(--border)] bg-white"
        style={{ width: deviceWidth * scale, height: viewportHeight }}
      >
        <iframe
          title={title}
          srcDoc={srcDoc}
          sandbox={SANDBOX}
          className="absolute left-0 top-0 border-0"
          style={{
            width: deviceWidth,
            height: logicalHeight,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
        />
      </div>
    </div>
  );
}

/** Copy and Download, shared by the inline preview and the pop-out window.
 *
 * Download is the sanctioned way to run a generated page for real: the file opens from `file://`,
 * which is its own origin, so the page is as isolated on disk as it is in the sandboxed frame. */
function usePreviewActions(html: string, label: string) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(() => {
    void navigator.clipboard.writeText(html).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  }, [html]);

  const download = useCallback(() => {
    // Images written in first, so the file renders on a device that cannot reach their URLs.
    void embedImages(html).then((embedded) => {
      const blob = new Blob([embedded.html], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "page"}.html`;
      a.click();
      // Revoking in the same tick can cancel the download before the browser has read the blob.
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    });
  }, [html, label]);

  return { copy, download, copied };
}

/**
 * A generated page in a pop-up window.
 *
 * A dialog over a dimmed backdrop rather than a full-bleed takeover: a page being reviewed is
 * judged against the app around it, and a window you can dismiss reads as a preview where a
 * whole-screen swap reads as navigation. Portalled to `<body>` because `.msg-rise` leaves a settled
 * `transform` on the message card, and a `fixed` child of a transformed ancestor positions against
 * that ancestor rather than the viewport.
 *
 * **Not** a real browser window, and that is deliberate. `window.open` on a `blob:` URL runs the
 * page on this app's own origin, with this app's cookies and `/api/*` reachable — exactly the
 * property the sandbox on this iframe exists to deny, and it matters because competitor text
 * scraped from third-party pages flows into the prompts that produce this markup. So the "window"
 * is in-app, and Download covers the case where a real one is wanted: a file opened from `file://`
 * is its own origin.
 */
export function HtmlPreviewDialog({
  html,
  label,
  onClose,
}: {
  html: string;
  label: string;
  onClose: () => void;
}) {
  const [device, setDevice] = useState<DeviceId>(initialDevice);
  const [showCode, setShowCode] = useState(false);
  const viewportHeight = useViewportHeight();
  const { copy, download, copied } = usePreviewActions(html, label);

  const srcDoc = toPreviewDocument(html);
  const deviceWidth = DEVICES.find((d) => d.id === device)!.width;
  const lineCount = html.split("\n").length;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while a modal window is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  // Portalled to `<body>` when there is a body to portal to, and rendered in place when there is
  // not. The app is client-only so the portal is what always runs in practice; the fallback keeps
  // the component renderable outside a browser, which is where the checks in `smoke/` run — and a
  // component that renders *nothing* off-browser is harder to trust than one that renders inline.
  const content = (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-2 sm:p-5">
      <button
        type="button"
        aria-label="Close preview"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/55"
      />
      {/* Its own `@container`: the toolbar's breakpoints were written against the transcript
          column, and here the same toolbar spans a window. */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${label} preview`}
        className="@container relative flex h-full max-h-[94vh] w-full max-w-[100rem] flex-col overflow-hidden rounded-xl border border-[var(--border-strong)] bg-[var(--bg)] shadow-[0_24px_64px_rgb(0_0_0/0.4)]"
      >
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-2.5 sm:px-4">
          <span aria-hidden>🖥️</span>
          <span className="min-w-0 flex-1 truncate text-[0.85rem] font-semibold">{label}</span>
          <span className="hidden shrink-0 text-[0.68rem] text-[var(--fg-faint)] @[34rem]:inline">
            {lineCount} lines · {deviceWidth}px
          </span>
          <div className="flex flex-wrap items-center gap-1.5">
            <DeviceToggle value={device} onChange={setDevice} />
            <ToolbarButton onClick={() => setShowCode((v) => !v)} active={showCode}>
              {showCode ? "Hide code" : "Code"}
            </ToolbarButton>
            <ToolbarButton onClick={copy}>{copied ? "Copied" : "Copy HTML"}</ToolbarButton>
            <ToolbarButton onClick={download}>Download</ToolbarButton>
            <ToolbarButton onClick={onClose}>Close</ToolbarButton>
          </div>
        </div>
        <div className="pane-scroll min-h-0 flex-1 overflow-auto p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:p-4">
          {showCode ? (
            <pre className="pane-scroll h-full overflow-auto rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] p-2.5 text-[0.72rem] leading-relaxed sm:p-3">
              <code>{html}</code>
            </pre>
          ) : (
            <PreviewFrame
              document={srcDoc}
              deviceWidth={deviceWidth}
              viewportHeight={Math.max(240, Math.min(viewportHeight * 0.94, viewportHeight) - 120)}
              title={label}
            />
          )}
        </div>
      </div>
    </div>
  );

  return typeof document === "undefined" ? content : createPortal(content, document.body);
}

export function HtmlPreview({ html, label = "Generated page" }: { html: string; label?: string }) {
  const [device, setDevice] = useState<DeviceId>(initialDevice);
  const [showCode, setShowCode] = useState(false);
  const [popped, setPopped] = useState(false);
  const viewportHeight = useViewportHeight();
  const { copy, download, copied } = usePreviewActions(html, label);
  // Inline, the frame takes a little over half the screen: enough to judge the page, little enough
  // that the Save/Refine row under it is still reachable without hunting for it. Floored so a short
  // landscape window still shows something, capped so a tall monitor doesn't hand one preview the
  // whole column.
  const inlineFrameHeight = Math.round(Math.min(520, Math.max(240, viewportHeight * 0.55)));

  const srcDoc = toPreviewDocument(html);
  const deviceWidth = DEVICES.find((d) => d.id === device)!.width;
  const lineCount = html.split("\n").length;

  return (
    <div className="my-3 min-w-0 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] p-2 @[30rem]:p-2.5">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        {/* Deliberately not `flex-1`: content-sized, it lets the wrapping parent push the toolbar
            onto its own line when both cannot share one. Told to grow, it instead holds the toolbar
            beside it and squeezes its own label down to the ellipsis. */}
        <div className="flex min-w-0 items-center gap-1.5">
          <span aria-hidden>🖥️</span>
          <span className="truncate text-[0.8rem] font-semibold">{label}</span>
          {/* First thing to go when the row needs the room — it is context, not a control. */}
          <span className="hidden shrink-0 text-[0.68rem] text-[var(--fg-faint)] @[26rem]:inline">
            {lineCount} lines · {deviceWidth}px
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <DeviceToggle value={device} onChange={setDevice} />
          <ToolbarButton onClick={() => setShowCode((v) => !v)} active={showCode}>
            {showCode ? "Hide code" : "Code"}
          </ToolbarButton>
          <ToolbarButton onClick={copy}>{copied ? "Copied" : "Copy HTML"}</ToolbarButton>
          <ToolbarButton onClick={download}>Download</ToolbarButton>
          <ToolbarButton onClick={() => setPopped(true)}>Pop out</ToolbarButton>
        </div>
      </div>

      {showCode ? (
        <pre className="pane-scroll max-h-[60vh] overflow-auto rounded-xl border border-[var(--border)] bg-[var(--bg)] p-2.5 text-[0.7rem] leading-relaxed sm:max-h-[520px] sm:p-3">
          <code>{html}</code>
        </pre>
      ) : (
        <div className="relative">
          <PreviewFrame document={srcDoc} deviceWidth={deviceWidth} viewportHeight={inlineFrameHeight} title={label} />
          {/* The frame stays interactive — accordions and tabs are part of what is being reviewed —
              so the enlarge affordance is a button over one corner rather than a click handler on
              the frame, which would have to swallow every click meant for the page. */}
          <button
            type="button"
            onClick={() => setPopped(true)}
            title={`Open ${label} in a window`}
            className="absolute right-2 top-2 cursor-pointer rounded-full border border-[var(--border-strong)] bg-[var(--bg)]/90 px-2 py-1 text-[0.68rem] font-semibold text-[var(--fg-muted)] backdrop-blur-sm hover:bg-[var(--bg)] hover:text-[var(--fg)]"
          >
            ⤢ Pop out
          </button>
        </div>
      )}

      {popped && <HtmlPreviewDialog html={html} label={label} onClose={() => setPopped(false)} />}
    </div>
  );
}
