import { useCallback, useEffect, useRef, useState } from "react";
import { buildAssetExport, downloadExport, shareExport } from "./exportAsset";
import { splitHtmlBlocks } from "./htmlBlocks";
import { usePipelineStore } from "../pipeline/pipelineStore";

/**
 * Download and Share for one generated asset, without a presentation.
 *
 * A hook in `lib/` rather than a third export from `components/AssetExportButtons.tsx`, because
 * Fast Refresh only re-renders a module that exports components alone — mixing a hook in makes
 * every edit to that file a full reload.
 *
 * The two actions have two presentations: a pair of pills beside Save (`AssetExportButtons`) and
 * two rows inside a card's `⋯` menu (`AssetExportMenuItems`). Both call this, so the file naming,
 * the share-then-copy fallback and the transient confirmation labels are decided once.
 */

export interface AssetExportTarget {
  text: string;
  label: string;
  stageNumber?: number;
  /** See `buildAssetExport`. A function is read at click time, so a card can offer the transcript's
   * topic suggestions without subscribing to the whole transcript to have them ready. */
  preamble?: string | (() => string);
  /** Where the preamble is placed in the document. */
  assetId?: string;
  /** See `buildAssetExport`. Read at click time, like `preamble`. */
  appendix?: string | (() => string);
}

/** A transient button label ("Downloaded", "Copied") that reverts on its own, without leaving a
 * timer running against an unmounted card. */
function useFlash(revertAfterMs = 1800) {
  const [flash, setFlash] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return [
    flash,
    useCallback(
      (label: string) => {
        setFlash(label);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setFlash(null), revertAfterMs);
      },
      [revertAfterMs],
    ),
  ] as const;
}

function resolve(value: string | (() => string) | undefined): string | undefined {
  return typeof value === "function" ? value() : value;
}

export function useAssetExport({ text, label, stageNumber, preamble, assetId, appendix }: AssetExportTarget) {
  const [downloadFlash, flashDownload] = useFlash();
  const [shareFlash, flashShare] = useFlash();
  const [reportFlash, flashReport] = useFlash();

  const download = useCallback(() => {
    downloadExport(buildAssetExport({ text, label, stageNumber, preamble: resolve(preamble), assetId, appendix: resolve(appendix) }));
    flashDownload("Downloaded");
  }, [text, label, stageNumber, preamble, assetId, appendix, flashDownload]);

  const share = useCallback(() => {
    void shareExport(buildAssetExport({ text, label, stageNumber, preamble: resolve(preamble), assetId, appendix: resolve(appendix) }), label)
      .then((outcome) => {
        if (outcome === "shared") flashShare("Shared");
        else if (outcome === "copied") flashShare("Copied");
        // "cancelled" — the user dismissed the share sheet; say nothing.
      })
      .catch(() => flashShare("Couldn't share"));
  }, [text, label, stageNumber, preamble, assetId, appendix, flashShare]);

  // The designed HTML report. Loaded on click: the renderer it needs is not part of the app's first
  // load. The client's name is read at click time, like the preamble.
  const downloadReport = useCallback(() => {
    flashReport("Preparing…");
    void import("./assetReportHtml")
      .then(({ buildAssetReportExport }) =>
        buildAssetReportExport({
          text,
          label,
          stageNumber,
          preamble: resolve(preamble),
          appendix: resolve(appendix),
          assetId,
          client: usePipelineStore.getState().clientProfile.client_name || undefined,
        }),
      )
      .then((exported) => {
        downloadExport(exported);
        flashReport("Downloaded");
      })
      .catch(() => flashReport("Couldn't build"));
  }, [text, label, stageNumber, preamble, assetId, appendix, flashReport]);

  // A document that is only an HTML page already downloads as that page.
  const segments = splitHtmlBlocks(text);
  const canReport = !(segments.length === 1 && segments[0].kind === "html");

  return { download, share, downloadReport, canReport, downloadFlash, shareFlash, reportFlash };
}
