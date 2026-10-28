import { useState } from "react";
import { motion } from "framer-motion";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";

/** The permission step in front of a mid-intake competitor search.
 *
 * Asked rather than assumed for two reasons. The search costs a minute of web verification and the
 * operator may already hold a curated competitor list — in which case sitting through it is pure
 * waste. And it searches on a topic *they* just typed, so showing that topic back before spending
 * the minute is the cheapest way to catch "Social Media Marketing" when they meant "Social Media
 * Management".
 */
export function CompetitorConsentCard({ message }: { message: PipelineMessage }) {
  const acceptCompetitorResearch = usePipelineStore((s) => s.acceptCompetitorResearch);
  const declineCompetitorResearch = usePipelineStore((s) => s.declineCompetitorResearch);
  const [busy, setBusy] = useState(false);

  const consent = message.consent;
  if (!consent) return null;

  const decided = consent.status !== "pending";

  return (
    <div className="w-full min-w-0 max-w-[37rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <span aria-hidden>🔎</span>
        <span className="text-[0.85rem] font-semibold">{consent.label}</span>
        <span className="rounded-full border border-[var(--border-strong)] px-1.5 py-[1px] text-[0.62rem] font-semibold text-[var(--fg-muted)]">
          Sub-step
        </span>
      </div>

      <p className="text-[0.92rem] font-medium">
        Research competitors' {consent.subject} on “{consent.topic}”?
      </p>

      <p className="mt-1.5 text-[0.8rem] leading-relaxed text-[var(--fg-muted)]">
        I'll find up to 10 competitors who sell the same service as you
        {consent.location ? ` in ${consent.location}` : ""}, with a genuine, qualifying
        {consent.subject} on this topic — each one checked by opening the page, not trusted from a
        search snippet. You'll review the list, and the notes explaining anything that couldn't be
        found, before it's used.
      </p>

      {/* Shown because a wrong topic here is the expensive mistake: the whole benchmark follows from
          it, and so does everything the main stage builds on top of the benchmark. Cheaper to catch
          "Social Media Marketing" when they meant "Social Media Management" now than after a minute
          of verified search. */}
      <dl className="mt-2.5 grid grid-cols-1 gap-x-3 gap-y-0.5 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2 text-[0.76rem] @[26rem]:grid-cols-[auto_1fr] @[26rem]:gap-y-1">
        <dt className="text-[var(--fg-faint)]">Topic</dt>
        <dd className="mb-1 font-medium @[26rem]:mb-0">
          {consent.topic}
          {/* Said out loud on a multi-topic stage. The benchmark is one search, so it runs on the
              strongest pick — but an operator who chose five topics and is shown one would
              otherwise reasonably conclude the other four had been dropped. */}
          {consent.topicCount && consent.topicCount > 1 ? (
            <span className="ml-1.5 font-normal text-[var(--fg-faint)]">
              — the first of your {consent.topicCount}; the benchmark covers the set
            </span>
          ) : null}
        </dd>
        <dt className="text-[var(--fg-faint)]">Benchmarked against</dt>
        <dd className="mb-1 break-all @[26rem]:mb-0">{consent.targetUrl}</dd>
        {consent.location && (
          <>
            <dt className="text-[var(--fg-faint)]">Market</dt>
            <dd>{consent.location}</dd>
          </>
        )}
      </dl>

      {decided ? (
        <p className="mt-2.5 text-[0.78rem] text-[var(--fg-muted)]">
          {consent.status === "accepted" ? "Researching competitors…" : "Using the list you supply below."}
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <motion.button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await acceptCompetitorResearch(message.id);
            }}
            whileTap={{ scale: 0.97 }}
            className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-0"
            style={{ backgroundColor: "var(--color-electric-blue)" }}
          >
            {busy ? "Researching…" : "Yes — scan competitors"}
          </motion.button>
          <motion.button
            type="button"
            disabled={busy}
            onClick={() => declineCompetitorResearch(message.id)}
            whileHover={{ backgroundColor: "var(--hover)" }}
            whileTap={{ scale: 0.97 }}
            className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-0"
          >
            No — I'll paste my own list
          </motion.button>
        </div>
      )}
    </div>
  );
}
