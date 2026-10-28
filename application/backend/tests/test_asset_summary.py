"""The reader's Summary tab. Never reaches Anthropic: `asset_summary._ask` is patched."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import asset_summary

DOC = """# Value Ladder

## Offers

1. **[Audit] "Free SEO Health Check"**
- Price: $0
- Ascends To: Growth Retainer

2. **[Retainer] "Growth Retainer"**
- Price: $1,500/mo

## Scorecard

Total 21/25.

```html
<!doctype html><html><body><div>Old page 9999</div></body></html>
```
"""


def _reply(points: list[dict]) -> str:
    lines = [f"- {p['text']} || {p['section'] or '-'}" for p in points]
    return "Here are the points:\n" + "\n".join(lines)


def test_a_quote_inside_a_point_cannot_break_the_reply() -> None:
    raw = '- She says "fix what is broken" before spending again || Offers\n- Two offers. || -\nnot a point'
    assert asset_summary.parse_points(raw) == [
        ('She says "fix what is broken" before spending again', "Offers"),
        ("Two offers.", ""),
    ]


def test_points_with_invented_numbers_are_dropped_and_sections_must_exist() -> None:
    raw = _reply(
        [
            {"text": "A free SEO Health Check at $0 leads into the Growth Retainer.", "section": "Offers"},
            {"text": "The Growth Retainer costs $1,500/mo.", "section": "**Offers**"},
            {"text": "The retainer costs $2,000 a month.", "section": "Offers"},
            {"text": "The scorecard totals 21/25.", "section": "Nonexistent section"},
        ]
    )
    summary = asset_summary._verified_points(raw, DOC)
    assert [p.text for p in summary.points] == [
        "A free SEO Health Check at $0 leads into the Growth Retainer.",
        "The Growth Retainer costs $1,500/mo.",
        "The scorecard totals 21/25.",
    ]
    assert [p.section for p in summary.points] == ["Offers", "Offers", ""]
    assert summary.dropped == 1


def test_too_many_points_are_thinned_evenly_and_the_end_is_kept() -> None:
    raw = _reply([{"text": f"Point about part {chr(65 + i)}.", "section": ""} for i in range(11)])
    points = [p.text for p in asset_summary._verified_points(raw, DOC).points]
    assert len(points) == asset_summary.MAX_POINTS
    assert points[0] == "Point about part A." and points[-1] == "Point about part K."


def test_a_built_page_is_not_shown_to_the_model() -> None:
    prompt = asset_summary.build_prompt("Offers", DOC)
    assert "Old page 9999" not in prompt and "[A built HTML page is here.]" in prompt
    assert "- Offers" in prompt and "- Scorecard" in prompt


def test_a_reply_with_nothing_checkable_is_an_error() -> None:
    with pytest.raises(asset_summary.SummaryError):
        asset_summary._verified_points("no json here", DOC)
    with pytest.raises(asset_summary.SummaryError):
        asset_summary._verified_points(_reply([{"text": "It costs $7,777.", "section": ""}]), DOC)


def test_too_short_to_summarise_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(*_a: Any) -> str:
        raise AssertionError("no call for an empty document")

    monkeypatch.setattr(asset_summary, "_ask", never)
    with pytest.raises(asset_summary.SummaryError, match="not enough"):
        asyncio.run(asset_summary.summarise("ICP", "# Empty"))


def test_the_route_returns_points_or_a_422(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ask(_prompt: str, _on_usage: Any) -> str:
        return _reply([{"text": "Two offers: a $0 audit and a $1,500/mo retainer.", "section": "Offers"}])

    monkeypatch.setattr(asset_summary, "_ask", ask)
    client = TestClient(app)
    ok = client.post("/pipeline/summary/offers", json={"text": DOC, "label": "Offers"})
    assert ok.status_code == 200
    assert ok.json()["points"] == [{"text": "Two offers: a $0 audit and a $1,500/mo retainer.", "section": "Offers"}]

    bad = client.post("/pipeline/summary/offers", json={"text": "short", "label": "Offers"})
    assert bad.status_code == 422 and "not enough" in bad.json()["detail"]
