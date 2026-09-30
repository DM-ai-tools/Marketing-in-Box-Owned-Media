import { useCallback, useEffect, useState } from "react";
import { TypingIndicator } from "../components/TypingIndicator";
import { ASSET_BY_ID } from "../data/assetCatalog";
import { fetchUsageCalls, fetchUsageSummary, UNATTRIBUTED } from "../lib/usageApi";
import type { ChatUsage, UsageCall, UsageSummary, UsageTotals } from "../lib/usageApi";
import { usePipelineStore } from "./pipelineStore";
import { useUiStore } from "../store/uiStore";

/** API spend, segregated by chat.
 *
 * Every figure here is measured, not estimated: each row of `api_usage` is written from the `usage`
 * block the API returned for one call, and priced at that moment (see `app/services/pricing.py`).
 * That distinction is worth keeping visible — a character-count estimate of the same run came out
 * ~15% low, because thinking tokens are billed as output and never appear in the saved text.
 *
 * Chat is the unit because it is the unit an operator recognises. A run only starts existing when
 * its first stage is approved, so run-scoped accounting would silently omit every draft rejected
 * before that — which is exactly the spend someone asking "why did today cost that" wants to see.
 */

function money(usd: number): string {
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

function compact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function when(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

const KIND_LABEL: Record<string, string> = {
  generation: "Generated",
  revision: "Refined",
  competitor: "Competitor search",
  briefing: "Briefing",
  check: "Business check",
};

/** One headline number. */
function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2.5">
      <div className="text-[0.66rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">{label}</div>
      <div className="mt-0.5 truncate text-[1.05rem] font-semibold tabular-nums">{value}</div>
      {sub && <div className="truncate text-[0.7rem] text-[var(--fg-muted)]">{sub}</div>}
    </div>
  );
}

/** The token split for a totals block. Input and output are shown separately on purpose: input is
 * usually the larger count and the smaller cost, and collapsing them into one number hides the
 * single most useful lever (re-sending the same documents to every stage). */
function TokenLine({ totals }: { totals: UsageTotals }) {
  return (
    <span className="tabular-nums text-[var(--fg-muted)]">
      {compact(totals.input_tokens)} in · {compact(totals.output_tokens)} out
      {totals.cache_read_input_tokens > 0 && <> · {compact(totals.cache_read_input_tokens)} cached</>}
      {totals.web_search_requests > 0 && <> · {totals.web_search_requests} searches</>}
    </span>
  );
}

function CallRow({ call }: { call: UsageCall }) {
  const label = call.asset_id ? ASSET_BY_ID[call.asset_id]?.label ?? call.asset_id : KIND_LABEL[call.kind] ?? call.kind;
  const truncated = call.stop_reason === "max_tokens";

  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-t border-[var(--border)] px-3 py-1.5 text-[0.74rem] first:border-t-0">
      <span className="shrink-0 tabular-nums text-[var(--fg-faint)]">{when(call.created_at)}</span>
      <span className="min-w-0 flex-1 truncate">
        <span className="font-medium">{label}</span>
        <span className="text-[var(--fg-faint)]">
          {" · "}
          {KIND_LABEL[call.kind] ?? call.kind}
          {call.phase === "phase2" ? " · P2" : ""}
        </span>
      </span>
      {truncated && (
        <span
          className="shrink-0 rounded-full px-1.5 text-[0.62rem] font-semibold"
          style={{ backgroundColor: "color-mix(in srgb, var(--color-signal-orange) 18%, transparent)", color: "var(--color-signal-orange)" }}
          title="Hit the max_tokens ceiling — the deliverable was cut off, and everything up to the cut was billed"
        >
          truncated
        </span>
      )}
      <span className="shrink-0 tabular-nums text-[var(--fg-muted)]">
        {compact(call.input_tokens)}/{compact(call.output_tokens)}
        {call.web_search_requests > 0 && ` · ${call.web_search_requests}🔎`}
      </span>
      <span className="w-[4.5rem] shrink-0 text-right font-semibold tabular-nums">{money(call.cost_usd)}</span>
    </li>
  );
}

function ChatRow({ chat, total }: { chat: ChatUsage; total: number }) {
  const loadSession = usePipelineStore((s) => s.loadSession);
  const activeSessionId = usePipelineStore((s) => s.sessionId);
  const closeUsage = useUiStore((s) => s.closeUsage);

  const [open, setOpen] = useState(false);
  const [calls, setCalls] = useState<UsageCall[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const key = chat.chat_session_id ?? UNATTRIBUTED;
  const share = total > 0 ? (chat.cost_usd / total) * 100 : 0;

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next || calls) return;
    try {
      setCalls(await fetchUsageCalls(key));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-raised)]">
      <button
        type="button"
        onClick={() => void toggle()}
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2.5 text-left"
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-[0.86rem] font-semibold">{chat.title}</span>
            {chat.chat_session_id === activeSessionId && chat.chat_session_id && (
              <span className="shrink-0 text-[0.64rem] font-semibold uppercase tracking-wide" style={{ color: "var(--color-signal-green)" }}>
                open
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate text-[0.72rem]">
            <TokenLine totals={chat} />
            <span className="text-[var(--fg-faint)]">
              {" · "}
              {chat.calls} {chat.calls === 1 ? "call" : "calls"} · {when(chat.last_call_at)}
            </span>
          </span>
        </span>
        <span className="shrink-0 text-right">
          <span className="block text-[0.92rem] font-semibold tabular-nums">{money(chat.cost_usd)}</span>
          <span className="block text-[0.66rem] tabular-nums text-[var(--fg-faint)]">{share.toFixed(0)}%</span>
        </span>
      </button>

      {/* Share of total, as a bar under the row. Cheaper to read than the percentage next to it,
          which is why both are here — the number for precision, the bar for the shape. */}
      <div className="mx-3 h-[3px] overflow-hidden rounded-full bg-[var(--bg-sunken)]">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(share, 1)}%`, backgroundColor: "var(--color-electric-blue)" }}
        />
      </div>

      {open && (
        <div className="mt-1.5 px-0 pb-2">
          {error && (
            <p className="px-3 py-1.5 text-[0.74rem]" style={{ color: "var(--color-signal-orange)" }}>
              {error}
            </p>
          )}
          {!calls && !error && (
            <div className="px-3 py-2">
              <TypingIndicator />
            </div>
          )}
          {calls && calls.length === 0 && (
            <p className="px-3 py-1.5 text-[0.74rem] text-[var(--fg-muted)]">No calls recorded.</p>
          )}
          {calls && calls.length > 0 && (
            <ul className="border-t border-[var(--border)]">
              {calls.map((c) => (
                <CallRow key={c.id} call={c} />
              ))}
            </ul>
          )}
          {chat.openable && chat.chat_session_id && chat.chat_session_id !== activeSessionId && (
            <button
              type="button"
              onClick={() => {
                void loadSession(chat.chat_session_id as string);
                closeUsage();
              }}
              className="mx-3 mt-2 cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.72rem] font-medium"
            >
              Open this chat
            </button>
          )}
          {!chat.openable && (
            <p className="px-3 pt-1.5 text-[0.7rem] italic text-[var(--fg-faint)]">
              {chat.chat_session_id
                ? "This chat has been deleted. Its spend stays counted."
                : "Recorded before a chat existed to attribute it to."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function UsagePanel() {
  const closeUsage = useUiStore((s) => s.closeUsage);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSummary(await fetchUsageSummary());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeUsage();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeUsage]);

  const total = summary?.all_time.cost_usd ?? 0;

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--bg)]">
      <header className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <span aria-hidden>📊</span>
        <h2 className="flex-1 text-[0.95rem] font-semibold">API usage &amp; cost</h2>
        <button
          type="button"
          onClick={() => void load()}
          className="cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.72rem] font-medium"
        >
          Refresh
        </button>
        <button
          type="button"
          onClick={closeUsage}
          aria-label="Close usage monitor"
          className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg text-[var(--fg-muted)] hover:bg-[var(--hover)]"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      <div className="pane-scroll min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {loading && !summary && (
          <div className="flex flex-col items-center gap-2 py-10">
            <TypingIndicator />
            <p className="text-[0.8rem] text-[var(--fg-muted)]">Reading the usage ledger…</p>
          </div>
        )}

        {error && (
          <p className="rounded-xl border px-3 py-2.5 text-[0.8rem]" style={{ borderColor: "var(--color-signal-orange)", color: "var(--color-signal-orange)" }}>
            {error}
          </p>
        )}

        {summary && (
          <>
            <div className="grid grid-cols-2 gap-2 @[34rem]:grid-cols-4">
              <Stat label="Today" value={money(summary.today.cost_usd)} sub={`${summary.today.calls} calls`} />
              <Stat label="All time" value={money(summary.all_time.cost_usd)} sub={`${summary.all_time.calls} calls`} />
              <Stat
                label="Tokens today"
                value={`${compact(summary.today.input_tokens + summary.today.output_tokens)}`}
                sub={`${compact(summary.today.input_tokens)} in · ${compact(summary.today.output_tokens)} out`}
              />
              <Stat
                label="Web searches"
                value={String(summary.all_time.web_search_requests)}
                sub={`${money(summary.all_time.web_search_requests * summary.web_search_usd_per_request)} of the total`}
              />
            </div>

            {summary.all_time.calls === 0 && (
              <div className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 text-[0.8rem] leading-relaxed text-[var(--fg-muted)]">
                <span className="font-medium text-[var(--fg)]">Nothing recorded yet.</span> Every
                Anthropic call is measured from the moment this monitor was added — the runs that came
                before it were not instrumented, so they do not appear here. The next generation you
                run will.
              </div>
            )}

            {summary.by_model.length > 0 && (
              <section className="mt-4">
                <h3 className="mb-1.5 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
                  By model
                </h3>
                <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-raised)]">
                  {summary.by_model.map((m) => (
                    <div
                      key={m.model}
                      className="flex flex-wrap items-baseline gap-x-2 border-t border-[var(--border)] px-3 py-1.5 text-[0.76rem] first:border-t-0"
                    >
                      <span className="min-w-0 flex-1 truncate font-medium">{m.model}</span>
                      <TokenLine totals={m} />
                      <span className="w-[4.5rem] shrink-0 text-right font-semibold tabular-nums">
                        {money(m.cost_usd)}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section className="mt-4">
              <h3 className="mb-1.5 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
                By chat — click a row for its calls
              </h3>
              <div className="flex flex-col gap-1.5">
                {summary.by_chat.map((c) => (
                  <ChatRow key={c.chat_session_id ?? UNATTRIBUTED} chat={c} total={total} />
                ))}
              </div>
            </section>

            <p className="mt-4 text-[0.68rem] leading-relaxed text-[var(--fg-faint)]">
              Measured from each response's own usage block and priced at list rates
              ({summary.rates_version}); web search is billed separately at{" "}
              {money(summary.web_search_usd_per_request)} per search. Costs are stored as priced, so a
              later change to list prices does not move these figures. Priced models:{" "}
              {summary.priced_models.join(", ")}.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
