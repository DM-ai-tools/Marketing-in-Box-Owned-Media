import { parseAssetDocument, type DocSection } from "../../lib/assetDocument";
import { assetGlance } from "../../lib/assetGlance";
import { isFolded, readingMinutes, templateFor, type AssetTemplate } from "../../lib/assetTemplates";
import { GlanceView } from "./GlanceView";
import { VisualOptionsContext, VisualSectionBody } from "./VisualBlocks";

/* The designed layout of one deliverable — the pieces the reader's Visual view and the exported HTML
 * report share, so what an operator reads in the app and what the client opens as a file cannot
 * drift apart. Framing only: every section is rendered, in the document's own order. */

/** The top of the document: what it is, for whom, and how long it is. */
export function ReportHero({
  label,
  template,
  stageNumber,
  words,
  sections,
  client,
  date,
}: {
  label: string;
  template: AssetTemplate;
  stageNumber?: number;
  words: number;
  sections: number;
  client?: string;
  date?: string;
}) {
  const facts = [
    stageNumber !== undefined ? `Stage ${String(stageNumber).padStart(2, "0")}` : null,
    sections > 1 ? `${sections} parts` : null,
    `${words.toLocaleString("en-US")} words`,
    `${readingMinutes(words)} min read`,
    date ?? null,
  ].filter(Boolean);
  return (
    <header className="mb-6 rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-4 py-4 sm:px-5">
      <div className="text-[0.68rem] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--color-electric-blue)" }}>
        {template.kicker}
      </div>
      <h1 className="mt-1 text-balance text-[1.45rem] font-semibold leading-tight">{label}</h1>
      {client && <div className="mt-0.5 text-[0.9rem] text-[var(--fg-muted)]">{client}</div>}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {facts.map((f) => (
          <span key={f} className="rounded-full border border-[var(--border)] bg-[var(--bg)] px-2 py-0.5 text-[0.7rem] text-[var(--fg-muted)]">
            {f}
          </span>
        ))}
      </div>
    </header>
  );
}

/** A section's title row: the label, then how long the section is. */
export function SectionTitle({ section }: { section: DocSection }) {
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      <span className="text-balance text-[1.02rem] font-semibold">{section.label}</span>
      <span className="text-[0.7rem] font-normal text-[var(--fg-faint)]">{section.words.toLocaleString("en-US")} words</span>
    </span>
  );
}

/** A section's body, folded behind its title when the template says it is reference material. */
export function SectionContent({ section, template, label }: { section: DocSection; template: AssetTemplate; label: string }) {
  const body = <VisualSectionBody body={section.body} label={`${label} — ${section.label}`} />;
  if (!isFolded(template, section.label)) {
    return (
      <>
        <h2 className="mb-2">
          <SectionTitle section={section} />
        </h2>
        {body}
      </>
    );
  }
  return (
    <details className="group">
      <summary className="cursor-pointer list-none">
        <h2 className="mb-2 inline">
          <SectionTitle section={section} />
        </h2>
        <span className="ml-2 text-[0.74rem] font-semibold text-[var(--color-electric-blue)] group-open:hidden">Show</span>
      </summary>
      <div className="mt-2">{body}</div>
    </details>
  );
}

/** The whole deliverable, static: for the exported HTML file. No hooks that need a browser, no
 * handlers — `<details>` does the folding and anchor links do the navigation. */
export function AssetReport({
  text,
  assetId,
  label,
  stageNumber,
  client,
  date,
}: {
  /** The document as the reader shows it — topic suggestions and competitor sources included. */
  text: string;
  assetId?: string;
  label: string;
  stageNumber?: number;
  client?: string;
  date?: string;
}) {
  const doc = parseAssetDocument(text);
  const template = templateFor(assetId);
  const glance = assetGlance(assetId, doc);
  return (
    <VisualOptionsContext.Provider value={{ expandProse: !!template.longForm, staticHtml: true }}>
      <main className="report doc-measure mx-auto w-full px-4 py-8 sm:px-6" style={{ "--doc-measure": "62rem" } as React.CSSProperties}>
        <ReportHero
          label={label}
          template={template}
          stageNumber={stageNumber}
          words={doc.words}
          sections={doc.sections.length}
          client={client}
          date={date}
        />
        {doc.structured && (
          <nav aria-label="Contents" className="mb-6 rounded-2xl border border-[var(--border)] px-4 py-3">
            <div className="mb-1.5 text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">Contents</div>
            <ol className="grid gap-x-6 gap-y-1 text-[0.84rem] sm:grid-cols-2">
              {doc.sections.map((s, i) => (
                <li key={s.id} className="flex gap-2">
                  <span className="w-5 shrink-0 text-right tabular-nums text-[var(--fg-faint)]">{i + 1}</span>
                  <a href={`#${s.id}`} className="min-w-0 hover:underline">
                    {s.label}
                  </a>
                </li>
              ))}
            </ol>
          </nav>
        )}
        {glance && <GlanceView glance={glance} text={text} label={label} />}
        {doc.structured ? (
          doc.sections.map((section, i) => (
            <section key={section.id} id={section.id} className={i > 0 ? "mt-8 border-t border-[var(--border)] pt-6" : ""}>
              <SectionContent section={section} template={template} label={label} />
            </section>
          ))
        ) : (
          <VisualSectionBody body={text} label={label} />
        )}
        <footer className="mt-10 border-t border-[var(--border)] pt-4 text-[0.72rem] text-[var(--fg-faint)]">
          {label}
          {client ? ` · ${client}` : ""}
          {date ? ` · ${date}` : ""} · Marketing-in-a-Box
        </footer>
      </main>
    </VisualOptionsContext.Provider>
  );
}
