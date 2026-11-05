"""Site architecture from measured keyword clusters: URL map + internal-link plan.

SearchFit's keyword-clustering and internal-linking skills say how to organise pages (one cluster =
one page, hub-and-spoke links, descriptive anchors). This module productises those *rules* on top of
the run's already-measured `keyword_clusters` — volumes and recommended URLs come from DataForSEO +
the clustering call, never invented here.

Built once when the keyword report is built (same fingerprint). Stages that write pages or strategy
get an `SEO_ARCHITECTURE` prompt block; Plan of Action summarises the same map. Phase-scoped like
`keyword_clusters`: a sub-service run builds its own.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

CONTEXT_KEY = "seo_architecture"
ARCHITECTURE_BLOCK = "SEO_ARCHITECTURE"

#: Stages that receive the prompt block and should plan links / URLs from it.
CONSUMER_ASSETS: frozenset[str] = frozenset(
    {
        "pillar_page",
        "funnel",
        "funnel_hub_media",
        "lead_magnet",
        "offers",
        "blog",
        "content_marketing_strategy",
        "plan_of_action",
    }
)

_MAX_PAGES = 12
_MAX_LINKS = 40
#: SERP-safe lengths. A title past 60 and a description past 155 are truncated in the map.
_TITLE_LIMIT = 60
_META_LIMIT = 155

# SearchFit-style intent / content-type when the cluster row is missing them.
_INTENT_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\b(how to|what is|why|guide|tutorial)\b", re.I), "informational", "guide"),
    (re.compile(r"\b(vs|versus|alternative|compare|comparison)\b", re.I), "commercial", "comparison"),
    (re.compile(r"\b(best|top \d+|list of)\b", re.I), "commercial", "listicle"),
    (re.compile(r"\b(buy|pricing|cost|near me|quote|hire|agency)\b", re.I), "transactional", "landing"),
)


@dataclass(frozen=True)
class SitePage:
    """A page already on the client's site (from the service picker), used to prefer real URLs."""

    name: str
    url: str


@dataclass
class MappedPage:
    cluster: str
    primary_keyword: str
    intent: str
    content_type: str
    funnel: str
    volume: int | None
    url: str
    status: str  # existing | proposed
    role: str  # hub | spoke | support
    #: Proposed `<title>` for this URL (≤60 chars). Built from the primary keyword, never a client claim.
    title_tag: str = ""
    #: Proposed meta description for this URL (≤155 chars). Same rule.
    meta_description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LinkEdge:
    from_url: str
    to_url: str
    from_label: str
    to_label: str
    anchor: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Architecture:
    service: str
    location: str
    fingerprint: str
    pages: list[MappedPage] = field(default_factory=list)
    links: list[LinkEdge] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def hub(self) -> MappedPage | None:
        return next((p for p in self.pages if p.role == "hub"), None)

    def to_markdown(self) -> str:
        """Operator-readable map. Plan of Action and the Context Store read this as a document."""
        if not self.pages:
            return ""
        lines = [
            "## SEO Architecture Map",
            "",
            f"**Service**: {self.service or '—'}",
            f"**Location**: {self.location or '—'}",
            f"**Pages mapped**: {len(self.pages)}",
            f"**Internal links planned**: {len(self.links)}",
            "",
            "One keyword cluster → one page. Volumes are measured (DataForSEO); URLs are either on "
            "the client's site or proposed slugs — never invented hosts. Title tags (≤60) and meta "
            "descriptions (≤155) are proposed from the primary keyword for each URL — not client claims.",
            "",
            "### URL map",
            "",
            "| Role | Cluster | Primary keyword | Intent | Content | Status | URL | Volume/mo |",
            "|------|---------|-----------------|--------|---------|--------|-----|-----------|",
        ]
        for p in self.pages:
            vol = f"{p.volume:,}" if isinstance(p.volume, int) else "—"
            lines.append(
                f"| {p.role} | {p.cluster} | {p.primary_keyword} | {p.intent or '—'} | "
                f"{p.content_type or '—'} | {p.status} | {p.url} | {vol} |"
            )
        lines += [
            "",
            "### Page meta tags",
            "",
            "| Role | URL | Title tag | Meta description |",
            "|------|-----|-----------|------------------|",
        ]
        for p in self.pages:
            title = (p.title_tag or "—").replace("|", "/")
            meta = (p.meta_description or "—").replace("|", "/")
            lines.append(f"| {p.role} | {p.url} | {title} | {meta} |")
        lines += ["", "### Hub and spoke", ""]
        hub = self.hub
        if hub:
            lines.append(f"**Hub (pillar)**: [{hub.cluster}]({hub.url}) — `{hub.primary_keyword}`")
            spokes = [p for p in self.pages if p.role == "spoke"]
            if spokes:
                lines.append("")
                for s in spokes:
                    lines.append(f"- Spoke: [{s.cluster}]({s.url}) — `{s.primary_keyword}`")
            support = [p for p in self.pages if p.role == "support"]
            if support:
                lines.append("")
                lines.append("**Supporting pages**")
                for s in support:
                    lines.append(f"- [{s.cluster}]({s.url}) — `{s.primary_keyword}`")
        lines += ["", "### Internal links to add", ""]
        if self.links:
            lines += [
                "| From | To | Anchor text | Why |",
                "|------|----|-------------|-----|",
            ]
            for e in self.links:
                lines.append(f"| {e.from_label} | {e.to_label} | \"{e.anchor}\" | {e.reason} |")
        else:
            lines.append("_No link edges yet — need at least a hub and one spoke._")
        if self.notes:
            lines += ["", "### Notes", ""]
            lines += [f"- {n}" for n in self.notes]
        lines.append("")
        return "\n".join(lines)

    def to_context_value(self) -> dict[str, Any]:
        return {
            "content": self.to_markdown(),
            "service": self.service,
            "location": self.location,
            "config_fingerprint": self.fingerprint,
            "pages": [p.as_dict() for p in self.pages],
            "links": [e.as_dict() for e in self.links],
            "notes": list(self.notes),
        }

    def as_event(self) -> dict[str, Any]:
        return {
            "type": "seo_architecture",
            "service": self.service,
            "location": self.location,
            "pages": [p.as_dict() for p in self.pages],
            "links": [e.as_dict() for e in self.links],
            "notes": list(self.notes),
        }

    def link_answers(self) -> str:
        """Prefill text for Pillar/Blog internal-link fields: `url — label` per spoke and hub."""
        lines: list[str] = []
        for p in self.pages:
            if p.role == "hub":
                continue
            lines.append(f"{p.url} — {p.cluster}")
        hub = self.hub
        if hub and lines:
            lines.append(f"{hub.url} — {hub.cluster} (hub)")
        return "\n".join(lines)


def architecture_block(arch: Architecture | None, asset_id: str | None = None) -> str:
    """The `SEO_ARCHITECTURE` prompt block, or "" when this stage should not see one."""
    if arch is None or not arch.pages:
        return ""
    if asset_id and asset_id not in CONSUMER_ASSETS:
        return ""
    duty = _asset_duty(asset_id)
    page_lines = []
    for p in arch.pages:
        vol = f"{p.volume:,}/mo" if isinstance(p.volume, int) else "volume not measured"
        title = p.title_tag or "(no title yet)"
        meta = p.meta_description or "(no meta description yet)"
        page_lines.append(
            f"- [{p.role}] {p.cluster}: primary `{p.primary_keyword}` ({vol}); "
            f"intent={p.intent or '—'}; type={p.content_type or '—'}; "
            f"{p.status} URL `{p.url}`; "
            f"title tag `{title}`; meta description `{meta}`"
        )
    link_lines = [
        f"- From `{e.from_url}` → `{e.to_url}` using anchor \"{e.anchor}\" ({e.reason})"
        for e in arch.links[:_MAX_LINKS]
    ] or ["- (no edges yet)"]
    return (
        f"===== BEGIN {ARCHITECTURE_BLOCK} =====\n"
        "HOW THIS BLOCK BINDS YOUR RESPONSE:\n"
        "It is the measured site map for this run: one cluster per page, real or proposed URLs, and "
        "the hub-and-spoke links to place. Do not quote or name this block. Never invent a different "
        "URL host, never invent search volumes, and never target two pages at the same primary "
        "keyword (cannibalisation).\n\n"
        f"Service: {arch.service or 'this run'}"
        f"{f' · {arch.location}' if arch.location else ''}\n\n"
        "URL map:\n"
        + "\n".join(page_lines)
        + "\n\nInternal links (hub ↔ spokes):\n"
        + "\n".join(link_lines)
        + f"\n\n{duty}"
        f"===== END {ARCHITECTURE_BLOCK} =====\n\n"
    )


def _asset_duty(asset_id: str | None) -> str:
    duties = {
        "pillar_page": (
            "For this pillar page: treat the hub row as this page's URL when it is `existing`, else "
            "use the proposed slug. Use that row's title tag and meta description for `<title>` and "
            "the meta description (trim only to keep them under 60 / 155 characters). Link out to "
            "every spoke URL listed (descriptive anchors, never \"click here\"). Cover the hub "
            "cluster's primary keyword in the H1 and the spokes' keywords in H2 sections or cluster "
            "cards.\n"
        ),
        "blog": (
            "For this blog: pick the spoke (or support) row whose primary keyword matches the chosen "
            "topic. Use that row's title tag and meta description for the post. Link up to the hub "
            "pillar and across to 1–2 related spokes with keyword-relevant anchors.\n"
        ),
        "content_marketing_strategy": (
            "For the content strategy: use this map as the topical cluster plan — one calendar item "
            "per page row, hub first, then spokes by volume. Carry each row's title tag and meta "
            "description into the calendar brief. State the internal-link rules (spoke→hub, related "
            "spokes) explicitly.\n"
        ),
        "funnel": (
            "For the funnel: map awareness steps to informational clusters, consideration to commercial "
            "ones, and conversion to transactional / landing rows. Landing URLs must be ones on this "
            "map (existing or proposed). Where a step is a page, use that row's title tag and meta "
            "description.\n"
        ),
        "funnel_hub_media": (
            "For hub & media: the hub page is the SEO hub above; spokes are the topic pages the hub "
            "media and emails should send people to.\n"
        ),
        "lead_magnet": (
            "For the lead magnet: align the opt-in promise with the highest-volume informational or "
            "commercial cluster that is not the hub; say which pillar/spoke URL the thank-you page "
            "links to.\n"
        ),
        "offers": (
            "For the value ladder: attach each paid offer to the transactional / landing rows on the "
            "map, and free/tripwire offers to informational spokes. Name the URL each offer lives on.\n"
        ),
        "plan_of_action": (
            "For the Plan of Action: include a section headed exactly `## SEO Architecture Map` that "
            "restates this hub-and-spoke map (URL table, page meta tags, and link list). Under Phase 3 "
            "sitemap work, nest each proposed URL as its own heading so the plan tree can draw it, and "
            "restate each page's title tag and meta description under that heading. Do not invent "
            "volumes — copy the measured ones, or write \"not measured\".\n"
        ),
    }
    return duties.get(asset_id or "", "Use the URLs and link edges above wherever this asset places pages or links.\n")


def from_stored(value: Any) -> Architecture | None:
    """Rehydrate a stored context entry, or None when empty / wrong shape."""
    if not isinstance(value, dict):
        return None
    pages_raw = value.get("pages") or []
    if not pages_raw:
        return None
    service = str(value.get("service") or "")
    location = str(value.get("location") or "")
    pages = [
        MappedPage(
            cluster=str(p.get("cluster") or ""),
            primary_keyword=str(p.get("primary_keyword") or ""),
            intent=str(p.get("intent") or ""),
            content_type=str(p.get("content_type") or ""),
            funnel=str(p.get("funnel") or ""),
            volume=p.get("volume") if isinstance(p.get("volume"), int) else None,
            url=str(p.get("url") or ""),
            status=str(p.get("status") or "proposed"),
            role=str(p.get("role") or "spoke"),
            title_tag=str(p.get("title_tag") or ""),
            meta_description=str(p.get("meta_description") or ""),
        )
        for p in pages_raw
        if isinstance(p, dict) and (p.get("cluster") or p.get("primary_keyword"))
    ]
    if not pages:
        return None
    # A map stored before title/meta existed still gets them on read — same rules as a fresh build.
    _fill_page_meta(pages, location=location, service=service)
    links = [
        LinkEdge(
            from_url=str(e.get("from_url") or ""),
            to_url=str(e.get("to_url") or ""),
            from_label=str(e.get("from_label") or ""),
            to_label=str(e.get("to_label") or ""),
            anchor=str(e.get("anchor") or ""),
            reason=str(e.get("reason") or ""),
        )
        for e in (value.get("links") or [])
        if isinstance(e, dict) and e.get("from_url") and e.get("to_url")
    ]
    return Architecture(
        service=service,
        location=location,
        fingerprint=str(value.get("config_fingerprint") or ""),
        pages=pages,
        links=links,
        notes=[str(n) for n in (value.get("notes") or []) if n],
    )


def build_from_keyword_entry(
    stored: dict[str, Any],
    *,
    site_pages: Iterable[SitePage] = (),
    website: str = "",
) -> Architecture | None:
    """Build the map from a `keyword_clusters` context value. None when there is nothing measured."""
    if not isinstance(stored, dict):
        return None
    if stored.get("provider") and stored.get("provider") != "dataforseo":
        # Stub placeholders must not become a site map the operator treats as measured.
        return None
    report = stored.get("keyword_report") if isinstance(stored.get("keyword_report"), dict) else {}
    clusters = list(report.get("clusters") or [])
    groups = list(stored.get("keyword_groups") or report.get("keyword_groups") or [])
    fingerprint = str(stored.get("config_fingerprint") or "")
    service = str(stored.get("service") or "")
    location = str(stored.get("location") or "")
    site = list(site_pages)
    base = _site_origin(website) or _origin_from_pages(site)

    pages: list[MappedPage] = []
    notes: list[str] = []

    if clusters:
        for cluster in clusters[:_MAX_PAGES]:
            if not isinstance(cluster, dict):
                continue
            page = _page_from_cluster(cluster, site, base)
            if page:
                pages.append(page)
    elif groups:
        # Ranking groups without Claude clusters: still map primary→URL, intent from the keyword.
        notes.append("Built from primary/secondary keyword groups (no Claude cluster report).")
        for group in groups[:_MAX_PAGES]:
            if not isinstance(group, dict):
                continue
            page = _page_from_group(group, site, base)
            if page:
                pages.append(page)
    else:
        return None

    if not pages:
        return None

    _assign_roles(pages)
    _fill_page_meta(pages, location=location, service=service)
    links = _hub_spoke_links(pages)
    return Architecture(
        service=service,
        location=location,
        fingerprint=fingerprint,
        pages=pages,
        links=links,
        notes=notes,
    )


def render_for_plan(arch: Architecture | None) -> str:
    """Section appended to Plan of Action's composed Phase 1 inputs."""
    if arch is None or not arch.pages:
        return ""
    return arch.to_markdown()


# -------------------------------------------------------------------------------------- builders


def _page_from_cluster(cluster: dict[str, Any], site: list[SitePage], base: str) -> MappedPage | None:
    name = str(cluster.get("name") or "").strip() or "(unnamed)"
    primary, volume = _primary_of_cluster(cluster)
    if not primary:
        return None
    intent = str(cluster.get("intent") or "").strip().lower()
    content_type = str(cluster.get("content_type") or cluster.get("recommended_content") or "").strip().lower()
    funnel = str(cluster.get("funnel") or "").strip().lower()
    if not intent or not content_type:
        guessed_intent, guessed_type = _guess_intent(primary)
        intent = intent or guessed_intent
        content_type = content_type or guessed_type
    recommended = str(cluster.get("recommended_url") or "").strip()
    url, status = _resolve_url(primary, name, recommended, site, base)
    return MappedPage(
        cluster=name,
        primary_keyword=primary,
        intent=intent,
        content_type=content_type,
        funnel=funnel,
        volume=volume,
        url=url,
        status=status,
        role="spoke",
    )


def _page_from_group(group: dict[str, Any], site: list[SitePage], base: str) -> MappedPage | None:
    primary = group.get("primary") if isinstance(group.get("primary"), dict) else {}
    keyword = str(primary.get("keyword") or "").strip()
    if not keyword:
        return None
    volume = primary.get("volume") if isinstance(primary.get("volume"), int) else None
    intent, content_type = _guess_intent(keyword)
    url, status = _resolve_url(keyword, keyword, "", site, base)
    return MappedPage(
        cluster=keyword.title(),
        primary_keyword=keyword,
        intent=intent,
        content_type=content_type,
        funnel="",
        volume=volume,
        url=url,
        status=status,
        role="spoke",
    )


def _primary_of_cluster(cluster: dict[str, Any]) -> tuple[str, int | None]:
    rows = [k for k in (cluster.get("keywords") or []) if isinstance(k, dict)]
    primary = next((k for k in rows if str(k.get("role") or "").lower() == "primary"), None)
    if primary is None and rows:
        primary = max(rows, key=lambda k: int(k["volume"]) if isinstance(k.get("volume"), int) else -1)
    if not primary:
        return "", None
    keyword = str(primary.get("keyword") or "").strip()
    volume = primary.get("volume") if isinstance(primary.get("volume"), int) else None
    return keyword, volume


def _guess_intent(keyword: str) -> tuple[str, str]:
    for pattern, intent, content_type in _INTENT_RULES:
        if pattern.search(keyword):
            return intent, content_type
    return "commercial", "landing"


def _resolve_url(
    primary: str,
    cluster_name: str,
    recommended: str,
    site: list[SitePage],
    base: str,
) -> tuple[str, str]:
    match = _match_site_page(primary, cluster_name, site)
    if match:
        return match.url, "existing"
    path = _as_path(recommended) or _slugify(primary)
    if base and path.startswith("/"):
        return f"{base.rstrip('/')}{path}", "proposed"
    return path, "proposed"


def _match_site_page(primary: str, cluster_name: str, site: list[SitePage]) -> SitePage | None:
    if not site:
        return None
    needle = _words(f"{primary} {cluster_name}")
    if not needle:
        return None
    best: tuple[int, SitePage] | None = None
    for page in site:
        score = len(needle & _words(f"{page.name} {page.url}"))
        if score >= 2 or (len(needle) == 1 and score == 1):
            if best is None or score > best[0]:
                best = (score, page)
    return best[1] if best else None


def _assign_roles(pages: list[MappedPage]) -> None:
    """Hub = best informational/guide page, else highest volume. Rest are spokes (or support if thin)."""
    if not pages:
        return
    hub_idx = 0
    best_score = -1
    for i, p in enumerate(pages):
        score = 0
        if p.intent == "informational":
            score += 5
        if any(t in (p.content_type or "") for t in ("guide", "pillar", "hub", "comprehensive")):
            score += 5
        if isinstance(p.volume, int):
            score += min(p.volume, 50_000) / 10_000
        if score > best_score:
            best_score = score
            hub_idx = i
    for i, p in enumerate(pages):
        if i == hub_idx:
            p.role = "hub"
        elif (p.volume or 0) < 50 and len(pages) > 6:
            p.role = "support"
        else:
            p.role = "spoke"


def _fill_page_meta(pages: list[MappedPage], *, location: str = "", service: str = "") -> None:
    """Propose a title tag and meta description per URL. Skips a page that already has both."""
    for page in pages:
        if page.title_tag and page.meta_description:
            continue
        title, meta = page_meta(
            primary=page.primary_keyword,
            cluster=page.cluster,
            intent=page.intent,
            content_type=page.content_type,
            location=location,
            service=service,
            role=page.role,
        )
        if not page.title_tag:
            page.title_tag = title
        if not page.meta_description:
            page.meta_description = meta


def page_meta(
    *,
    primary: str,
    cluster: str = "",
    intent: str = "",
    content_type: str = "",
    location: str = "",
    service: str = "",
    role: str = "spoke",
) -> tuple[str, str]:
    """Deterministic `<title>` and meta description from the measured keyword — never a client claim.

    Lengths match the scorer's SERP limits (60 / 155). The primary keyword is always present.
    """
    keyword = (primary or cluster or "page").strip()
    phrase = _display_keyword(keyword)
    where = f" in {location.strip()}" if location.strip() else ""
    intent_l = (intent or "").strip().lower()
    type_l = (content_type or "").strip().lower()
    is_hub = role == "hub" or any(t in type_l for t in ("guide", "pillar", "hub", "comprehensive"))

    if is_hub:
        title = f"{phrase}: Complete Guide" + (f" | {location.strip()}" if location.strip() else "")
    elif "listicle" in type_l or (intent_l == "commercial" and re.search(r"\bbest\b", keyword, re.I)):
        title = f"Best {phrase}{where}"
    elif "comparison" in type_l or intent_l == "commercial" and re.search(r"\bvs\b", keyword, re.I):
        title = f"{phrase} Compared" + (f" | {location.strip()}" if location.strip() else "")
    elif intent_l == "transactional":
        title = f"{phrase}{where}"
    elif service.strip() and service.strip().lower() not in keyword.lower():
        title = f"{phrase} | {service.strip()}"
    else:
        title = f"{phrase}{where}" if where else phrase
    title = _fit_length(title, _TITLE_LIMIT)

    topic = cluster.strip() if cluster.strip() and cluster.strip().lower() != keyword.lower() else ""
    if intent_l == "informational" or is_hub:
        meta = (
            f"Learn {keyword}{where}. A clear guide"
            + (f" on {topic}" if topic else "")
            + " covering what it is, how it works, and what to do next."
        )
    elif intent_l == "transactional":
        meta = f"Get {keyword}{where}. See what's included, who it's for, and how to get started."
    elif intent_l == "commercial":
        meta = f"Compare {keyword} options{where}. What to look for, common trade-offs, and how to choose."
    else:
        meta = f"{phrase}{where}. What to know before you decide, and how this page helps you act on it."
    meta = _fit_length(meta, _META_LIMIT)
    return title, meta


def _display_keyword(keyword: str) -> str:
    """Title-case a keyword for a SERP title without shouting short words."""
    small = {"a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "vs"}
    words = [w for w in re.split(r"\s+", keyword.strip()) if w]
    out: list[str] = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i > 0 and lower in small:
            out.append(lower)
        else:
            out.append(lower.upper() if len(lower) <= 2 and lower.isalpha() else lower.capitalize())
    return " ".join(out) or "Page"


def _fit_length(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return ((cut or cleaned[: limit - 1]).rstrip() + "…")[:limit]


def _hub_spoke_links(pages: list[MappedPage]) -> list[LinkEdge]:
    hub = next((p for p in pages if p.role == "hub"), None)
    if hub is None:
        return []
    spokes = [p for p in pages if p.role in ("spoke", "support") and p.url != hub.url]
    edges: list[LinkEdge] = []
    for spoke in spokes:
        edges.append(
            LinkEdge(
                from_url=hub.url,
                to_url=spoke.url,
                from_label=hub.cluster,
                to_label=spoke.cluster,
                anchor=spoke.primary_keyword,
                reason="hub → spoke",
            )
        )
        edges.append(
            LinkEdge(
                from_url=spoke.url,
                to_url=hub.url,
                from_label=spoke.cluster,
                to_label=hub.cluster,
                anchor=hub.primary_keyword,
                reason="spoke → hub",
            )
        )
    # Cross-link neighbouring spokes (SearchFit: every page in a cluster links to ≥2 others).
    for i, a in enumerate(spokes):
        b = spokes[(i + 1) % len(spokes)] if len(spokes) > 1 else None
        if b is None or a.url == b.url:
            continue
        edges.append(
            LinkEdge(
                from_url=a.url,
                to_url=b.url,
                from_label=a.cluster,
                to_label=b.cluster,
                anchor=b.primary_keyword,
                reason="spoke ↔ spoke",
            )
        )
    # Dedupe by from→to
    seen: set[tuple[str, str]] = set()
    out: list[LinkEdge] = []
    for e in edges:
        key = (e.from_url, e.to_url)
        if key in seen or e.from_url == e.to_url:
            continue
        seen.add(key)
        out.append(e)
        if len(out) >= _MAX_LINKS:
            break
    return out


def _site_origin(website: str) -> str:
    text = (website or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _origin_from_pages(site: list[SitePage]) -> str:
    for page in site:
        origin = _site_origin(page.url)
        if origin:
            return origin
    return ""


def _as_path(value: str) -> str:
    text = (value or "").strip()
    if not text or text in ("—", "-", "n/a", "N/A"):
        return ""
    if re.match(r"^https?://", text, re.I):
        parsed = urlparse(text)
        path = parsed.path or "/"
        return path if path.startswith("/") else f"/{path}"
    if text.startswith("/"):
        return text.split("?")[0].split("#")[0]
    # Bare slug
    if re.match(r"^[\w\-]+(/[\w\-]+)*\/?$", text):
        return "/" + text.strip("/")
    return ""


def _slugify(keyword: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    return f"/{slug}/" if slug else "/page/"


def _words(text: str) -> frozenset[str]:
    stop = {"a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "page", "https", "http", "www"}
    return frozenset(w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop and len(w) > 1)
