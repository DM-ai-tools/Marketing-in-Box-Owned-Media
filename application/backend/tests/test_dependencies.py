"""Tests for the asset dependency graph — `app/services/dependencies.py`.

Why this file is worth its length
---------------------------------
The graph is what the two standalone-run routes answer from: "can `offers` run on this run" and
"what do I have to hand it if not". Every fact in it is derived from the draft schemas at import
time except two tables — `WRITES` and `PREPASS` — which are a second copy of information the
frontend's `assetCatalog.ts` also holds. A second copy that drifts is worse than no copy: readiness
would report a dependency as satisfiable by running an asset that does not write it, and the
operator would run that asset and be asked for the document anyway. So the drift tests at the top
parse the TypeScript and compare, and they are the reason keeping the copy is acceptable at all.

The rest pins the distinctions the dependency report is *for*, because each one is a wrong answer an
operator would act on:

  - a competitor prepass is not a dependency (it runs itself; there is no stage to run first),
  - `unresolved_context_key` is not a missing document (it is a question, asked every time),
  - a Phase 2 stage does not depend on a field Phase 2 drops,
  - and the "run these first" chain is short — one or two assets, not the whole pipeline.

No network and no database: everything here reads the schemas on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import dependencies as D
from app.services.generation import CONFIGS_BY_PHASE, STAGE_CONFIGS

# --------------------------------------------------------------------------------------
# Drift: the two hand-maintained tables against the frontend catalog
# --------------------------------------------------------------------------------------

_CATALOG = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "assetCatalog.ts"
)

# The main assets declare `asset_id: "x"` literally. The ten competitor assets are built by the
# `competitorAsset(...)` factory instead, so they have no literal `asset_id:` line and do not appear
# here — which is what we want: their own `pairedCompetitorAssetId` is a *back*-pointer to their main
# asset, the inverse of the mapping `PREPASS` holds.
_ASSET_ID = re.compile(r'asset_id:\s*"([a-z_0-9]+)"')


def _catalog_blocks() -> dict[str, str]:
    if not _CATALOG.exists():  # pragma: no cover - monorepo checkout always has it
        pytest.skip(f"frontend catalog not present at {_CATALOG}")
    text = _CATALOG.read_text(encoding="utf-8")
    marks = [(m.group(1), m.start(), m.end()) for m in _ASSET_ID.finditer(text)]
    blocks: dict[str, str] = {}
    for index, (asset_id, _, end) in enumerate(marks):
        stop = marks[index + 1][1] if index + 1 < len(marks) else len(text)
        blocks[asset_id] = text[end:stop]
    return blocks


def test_the_catalog_is_parseable_and_holds_every_main_asset() -> None:
    """Guard on the guard: a regex that silently matched nothing would make every drift test below
    pass vacuously."""
    blocks = _catalog_blocks()
    assert set(blocks) == set(STAGE_CONFIGS), (
        "catalog asset ids and the Phase 1 stage table disagree: "
        f"catalog-only={sorted(set(blocks) - set(STAGE_CONFIGS))} "
        f"stages-only={sorted(set(STAGE_CONFIGS) - set(blocks))}"
    )


def test_writes_matches_the_frontend_catalog() -> None:
    parsed: dict[str, tuple[str, ...]] = {}
    for asset_id, block in _catalog_blocks().items():
        found = re.search(r"writesContextKeys:\s*\[([^\]]*)\]", block)
        assert found is not None, f"{asset_id} declares no writesContextKeys"
        keys = tuple(k.strip().strip('"') for k in found.group(1).split(",") if k.strip())
        parsed[asset_id] = keys
    assert D.WRITES == parsed


def test_prepass_matches_the_frontend_catalog() -> None:
    parsed = {}
    for asset_id, block in _catalog_blocks().items():
        found = re.search(r'pairedCompetitorAssetId:\s*"([a-z_]+)"', block)
        if found is not None:
            parsed[asset_id] = found.group(1)
    assert D.PREPASS == parsed


def test_every_prepass_names_a_competitor_asset() -> None:
    """A prepass id that was not a `competitor_analysis_*` would mean the pairing had been pointed at
    a main asset — the back-pointer direction — which readiness would then report as a stage that
    runs itself."""
    for main, prepass in D.PREPASS.items():
        assert prepass.startswith("competitor_analysis_"), f"{main} -> {prepass}"
        assert prepass != main


# --------------------------------------------------------------------------------------
# Producers
# --------------------------------------------------------------------------------------


def test_every_asset_writes_at_least_its_own_key() -> None:
    for asset_id in STAGE_CONFIGS:
        assert D.WRITES[asset_id], asset_id


def test_producer_of_resolves_extra_keys_to_their_asset() -> None:
    assert D.producer_of("cro_rewritten_copy") == "cro"
    assert D.producer_of("design_tokens") == "pillar_page"
    assert D.producer_of("offer_ladder") == "offers"
    assert D.producer_of("webinar_script") == "webinar"


def test_producer_of_is_none_for_the_placeholder_not_a_lookup_failure() -> None:
    assert D.producer_of(D.UNRESOLVED) is None


def test_email_sequence_copy_has_no_producer() -> None:
    """`sms_sequence` reads it and no asset in the pipeline writes it, so "run the producer first" is
    never an option for it and seeding is the only one. If an email stage is ever added this test is
    the reminder to say so."""
    assert D.producer_of("email_sequence_copy") is None


def test_the_only_producerless_dependency_keys_are_the_named_four() -> None:
    producerless: set[str] = set()
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            for dep in D.dependencies_for(asset_id, phase).dependencies:
                if dep.producer is None:
                    producerless.add(dep.context_key)
    assert producerless == {D.UNRESOLVED, "email_sequence_copy"}


def test_satisfiable_by_running_tracks_the_producer() -> None:
    deps = {d.field_id: d for d in D.dependencies_for("sms_sequence").dependencies}
    assert deps["email_sequence_copy"].satisfiable_by_running is False
    assert deps["icp_document"].satisfiable_by_running is True


# --------------------------------------------------------------------------------------
# The prepass is not a dependency
#
# This is the distinction the whole module exists to make. `lead_magnet` pins it hardest: its
# competitor list is a *required* field that no asset upstream produces, so reported as a dependency
# it would send an operator looking for a stage that does not exist, and readiness would call the
# stage permanently blocked.
# --------------------------------------------------------------------------------------


def test_no_prepass_key_is_ever_reported_as_a_dependency() -> None:
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            spec = D.dependencies_for(asset_id, phase)
            prepass_keys = set(D.PREPASS.values())
            leaked = [d.context_key for d in spec.dependencies if d.context_key in prepass_keys]
            assert not leaked, f"{phase}/{asset_id} reports prepass keys as dependencies: {leaked}"


def test_a_stage_with_a_prepass_reports_it_and_the_field_it_fills() -> None:
    spec = D.dependencies_for("offers")
    assert spec.prepass == "competitor_analysis_offers"
    assert [d.field_id for d in spec.prepass_fields] == ["competitor_analysis"]
    assert all(d.context_key == "competitor_analysis_offers" for d in spec.prepass_fields)


def test_lead_magnets_required_competitor_list_is_a_prepass_field_not_a_requirement() -> None:
    spec = D.dependencies_for("lead_magnet")
    assert [d.field_id for d in spec.prepass_fields] == ["competitor_lead_magnet_list"]
    assert spec.prepass_fields[0].required is True
    assert "competitor_lead_magnet_list" not in {d.field_id for d in spec.required}


def test_a_stage_without_a_prepass_reports_neither() -> None:
    spec = D.dependencies_for("sms_sequence")
    assert spec.prepass is None
    assert spec.prepass_fields == ()


def test_every_asset_with_a_prepass_has_a_field_for_it() -> None:
    """A pairing with no field to fill would run a web search whose result nothing reads."""
    for asset_id in D.PREPASS:
        spec = D.dependencies_for(asset_id)
        assert spec.prepass_fields, f"{asset_id} pairs a prepass but reads no key from it"


# --------------------------------------------------------------------------------------
# What each asset actually needs
# --------------------------------------------------------------------------------------


def test_offers_needs_only_the_icp() -> None:
    """The example the dependency report is written around: skip the eleven stages between and
    `offers` still runs, given one document."""
    spec = D.dependencies_for("offers")
    assert [d.context_key for d in spec.required] == ["icp"]
    assert D.transitive_producers("offers") == ("icp",)


def test_icp_needs_nothing_upstream() -> None:
    assert D.dependencies_for("icp").dependencies == ()
    assert D.transitive_producers("icp") == ()


def test_pillar_page_reads_no_icp_field_at_all() -> None:
    """Every other asset after `icp` has one, required in eleven. `pillar_page` inherits the audience
    only through the CRO rewrite it is handed — which is worth a failing test if it ever changes,
    because a standalone pillar page is exactly as audience-aware as that copy."""
    keys = {d.context_key for d in D.dependencies_for("pillar_page").dependencies}
    assert "icp" not in keys
    others = set(STAGE_CONFIGS) - {"icp", "pillar_page"}
    without = sorted(
        a for a in others if "icp" not in {d.context_key for d in D.dependencies_for(a).dependencies}
    )
    assert without == []


def test_social_audit_needs_no_upstream_asset() -> None:
    """A leaf: its own competitor scan plus questions. It is the cheapest stage to run standalone."""
    spec = D.dependencies_for("social_content_strategy_audit")
    assert spec.required == ()
    assert D.transitive_producers("social_content_strategy_audit") == ()


@pytest.mark.parametrize(
    ("asset_id", "chain"),
    [
        ("offers", ("icp",)),
        ("pillar_page", ("icp", "cro")),
        ("blog", ("icp", "cro", "pillar_page")),
        ("book", ("icp", "cro", "webinar")),
        ("sms_sequence", ("icp", "cro", "pillar_page", "funnel")),
    ],
)
def test_the_run_first_chain_is_short_and_ordered(asset_id: str, chain: tuple[str, ...]) -> None:
    """Dependency order, so the list can be run top to bottom. Four assets is the longest chain in
    the pipeline — the point of the report being that nothing needs all fifteen."""
    assert D.transitive_producers(asset_id) == chain


def test_transitive_producers_never_includes_the_asset_itself() -> None:
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            assert asset_id not in D.transitive_producers(asset_id, phase)


def test_no_chain_is_longer_than_the_pipeline() -> None:
    """Terminates, i.e. the required-dependency graph has no cycle. A cycle would hang the readiness
    walk rather than return a wrong answer."""
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            chain = D.transitive_producers(asset_id, phase)
            assert len(chain) == len(set(chain))
            assert len(chain) <= len(configs)


def test_the_set_of_hard_prerequisites_is_small_and_fixed() -> None:
    """The load-bearing finding of the dependency report: of fifteen assets, only five are ever a
    required input to another, and ten are leaves nothing waits on. If a sixth appears, standalone
    runs have got more expensive and the report needs revisiting."""
    prerequisites: set[str] = set()
    for asset_id in STAGE_CONFIGS:
        prerequisites.update(D.transitive_producers(asset_id))
    assert prerequisites == {"icp", "cro", "pillar_page", "funnel", "webinar"}


# --------------------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------------------


def test_phase2_does_not_depend_on_a_field_phase2_drops() -> None:
    """`PHASE2_OVERRIDES` drops `cro_locked_sections` from the pillar page, because the Phase 2
    prompt file has no input for it. A dropped field is not a dependency, so the phase has to be read
    through `_config` rather than the schema alone."""
    phase1 = {d.context_key for d in D.dependencies_for("pillar_page", "phase1").dependencies}
    phase2 = {d.context_key for d in D.dependencies_for("pillar_page", "phase2").dependencies}
    assert "cro_locked_sections" in phase1
    assert "cro_locked_sections" not in phase2
    assert phase1 - phase2 == {"cro_locked_sections"}


def test_asking_for_a_stage_a_phase_does_not_run_raises() -> None:
    assert "offers" not in CONFIGS_BY_PHASE["phase2"]
    with pytest.raises(KeyError):
        D.dependencies_for("offers", "phase2")


def test_wildcard_keys_resolve_to_the_key_an_operator_would_recognise() -> None:
    """Thirteen fields declare `icp_*`. Reported raw, the gate would offer to seed a key called
    `icp_*` and nothing would ever read it."""
    assert D._resolve_wildcard("icp_*") == "icp"
    assert D._resolve_wildcard("icp") == "icp"
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            for dep in D.dependencies_for(asset_id, phase).dependencies:
                assert not dep.context_key.endswith("_*")


# --------------------------------------------------------------------------------------
# Seedable keys
# --------------------------------------------------------------------------------------


def test_seedable_keys_are_exactly_what_some_stage_reads() -> None:
    read: set[str] = set()
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            for dep in D.dependencies_for(asset_id, phase).dependencies:
                read.add(dep.context_key)
    assert D.seedable_keys() == frozenset(read - {D.UNRESOLVED})


def test_the_placeholder_is_not_seedable() -> None:
    assert D.UNRESOLVED not in D.seedable_keys()


def test_no_prepass_key_is_seedable() -> None:
    """Not a restriction for its own sake: offering to paste a competitor list under a key the stage
    is about to overwrite with its own search would waste the operator's time."""
    assert D.seedable_keys().isdisjoint(set(D.PREPASS.values()))


def test_the_four_prerequisites_are_all_seedable() -> None:
    """Which is what makes "skip everything in between" work — the documents you would otherwise
    have to run for can all be handed over instead."""
    for key in ("icp", "cro_rewritten_copy", "design_tokens", "funnel_stages", "webinar_script"):
        assert key in D.seedable_keys(), key


def test_a_producerless_key_is_still_seedable() -> None:
    assert "email_sequence_copy" in D.seedable_keys()


# --------------------------------------------------------------------------------------
# The two routes, as far as they go without a database
#
# Both reject before they open a session, which is what makes these testable here: a bad run id, a
# key nothing reads, and a stage the phase does not run are all decided from the request alone. The
# readiness happy path needs Postgres and is not covered.
# --------------------------------------------------------------------------------------


@pytest.fixture(name="client")
def _client() -> TestClient:
    return TestClient(app)


def test_seeding_rejects_a_malformed_run_id(client: TestClient) -> None:
    response = client.post(
        "/pipeline/runs/not-a-uuid/context", json={"context_key": "icp", "content": "x"}
    )
    assert response.status_code == 400


def test_seeding_rejects_a_key_nothing_reads_and_says_which_are_allowed(
    client: TestClient,
) -> None:
    response = client.post(
        "/pipeline/runs/00000000-0000-0000-0000-000000000000/context",
        json={"context_key": "icp_documnet", "content": "x"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "icp_documnet" in detail
    # The list is in the message on purpose: the operator who typo'd a key needs the right one, not
    # a second request to find it.
    assert "icp" in detail
    assert "offer_ladder" in detail


def test_seeding_rejects_a_prepass_key(client: TestClient) -> None:
    response = client.post(
        "/pipeline/runs/00000000-0000-0000-0000-000000000000/context",
        json={"context_key": "competitor_analysis_offers", "content": "x"},
    )
    assert response.status_code == 422


def test_seeding_rejects_blank_content(client: TestClient) -> None:
    """A blank seed satisfies every downstream check while carrying nothing — worse for the operator
    than the stage having asked."""
    response = client.post(
        "/pipeline/runs/00000000-0000-0000-0000-000000000000/context",
        json={"context_key": "icp", "content": "   "},
    )
    assert response.status_code == 422


def test_readiness_rejects_a_malformed_run_id(client: TestClient) -> None:
    response = client.get("/pipeline/runs/not-a-uuid/readiness/offers")
    assert response.status_code == 400


def test_readiness_404s_for_a_stage_the_phase_does_not_run(client: TestClient) -> None:
    response = client.get(
        "/pipeline/runs/00000000-0000-0000-0000-000000000000/readiness/offers",
        params={"phase": "phase2"},
    )
    assert response.status_code == 404
    assert "phase2" in response.json()["detail"]


def test_readiness_404s_for_an_unknown_asset(client: TestClient) -> None:
    response = client.get(
        "/pipeline/runs/00000000-0000-0000-0000-000000000000/readiness/no_such_asset"
    )
    assert response.status_code == 404
