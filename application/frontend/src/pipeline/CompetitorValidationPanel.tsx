import { useEffect, useRef } from "react";
import { TypingIndicator } from "../components/TypingIndicator";
import { BUSINESS_LEVEL_LABEL, defaultPicks, isPickable, PLAY_COLUMN_HINTS, uniqueFetchedKeywords, VERDICT_HINT, VERDICT_LABEL } from "../lib/competitorValidation";
import type { CompetitorValidation, ValidatedCompetitor, ValidationMetric, ValidationVerdict } from "./pipelineApi";
import { usePipelineStore, type PipelineMessage } from "./pipelineStore";
import { ratioTone } from "./visual/tone";
import { comparisonWins, KeywordCompareHover, RankingWinnerTag } from "./KeywordCompare";
import { ScannedKeywords } from "./ScannedKeywords";

const VERDICT_COLOR: Record<ValidationVerdict, string> = {
  implement_now: "var(--color-signal-green)",
  test_first: "var(--color-electric-blue)",
  differentiate: "var(--color-signal-amber)",
  avoid: "var(--color-signal-orange)",
  not_a_fit: "var(--border-strong)",
};

const SOURCE_LABEL: Record<ValidationMetric["source"], string> = {
  measured: "Measured",
  judged: "Judged",
  unavailable: "Not available",
};

const n = (v: number | null | undefined) => (v === null || v === undefined ? "—" : Math.round(v).toLocaleString("en-US"));

/** "Avoid" and "Not a fit" are not shown as tags: the row's reason line already says why, and
 * neither play has a "Use as reference" box. The verdict still ranks the row. */
const HIDDEN_VERDICTS: ReadonlySet<ValidationVerdict> = new Set(["avoid", "not_a_fit"]);

function VerdictPill({ verdict }: { verdict: ValidationVerdict }) {
  if (HIDDEN_VERDICTS.has(verdict)) return null;
  return (
    <span
      className="shrink-0 rounded-full px-2 py-0.5 text-[0.64rem] font-semibold"
      style={{
        backgroundColor: `color-mix(in srgb, ${VERDICT_COLOR[verdict]} 18%, transparent)`,
        color: VERDICT_COLOR[verdict],
      }}
      title={VERDICT_HINT[verdict]}
    >
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

/** One axis as a number and a bar beside it. The number carries the meaning; the bar only helps the
 * eye down the column. A missing score is "—", never an empty bar that reads as zero. */
function Axis({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-1 text-[0.66rem] text-[var(--fg-faint)]">
        <span>{label}</span>
        <span className="font-semibold text-[var(--fg)]">{value === null ? "—" : value}</span>
      </div>
      <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-[var(--bg-sunken)]">
        {value !== null && <div className="h-full rounded-full" style={{ width: `${value}%`, backgroundColor: ratioTone(value / 100) }} />}
      </div>
    </div>
  );
}

function CompetitorScoreRow({
  c,
  limit,
  clientKeywords,
  picked,
  onPick,
}: {
  c: ValidatedCompetitor;
  limit?: number;
  clientKeywords?: CompetitorValidation["client_keywords"];
  /** Undefined when the card is saved: no box, the choice is already made. */
  picked?: boolean;
  onPick?: () => void;
}) {
  const wins = comparisonWins(c.keyword_comparisons);
  return (
    <li data-validated={c.domain} className="border-t border-[var(--border)] px-2.5 py-2.5 first:border-t-0 @[30rem]:px-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[0.78rem] font-bold"
          style={{ border: `2px solid ${c.total === null ? "var(--border-strong)" : ratioTone(c.total / 100)}` }}
          title="Fit score: 40% proof, 40% fit, 20% feasibility"
          aria-label={`Fit score ${n(c.total)} of 100`}
        >
          {n(c.total)}
        </span>
        <span className="text-[0.84rem] font-semibold">{c.name}</span>
        <VerdictPill verdict={c.verdict} />
        {c.business_level && (
          <span
            className="shrink-0 rounded-full border px-2 py-0.5 text-[0.64rem] font-medium"
            style={{ borderColor: "var(--border-strong)", color: "var(--fg-muted)" }}
            title="Organic traffic vs this client's own site"
          >
            {BUSINESS_LEVEL_LABEL[c.business_level]}
          </span>
        )}
        <RankingWinnerTag comparisons={c.keyword_comparisons} />
        <span className="text-[0.7rem] text-[var(--fg-faint)]">{c.play}</span>
        {onPick && isPickable(c) && (
          <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-[0.72rem] font-medium">
            <input type="checkbox" checked={Boolean(picked)} onChange={onPick} data-pick={c.domain} />
            Use as reference
          </label>
        )}
      </div>
      {(c.page_count != null || c.keyword_count != null) && (
        <p className="mt-0.5 text-[0.7rem] text-[var(--fg-faint)]">
          {c.page_count != null ? `Number of pages: ${c.page_count.toLocaleString("en-US")}` : null}
          {c.page_count != null && c.keyword_count != null ? " · " : null}
          {c.keyword_count != null ? (
            <ScannedKeywords keywords={c.scanned_keywords} total={c.keyword_count} limit={limit}>
              {`${c.keyword_count.toLocaleString("en-US")} ranking keywords`}
            </ScannedKeywords>
          ) : null}
        </p>
      )}
      <p className="mt-1 text-[0.74rem] text-[var(--fg-muted)]">{c.verdict_reason}</p>

      <div className="mt-1.5 grid grid-cols-3 gap-3">
        <Axis label="Proof" value={c.proof} />
        <Axis label="Fit" value={c.fit} />
        <Axis label="Feasibility" value={c.feasibility} />
      </div>

      {c.blockers.map((b, i) => (
        <p key={i} className="mt-1.5 text-[0.74rem]" style={{ color: "var(--color-signal-orange)" }}>
          ⚠ {b.reason}
          {b.source === "judged" && b.quote ? <span className="text-[var(--fg-muted)]"> — “{b.quote}”</span> : null}
        </p>
      ))}
      {c.what_to_take && c.verdict !== "avoid" && c.verdict !== "not_a_fit" && (
        <p className="mt-1.5 text-[0.76rem]">
          <span className="font-semibold">Take: </span>
          {c.what_to_take}
        </p>
      )}
      {c.how_to_apply && c.verdict !== "avoid" && c.verdict !== "not_a_fit" && (
        <p className="mt-0.5 text-[0.76rem]">
          <span className="font-semibold">How to apply: </span>
          {c.how_to_apply}
        </p>
      )}
      {c.how_to_prove && c.verdict !== "avoid" && c.verdict !== "not_a_fit" && (
        <p className="mt-0.5 text-[0.76rem]">
          <span className="font-semibold">Prove it for this client: </span>
          {c.how_to_prove}
        </p>
      )}

      <details className="mt-1.5">
        <summary className="cursor-pointer text-[0.72rem] font-medium text-[var(--color-electric-blue)]">The evidence</summary>
        <table className="mt-1 w-full text-left text-[0.72rem]">
          <tbody>
            {c.metrics.map((m) => (
              <tr key={m.key} className="border-t border-[var(--border)] align-top">
                <td className="py-1 pr-2 text-[var(--fg-muted)]">
                  {m.key === "keywords" ? (
                    <ScannedKeywords keywords={c.scanned_keywords} total={c.keyword_count} limit={limit}>
                      {m.label}
                    </ScannedKeywords>
                  ) : m.key === "overlap" ? (
                    <span className="inline-flex flex-wrap items-center gap-y-0.5">
                      <KeywordCompareHover comparisons={c.keyword_comparisons}>{m.label}</KeywordCompareHover>
                      <RankingWinnerTag comparisons={c.keyword_comparisons} />
                    </span>
                  ) : (
                    m.label
                  )}
                </td>
                <td className="py-1 pr-2 font-semibold">
                  {m.key === "overlap" && clientKeywords?.length ? (
                    <ScannedKeywords keywords={clientKeywords} total={clientKeywords.length} limit={limit}>
                      {m.value}
                    </ScannedKeywords>
                  ) : (
                    m.value
                  )}
                </td>
                <td className="py-1 pr-2">
                  <span
                    className="rounded-full px-1.5 py-[1px] text-[0.6rem] font-semibold"
                    style={{
                      border: "1px solid var(--border-strong)",
                      color: m.source === "measured" ? "var(--color-signal-green)" : "var(--fg-muted)",
                    }}
                  >
                    {SOURCE_LABEL[m.source]}
                  </span>
                </td>
                <td className="py-1 text-[var(--fg-muted)]">{m.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {c.top_keywords.length > 0 && (
          <p className="mt-1 text-[0.72rem] text-[var(--fg-muted)]">
            Ranks for:{" "}
            {c.top_keywords.map((k, i) => {
              const cmp = c.keyword_comparisons?.find((row) => row.keyword.toLowerCase() === k.keyword.toLowerCase());
              const winner = cmp?.winner;
              return (
                <span key={k.keyword}>
                  {i > 0 ? ", " : ""}
                  <span
                    style={
                      winner === "competitor"
                        ? { color: "var(--color-signal-amber)", fontWeight: 600 }
                        : winner === "client"
                          ? { color: "var(--color-signal-green)", fontWeight: 600 }
                          : undefined
                    }
                  >
                    {k.keyword}
                    {k.position != null ? ` (#${k.position})` : ""}
                  </span>
                  {cmp?.client_position != null ? (
                    <span className="text-[var(--fg-faint)]">{` · client #${cmp.client_position}`}</span>
                  ) : cmp && cmp.client_position == null ? (
                    <span className="text-[var(--fg-faint)]"> · client not ranking</span>
                  ) : null}
                </span>
              );
            })}
          </p>
        )}
        {wins.client > 0 && (
          <p className="mt-1 text-[0.72rem]" style={{ color: "var(--color-signal-green)" }} data-client-keyword-wins>
            Client ranks better on {wins.client} shared keyword{wins.client === 1 ? "" : "s"}
            {c.keyword_comparisons
              ?.filter((r) => r.winner === "client")
              .slice(0, 6)
              .map((r, i) => `${i === 0 ? ": " : ", "}${r.keyword} (#${r.client_position})`)
              .join("")}
            .
          </p>
        )}
      </details>
    </li>
  );
}

function PlaysColumnsHint() {
  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-describedby="plays-columns-hint"
        aria-label="What these columns mean"
        className="peer flex h-4 w-4 cursor-help items-center justify-center rounded-full border text-[0.62rem] font-semibold italic leading-none text-[var(--fg-faint)]"
        style={{ borderColor: "var(--border-strong)" }}
      >
        i
      </button>
      <div
        id="plays-columns-hint"
        role="tooltip"
        className="pointer-events-none invisible absolute right-0 top-[calc(100%+0.35rem)] z-20 w-[min(18.5rem,calc(100vw-2.5rem))] rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] px-2.5 py-2 text-left shadow-sm peer-hover:visible peer-focus:visible peer-focus-visible:visible"
      >
        <dl className="space-y-1.5">
          {PLAY_COLUMN_HINTS.map((col) => (
            <div key={col.label}>
              <dt className="text-[0.64rem] font-semibold text-[var(--fg)]">{col.label}</dt>
              <dd className="text-[0.7rem] leading-snug text-[var(--fg-muted)]">{col.hint}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

function HeaderCheck({
  checked,
  indeterminate,
  disabled,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled?: boolean;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={onChange}
      aria-label={checked ? "Clear every keyword" : "Select every keyword"}
      data-keyword-select-all
    />
  );
}

/** Every ranking keyword fetched for the listing, one row each, for the operator to tick. */
function KeywordSelectTable({
  report,
  keywordPicks,
  onToggleKeyword,
  onSetKeywords,
  locked,
}: {
  report: CompetitorValidation;
  keywordPicks: string[];
  onToggleKeyword?: (keyword: string) => void;
  onSetKeywords?: (keywords: string[]) => void;
  locked?: boolean;
}) {
  const rows = uniqueFetchedKeywords(report.competitors);
  if (!rows.length) return null;
  const chosen = new Set(keywordPicks.map((k) => k.toLowerCase()));
  const selected = rows.filter((r) => chosen.has(r.keyword.toLowerCase())).length;
  const allOn = selected === rows.length;
  const pos = (value: number | null) => (value == null ? "—" : `#${value}`);
  return (
    <div className="mt-3" data-keyword-picker>
      <div className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
        Keywords for this stage
      </div>
      <p className="text-[0.72rem] leading-snug text-[var(--fg-muted)]">
        {locked
          ? `${selected} keyword${selected === 1 ? "" : "s"} selected for this stage's draft.`
          : "Every ranking keyword fetched for these competitors. Duplicates are shown once. Tick the ones to use in the draft — nothing is included until you tick it."}
      </p>
      {!locked && onSetKeywords && (
        <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[0.72rem] text-[var(--fg-muted)]">
          <span>
            {selected} of {rows.length} selected
          </span>
          <button type="button" className="cursor-pointer underline underline-offset-2" onClick={() => onSetKeywords(rows.map((r) => r.keyword))}>
            Select all
          </button>
          <button type="button" className="cursor-pointer underline underline-offset-2" onClick={() => onSetKeywords([])}>
            Clear
          </button>
        </div>
      )}
      <div className="mt-1.5 max-h-80 overflow-y-auto rounded-xl border border-[var(--border)]">
        <table className="w-full text-left text-[0.74rem]">
          <thead className="sticky top-0 bg-[var(--bg-raised)] text-[var(--fg-faint)]">
            <tr>
              <th className="w-8 py-1.5 pl-2 pr-1 font-medium">
                <HeaderCheck
                  checked={allOn && rows.length > 0}
                  indeterminate={selected > 0 && !allOn}
                  disabled={locked || !onSetKeywords}
                  onChange={() => onSetKeywords?.(allOn ? [] : rows.map((r) => r.keyword))}
                />
              </th>
              <th className="py-1.5 pr-2 font-medium">Keyword</th>
              <th className="py-1.5 pr-2 text-right font-medium">Position</th>
              <th className="py-1.5 pr-2 text-right font-medium">Searches/mo</th>
              <th className="py-1.5 pr-2 text-right font-medium">Client</th>
              <th className="py-1.5 pr-2 font-medium">Ranked by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const checked = chosen.has(row.keyword.toLowerCase());
              return (
                <tr key={row.keyword.toLowerCase()} className="border-t border-[var(--border)] align-top" data-fetched-keyword={row.keyword}>
                  <td className="py-1 pl-2 pr-1">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={locked || !onToggleKeyword}
                      onChange={() => onToggleKeyword?.(row.keyword)}
                      data-keyword-pick={row.keyword}
                      aria-label={`Use ${row.keyword}`}
                    />
                  </td>
                  <td className="py-1 pr-2 font-medium text-[var(--fg)]" style={locked && !checked ? { opacity: 0.55 } : undefined}>
                    {row.keyword}
                  </td>
                  <td className="whitespace-nowrap py-1 pr-2 text-right tabular-nums">{pos(row.position)}</td>
                  <td className="whitespace-nowrap py-1 pr-2 text-right tabular-nums">{n(row.volume)}</td>
                  <td className="whitespace-nowrap py-1 pr-2 text-right tabular-nums text-[var(--fg-muted)]">
                    {row.clientKnown ? (row.clientPosition == null ? "not ranking" : pos(row.clientPosition)) : "—"}
                  </td>
                  <td className="py-1 pr-2 text-[var(--fg-muted)]" title={row.competitors.join(", ")}>
                    {row.competitors.join(", ")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Report({
  report,
  picks,
  onPick,
  keywordPicks,
  onToggleKeyword,
  onSetKeywords,
  locked,
}: {
  report: CompetitorValidation;
  picks: string[];
  onPick?: (domain: string) => void;
  keywordPicks: string[];
  onToggleKeyword?: (keyword: string) => void;
  onSetKeywords?: (keywords: string[]) => void;
  locked?: boolean;
}) {
  const counts = report.competitors.reduce<Partial<Record<ValidationVerdict, number>>>((acc, c) => {
    acc[c.verdict] = (acc[c.verdict] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <>
      <div className="flex flex-wrap gap-1.5">
        {(Object.keys(VERDICT_LABEL) as ValidationVerdict[])
          .filter((v) => counts[v] && !HIDDEN_VERDICTS.has(v))
          .map((v) => (
            <span key={v} className="flex items-center gap-1 text-[0.72rem]">
              <VerdictPill verdict={v} />
              <span className="font-semibold">{counts[v]}</span>
            </span>
          ))}
      </div>
      {(report.client_traffic !== null || report.client_top10 !== null || report.client_page_count !== null) && (
        <p className="mt-1.5 text-[0.74rem] text-[var(--fg-muted)]">
          Baseline, the client's own site: <span className="font-medium text-[var(--fg)]">{n(report.client_traffic)}</span> organic visits/mo,{" "}
          number of pages <span className="font-medium text-[var(--fg)]">{n(report.client_page_count)}</span>,{" "}
          <ScannedKeywords
            keywords={report.client_keywords}
            total={report.client_keyword_count}
            limit={report.scanned_limit}
          >
            <span className="font-medium text-[var(--fg)]">{n(report.client_keyword_count ?? report.client_top10)}</span> ranking keywords
          </ScannedKeywords>
          {report.client_top10 != null ? ` (${n(report.client_top10)} in the top 10)` : ""}.
        </p>
      )}

      {onPick && (
        <p className="mt-2 text-[0.74rem] text-[var(--fg-muted)]" data-pick-summary>
          Ranked best first. Tick the lead magnets, offers and funnel entries to use as the reference: {picks.length} selected. The
          stage builds from the ticked ones.
        </p>
      )}
      <ul className="mt-2 overflow-hidden rounded-xl border border-[var(--border)]">
        {report.competitors.map((c) => (
          <CompetitorScoreRow
            key={c.domain}
            c={c}
            limit={report.scanned_limit}
            clientKeywords={report.client_keywords}
            picked={picks.includes(c.domain)}
            onPick={onPick ? () => onPick(c.domain) : undefined}
          />
        ))}
      </ul>

      {report.plays.length > 0 && (
        <div className="relative mt-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <div className="text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">Plays across the market</div>
            <PlaysColumnsHint />
          </div>
          <table data-plays className="w-full text-left text-[0.74rem]">
            <thead className="text-[var(--fg-faint)]">
              <tr>
                <th className="py-1 pr-2 font-medium">Play</th>
                <th className="py-1 pr-2 font-medium">Used by</th>
                <th className="py-1 font-medium">Their traffic/mo</th>
              </tr>
            </thead>
            <tbody>
              {report.plays.map((p) => (
                <tr key={p.play} className="border-t border-[var(--border)]">
                  <td className="py-1 pr-2 font-medium" title={p.competitors.join(", ")}>{p.play}</td>
                  <td className="py-1 pr-2">{p.count} of {report.competitors.length}</td>
                  <td className="py-1">{n(p.combined_traffic)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {report.market_gaps.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">Gaps no competitor fills</div>
          <ul className="list-disc pl-5 text-[0.76rem]">
            {report.market_gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}

      <KeywordSelectTable
        report={report}
        keywordPicks={keywordPicks}
        onToggleKeyword={onToggleKeyword}
        onSetKeywords={onSetKeywords}
        locked={locked}
      />

      <details className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
        <summary className="cursor-pointer text-[0.72rem] font-semibold text-[var(--fg-muted)]">How this was checked</summary>
        <ul className="mt-1 list-disc pl-5 text-[0.72rem] text-[var(--fg-muted)]">
          {report.sources.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
          <li>Feasibility comes from the client's own settings: testimonials, claim tier and pricing disclosure.</li>
          <li>Conversion rates and lead counts aren't public. Traffic, rankings and how common a play is are proxies, so prove each “Implement now” item with the test it lists.</li>
          {report.notes.map((s, i) => (
            <li key={`n${i}`} style={{ color: "var(--color-signal-amber)" }}>{s}</li>
          ))}
        </ul>
      </details>
    </>
  );
}

/** "Will this work for the client?" on a competitor listing: each competitor scored on proof, fit and
 * feasibility, ranked by verdict, with every number saying where it came from. Advisory: approving
 * the listing never waits on it, and the ranking reaches the stage's prompt with the approved list. */
export function CompetitorValidationPanel({ message }: { message: PipelineMessage }) {
  const validate = usePipelineStore((s) => s.validateCompetitorStep);
  const state = message.competitorValidation;
  const togglePick = usePipelineStore((s) => s.togglePlayPick);
  const toggleKeywordPick = usePipelineStore((s) => s.toggleKeywordPick);
  const setKeywordPicks = usePipelineStore((s) => s.setKeywordPicks);
  const saved = message.savePhase === "saved";
  if (!state) {
    if (saved || !message.competitor?.competitors?.length) return null;
    return (
      <button
        type="button"
        onClick={() => validate(message.id)}
        className="mb-2.5 cursor-pointer text-[0.76rem] font-medium underline underline-offset-2"
        style={{ color: "var(--color-electric-blue)" }}
      >
        Check which of these would work for this client
      </button>
    );
  }
  return (
    <section
      data-competitor-validation
      aria-label="Will it work for this client?"
      className="mb-3 rounded-xl border border-[var(--border)] px-3 py-2.5"
    >
      <div className="mb-1 text-[0.82rem] font-semibold">Will it work for this client?</div>
      {state.status === "loading" && (
        <div role="status" className="text-[0.76rem] text-[var(--fg-muted)]">
          <p className="mb-1">
            Measuring each competitor's traffic and rankings with DataForSEO, checking their pages are live, and comparing each one
            with the ICP and the client's settings…
          </p>
          <TypingIndicator />
        </div>
      )}
      {state.status === "error" && (
        <p className="text-[0.76rem] text-[var(--fg-muted)]">
          Couldn't check this listing: {state.error}{" "}
          {!saved && (
            <button type="button" onClick={() => validate(message.id)} className="cursor-pointer font-medium underline underline-offset-2">
              Check again
            </button>
          )}
        </p>
      )}
      {state.status === "ready" && state.report && (
        <Report
          report={state.report}
          picks={state.picks ?? defaultPicks(state.report)}
          onPick={saved ? undefined : (domain) => togglePick(message.id, domain)}
          keywordPicks={state.keywordPicks ?? []}
          onToggleKeyword={saved ? undefined : (keyword) => toggleKeywordPick(message.id, keyword)}
          onSetKeywords={saved ? undefined : (keywords) => setKeywordPicks(message.id, keywords)}
          locked={saved}
        />
      )}
    </section>
  );
}
