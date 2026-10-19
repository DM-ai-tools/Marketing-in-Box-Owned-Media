import { FunnelBuildMenuItem } from "./FunnelBuildMenuItem";
import { PlanMapDownload } from "./PlanMapDownload";
import { SlideDeckMenuItem } from "./SlideDeckMenuItem";

/** The files some assets can be built into besides the document itself: the Plan of Action's mind
 * map, the webinar's slide decks.
 *
 * One component for both places they are offered — rows in the generation card's `⋯` menu, pills in
 * the reader's export row — so the two can never offer different files. Each renders nothing unless
 * its file can actually be built: a plan with no readable map, or a webinar with no parsable Step 5
 * brief, shows no control at all.
 *
 * `text` must be the stage's saved text, not the reader's display text.
 */
export function AssetFileDownloads({
  assetId,
  text,
  label,
  runId,
  variant,
  onDone,
}: {
  assetId?: string;
  text: string;
  label: string;
  runId: string | null;
  variant: "menu" | "pill";
  onDone?: () => void;
}) {
  if (assetId === "plan_of_action") return <PlanMapDownload text={text} label={label} variant={variant} onDone={onDone} />;
  if (assetId === "webinar") return <SlideDeckMenuItem text={text} runId={runId} variant={variant} onDone={onDone} />;
  // The deployable funnel reads both documents off the run, so either card can open it.
  if (assetId === "funnel" || assetId === "funnel_hub_media") return <FunnelBuildMenuItem variant={variant} onDone={onDone} />;
  return null;
}
