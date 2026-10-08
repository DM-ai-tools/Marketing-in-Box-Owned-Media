import { buildPlanTree, type PlanNode } from "./planTree";

/**
 * The plan map as one self-contained HTML file.
 *
 * The in-app diagram is for the operator; this is for everyone else. A plan is the asset most
 * likely to be sent onward — to the client, to whoever is going to build it — and neither of them
 * has a login. So the same tree is serialised into a single file with its CSS, its script and its
 * data inline: no build step, no CDN, no network at all. It opens by double-clicking it, and it
 * still works in five years.
 *
 * Deliberately a *rendering* of the parsed document rather than a second implementation of the
 * diagram. The layout maths is simpler here — a nested list with CSS connectors instead of the
 * app's absolutely-positioned SVG tree — because a file that has to survive being emailed around
 * should have as little machinery in it as possible. What matters is that the tree, the tags, the
 * ratings and every node's own text come from `buildPlanTree`, so the file and the app agree.
 *
 * Nothing is summarised: every node's body travels with it, so the file is the whole plan.
 */

interface ExportNode {
  t: string;
  k: string;
  b: string;
  s?: number;
  v?: number;
  u?: string;
  c: ExportNode[];
}

/** Kind -> the short tag the app uses. Kept in step with `KIND_TAG` in `planFamilies.ts`. */
const TAGS: Record<string, string> = {
  root: "PLAN",
  phase: "PHASE",
  summary: "SUMMARY",
  timeline: "TIMELINE",
  table: "TABLE",
  "sub-service": "SERVICE",
  category: "GROUP",
  page: "PAGE",
  blog: "BLOG",
  "lead-magnet": "MAGNET",
  funnel: "FUNNEL",
  email: "EMAIL",
  sms: "SMS",
  "case-study": "CASE",
  video: "VIDEO",
  industry: "INDUSTRY",
  location: "LOCATION",
  keyword: "KEYWORD",
  detail: "",
};

function toExport(node: PlanNode): ExportNode {
  return {
    t: node.title,
    k: node.kind,
    b: node.body.trim(),
    s: node.stars,
    v: node.volume,
    u: node.slug,
    c: node.children.map(toExport),
  };
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** JSON safe to embed in a `<script>`.
 *
 * Three escapes, each for a real failure: `<` so a `</script>` inside a plan's own text cannot
 * close the tag early, and U+2028/U+2029 because those are valid in JSON strings but are line
 * terminators in JavaScript source — a plan pasted from a word processor can carry them, and they
 * would break the parse of the very object they sit in. */
function safeJson(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

export interface PlanExport {
  filename: string;
  html: string;
}

export function buildPlanMindMapHtml(text: string, planTitle: string): PlanExport | null {
  const tree = buildPlanTree(text, planTitle);
  if (!tree.available) return null;

  const data = {
    title: planTitle,
    totals: tree.totals,
    tags: TAGS,
    root: toExport(tree.root),
    generated: new Date().toISOString().slice(0, 10),
  };

  const slug =
    planTitle
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "plan-of-action";

  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(planTitle)} — Plan of Action</title>
<style>
  /* Two hues only — ink and paper — matching the app this came out of. Both themes, following the
     reader's system setting, because a plan gets opened on somebody else's machine. */
  :root {
    --ink: #16150f; --paper: #f6f3ec; --accent: #0b5fff;
    --bg: var(--paper); --fg: var(--ink);
    --raised: color-mix(in srgb, var(--paper) 92%, var(--ink) 8%);
    --sunken: color-mix(in srgb, var(--paper) 96%, var(--ink) 4%);
    --muted: color-mix(in srgb, var(--ink) 62%, var(--paper) 38%);
    --faint: color-mix(in srgb, var(--ink) 38%, var(--paper) 62%);
    --line: color-mix(in srgb, var(--ink) 16%, var(--paper) 84%);
    --line-strong: color-mix(in srgb, var(--ink) 30%, var(--paper) 70%);
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: var(--ink); --fg: var(--paper);
      --raised: color-mix(in srgb, var(--ink) 88%, var(--paper) 12%);
      --sunken: color-mix(in srgb, var(--ink) 94%, var(--paper) 6%);
      --muted: color-mix(in srgb, var(--paper) 66%, var(--ink) 34%);
      --faint: color-mix(in srgb, var(--paper) 42%, var(--ink) 58%);
      --line: color-mix(in srgb, var(--paper) 16%, var(--ink) 84%);
      --line-strong: color-mix(in srgb, var(--paper) 30%, var(--ink) 70%);
      color-scheme: dark;
    }
  }
  * { box-sizing: border-box }
  html, body { height: 100% }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer }
  header {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 10px 16px; border-bottom: 1px solid var(--line); background: var(--bg);
    position: sticky; top: 0; z-index: 5;
  }
  header h1 { margin: 0; font-size: 15px; letter-spacing: -.01em }
  .stat { font-size: 12px; color: var(--muted) }
  .stat b { color: var(--fg); font-variant-numeric: tabular-nums }
  .grow { flex: 1 }
  .ctl {
    border: 1px solid var(--line-strong); border-radius: 999px; padding: 3px 9px;
    font-size: 12px; font-weight: 600; color: var(--muted);
  }
  .ctl:hover { background: var(--sunken); color: var(--fg) }
  #find {
    border: 1px solid var(--line-strong); border-radius: 999px; padding: 3px 10px;
    font-size: 12px; background: var(--sunken); color: var(--fg); width: 150px; font-family: inherit;
  }
  main { display: flex; height: calc(100% - 47px) }
  #tree { flex: 1; overflow: auto; padding: 14px 18px 60px }
  aside {
    width: 300px; flex: none; border-left: 1px solid var(--line);
    overflow: auto; padding: 12px 14px 40px; background: var(--sunken);
  }
  @media (max-width: 780px) { main { flex-direction: column } aside { width: auto; border-left: none; border-top: 1px solid var(--line) } }

  ul.branch { list-style: none; margin: 0; padding-left: 22px; position: relative }
  ul.branch > li { position: relative; padding: 2px 0 }
  /* The connectors: one vertical spine per branch, one elbow per child. Cheaper and far more
     robust than positioned SVG when the file has to survive any window size. */
  ul.branch > li::before {
    content: ""; position: absolute; left: -12px; top: 0; bottom: 0;
    border-left: 1px solid var(--line-strong);
  }
  ul.branch > li:last-child::before { bottom: calc(100% - 18px) }
  ul.branch > li::after {
    content: ""; position: absolute; left: -12px; top: 18px; width: 12px;
    border-top: 1px solid var(--line-strong);
  }
  ul.branch > li.lit::before, ul.branch > li.lit::after { border-color: var(--accent) }
  ul.branch > li.lit::after { animation: flow 0.9s linear infinite; border-top-style: dashed }
  @keyframes flow { from { background-position: 0 0 } to { background-position: 12px 0 } }
  @media (prefers-reduced-motion: reduce) { ul.branch > li.lit::after { animation: none } }

  .node {
    display: inline-flex; align-items: center; gap: 7px; max-width: min(560px, 100%);
    border: 1px solid var(--line); border-radius: 8px; background: var(--bg);
    padding: 5px 9px; text-align: left;
  }
  .node:hover { border-color: var(--line-strong) }
  .node.sel { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 14%, transparent) }
  .node .tw { min-width: 0 }
  .node .ti { display: block; font-size: 12.5px; font-weight: 600; line-height: 1.25 }
  .node.accent .ti { color: var(--accent) }
  .node .me { display: flex; gap: 7px; align-items: center; line-height: 1.2 }
  .tag { font: 600 9.5px/1 ui-monospace, SFMono-Regular, Consolas, monospace; letter-spacing: .08em; color: var(--faint) }
  .vol, .stars { font-size: 10px; color: var(--faint); font-variant-numeric: tabular-nums }
  .toggle {
    width: 17px; height: 17px; flex: none; border: 1px solid var(--line-strong); border-radius: 4px;
    font: 700 10px/1 inherit; color: var(--muted); display: grid; place-items: center;
  }
  .toggle:hover { background: var(--sunken); color: var(--fg) }
  .leaf { width: 5px; height: 5px; flex: none; border-radius: 50%; background: var(--line-strong) }
  .count { font-size: 10px; color: var(--muted); background: var(--sunken); border-radius: 999px; padding: 0 5px; flex: none }
  .hide { display: none }

  aside h2 { margin: 0 0 2px; font-size: 14px }
  aside .sub { margin: 0 0 10px; font-size: 11px; color: var(--faint); display: flex; gap: 8px; flex-wrap: wrap }
  aside .body { font-size: 12.5px; white-space: pre-wrap; word-break: break-word; color: var(--fg) }
  aside .body table { border-collapse: collapse; width: 100% }
  aside .empty { font-size: 12.5px; color: var(--muted) }
  footer { padding: 8px 16px; border-top: 1px solid var(--line); font-size: 11px; color: var(--faint) }
</style>
</head>
<body>
<header>
  <h1>${escapeHtml(planTitle)}</h1>
  <span class="stat"><b id="s-nodes"></b> nodes</span>
  <span class="stat"><b id="s-phases"></b> phases</span>
  <span class="stat"><b id="s-assets"></b> named assets</span>
  <span class="stat" id="s-months-wrap"><b id="s-months"></b>-month timeline</span>
  <span class="grow"></span>
  <input id="find" type="search" placeholder="Find a node…" aria-label="Find a node">
  <button class="ctl" id="expand">Expand all</button>
  <button class="ctl" id="collapse">Collapse</button>
</header>
<main>
  <div id="tree"></div>
  <aside id="detail"><p class="empty">Click any node to read that step of the plan in full.</p></aside>
</main>
<footer>Plan of Action map — generated ${escapeHtml(data.generated)} from the plan document. Every node's own text is included; nothing is summarised.</footer>

<script id="plan-data" type="application/json">${safeJson(data)}</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById("plan-data").textContent);
  var tags = data.tags;
  var treeEl = document.getElementById("tree");
  var detailEl = document.getElementById("detail");
  var nodes = [];          // flat, in document order
  var selected = null;

  document.getElementById("s-nodes").textContent = data.totals.nodes;
  document.getElementById("s-phases").textContent = data.totals.phases;
  document.getElementById("s-assets").textContent = data.totals.assets;
  if (data.totals.months) document.getElementById("s-months").textContent = data.totals.months;
  else document.getElementById("s-months-wrap").style.display = "none";

  function assignIds(node, parent, depth) {
    node.id = "n" + nodes.length;
    node.parent = parent;
    node.depth = depth;
    node.open = depth < 1;      // root's children visible, deeper closed
    nodes.push(node);
    (node.c || []).forEach(function (child) { assignIds(child, node, depth + 1); });
    return node;
  }
  assignIds(data.root, null, 0);

  function stars(n) { return "\\u2605".repeat(n) + "\\u2606".repeat(Math.max(0, 5 - n)); }

  function nodeEl(node) {
    var el = document.createElement("button");
    el.type = "button";
    el.className = "node" + (node.k === "root" || node.k === "phase" ? " accent" : "");
    el.dataset.id = node.id;

    var kids = node.c || [];
    if (kids.length) {
      var t = document.createElement("span");
      t.className = "toggle";
      t.textContent = node.open ? "\\u2212" : "+";
      t.setAttribute("role", "button");
      t.dataset.toggle = node.id;
      el.appendChild(t);
    } else {
      var dot = document.createElement("span");
      dot.className = "leaf";
      el.appendChild(dot);
    }

    var wrap = document.createElement("span");
    wrap.className = "tw";
    var title = document.createElement("span");
    title.className = "ti";
    title.textContent = node.t;
    wrap.appendChild(title);

    var meta = document.createElement("span");
    meta.className = "me";
    var tag = tags[node.k];
    if (tag) { var tg = document.createElement("span"); tg.className = "tag"; tg.textContent = tag; meta.appendChild(tg); }
    if (node.v) { var v = document.createElement("span"); v.className = "vol"; v.textContent = "~" + node.v.toLocaleString() + "/mo"; meta.appendChild(v); }
    if (node.s) { var s = document.createElement("span"); s.className = "stars"; s.textContent = stars(node.s); meta.appendChild(s); }
    if (node.u) { var u = document.createElement("span"); u.className = "vol"; u.textContent = node.u; meta.appendChild(u); }
    if (meta.childNodes.length) wrap.appendChild(meta);
    el.appendChild(wrap);

    if (kids.length && !node.open) {
      var c = document.createElement("span");
      c.className = "count";
      c.textContent = countBelow(node);
      el.appendChild(c);
    }
    return el;
  }

  function countBelow(node) {
    var n = 0;
    (node.c || []).forEach(function (child) { n += 1 + countBelow(child); });
    return n;
  }

  function render() {
    treeEl.textContent = "";
    treeEl.appendChild(branch([data.root]));
    highlight();
  }

  function branch(list) {
    var ul = document.createElement("ul");
    ul.className = "branch";
    list.forEach(function (node) {
      var li = document.createElement("li");
      li.dataset.id = node.id;
      li.appendChild(nodeEl(node));
      if (node.open && (node.c || []).length) li.appendChild(branch(node.c));
      ul.appendChild(li);
    });
    return ul;
  }

  /* The branch from the root down to the selected node, lit and flowing. Only that branch: lighting
     every connector at once is decoration, lighting the one path just asked about is information. */
  function highlight() {
    var lit = {};
    for (var n = selected; n; n = n.parent) lit[n.id] = true;
    Array.prototype.forEach.call(treeEl.querySelectorAll("li"), function (li) {
      li.classList.toggle("lit", !!lit[li.dataset.id]);
    });
    Array.prototype.forEach.call(treeEl.querySelectorAll(".node"), function (el) {
      el.classList.toggle("sel", selected && el.dataset.id === selected.id);
    });
  }

  function byId(id) {
    for (var i = 0; i < nodes.length; i++) if (nodes[i].id === id) return nodes[i];
    return null;
  }

  function show(node) {
    selected = node;
    detailEl.textContent = "";
    var h = document.createElement("h2");
    h.textContent = node.t;
    detailEl.appendChild(h);

    var sub = document.createElement("p");
    sub.className = "sub";
    var bits = [];
    if (tags[node.k]) bits.push(tags[node.k]);
    var below = countBelow(node);
    if (below) bits.push(below + " below");
    if (node.u) bits.push(node.u);
    if (node.v) bits.push("~" + node.v.toLocaleString() + "/mo");
    if (node.s) bits.push(stars(node.s));
    bits.forEach(function (b) { var s = document.createElement("span"); s.textContent = b; sub.appendChild(s); });
    detailEl.appendChild(sub);

    var body = document.createElement("div");
    if (node.b) { body.className = "body"; body.textContent = node.b; }
    else { body.className = "empty"; body.textContent = below
      ? "This node is a heading — its detail is in the " + below + " node(s) beneath it."
      : "No further detail in the plan for this node."; }
    detailEl.appendChild(body);
    highlight();
  }

  treeEl.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-toggle]");
    if (toggle) {
      var t = byId(toggle.dataset.toggle);
      if (t) { t.open = !t.open; render(); }
      event.stopPropagation();
      return;
    }
    var el = event.target.closest(".node");
    if (el) show(byId(el.dataset.id));
  });

  document.getElementById("expand").addEventListener("click", function () {
    nodes.forEach(function (n) { n.open = true; });
    render();
  });
  document.getElementById("collapse").addEventListener("click", function () {
    nodes.forEach(function (n) { n.open = n.depth === 0; });
    render();
  });

  document.getElementById("find").addEventListener("input", function (event) {
    var q = event.target.value.trim().toLowerCase();
    if (q.length < 2) return;
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.t.toLowerCase().indexOf(q) !== -1 || (n.b || "").toLowerCase().indexOf(q) !== -1) {
        for (var p = n.parent; p; p = p.parent) p.open = true;
        render();
        show(n);
        var el = treeEl.querySelector('.node[data-id="' + n.id + '"]');
        if (el && el.scrollIntoView) el.scrollIntoView({ block: "center" });
        return;
      }
    }
  });

  render();
})();
</script>
</body>
</html>
`;

  return { filename: `${slug}-plan-map.html`, html };
}
