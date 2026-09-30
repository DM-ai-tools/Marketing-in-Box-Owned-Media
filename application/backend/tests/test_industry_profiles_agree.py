"""The three files the industry taxonomy lives in have to agree.

`BUCKETS` in `industry_voice.py` (the classifier's menu and the route's validation), one pack per
bucket in `assets/voice_packs/` (checked in `test_industry_voice.py`), and `INDUSTRY_PROFILES` in the
frontend's `data/industryProfiles.ts` (the labels the confirm card offers and the stages each bucket
advises against). Nothing but this test connects the Python to the TypeScript, and drift has no
symptom: a bucket the UI offers that the server does not know is a 422 when the operator confirms
it, and a label that differs means a confirmed pill maps to no bucket at all.

The second half is the rule that makes advising safe: a stage a bucket advises against must never be
one that a stage it still recommends depends on. Skipping Webinar while recommending Book would leave
Book asking for a webinar nobody built. The dependency graph is `dependencies.py`, read from the same
schemas the stages are built from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.dependencies import transitive_producers
from app.services.generation import CONFIGS_BY_PHASE
from app.services.industry_voice import BUCKETS, DEFAULT_BUCKET

_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "data" / "industryProfiles.ts"


def _profiles() -> dict[str, dict[str, object]]:
    """Parse `INDUSTRY_PROFILES` into {bucket: {label, notRecommended: [asset ids]}}.

    A shape-specific reader, not a TypeScript parser: it relies on the table's own layout (a bucket
    key at two spaces, `label:` and `notRecommended:` inside it), which is what the file is written
    in. If the layout changes this fails loudly rather than reading nothing.
    """
    text = _TS.read_text(encoding="utf-8")
    body = text.split("export const INDUSTRY_PROFILES", 1)[1].split("\n};", 1)[0]
    blocks = re.split(r"\n  (?=[a-z0-9_]+: \{)", body)[1:]
    parsed: dict[str, dict[str, object]] = {}
    for block in blocks:
        key = re.match(r"([a-z0-9_]+): \{", block).group(1)  # type: ignore[union-attr]
        label = re.search(r'\n    label: "([^"]+)"', block).group(1)  # type: ignore[union-attr]
        not_rec = re.search(r"notRecommended: \{(.*?)\n?    \}", block, re.DOTALL)
        ids = re.findall(r"\n      ([a-z0-9_]+): ", not_rec.group(1)) if not_rec else []
        parsed[key] = {"label": label, "notRecommended": ids}
    assert parsed, "INDUSTRY_PROFILES could not be read — has its layout changed?"
    return parsed


def test_the_ui_offers_exactly_the_servers_buckets() -> None:
    assert list(_profiles()) == list(BUCKETS)


def test_every_label_matches_the_servers() -> None:
    """The confirm card answers with a label and the store maps it back to a bucket by that label."""
    assert {b: p["label"] for b, p in _profiles().items()} == BUCKETS


def test_the_bucket_type_union_lists_the_same_buckets() -> None:
    text = _TS.read_text(encoding="utf-8")
    union = text.split("export type IndustryBucket =", 1)[1].split(";", 1)[0]
    assert re.findall(r'"([a-z0-9_]+)"', union) == list(BUCKETS)


def test_the_default_bucket_advises_against_nothing() -> None:
    """The default reproduces today's walk exactly."""
    assert _profiles()[DEFAULT_BUCKET]["notRecommended"] == []


@pytest.mark.parametrize("phase", list(CONFIGS_BY_PHASE))
def test_no_advised_against_stage_is_a_prerequisite_of_a_recommended_one(phase: str) -> None:
    stages = set(CONFIGS_BY_PHASE[phase])
    for bucket, profile in _profiles().items():
        advised_against = set(profile["notRecommended"])  # type: ignore[arg-type]
        for asset_id in stages - advised_against:
            stranded = set(transitive_producers(asset_id, phase)) & advised_against
            assert not stranded, (
                f"{bucket}: {asset_id} is still recommended but depends on {sorted(stranded)}, "
                "which this bucket advises skipping"
            )


def test_every_advised_against_stage_is_a_real_stage() -> None:
    known = {asset_id for configs in CONFIGS_BY_PHASE.values() for asset_id in configs}
    for bucket, profile in _profiles().items():
        unknown = set(profile["notRecommended"]) - known  # type: ignore[arg-type]
        assert not unknown, f"{bucket} names unknown stages {sorted(unknown)}"
