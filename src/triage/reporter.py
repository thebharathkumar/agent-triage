"""reporter.py - render severity report as markdown."""

from __future__ import annotations

import datetime

from triage.grouper import IncidentPattern
from triage.scorer import ScoredPattern

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


def _recovery_bar(rate: float) -> str:
    filled = round(rate * 10)
    bar = "#" * filled + "-" * (10 - filled)
    return f"[{bar}] {rate:.0%}"


def _explain(sp: ScoredPattern, total_runs: int) -> str:
    p = sp.pattern
    label = CLASSIFICATION_LABELS.get(p.failure_classification, p.failure_classification)
    run_count = len(p.run_ids)
    run_word = "run" if run_count == 1 else "runs"
    freq = p.frequency

    if sp.recovery_rate == 0.0:
        recovery_note = (
            "None of these failures were followed by a successful action "
            f"within {3} turns, meaning the agent got stuck rather than adapting."
        )
    else:
        recovery_note = (
            f"About {sp.recovery_rate:.0%} of occurrences were followed by a "
            "successful action within 3 turns, suggesting partial self-correction."
        )

    div_note = ""
    if p.divergence_fields:
        fields = ", ".join(sorted(p.divergence_fields))
        div_note = (
            f" Belief divergence was present on field(s): {fields}, "
            "meaning the agent was operating on stale world state when it failed."
        )

    return (
        f"Agent {p.agent_id} hit a **{label}** on `{p.tool_name}` "
        f"{freq} time(s) across {run_count} {run_word}. "
        f"{recovery_note}"
        f"{div_note} "
        f"With a final score of {sp.final_score:.2f}, this pattern ranks high "
        "because its classification carries significant weight in the scoring model."
    )


def build_report(
    scored: list[ScoredPattern],
    total_runs: int,
    total_patterns: int,
    source_files: list[str],
    top_n: int = 3,
) -> str:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
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
            f"| Metric | Value |"
        )
        lines.append("|--------|-------|")
        lines.append(f"| Severity Score | {sp.severity_score:.2f} / 15.00 |")
        lines.append(f"| Frequency | {p.frequency} event(s) across {len(p.run_ids)} run(s) |")
        lines.append(f"| Recovery Rate | {_recovery_bar(sp.recovery_rate)} |")
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
