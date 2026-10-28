import { motion, useReducedMotion } from "framer-motion";
import { useState } from "react";
import type { BlogKeywordOpportunity } from "./pipelineApi";
import { usePipelineStore, type PipelineMessage } from "./pipelineStore";

const n = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-US"));

const STATUS: Record<BlogKeywordOpportunity["status"], { label: string; tone: string }> = {
  gap: { label: "Competitors rank, you don't", tone: "var(--color-signal-green)" },
  improve: { label: "You rank past page one", tone: "var(--color-electric-blue)" },
  new: { label: "Open opportunity", tone: "var(--fg-muted)" },
  owned: { label: "You already rank top 10", tone: "var(--fg-faint)" },
};

/** Better keywords for a blog post, measured against the client's reference page and the pages
 * competing for the topic (`BLOG_KEYWORD_OPTION`).
 *
 * The primary keyword is one choice: a suggestion, the current keyword, or one typed. Supporting
 * keywords are optional ticks. Every row says *why* it is suggested (who ranks for it, where the
 * client stands), both providers' numbers side by side, and how it compares with the current keyword,
 * so "better" is something the operator can check rather than take on trust.
 */
export function BlogKeywordCard({ message }: { message: PipelineMessage }) {
  const confirm = usePipelineStore((s) => s.confirmBlogKeywords);
  const retry = usePipelineStore((s) => s.retryServiceScan);
  const research = usePipelineStore((s) => s.researchBlogKeywords);
  const still = Boolean(useReducedMotion());
  const [primary, setPrimary] = useState<string | null>(null);
  const [own, setOwn] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState("");
  const [seed, setSeed] = useState("");

  const scan = message.serviceScan;
  const blog = scan?.blog;
  if (!scan || !blog) return null;
  const result = blog.result;
  const superseded = Boolean(message.superseded);
  const current = result?.current ?? null;
  const chosenPrimary = own.trim() || primary || (result?.primary[0]?.keyword ?? blog.current);

  function submit() {
    const secondary = [...(result?.primary ?? []), ...(result?.secondary ?? [])].filter(
      (k) => picked.includes(k.keyword) && k.keyword !== chosenPrimary,
    );
    confirm(message.id, chosenPrimary, secondary, other);
  }

  if (scan.status === "done" || scan.status === "skipped") {
    return (
      <div className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 @[30rem]:px-4" style={{ opacity: 0.75 }}>
        <div className="text-[0.88rem] font-medium">Blog keywords</div>
        <p className="mt-0.5 text-[0.76rem] text-[var(--fg-muted)]">
          Primary: <span className="font-medium text-[var(--fg)]">{blog.chosenPrimary}</span>
          {blog.pickedSecondary?.length ? ` · Supporting: ${blog.pickedSecondary.join(", ")}` : ""}
        </p>
      </div>
    );
  }

  return (
    <div
      data-blog-keywords
      className="w-full min-w-0 max-w-[52rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="text-[0.92rem] font-medium">Better keywords for this blog</div>
      <p className="mt-0.5 text-[0.75rem] leading-relaxed text-[var(--fg-muted)]">
        What the client's page and the pages competing for “{blog.seed}” rank for, measured by DataForSEO. Pick the
        primary keyword, and tick any supporting keywords.
      </p>

      {scan.status === "loading" && (
        <div className="mt-2.5 flex items-center gap-2 text-[0.78rem] text-[var(--fg-muted)]" role="status">
          <span className="flex gap-1" aria-hidden>
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                className="h-1 w-1 rounded-full bg-[var(--color-electric-blue)]"
                animate={still ? undefined : { opacity: [0.25, 1, 0.25] }}
                transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15, ease: "easeInOut" }}
              />
            ))}
          </span>
          Reading what the client's page and the competing pages rank for…
        </div>
      )}

      {scan.status === "error" && !superseded && (
        <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2">
          <p className="m-0 text-[0.75rem] text-[var(--fg-muted)]">Couldn't fetch keyword data: {scan.error}</p>
          <button type="button" onClick={() => retry(message.id)} className="mt-1 cursor-pointer text-[0.75rem] font-medium underline underline-offset-2">
            Try again
          </button>
        </div>
      )}

      {scan.status === "ready" && result && !superseded && (
        <>
          <Compared result={result} />
          {result.notes.map((note, i) => (
            <p key={i} className="mt-1.5 text-[0.72rem] text-[var(--fg-muted)]">{note}</p>
          ))}
          {result.dataforseo.status !== "ok" && (
            <p className="mt-1 text-[0.72rem] text-[var(--color-signal-amber)]">
              DataForSEO: {result.dataforseo.error || result.dataforseo.status}
            </p>
          )}

          {current && (
            <p data-current-keyword className="mt-2.5 text-[0.78rem]">
              <span className="text-[var(--fg-muted)]">Current keyword: </span>
              <span className="font-medium">{current.keyword}</span>
              <span className="text-[var(--fg-muted)]"> — {n(current.volume)}/mo · KD {n(current.difficulty)}</span>
            </p>
          )}

          <fieldset className="mt-2.5">
            <legend className="text-[0.7rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">Primary keyword — pick one</legend>
            {result.primary.length === 0 && (
              <p className="mt-1 text-[0.75rem] text-[var(--fg-muted)]">No better keyword was found for “{blog.seed}”. Keep the current one or type another.</p>
            )}
            <ul className="mt-1 divide-y divide-[var(--border)]">
              {result.primary.map((k, i) => (
                <KeywordRow
                  key={k.keyword}
                  item={k}
                  current={current}
                  control={
                    <input
                      type="radio"
                      name={`primary-${message.id}`}
                      checked={!own.trim() && (primary ? primary === k.keyword : i === 0)}
                      onChange={() => {
                        setPrimary(k.keyword);
                        setOwn("");
                      }}
                      aria-label={`Use ${k.keyword} as the primary keyword`}
                    />
                  }
                />
              ))}
              {current && (
                <li className="flex items-center gap-2 py-1.5 text-[0.78rem]">
                  <input
                    type="radio"
                    name={`primary-${message.id}`}
                    checked={!own.trim() && primary === current.keyword}
                    onChange={() => {
                      setPrimary(current.keyword);
                      setOwn("");
                    }}
                    aria-label="Keep the current keyword"
                  />
                  Keep the current keyword ({current.keyword})
                </li>
              )}
            </ul>
            <input
              value={own}
              onChange={(e) => setOwn(e.target.value)}
              placeholder="…or type your own primary keyword"
              aria-label="Your own primary keyword"
              className="mt-1.5 w-full rounded-lg border border-[var(--border-strong)] bg-[var(--bg)] px-2.5 py-1.5 text-[0.8rem] outline-none focus:border-[var(--color-electric-blue)]"
            />
          </fieldset>

          {(result.secondary.length > 0 || result.primary.length > 1) && (
            <fieldset className="mt-3">
              <legend className="text-[0.7rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
                Supporting keywords — optional
              </legend>
              <ul className="mt-1 divide-y divide-[var(--border)]">
                {[...result.primary, ...result.secondary]
                  .filter((k) => k.keyword !== chosenPrimary)
                  .map((k) => (
                    <KeywordRow
                      key={k.keyword}
                      item={k}
                      current={null}
                      control={
                        <input
                          type="checkbox"
                          checked={picked.includes(k.keyword)}
                          onChange={() =>
                            setPicked((p) => (p.includes(k.keyword) ? p.filter((x) => x !== k.keyword) : [...p, k.keyword]))
                          }
                          aria-label={`Add ${k.keyword} as a supporting keyword`}
                        />
                      }
                    />
                  ))}
              </ul>
              <input
                value={other}
                onChange={(e) => setOther(e.target.value)}
                placeholder="Other supporting keywords, comma-separated"
                aria-label="Other supporting keywords"
                className="mt-1.5 w-full rounded-lg border border-[var(--border-strong)] bg-[var(--bg)] px-2.5 py-1.5 text-[0.8rem] outline-none focus:border-[var(--color-electric-blue)]"
              />
            </fieldset>
          )}
        </>
      )}

      {(scan.status === "ready" || scan.status === "error") && !superseded && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              onClick={submit}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              Use “{chosenPrimary}”{picked.length ? ` + ${picked.length} supporting` : ""}
            </motion.button>
          </div>
          <form
            className="mt-2 flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (seed.trim()) research(message.id, seed);
            }}
          >
            <input
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              placeholder="Compare around a different term"
              aria-label="Search a different term"
              className="min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 text-[0.76rem] outline-none focus:border-[var(--color-electric-blue)]"
            />
            <button type="submit" disabled={!seed.trim()} className="cursor-pointer rounded-full border border-[var(--border-strong)] px-3 py-1 text-[0.74rem] disabled:opacity-50">
              Search
            </button>
          </form>
        </>
      )}
    </div>
  );
}

/** What was compared: the client's page and each competing page, with how many on-topic keywords it
 * ranks for. */
function Compared({ result }: { result: NonNullable<NonNullable<PipelineMessage["serviceScan"]>["blog"]>["result"] }) {
  if (!result || !result.compared.length) return null;
  return (
    <div data-compared className="mt-2 rounded-xl bg-[var(--bg-sunken)] px-3 py-2 text-[0.72rem] leading-relaxed text-[var(--fg-muted)]">
      <span className="font-semibold text-[var(--fg)]">Compared against </span>
      {result.compared.map((p, i) => (
        <span key={p.url}>
          {i > 0 ? " · " : ""}
          {p.role === "reference" ? "your page " : ""}
          <a href={p.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
            {p.domain}
          </a>
          {p.error ? " (could not be read)" : ` (${p.on_topic ?? 0} on-topic)`}
        </span>
      ))}
      <span className="block text-[0.68rem] text-[var(--fg-faint)]">
        {result.competitor_source === "listing"
          ? "Competitors from this blog's approved competitor research."
          : `Competitors are the pages in Google's top 10 for “${result.seed}”${result.location ? ` in ${result.location}` : ""}.`}{" "}
        Volumes are monthly Google searches; KD is keyword difficulty, 0–100.
      </span>
    </div>
  );
}

function KeywordRow({
  item,
  current,
  control,
}: {
  item: BlogKeywordOpportunity;
  current: BlogKeywordOpportunity | null;
  control: React.ReactNode;
}) {
  const status = STATUS[item.status];
  const gain = current && item.volume !== null && current.volume !== null ? item.volume - current.volume : null;
  return (
    <li className="flex items-start gap-2 py-1.5">
      <span className="pt-0.5">{control}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span className="text-[0.8rem] font-medium">{item.keyword}</span>
          <span className="rounded-full border px-1.5 text-[0.62rem] font-medium" style={{ borderColor: status.tone, color: status.tone }}>
            {status.label}
          </span>
          {gain !== null && gain > 0 && (
            <span className="text-[0.66rem] font-medium text-[var(--color-signal-green)]">+{gain.toLocaleString("en-US")}/mo vs current</span>
          )}
        </div>
        <div className="mt-0.5 text-[0.7rem] text-[var(--fg-muted)]">
          {n(item.dataforseo_volume)}/mo · KD {n(item.dataforseo_difficulty)}
          {item.intent ? <span className="text-[var(--fg-faint)]"> · {item.intent}</span> : null}
        </div>
        {(item.competitors.length > 0 || item.reference_position !== null) && (
          <div className="mt-0.5 text-[0.68rem] text-[var(--fg-faint)]">
            {item.reference_position !== null ? `Your page #${item.reference_position}` : ""}
            {item.reference_position !== null && item.competitors.length ? " · " : ""}
            {item.competitors.map((c) => `${c.domain}${c.position ? ` #${c.position}` : ""}`).join(" · ")}
          </div>
        )}
      </div>
    </li>
  );
}
