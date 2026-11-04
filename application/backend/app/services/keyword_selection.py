"""Which of a run's keyword clusters the operator wants later stages to use.

The demand map is built once per run and clustered by a model, so some clusters are about a service
the client does not push, or a topic the operator does not want a page for. The operator ticks the
ones to keep; every later reader (keyword targets, the SEO map, headline suggestions, the plan, the
business check) sees only those.

The choice is stored on the `keyword_clusters` entry itself as `selected_clusters` (a list of cluster
names), as a new version of that entry. Absent means every cluster, which is what every report
stored before this existed means too. Nothing reads the key directly: `apply` returns the entry
value as the readers expect it, narrowed to the selection, so a reader changes by one call.

A keyword belongs to the selection when it is a member of a selected cluster. Keywords in no cluster
(orphans) and keywords only in unselected clusters are left out, so an unticked topic cannot come
back in through the ranked list.
"""

from __future__ import annotations

import copy
from typing import Any

SELECTION_KEY = "selected_clusters"

#: Stored groups carry the primary/secondary split of the full ranking. Once the ranking is narrowed
#: they would still name unselected keywords, so they are dropped and regrouped from what is left
#: (`pipeline._groups_from_stored` and `keyword_rank.targets_from_entry` both do that on their own).
_REGROUPED_KEYS = ("keyword_groups",)

#: Keyword lists in the stored value, each a list of dicts with a `keyword`.
_KEYWORD_LISTS = ("clean_keywords", "ranked_keywords")


def _norm(word: Any) -> str:
    return " ".join(str(word or "").casefold().split())


def cluster_names(value: Any) -> list[str]:
    """Every cluster name in a stored `keyword_clusters` value, in report order."""
    report = value.get("keyword_report") if isinstance(value, dict) else None
    clusters = report.get("clusters") if isinstance(report, dict) else None
    return [str(c.get("name") or "(unnamed)") for c in clusters or [] if isinstance(c, dict)]


def selected_names(value: Any) -> set[str] | None:
    """The chosen cluster names, or None when the operator has not narrowed the set (all of them)."""
    raw = value.get(SELECTION_KEY) if isinstance(value, dict) else None
    if not isinstance(raw, list):
        return None
    return {str(n) for n in raw}


def apply(value: Any) -> Any:
    """`value` narrowed to the selected clusters. The same object when nothing is narrowed."""
    chosen = selected_names(value)
    if chosen is None or not isinstance(value, dict):
        return value
    out = copy.copy(value)
    report = value.get("keyword_report") if isinstance(value.get("keyword_report"), dict) else {}
    clusters = [c for c in report.get("clusters") or [] if isinstance(c, dict) and str(c.get("name") or "(unnamed)") in chosen]
    out["keyword_report"] = {**report, "clusters": clusters}

    allowed = {
        _norm(e.get("keyword"))
        for c in clusters
        for e in c.get("keywords") or []
        if isinstance(e, dict) and e.get("keyword")
    }
    for key in _KEYWORD_LISTS:
        if isinstance(value.get(key), list):
            out[key] = [r for r in value[key] if isinstance(r, dict) and _norm(r.get("keyword")) in allowed]
    for key in _REGROUPED_KEYS:
        out.pop(key, None)
    if isinstance(out["keyword_report"].get("keyword_groups"), list):
        out["keyword_report"] = {k: v for k, v in out["keyword_report"].items() if k != "keyword_groups"}
    return out


def with_selection(value: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """A copy of `value` recording `names` as the selection. Choosing every cluster clears it, so the
    stored entry says "all" the one way a fresh report does."""
    out = dict(value)
    everything = cluster_names(value)
    if set(names) >= set(everything):
        out.pop(SELECTION_KEY, None)
    else:
        out[SELECTION_KEY] = [n for n in everything if n in set(names)]
    return out
