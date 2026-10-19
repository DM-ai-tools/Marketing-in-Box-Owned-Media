import { motion } from "framer-motion";
import { useState } from "react";
import { HtmlPreviewDialog } from "../components/HtmlPreview";
import { fetchFunnelBuild, funnelBuildZipUrl, type FunnelBuiltItem } from "./pipelineApi";
import { usePipelineStore, type PipelineMessage } from "./pipelineStore";

/** The deployable funnel: offered once Funnel and Funnel Hub & Media are approved, then the build's
 * progress, then every page and email with its deploy check, a preview, a requested change, a
 * rebuild and the zip.
 *
 * "Request changes" applies the operator's note to one file as exact edits (the Pillar Page's Refine
 * mechanism, `page_revision`): only what the note asks for changes, and the rest of the page stays as
 * built. "Rebuild" is the other tool — a new page from the approved copy.
 *
 * The card carries summaries only. A preview fetches the stored build, so a chat's saved state never
 * holds every page's HTML, and what is previewed is exactly what the zip will contain. */
export function FunnelBuildCard({ message }: { message: PipelineMessage }) {
  const start = usePipelineStore((s) => s.startFunnelBuild);
  const decline = usePipelineStore((s) => s.declineFunnelBuild);
  const rebuild = usePipelineStore((s) => s.rebuildFunnelBuildItem);
  const revise = usePipelineStore((s) => s.reviseFunnelBuildItem);
  const save = usePipelineStore((s) => s.saveFunnelBuild);
  const runId = usePipelineStore((s) => s.runId);
  const [preview, setPreview] = useState<{ html: string; label: string } | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const build = message.funnelBuild;
  if (!build) return null;
  const live = !message.superseded;
  const plan = build.plan;
  const pages = build.items.filter((i) => i.kind === "page");
  const emails = build.items.filter((i) => i.kind === "email");
  const expected = plan ? plan.files.length + plan.emails.length : 0;

  const openPreview = async (item: FunnelBuiltItem) => {
    if (!runId) return;
    setPreviewError(null);
    try {
      const stored = await fetchFunnelBuild(runId);
      const found = stored.items.find((i) => i.key === item.key);
      if (found?.html) setPreview({ html: found.html, label: found.title });
      else setPreviewError("That file is not in the stored build yet.");
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div
      className="w-full min-w-0 max-w-[42rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={live || build.status === "done" ? undefined : { opacity: 0.7 }}
    >
      <div className="text-[0.92rem] font-medium">Deployable funnel</div>

      {build.status === "offer" && (
        <>
          <p className="mt-1 text-[0.8rem] leading-relaxed text-[var(--fg-muted)]">
            Funnel and Funnel Hub &amp; Media are approved. Build the funnel's pages
            {plan ? ` (${plan.files.length})` : ""} and follow-up emails{plan ? ` (${plan.emails.length})` : ""} as
            ready-to-publish HTML? Manus builds the pages as an extension of the client's own site: it opens
            the reference page, screenshots it, and reproduces its palette, layout, components and scroll
            animations. Forms post to the client's own form, and the pages link each other at the URLs the
            funnel names.
          </p>
          {live && (
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <motion.button
                type="button"
                onClick={() => void start(message.id)}
                whileTap={{ scale: 0.97 }}
                className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white sm:min-h-0"
                style={{ backgroundColor: "var(--color-electric-blue)" }}
              >
                Build deployable files
              </motion.button>
              <motion.button
                type="button"
                onClick={() => decline(message.id)}
                whileHover={{ backgroundColor: "var(--hover)" }}
                whileTap={{ scale: 0.97 }}
                className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
              >
                Not now
              </motion.button>
            </div>
          )}
          <p className="mt-1.5 text-[0.68rem] leading-relaxed text-[var(--fg-faint)]">
            One Manus task builds every page (usually 15–40 minutes, billed in Manus credits); Claude writes
            the emails alongside. If Manus fails, Claude builds the pages instead.
          </p>
        </>
      )}

      {build.status === "declined" && (
        <p className="mt-1 text-[0.76rem] italic text-[var(--fg-faint)]">
          Not built. "Build deployable funnel" on the Funnel card's menu offers it again.
        </p>
      )}

      {(build.status === "building" || build.status === "done" || (build.status === "error" && build.items.length > 0)) && (
        <>
          {build.status === "building" && (
            <p className="mt-1 text-[0.8rem] text-[var(--fg-muted)]" role="status">
              Building {build.items.length} of {expected || "…"} done
              {build.building.length ? ` · ${build.building.length} in progress` : ""}
            </p>
          )}
          {build.manus && <ManusLine manus={build.manus} building={build.status === "building" || !!build.rebuilding} />}
          {build.status === "done" && !build.manus && build.engine?.pages === "manus" && (
            <p className="mt-1.5 text-[0.74rem] text-[var(--fg-muted)]">Pages built by Manus from the reference page.</p>
          )}
          {build.branded === false && (
            <p className="mt-1.5 text-[0.76rem] text-[var(--color-signal-amber)]">
              This run has no captured page design, so the pages are built without the client's brand tokens.
            </p>
          )}
          {build.forms && (
            <p className="mt-1.5 text-[0.74rem] text-[var(--fg-muted)]">
              {build.forms.length
                ? `Forms post to ${build.forms.map((f) => f.action).join(", ")}`
                : "No form was found on the client's site, so form steps link to the client's call to action instead."}
            </p>
          )}
          <ItemList
            title="Pages"
            planned={plan?.files.map((f) => ({ key: f.key, title: f.title, sub: f.url_path })) ?? []}
            items={pages}
            building={build.building}
            rebuilding={build.rebuilding ?? null}
            revising={build.revising ?? null}
            revision={build.revision}
            onPreview={openPreview}
            onRebuild={live && build.status === "done" ? (key) => void rebuild(message.id, key) : undefined}
            onRevise={live && build.status === "done" ? (key, note) => revise(message.id, key, note) : undefined}
          />
          {(emails.length > 0 || (plan?.emails.length ?? 0) > 0) && (
            <ItemList
              title="Emails"
              planned={plan?.emails.map((e) => ({ key: e.key, title: e.title, sub: e.subject })) ?? []}
              items={emails}
              building={build.building}
              rebuilding={build.rebuilding ?? null}
              revising={build.revising ?? null}
              revision={build.revision}
              onPreview={openPreview}
              onRebuild={live && build.status === "done" ? (key) => void rebuild(message.id, key) : undefined}
              onRevise={live && build.status === "done" ? (key, note) => revise(message.id, key, note) : undefined}
            />
          )}
          {build.status === "done" && runId && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <a
                href={funnelBuildZipUrl(runId)}
                download
                className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white sm:min-h-0"
                style={{ backgroundColor: "var(--color-electric-blue)" }}
              >
                Download deployable funnel (.zip)
              </a>
              {live && (
                <button
                  type="button"
                  onClick={() => void start(message.id)}
                  className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium hover:bg-[var(--hover)] sm:min-h-0"
                >
                  Rebuild everything
                </button>
              )}
              <span className="text-[0.68rem] text-[var(--fg-faint)]">
                Each page at its funnel URL, images written in, plus the emails and a README.
              </span>
            </div>
          )}
          {build.status === "done" && (
            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-3">
              {build.saved ? (
                <span className="text-[0.8rem] font-medium text-[var(--color-signal-green)]">✓ Saved to Deliverables</span>
              ) : (
                live && (
                  <>
                    <motion.button
                      type="button"
                      onClick={() => save(message.id)}
                      disabled={!!build.revising || !!build.rebuilding}
                      whileTap={{ scale: 0.97 }}
                      className="min-h-10 cursor-pointer rounded-full px-4 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-0"
                      style={{ backgroundColor: "var(--color-signal-green)" }}
                    >
                      Save
                    </motion.button>
                    <span className="text-[0.7rem] text-[var(--fg-faint)]">
                      Adds the deployable funnel to Deliverables and moves on to the next asset.
                    </span>
                  </>
                )
              )}
            </div>
          )}
        </>
      )}

      {build.status === "error" && (
        <div className="mt-2">
          <p className="text-[0.8rem] text-[var(--color-signal-red)]" role="alert">
            {build.error || "The build failed."}
          </p>
          {live && (
            <button
              type="button"
              onClick={() => void start(message.id)}
              className="mt-2 min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium hover:bg-[var(--hover)] sm:min-h-0"
            >
              Try again
            </button>
          )}
        </div>
      )}
      {build.status === "done" && build.error && (
        <p className="mt-2 text-[0.76rem] text-[var(--color-signal-red)]" role="alert">
          {build.error}
        </p>
      )}
      {previewError && <p className="mt-2 text-[0.76rem] text-[var(--color-signal-red)]">{previewError}</p>}
      {preview && <HtmlPreviewDialog html={preview.html} label={preview.label} onClose={() => setPreview(null)} />}
    </div>
  );
}

type RevisionReport = NonNullable<NonNullable<PipelineMessage["funnelBuild"]>["revision"]>;

function ItemList({
  title,
  planned,
  items,
  building,
  rebuilding,
  revising,
  revision,
  onPreview,
  onRebuild,
  onRevise,
}: {
  title: string;
  planned: { key: string; title: string; sub: string }[];
  items: FunnelBuiltItem[];
  building: string[];
  rebuilding: string | null;
  revising: string | null;
  revision?: RevisionReport;
  onPreview: (item: FunnelBuiltItem) => void;
  onRebuild?: (key: string) => void;
  onRevise?: (key: string, note: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  // Every planned row from the start, so the list does not reshuffle as items finish.
  const rows = planned.length ? planned : items.map((i) => ({ key: i.key, title: i.title, sub: i.url_path || i.subject }));
  return (
    <div className="mt-2.5">
      <div className="text-[0.7rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">{title}</div>
      <ul className="mt-1 divide-y divide-[var(--border)]">
        {rows.map((row) => {
          const item = items.find((i) => i.key === row.key);
          const busy = building.includes(row.key) || rebuilding === row.key || revising === row.key;
          return (
            <li key={row.key} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[0.8rem] font-medium">{row.title}</div>
                <div className="truncate text-[0.7rem] text-[var(--fg-faint)]">{row.sub}</div>
              </div>
              {busy ? (
                <span className="text-[0.72rem] text-[var(--fg-muted)]" role="status">
                  {revising === row.key ? "applying changes…" : rebuilding === row.key ? "rebuilding…" : "building…"}
                </span>
              ) : item?.error ? (
                <span className="max-w-[16rem] text-[0.72rem] text-[var(--color-signal-red)]">{item.error}</span>
              ) : item?.score != null ? (
                <span
                  className="rounded-full border px-2 py-0.5 text-[0.68rem] font-medium"
                  title={item.verdict}
                  style={{
                    borderColor: "var(--border-strong)",
                    color: item.issues.some((i) => i.severity === "error") ? "var(--color-signal-red)" : "var(--fg-muted)",
                  }}
                >
                  {item.score}/100
                </span>
              ) : item ? (
                <span className="text-[0.72rem] text-[var(--fg-muted)]">built</span>
              ) : (
                <span className="text-[0.72rem] text-[var(--fg-faint)]">waiting</span>
              )}
              {item && !item.error && !busy && (
                <button
                  type="button"
                  onClick={() => onPreview(item)}
                  className="cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-0.5 text-[0.72rem] hover:bg-[var(--hover)]"
                >
                  Preview
                </button>
              )}
              {item && !item.error && onRevise && !busy && (
                <button
                  type="button"
                  onClick={() => setEditing(editing === row.key ? null : row.key)}
                  aria-expanded={editing === row.key}
                  className="cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-0.5 text-[0.72rem] hover:bg-[var(--hover)]"
                >
                  Request changes
                </button>
              )}
              {item && onRebuild && !busy && (
                <button
                  type="button"
                  onClick={() => onRebuild(row.key)}
                  className="cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-0.5 text-[0.72rem] hover:bg-[var(--hover)]"
                >
                  Rebuild
                </button>
              )}
              {editing === row.key && onRevise && (
                <ChangeForm
                  title={row.title}
                  busy={revising === row.key}
                  onCancel={() => setEditing(null)}
                  onSubmit={async (note) => {
                    const ok = await onRevise(row.key, note);
                    if (ok) setEditing(null);
                  }}
                />
              )}
              {revision?.key === row.key && revising !== row.key && <RevisionLine report={revision} />}
              {item?.revisions && item.revisions.length > 0 && editing !== row.key && (
                <p className="w-full text-[0.68rem] text-[var(--fg-faint)]">
                  {item.revisions.length} requested change{item.revisions.length === 1 ? "" : "s"} applied to this file
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The requested change for one file, in the operator's own words. */
function ChangeForm({
  title,
  busy,
  onSubmit,
  onCancel,
}: {
  title: string;
  busy: boolean;
  onSubmit: (note: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="w-full pt-1.5">
      <textarea
        autoFocus
        value={note}
        onChange={(e) => setNote(e.target.value)}
        aria-label={`Changes to ${title}`}
        placeholder="Describe exactly what to change — only that will change. e.g. Change the hero button to “Get my free score” and remove the second testimonial."
        rows={3}
        className="w-full resize-none rounded-xl border border-[var(--border-strong)] bg-[var(--bg)] px-3 py-2 text-[0.82rem] outline-none focus:border-[var(--color-electric-blue)]"
      />
      <div className="mt-1.5 flex items-center gap-2">
        <motion.button
          type="button"
          onClick={() => void onSubmit(note)}
          disabled={!note.trim() || busy}
          whileTap={{ scale: 0.97 }}
          className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.78rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          Apply changes
        </motion.button>
        <button
          type="button"
          onClick={onCancel}
          className="min-h-10 cursor-pointer rounded-full px-3 py-1.5 text-[0.78rem] font-medium text-[var(--fg-muted)] sm:min-h-0"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** What a requested change did. Says when nothing changed and why, since the file is then exactly as
 * it was and the operator needs to know the request did not land. */
function RevisionLine({ report }: { report: RevisionReport }) {
  if (report.unreadable) {
    return (
      <p className="w-full text-[0.72rem] text-[var(--color-signal-amber)]" role="status">
        Nothing was changed: the reply held no edits that could be applied. Try wording the change more specifically.
      </p>
    );
  }
  if (report.applied === 0) {
    const reasons = report.notApplied.map((r) => r.why).filter(Boolean).slice(0, 3);
    return (
      <div className="w-full text-[0.72rem] text-[var(--color-signal-amber)]" role="status">
        <p>Nothing was changed.</p>
        {reasons.length > 0 && (
          <ul className="ml-4 list-disc">
            {reasons.map((why, i) => (
              <li key={i}>{why}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  return (
    <p className="w-full text-[0.72rem] text-[var(--color-signal-green)]" role="status">
      Applied {report.applied} change{report.applied === 1 ? "" : "s"}. Everything else on the page is as built.
      {report.notApplied.length > 0 ? ` ${report.notApplied.length} part${report.notApplied.length === 1 ? "" : "s"} of the request could not be placed.` : ""}
    </p>
  );
}

function minutes(seconds: number | undefined): string {
  if (!seconds) return "";
  const m = Math.floor(seconds / 60);
  return m ? `${m}m ${seconds % 60}s` : `${seconds}s`;
}

/** The Manus task: what it is doing in its own words while it builds, the credits once done, and why
 * Claude took over when it could not. */
function ManusLine({ manus, building }: { manus: NonNullable<PipelineMessage["funnelBuild"]>["manus"]; building: boolean }) {
  if (!manus) return null;
  const link = manus.task_url ? (
    <a href={manus.task_url} target="_blank" rel="noreferrer" className="ml-1 text-[var(--color-electric-blue)] hover:underline">
      View task
    </a>
  ) : null;
  if (manus.status === "fallback") {
    return (
      <p className="mt-1.5 text-[0.74rem] text-[var(--color-signal-amber)]" role="status">
        Manus could not build the pages ({manus.reason}), so Claude built them in the client's design language instead.
      </p>
    );
  }
  if (manus.status === "done" || !building) {
    return (
      <p className="mt-1.5 text-[0.74rem] text-[var(--fg-muted)]">
        Pages built by Manus from the reference page
        {typeof manus.credits === "number" ? ` · ${Math.round(manus.credits)} credits` : ""}
        {link}
      </p>
    );
  }
  return (
    <p className="mt-1.5 text-[0.74rem] text-[var(--fg-muted)]" role="status">
      <span className="font-medium text-[var(--fg)]">Manus is building the pages</span>
      {manus.brief ? ` — ${manus.brief}` : ""}
      {manus.elapsed ? ` · ${minutes(manus.elapsed)}` : ""}
      {link}
    </p>
  );
}
