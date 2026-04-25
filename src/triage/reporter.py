"""reporter.py - render severity report as markdown."""

from __future__ import annotations

import datetime
import math
from typing import TYPE_CHECKING

from triage.scorer import RECOVERY_WINDOW, TAIL_RISK_WINDOW, ScoredPattern

if TYPE_CHECKING:
    from triage.comparer import ComparisonReport

# Human-readable descriptions for each failure classification
CLASSIFICATION_LABELS: dict[str, str] = {
    "coordination_failure": "Coordination Failure",
    "agent_error": "Agent Error",
    "information_lag": "Information Lag",
    "environment_constraint": "Environment Constraint",
    "unclassified": "Unclassified",
}

NEXT_ACTIONS: dict[str, str] = {
    "coordination_failure": (
        "Review inter-agent messaging cadence and add position-sync "
        "checkpoints before high-stakes joint actions."
    ),
    "agent_error": (
        "Inspect the agent's reasoning trace at the failing turns; "
        "check if the action selection logic handles boundary states."
    ),
    "information_lag": (
        "Increase belief-state refresh frequency or add an explicit "
        "re-observe step after N turns without a sync message."
    ),
    "environment_constraint": (
        "Audit world-boundary detection in the agent's planner; "
        "add wall/edge checks before committing to a move."
    ),
    "unclassified": (
        "Manually review these events to assign a classification, "
        "then rerun triage with the updated labels."
    ),
}


def _fmt_pct(rate: float) -> str:
    """Format a rate as a truncated integer percentage string.

    The input is first clamped into the 0.0–1.0 range. Values below 1.0
    are capped at 99% after truncation, so this only returns 100% when the
    clamped input is exactly 1.0.
    """
    clamped = max(0.0, min(rate, 1.0))
    pct = math.floor(clamped * 100 + 1e-9)
    if clamped < 1.0:
        pct = min(pct, 99)
    return f"{pct}%"


def _recovery_bar(rate: float) -> str:
    clamped = max(0.0, min(rate, 1.0))
    if clamped == 0.0:
        filled = 0
    else:
        filled = max(1, min(10, math.floor(clamped * 10)))
    bar = "#" * filled + "-" * (10 - filled)
    return f"[{bar}] {_fmt_pct(clamped)}"


def _format_latency(sp: ScoredPattern) -> str:
    """Human-readable recovery dynamics summary."""
    if sp.median_recovery_latency is None:
        return "no recoveries observed"
    latency = sp.median_recovery_latency
    turn_word = "turn" if latency == 1 else "turns"
    return f"median recovery latency: {latency:g} {turn_word}"


def _format_tail_risk(sp: ScoredPattern) -> str:
    if sp.unrecovered_tail_count == 0:
        return "no tail risk"
    run_word = "failure" if sp.unrecovered_tail_count == 1 else "failures"
    return (
        f"{sp.unrecovered_tail_count} {run_word} unrecovered after "
        f"{TAIL_RISK_WINDOW} turns"
    )


def _explain(sp: ScoredPattern, total_runs: int) -> str:
    p = sp.pattern
    label = CLASSIFICATION_LABELS.get(p.failure_classification, p.failure_classification)
    run_count = len(p.run_ids)
    run_word = "run" if run_count == 1 else "runs"
    freq = p.frequency

    if sp.recovery_rate == 0.0:
        recovery_note = (
            "None of these failures were followed by a successful action "
            f"within {RECOVERY_WINDOW} turns, meaning the agent got stuck rather than adapting."
        )
    else:
        latency_phrase = ""
        if sp.median_recovery_latency is not None:
            latency = sp.median_recovery_latency
            latency_turn_word = "turn" if latency == 1 else "turns"
            latency_phrase = (
                f" (median latency {latency:g} {latency_turn_word})"
            )
        recovery_note = (
            f"About {_fmt_pct(sp.recovery_rate)} of occurrences were followed by a "
            f"successful action within {RECOVERY_WINDOW} turns{latency_phrase}, "
            "suggesting partial self-correction."
        )
        if sp.unrecovered_tail_count > 0:
            tail_word = (
                "failure" if sp.unrecovered_tail_count == 1 else "failures"
            )
            recovery_note += (
                f" However, {sp.unrecovered_tail_count} {tail_word} remained "
                f"unrecovered after {TAIL_RISK_WINDOW} turns — a tail-risk signal."
            )

    div_note = ""
    if p.divergence_fields:
        fields = ", ".join(sorted(p.divergence_fields))
        div_note = (
            f" Belief divergence was present on field(s): {fields}, "
            "meaning the agent was operating on stale world state when it failed."
        )

    recurrence_note = _recurrence_note(sp)

    return (
        f"Agent {p.agent_id} hit a **{label}** on `{p.tool_name}` "
        f"{freq} time(s) across {run_count} {run_word}. "
        f"{recurrence_note}"
        f"{recovery_note}"
        f"{div_note} "
        f"With a final score of {sp.final_score:.2f}, this pattern ranks high "
        "because its classification carries significant weight in the scoring model."
    )


def _recurrence_note(sp: ScoredPattern) -> str:
    """One-sentence recurrence summary for _explain, or empty when N/A."""
    if sp.runs_total <= 1:
        return ""
    coverage = f"Appeared in {sp.runs_seen_in}/{sp.runs_total} runs"
    if sp.trend == "insufficient data":
        return f"{coverage}. "
    if sp.trend == "new":
        return f"{coverage}; this is a newly-emerging pattern. "
    if sp.trend == "resolved":
        return f"{coverage}; the pattern appears to have resolved in recent runs. "
    return f"{coverage}; trend is {sp.trend}. "


def build_report(
    scored: list[ScoredPattern],
    total_runs: int,
    total_patterns: int,
    source_files: list[str],
    top_n: int = 3,
) -> str:
    now = datetime.datetime.now(tz=datetime.UTC)
    date_str = now.strftime("%Y-%m-%d %H:%M UTC")
    top = scored[:top_n]

    lines: list[str] = []

    lines.append("# Triage Report")
    lines.append("")
    lines.append(f"Generated: {date_str}")
    lines.append("")

    file_list = ", ".join(source_files)
    lines.append(
        f"**{total_runs} run(s) analyzed. "
        f"{total_patterns} incident pattern(s) detected. "
        f"Top {min(top_n, total_patterns)} worth your attention this morning.**"
    )
    lines.append("")
    lines.append(f"Sources: `{file_list}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not top:
        lines.append("No incidents detected. All agents completed their turns cleanly.")
        return "\n".join(lines)

    for rank, sp in enumerate(top, start=1):
        p = sp.pattern
        label = CLASSIFICATION_LABELS.get(p.failure_classification, p.failure_classification)

        lines.append(f"## #{rank} - {p.display_name()}")
        lines.append("")
        lines.append(f"**Category:** {label}")
        lines.append("")
        lines.append(
            "| Metric | Value |"
        )
        lines.append("|--------|-------|")
        lines.append(f"| Severity Score | {sp.severity_score:.2f} / 15.00 |")
        lines.append(f"| Frequency | {p.frequency} event(s) across {len(p.run_ids)} run(s) |")
        lines.append(
            f"| Appeared in | {sp.runs_seen_in}/{sp.runs_total} runs |"
        )
        lines.append(f"| Trend | {sp.trend} |")
        lines.append(f"| Recovery Rate | {_recovery_bar(sp.recovery_rate)} |")
        lines.append(f"| Recovery Latency | {_format_latency(sp)} |")
        lines.append(f"| Tail Risk | {_format_tail_risk(sp)} |")
        lines.append(
            f"| Confidence | {sp.confidence_label} ({p.frequency} occurrence"
            f"{'s' if p.frequency != 1 else ''}) |"
        )
        lines.append(f"| Final Score | **{sp.final_score:.2f}** |")
        lines.append("")
        lines.append("**Why this matters:**")
        lines.append("")
        lines.append(_explain(sp, total_runs))
        lines.append("")
        next_action = NEXT_ACTIONS.get(p.failure_classification, NEXT_ACTIONS["unclassified"])
        lines.append(f"**Suggested next action:** {next_action}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def build_comparison_report(
    comparison: ComparisonReport,
    before_path: str,
    after_path: str,
) -> str:
    """Render a before/after comparison as a markdown report.

    The output answers a different question than build_report: not
    "what should I look at this morning" but "did the change between
    these two batches make agent behavior better or worse, and where".
    """
    now = datetime.datetime.now(tz=datetime.UTC)
    date_str = now.strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("# Triage Comparison Report")
    lines.append("")
    lines.append(f"Generated: {date_str}")
    lines.append("")
    lines.append(
        f"**Before:** `{before_path}` "
        f"({comparison.before_run_count} run(s), "
        f"{comparison.before_event_count} event(s))"
    )
    lines.append(
        f"**After:** `{after_path}` "
        f"({comparison.after_run_count} run(s), "
        f"{comparison.after_event_count} event(s))"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Headline")
    lines.append("")
    headline_lines = _comparison_headlines(comparison)
    if not headline_lines:
        lines.append("No changes detected between the two batches.")
    else:
        for h in headline_lines:
            lines.append(f"- {h}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Classification deltas")
    lines.append("")
    lines.append(
        "| Classification | Before | After | Δ frequency | "
        "Unrecovered before | Unrecovered after | Δ unrecovered |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for d in comparison.deltas:
        label = CLASSIFICATION_LABELS.get(d.classification, d.classification)
        lines.append(
            f"| {label} | {d.before_frequency} | {d.after_frequency} | "
            f"{d.frequency_change} | {d.before_unrecovered} | "
            f"{d.after_unrecovered} | {d.unrecovered_change} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## New patterns (in `after` only)")
    lines.append("")
    if not comparison.new_patterns:
        lines.append("None.")
    else:
        for sp in comparison.new_patterns:
            p = sp.pattern
            occ_word = "occurrence" if p.frequency == 1 else "occurrences"
            lines.append(
                f"- {p.display_name()} — {p.frequency} {occ_word}"
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Resolved patterns (in `before` only)")
    lines.append("")
    if not comparison.resolved_patterns:
        lines.append("None.")
    else:
        for sp in comparison.resolved_patterns:
            p = sp.pattern
            occ_word = "occurrence" if p.frequency == 1 else "occurrences"
            lines.append(
                f"- {p.display_name()} — was {p.frequency} {occ_word}"
            )
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## Persisting patterns")
    lines.append("")
    if not comparison.persisting_patterns:
        lines.append("None.")
    else:
        lines.append("| Pattern | Before | After | Δ frequency |")
        lines.append("|---|---|---|---|")
        for before_sp, after_sp in comparison.persisting_patterns:
            from triage.comparer import _pct_change
            delta = _pct_change(
                before_sp.pattern.frequency, after_sp.pattern.frequency
            )
            lines.append(
                f"| {after_sp.pattern.display_name()} "
                f"| {before_sp.pattern.frequency} "
                f"| {after_sp.pattern.frequency} "
                f"| {delta} |"
            )
    lines.append("")
    return "\n".join(lines)


def _comparison_headlines(comparison: ComparisonReport) -> list[str]:
    """Pick the top few facts a reader should see first."""
    headlines: list[str] = []
    for d in comparison.deltas:
        label = CLASSIFICATION_LABELS.get(d.classification, d.classification)
        change = d.frequency_change
        if change == "stable":
            continue
        headlines.append(f"{label}: {change} ({d.before_frequency} → {d.after_frequency})")
        unrec_change = d.unrecovered_change
        if unrec_change not in ("stable",) and (
            d.before_unrecovered != 0 or d.after_unrecovered != 0
        ):
            headlines.append(
                f"{label} unrecovered: {unrec_change} "
                f"({d.before_unrecovered} → {d.after_unrecovered})"
            )
    if comparison.new_patterns:
        for sp in comparison.new_patterns[:3]:
            headlines.append(f"new pattern: {sp.pattern.display_name()}")
    if comparison.resolved_patterns:
        for sp in comparison.resolved_patterns[:3]:
            headlines.append(f"resolved: {sp.pattern.display_name()}")
    return headlines
