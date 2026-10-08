import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useReducedMotion } from "framer-motion";
import { ancestorsOf, detailIdFor, layoutPlan, pathToNode, type PlanEdge } from "../lib/planLayout";
import { FAMILY_LABEL, FAMILY_ORDER, familyColor, familyOf } from "../lib/planFamilies";
import { buildPlanTree, flattenPlan } from "../lib/planTree";
import { useMeasuredHeights } from "../lib/useMeasuredHeights";
import { PlanDetailCard } from "./plan/PlanDetailCard";
import { PlanNodeCard } from "./plan/PlanNodeCard";

/**
 * The Plan of Action as an interactive tree, with the document one click away.
 *
 * The plan is the one asset in the pipeline that is a *structure* rather than a piece of writing —
 * three phases, a set of sub-services, a hundred named assets and a month-by-month order to build
 * them in. Read as ten thousand words of prose that structure is present but unusable: you cannot
 * see how many assets Phase 2 implies, or which sub-service ranks first, without holding the whole
 * document in your head.
 *
 * Colour encodes a node's *family* (`lib/planFamilies.ts`): five muted hues for the job an asset
 * does (targeting, content, conversion, nurture, strategy), plus the electric blue structure has
 * always had. Five rather than one per kind, because the monospace tag already names the exact
 * kind and survives a black-and-white print; colour only answers the coarser question an operator
 * scanning a phase asks. It sits on a card's edge bar, tag and connectors, never as a fill, so it
 * cannot be read as one of the three state accents the rest of the UI uses.
 *
 * Cards are as tall as their titles need — measured, not truncated — and a selected card unfolds
 * in place to show what it holds, while the connectors from the root to it carry a travelling
 * signal so the operator can see where in the plan the thing they clicked comes from.
 *
 * The text is never replaced. `Document` switches to the same outline every other asset gets, and
 * the diagram is derived from that document rather than from a second generation — so the two can
 * never disagree, and a plan this cannot draw falls back to the document with a stated reason.
 */

const ZOOM_MIN = 0.3;
const ZOOM_MAX = 1.6;
/** The smallest zoom `Fit` will choose on its own: titles are still legible here. */
const ZOOM_READABLE = 0.72;
const PAD = 16;

/** A dot travelling along one connector. SVG's own animateMotion, so it follows the curve exactly
 * and costs no JavaScript per frame. */
function FlowParticle({ edge, color, delay }: { edge: PlanEdge; color: string; delay: number }) {
  return (
    <circle r={3.2} fill={color}>
      <animateMotion dur="1.3s" begin={`${delay}s`} repeatCount="indefinite" path={edge.path} />
      <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.8;1" dur="1.3s" begin={`${delay}s`} repeatCount="indefinite" />
    </circle>
  );
}

const toolButton =
  "h-7 cursor-pointer rounded-md px-2 text-[0.7rem] text-[var(--fg-muted)] hover:bg-[var(--hover)] hover:text-[var(--fg)] active:scale-[0.97]";

export function PlanMindMap({ text, label }: { text: string; label: string }) {
  const tree = useMemo(() => buildPlanTree(text, label), [text, label]);
  const all = useMemo(() => flattenPlan(tree.root), [tree.root]);
  const byId = useMemo(() => new Map(all.map((n) => [n.id, n])), [all]);
  const reduceMotion = useReducedMotion();

  /* Root and its top-level sections open, everything below closed. Opening the whole tree on mount
   * puts two hundred nodes on screen at a zoom where none of them is readable, which is the wall of
   * text again in a different shape. */
  const initialExpanded = useMemo(
    () => new Set<string>([tree.root.id, ...tree.root.children.map((c) => c.id)]),
    [tree.root],
  );
  const [expanded, setExpanded] = useState<Set<string>>(initialExpanded);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: PAD, y: PAD });
  const [query, setQuery] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const viewport = useRef<HTMLDivElement>(null);
  const dragging = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  /** Once the operator has zoomed or panned, a resize stops re-fitting under them. */
  const userMoved = useRef(false);
  /** A node to bring into view once the layout it is in has been drawn. */
  const pendingFocus = useRef<string | null>(null);

  const { heights, refFor } = useMeasuredHeights();

  useEffect(() => setExpanded(initialExpanded), [initialExpanded]);

  const selected = selectedId ? byId.get(selectedId) : undefined;
  /** The node whose text opens as the last card on its branch: the selection, when it has any. */
  const detailFor = selected && selected.body.trim() ? selected.id : null;
  const layout = useMemo(
    () => layoutPlan(tree.root, expanded, heights, detailFor),
    [tree.root, expanded, heights, detailFor],
  );
  const litTrail = useMemo(() => (selectedId ? pathToNode(tree.root, selectedId) : []), [selectedId, tree.root]);
  const litPath = useMemo(() => {
    const ids = new Set(litTrail);
    // The connector into the text card is part of the lit path, so the signal arrives there.
    if (detailFor) ids.add(detailIdFor(detailFor));
    return ids;
  }, [litTrail, detailFor]);
  const trailTitles = useMemo(
    () => litTrail.slice(1, -1).map((id) => byId.get(id)?.title ?? "").filter(Boolean),
    [litTrail, byId],
  );

  /* Which nodes were on screen last render, so a node that has just appeared (its branch opened)
   * fades in instead of popping. */
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    seen.current = new Set(layout.nodes.map((n) => n.node.id));
  }, [layout.nodes]);

  const families = useMemo(() => {
    const present = new Set(all.map((n) => familyOf(n.kind)));
    return FAMILY_ORDER.filter((f) => present.has(f));
  }, [all]);

  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const closeDetail = useCallback(() => setSelectedId(null), []);

  const select = useCallback((id: string) => {
    setSelectedId((current) => {
      if (current === id) return null;
      pendingFocus.current = id;
      /* Selecting takes over the view, like a zoom or a pan. Otherwise the card growing and its
       * text card opening change the layout, the auto-fit re-runs, and the view jumps back to the
       * whole tree, away from the text the operator just opened. */
      userMoved.current = true;
      return id;
    });
  }, []);

  const fit = useCallback(() => {
    const box = viewport.current;
    if (!box) return;
    /* Fit the width, and the height only while that keeps the text readable. Squeezing a fifty-node
     * plan into a 40rem box to show all of it at once drew it at 30%, where every title is a grey
     * smear: the old truncation problem again by another route. A tall tree is panned instead,
     * starting centred on the root, which is where the plan reads from. */
    const fitWidth = (box.clientWidth - PAD * 2) / Math.max(1, layout.width);
    const fitHeight = (box.clientHeight - PAD * 2) / Math.max(1, layout.height);
    const scale = Math.max(ZOOM_MIN, Math.min(1, fitWidth, Math.max(fitHeight, ZOOM_READABLE)));
    const treeH = layout.height * scale;
    let y = PAD;
    if (treeH > box.clientHeight - PAD * 2) {
      const root = layout.nodes.find((n) => n.node.depth === 0);
      const rootCentre = root ? (root.y + root.h / 2) * scale : treeH / 2;
      y = Math.min(PAD, Math.max(box.clientHeight - PAD - treeH, box.clientHeight / 2 - rootCentre));
    }
    setZoom(scale);
    setPan({ x: PAD, y });
    userMoved.current = false;
  }, [layout]);

  /* Entering or leaving fullscreen changes how much room the tree has, so re-fit it to the new
   * box. Without this the map keeps the zoom it was given for a 22-40rem card and opens full-screen
   * as a small tree marooned in a large empty area — which reads as the button not having worked.
   *
   * On the frame after the layer is painted, because `fit` measures `viewport.clientHeight` and the
   * portalled node has no size until the browser has laid it out.
   *
   * The same effect re-fits as branches open and cards grow, but only until the operator zooms or
   * pans: after that, re-fitting would yank the view away from what they were looking at. */
  const lastFullscreen = useRef(fullscreen);
  useEffect(() => {
    const toggled = lastFullscreen.current !== fullscreen;
    lastFullscreen.current = fullscreen;
    if (userMoved.current && !toggled) return;
    const frame = window.requestAnimationFrame(() => fit());
    return () => window.cancelAnimationFrame(frame);
  }, [fullscreen, fit]);

  /* A phone rotating, a sidebar closing, the window resizing: re-fit, unless the operator has
   * already zoomed or panned and would lose their place. */
  useEffect(() => {
    const box = viewport.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (!userMoved.current) fit();
    });
    ro.observe(box);
    return () => ro.disconnect();
  }, [fit, fullscreen]);

  /* Escape closes it, and the page behind must not scroll while a full-viewport layer is over it —
   * the same two obligations `HtmlPreview` carries for its pop-out. Both are wired only while
   * fullscreen is actually open, so the plain card leaves no listener behind. */
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [fullscreen]);

  /* Bring a freshly selected card into view. It has just grown, so this runs after the layout that
   * includes its new height, and only moves the view when the card is actually off screen. */
  useEffect(() => {
    const id = pendingFocus.current;
    const box = viewport.current;
    if (!id || !box) return;
    // When the node's text opened as a card beside it, that card is what the operator wants to see.
    const placed =
      layout.nodes.find((n) => n.detailOf === id) ?? layout.nodes.find((n) => n.node.id === id);
    if (!placed) return;
    pendingFocus.current = null;
    const left = pan.x + placed.x * zoom;
    const top = pan.y + placed.y * zoom;
    const right = left + placed.w * zoom;
    const bottom = top + placed.h * zoom;
    const margin = 24;
    let dx = 0;
    let dy = 0;
    if (right > box.clientWidth - margin) dx = box.clientWidth - margin - right;
    if (left + dx < margin) dx = margin - left;
    if (bottom > box.clientHeight - margin) dy = box.clientHeight - margin - bottom;
    if (top + dy < margin) dy = margin - top;
    if (dx || dy) setPan((p) => ({ x: p.x + dx, y: p.y + dy }));
  }, [layout, pan.x, pan.y, zoom]);

  const expandAll = () => setExpanded(new Set(all.filter((n) => n.children.length).map((n) => n.id)));
  const collapseAll = () => setExpanded(new Set([tree.root.id]));

  /** Reveal a node: open every branch above it and select it. */
  const reveal = useCallback(
    (id: string) => {
      setExpanded((prev) => new Set([...prev, ...ancestorsOf(tree.root, id)]));
      pendingFocus.current = id;
      userMoved.current = true;
      setSelectedId(id);
    },
    [tree.root],
  );

  const zoomBy = (factor: number) => {
    userMoved.current = true;
    setZoom((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor)));
  };

  const hits = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle.length < 2) return [];
    return all
      .filter((n) => n.id !== "root")
      .filter((n) => n.title.toLowerCase().includes(needle) || n.body.toLowerCase().includes(needle))
      .slice(0, 8);
  }, [query, all]);

  // Wheel to zoom, drag to pan. `passive: false` because the handler calls preventDefault to stop
  // the page scrolling behind the diagram.
  useEffect(() => {
    const box = viewport.current;
    if (!box) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && Math.abs(event.deltaY) < 2) return;
      // Inside a text card that can scroll, the wheel scrolls it. Ctrl+wheel still zooms.
      const scroller = (event.target as HTMLElement).closest?.("[data-scroll]") as HTMLElement | null;
      if (!event.ctrlKey && scroller && scroller.scrollHeight > scroller.clientHeight) return;
      event.preventDefault();
      userMoved.current = true;
      setZoom((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * (event.deltaY < 0 ? 1.08 : 0.92))));
    };
    box.addEventListener("wheel", onWheel, { passive: false });
    return () => box.removeEventListener("wheel", onWheel);
  }, [fullscreen]);

  if (!tree.available) {
    return (
      <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5 text-[0.8rem] text-[var(--fg-muted)]">
        <span className="font-semibold text-[var(--fg)]">No diagram for this plan.</span>{" "}
        {tree.reason} The full document is below, unchanged.
      </div>
    );
  }

  const selectedFamily = selected ? familyOf(selected.kind) : null;
  const flowColor = selectedFamily ? familyColor(selectedFamily) : "var(--color-electric-blue)";
  const animate = !reduceMotion;
  const glide = animate && !isDragging;

  const canvas = (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-sunken)]">
      {/* Header: the plan's headline figures, which are the first thing anyone asks of a plan, then
          the tools. Two rows on a phone, one on a desktop. */}
      <div className="flex flex-col gap-1.5 border-b border-[var(--border)] px-2.5 py-2 lg:flex-row lg:items-center lg:gap-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[0.72rem] text-[var(--fg-muted)]">
          <span className="font-semibold text-[var(--fg)]">{tree.totals.nodes} nodes</span>
          <span>{tree.totals.phases} phases</span>
          <span>{tree.totals.assets} named assets</span>
          {tree.totals.months ? <span>{tree.totals.months}-month timeline</span> : null}
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-1 lg:ml-auto">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Find a node…"
            aria-label="Find a node in the plan"
            className="h-7 min-w-0 flex-1 rounded-full border border-[var(--border-strong)] bg-[var(--bg)] px-3 text-[0.72rem] outline-none focus:border-[var(--color-electric-blue)] sm:w-44 sm:flex-none"
          />
          <button type="button" onClick={expandAll} className={toolButton}>
            Expand all
          </button>
          <button type="button" onClick={collapseAll} className={toolButton}>
            Collapse
          </button>
          <button
            type="button"
            onClick={() => setFullscreen((v) => !v)}
            className={`${toolButton} font-semibold text-[var(--fg)]`}
          >
            {fullscreen ? "Exit fullscreen" : "Fullscreen"}
          </button>
        </div>
      </div>

      {/* Legend: only the families this plan actually contains. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-[var(--border)] px-2.5 py-1.5 text-[0.64rem] text-[var(--fg-muted)]">
        {families.map((f) => (
          <span key={f} className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-2.5 w-[3px] rounded-full" style={{ backgroundColor: familyColor(f) }} />
            {FAMILY_LABEL[f]}
          </span>
        ))}
        <span className="text-[var(--fg-faint)] sm:ml-auto">
          {selected ? "Its full text is the last card on the branch." : "Click any node to read that step in full."}
        </span>
      </div>

      {hits.length > 0 && (
        <div className="flex flex-wrap gap-1 border-b border-[var(--border)] px-2.5 py-1.5">
          {hits.map((hit) => (
            <button
              key={hit.id}
              type="button"
              onClick={() => {
                reveal(hit.id);
                setQuery("");
              }}
              title={hit.title}
              className="max-w-[16rem] cursor-pointer truncate rounded-full border px-2 py-[2px] text-[0.68rem] hover:bg-[var(--hover)]"
              style={{ borderColor: `color-mix(in srgb, ${familyColor(familyOf(hit.kind))} 45%, var(--border))` }}
            >
              {hit.title}
            </button>
          ))}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div
          ref={viewport}
          className="relative min-h-0 flex-1 cursor-grab touch-none overflow-hidden active:cursor-grabbing"
          style={{
            backgroundImage: "radial-gradient(circle, var(--border) 1px, transparent 1px)",
            backgroundSize: `${22 * zoom}px ${22 * zoom}px`,
            backgroundPosition: `${pan.x}px ${pan.y}px`,
          }}
          onPointerDown={(e) => {
            if ((e.target as HTMLElement).closest("[data-node]")) return;
            dragging.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
            setIsDragging(true);
            (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          }}
          onPointerMove={(e) => {
            const drag = dragging.current;
            if (!drag) return;
            userMoved.current = true;
            setPan({ x: drag.panX + (e.clientX - drag.x), y: drag.panY + (e.clientY - drag.y) });
          }}
          onPointerUp={() => {
            dragging.current = null;
            setIsDragging(false);
          }}
          onPointerLeave={() => {
            dragging.current = null;
            setIsDragging(false);
          }}
        >
          <div
            className="absolute left-0 top-0 origin-top-left"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transition: glide ? "transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)" : undefined,
              width: layout.width,
              height: layout.height,
            }}
          >
            <svg width={layout.width} height={layout.height} className="absolute left-0 top-0 overflow-visible" aria-hidden>
              {layout.edges.map((edge) => {
                const lit = litPath.has(edge.fromId) && litPath.has(edge.toId);
                const outflow = !!selectedId && edge.fromId === selectedId;
                const child = byId.get(edge.toId);
                const tint = child ? familyColor(familyOf(child.kind)) : "var(--border-strong)";
                return (
                  <path
                    key={edge.id}
                    d={edge.path}
                    fill="none"
                    strokeLinecap="round"
                    strokeWidth={lit ? 2.2 : outflow ? 1.6 : 1.1}
                    stroke={
                      lit
                        ? flowColor
                        : outflow
                          ? `color-mix(in srgb, ${tint} 70%, var(--border-strong))`
                          : `color-mix(in srgb, ${tint} 28%, var(--border-strong))`
                    }
                    /* The data-flow animation, and only on the branch leading to the selected node.
                     * Animating every edge at once is decoration that competes with the content;
                     * animating the one path the operator just asked about shows the plan flowing
                     * from the root to the thing they clicked. */
                    className={lit && animate ? "plan-flow" : undefined}
                  />
                );
              })}
              {/* Travelling signals: down the trail from the root, staggered by depth so they read
                  as one wave arriving at the selected card, then out along its open children. */}
              {animate &&
                selectedId &&
                layout.edges.map((edge) => {
                  const step = litTrail.indexOf(edge.toId);
                  if (step > 0 && litTrail[step - 1] === edge.fromId) {
                    return <FlowParticle key={`p-${edge.id}`} edge={edge} color={flowColor} delay={(step - 1) * 0.22} />;
                  }
                  if (edge.fromId === selectedId) {
                    const child = byId.get(edge.toId);
                    return (
                      <FlowParticle
                        key={`p-${edge.id}`}
                        edge={edge}
                        color={child ? familyColor(familyOf(child.kind)) : flowColor}
                        delay={(litTrail.length - 1) * 0.22}
                      />
                    );
                  }
                  return null;
                })}
            </svg>

            {layout.nodes.map((placed) =>
              placed.detailOf ? (
                <PlanDetailCard
                  key={placed.node.id}
                  placed={placed}
                  owner={byId.get(placed.detailOf) ?? placed.node}
                  trail={trailTitles}
                  measureRef={refFor(placed.node.id)}
                  onClose={closeDetail}
                />
              ) : (
              <PlanNodeCard
                key={placed.node.id}
                placed={placed}
                isSelected={placed.node.id === selectedId}
                onPath={litPath.has(placed.node.id)}
                isNew={!!seen.current && !seen.current.has(placed.node.id)}
                measureRef={refFor(placed.node.id)}
                onToggle={toggle}
                onSelect={select}
                onReveal={reveal}
              />
              ),
            )}
          </div>

          {/* Zoom controls for touch screens, where there is no wheel. */}
          <div className="absolute bottom-2 right-2 flex items-center gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--bg)] p-0.5 shadow-sm">
            <span className="hidden px-2 text-[0.62rem] text-[var(--fg-faint)] lg:inline">Drag to pan, scroll to zoom</span>
            <button type="button" onClick={() => zoomBy(0.85)} aria-label="Zoom out" className={`${toolButton} w-7 px-0 text-[0.85rem]`}>
              −
            </button>
            <span className="w-10 text-center text-[0.64rem] tabular-nums text-[var(--fg-muted)]">{Math.round(zoom * 100)}%</span>
            <button type="button" onClick={() => zoomBy(1.18)} aria-label="Zoom in" className={`${toolButton} w-7 px-0 text-[0.85rem]`}>
              +
            </button>
            <button type="button" onClick={fit} className={toolButton}>
              Fit
            </button>
          </div>
        </div>

      </div>
    </div>
  );

  if (fullscreen) {
    const layer = (
      <div className="fixed inset-0 z-50 flex flex-col gap-2 bg-[var(--bg)] p-2 sm:p-3">
        <p className="shrink-0 truncate text-[0.8rem] font-semibold">{label}: plan map</p>
        {canvas}
      </div>
    );

    // Portalled to `<body>`, and that is the whole fix rather than a tidiness preference.
    //
    // `position: fixed` resolves against the viewport *only* while no ancestor establishes a
    // containing block, and in this transcript two separate ancestors do:
    //
    //   * the transcript pane is a Tailwind `@container` (`container-type: inline-size`), which
    //     applies containment;
    //   * every card carries `.msg-rise`, whose `animation-fill-mode: both` leaves the final
    //     keyframe's `transform: translateY(0)` applied for good — a transform, therefore a
    //     containing block, permanently and not just for the 0.28s the animation runs.
    //
    // So `inset-0` pinned the "fullscreen" map to the *card*, and since the transcript column is
    // narrower than the map's normal box, pressing Fullscreen made the tree smaller — which is
    // exactly what it looked like. Rendering into `<body>` escapes both ancestors, and is the same
    // treatment `HtmlPreview` already needed for its pop-out.
    //
    // Rendered in place when there is no document to portal into, which is where `smoke/` runs.
    return typeof document === "undefined" ? layer : createPortal(layer, document.body);
  }

  return <div className="mt-2 flex h-[clamp(26rem,72dvh,46rem)] flex-col">{canvas}</div>;
}
