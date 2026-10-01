"""The industry voice block, and the classifier that picks its bucket.

The one property everything else rests on is the first test: a run with no industry, or with the
default bucket, sends every stage's prompt byte-for-byte as it was before voice packs existed. The
rest pin where the block sits (after the cached library, before INPUTS), what it promises (INPUTS
win), and how the classifier degrades — to asking, never to a wrong bucket.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import generation, industry_voice
from app.services.generation import (
    CONFIGS_BY_PHASE,
    _prompt_parts,
    build_prompt,
    build_revision_prompt,
    build_stage_request,
)
from app.services.industry_voice import (
    BUCKETS,
    DEFAULT_BUCKET,
    GENERAL_BUCKET,
    IndustryProfile,
    from_entry,
    infer_industry,
    render,
    voice_block,
)

FINANCE = IndustryProfile(bucket="regulated_finance", label="Mortgage broking", source="operator_typed")
DEFAULT = IndustryProfile(bucket=DEFAULT_BUCKET, label="Digital marketing", source="inferred_confirmed")

ALL_STAGES = [(phase, asset_id) for phase, configs in CONFIGS_BY_PHASE.items() for asset_id in configs]


# --------------------------------------------------------------------------------------
# The default is today
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("phase", "asset_id"), ALL_STAGES)
def test_default_bucket_and_no_profile_leave_every_prompt_byte_identical(phase: str, asset_id: str) -> None:
    answers = {"client_name": "Acme"}
    before = build_prompt(asset_id, answers, phase)
    assert build_prompt(asset_id, answers, phase, voice=None) == before
    assert build_prompt(asset_id, answers, phase, voice=DEFAULT) == before


def test_default_bucket_renders_no_block() -> None:
    assert voice_block(None) == ""
    assert voice_block(DEFAULT) == ""
    assert voice_block(IndustryProfile(bucket="not_a_bucket", label="x", source="operator_typed")) == ""


# --------------------------------------------------------------------------------------
# Where the block goes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("phase", "asset_id"), ALL_STAGES)
def test_block_leads_the_tail_and_never_enters_the_cached_library(phase: str, asset_id: str) -> None:
    library, tail = _prompt_parts(asset_id, {}, phase, None, FINANCE)
    assert tail.startswith("===== BEGIN INDUSTRY_VOICE =====")
    assert "INDUSTRY_VOICE" not in library
    assert tail.index("===== END INDUSTRY_VOICE =====") < tail.index("— INPUTS")


def test_cached_system_block_is_unchanged_by_a_voice() -> None:
    """The library is a cache prefix. A per-client block inside it would make it uncacheable."""
    plain, _ = build_stage_request("offers", {}, "phase1")
    voiced, user = build_stage_request("offers", {}, "phase1", voice=FINANCE)
    assert plain == voiced
    assert isinstance(user, str) and user.startswith("===== BEGIN INDUSTRY_VOICE")


def test_block_precedes_the_brand_tokens_on_an_html_stage() -> None:
    design = generation.PageDesignInput(design_md="# DESIGN\n", theme_brief="# THEME\n")
    _, tail = _prompt_parts("lead_magnet", {}, "phase1", design, FINANCE)
    assert tail.index("INDUSTRY_VOICE") < tail.index("BRAND_DESIGN_TOKENS") < tail.index("— INPUTS")


def test_block_names_the_industry_and_says_inputs_win() -> None:
    block = voice_block(FINANCE)
    assert "CLIENT INDUSTRY: Mortgage broking (bucket: Regulated Finance)" in block
    assert "INPUTS win" in block
    assert industry_voice.load_pack("regulated_finance") in block


def test_block_tells_the_model_never_to_echo_it() -> None:
    # The Value Ladder's Step 0 asks for the context "stated" first, and the model printed this
    # block's markers into a client's Overview. The block has to say it is not an input to report.
    block = voice_block(FINANCE)
    assert "not an input to report" in block
    assert "Never quote, name, summarise or acknowledge it" in block


def test_revision_prompt_carries_the_block_and_is_unchanged_without_one() -> None:
    plain = build_revision_prompt("draft", "shorter")
    assert build_revision_prompt("draft", "shorter", DEFAULT) == plain
    voiced = build_revision_prompt("draft", "shorter", FINANCE)
    assert voiced.startswith("===== BEGIN INDUSTRY_VOICE") and voiced.endswith(plain)


# --------------------------------------------------------------------------------------
# The packs
# --------------------------------------------------------------------------------------


def test_every_bucket_but_the_default_has_a_non_empty_pack() -> None:
    for bucket in BUCKETS:
        if bucket == DEFAULT_BUCKET:
            assert not (industry_voice._PACKS_DIR / f"{bucket}.md").exists(), "the default must stay pack-less"
            continue
        assert industry_voice.load_pack(bucket), bucket


def test_no_pack_file_without_a_bucket() -> None:
    files = {p.stem for p in industry_voice._PACKS_DIR.glob("*.md")}
    assert files == set(BUCKETS) - {DEFAULT_BUCKET}


# --------------------------------------------------------------------------------------
# The stored shape
# --------------------------------------------------------------------------------------


def test_render_round_trips_through_from_entry() -> None:
    profile = IndustryProfile("healthcare", "Dental clinic", "inferred_confirmed", 0.82, "Books dental appointments.")
    value = render(profile)
    assert "- bucket = **healthcare**" in value["content"]
    assert from_entry(value) == profile


@pytest.mark.parametrize("value", [None, "text", {"content": "x"}, {"fields": {"bucket": "nope"}}])
def test_from_entry_rejects_what_this_module_did_not_write(value: Any) -> None:
    assert from_entry(value) is None


# --------------------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def create(self, **kwargs: Any) -> Any:
        self.prompts.append(kwargs["messages"][0]["content"])
        assert "output_config" not in kwargs, "Haiku 4.5 rejects effort"
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)])


@pytest.fixture
def fake_claude(monkeypatch: pytest.MonkeyPatch):
    def install(reply: str | Exception) -> _FakeMessages:
        messages = _FakeMessages(reply)
        monkeypatch.setattr(industry_voice, "get_client", lambda: SimpleNamespace(messages=messages))
        return messages

    return install


SIGNALS = {"company_name": "Arg Finance", "website_url": "argfinance.com.au", "industry": "Construction"}


@pytest.mark.asyncio
async def test_a_confident_guess_is_returned(fake_claude) -> None:
    fake = fake_claude('{"bucket": "regulated_finance", "label": "Business lending", "confidence": 0.91, "rationale": "Loans."}')
    profile = await infer_industry(SIGNALS, fetch_page=False)
    assert profile == IndustryProfile("regulated_finance", "Business lending", "inferred_confirmed", 0.91, "Loans.")
    prompt = fake.prompts[0]
    assert "Company name: Arg Finance" in prompt
    assert "a hint only, NOT the client's own industry" in prompt


@pytest.mark.asyncio
async def test_a_fenced_reply_still_parses(fake_claude) -> None:
    fake_claude('```json\n{"bucket": "healthcare", "label": "Dental", "confidence": 0.8, "rationale": "r"}\n```')
    profile = await infer_industry(SIGNALS, fetch_page=False)
    assert profile is not None and profile.bucket == "healthcare"


@pytest.mark.parametrize(
    "reply",
    [
        '{"bucket": "crypto", "label": "x", "confidence": 0.99}',
        '{"bucket": "healthcare", "label": "x", "confidence": 0.3}',
        "I think it is a bank.",
        RuntimeError("overloaded"),
    ],
)
@pytest.mark.asyncio
async def test_no_usable_guess_means_ask(fake_claude, reply: Any) -> None:
    fake_claude(reply)
    assert await infer_industry(SIGNALS, fetch_page=False) is None


@pytest.mark.asyncio
async def test_a_typed_answer_keeps_the_operators_words(fake_claude) -> None:
    fake = fake_claude('{"bucket": "regulated_finance", "label": "Lending", "confidence": 0.7, "rationale": "r"}')
    profile = await infer_industry({}, typed="Mortgage broking")
    assert profile is not None
    assert (profile.bucket, profile.label, profile.source) == ("regulated_finance", "Mortgage broking", "operator_typed")
    assert '"Mortgage broking"' in fake.prompts[0]


@pytest.mark.parametrize("reply", ['{"bucket": "nope"}', RuntimeError("down")])
@pytest.mark.asyncio
async def test_a_typed_answer_never_comes_back_empty(fake_claude, reply: Any) -> None:
    fake_claude(reply)
    profile = await infer_industry({}, typed="Veterinary clinics")
    assert profile == IndustryProfile(GENERAL_BUCKET, "Veterinary clinics", "operator_typed")


@pytest.mark.asyncio
async def test_home_page_title_is_a_signal_and_its_failure_is_not(fake_claude, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import scraper

    async def page(_url: str) -> Any:
        return SimpleNamespace(title="Arg Finance | Business Loans", meta_description="Fast approvals for SMEs")

    monkeypatch.setattr(scraper, "read_direct", page)
    fake = fake_claude('{"bucket": "regulated_finance", "label": "Lending", "confidence": 0.9, "rationale": "r"}')
    await infer_industry(SIGNALS)
    assert "Home page title: Arg Finance | Business Loans" in fake.prompts[0]

    async def refuse(_url: str) -> Any:
        raise scraper.ScrapeError("blocked")

    monkeypatch.setattr(scraper, "read_direct", refuse)
    assert (await infer_industry(SIGNALS)) is not None
