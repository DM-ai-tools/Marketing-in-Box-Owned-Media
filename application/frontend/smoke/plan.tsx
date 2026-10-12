/* The Plan of Action tree and its diagram. Not shipped — see smoke/README.md. */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { PlanMindMap } from "../src/pipeline/PlanMindMap";
import { buildPlanMindMapHtml } from "../src/lib/planMindMapHtml";
import { DETAIL_W, detailIdFor, estimateNodeHeight, layoutPlan, NODE_H, NODE_W, ROW_GAP, ROW_H } from "../src/lib/planLayout";
import { plainExcerpt } from "../src/lib/planFamilies";
import { buildPlanTree, flattenPlan, type PlanNode } from "../src/lib/planTree";
import { DEMO_PLAN } from "../src/pipeline/planFixture";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const tree = buildPlanTree(DEMO_PLAN, "Sample Dental");
const all = flattenPlan(tree.root);
const byTitle = (needle: string): PlanNode | undefined =>
  all.find((n) => n.title.toLowerCase().includes(needle.toLowerCase()));

const kids = (node: PlanNode | undefined) => (node?.children ?? []).map((c) => c.title);

/* ---------- top level ---------- */
ok("tree: available", tree.available, tree.reason);
ok("tree: ten top-level sections", tree.root.children.length === 10, kids(tree.root).join(" | "));
ok(
  "tree: sections are the declared ten",
  ["Executive Summary", "Current State", "Phase 1", "Phase 2", "Phase 3", "Total Asset Summary"].every((t) =>
    kids(tree.root).some((k) => k.includes(t)),
  ),
  kids(tree.root).join(" | "),
);

/* ---------- the numbering collision ----------
 * Phase 2's sub-services are written `## 1. Single-Tooth Implants` — the same `N.` form the
 * top-level sections use. They must land *under* Phase 2, not beside it. */
{
  const phase2 = byTitle("Phase 2");
  ok("phase 2: found", !!phase2);
  ok("phase 2: is a phase", phase2?.kind === "phase", phase2?.kind);
  ok("phase 2: has both sub-services as children", (phase2?.children.length ?? 0) === 2, kids(phase2).join(" | "));
  ok("phase 2: sub-services named", kids(phase2).some((k) => k.includes("Single-Tooth")), kids(phase2).join(" | "));
  ok(
    "phase 2: sub-services did NOT become top-level sections",
    !tree.root.children.some((c) => c.title.includes("Single-Tooth")),
    kids(tree.root).join(" | "),
  );
}

/* ---------- Phase 1's eight numbered assets ---------- */
{
  const phase1 = byTitle("Phase 1");
  ok("phase 1: found", !!phase1);
  const assetChildren = (phase1?.children ?? []).filter((c) => c.kind !== "funnel");
  ok("phase 1: eight assets plus the funnel flow", (phase1?.children.length ?? 0) === 9, kids(phase1).join(" | "));
  ok("phase 1: assets carry real titles", assetChildren.some((c) => c.title.includes("CRO Audit")), kids(phase1).join(" | "));
  ok("phase 1: the funnel flow is tagged a funnel", (phase1?.children ?? []).some((c) => c.kind === "funnel"));
}

/* ---------- Phase 3's lettered categories ---------- */
{
  const phase3 = byTitle("Phase 3");
  ok("phase 3: eight categories plus the funnels group", (phase3?.children.length ?? 0) === 9, kids(phase3).join(" | "));
  ok("phase 3: keeps the letter prefix", kids(phase3).some((k) => k.startsWith("A.")), kids(phase3).join(" | "));
  ok("phase 3: location category tagged", (phase3?.children ?? []).some((c) => c.kind === "location"));
  ok("phase 3: case studies tagged", (phase3?.children ?? []).some((c) => c.kind === "case-study"));
  ok("phase 3: video tagged", (phase3?.children ?? []).some((c) => c.kind === "video"));
}

/* ---------- the sub-service schema becomes children ---------- */
{
  const single = byTitle("Single-Tooth Implants");
  ok("sub-service: found", !!single);
  ok("sub-service: slug read", single?.slug === "/implants/single-tooth", single?.slug);
  ok("sub-service: volume read", single?.volume === 2400, String(single?.volume));
  ok("sub-service: schema labels became children", (single?.children.length ?? 0) >= 5, kids(single).join(" | "));
  const labels = kids(single).join(" | ");
  ok("sub-service: keyword cluster present", labels.includes("TARGET KEYWORD CLUSTER"), labels);
  ok("sub-service: blog posts present", labels.includes("BLOG POST 1"), labels);
  ok("sub-service: lead magnet tagged", (single?.children ?? []).some((c) => c.kind === "lead-magnet"), labels);
  ok("sub-service: funnel tagged", (single?.children ?? []).some((c) => c.kind === "funnel"), labels);
}

/* ---------- metrics ---------- */
{
  // The exact `FUNNEL:` label node, not the first title containing "funnel" — that is
  // "Lead Magnet Funnel Content", which carries no rating.
  const funnelLabels = all.filter((n) => n.title === "FUNNEL");
  ok("stars: sub-service funnel ratings read", funnelLabels.map((n) => n.stars).join(",") === "5,4", funnelLabels.map((n) => n.stars).join(","));
  ok("stars: phase 1 funnel flow rated", byTitle("Phase 1 Funnel Flow")?.stars === 4, String(byTitle("Phase 1 Funnel Flow")?.stars));
  ok("kind: phase 1 funnel flow is a funnel, not a phase", byTitle("Phase 1 Funnel Flow")?.kind === "funnel", byTitle("Phase 1 Funnel Flow")?.kind);
  ok("kind: top-of-funnel blogs are blogs", byTitle("Top-of-Funnel")?.kind === "blog", byTitle("Top-of-Funnel")?.kind);
  ok("kind: sub-services identified by depth", byTitle("Single-Tooth Implants")?.kind === "sub-service", byTitle("Single-Tooth Implants")?.kind);
  ok("depth: assets sit one below their phase", byTitle("CRO Audit")?.depth === 2, String(byTitle("CRO Audit")?.depth));
  ok("depth: schema labels sit one below their sub-service", byTitle("TARGET KEYWORD")?.depth === 3, String(byTitle("TARGET KEYWORD")?.depth));
}
ok("months: timeline counted", tree.totals.months === 6, String(tree.totals.months));
ok("totals: phases counted", tree.totals.phases === 3, String(tree.totals.phases));
ok("totals: nodes counted", tree.totals.nodes === all.length - 1, `${tree.totals.nodes} vs ${all.length - 1}`);
ok("totals: assets counted", tree.totals.assets > 5, String(tree.totals.assets));

/* ---------- nothing is lost ---------- */
{
  /* Word-multiset containment: every content word of the plan must appear in the tree at least
   * as many times as it appears in the source.
   *
   * Two earlier versions of this check were too weak and too strong in turn. Matching whole
   * lines but skipping any line starting with a node title is how a real bug hid: `TARGET
   * KEYWORD CLUSTER: single tooth implant (~880/mo)` became the title `TARGET KEYWORD CLUSTER`
   * with the keywords dropped, and the check waved the line through. Matching six-word runs then
   * failed on every heading boundary, where the words either side legitimately land in two
   * different nodes. Counting words catches a dropped span and cannot be confused by where the
   * boundaries fall.
   *
   * Bare ordinals are excluded. `cleanTitle` strips the `1.` from `1. CRO Audit` because the
   * position in the tree carries it, and an ordinal is formatting rather than content. */
  const tokens = (s: string) =>
    s
      .toLowerCase()
      .replace(/[*#|>]/g, " ")
      .split(/\s+/)
      .filter(Boolean)
      // Ordinals are dropped *before* punctuation is stripped, because that dot is the only
      // thing separating the `1.` of a numbered heading from a genuine `1` in the prose.
      .filter((w) => !/^\d{1,2}[.)]$/.test(w) && !/^[a-h][.)]$/.test(w))
      // Edge punctuation off each token: the title of `LEAD MAGNET:` keeps the words and drops
      // the colon, so comparing `magnet:` against `magnet` would report a loss that is not one.
      .map((w) => w.replace(/^[^a-z0-9~$]+|[^a-z0-9%)]+$/g, ""))
      .filter((w) => /[a-z0-9]/.test(w));

  const tally = (words: string[]) => {
    const counts = new Map<string, number>();
    for (const w of words) counts.set(w, (counts.get(w) ?? 0) + 1);
    return counts;
  };

  const source = tally(tokens(DEMO_PLAN));
  const inTree = tally(tokens(all.map((n) => `${n.title} ${n.body}`).join(" ")));
  const short: string[] = [];
  for (const [word, need] of source) {
    if ((inTree.get(word) ?? 0) < need) short.push(`${word} (${inTree.get(word) ?? 0}/${need})`);
  }
  ok("lossless: every content word survives, with its count", short.length === 0, short.slice(0, 5).join(" // "));

  // And the two specific cases that got past earlier versions of this check.
  const cluster = all.find((n) => n.title === "TARGET KEYWORD CLUSTER");
  ok("lossless: a label keeps the value on its own line", cluster?.body.includes("single tooth implant") ?? false,
    JSON.stringify(cluster?.body.slice(0, 80)));
  ok("lossless: the document title is kept", tree.root.body.includes("Owned Media Plan of Action"),
    JSON.stringify(tree.root.body.slice(0, 80)));
}

/* ---------- tables and fences are content, not structure ---------- */
{
  const summary = byTitle("Total Asset Summary");
  ok("table section: table rows did not become nodes", (summary?.children.length ?? 0) === 0, kids(summary).join(" | "));
  ok("table section: the table is in its body", summary?.body.includes("| Pillar Pages |") ?? false);

  const fenced = buildPlanTree(
    "# Plan\n\n## 1. A\ntext\n\n```html\n<h1>## 2. Not a section</h1>\n### Neither\n```\n\n## 2. B\ntext\n\n## 3. C\nmore\n",
  );
  ok("fenced: headings inside a fence are ignored", fenced.root.children.length === 3, fenced.root.children.map((c) => c.title).join(" | "));
}

/* ---------- honest failure ---------- */
{
  const nothing = buildPlanTree("");
  ok("empty: unavailable", !nothing.available && !!nothing.reason, nothing.reason);
  const prose = buildPlanTree("Just a paragraph of prose with no structure at all in it whatsoever.");
  ok("prose: unavailable with a reason", !prose.available, prose.reason);
  ok("undefined: safe", !buildPlanTree(undefined).available);
}

/* ---------- level normalisation ---------- */
{
  // `#` then `###` with no `##` must nest the same as `#` then `##`.
  const skipped = buildPlanTree("# Root\n\n# 1. A\nx\n\n### A1\ny\n\n### A2\nz\n\n# 2. B\nw\n");
  const a = skipped.root.children.find((c) => c.title.includes("1. A") || c.title === "A");
  ok("levels: a skipped heading level still nests", (a?.children.length ?? 0) === 2, skipped.root.children.map((c) => `${c.title}(${c.children.length})`).join(" | "));
}

/* ---------- layout ---------- */
{
  const rootOnly = layoutPlan(tree.root, new Set([tree.root.id]));
  ok("layout: root open shows root plus its ten sections", rootOnly.nodes.length === 11, String(rootOnly.nodes.length));
  ok("layout: one edge per visible child", rootOnly.edges.length === 10, String(rootOnly.edges.length));
  ok("layout: a fully closed tree is one node", layoutPlan(tree.root, new Set()).nodes.length === 1);

  // What the map opens with: root and its sections, so each phase shows what is directly in it.
  const open = new Set<string>([tree.root.id, ...tree.root.children.map((c) => c.id)]);
  const layout = layoutPlan(tree.root, open);
  ok("layout: sections open reveals their children", layout.nodes.length > 30, String(layout.nodes.length));
  ok("layout: columns follow depth", layout.nodes.every((n) => n.x === n.node.depth * (NODE_W + 48)));

  /* Two nodes may share a row when they are in different columns — a parent is placed at the mean
   * of its children, which routinely lands on a child's row one column to the right. What must
   * never happen is two nodes sharing a row *within* a column. */
  const byColumn = new Map<number, number[]>();
  for (const n of layout.nodes) {
    const col = byColumn.get(n.x) ?? [];
    col.push(n.y);
    byColumn.set(n.x, col);
  }
  ok("layout: no two nodes overlap within a column", [...byColumn.values()].every((ys) => new Set(ys).size === ys.length),
    [...byColumn.entries()].map(([x, ys]) => `${x}:${ys.length}/${new Set(ys).size}`).join(" "));
  ok("layout: leaves are one row pitch apart", (() => {
    const leafYs = layout.nodes.filter((n) => !n.hasChildren).map((n) => n.y).sort((a, b) => a - b);
    return leafYs.every((y, i) => i === 0 || y - leafYs[i - 1] >= ROW_H - 0.01);
  })());
  ok("layout: a parent sits between its first and last child", (() => {
    const phase = layout.nodes.find((n) => n.node.kind === "phase" && n.node.title.includes("Phase 1"));
    const kidsY = layout.nodes.filter((n) => phase?.node.children.some((c) => c.id === n.node.id)).map((n) => n.y);
    return !!phase && kidsY.length > 1 && phase.y > Math.min(...kidsY) && phase.y < Math.max(...kidsY);
  })());
  ok("layout: collapsed nodes are flagged", layout.nodes.some((n) => n.collapsed));

  /* Cards are as tall as their text. A fixed 40px box with a truncated title cut the plan's
   * longer node names in half; a long title must now be given more height than a short one, and a
   * measured height must win over the estimate. */
  const long = layout.nodes.reduce((a, b) => (b.node.title.length > a.node.title.length ? b : a));
  ok("layout: a long title gets a taller card", long.h > NODE_H, `${long.node.title.length} chars -> ${long.h}px`);
  ok("layout: estimate never below the minimum", layout.nodes.every((n) => n.h >= NODE_H && n.h === estimateNodeHeight(n.node)));
  const grown = layoutPlan(tree.root, open, new Map([[long.node.id, 260]]));
  ok("layout: a measured height is used", grown.nodes.find((n) => n.node.id === long.node.id)?.h === 260);

  /* With variable heights, "no overlap" means boxes, not row numbers. */
  const noOverlap = (l: ReturnType<typeof layoutPlan>) => {
    const cols = new Map<number, { y: number; h: number }[]>();
    for (const n of l.nodes) cols.set(n.x, [...(cols.get(n.x) ?? []), { y: n.y, h: n.h }]);
    return [...cols.values()].every((boxes) =>
      boxes
        .sort((a, b) => a.y - b.y)
        .every((b, i) => i === 0 || b.y >= boxes[i - 1].y + boxes[i - 1].h + ROW_GAP - 0.01),
    );
  };
  ok("layout: no two cards overlap within a column", noOverlap(layout));
  ok("layout: still no overlap after a card grows", noOverlap(grown));
  // A parent taller than its only child must push its subtree down rather than poke above it.
  const tall = new Map(layout.nodes.filter((n) => n.hasChildren).map((n) => [n.node.id, 400]));
  ok("layout: tall parents never overlap the sibling above", noOverlap(layoutPlan(tree.root, open, tall)));
  /* A selected node's text opens as one more card at the end of its branch, not in a side panel.
   * It must be a real child in the layout: one column right, joined by an edge, and pushing the
   * column down rather than covering it, however tall the text is. */
  {
    const leaf = layout.nodes.find((n) => !n.hasChildren && n.node.body.trim().length > 40)!;
    const withText = layoutPlan(tree.root, open, new Map([[detailIdFor(leaf.node.id), 900]]), leaf.node.id);
    const card = withText.nodes.find((n) => n.detailOf === leaf.node.id);
    const owner = withText.nodes.find((n) => n.node.id === leaf.node.id)!;
    ok("detail: the selected node's text is one more card", !!card && withText.nodes.length === layout.nodes.length + 1);
    ok("detail: in the next column", card?.x === owner.x + NODE_W + 48);
    ok("detail: wider than a node", card?.w === DETAIL_W && DETAIL_W > NODE_W);
    ok("detail: carries the node's own text", card?.node.body === leaf.node.body);
    ok("detail: joined to its node by an edge", withText.edges.some((e) => e.fromId === leaf.node.id && e.toId === card?.node.id));
    ok("detail: a 900px card overlaps nothing", noOverlap(withText));
    ok("detail: and nothing to its right shares its band", withText.nodes.every((n) =>
      n === card || n.x <= card!.x || n.y + n.h <= card!.y || n.y >= card!.y + card!.h));
    const parent = layout.nodes.find((n) => n.hasChildren && !n.collapsed && n.node.depth > 0)!;
    const afterKids = layoutPlan(tree.root, open, undefined, parent.node.id);
    const kidYs = afterKids.nodes.filter((n) => parent.node.children.some((c) => c.id === n.node.id)).map((n) => n.y);
    ok("detail: comes after a parent's real children", (afterKids.nodes.find((n) => n.detailOf === parent.node.id)?.y ?? -1) > Math.max(...kidYs));
    ok("detail: none without a selection", layout.nodes.every((n) => !n.detailOf));
  }

  ok("layout: edges end at card centres", grown.edges.every((e) => {
    const to = grown.nodes.find((n) => n.node.id === e.toId)!;
    return e.path.endsWith(` ${to.x} ${to.y + to.h / 2}`);
  }));
}

/* ---------- the diagram renders ---------- */
{
  const html = renderToStaticMarkup(<PlanMindMap text={DEMO_PLAN} label="Sample Dental" />);
  ok("map: renders", html.length > 2000);
  ok("map: header states the totals", html.includes("phases") && html.includes("named assets"), html.slice(0, 400));
  ok("map: month count shown", html.includes("6-month timeline"), html.slice(0, 400));
  ok("map: phases are on screen", html.includes("Phase 1") && html.includes("Phase 2"));
  ok("map: type carried by a monospace tag, not ten colours", html.includes(">PHASE<"), html.slice(0, 600));
  ok("map: connectors drawn as svg paths", html.includes("<path"));
  ok("map: expand/collapse offered", html.includes("Expand all") && html.includes("Collapse"));
  ok("map: search offered", html.includes("Find a node"));
  ok("map: detail panel prompts a click", html.includes("Click any node"), html.slice(-500));
  ok("map: deeper nodes closed on mount", !html.includes("TARGET KEYWORD CLUSTER"));
  ok("map: no flow animation until something is selected", !html.includes("plan-flow"));
  ok("map: titles wrap, never truncate", !/class="[^"]*\btruncate\b[^"]*"[^>]*>[^<]*Phase 1/.test(html));
  ok("map: legend names the families present", html.includes("Conversion") && html.includes("Plan &amp; phases"));
  ok("map: touch zoom controls offered", html.includes('aria-label="Zoom in"') && html.includes('aria-label="Zoom out"'));

  // The inline detail a selected card unfolds: plain text, no table or markdown noise.
  const withBody = all.find((n) => n.body.includes("|") && n.body.replace(/^\s*\|.*$/gm, "").trim().length > 40);
  if (withBody) {
    const excerpt = plainExcerpt(withBody);
    ok("excerpt: drops table rows and markdown", !excerpt.includes("|") && !excerpt.includes("**"), excerpt);
    ok("excerpt: bounded", excerpt.length <= 221, String(excerpt.length));
  }

  const unusable = renderToStaticMarkup(<PlanMindMap text="just prose, no structure at all here" label="X" />);
  ok("map: says so when it cannot draw a plan", unusable.includes("No diagram for this plan"), unusable.slice(0, 200));
  ok("map: points at the document instead", unusable.includes("full document is below"));
}

/* ---------- the standalone html export ---------- */
{
  const built = buildPlanMindMapHtml(DEMO_PLAN, "Sample Dental");
  ok("export: produced", !!built);
  const html = built?.html ?? "";
  ok("export: filename slugged", built?.filename === "sample-dental-plan-map.html", built?.filename);
  ok("export: is a whole document", html.startsWith("<!doctype html>") && html.trimEnd().endsWith("</html>"));
  ok("export: self-contained — no network references", !/(src|href)="https?:/.test(html), "found an external reference");
  ok("export: styles inline", html.includes("<style>"));
  ok("export: script inline", html.includes("<script>"));
  ok("export: carries the plan data", html.includes('id="plan-data"'));
  ok("export: both themes", html.includes("prefers-color-scheme: dark"));
  ok("export: respects reduced motion", html.includes("prefers-reduced-motion"));
  ok("export: has the flow animation", html.includes("@keyframes flow"));

  // The embedded JSON must be both valid and complete.
  const match = /<script id="plan-data" type="application\/json">([\s\S]*?)<\/script>/.exec(html);
  ok("export: data block found", !!match);
  let parsed: { root?: { c?: unknown[] }; totals?: { phases?: number } } | null = null;
  try {
    parsed = JSON.parse(match?.[1] ?? "null");
  } catch (err) {
    ok("export: embedded json parses", false, String(err));
  }
  if (parsed) {
    ok("export: embedded json parses", true);
    ok("export: tree survived the round trip", (parsed.root?.c ?? []).length === 10, String((parsed.root?.c ?? []).length));
    ok("export: totals survived", parsed.totals?.phases === 3, String(parsed.totals?.phases));
  }

  // Every node's own text has to travel, or the file is a summary rather than the plan.
  ok("export: node bodies included", html.includes("single tooth implant"), "expected a keyword cluster in the payload");
  ok("export: nothing left to draw for an unusable plan", buildPlanMindMapHtml("prose only", "X") === null);

  // A plan containing `</script>` must not be able to break out of the data block.
  const hostile = buildPlanMindMapHtml(DEMO_PLAN.replace("Executive Summary", "Exec </script><script>alert(1)</script>"), "X");
  ok("export: a script tag in the plan cannot escape the data block", !!hostile && !/<script>alert/.test(hostile.html), "escaped!");
}

/* Fullscreen must escape the transcript's containing blocks.
 *
 * `position: fixed` resolves against the viewport only while no ancestor establishes a containing
 * block, and two ancestors here do: the transcript pane is a Tailwind `@container`
 * (`container-type: inline-size`), and every card carries `.msg-rise`, whose
 * `animation-fill-mode: both` leaves `transform: translateY(0)` applied permanently. With the layer
 * rendered in place, `inset-0` pinned it to the card and Fullscreen made the tree SMALLER.
 *
 * Server rendering cannot show that - there is no layout and no containing block off-browser, and
 * the component deliberately renders in place when there is no `document`. So this is a source
 * check: it pins that the fullscreen layer goes through `createPortal` into `document.body`, which
 * is the one line that fixes it and an easy one to "simplify" away.
 */
{
  let dir = fileURLToPath(new URL(".", import.meta.url));
  let srcPath = "";
  for (let i = 0; i < 5 && !srcPath; i++) {
    const candidate = join(dir, "src", "pipeline", "PlanMindMap.tsx");
    if (existsSync(candidate)) srcPath = candidate;
    dir = dirname(dir);
  }
  ok("fullscreen: located PlanMindMap.tsx to check", srcPath !== "");
  const src = srcPath ? readFileSync(srcPath, "utf8") : "";

  ok(
    "fullscreen: the layer is portalled into document.body, not rendered in place",
    /createPortal\(\s*layer\s*,\s*document\.body\s*\)/.test(src),
  );
  ok(
    "fullscreen: and still renders inline when there is no document (smoke runs in node)",
    /typeof document === "undefined" \? layer/.test(src),
  );
  // The layer has to actually fill the viewport once it is out of the card.
  ok("fullscreen: the layer is a full-viewport fixed box", /fixed inset-0 z-50/.test(src));
  // Escape and the scroll lock are the obligations any full-viewport layer carries.
  ok("fullscreen: Escape exits", /e\.key === "Escape"/.test(src) && /setFullscreen\(false\)/.test(src));
  ok("fullscreen: the page behind is scroll-locked", /document\.body\.style\.overflow = "hidden"/.test(src));
  // Without a re-fit the map keeps the zoom it was given for a 22-40rem card.
  ok("fullscreen: the tree is re-fitted to the new box", /\[fullscreen, fit\]/.test(src));
}

// The plan lists the pipeline's own stage names under phases titled like the toggle
// (`test_plan_names_match_pipeline.py` pins the prompt side). Each name gets its tag, not a blank.
{
  const stagePlan = [
    "## 3. Phase 1: Core Service",
    "  1. ICP — Ideal Customer Profile",
    "  2. CRO — Page Rewrite",
    "  3. Pillar Page Design",
    "  4. Funnel",
    "  5. Funnel Hub Media",
    "  6. Offers / Value Ladder",
    "  7. Lead Magnet",
    "  8. Blog Post",
    "  9. Content Marketing Strategy",
    "  10. Social Content Strategy Audit",
    "  11. Webinar",
    "  12. Book (from Webinar)",
    "  13. Podcast Episode",
    "  14. SMS Sequence",
    "## 4. Phase 2: Sub-Service",
    "### 1. Meta Ads",
  ].join("\n");
  const nodes = flattenPlan(buildPlanTree(stagePlan, "Plan").root);
  const kindOfTitle = (t: string) => nodes.find((n) => n.title.includes(t))?.kind;
  const want: [string, string][] = [
    ["Phase 1: Core Service", "phase"],
    ["Phase 2: Sub-Service", "phase"],
    ["ICP — Ideal Customer Profile", "icp"],
    ["Pillar Page Design", "page"],
    ["Funnel Hub Media", "funnel"],
    ["Offers / Value Ladder", "offer"],
    ["Lead Magnet", "lead-magnet"],
    ["Blog Post", "blog"],
    ["Content Marketing Strategy", "strategy"],
    ["Social Content Strategy Audit", "strategy"],
    ["Webinar", "webinar"],
    ["Book (from Webinar)", "book"],
    ["Podcast Episode", "podcast"],
    ["SMS Sequence", "sms"],
  ];
  for (const [title, kind] of want) ok(`stage names: "${title}" is tagged ${kind}`, kindOfTitle(title) === kind);
}

console.log(`\n${pass} passed, ${fails.length} failed`);
if (fails.length) {
  console.log("\nFAILURES:");
  fails.forEach((f) => console.log("  x " + f));
  process.exitCode = 1;
}
