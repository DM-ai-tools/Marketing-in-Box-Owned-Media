"""Competitor content intelligence — what earned interaction, measured off posts already fetched.

Stage 10's prepass (`_run_social_data_prepass`) pays SociaVault for the client's and up to five
competitors' recent posts, and `social_audit.render_social_data_md` lists them as tables. A model
handed six such tables and asked "which content type gets competitors more interaction" has to do
the arithmetic in its head over a few hundred rows, and what comes back is an impression with a
number attached. This module does the arithmetic once, here, and hands the prompt the result — the
same measure-then-judge split `asset_check.py` and `design_tokens.py` use.

Everything here is counting over the sample. It costs no credits and makes no request.

- **Interaction, never leads.** Public post data has no lead count. Comments and posts carrying a
  lead CTA are the closest proxies, and the document says so in its first lines, so the stage cannot
  present "18 likes" as "18 leads".
- **Signals are keyword-tagged, and say so.** "Proof", "offer", "people" and the rest are read off
  caption wording by the patterns below. They are a starting point the model checks against the
  quoted captions, not a classification it may cite as fact.
- **Missing stays missing.** A platform that publishes no counts (LinkedIn's company feed) is N/D,
  never 0. A lift needs `_MIN_GROUP` measured posts on each side, otherwise it is N/D.
"""

from __future__ import annotations

import datetime
import re
import statistics
from collections import Counter
from dataclasses import dataclass

from app.services.social_audit import _PLATFORM_NAMES, _engagement, _format_of, _format_timestamp
from app.services.sociavault_client import SocialFetchResult, SocialPost

#: The heading the prompt's Step 7 names. Public so the prompt test can check the two agree: an
#: instruction pointing at a section that is not there gets filled in by the model.
INTEL_HEADING = "Competitor content intelligence (measured)"

ND = "N/D"
#: Fewest measured posts on each side of a comparison before a mean or a lift is shown.
_MIN_GROUP = 3
_TOP_POSTS_PER_COMPETITOR = 3
_OFFER_LINES_PER_COMPETITOR = 5
_CLIENT_POSTS_TO_REWRITE = 5
_CAPTION_CHARS = 220

_MONTHS = (
    # "may" is left out: it is far more often the verb ("you may qualify") than the month.
    "january|february|march|april|june|july|august|september|october|november|december"
)

#: Keyword patterns for the caption signals. Order is the order of the table.
SIGNALS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "offer",
        "Offer / lead magnet (free, guide, calculator, audit, consult, discount…)",
        re.compile(
            r"\b(free|complimentary|download|guide|checklist|template|e-?book|calculator|scorecard|"
            r"audit|webinar|masterclass|discovery (?:call|session)|consult(?:ation)?|quote|"
            r"\d+\s?% off|discount|offer|giveaway)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "lead_cta",
        "Direct response CTA (book, call, DM, link in bio, apply, enquire…)",
        re.compile(
            r"\b(book (?:a|your|now|in)|call (?:us|now|today)|contact us|enquire|get in touch|apply|"
            r"sign ?up|register|link in (?:our )?bio|dm us|send us a (?:dm|message)|message us|"
            r"get started|get a quote|speak (?:to|with)|chat (?:to|with))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "proof",
        "Proof (client, case study, result, testimonial, award)",
        re.compile(
            r"\b(case study|client|customer|testimonial|review|award|finalist|winner|won|"
            r"results?|helped|congrat\w*|settled|approved|partner(?:ed|ship)?)\b|\d+(?:\.\d+)?\s?%|\$\s?\d",
            re.IGNORECASE,
        ),
    ),
    (
        "people",
        "People (named staff, team, founder, behind the scenes)",
        re.compile(
            r"\b(meet|our team|the team|team member|ceo|founder|director|manager|staff|"
            r"welcome(?: to the team)?|behind the scenes|birthday|anniversary)\b|(?<!\w)@\w{3,}",
            re.IGNORECASE,
        ),
    ),
    (
        "timely",
        "Timely hook (season, news, launch, platform or market update)",
        re.compile(
            rf"\b(black friday|eofy|end of (?:financial )?year|christmas|easter|new year|"
            rf"q[1-4]|budget|rate (?:cut|rise|hike)|rba|interest rates?|update|announc\w*|launch\w*|"
            rf"this week|now available|{_MONTHS})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "education",
        "Education (how-to, tips, mistakes, explainer)",
        re.compile(
            r"\b(how to|tips?|mistakes?|what is|what are|explained|step[- ]by[- ]step|lessons?|"
            r"myths?|things to know|\d+ ways|did you know|should you)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "question",
        "Opens with a question",
        re.compile(r"^[^.!\n]{0,160}\?"),
    ),
)
_SIGNAL_PATTERNS = {key: pattern for key, _label, pattern in SIGNALS}
_OFFER_OR_CTA = ("offer", "lead_cta")


@dataclass(frozen=True)
class AccountSample:
    """One account's fetched posts — the client's own, or one competitor's."""

    label: str
    is_client: bool
    results: tuple[SocialFetchResult, ...]


@dataclass(frozen=True)
class _Row:
    account: str
    is_client: bool
    platform: str
    post: SocialPost
    engagement: int | None
    signals: frozenset[str]


def signals_of(caption: str | None) -> frozenset[str]:
    """The caption signals a post carries, keyword-tagged."""
    text = (caption or "").strip()
    if not text:
        return frozenset()
    return frozenset(key for key, pattern in _SIGNAL_PATTERNS.items() if pattern.search(text))


def _rows(samples: list[AccountSample]) -> list[_Row]:
    return [
        _Row(s.label, s.is_client, r.platform, p, _engagement(p), signals_of(p.caption))
        for s in samples
        for r in s.results
        for p in r.posts
    ]


def _mean(values: list[int]) -> float | None:
    return statistics.fmean(values) if len(values) >= 1 else None


def _num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return ND
    return f"{value:,.0f}" if value >= 100 else f"{value:,.{digits}f}"


def _clean(text: str | None, limit: int = _CAPTION_CHARS) -> str:
    """One line, table-safe, verbatim apart from whitespace and a trailing cut."""
    if not text:
        return "(no caption)"
    flat = " ".join(text.split()).replace("|", "/")
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _platform(name: str) -> str:
    return _PLATFORM_NAMES.get(name, name.title())


def _cadence(posts: tuple[SocialPost, ...]) -> tuple[str, str]:
    """(posts per week, days since the newest post) over the dated posts in one sample."""
    dated = sorted(p.published_at for p in posts if p.published_at)
    if not dated:
        return ND, ND
    now = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
    since = f"{max(0, int((now - dated[-1]) // 86400))}"
    span_days = (dated[-1] - dated[0]) / 86400
    if len(dated) < 2 or span_days <= 0:
        return ND, since
    return f"{(len(dated) - 1) / span_days * 7:.1f}", since


def _account_table(samples: list[AccountSample]) -> list[str]:
    lines = [
        "### A. Cadence and interaction by account and platform",
        "",
        "| Account | Platform | Posts sampled | Posts/week | Days since last post | Mean engagement | "
        "Median engagement | Mean comments | Median views |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in samples:
        for r in s.results:
            if not r.posts:
                continue
            engagement = [e for e in (_engagement(p) for p in r.posts) if e is not None]
            comments = [p.comment_count for p in r.posts if p.comment_count is not None]
            views = [p.view_count for p in r.posts if p.view_count is not None]
            per_week, since = _cadence(r.posts)
            name = f"**{s.label}**" if s.is_client else s.label
            lines.append(
                f"| {name} | {_platform(r.platform)} | {len(r.posts)} | {per_week} | {since} | "
                f"{_num(_mean(engagement))} | {_num(statistics.median(engagement) if engagement else None)} | "
                f"{_num(_mean(comments))} | {_num(statistics.median(views) if views else None, 0)} |"
            )
    return lines + [""]


def _format_table(rows: list[_Row]) -> list[str]:
    """Platform × format: what each content type earned, competitors pooled against the client."""
    keys = sorted({(r.platform, _format_of(r.post)) for r in rows})
    lines = [
        "### B. What content type earned interaction (platform × format)",
        "",
        "Competitor columns pool every competitor's posts of that format on that platform. Compare "
        "formats within one platform: audiences differ in size between platforms.",
        "",
        "| Platform | Format | Competitor posts | Competitor mean engagement | Competitor median views | "
        "Client posts | Client mean engagement |",
        "|---|---|---|---|---|---|---|",
    ]
    for platform, fmt in keys:
        group = [r for r in rows if r.platform == platform and _format_of(r.post) == fmt]
        comp = [r for r in group if not r.is_client]
        client = [r for r in group if r.is_client]
        comp_eng = [r.engagement for r in comp if r.engagement is not None]
        client_eng = [r.engagement for r in client if r.engagement is not None]
        comp_views = [r.post.view_count for r in comp if r.post.view_count is not None]
        lines.append(
            f"| {_platform(platform)} | {fmt} | {len(comp)} | {_num(_mean(comp_eng))} | "
            f"{_num(statistics.median(comp_views) if comp_views else None, 0)} | {len(client)} | "
            f"{_num(_mean(client_eng))} |"
        )
    return lines + [""]


def _lift(with_: list[int], without: list[int]) -> str:
    if len(with_) < _MIN_GROUP or len(without) < _MIN_GROUP:
        return f"{ND} (fewer than {_MIN_GROUP} measured posts on one side)"
    base = statistics.fmean(without)
    if base == 0:
        return ND + " (posts without it averaged 0)"
    return f"{statistics.fmean(with_) / base:.1f}x"


def _signal_table(rows: list[_Row]) -> list[str]:
    comp = [r for r in rows if not r.is_client]
    client = [r for r in rows if r.is_client]
    lines = [
        "### C. Caption signals vs interaction (competitor posts)",
        "",
        "Signals are keyword-tagged from caption wording; check them against the captions quoted in D "
        "and E before relying on one. Lift = mean engagement of competitor posts with the signal ÷ "
        "those without it, measured posts only.",
        "",
        "| Signal | Competitor posts with it | Mean engagement with | Mean engagement without | Lift | "
        "Client posts with it |",
        "|---|---|---|---|---|---|",
    ]
    for key, label, _pattern in SIGNALS:
        has = [r for r in comp if key in r.signals]
        with_ = [r.engagement for r in has if r.engagement is not None]
        without = [r.engagement for r in comp if key not in r.signals and r.engagement is not None]
        client_has = sum(1 for r in client if key in r.signals)
        share = f"{len(has)} of {len(comp)}" if comp else "0"
        client_share = f"{client_has} of {len(client)}" if client else ND
        lines.append(
            f"| {label} | {share} | {_num(_mean(with_)) if len(with_) >= _MIN_GROUP else ND} | "
            f"{_num(_mean(without)) if len(without) >= _MIN_GROUP else ND} | {_lift(with_, without)} | "
            f"{client_share} |"
        )
    return lines + [""]


def _counts(post: SocialPost, engagement: int | None) -> str:
    parts = []
    if post.like_count is not None:
        parts.append(f"{post.like_count:,} likes")
    if post.comment_count is not None:
        parts.append(f"{post.comment_count:,} comments")
    if post.share_count is not None:
        parts.append(f"{post.share_count:,} shares")
    if post.view_count is not None:
        parts.append(f"{post.view_count:,} views")
    return ", ".join(parts) if parts else ("not published" if engagement is None else str(engagement))


def _signal_names(signals: frozenset[str]) -> str:
    return ", ".join(key for key, _label, _p in SIGNALS if key in signals) or "none tagged"


def _post_row(r: _Row) -> str:
    link = f" [link]({r.post.url})" if r.post.url else ""
    return (
        f"| {r.account} | {_platform(r.platform)} | {_format_timestamp(r.post.published_at)} | "
        f"{_format_of(r.post)} | {_counts(r.post, r.engagement)} | {_signal_names(r.signals)} | "
        f"{_clean(r.post.caption)}{link} |"
    )


_POST_HEADER = [
    "| Account | Platform | Date | Format | Interaction | Signals | Caption (verbatim, cut at "
    f"{_CAPTION_CHARS} chars) |",
    "|---|---|---|---|---|---|---|",
]


def _top_posts(samples: list[AccountSample], rows: list[_Row]) -> list[str]:
    lines = ["### D. Each competitor's highest-interaction posts", "", *_POST_HEADER]
    any_row = False
    for s in samples:
        if s.is_client:
            continue
        measured = [r for r in rows if r.account == s.label and r.engagement is not None]
        for r in sorted(measured, key=lambda r: r.engagement or 0, reverse=True)[:_TOP_POSTS_PER_COMPETITOR]:
            lines.append(_post_row(r))
            any_row = True
    if not any_row:
        lines = ["### D. Each competitor's highest-interaction posts", "", f"{ND}: no competitor post "
                 "in the sample has published engagement counts."]
    return lines + [""]


_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def offer_lines(caption: str | None) -> list[str]:
    """The sentences of a caption that carry an offer or a lead CTA, verbatim."""
    found: list[str] = []
    for match in _SENTENCE.finditer(caption or ""):
        sentence = " ".join(match.group(0).split())
        if len(sentence) < 6:
            continue
        if any(_SIGNAL_PATTERNS[key].search(sentence) for key in _OFFER_OR_CTA):
            found.append(_clean(sentence, 160))
    return found


def _offers(samples: list[AccountSample], rows: list[_Row]) -> list[str]:
    lines = [
        "### E. Offers and calls to action seen in competitor captions",
        "",
        "Verbatim sentences carrying an offer or a direct-response CTA, highest-interaction post first.",
        "",
        "| Competitor | Platform | Interaction | Offer / CTA sentence |",
        "|---|---|---|---|",
    ]
    any_row = False
    for s in samples:
        if s.is_client:
            continue
        mine = sorted(
            (r for r in rows if r.account == s.label and r.signals & set(_OFFER_OR_CTA)),
            key=lambda r: r.engagement if r.engagement is not None else -1,
            reverse=True,
        )
        seen: set[str] = set()
        kept = 0
        for r in mine:
            for sentence in offer_lines(r.post.caption):
                key = sentence.lower()
                if key in seen or kept >= _OFFER_LINES_PER_COMPETITOR:
                    continue
                seen.add(key)
                kept += 1
                any_row = True
                lines.append(f"| {s.label} | {_platform(r.platform)} | {_counts(r.post, r.engagement)} | {sentence} |")
    if not any_row:
        lines.append(f"| {ND} | — | — | No competitor caption in the sample carries an offer or CTA wording. |")
    return lines + [""]


def _cross_posting(samples: list[AccountSample]) -> list[str]:
    lines = [
        "### F. Distinct captions vs cross-posting",
        "",
        "| Account | Posts | Distinct captions | Captions posted on 2+ platforms |",
        "|---|---|---|---|",
    ]
    for s in samples:
        platforms_by_caption: dict[str, set[str]] = {}
        total = 0
        for r in s.results:
            for p in r.posts:
                total += 1
                key = " ".join((p.caption or "").lower().split())[:80]
                if key:
                    platforms_by_caption.setdefault(key, set()).add(r.platform)
        if not total:
            continue
        shared = sum(1 for platforms in platforms_by_caption.values() if len(platforms) > 1)
        name = f"**{s.label}**" if s.is_client else s.label
        lines.append(f"| {name} | {total} | {len(platforms_by_caption)} | {shared} |")
    return lines + [""]


def _client_posts(rows: list[_Row]) -> list[str]:
    client = [r for r in rows if r.is_client and r.engagement is not None]
    lines = ["### G. The client's own posts to optimise (lowest interaction first)", ""]
    if not client:
        return lines + [f"{ND}: none of the client's sampled posts publishes engagement counts.", ""]
    lowest = sorted(client, key=lambda r: (r.engagement or 0, -(r.post.published_at or 0)))[:_CLIENT_POSTS_TO_REWRITE]
    best = max(client, key=lambda r: r.engagement or 0)
    lines += [*_POST_HEADER, *(_post_row(r) for r in lowest), ""]
    if best not in lowest:
        lines += ["The client's own highest-interaction post, for reference:", "", *_POST_HEADER, _post_row(best), ""]
    return lines


def build_intel_md(samples: list[AccountSample]) -> str:
    """The measured comparison block, or "" when no competitor post was fetched — there is nothing
    to compare against then, and Step 7 says what to do without it."""
    rows = _rows(samples)
    if not any(not r.is_client for r in rows):
        return ""
    measured_formats = Counter(_format_of(r.post) for r in rows)
    lines = [
        f"## {INTEL_HEADING}",
        "",
        "Computed from the same SociaVault posts listed above — no further data was fetched. "
        "Engagement = likes + comments + shares, over whichever of those the platform publishes; "
        f"a platform that publishes none (LinkedIn's company feed) is {ND}, never 0. **Leads are not "
        "observable in public post data.** Comments and posts carrying a direct-response CTA are the "
        "closest proxies; interaction must never be reported as leads.",
        "",
        f"Sample: {len(rows)} posts across {len(samples)} accounts; formats "
        + ", ".join(f"{fmt} {n}" for fmt, n in measured_formats.most_common())
        + ".",
        "",
    ]
    lines += _account_table(samples)
    lines += _format_table(rows)
    lines += _signal_table(rows)
    lines += _top_posts(samples, rows)
    lines += _offers(samples, rows)
    lines += _cross_posting(samples)
    lines += _client_posts(rows)
    return "\n".join(lines).rstrip() + "\n"
