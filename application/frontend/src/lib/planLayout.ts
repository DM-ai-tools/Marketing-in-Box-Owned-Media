import type { PlanNode } from "./planTree";

/**
 * Tidy horizontal tree layout: root on the left, one column per depth.
 *
 * Horizontal rather than the radial mind map the word suggests, because the labels are sentences.
 * A radial layout puts them at every angle, which either rotates the text or overlaps it, and a
 * plan's node titles ("CRO Audit & Enhanced Pillar Page Content") are the content — not decoration
 * around a shape. Left-to-right also matches how the plan reads: root, phases, assets, details.
 *
 * The algorithm is the standard simplification of Reingold-Tilford: walk the visible tree in order,
 * give every leaf the next free row, and place every parent at the mean of its children's rows.
 * That is O(n), never overlaps, and keeps siblings contiguous — which is what makes a phase's
 * assets read as one group.
 */

export const NODE_W = 236;
/** The *minimum* node height. A node is as tall as its text needs — see `estimateNodeHeight`. */
export const NODE_H = 44;
/** Column pitch. The gap is where the connector curve lives. */
export const COL_W = NODE_W + 48;
/** Vertical gap between two nodes in the same column. */
export const ROW_GAP = 12;
/** Smallest row pitch: a one-line node plus the gap. */
export const ROW_H = NODE_H + ROW_GAP;

/**
 * How tall a node's card has to be to show its whole title, before the browser has measured it.
 *
 * Every node used to be a fixed 208x40 box with a single `truncate`d line, so a title like "CRO
 * Audit & Enhanced Pillar Page Content for Single-Tooth Implants" was cut to its first half and
 * the only way to read it was the tooltip. Titles *are* the content of this diagram, so a node now
 * wraps and grows instead. The real height is measured off the DOM (`PlanMindMap`); this estimate
 * is what the first frame, and server rendering, lay out with. It deliberately overestimates a
 * little — a card with a few spare pixels looks fine, a card one line short clips text.
 */
export function estimateNodeHeight(node: PlanNode): number {
  // ~0.76rem semibold at 236px wide, minus the toggle, padding and count badge.
  const charsPerLine = 27;
  const words = node.title.split(/\s+/);
  let lines = 1;
  let used = 0;
  for (const word of words) {
    const len = word.length;
    if (used && used + 1 + len > charsPerLine) {
      lines += 1 + Math.floor(len / (charsPerLine + 1));
      used = len % charsPerLine;
    } else {
      used += (used ? 1 : 0) + len;
    }
  }
  // 16px vertical padding + 15px per title line + the tag/metric row.
  return Math.max(NODE_H, 16 + lines * 15 + 15);
}

export interface PlacedNode {
  node: PlanNode;
  x: number;
  y: number;
  /** This node's own height — measured when the caller supplied one, estimated otherwise. */
  h: number;
  /** This node's width: `NODE_W`, or `DETAIL_W` for a detail card. */
  w: number;
  /** Whether this node has children that are currently hidden. */
  collapsed: boolean;
  hasChildren: boolean;
  /** Set on the detail card only: the id of the node whose text it shows. */
  detailOf?: string;
}

/** The full-text card that opens as the last child of the selected node. Wider than a node,
 * because it holds paragraphs and tables rather than a title. */
export const DETAIL_W = 400;
/** Its height before the browser has measured it. */
const DETAIL_H_ESTIMATE = 180;

export function detailIdFor(id: string): string {
  return `${id}::detail`;
}

export interface PlanEdge {
  id: string;
  fromId: string;
  toId: string;
  /** Cubic bezier `d`, left edge of the parent's column to the child's left edge. */
  path: string;
}

export interface PlanLayout {
  nodes: PlacedNode[];
  edges: PlanEdge[];
  width: number;
  height: number;
}

function edgePath(px: number, py: number, ph: number, cx: number, cy: number, ch: number): string {
  const x1 = px + NODE_W;
  const y1 = py + ph / 2;
  const y2 = cy + ch / 2;
  // Control points half way across the gap, so the curve leaves and arrives horizontally. A
  // straight line would cross neighbouring nodes whenever a parent has many children.
  const mid = x1 + (cx - x1) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${cx} ${y2}`;
}

/**
 * Place every visible node.
 *
 * `expanded` holds the ids whose children are shown. A node absent from it is collapsed, so the
 * default — an empty set — is a fully collapsed tree; callers seed it with the depths they want
 * open. Storing the *open* set rather than the closed one means a node added by a later
 * regeneration is closed by default rather than appearing already expanded.
 */
export function layoutPlan(
  root: PlanNode,
  expanded: Set<string>,
  heights?: ReadonlyMap<string, number>,
  detailFor?: string | null,
): PlanLayout {
  const nodes: PlacedNode[] = [];
  const byId = new Map<string, PlacedNode>();
  const links: [string, string][] = [];

  /* The detail card is laid out as one more child, after the node's real ones, rather than drawn
   * over the map. That gives it a vertical band of its own, so however long the text is it pushes
   * the rest of the column down instead of covering it, and its extra width can never reach a
   * card in the next column: every node right of it in that band would be its own descendant, and
   * it has none. */
  const placeDetail = (owner: PlanNode): number => {
    const id = detailIdFor(owner.id);
    const h = Math.max(NODE_H, heights?.get(id) ?? DETAIL_H_ESTIMATE);
    const node: PlanNode = {
      id,
      title: owner.title,
      kind: "detail",
      depth: owner.depth + 1,
      body: owner.body,
      children: [],
      count: 0,
    };
    const placed: PlacedNode = { node, x: node.depth * COL_W, y: cursor, h, w: DETAIL_W, collapsed: false, hasChildren: false, detailOf: owner.id };
    cursor += h + ROW_GAP;
    nodes.push(placed);
    byId.set(id, placed);
    links.push([owner.id, id]);
    return placed.y + h / 2;
  };
  /** Top of the next free slot. Leaves stack downwards from here, each as tall as its own text. */
  let cursor = 0;

  const heightOf = (node: PlanNode) => Math.max(NODE_H, heights?.get(node.id) ?? estimateNodeHeight(node));

  /** Returns the node's vertical centre, which is what a parent centres itself on. */
  const place = (node: PlanNode): number => {
    const hasChildren = node.children.length > 0;
    const open = hasChildren && expanded.has(node.id);
    const x = node.depth * COL_W;
    const h = heightOf(node);
    const start = cursor;
    const firstIndex = nodes.length;

    const withDetail = detailFor === node.id;

    let y: number;
    if (!open && !withDetail) {
      y = cursor;
      cursor += h + ROW_GAP;
    } else {
      const centres = open
        ? node.children.map((child) => {
            links.push([node.id, child.id]);
            return place(child);
          })
        : [];
      if (withDetail) centres.push(placeDetail(node));
      y = (centres[0] + centres[centres.length - 1]) / 2 - h / 2;
      /* A parent taller than the span of its children would poke above the slot its subtree
       * started in and overlap the sibling above it — which the fixed-height layout never had to
       * consider. Push the whole subtree down instead, then make sure the next sibling clears it. */
      if (y < start) {
        const shift = start - y;
        for (let i = firstIndex; i < nodes.length; i++) nodes[i].y += shift;
        y = start;
        cursor += shift;
      }
      cursor = Math.max(cursor, y + h + ROW_GAP);
    }

    const placed: PlacedNode = { node, x, y, h, w: NODE_W, collapsed: hasChildren && !open, hasChildren };
    nodes.push(placed);
    byId.set(node.id, placed);
    return y + h / 2;
  };

  place(root);

  // Edges last, from final positions: a subtree may have been shifted after its edges' ends were
  // first known.
  const edges: PlanEdge[] = [];
  for (const [fromId, toId] of links) {
    const parent = byId.get(fromId);
    const child = byId.get(toId);
    if (!parent || !child) continue;
    edges.push({
      id: `${fromId}-${toId}`,
      fromId,
      toId,
      path: edgePath(parent.x, parent.y, parent.h, child.x, child.y, child.h),
    });
  }

  const width = Math.max(...nodes.map((n) => n.x + n.w), NODE_W) + 8;
  const height = Math.max(...nodes.map((n) => n.y + n.h), NODE_H) + 8;
  return { nodes, edges, width, height };
}

/** The chain of ids from the root down to `id`, for highlighting the path to the selected node. */
export function pathToNode(root: PlanNode, id: string): string[] {
  const trail: string[] = [];
  const walk = (node: PlanNode): boolean => {
    trail.push(node.id);
    if (node.id === id) return true;
    for (const child of node.children) if (walk(child)) return true;
    trail.pop();
    return false;
  };
  walk(root);
  return trail;
}

/** Every ancestor id of `id`, so revealing a search hit can open the branches above it. */
export function ancestorsOf(root: PlanNode, id: string): string[] {
  const trail = pathToNode(root, id);
  return trail.slice(0, -1);
}
