"""Phase 2's CRO stage writes about the *sub-service*, and never about the parent service.

The case this covers
--------------------
Phase 2 exists to build for one sub-service — Meta Ads — of the headline service Phase 1 covered —
Social Media Marketing. Its CRO stage is stage 01 and writes `cro_rewritten_copy`, and the Phase 2
Pillar Page prompt is a design replicator with no service input of any kind: it lays out whatever
copy it is handed. So the copy this stage writes *is* the subject of every page the phase produces.
If it comes back about Social Media Marketing, the whole run is about Social Media Marketing, and
nothing downstream can correct it — there is no later stage that knows what the page was meant to be.

The inputs alone do not prevent that. Some of the strategy inputs this stage receives are written at
the parent's scope by construction: Proof Assets Available and the outcome words in the terminology
map come from the parent run's `cro_client_settings`. (The ICP used to be one of them. Phase 2 now
builds its own at stage 01, and the lock says so; see `tests/test_phase2_icp.py`.) Handed
parent-scope material and a one-line "Target Service or Sub-Service", a model writes the page the
bulk of its input describes. That is the same unfollowable-instruction
failure the design pipeline documents for colours: the fix is an explicit rule, not a better hope.

So the Phase 2 prompt file carries a **SCOPE LOCK** section that Phase 1's does not, and this file
pins it. It is the only thing standing between the two files: they were byte-identical before the
lock landed, and a well-meaning "sync the phase prompts" edit would restore that silently — the
stage would still build, still render its INPUTS, still produce a polished page, and the page would
be about the wrong service.

No network and no model call: the prompts are read off disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.generation import _PROMPTS_DIR, build_prompt
from app.services.generation import _config as stage_config

_BACKEND = Path(__file__).resolve().parents[1]
_FRONTEND_SRC = _BACKEND.parents[0] / "frontend" / "src"

PHASE1_CRO_PROMPT = _PROMPTS_DIR / stage_config("cro", "phase1").prompt_file
PHASE2_CRO_PROMPT = _PROMPTS_DIR / stage_config("cro", "phase2").prompt_file

PIPELINE_DATA = _FRONTEND_SRC / "pipeline" / "pipelineData.ts"
PHASE2_CATALOG = _FRONTEND_SRC / "data" / "phase2Catalog.ts"

#: The heading the whole rule hangs off. `build_prompt` splices the file below the INPUTS block, so
#: a section present in the file and absent from the built prompt is a real failure, not a typo.
SCOPE_LOCK_HEADING = "## SCOPE LOCK — THIS PAGE IS ABOUT THE SUB-SERVICE, NOT THE PARENT SERVICE"


def _phase2_text() -> str:
    return PHASE2_CRO_PROMPT.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Whitespace collapsed to single spaces.

    The prompt files are hard-wrapped at about 100 characters, so any phrase long enough to be worth
    asserting on is liable to have a newline in the middle of it — and re-wrapping a paragraph is an
    edit that must not fail a test about what the paragraph says.
    """
    return re.sub(r"\s+", " ", text)


def _section(text: str, heading: str) -> str:
    """One `##` section, heading to the next `##`, flattened — so a match cannot come from
    elsewhere in the file, and cannot be lost to a line wrap."""
    start = text.index(heading)
    rest = text[start + len(heading) :]
    end = rest.find("\n## ")
    return _flat(rest if end == -1 else rest[:end])


# --------------------------------------------------------------------------------------
# The two files are no longer the same file
# --------------------------------------------------------------------------------------


def test_the_phase2_cro_prompt_is_not_a_copy_of_phase1s() -> None:
    """The load-bearing one, and the one with no symptom when it fails.

    These files were byte-identical until the scope lock landed, and the Phase 1 file is the right
    file for Phase 1 — it writes a page for whatever service it is pointed at, which at the headline
    level is correct. Re-copying it over the Phase 2 file breaks nothing that any other test, the
    UI, or a completed run would notice.
    """
    assert PHASE1_CRO_PROMPT.read_text(encoding="utf-8") != _phase2_text()


def test_only_the_phase2_file_carries_the_scope_lock() -> None:
    assert SCOPE_LOCK_HEADING in _phase2_text()
    assert "SCOPE LOCK" not in PHASE1_CRO_PROMPT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# What the lock actually says
# --------------------------------------------------------------------------------------


def test_the_lock_names_the_input_that_decides_the_subject() -> None:
    """Pointing at "the sub-service" in the abstract is not enough — the model has to be told which
    input to read, by the label that input is rendered under."""
    lock = _section(_phase2_text(), SCOPE_LOCK_HEADING)
    assert "Target Service or Sub-Service" in lock

    # And that label has to be the one the INPUTS block is actually built with, or the lock is
    # pointing at a line that is not there.
    block, _, _ = build_prompt("cro", {}, "phase2").partition("— END OF INPUTS —")
    assert "Target Service or Sub-Service" in block


def test_the_lock_forbids_the_parent_on_every_surface_that_decides_the_page() -> None:
    """A page can be "about" the sub-service in its body and still be the parent's page, because
    what a page is about is settled by its H1, title, keyword target and offer name. Those are the
    surfaces to name, and they are what Part 3 asks for."""
    lock = _section(_phase2_text(), SCOPE_LOCK_HEADING)
    for surface in ("H1", "title tag", "meta description", "URL", "primary keyword", "offer name", "CTA"):
        assert surface in lock, f"the scope lock never mentions the {surface}"


def test_the_lock_keeps_the_parent_reachable_as_a_link_and_a_negative_target() -> None:
    """Not a ban on the parent existing. A sub-service page that never links up to its pillar, or
    that does not know which term it must avoid, fails Rule 8 — so the lock has to carve both out
    rather than say "do not mention it"."""
    lock = _section(_phase2_text(), SCOPE_LOCK_HEADING)
    assert "parent pillar" in lock
    assert "compete" in lock


def test_the_lock_says_what_to_do_with_a_parent_scope_input() -> None:
    """The inherited inputs are not noise to be discarded wholesale — the client has not changed,
    so the buyer-level content in them is correct and expensive to re-derive. The lock has to split
    them rather than reject them, or the stage loses the settings work Phase 1 did.

    The ICP is no longer one of them: Phase 2 builds its own at stage 01, and the lock has to say
    so, or it tells the model to narrow a document that is already at the right scope."""
    lock = _section(_phase2_text(), SCOPE_LOCK_HEADING)
    assert "Proof Assets Available" in lock
    assert "The ICP Document is the exception: it was built in this run for this sub-service" in _flat(lock)
    for verb in ("Keep", "Re-point", "Discard"):
        assert f"**{verb}**" in lock, f"the scope lock does not say when to {verb.lower()} an inherited input"


def test_the_lock_closes_the_gap_without_falling_back_to_the_parent() -> None:
    """The failure this prevents is the quiet one: an input silent on the sub-service, and a model
    that fills the hole with the only service it has material for."""
    lock = _section(_phase2_text(), SCOPE_LOCK_HEADING)
    assert "competitor analysis" in lock  # researched at sub-service scope; the right thing to reach for
    assert "Never close the gap by falling back to the parent service." in lock


# --------------------------------------------------------------------------------------
# Where the lock is enforced downstream
# --------------------------------------------------------------------------------------


def test_step_0_makes_the_model_name_the_sub_service_before_it_writes() -> None:
    """Step 0 is stated before anything else and everything downstream must obey it, so this is
    where the subject gets fixed. A lock the model only reads is a lock it can drift away from."""
    step0 = _section(_phase2_text(), "## STEP 0 — MODE RESOLUTION (do this first, output it before anything else)")
    assert "quoted verbatim from the Target Service or Sub-Service input" in step0
    assert "Scope Lock" in step0, "Step 0 never asks what the lock made the model narrow or discard"


def test_the_icp_step_re_reads_the_document_at_sub_service_scope() -> None:
    """Step 1B is where the ICP is turned into copy decisions. Phase 2's ICP is built in-run for the
    sub-service, so it is used as written — but the card lets the operator replace it, and a pasted
    parent-scope ICP must still be re-pointed rather than written up as the sub-service's buyer."""
    text = _phase2_text()
    icp = _flat(text[text.index("### 1B) ICP Document") : text.index("### 1C) Competitor Analysis")])
    assert "built in this run for this sub-service" in icp
    assert "describes the buyer of the parent service" in icp
    assert "Scope Lock" in icp


def test_rule_8_covers_the_mode_phase2_always_runs_in() -> None:
    """Phase 2's CRO stage is always NEW PAGE — that is what `PHASE2_CONSTANT_ANSWERS` seeds. Rule 8
    opens "keep the existing H1 intent and primary keyword target", which in that mode names three
    things that do not exist, leaving the most familiar term in the inputs as the obvious answer:
    the parent's."""
    text = _phase2_text()
    rule8 = _flat(text[text.index("**Rule 8 —") : text.index("### PAGE ARCHITECTURE")])
    assert "NEW PAGE mode" in rule8
    assert "Target Service or Sub-Service" in rule8


def test_part_0_reports_the_lock_back_to_the_operator() -> None:
    """The operator's only check that the right page was written. Without it the scope decisions are
    internal to the model and the first visible sign of a wrong one is the finished page."""
    output = _section(_phase2_text(), "## OUTPUT FORMAT")
    part0 = output[output.index("**PART 0") : output.index("**PART 1")]
    assert "Scope Lock" in part0


def test_the_build_from_scratch_clause_survived() -> None:
    """Phase 2 depends on it absolutely: both sentinels are seeded, there is no page to audit, and
    without the clause the stage is asked to rewrite a page that does not exist. Pinned for the
    Phase 1 file in `test_new_page_mode.py`; this is the same clause in the Phase 2 copy."""
    assert "NEW PAGE" in _phase2_text()


# --------------------------------------------------------------------------------------
# The prompt the stage is actually handed
# --------------------------------------------------------------------------------------


def test_the_lock_reaches_the_built_prompt_and_only_in_phase2() -> None:
    """`build_prompt` reproduces the INPUTS block and splices the body below it. A section added to
    the file but lost in the splice is worth nothing."""
    assert SCOPE_LOCK_HEADING in build_prompt("cro", {}, "phase2")
    assert "SCOPE LOCK" not in build_prompt("cro", {}, "phase1")


def test_the_lock_leads_the_body_ahead_of_step_0() -> None:
    """Order matters here: Step 0 is answered first and refers back to the lock, so the lock has to
    have been read by then."""
    body = build_prompt("cro", {}, "phase2").split("— END OF INPUTS —")[1]
    assert body.index(SCOPE_LOCK_HEADING) < body.index("## STEP 0")


# --------------------------------------------------------------------------------------
# What fills the field the lock points at
# --------------------------------------------------------------------------------------


def test_the_sub_service_fact_answers_the_target_service_field() -> None:
    """The lock says that input decides the subject. It is answered from the run-level `sub_service`
    fact rather than asked, so an operator cannot leave it at the parent's name by accident — and if
    this delta entry were dropped, the field would go back to being a free-text question with the
    parent's engagement all around it."""
    catalog = PHASE2_CATALOG.read_text(encoding="utf-8")
    cro_delta = catalog[catalog.index("  cro: {") : catalog.index("  pillar_page: {")]
    assert re.search(r'fromSubService:\s*\[\s*"target_service_or_sub_service"\s*\]', cro_delta)


def test_the_ui_seeds_the_page_scope_as_sub_service() -> None:
    """`page_scope` is what Step 0 item 5 and Rule 8 branch on. Seeded as SUB-SERVICE it puts the
    stage on the cannibalisation-aware path every time; left to the operator it is one dropdown
    between a sub-service page and a second pillar page for the parent."""
    data = PIPELINE_DATA.read_text(encoding="utf-8")
    constants = data[data.index("export const PHASE2_CONSTANT_ANSWERS") :]
    cro_block = constants[constants.index("cro: {") : constants.index("};")]
    assert re.search(r'page_scope:\s*"SUB-SERVICE"', cro_block)
