import { useState } from "react";
import { fetchReplicaOutput, type ManusBuildEvent } from "./pipelineApi";
import type { PipelineMessage } from "./pipelineStore";

/** CRO's assembled page — the client's own markup with the rewritten copy in it — on the card.
 *
 * The backend has assembled this for a while (`page_replica.py`, stored under
 * `page_replica_output`) and the stream announced it, but the client dropped the events, so the
 * operator never knew it existed. This shows where the assembly got to, its report, and — only once
 * an assembly is actually stored — a download. The HTML is read from
 * `GET /runs/{runId}/replica/output` when the button is pressed, never carried on the stream.
 *
 * A failed assembly is a note here, not a failed stage: the stage's document streamed in full
 * before the assembly started. Only stages that rebuild a page ever get here — the pillar page is a
 * design-language stage and Phase 2 / NEW PAGE CRO is copy-only, so neither emits these events.
 */
export function ReplicaPanel({ message, label }: { message: PipelineMessage; label: string }) {
  const replica = message.replica;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!replica) return null;

  const report = replica.report;
  const canDownload = replica.status === "done" && !!report && !!replica.runId;

  const download = () => {
    if (!replica.runId) return;
    setBusy(true);
    setError(null);
    void fetchReplicaOutput(replica.runId)
      .then(({ html }) => {
        const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = `${slugify(label) || "page"}-assembled.html`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
        setBusy(false);
      })
      .catch((err: unknown) => {
        setBusy(false);
        setError(err instanceof Error ? err.message : "Couldn't load the assembled page");
      });
  };

  const host = hostOf(report?.source_url ?? replica.template?.source_url ?? "");

  return (
    <div
      data-replica={replica.status}
      className="mt-3 rounded-xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2.5 text-[0.8rem]"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Assembled page</span>
        {host && <span className="text-[var(--fg-faint)]">· built on {host}</span>}
        {replica.template?.source && replica.template.source !== "direct" && (
          // Said whenever the free read was refused and the paid rendered reader answered instead.
          <span className="text-[var(--fg-faint)]">· read via Context.dev (rendered page)</span>
        )}
        <span className="ml-auto text-[0.74rem] text-[var(--fg-muted)]">{statusLine(replica.status, replica.template)}</span>
      </div>

      {report && (
        <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[0.76rem] text-[var(--fg-muted)]">
          <li>
            <strong className="text-[var(--fg)]">{report.filled}</strong> lines filled
          </li>
          <li>
            <strong className="text-[var(--fg)]">{report.kept}</strong> kept as the client wrote them
          </li>
          <li>{report.intact ? "Structure intact" : "Structure changed — see the checks"}</li>
          <li>
            {report.warnings.length} {report.warnings.length === 1 ? "warning" : "warnings"}
          </li>
        </ul>
      )}

      {report && report.warnings.length > 0 && (
        <details className="mt-1.5 text-[0.74rem] text-[var(--fg-muted)]">
          <summary className="cursor-pointer">Warnings</summary>
          <ul className="mt-1 list-disc pl-4">
            {report.warnings.map((row, i) => (
              <li key={i}>{row.filter(Boolean).join(" — ")}</li>
            ))}
          </ul>
        </details>
      )}
      {report && report.checks.some(([, passed]) => !passed) && (
        <details className="mt-1 text-[0.74rem] text-[var(--fg-muted)]">
          <summary className="cursor-pointer">Checks that did not pass</summary>
          <ul className="mt-1 list-disc pl-4">
            {report.checks
              .filter(([, passed]) => !passed)
              .map(([name, , detail]) => (
                <li key={name}>
                  {name}
                  {detail ? ` — ${detail}` : ""}
                </li>
              ))}
          </ul>
        </details>
      )}

      {replica.status === "error" && (
        <p className="mt-1 text-[0.76rem]" style={{ color: "var(--color-signal-amber)" }}>
          The page could not be assembled: {replica.error}. The document above is complete — this was only the extra
          page built from it.
        </p>
      )}

      {canDownload && (
        <button
          type="button"
          onClick={download}
          disabled={busy}
          className="mt-2 cursor-pointer rounded-full border border-[var(--border-strong)] px-3 py-1 text-[0.76rem] font-medium hover:bg-[var(--hover)] disabled:cursor-default disabled:opacity-60"
        >
          {busy ? "Loading…" : "Download assembled page (.html)"}
        </button>
      )}
      {error && (
        <p className="mt-1 text-[0.72rem]" style={{ color: "var(--color-signal-orange)" }}>
          {error}
        </p>
      )}
    </div>
  );
}

const minutes = (seconds?: number) => {
  if (!seconds || seconds < 60) return "under a minute";
  const m = Math.round(seconds / 60);
  return `${m} min`;
};

/** The Pillar Page being built by Manus. A build takes minutes and sends no text until it is done,
 * so this is what tells the operator the card is working rather than stuck. Once done it stays as
 * one line with the task link; a fallback to Claude stays as an amber note with the reason. */
export function ManusPanel({ build, streaming }: { build?: ManusBuildEvent; streaming?: boolean }) {
  if (!build) return null;
  const link = build.task_url ? (
    <a href={build.task_url} target="_blank" rel="noreferrer" className="underline">
      Open the task in Manus
    </a>
  ) : null;

  if (build.status === "fallback") {
    return (
      <div
        data-manus-panel="fallback"
        className="mt-3 rounded-xl border px-3 py-2 text-[0.78rem]"
        style={{ borderColor: "var(--color-signal-amber)" }}
      >
        <p className="font-semibold">Manus did not build this page</p>
        <p className="mt-1 text-[var(--fg-muted)]">{build.message}</p>
        {link && <p className="mt-1">{link}</p>}
      </div>
    );
  }

  if (build.status === "done" || !streaming) {
    const credits = typeof build.credits === "number" ? ` · ${build.credits} Manus credits` : "";
    return (
      <p data-manus-panel="done" className="mt-3 text-[0.76rem] text-[var(--fg-muted)]">
        Page built by Manus{credits}. {link}
      </p>
    );
  }

  return (
    <div data-manus-panel="building" role="status" className="mt-3 rounded-xl border border-[var(--border)] px-3 py-2 text-[0.78rem]">
      <p className="flex items-center gap-2 font-semibold">
        <span
          aria-hidden
          className="inline-block h-2 w-2 rounded-full motion-safe:animate-pulse"
          style={{ background: "var(--color-signal-amber)" }}
        />
        Manus is building the page · {minutes(build.elapsed)}
      </p>
      {build.brief && <p className="mt-1 text-[var(--fg-muted)]">{build.brief}</p>}
      <p className="mt-1 text-[var(--fg-muted)]">
        A full page usually takes several minutes. You can leave this open. {link}
      </p>
    </div>
  );
}

/** Image generation that failed for this stage. The stage ran on without those images, and the
 * prompt was given no URL for them, so this is the only place the operator learns one is missing. */
export function ImageNotes({ notes }: { notes?: string[] }) {
  if (!notes?.length) return null;
  return (
    <div
      data-image-notes
      className="mt-3 rounded-xl border px-3 py-2 text-[0.78rem]"
      style={{ borderColor: "var(--color-signal-amber)" }}
    >
      <p className="font-semibold">Some images were not generated</p>
      <ul className="mt-1 list-disc pl-4 text-[var(--fg-muted)]">
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

/** The outcome of a Refine made of edits. The draft was edited in place, so this is the only place
 * to learn that a requested change was not made — and, on "Fix with Refine", that the layout was
 * locked: anything that needed a layout change was left alone and is listed here, never improvised. */
export function RevisionNotes({ report }: { report?: PipelineMessage["revisionReport"] }) {
  if (!report) return null;
  const { applied, rejected } = report;
  const skipped = report.skipped ?? [];
  const lock = report.layout_locked ? " Layout locked: only copy and links were changed." : "";
  if (!rejected.length && !skipped.length) {
    return (
      <p data-revision-notes className="mt-3 text-[0.76rem] text-[var(--fg-muted)]">
        Refined in place: {applied} {applied === 1 ? "edit" : "edits"} applied; everything else is unchanged.{lock}
      </p>
    );
  }
  return (
    <div
      data-revision-notes
      className="mt-3 rounded-xl border px-3 py-2 text-[0.78rem]"
      style={{ borderColor: "var(--color-signal-amber)" }}
    >
      <p className="font-semibold">
        {applied} {applied === 1 ? "edit" : "edits"} applied
        {rejected.length > 0 && `, ${rejected.length} not applied`}
        {skipped.length > 0 && `, ${skipped.length} left for a layout change`}
      </p>
      {lock && <p className="mt-0.5 text-[var(--fg-muted)]">{lock.trim()}</p>}
      {rejected.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-[var(--fg-muted)]">
          {rejected.map(([find, why], i) => (
            <li key={`r${i}`}>
              {find && <span className="font-mono text-[0.72rem]">“{find}”</span>} {why}
            </li>
          ))}
        </ul>
      )}
      {skipped.length > 0 && (
        <>
          <p className="mt-1.5 font-medium">Not changed — these need the layout changed:</p>
          <ul className="mt-0.5 list-disc pl-4 text-[var(--fg-muted)]">
            {skipped.map(([finding, why], i) => (
              <li key={`s${i}`}>
                {finding}
                {why ? ` — ${why}` : ""}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[var(--fg-faint)]">
            To make one, use Refine and ask for that change in your own words, or re-run the stage.
          </p>
        </>
      )}
    </div>
  );
}

function statusLine(status: string, template?: { available: boolean; reason?: string | null }): string {
  if (status === "template") return template?.available ? "Template captured" : `No template: ${template?.reason ?? "unavailable"}`;
  if (status === "assembling") return "Assembling…";
  if (status === "done") return "Ready";
  return "Not assembled";
}

function hostOf(url: string): string {
  try {
    return url ? new URL(url).host : "";
  } catch {
    return "";
  }
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}
