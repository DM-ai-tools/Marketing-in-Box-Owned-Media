import { useMemo } from "react";
import { buildPlanMindMapHtml } from "../lib/planMindMapHtml";

/** "Download plan map (.html)" — the Plan of Action's mind map as a standalone file.
 *
 * Shared by the generation card's menu and the reader's export row, so both offer it under the same
 * rule: **only when a map can actually be built.** `buildPlanMindMapHtml` returns null for a plan
 * whose structure it cannot read, and a row that downloads nothing is worse than no row.
 *
 * The file is self-contained — inline CSS, inline script, the plan's own text embedded — because a
 * plan is the asset most likely to be sent to somebody without a login. See `planMindMapHtml.ts`.
 */
export function PlanMapDownload({
  text,
  label,
  variant,
  onDone,
}: {
  text: string;
  label: string;
  variant: "menu" | "pill";
  onDone?: () => void;
}) {
  const built = useMemo(() => buildPlanMindMapHtml(text, label), [text, label]);
  if (!built) return null;

  // A blob URL rather than a data URI: a full plan's map runs to a few hundred kilobytes, and a
  // data URI that long is refused outright by some browsers and silently truncated by others.
  const download = () => {
    const url = URL.createObjectURL(new Blob([built.html], { type: "text/html;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = built.filename;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    onDone?.();
  };

  return (
    <button
      type="button"
      role={variant === "menu" ? "menuitem" : undefined}
      onClick={download}
      aria-label={variant === "pill" ? `Download ${label} plan map` : undefined}
      className={variant === "menu" ? MENU_ROW : PILL}
    >
      <MapIcon />
      {variant === "menu" ? "Download plan map (.html)" : "Plan map (.html)"}
    </button>
  );
}

/** The two looks a download control takes: a row in an `OverflowMenu`, or a pill beside the
 * reader's Markdown / Report / Share buttons (`AssetExportButtons`' `QuietButton`). */
export const MENU_ROW =
  "flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[0.8rem] font-medium hover:bg-[var(--hover)] disabled:cursor-default disabled:opacity-60";
export const PILL =
  "flex min-h-10 cursor-pointer items-center gap-1.5 rounded-full border border-[var(--border-strong)] px-3 py-1.5 text-[0.78rem] font-medium text-[var(--fg-muted)] hover:bg-[var(--hover)] disabled:cursor-default disabled:opacity-60 sm:min-h-0";

function MapIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M5 6h5M14 6h5M5 12h14M5 18h5M14 18h5M12 6v12"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}
