"""Check a draft against the client's actual business before it is approved.

Four checks, each scored where it applies:

  * `offer_relevance`  — does it sell what the client actually sells, at the prices on record?
  * `funnel_relevance` — does it route the buyer through the client's real offers and funnel?
  * `business_logic`   — are its claims, prices, testimonials and vocabulary allowed for this client?
  * `virality`         — how likely is it to travel? A **prediction**, never a measurement.

Measure first, then judge. The same fix `design_tokens.py` documents for colours applies here:
deterministic rules run against the facts on record (`business_facts.py`) before any model is
asked. The model then judges what rules cannot, such as whether an offer fits the ICP. Every finding
it returns must quote the draft verbatim. `_verified` drops any quote that is not actually in the
text, and the report says how many were dropped. A checker that invents problems is as useless as
a stage that invents facts.

Advisory only. Nothing here blocks approval, and a model failure degrades to the rule findings
alone.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field

from app.services.business_facts import BusinessFacts, amount_of, money_matches
from app.services.claude_client import get_client
from app.services.generation import EFFORT_CAPABLE_MODELS, SONNET, OnUsage
from app.services.html_dom import parse_html
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

CHECKS = ("offer_relevance", "funnel_relevance", "business_logic", "virality")

#: Which checks apply to which asset. Every stage in both phases is listed, so a stage added later
#: fails `test_every_stage_has_checks` instead of silently being unchecked.
CHECKS_BY_ASSET: dict[str, tuple[str, ...]] = {
    "icp": ("business_logic",),
    "cro": ("offer_relevance", "business_logic"),
    "pillar_page": ("offer_relevance", "business_logic"),
    "funnel": ("offer_relevance", "funnel_relevance", "business_logic"),
    "funnel_hub_media": ("funnel_relevance", "business_logic"),
    "offers": ("offer_relevance", "business_logic"),
    "lead_magnet": ("offer_relevance", "funnel_relevance", "business_logic", "virality"),
    "blog": ("business_logic", "virality"),
    "content_marketing_strategy": ("business_logic",),
    # No virality: the deliverable is an audit of posts (workbooks and a narrative), not posts. The
    # judge said so on every run — "an internal data/methodology deliverable ... cannot be
    # predicted" — and scored it ~20, a number no Refine could raise without wrecking the audit.
    "social_content_strategy_audit": ("business_logic",),
    "webinar": ("offer_relevance", "business_logic", "virality"),
    "book": ("business_logic",),
    "podcast": ("business_logic", "virality"),
    "sms_sequence": ("offer_relevance", "funnel_relevance", "business_logic", "virality"),
    "plan_of_action": ("business_logic",),
}

#: Stages that get no business check in a phase, as `(phase, asset_id)`. Phase 2's ICP is built for
#: one sub-service's buyer and there is no business-side document yet to hold it against. The UI
#: never asks (`checkAppliesTo` in `lib/businessCheck.ts` — keep the two in step); the route refuses
#: so a stale client cannot spend a judge call on it.
NO_CHECK: frozenset[tuple[str, str]] = frozenset({("phase2", "icp")})


def check_applies(asset_id: str, phase: str) -> bool:
    return (phase, asset_id) not in NO_CHECK


#: Copy a prospect reads. Strategy documents quote KPI targets ("aim for a 3% CTR") by design, and
#: flagging every percentage in them as an unsourced statistic would bury the findings that matter.
CUSTOMER_FACING = frozenset(
    {"cro", "pillar_page", "funnel", "lead_magnet", "blog", "webinar", "podcast", "book", "sms_sequence"}
)

JUDGE_MODEL = SONNET
_JUDGE_EFFORT = "low"
_JUDGE_MAX_TOKENS = 6000
#: Characters of draft text the judge reads. A CRO document runs to ~80KB, most of it built HTML
#: whose text is extracted first; this keeps a check in cents rather than a stage's own cost.
_MAX_DRAFT_CHARS = 60_000
_MAX_PER_RULE = 6


@dataclass
class Finding:
    check: str
    severity: str  # "error" | "warn" | "info"
    quote: str
    why: str
    fix: str = ""
    source: str = "rule"  # "rule" | "judge"


@dataclass
class CheckResult:
    check: str
    score: int | None = None
    verdict: str = ""
    findings: list[Finding] = field(default_factory=list)


@dataclass
class CheckReport:
    asset_id: str
    checks: list[CheckResult]
    facts_used: list[str]
    facts_missing: list[str]
    judge: str  # "ok" | "unavailable" | "skipped"
    dropped_quotes: int = 0
    virality_basis: str = ""  # "benchmarked" | "rubric only" | ""
    duration_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------------------


_NON_TEXT = re.compile(r"<(script|style|svg|noscript)\b.*?</\1>", re.DOTALL | re.IGNORECASE)


def _html_text(html: str) -> str:
    html = _NON_TEXT.sub(" ", html)
    try:
        return parse_html(html).body.text_content()
    except Exception:  # noqa: BLE001 — a malformed page is still words
        return re.sub(r"<[^>]+>", " ", html)


def draft_text(raw: str) -> str:
    """What a reader sees. Fenced HTML — and a draft that is a whole HTML page — become their
    visible text; CSS, scripts and SVG paths would otherwise be read as copy."""
    text = re.sub(r"```html\s*(.*?)```", lambda m: _html_text(m.group(1)), raw or "", flags=re.DOTALL | re.IGNORECASE)
    if re.search(r"<(html|body)\b", text, re.IGNORECASE):
        text = _html_text(text)
    return text


def _snippet(text: str, start: int, end: int) -> str:
    """The line around a match, verbatim, trimmed to a readable length."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : line_end if line_end != -1 else len(text)].strip()
    if len(line) <= 200:
        return line
    offset = max(0, start - line_start - 80)
    return line[offset : offset + 200].strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip().casefold()


def _verified(quote: str, text_norm: str) -> bool:
    q = _norm(quote).strip(" .…\"'")
    return len(q) >= 4 and q in text_norm


# --------------------------------------------------------------------------------------
# Rules — free, deterministic, run first
# --------------------------------------------------------------------------------------

# A rating, or a quote attributed to a *person*: "…" — Jane Smith / "…" — Jane, Director. The word
# "testimonial" alone is not one — a brief saying "no testimonials are used" is compliant, not a breach.
# "4.9/5" or "5/5 stars" — not "SMS 4/5", which is messages four and five.
_RATING = re.compile(
    r"(★{3,}|\b[45](?:\.\d)?[- ]stars?\b|\b[45]\.\d\s?/\s?5\b|\b[45](?:\.\d)?\s?/\s?5\s*(?:stars?|rating|reviews?)\b)",
    re.IGNORECASE,
)
_ATTRIBUTED_QUOTE = re.compile(r"[“\"][^”\"\n]{25,}[”\"]\s*[—–-]\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+|,\s*[A-Z])")
_ABSOLUTE_CLAIMS = re.compile(
    r"\b(guarantee[ds]?|risk[- ]free|no[- ]risk|#1|number one|best in (?:the )?(?:city|country|market|industry|australia|town)|100% (?:success|results|guaranteed)|proven to|cure[sd]?)\b",
    re.IGNORECASE,
)
# Read over the sentence so far: "results are not guaranteed", "if you're looking for a guaranteed
# outcome, this isn't for you" — the compliant sentences, not the breach.
_NEGATED = re.compile(r"\b(not|no|never|without|cannot|can't|won't|isn't|aren't|looking for)\b", re.IGNORECASE)
_TARGET = re.compile(
    r"\b(targets?|aim(?:s|ing)?|goals?|kpis?|benchmarks?|density|thresholds?|conversion rates?|ctr|open rates?|overage|remove)\b",
    re.IGNORECASE,
)
_SOFT_CLAIMS = re.compile(r"\b(guarantee[ds]?|risk[- ]free)\b", re.IGNORECASE)
_STAT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
_SOURCED = re.compile(r"\b(source|according to|survey|study|report|research|data from|\(\d{4}\))", re.IGNORECASE)
_PLACEHOLDER = re.compile(
    r"(\[(?:YOUR ANSWER|INSERT[^\]]{0,40}|PLACEHOLDER[^\]]{0,40}|TBD|TODO)\]|\(not specified\)|lorem ipsum)",
    re.IGNORECASE,
)


#: Sections that describe someone else — competitor scans, market benchmarks, the social audit's
#: "what the top five post". A competitor's "Guaranteed results" quoted in a benchmark table is
#: evidence, not this client making the claim, and flagging it would bury the findings that matter.
_OTHERS_HEADING = re.compile(r"competitor|benchmark|market (?:scan|landscape|research)|landscape|top \d+|vs\.? top", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _others_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges under a heading about other businesses, up to the next heading of the same
    level or shallower."""
    headings = [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING.finditer(text)]
    spans: list[tuple[int, int]] = []
    for i, (start, level, title) in enumerate(headings):
        if not _OTHERS_HEADING.search(title):
            continue
        end = next((s for s, lvl, _ in headings[i + 1 :] if lvl <= level), len(text))
        spans.append((start, end))
    return spans


def run_rules(asset_id: str, text: str, facts: BusinessFacts) -> tuple[list[Finding], list[str]]:
    """The deterministic findings, and which facts they were checked against.

    One finding per rule per line, at most `_MAX_PER_RULE` per rule. Each rule's `fix` text is
    unique, so it doubles as the rule's key.
    """
    findings: list[Finding] = []
    used: list[str] = []
    others = _others_spans(text)

    def add(check: str, severity: str, m: re.Match[str], why: str, fix: str = "") -> None:
        if any(start <= m.start() < end for start, end in others):
            return
        quote = _snippet(text, m.start(), m.end())
        same_rule = [f for f in findings if f.fix == fix]
        if len(same_rule) >= _MAX_PER_RULE or any(f.quote == quote for f in same_rule):
            return
        findings.append(Finding(check, severity, quote, why, fix))

    # Prices.
    if facts.prices_forbidden:
        used.append("pricing disclosure mode")
        for m in money_matches(text):
            add(
                "business_logic",
                "error",
                m,
                f"States a price ({m.group(0).strip()}), but this client's pricing disclosure mode is "
                f"\"{facts.settings.get('pricing_disclosure_mode')}\".",
                "Remove the figure, or describe what drives the cost instead.",
            )
    elif asset_id != "offers" and facts.allowed_amounts:
        used.append("prices on record (offer ladder / pricing facts)")
        allowed = facts.allowed_amounts
        for m in money_matches(text):
            value = amount_of(m)
            if value and value not in allowed:
                add(
                    "offer_relevance",
                    "warn",
                    m,
                    f"{m.group(0).strip()} is not a price or value on record for any of this client's offers.",
                    "Use a price from the offer ladder or the pricing facts, or remove it.",
                )

    # Offers named at all, for the stages whose job is to route to them.
    if asset_id in {"funnel", "lead_magnet", "sms_sequence", "webinar"} and facts.offers:
        used.append("offer ladder")
        low = text.casefold()
        if not any(o.title.casefold() in low for o in facts.offers):
            findings.append(
                Finding(
                    "funnel_relevance" if asset_id != "webinar" else "offer_relevance",
                    "warn",
                    "",
                    "Names none of the offers on the client's ladder, so it routes the buyer to nothing "
                    "the client actually sells.",
                    "Name the offer this asset ascends to: "
                    + ", ".join(f'"{o.title}"' for o in facts.offers[:5])
                    + ("…" if len(facts.offers) > 5 else ""),
                )
            )

    # Words to avoid.
    words = facts.words_to_avoid
    if words:
        used.append("words to avoid")
        for word in words:
            for m in re.finditer(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text, re.IGNORECASE):
                add("business_logic", "warn", m, f'Uses "{word}", which is on this client\'s words-to-avoid list.',
                    "Rephrase without it.")

    # Testimonials.
    # Customer-facing copy only: a strategy document scoring funnels ★★★★☆ is not a testimonial.
    if facts.testimonials in {"NO", "UNSURE"} and asset_id in CUSTOMER_FACING:
        used.append("testimonial permission")
        severity = "error" if facts.testimonials == "NO" else "warn"
        why = (
            "Uses a testimonial, rating or attributed quote, and this client is not permitted to."
            if facts.testimonials == "NO"
            else "Uses a testimonial, rating or attributed quote, and permission to use them is unconfirmed."
        )
        for m in _RATING.finditer(text):
            add("business_logic", severity, m, why, "Replace with process, credentials or a permitted proof asset.")
        # An attributed quote is a testimonial far more often than not, but a strategist's note can
        # quote a persona too — so it is never more than a warning.
        for m in _ATTRIBUTED_QUOTE.finditer(text):
            add("business_logic", "warn", m, why, "Remove the attributed quote, or confirm it is a permitted testimonial.")

    # Claims.
    tier = facts.claim_tier
    if tier is not None:
        used.append("claim substantiation tier")
        pattern, severity = (_ABSOLUTE_CLAIMS, "error") if tier >= 2 else (_SOFT_CLAIMS, "warn")
        for m in pattern.finditer(text):
            # "Results are not guaranteed" is the compliant sentence, not the breach.
            sentence_start = max(text.rfind(ch, 0, m.start()) for ch in ".!?\n") + 1
            if _NEGATED.search(text[max(sentence_start, m.start() - 80) : m.start()]):
                continue
            add(
                "business_logic",
                severity,
                m,
                f'"{m.group(0)}" is an absolute claim; this client\'s claim tier is '
                f"\"{facts.settings.get('claim_substantiation_tier')}\".",
                "Qualify it, or back it with a substantiated proof asset.",
            )
    if asset_id in CUSTOMER_FACING:
        for m in _STAT.finditer(text):
            line = _snippet(text, m.start(), m.end())
            # Tables and targets are the brief talking to the operator ("keyword density 1-2%",
            # "aim for a 3% CTR"), not a claim made to a prospect.
            if line.startswith("|") or _TARGET.search(line):
                continue
            if not _SOURCED.search(line):
                # Info, not a warning: these documents mix copy with the strategist's own notes,
                # and a rule cannot tell "400% more leads" in a hero from a note saying to remove it.
                add("business_logic", "info", m, "A statistic with no stated source.",
                    "Cite where it comes from, or remove it.")

    for m in _PLACEHOLDER.finditer(text):
        add("business_logic", "info", m, "A placeholder left for the client to complete.", "Fill it in before publishing.")

    return findings, sorted(set(used))


# --------------------------------------------------------------------------------------
# Virality benchmarks — real data, when the run has it
# --------------------------------------------------------------------------------------


def virality_benchmarks(text: str, facts: BusinessFacts) -> tuple[str, bool]:
    """The real numbers the virality score is anchored on, as prose for the judge, and whether any
    exist. Nothing here is estimated: keyword volume and post engagement were both measured."""
    lines: list[str] = []
    low = text.casefold()
    matched = [
        k for k in facts.keywords
        if isinstance(k.get("keyword"), str) and len(k["keyword"]) > 3 and k["keyword"].casefold() in low
    ]
    matched.sort(key=lambda k: k.get("volume") or 0, reverse=True)
    if matched:
        lines.append("Search demand for terms this draft uses (measured, DataForSEO):")
        for k in matched[:8]:
            lines.append(f"  - \"{k['keyword']}\": volume {k.get('volume')}, difficulty {k.get('difficulty')}")
    measured = [s for s in facts.social if s.get("median_engagement") is not None]
    if measured:
        lines.append("Real social engagement (measured, SociaVault; engagement = likes+comments+shares where published):")
        for s in measured[:8]:
            lines.append(
                f"  - {s.get('account')} on {s.get('platform')}: median {s.get('median_engagement')} over "
                f"{s.get('measured_posts')} posts. Top posts opened with:"
            )
            for post in (s.get("top_posts") or [])[:3]:
                if isinstance(post, dict):
                    kind = "video" if post.get("is_video") else "static"
                    lines.append(f"      · ({post.get('engagement')}, {kind}) \"{post.get('opening')}\"")
    return "\n".join(lines), bool(lines)


# --------------------------------------------------------------------------------------
# The judge
# --------------------------------------------------------------------------------------

_RUBRIC = {
    "offer_relevance": (
        "Does this draft sell or point to what this client actually sells? Every offer, product, "
        "service or price it names must be on the offer ladder or in the pricing facts. Flag invented "
        "offers, offers for a different service than the one being marketed, prices off the ladder, "
        "and an offer pitched at the wrong buyer for the ICP."
    ),
    "funnel_relevance": (
        "Does it move the ICP's buyer, at their awareness level, to the next real step: the rung the "
        "offer ladder says it ascends to, and the funnel stage it belongs to? Flag CTAs to steps that "
        "do not exist, a mismatch between awareness level and ask, and dead ends."
    ),
    "business_logic": (
        "Is every claim, figure, guarantee, testimonial, credential and promise consistent with the "
        "facts on record and permitted for this client (claim tier, pricing mode, testimonials, words "
        "to avoid)? Flag contradictions between the draft and the facts, and internal contradictions "
        "(a price stated two ways, a rung ascending to itself)."
    ),
    "virality": (
        "PREDICT how likely this is to be shared or engaged with, on the platforms it is written for. "
        "Rubric: hook strength in the first line, curiosity gap, specificity (numbers, names, "
        "outcomes), emotional charge, shareability (would someone send it to a colleague?), platform "
        "fit (length, format). Where real benchmarks are supplied, compare this draft's hooks against "
        "the hooks that actually performed and say which it resembles. Where none are supplied, say "
        "the score is rubric-only. This is a prediction and must be worded as one."
    ),
}


def fix_context(facts: BusinessFacts) -> str:
    """The facts a "Fix with Refine" revision is written against: the same summary and ICP excerpt
    the judge checked the draft against, so a fix and the re-check agree on what is true."""
    return facts.summary() + (f"\n\nICP (excerpt):\n{facts.icp[:6000]}" if facts.icp else "")


def _previous_block(previous: list[CheckResult] | None, checks: tuple[str, ...]) -> str:
    """The report on the draft this one was revised from, so a re-check judges the revision against
    what was asked of it rather than starting from zero and finding a different set of problems."""
    if not previous:
        return ""
    lines: list[str] = []
    for result in previous:
        if result.check not in checks:
            continue
        # A missing previous score is written as "not scored", never "n/a": the judge copies what it
        # is shown, and "score n/a" came back as `"score": "n/a"` on every re-check after it.
        score = f"score {result.score}" if result.score is not None else "not scored"
        lines.append(f"- {result.check}: {score}" + (f" — {result.verdict}" if result.verdict else ""))
        for f in result.findings:
            where = f' — "{f.quote}"' if f.quote else ""
            lines.append(f"    - [{f.severity}] {f.why}{where}")
    if not lines:
        return ""
    return (
        "PREVIOUS CHECK — this draft is a revision of one checked before, which was given these scores "
        "and findings:\n" + "\n".join(lines) + "\n\n"
        "RE-CHECK RULES:\n"
        "- For each previous finding, decide whether the revision fixed it. Re-report it (quoting the "
        "draft as it now reads) only if the problem is still there. Do not re-report a fixed one.\n"
        "- Add a new finding only if the revision introduced it, or it is material and was clearly "
        "missed before. Do not go looking for fresh minor issues to replace the fixed ones.\n"
        "- Score relative to the previous score: higher when previous findings were fixed and nothing "
        "worse was introduced. Score lower than before only when a previous problem remains AND the "
        "revision introduced a new one — and that new problem must be one of your findings.\n\n"
    )


def _judge_prompt(asset_id: str, checks: tuple[str, ...], facts: BusinessFacts, text: str,
                  rule_findings: list[Finding], benchmarks: str,
                  previous: list[CheckResult] | None = None) -> str:
    rubric = "\n".join(f"- {c}: {_RUBRIC[c]}" for c in checks)
    already = "\n".join(f"- [{f.check}] {f.why} — \"{f.quote}\"" for f in rule_findings if f.quote) or "(none)"
    schema = {
        "checks": {
            c: {
                "score": "<0-100 integer>",
                "verdict": "<one sentence>",
                "findings": [
                    {"severity": "error|warn|info", "quote": "<exact words copied from the draft>",
                     "why": "<one sentence citing the fact it conflicts with>", "fix": "<one sentence>"}
                ],
            }
            for c in checks
        }
    }
    return (
        f"You are checking a draft {asset_id.replace('_', ' ')} against the client's real business.\n\n"
        f"THE CLIENT'S BUSINESS — the only source of truth. Anything not here is not on record; do not "
        f"assume it, and do not flag the draft merely for mentioning something you cannot verify.\n"
        f"{facts.summary()}\n\n"
        + (f"ICP (excerpt):\n{facts.icp[:6000]}\n\n" if facts.icp else "")
        + (f"FUNNEL (excerpt):\n{facts.funnel[:4000]}\n\n" if facts.funnel and 'funnel_relevance' in checks else "")
        + (f"REAL BENCHMARKS:\n{benchmarks}\n\n" if benchmarks and "virality" in checks else "")
        + f"CHECKS TO RUN:\n{rubric}\n\n"
        f"ALREADY FOUND BY RULES (do not repeat these):\n{already}\n\n"
        + _previous_block(previous, checks)
        + "RULES FOR FINDINGS:\n"
        "- Every finding's `quote` must be words copied exactly from the draft, 4 to 200 characters. "
        "A finding you cannot quote is not a finding.\n"
        "- At most 6 findings per check, most important first. No findings is a valid answer.\n"
        "- Every check's `score` is an integer from 0 to 100, never null, \"n/a\" or words, even "
        "when a previous score was missing or the check fits the draft poorly.\n"
        "- Score 90+ only when there is nothing material to fix.\n\n"
        f"DRAFT:\n-----\n{text[:_MAX_DRAFT_CHARS]}\n-----\n\n"
        "Reply with ONLY a JSON object of this shape, no prose, no code fence:\n"
        + json.dumps(schema)
    )


_SCORE_TEXT = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*(?:/\s*100|%)?\s*$")


def _score_of(value: object) -> int | None:
    """The judge's score as 0-100, or None. A number, or a string holding one ("80", "80/100",
    "80%"). Anything else ("n/a", null, prose) is no score rather than a guessed one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and (match := _SCORE_TEXT.match(value)):
        number = float(match.group(1))
    else:
        return None
    return max(0, min(100, int(number)))


def _parse_judge(raw: str) -> dict[str, object] | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("checks"), dict) else None


async def _call_judge(prompt: str, on_usage: OnUsage | None) -> str:
    kwargs: dict[str, object] = {
        "model": JUDGE_MODEL,
        "max_tokens": _JUDGE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    if JUDGE_MODEL in EFFORT_CAPABLE_MODELS:
        kwargs["output_config"] = {"effort": _JUDGE_EFFORT}
    started = time.monotonic()
    response = await get_client().messages.create(**kwargs)
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(response, requested_model=JUDGE_MODEL,
                                    duration_ms=int((time.monotonic() - started) * 1000))
        )
    return next((b.text for b in response.content if b.type == "text"), "")


# --------------------------------------------------------------------------------------
# The whole check
# --------------------------------------------------------------------------------------


def _hold_unexplained_drops(results: dict[str, CheckResult], previous: list[CheckResult]) -> None:
    """A re-check may not score a check lower than before while reporting nothing material in it.
    A lower score with no error or warning to show for it is the judge's run-to-run noise, and it
    is what made "Fix with Refine" look as if it had made the draft worse."""
    before = {p.check: p.score for p in previous if p.score is not None}
    for name, result in results.items():
        was = before.get(name)
        if was is None or result.score is None or result.score >= was:
            continue
        if not any(f.severity in {"error", "warn"} for f in result.findings):
            result.score = was


async def check_asset(
    asset_id: str,
    raw: str,
    facts: BusinessFacts,
    *,
    on_usage: OnUsage | None = None,
    use_judge: bool = True,
    previous: list[CheckResult] | None = None,
) -> CheckReport:
    """`previous` is the report on the draft this one was revised from (a Refine), if any. The judge
    is shown it so the re-check scores the revision against what was asked of it."""
    started = time.monotonic()
    checks = CHECKS_BY_ASSET.get(asset_id, ("business_logic",))
    text = draft_text(raw)
    text_norm = _norm(text)

    rule_findings, used = run_rules(asset_id, text, facts)
    results = {c: CheckResult(c) for c in checks}
    for f in rule_findings:
        results.setdefault(f.check, CheckResult(f.check)).findings.append(f)

    benchmarks, benchmarked = virality_benchmarks(text, facts) if "virality" in checks else ("", False)
    judge_state = "skipped"
    dropped = 0

    if use_judge and text.strip():
        try:
            data = _parse_judge(await _call_judge(_judge_prompt(asset_id, checks, facts, text, rule_findings, benchmarks, previous), on_usage))
            if data is None:
                raise ValueError("the judge's reply was not the requested JSON")
            judge_state = "ok"
            for name, body in data["checks"].items():  # type: ignore[union-attr]
                if name not in results or not isinstance(body, dict):
                    continue
                result = results[name]
                result.score = _score_of(body.get("score"))
                result.verdict = " ".join(str(body.get("verdict") or "").split())
                for item in body.get("findings") or []:
                    if not isinstance(item, dict):
                        continue
                    quote = " ".join(str(item.get("quote") or "").split())
                    if not _verified(quote, text_norm):
                        dropped += 1
                        continue
                    severity = str(item.get("severity") or "warn").lower()
                    result.findings.append(
                        Finding(
                            name,
                            severity if severity in {"error", "warn", "info"} else "warn",
                            quote[:200],
                            " ".join(str(item.get("why") or "").split()),
                            " ".join(str(item.get("fix") or "").split()),
                            source="judge",
                        )
                    )
        except Exception as exc:  # noqa: BLE001 — the rules still stand on their own
            logger.warning("Asset check judge failed for %s: %s", asset_id, exc)
            judge_state = "unavailable"

    if judge_state == "ok" and previous:
        _hold_unexplained_drops(results, previous)

    if dropped:
        logger.info("Asset check for %s dropped %d finding(s) whose quote is not in the draft", asset_id, dropped)

    facts_used = sorted(set(used) | ({"offer ladder"} if facts.offers else set()) | ({"CRO client settings"} if facts.settings else set()))
    return CheckReport(
        asset_id=asset_id,
        checks=[results[c] for c in checks] + [r for c, r in results.items() if c not in checks],
        facts_used=facts_used,
        facts_missing=list(facts.missing),
        judge=judge_state,
        dropped_quotes=dropped,
        virality_basis=("benchmarked" if benchmarked else "rubric only") if "virality" in checks else "",
        duration_ms=int((time.monotonic() - started) * 1000),
    )
