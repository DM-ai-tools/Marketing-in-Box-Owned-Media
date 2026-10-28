import type { ReactNode } from "react";
import type { ScannedKeyword } from "./pipelineApi";

const num = (value: number | null | undefined) => (typeof value === "number" ? value.toLocaleString("en-US") : "—");

/** "N ranking keywords", with every keyword that was scanned for the domain shown on hover.
 *
 * The count is DataForSEO's total for the domain; the scan reads the top `limit` of those by
 * estimated traffic, so the popover says how many of the total it lists rather than implying it is
 * all of them. A popover the pointer can move into, not a native `title`: fifty rows need to
 * scroll. Opens on hover and on keyboard focus (`focus-within`), and renders the trigger alone when
 * nothing was scanned (a report scored before the list was kept). */
export function ScannedKeywords({
  keywords,
  total,
  limit,
  children,
  align = "left",
}: {
  keywords: ScannedKeyword[] | undefined;
  total: number | null;
  limit?: number;
  children: ReactNode;
  align?: "left" | "right";
}) {
  if (!keywords?.length) return <>{children}</>;
  const shown = keywords.length;
  const ofTotal = total != null && total > shown ? ` of ${num(total)}` : "";
  return (
    <span className="group relative inline-block">
      <span
        tabIndex={0}
        className="cursor-help underline decoration-dotted underline-offset-2 outline-none focus-visible:rounded focus-visible:ring-1 focus-visible:ring-[var(--color-electric-blue)]"
        aria-label={`${shown} scanned keywords — show the list`}
      >
        {children}
      </span>
      <span
        role="tooltip"
        data-scanned-keywords
        className={`invisible absolute top-full z-30 pt-1 opacity-0 transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100 ${
          align === "right" ? "right-0" : "left-0"
        }`}
      >
        <span className="block w-[min(22rem,calc(100vw-2.5rem))] rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] px-2.5 py-2 text-left shadow-sm">
          <span className="block text-[0.66rem] font-semibold text-[var(--fg)]">
            {shown} keywords scanned{ofTotal}
          </span>
          <span className="block text-[0.62rem] text-[var(--fg-faint)]">
            {limit ? `The top ${limit} by estimated traffic, ` : ""}from DataForSEO. Position in Google · searches/mo.
          </span>
          <span className="mt-1.5 block max-h-64 overflow-y-auto pr-1">
            <table className="w-full text-[0.7rem]">
              <tbody>
                {keywords.map((k) => (
                  <tr key={`${k.keyword}|${k.url ?? ""}`} className="border-t border-[var(--border)] align-top">
                    <td className="py-0.5 pr-2 text-[var(--fg)]">{k.keyword}</td>
                    <td className="whitespace-nowrap py-0.5 pr-2 text-right tabular-nums text-[var(--fg-muted)]">
                      {k.position != null ? `#${k.position}` : "—"}
                    </td>
                    <td className="whitespace-nowrap py-0.5 text-right tabular-nums text-[var(--fg-muted)]">{num(k.volume)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </span>
        </span>
      </span>
    </span>
  );
}
