"""Which assets an asset needs before it can run, and which of those a run already has.

Why this exists
---------------
The pipeline was built to run in order: fifteen stages, each reading the approved output of the ones
before it. That is the right default and it is not the only way an operator wants to work. A client
who already has a value ladder wants the Offers stage skipped; a client who wants only a Plan of
Action should not have to pass through thirteen stages to reach it.

The dependency information to support that already existed but was scatt: `context_key`, `required`
and `fallback` live in `schemas/drafts/*.json`, while which asset *writes* each key lived only in
the frontend's `assetCatalog.ts`. Nothing on the server could answer "what does `offers` actually
need". This module answers it, so the two routes that make standalone runs possible —
`POST /runs/{id}/context` to seed a dependency and `GET /runs/{id}/readiness/{asset}` to report on
them — have one source of truth rather than a hand-maintained list each.

The shape of the answer, from `docs/Asset_Dependency_Map.md`
-----------------------------------------------------------
Only four assets are ever a hard prerequisite for another: `icp` (needed by eleven), `cro` (its
rewrite feeds six), `pillar_page` (its `design_tokens` feeds three) and `funnel`/`webinar` (one
each). Everything else is a leaf. So most "skip to stage N" requests need one or two upstream
documents, not a rebuild of the whole run.

What is *not* a dependency
--------------------------
The ten `competitor_analysis_*` prepasses. Each runs itself inside its paired stage and does its own
web search, so a stage with a prepass needs no upstream asset for it — see `prepass_for`. They are
reported separately from dependencies for exactly that reason: telling an operator that `offers`
"requires a competitor analysis" would send them looking for a stage to run first, when the answer
is that it happens on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from app.services.generation import CONFIGS_BY_PHASE, DEFAULT_PHASE, _SCHEMAS_DIR, _config

# --------------------------------------------------------------------------------------
# Which asset writes which context key.
#
# Declared here rather than derived, because the server has no other copy of it: an asset's own
# output is stored under `context_key = asset_id`, and these are the *extra* keys each one
# publishes. It mirrors `writesContextKeys` in `application/frontend/src/data/assetCatalog.ts`, and
# `tests/test_dependencies.py` parses that file to prove the two have not drifted — which is the
# only reason it is safe to keep a second copy at all.
# --------------------------------------------------------------------------------------
WRITES: dict[str, tuple[str, ...]] = {
    "icp": ("icp",),
    "cro": (
        "cro_audit_findings",
        "cro_rewritten_copy",
        "cro_locked_sections",
        "cro_terminology_map",
        "cro_client_settings",
    ),
    "pillar_page": ("pillar_page_html", "design_tokens", "seo_pillar_page_copy"),
    "funnel": ("funnel_stages",),
    "funnel_hub_media": ("funnel_hub_media",),
    "offers": ("offer_ladder",),
    "lead_magnet": ("lead_magnet",),
    "blog": ("blog",),
    "content_marketing_strategy": ("content_marketing_strategy",),
    "social_content_strategy_audit": ("social_content_strategy_audit",),
    "webinar": ("webinar_script",),
    "book": ("book",),
    "podcast": ("podcast",),
    "sms_sequence": ("sms_sequence",),
    "plan_of_action": ("plan_of_action_summary",),
}

#: main asset -> its competitor prepass. Mirrors `pairedCompetitorAssetId`; same drift test.
PREPASS: dict[str, str] = {
    "cro": "competitor_analysis_cro",
    "pillar_page": "competitor_analysis_seo_pillar_page",
    "offers": "competitor_analysis_offers",
    "lead_magnet": "competitor_analysis_lead_magnet",
    "blog": "competitor_analysis_blog",
    "content_marketing_strategy": "competitor_analysis_content_marketing",
    "social_content_strategy_audit": "competitor_analysis_social_content_strategy",
    "webinar": "competitor_analysis_webinars",
    "book": "competitor_analysis_book",
    "podcast": "competitor_analysis_podcast",
    "funnel": "competitor_analysis_funnel",
}

#: The placeholder three schemas use for "there is no upstream key for this — always ask".
UNRESOLVED = "unresolved_context_key"


@dataclass(frozen=True)
class Dependency:
    """One field an asset fills from an upstream asset's output."""

    field_id: str
    label: str
    context_key: str
    #: Set when the field reads one value out of a document rather than the whole thing (the CRO
    #: rewrite's terminology row). A sub-key that cannot be found falls through to being asked.
    sub_key: str | None
    required: bool
    fallback: str
    #: The asset whose approved output lands under `context_key`, or None when nothing writes it.
    producer: str | None

    @property
    def satisfiable_by_running(self) -> bool:
        """Whether running another asset could fill this. False for the three permanently-manual
        fields and for `email_sequence_copy`, which no asset writes."""
        return self.producer is not None


@dataclass(frozen=True)
class AssetDependencies:
    asset_id: str
    phase: str
    dependencies: tuple[Dependency, ...]
    writes: tuple[str, ...]
    #: The competitor prepass that runs inside this stage, if it has one. Not a dependency.
    prepass: str | None
    #: Fields the prepass fills. Reported apart from `dependencies` on purpose: they read a context
    #: key like any other, but that key is written by a search running *inside* this stage, so
    #: there is no asset to run first. Listing them as dependencies sends an operator hunting for a
    #: stage that does not exist — `lead_magnet`'s competitor list is `required` and yet nothing
    #: upstream produces it.
    prepass_fields: tuple[Dependency, ...] = ()

    @property
    def required(self) -> tuple[Dependency, ...]:
        return tuple(d for d in self.dependencies if d.required)

    @property
    def optional(self) -> tuple[Dependency, ...]:
        return tuple(d for d in self.dependencies if not d.required)

    @property
    def producers(self) -> tuple[str, ...]:
        """Distinct upstream assets this one reads from, in first-mention order."""
        out: list[str] = []
        for dep in self.dependencies:
            if dep.producer and dep.producer not in out:
                out.append(dep.producer)
        return tuple(out)


@lru_cache(maxsize=1)
def _producer_index() -> dict[str, str]:
    """context key -> the asset that writes it.

    Built from `WRITES` plus the implicit `context_key = asset_id` each asset publishes, and plus
    the prepasses, whose output is stored under their own asset id.
    """
    index: dict[str, str] = {}
    for asset, keys in WRITES.items():
        index.setdefault(asset, asset)
        for key in keys:
            index.setdefault(key, asset)
    for prepass in PREPASS.values():
        index.setdefault(prepass, prepass)
    return index


def producer_of(context_key: str) -> str | None:
    """Which asset's output lands under `context_key`, or None.

    None is a real answer for three keys, not a lookup failure: `unresolved_context_key` (the
    always-ask placeholder in three schemas) and `email_sequence_copy`, which `sms_sequence` reads
    and no asset writes.
    """
    if context_key == UNRESOLVED:
        return None
    return _producer_index().get(context_key)


def _resolve_wildcard(context_key: str) -> str:
    """`icp_*` -> `icp`.

    Thirteen fields declare their key as a prefix wildcard, which the frontend's `resolveContext`
    matches against whatever the session holds. Server-side there is exactly one key per prefix in
    practice, so the prefix is the key — and reporting the dependency as `icp` rather than `icp_*`
    is what lets an operator recognise it as "the ICP".
    """
    return context_key[:-2] if context_key.endswith("_*") else context_key


@lru_cache(maxsize=64)
def dependencies_for(asset_id: str, phase: str = DEFAULT_PHASE) -> AssetDependencies:
    """Every upstream document `asset_id` reads, with the phase's field deltas applied.

    Phase 2 drops fields its own prompt files do not have (`PHASE2_OVERRIDES` in `generation.py`),
    and a dropped field is not a dependency — so the phase is read through `_config` rather than
    reimplemented here.
    """
    cfg = _config(asset_id, phase)
    data = json.loads((_SCHEMAS_DIR / cfg.schema_file).read_text(encoding="utf-8"))

    prepass_key = PREPASS.get(asset_id)
    deps: list[Dependency] = []
    prepass_deps: list[Dependency] = []

    for field in data["fields"]:
        if field["field_id"] in cfg.drop_fields:
            continue
        if field.get("source") != "auto_from_context":
            continue
        key = _resolve_wildcard(field.get("context_key") or UNRESOLVED)
        dependency = Dependency(
            field_id=field["field_id"],
            label=field.get("label", field["field_id"]),
            context_key=key,
            sub_key=field.get("sub_key"),
            required=bool(field.get("required")),
            # Unset means ask, which is what `planField` does with it on the client.
            fallback=field.get("fallback") or "ask_user_if_missing",
            producer=producer_of(key),
        )
        # A prepass key is not something an operator can be sent to go and produce.
        (prepass_deps if key == prepass_key else deps).append(dependency)

    return AssetDependencies(
        asset_id=asset_id,
        phase=phase,
        dependencies=tuple(deps),
        writes=WRITES.get(asset_id, (asset_id,)),
        prepass=prepass_key,
        prepass_fields=tuple(prepass_deps),
    )


def prepass_for(asset_id: str) -> str | None:
    return PREPASS.get(asset_id)


@lru_cache(maxsize=1)
def seedable_keys() -> frozenset[str]:
    """Context keys a caller may seed by hand.

    Restricted to keys some asset actually reads, and that is the point rather than caution: a typo
    accepted as a key writes a row nothing will ever look at, and the operator's evidence that they
    supplied the dependency would be a stage that carries on asking for it. `UNRESOLVED` is excluded
    because it is a placeholder, not a key.
    """
    keys: set[str] = set()
    for phase, configs in CONFIGS_BY_PHASE.items():
        for asset_id in configs:
            for dep in dependencies_for(asset_id, phase).dependencies:
                if dep.context_key != UNRESOLVED:
                    keys.add(dep.context_key)
    return frozenset(keys)


def transitive_producers(asset_id: str, phase: str = DEFAULT_PHASE) -> tuple[str, ...]:
    """Every asset that would have to run, in dependency order, if nothing were supplied by hand.

    Depth-first over required dependencies only — optional ones are, by definition, skippable. The
    result is what "run the required assets first" has to build, and its length is the number in the
    dependency map's "cheapest path" column minus the asset itself.
    """
    order: list[str] = []
    seen: set[str] = set()

    def walk(current: str) -> None:
        if current in seen:
            return
        seen.add(current)
        try:
            deps = dependencies_for(current, phase)
        except KeyError:
            # An asset this phase does not run — a prepass, or a Phase-1-only asset asked about
            # while in Phase 2. Nothing to walk, and not an error worth failing the report over.
            return
        for dep in deps.required:
            if dep.producer and dep.producer != current:
                walk(dep.producer)
        if current != asset_id:
            order.append(current)

    walk(asset_id)
    return tuple(order)
