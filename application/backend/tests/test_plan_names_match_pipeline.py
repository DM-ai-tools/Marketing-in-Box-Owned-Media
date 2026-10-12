"""The Plan of Action names its phases and assets the way the pipeline does.

The plan used to call its phases "Quick Win (Single Pillar Ecosystem)" and "Sub-Service Expansion"
and list its own eight Phase 1 assets ("SEO Keyword Optimisation Pass", "Blog Post HTML Design"),
while the app's toggle said "Core service" / "Sub-service" and its stages were fourteen others. The
mind map's chips are drawn from the plan's text, so an operator saw two vocabularies for one
engagement. This reads the frontend's own catalog and phase metadata and requires the prompts, and
the labels `plan_context` puts in the plan's inputs, to use the same names.

No network and no model call.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import plan_context as pc
from app.services.generation import _PROMPTS_DIR

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
PROMPTS = ("Plan-of-Action-Architect-Prompt.md", "Phase2/Plan-of-Action-Architect-Prompt_phase2.md")


def _catalog_labels() -> dict[str, str]:
    text = (_FRONTEND / "data" / "assetCatalog.ts").read_text(encoding="utf-8")
    return dict(re.findall(r'asset_id: "([a-z_]+)",\s*\n\s*label: "([^"]+)"', text))


def _phase_scopes() -> dict[str, str]:
    text = (_FRONTEND / "pipeline" / "pipelineData.ts").read_text(encoding="utf-8")
    return dict(re.findall(r'id: "(phase\d)",\s*\n\s*label: "[^"]+",\s*\n\s*short: "[^"]+",\s*\n\s*scope: "([^"]+)"', text))


def _read(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def test_plan_context_labels_are_the_catalog_labels() -> None:
    catalog = _catalog_labels()
    for asset_id, label in pc.LABELS.items():
        assert catalog[asset_id] == label, asset_id


@pytest.mark.parametrize("name", PROMPTS)
def test_phase_titles_are_the_toggles(name: str) -> None:
    text = _read(name)
    scopes = _phase_scopes()
    assert set(scopes) == {"phase1", "phase2"}
    for phase, scope in scopes.items():
        n = int(phase[-1])
        title = f"Phase {n}: {scope}"
        assert re.search(rf"^\s*{n + 2}\. {re.escape(title)} \(Step", text, re.MULTILINE | re.IGNORECASE), title
        assert f"# STEP {n + 1} — PHASE {n}: {scope.upper()}" in text
    for old in ("Quick Win", "Single Pillar Ecosystem", "Sub-Service Expansion"):
        assert old not in text, old


@pytest.mark.parametrize("name", PROMPTS)
def test_phase1_lists_the_core_services_stages_in_order(name: str) -> None:
    catalog = _catalog_labels()
    step2 = _read(name).split("# STEP 2 —", 1)[1].split("# STEP 3 —", 1)[0]
    listed = re.findall(r"^\s{2}\d{1,2}\. (.+)$", step2, re.MULTILINE)
    assert listed == [catalog[a] for a in ("icp", *pc.PHASE1_ASSET_IDS)]


@pytest.mark.parametrize("name", PROMPTS)
def test_phase2_schema_is_the_sub_services_stages_in_order(name: str) -> None:
    catalog = _catalog_labels()
    step3 = _read(name).split("# STEP 3 —", 1)[1].split("# STEP 4 —", 1)[0]
    fields = re.findall(r"^\s{2}([A-Z][A-Z —/()-]+[A-Z)]):", step3, re.MULTILINE)
    stage_fields = [f for f in fields if f not in {"TARGET KEYWORD CLUSTER"}]
    assert stage_fields == [catalog[a].upper() for a in pc.PHASE2_ASSET_IDS]
