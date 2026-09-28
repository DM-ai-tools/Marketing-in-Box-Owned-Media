import { motion } from "framer-motion";
import { useId, useMemo, useState } from "react";
import { TypingIndicator } from "../components/TypingIndicator";
import { ASSET_BY_ID } from "../data/assetCatalog";
import { PREPASS_BY_MAIN_ASSET } from "./pipelineData";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";
import type { CompetitorRow } from "./pipelineApi";
import {
  buildApprovedResult,
  parseOwnCompetitorList,
  type CompetitorSelectionMode,
} from "../lib/competitorSelection";

/** Colour-codes how firmly each competitor's offering was confirmed on their own page. Low
 * confidence is the whole reason the field exists, so it is shown, never hidden or averaged away. */
const CONFIDENCE_STYLE: Record<string, { bg: string; fg: string }> = {
  Verified: { bg: "var(--color-signal-green)", fg: "var(--signal-green-fg)" },
  "Partially verified": { bg: "var(--color-signal-orange)", fg: "var(--signal-orange-fg)" },
  Unverified: { bg: "var(--border-strong)", fg: "var(--fg)" },
};

function ConfidenceBadge({ value }: { value: string }) {
  const style = CONFIDENCE_STYLE[value] ?? CONFIDENCE_STYLE.Unverified;
  return (
    <span
      className="shrink-0 rounded-full px-2 py-0.5 text-[0.62rem] font-semibold"
      style={{ backgroundColor: style.bg, color: style.fg }}
    >
      {value}
    </span>
  );
}

function CompetitorRowItem({
  competitor,
  selectable = false,
  checked = true,
  onToggle,
}: {
  competitor: CompetitorRow;
  /** In "Choose which to use" mode each row gets a checkbox; an unticked row is dimmed rather than
   * hidden, so the operator can see what they are leaving out and change their mind. */
  selectable?: boolean;
  checked?: boolean;
  onToggle?: () => void;
}) {
  const host = competitor.page_url?.replace(/^https?:\/\//, "");
  return (
    <li
      className="border-t border-[var(--border)] px-2.5 py-2.5 first:border-t-0 @[30rem]:px-3"
      style={selectable && !checked ? { opacity: 0.5 } : undefined}
    >
      <div className="flex items-start gap-2">
        {selectable ? (
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            aria-label={`Use ${competitor.name}`}
            className="mt-1 h-4 w-4 shrink-0 cursor-pointer accent-[var(--color-electric-blue)]"
          />
        ) : (
          <span className="mt-0.5 w-5 shrink-0 text-[0.72rem] font-semibold text-[var(--fg-faint)]">
            {competitor.rank}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[0.86rem] font-semibold">{competitor.name}</span>
            <span className="text-[0.72rem] text-[var(--fg-faint)]">{competitor.domain}</span>
            <ConfidenceBadge value={competitor.verification_confidence} />
            {/* The stage's own classifier — lead-magnet type, blog content focus, podcast topical
                focus. Its whole value is being scannable down the column, so it gets a pill rather
                than a line of prose. */}
            {competitor.category && (
              <span
                className="shrink-0 rounded-full border px-2 py-0.5 text-[0.66rem] font-medium"
                style={{ borderColor: "var(--border-strong)", color: "var(--fg-muted)" }}
              >
                {competitor.category}
              </span>
            )}
            {/* Given its own pill, and pushed to the end of the row, because on the Offers stage the
                price is the column the operator scans down — a value ladder is priced against it.
                Rendered verbatim as published ("From $1,500/mo"), never reformatted. */}
            {competitor.starting_price && (
              <span
                className="shrink-0 rounded-full px-2 py-0.5 text-[0.72rem] font-semibold @[34rem]:ml-auto"
                style={{
                  backgroundColor: "color-mix(in srgb, var(--color-signal-green) 16%, transparent)",
                  color: "var(--color-signal-green)",
                }}
                title="Starting price as published on the competitor's own page"
              >
                {competitor.starting_price}
              </span>
            )}
          </div>

          {competitor.page_url && (
            <a
              href={competitor.page_url}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-0.5 block truncate text-[0.74rem] underline underline-offset-2"
              style={{ color: "var(--color-electric-blue)" }}
              title={competitor.page_url}
            >
              {host}
            </a>
          )}

          {competitor.offering_summary && (
            <p className="mt-1 text-[0.78rem] leading-relaxed text-[var(--fg-muted)]">
              {competitor.offering_summary}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

const MODE_LABELS: Record<CompetitorSelectionMode, string> = {
  all: "Use all",
  select: "Choose which to use",
  own: "Use my own list",
};

/** The three ways an operator can take a listing: all of it, some of it, or none of it in favour of
 * a list they already have. A segmented control rather than three buttons, because picking one is
 * not yet approving anything — the save button below still does that. */
function ModePicker({
  mode,
  onChange,
  modes,
  foundCount,
}: {
  mode: CompetitorSelectionMode;
  onChange: (mode: CompetitorSelectionMode) => void;
  modes: CompetitorSelectionMode[];
  foundCount: number;
}) {
  return (
    <div role="radiogroup" aria-label="Which competitors to use" className="mb-2.5 flex flex-wrap gap-1.5">
      {modes.map((m) => {
        const active = m === mode;
        return (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(m)}
            className="min-h-9 cursor-pointer rounded-full border px-3 py-1 text-[0.76rem] font-semibold sm:min-h-0"
            style={
              active
                ? { backgroundColor: "var(--color-electric-blue)", borderColor: "var(--color-electric-blue)", color: "white" }
                : { borderColor: "var(--border-strong)", color: "var(--fg-muted)" }
            }
          >
            {m === "all" ? `${MODE_LABELS.all} ${foundCount}` : MODE_LABELS[m]}
          </button>
        );
      })}
    </div>
  );
}

/** The operator's own list: one competitor per line, each with its website. What was read is shown
 * back as a count, and any line with no website is named, so nothing typed disappears silently. */
function OwnListEditor({
  text,
  onChange,
  readCount,
  unreadable,
}: {
  text: string;
  onChange: (text: string) => void;
  readCount: number;
  unreadable: string[];
}) {
  // Several competitor cards can sit in one transcript, so a fixed id would repeat.
  const inputId = useId();
  return (
    <div className="mb-1">
      <label className="mb-1 block text-[0.78rem] text-[var(--fg-muted)]" htmlFor={inputId}>
        One competitor per line, with their website — e.g. <span className="font-medium text-[var(--fg)]">Acme Digital — acme.com</span>
      </label>
      <textarea
        id={inputId}
        value={text}
        onChange={(e) => onChange(e.target.value)}
        rows={6}
        placeholder={"Acme Digital — acme.com\nhttps://www.rivalagency.com.au/services\nbrightgrowth.co"}
        className="w-full resize-y rounded-xl border border-[var(--border-strong)] bg-[var(--bg)] px-3 py-2 text-[0.82rem] leading-relaxed outline-none focus:border-[var(--color-electric-blue)]"
      />
      <p className="mt-1 text-[0.74rem] text-[var(--fg-muted)]">
        {readCount === 0 ? "No competitors read yet." : `${readCount} competitor${readCount === 1 ? "" : "s"} read.`}
        {" "}These are used as given — they are not researched or verified.
      </p>
      {unreadable.length > 0 && (
        <p className="mt-1 text-[0.74rem]" style={{ color: "var(--color-signal-orange)" }}>
          No website found on: {unreadable.map((line) => `"${line}"`).join(", ")} — add one, or it will be left out.
        </p>
      )}
    </div>
  );
}

/** The gated competitor sub-step's card: a reviewable listing of who was found, where, how firmly
 * it was verified, and what their offering is — plus the notes explaining any gap below the
 * requested 10. The model's raw JSON is parsed server-side and deliberately never rendered here.
 *
 * The operator decides what the stage it feeds actually receives: every competitor found (the
 * default), a ticked subset, or a list of their own in place of the search. Whichever it is, it is
 * saved through the same route, so every reader downstream sees one listing and needs no special
 * case. The choice itself is local to the card until it is saved — it is not an answer yet. */
export function CompetitorCard({ message }: { message: PipelineMessage }) {
  const saveCompetitorStep = usePipelineStore((s) => s.saveCompetitorStep);
  const retryCompetitorStep = usePipelineStore((s) => s.retryCompetitorStep);
  const result = message.competitor;
  const saving = message.savePhase === "saving";
  const saved = message.savePhase === "saved";

  const [mode, setMode] = useState<CompetitorSelectionMode>("all");
  // Tracked as what was *excluded*, so a re-run's fresh domains all start ticked without an effect
  // to reset the set, and "all" stays the default however often the listing is replaced.
  const [excluded, setExcluded] = useState<ReadonlySet<string>>(new Set());
  const [ownText, setOwnText] = useState("");
  const own = useMemo(() => parseOwnCompetitorList(ownText), [ownText]);

  // This card serves every competitor stage, not only CRO's: the gating one that runs before a
  // stage, and the mid-intake ones that answer a field (Pillar Page's, run on the topic the
  // operator gave). So the label comes from the catalog and the button says what approving does
  // *here* — continuing the stage that was interrupted, or resuming the intake it belongs to.
  const label = (message.assetId && ASSET_BY_ID[message.assetId]?.label) || "Competitor Analysis";
  // Named from the stage this sub-step feeds, not hardcoded: the same card now gates CRO, Offers,
  // and Pillar Page's mid-intake research.
  const mainAsset = Object.entries(PREPASS_BY_MAIN_ASSET).find(([, c]) => c.assetId === message.assetId)?.[0];
  const mainLabel = (mainAsset && ASSET_BY_ID[mainAsset]?.label) || "the next stage";
  const continueTo = message.competitorFillsFieldId ? "Continue" : `Continue to ${mainLabel}`;

  const found = result?.competitors ?? [];
  const selectedDomains = new Set(found.map((c) => c.domain).filter((d) => !excluded.has(d)));
  // An empty or failed search has nothing to take all or some of, so only the own list is offered.
  const modes: CompetitorSelectionMode[] = found.length > 0 ? ["all", "select", "own"] : ["own"];
  const activeMode: CompetitorSelectionMode = modes.includes(mode) ? mode : "own";
  const usedCount =
    activeMode === "all" ? found.length : activeMode === "select" ? selectedDomains.size : own.rows.length;

  const toggle = (domain: string) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain);
      else next.add(domain);
      return next;
    });

  const approve = () => {
    const approved =
      activeMode === "all"
        ? undefined
        : buildApprovedResult(result, activeMode, selectedDomains, own.rows, message.competitorInputs);
    void saveCompetitorStep(message.id, approved);
  };

  const saveLabel = (() => {
    if (saving) return "Saving…";
    if (message.savePhase === "error") return "Retry Save";
    if (activeMode === "all") return message.competitorFillsFieldId ? "Use This & Continue" : `Save & Continue to ${mainLabel}`;
    if (activeMode === "select") return `Use ${usedCount} selected & ${continueTo}`;
    return `Use my ${usedCount} & ${continueTo}`;
  })();

  const header = (
    <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
      <span aria-hidden>🔎</span>
      <span className="text-[0.85rem] font-semibold">{label}</span>
      <span className="rounded-full border border-[var(--border-strong)] px-1.5 py-[1px] text-[0.62rem] font-semibold text-[var(--fg-muted)]">
        Sub-step
      </span>
    </div>
  );

  const saveError = message.savePhase === "error" && (
    <p className="mb-2 text-[0.78rem]" style={{ color: "var(--color-signal-orange)" }}>
      Save failed: {message.saveError}
    </p>
  );

  const saveButton = (
    <motion.button
      type="button"
      disabled={saving || usedCount === 0}
      onClick={approve}
      whileTap={{ scale: 0.97 }}
      className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-0"
      style={{ backgroundColor: "var(--color-electric-blue)" }}
    >
      {saveLabel}
    </motion.button>
  );

  if (message.competitorError && !saved) {
    const typingOwn = mode === "own";
    return (
      <div
        className="w-full min-w-0 max-w-[47rem] rounded-2xl border-2 bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
        style={{ borderColor: "var(--color-signal-orange)" }}
      >
        {header}
        <p className="text-[0.85rem]" style={{ color: "var(--color-signal-orange)" }}>
          Competitor analysis failed: {message.competitorError}
        </p>
        {typingOwn && (
          <div className="mt-3">
            <OwnListEditor text={ownText} onChange={setOwnText} readCount={own.rows.length} unreadable={own.unreadable} />
          </div>
        )}
        <div className="mt-3">
          {typingOwn && saveError}
          <div className="flex flex-wrap items-center gap-2">
            {typingOwn && saveButton}
            <motion.button
              type="button"
              disabled={saving}
              onClick={() => void retryCompetitorStep(message.id)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-0"
              style={{ backgroundColor: typingOwn ? "var(--fg-muted)" : "var(--color-electric-blue)" }}
            >
              Retry Analysis
            </motion.button>
            {!typingOwn && (
              <motion.button
                type="button"
                onClick={() => setMode("own")}
                whileTap={{ scale: 0.97 }}
                className="min-h-10 cursor-pointer rounded-full border-2 px-3.5 py-1.5 text-[0.8rem] font-semibold sm:min-h-0"
                style={{ borderColor: "var(--color-electric-blue)", color: "var(--color-electric-blue)" }}
              >
                I have my own list
              </motion.button>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="w-full min-w-0 max-w-[47rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5">
        {header}
        <p className="mb-2 text-[0.8rem] text-[var(--fg-muted)]">
          Researching competitors and verifying each one's CRO offering on their own page…
        </p>
        <TypingIndicator />
      </div>
    );
  }

  const shortfall = result.requested_count - result.returned_count;

  return (
    <div className="w-full min-w-0 max-w-[47rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5">
      {header}

      {saved ? (
        <p className="mb-2.5 text-[0.78rem] text-[var(--fg-muted)]">
          <span className="font-medium text-[var(--fg)]">
            {result.returned_count} competitor{result.returned_count === 1 ? "" : "s"}
          </span>{" "}
          used for this stage.
        </p>
      ) : (
        <>
          <p className="mb-2.5 text-[0.78rem] text-[var(--fg-muted)]">
            Benchmarked against <span className="break-all font-medium text-[var(--fg)]">{result.target_url}</span>
            {result.location ? ` in ${result.location}` : ""} —{" "}
            <span className="font-medium text-[var(--fg)]">
              {result.returned_count} of {result.requested_count}
            </span>{" "}
            competitors found. Which should this stage use?
          </p>
          <ModePicker mode={activeMode} onChange={setMode} modes={modes} foundCount={found.length} />
        </>
      )}

      {!saved && activeMode === "own" ? (
        <OwnListEditor text={ownText} onChange={setOwnText} readCount={own.rows.length} unreadable={own.unreadable} />
      ) : result.returned_count === 0 ? (
        <p className="rounded-xl border border-[var(--border)] px-3 py-3 text-[0.82rem] text-[var(--fg-muted)]">
          No competitors met the qualifying criteria for this run.
        </p>
      ) : (
        <>
          {!saved && activeMode === "select" && (
            <div className="mb-1.5 flex flex-wrap items-center gap-3 text-[0.74rem] text-[var(--fg-muted)]">
              <span>
                {selectedDomains.size} of {found.length} selected
              </span>
              <button type="button" className="cursor-pointer underline underline-offset-2" onClick={() => setExcluded(new Set())}>
                Select all
              </button>
              <button
                type="button"
                className="cursor-pointer underline underline-offset-2"
                onClick={() => setExcluded(new Set(found.map((c) => c.domain)))}
              >
                Clear
              </button>
            </div>
          )}
          <ul className="overflow-hidden rounded-xl border border-[var(--border)]">
            {result.competitors.map((c) => (
              <CompetitorRowItem
                key={c.domain}
                competitor={c}
                selectable={!saved && activeMode === "select"}
                checked={selectedDomains.has(c.domain)}
                onToggle={() => toggle(c.domain)}
              />
            ))}
          </ul>
        </>
      )}

      {result.notes && (saved || activeMode !== "own") && (
        <div className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5">
          <div className="mb-1 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
            Notes{!saved && shortfall > 0 ? ` — ${shortfall} short of ${result.requested_count}` : ""}
          </div>
          <p className="text-[0.78rem] leading-relaxed text-[var(--fg-muted)]">{result.notes}</p>
        </div>
      )}

      <div className="mt-3 border-t border-[var(--border)] pt-3">
        {saved ? (
          <span
            className="flex w-fit items-center gap-1.5 rounded-full px-3 py-1 text-[0.78rem] font-semibold text-white"
            style={{ backgroundColor: "var(--color-signal-green)" }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
              <path d="M5 12l5 5L20 7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {message.competitorFillsFieldId ? "Saved — continuing this stage" : `Saved — continuing to ${mainLabel}`}
          </span>
        ) : (
          <>
            {saveError}
            <div className="flex flex-wrap items-center gap-2">
              {saveButton}
              <motion.button
                type="button"
                disabled={saving}
                onClick={() => void retryCompetitorStep(message.id)}
                whileTap={{ scale: 0.97 }}
                className="min-h-10 cursor-pointer rounded-full border-2 px-3.5 py-1.5 text-[0.8rem] font-semibold disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-0"
                style={{ borderColor: "var(--color-signal-orange)", color: "var(--color-signal-orange)" }}
              >
                Re-run Analysis
              </motion.button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
