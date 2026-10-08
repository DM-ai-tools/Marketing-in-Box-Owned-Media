import { memo } from "react";
import { NODE_H, NODE_W, type PlacedNode } from "../../lib/planLayout";
import { KIND_TAG, familyColor, familyOf } from "../../lib/planFamilies";
import type { PlanNode } from "../../lib/planTree";

/**
 * One node of the plan map.
 *
 * Its height is whatever its text needs: the title wraps instead of truncating, and the layout
 * measures the card (`PlanMindMap`'s ResizeObserver) and re-flows the column around it. Selected,
 * it unfolds to show its size, URL and children, and its own text opens as one more card at the
 * end of its branch (`PlanDetailCard`).
 */

const CHILD_PREVIEW = 5;

function Stars({ value }: { value: number }) {
  return (
    <span className="shrink-0 text-[0.6rem] tabular-nums text-[var(--fg-faint)]" title={`${value} of 5`}>
      {"★".repeat(value)}
      {"☆".repeat(Math.max(0, 5 - value))}
    </span>
  );
}

interface PlanNodeCardProps {
  placed: PlacedNode;
  isSelected: boolean;
  onPath: boolean;
  isNew: boolean;
  measureRef: (el: HTMLDivElement | null) => void;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  onReveal: (id: string) => void;
}

function PlanNodeCardImpl({ placed, isSelected, onPath, isNew, measureRef, onToggle, onSelect, onReveal }: PlanNodeCardProps) {
  const { node, x, y, collapsed, hasChildren } = placed;
  const family = familyOf(node.kind);
  const accent = familyColor(family);
  const structural = family === "structure";
  const tag = KIND_TAG[node.kind];

  return (
    <div
      ref={measureRef}
      data-node={node.id}
      className={[
        "plan-node absolute left-0 top-0 overflow-hidden rounded-xl border text-left",
        isNew ? "plan-node-enter" : "",
        isSelected ? "plan-selected-pulse" : "",
      ].join(" ")}
      style={
        {
          "--plan-accent": accent,
          transform: `translate(${x}px, ${y}px)`,
          width: NODE_W,
          minHeight: NODE_H,
          zIndex: isSelected ? 2 : onPath ? 1 : undefined,
          backgroundColor: isSelected ? `color-mix(in srgb, ${accent} 7%, var(--bg))` : "var(--bg)",
          borderColor: isSelected
            ? accent
            : onPath
              ? `color-mix(in srgb, ${accent} 55%, var(--border))`
              : "var(--border)",
          boxShadow: isSelected
            ? `0 0 0 3px color-mix(in srgb, ${accent} 16%, transparent), 0 10px 28px -12px rgb(var(--shadow-color) / 0.35)`
            : "0 1px 2px rgb(var(--shadow-color) / 0.06)",
        } as React.CSSProperties
      }
    >
      {/* The family bar: the one place colour sits on an unselected card. */}
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ backgroundColor: accent }} />

      <div className="flex items-start gap-1.5 py-2 pl-3 pr-2">
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(node.id)}
            aria-label={collapsed ? `Expand ${node.title}` : `Collapse ${node.title}`}
            aria-expanded={!collapsed}
            className="mt-px flex h-4 w-4 shrink-0 cursor-pointer items-center justify-center rounded border border-[var(--border-strong)] text-[0.62rem] font-bold leading-none text-[var(--fg-muted)] hover:bg-[var(--hover)] active:scale-95"
          >
            {collapsed ? "+" : "−"}
          </button>
        ) : (
          <span aria-hidden className="mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: accent, opacity: 0.6 }} />
        )}

        <button
          type="button"
          onClick={() => onSelect(node.id)}
          aria-pressed={isSelected}
          className="min-w-0 flex-1 cursor-pointer text-left"
        >
          <span
            className="block text-[0.76rem] font-semibold leading-[15px] [overflow-wrap:anywhere]"
            style={structural ? { color: accent } : undefined}
          >
            {node.title}
          </span>
          <span className="mt-[3px] flex flex-wrap items-center gap-x-1.5 gap-y-0.5 leading-[12px]">
            {tag && (
              <span
                className="rounded px-1 py-px font-mono text-[0.55rem] tracking-wider"
                style={{ color: accent, backgroundColor: `color-mix(in srgb, ${accent} 12%, transparent)` }}
              >
                {tag}
              </span>
            )}
            {node.volume ? (
              <span className="text-[0.58rem] tabular-nums text-[var(--fg-faint)]">~{node.volume.toLocaleString()}/mo</span>
            ) : null}
            {node.stars ? <Stars value={node.stars} /> : null}
          </span>
        </button>

        {collapsed && node.count > 0 && (
          <span className="mt-px shrink-0 rounded-full bg-[var(--bg-sunken)] px-1.5 text-[0.58rem] tabular-nums text-[var(--fg-muted)]">
            {node.count}
          </span>
        )}
      </div>

      {isSelected && <NodeDetail node={node} accent={accent} collapsed={collapsed} hasChildren={hasChildren} onToggle={onToggle} onReveal={onReveal} />}
    </div>
  );
}

function NodeDetail({
  node,
  accent,
  collapsed,
  hasChildren,
  onToggle,
  onReveal,
}: {
  node: PlanNode;
  accent: string;
  collapsed: boolean;
  hasChildren: boolean;
  onToggle: (id: string) => void;
  onReveal: (id: string) => void;
}) {
  const shown = node.children.slice(0, CHILD_PREVIEW);
  const more = node.children.length - shown.length;
  // The node's own text is not repeated here: it opens in full as the last card on this branch
  // (`PlanDetailCard`). What stays is what that card does not show: where the branch goes next.
  const isHeading = !node.body.trim();
  if (!isHeading && !node.count && !node.slug) return null;

  return (
    <div className="plan-detail-in border-t px-3 pb-2.5 pt-2" style={{ borderColor: `color-mix(in srgb, ${accent} 25%, var(--border))` }}>
      {isHeading && (
        <p className="mb-2 text-[0.7rem] leading-[1.45] text-[var(--fg-muted)]">
          A heading. Its detail is in the {node.count} node{node.count === 1 ? "" : "s"} beneath it.
        </p>
      )}

      {(node.count > 0 || node.slug) && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[0.62rem]">
          {node.count > 0 && (
            <>
              <dt className="text-[var(--fg-faint)]">Inside</dt>
              <dd className="tabular-nums">
                {node.children.length} direct, {node.count} total
              </dd>
            </>
          )}
          {node.slug && (
            <>
              <dt className="text-[var(--fg-faint)]">URL</dt>
              <dd className="font-mono [overflow-wrap:anywhere]">{node.slug}</dd>
            </>
          )}
        </dl>
      )}

      {shown.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {shown.map((child) => (
            <button
              key={child.id}
              type="button"
              onClick={() => onReveal(child.id)}
              title={child.title}
              className="max-w-full cursor-pointer truncate rounded-full border px-1.5 py-px text-[0.6rem] hover:bg-[var(--hover)] active:scale-[0.98]"
              style={{ borderColor: `color-mix(in srgb, ${familyColor(familyOf(child.kind))} 40%, var(--border))` }}
            >
              {child.title}
            </button>
          ))}
          {more > 0 && <span className="px-1 text-[0.6rem] text-[var(--fg-faint)]">+{more} more</span>}
        </div>
      )}

      {hasChildren && (
        <button
          type="button"
          onClick={() => onToggle(node.id)}
          className="mt-2 cursor-pointer text-[0.64rem] font-semibold hover:underline"
          style={{ color: accent }}
        >
          {collapsed ? "Open this branch" : "Close this branch"}
        </button>
      )}
    </div>
  );
}

export const PlanNodeCard = memo(PlanNodeCardImpl);
