import { motion } from "framer-motion";
import { useAssetExport, type AssetExportTarget } from "../lib/useAssetExport";

function DownloadIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5M4 20h16"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ReportIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M7 3h7l5 5v12a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1zm7 0v5h5M9 13h6M9 17h4"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ShareIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v5a1 1 0 001 1h12a1 1 0 001-1v-5"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function QuietButton({
  onClick,
  children,
  label,
}: {
  onClick: () => void;
  children: React.ReactNode;
  label: string;
}) {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      aria-label={label}
      whileHover={{ backgroundColor: "var(--hover)" }}
      whileTap={{ scale: 0.97 }}
      className="flex min-h-10 cursor-pointer items-center gap-1.5 rounded-full border border-[var(--border-strong)] px-3 py-1.5
        text-[0.78rem] font-medium text-[var(--fg-muted)] sm:min-h-0"
    >
      {children}
    </motion.button>
  );
}

/** Download / Share for one generated asset, sized to sit beside Save and Refine.
 *
 * Deliberately available before *and* after the asset is saved. Saving files it to the Context
 * Store for the next stage to read, which is a different job from getting it out to a client — and
 * an operator who wants the file is just as likely to want it from an approved asset as a draft. */
export function AssetExportButtons({ text, label, stageNumber, preamble, assetId, appendix }: AssetExportTarget) {
  const { download, share, downloadReport, canReport, downloadFlash, shareFlash, reportFlash } = useAssetExport({
    text,
    label,
    stageNumber,
    preamble,
    assetId,
    appendix,
  });

  return (
    <>
      {canReport && (
        <QuietButton onClick={downloadReport} label={`Download ${label} as a designed HTML report`}>
          <ReportIcon />
          {reportFlash ?? "Report (.html)"}
        </QuietButton>
      )}
      <QuietButton onClick={download} label={`Download ${label}`}>
        <DownloadIcon />
        {downloadFlash ?? "Markdown"}
      </QuietButton>
      <QuietButton onClick={share} label={`Share ${label}`}>
        <ShareIcon />
        {shareFlash ?? "Share"}
      </QuietButton>
    </>
  );
}

/** The same two actions as rows in an `OverflowMenu`.
 *
 * `onDone` fires after either action so the caller can dismiss the menu. The transient label is
 * still rendered, which matters more here than in the pill row: the menu closes on the click, so
 * "Downloaded" is the only confirmation the operator would otherwise get — and they will not see
 * it. Hence the download row stays open long enough to show it by not closing on download. */
export function AssetExportMenuItems({
  text,
  label,
  stageNumber,
  preamble,
  assetId,
  appendix,
  onDone,
}: AssetExportTarget & { onDone?: () => void }) {
  const { download, share, downloadReport, canReport, downloadFlash, shareFlash, reportFlash } = useAssetExport({
    text,
    label,
    stageNumber,
    preamble,
    assetId,
    appendix,
  });

  return (
    <>
      {canReport && (
        <button
          type="button"
          role="menuitem"
          onClick={downloadReport}
          className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[0.8rem] font-medium hover:bg-[var(--hover)]"
        >
          <ReportIcon />
          {reportFlash ?? "Download report (.html)"}
        </button>
      )}
      <button
        type="button"
        role="menuitem"
        onClick={download}
        className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[0.8rem] font-medium hover:bg-[var(--hover)]"
      >
        <DownloadIcon />
        {downloadFlash ?? "Download"}
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          share();
          onDone?.();
        }}
        className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[0.8rem] font-medium hover:bg-[var(--hover)]"
      >
        <ShareIcon />
        {shareFlash ?? "Share"}
      </button>
    </>
  );
}
