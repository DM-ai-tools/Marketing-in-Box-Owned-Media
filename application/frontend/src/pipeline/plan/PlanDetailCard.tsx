import { memo } from "react";
import { Markdown } from "../../components/Markdown";
import { DETAIL_W, NODE_H, type PlacedNode } from "../../lib/planLayout";
import { FAMILY_LABEL, KIND_TAG, familyColor, familyOf } from "../../lib/planFamilies";
import type { PlanNode } from "../../lib/planTree";

/**
 * The selected node's own text, verbatim, as the last card on its branch.
 *
 * This used to be a side panel. Shown here instead, at the end of the connector the operator just
 * followed, the text is read where the plan leads to it, and the map keeps its full width on every
 * screen (the panel took 18-20rem on a desktop and half the height on a phone).
 *
 * `data-node` keeps a drag on the card from panning the map, so text can be selected, and
 * `data-scroll` lets the wheel scroll a long body instead of zooming (see `PlanMindMap`).
 */

interface PlanDetailCardProps {
  placed: PlacedNode;
  owner: PlanNode;
  trail: string[];
  measureRef: (el: HTMLDivElement | null) => void;
  onClose: () => void;
}

function PlanDetailCardImpl({ placed, owner, trail, measureRef, onClose }: PlanDetailCardProps) {
  const family = familyOf(owner.kind);
  const accent = familyColor(family);
  const tag = KIND_TAG[owner.kind];

  return (
    <div
      ref={measureRef}
      data-node={placed.node.id}
      role="region"
      aria-label={`${owner.title}: full text`}
      className="plan-node plan-node-enter absolute left-0 top-0 flex flex-col overflow-hidden rounded-xl border text-left"
      style={{
        transform: `translate(${placed.x}px, ${placed.y}px)`,
        width: DETAIL_W,
        minHeight: NODE_H,
        zIndex: 2,
        backgroundColor: "var(--bg)",
        borderColor: `color-mix(in srgb, ${accent} 60%, var(--border))`,
        boxShadow: `0 0 0 3px color-mix(in srgb, ${accent} 10%, transparent), 0 14px 34px -14px rgb(var(--shadow-color) / 0.4)`,
      }}
    >
      <div
        className="flex items-start gap-2 border-b px-3 py-2"
        style={{
          borderColor: `color-mix(in srgb, ${accent} 25%, var(--border))`,
          backgroundColor: `color-mix(in srgb, ${accent} 6%, var(--bg))`,
        }}
      >
        <div className="min-w-0 flex-1">
          <p className="text-[0.8rem] font-semibold leading-snug [overflow-wrap:anywhere]">{owner.title}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[0.6rem] text-[var(--fg-faint)]">
            {tag && (
              <span
                className="rounded px-1 py-px font-mono tracking-wider"
                style={{ color: accent, backgroundColor: `color-mix(in srgb, ${accent} 12%, transparent)` }}
              >
                {tag}
              </span>
            )}
            <span>{FAMILY_LABEL[family]}</span>
            {owner.slug && <span className="font-mono [overflow-wrap:anywhere]">{owner.slug}</span>}
          </p>
          {trail.length > 0 && (
            <p className="mt-1 text-[0.6rem] leading-snug text-[var(--fg-muted)] [overflow-wrap:anywhere]">{trail.join("  /  ")}</p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close this text"
          className="flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-[0.9rem] leading-none text-[var(--fg-muted)] hover:bg-[var(--hover)] hover:text-[var(--fg)] active:scale-95"
        >
          ×
        </button>
      </div>
      <div
        data-scroll
        className="plan-detail-in doc-measure pane-scroll max-h-[26rem] overflow-y-auto px-3 py-2 text-[0.8rem]"
        style={{ "--md-cover": "var(--bg)", "--doc-measure": "100%" } as React.CSSProperties}
      >
        <Markdown text={owner.body} />
      </div>
    </div>
  );
}

export const PlanDetailCard = memo(PlanDetailCardImpl);
