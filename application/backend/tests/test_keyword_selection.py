"""The operator's choice of keyword clusters, and that every reader sees only the chosen ones."""

from __future__ import annotations

from app.routers.pipeline import _stored_report_response
from app.services import keyword_rank, keyword_selection, seo_architecture


def _kw(keyword: str, volume: int) -> dict:
    return {"keyword": keyword, "volume": volume, "difficulty": 30}


def _entry() -> dict:
    meta = [_kw("meta ads agency", 900), _kw("meta ads agency melbourne", 300)]
    seo = [_kw("seo agency", 2000), _kw("seo agency melbourne", 700)]
    orphan = [_kw("random thing", 50)]
    clusters = [
        {
            "name": "Meta ads",
            "intent": "commercial",
            "primary_keyword": "meta ads agency",
            "keywords": [{**k, "role": "primary" if i == 0 else "secondary"} for i, k in enumerate(meta)],
        },
        {
            "name": "SEO",
            "intent": "commercial",
            "primary_keyword": "seo agency",
            "keywords": [{**k, "role": "primary" if i == 0 else "secondary"} for i, k in enumerate(seo)],
        },
    ]
    rows = meta + seo + orphan
    return {
        "provider": "dataforseo",
        "service": "Social Media Marketing",
        "config_fingerprint": "fp",
        "keyword_report": {"clusters": clusters, "total_keywords": len(rows)},
        "clean_keywords": rows,
        "ranked_keywords": rows,
        "keyword_groups": [{"primary": rows[0], "secondary": [], "combined_volume": 900}],
        "location": "Australia",
    }


def test_no_selection_is_every_cluster_and_the_value_is_untouched() -> None:
    value = _entry()
    assert keyword_selection.selected_names(value) is None
    assert keyword_selection.apply(value) is value


def test_choosing_every_cluster_clears_the_selection() -> None:
    value = keyword_selection.with_selection(_entry(), ["SEO", "Meta ads"])
    assert keyword_selection.SELECTION_KEY not in value


def test_selection_keeps_report_order_and_drops_unknown_names_from_the_stored_list() -> None:
    value = keyword_selection.with_selection(_entry(), ["SEO"])
    assert value[keyword_selection.SELECTION_KEY] == ["SEO"]


def test_apply_narrows_clusters_and_keyword_lists_to_the_selection() -> None:
    value = keyword_selection.with_selection(_entry(), ["SEO"])
    view = keyword_selection.apply(value)
    assert [c["name"] for c in view["keyword_report"]["clusters"]] == ["SEO"]
    kept = {r["keyword"] for r in view["clean_keywords"]}
    assert kept == {"seo agency", "seo agency melbourne"}
    assert {r["keyword"] for r in view["ranked_keywords"]} == kept
    # The stored groups named the full ranking, so they are regrouped by the readers instead.
    assert "keyword_groups" not in view
    # Applying never edits what is stored.
    assert len(value["clean_keywords"]) == 5


def test_keyword_match_ignores_case_and_spacing() -> None:
    value = _entry()
    value["clean_keywords"][2] = _kw("  SEO   Agency ", 2000)
    view = keyword_selection.apply(keyword_selection.with_selection(value, ["SEO"]))
    assert len(view["clean_keywords"]) == 2


def test_targets_come_only_from_the_selected_clusters() -> None:
    value = keyword_selection.with_selection(_entry(), ["SEO"])
    targets = keyword_rank.targets_from_entry(keyword_selection.apply(value))
    words = {g.primary.keyword for g in targets.groups} | {k.keyword for g in targets.groups for k in g.secondary}
    assert words and words <= {"seo agency", "seo agency melbourne"}


def test_seo_map_is_built_from_the_selected_clusters_only() -> None:
    value = keyword_selection.with_selection(_entry(), ["Meta ads"])
    arch = seo_architecture.build_from_keyword_entry(keyword_selection.apply(value), website="https://example.com")
    assert arch is not None
    assert [p.cluster for p in arch.pages] == ["Meta ads"]


def test_response_lists_every_cluster_flagged_and_counts_only_the_kept_keywords() -> None:
    value = keyword_selection.with_selection(_entry(), ["SEO"])
    out = _stored_report_response("run", "phase1", value, 2)
    assert [(c.name, c.selected) for c in out.clusters] == [("Meta ads", False), ("SEO", True)]
    assert out.total_keywords == 2
    assert {k["keyword"] for k in out.ranked} == {"seo agency", "seo agency melbourne"}


def test_response_without_a_selection_flags_all_selected() -> None:
    out = _stored_report_response("run", "phase1", _entry(), 1)
    assert all(c.selected for c in out.clusters)
    assert out.total_keywords == 5
