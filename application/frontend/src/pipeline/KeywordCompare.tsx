import type { ReactNode } from "react";
import type { KeywordComparison } from "./pipelineApi";

const num = (value: number | null | undefined) => (typeof value === "number" ? value.toLocaleString("en-US") : "—");
const pos = (value: number | null | undefined) => (typeof value === "number" ? `#${value}` : "—");

export function comparisonWins(rows: KeywordComparison[] | undefined) {
  const list = rows ?? [];
  return {
    client: list.filter((r) => r.winner === "client").length,
    competitor: list.filter((r) => r.winner === "competitor").length,
    tie: list.filter((r) => r.winner === "tie").length,
    betterForCompetitor: list.filter((r) => r.winner === "competitor"),
  };
}

/** Tag on an evidence row: who wins more of the shared keywords. */
export function RankingWinnerTag({ comparisons }: { comparisons: KeywordComparison[] | undefined }) {
  const { client, competitor } = comparisonWins(comparisons);
  if (!comparisons?.length || (client === 0 && competitor === 0)) return null;
  if (client > competitor) {
    return (
      <span
        data-rank-winner="client"
        className="ml-1.5 inline-flex rounded-full px-1.5 py-[1px] text-[0.6rem] font-semibold"
        style={{
          backgroundColor: "color-mix(in srgb, var(--color-signal-green) 18%, transparent)",
          color: "var(--color-signal-green)",
        }}
        title={`${client} shared keyword${client === 1 ? "" : "s"} where this client ranks higher`}
      >
        Client ranks better
      </span>
    );
  }
  if (competitor > client) {
    return (
      <span
        data-rank-winner="competitor"
        className="ml-1.5 inline-flex rounded-full px-1.5 py-[1px] text-[0.6rem] font-semibold"
        style={{
          backgroundColor: "color-mix(in srgb, var(--color-signal-amber) 22%, transparent)",
          color: "var(--color-signal-amber)",
        }}
        title={`${competitor} shared keyword${competitor === 1 ? "" : "s"} where this competitor ranks higher`}
      >
        Competitor ranks better
      </span>
    );
  }
  return (
    <span
      data-rank-winner="tie"
      className="ml-1.5 inline-flex rounded-full border px-1.5 py-[1px] text-[0.6rem] font-semibold text-[var(--fg-muted)]"
      style={{ borderColor: "var(--border-strong)" }}
    >
      Even on shared keywords
    </span>
  );
}

/** Hover over "Ranks for the client's keywords": client vs competitor position per shared term. */
export function KeywordCompareHover({
  comparisons,
  children,
}: {
  comparisons: KeywordComparison[] | undefined;
  children: ReactNode;
}) {
  if (!comparisons?.length) return <>{children}</>;
  return (
    <span className="group relative inline-block">
      <span
        tabIndex={0}
        className="cursor-help underline decoration-dotted underline-offset-2 outline-none focus-visible:rounded focus-visible:ring-1 focus-visible:ring-[var(--color-electric-blue)]"
        aria-label={`${comparisons.length} shared keywords — show client vs competitor positions`}
      >
        {children}
      </span>
      <span
        role="tooltip"
        data-keyword-compare
        className="invisible absolute left-0 top-full z-30 pt-1 opacity-0 transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        <span className="block w-[min(26rem,calc(100vw-2.5rem))] rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] px-2.5 py-2 text-left shadow-sm">
          <span className="block text-[0.66rem] font-semibold text-[var(--fg)]">
            {comparisons.length} shared keyword{comparisons.length === 1 ? "" : "s"}
          </span>
          <span className="block text-[0.62rem] text-[var(--fg-faint)]">
            Client position vs competitor · lower Google rank wins · searches/mo.
          </span>
          <span className="mt-1.5 block max-h-64 overflow-y-auto pr-1">
            <table className="w-full text-[0.7rem]">
              <thead>
                <tr className="text-[0.62rem] text-[var(--fg-faint)]">
                  <th className="py-0.5 pr-2 text-left font-medium">Keyword</th>
                  <th className="py-0.5 pr-2 text-right font-medium">Client</th>
                  <th className="py-0.5 pr-2 text-right font-medium">Them</th>
                  <th className="py-0.5 text-right font-medium">Vol</th>
                </tr>
              </thead>
              <tbody>
                {comparisons.map((k) => (
                  <tr key={k.keyword} className="border-t border-[var(--border)] align-top" data-winner={k.winner}>
                    <td className="py-0.5 pr-2 text-[var(--fg)]">
                      {k.keyword}
                      {k.winner === "client" && (
                        <span className="ml-1 text-[0.58rem] font-semibold" style={{ color: "var(--color-signal-green)" }}>
                          client
                        </span>
                      )}
                      {k.winner === "competitor" && (
                        <span className="ml-1 text-[0.58rem] font-semibold" style={{ color: "var(--color-signal-amber)" }}>
                          them
                        </span>
                      )}
                    </td>
                    <td
                      className="whitespace-nowrap py-0.5 pr-2 text-right tabular-nums"
                      style={{ fontWeight: k.winner === "client" ? 600 : 400, color: k.winner === "client" ? "var(--color-signal-green)" : "var(--fg-muted)" }}
                    >
                      {pos(k.client_position)}
                    </td>
                    <td
                      className="whitespace-nowrap py-0.5 pr-2 text-right tabular-nums"
                      style={{ fontWeight: k.winner === "competitor" ? 600 : 400, color: k.winner === "competitor" ? "var(--color-signal-amber)" : "var(--fg-muted)" }}
                    >
                      {pos(k.competitor_position)}
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
