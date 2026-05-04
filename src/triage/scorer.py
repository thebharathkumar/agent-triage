"""scorer.py - compute severity scores for incident patterns."""

from __future__ import annotations

from dataclasses import dataclass

from triage.config import ScoringConfig
from triage.grouper import IncidentPattern
from triage.loader import TraceEvent

# Module-level constants kept for backward-compat with imports elsewhere
# (reporter.py uses RECOVERY_WINDOW). Configurable callers should pass
# a ``ScoringConfig`` to ``score_patterns`` instead.
CLASSIFICATION_WEIGHTS: dict[str, float] = {
    "coordination_failure": 1.0,
    "agent_error": 0.7,
    "information_lag": 0.5,
    "environment_constraint": 0.2,
    "unclassified": 0.3,
}
NO_RECOVERY_MULTIPLIER = 1.5
RECOVERY_WINDOW = 3


@dataclass
class ScoredPattern:
    pattern: IncidentPattern
    frequency_score: float
    severity_score: float
    recovery_rate: float
    final_score: float


AgentTimeline = dict[tuple[str, str], list[tuple[int, bool]]]


def _build_agent_timeline(all_events: list[TraceEvent]) -> AgentTimeline:
    timeline: AgentTimeline = {}
    for ev in all_events:
        key = (ev.run_id, ev.agent_id)
        if key not in timeline:
            timeline[key] = []
        timeline[key].append((ev.turn, ev.action_succeeded))
    for key in timeline:
        timeline[key].sort(key=lambda x: x[0])
    return timeline


def _compute_recovery_rate(
    pattern: IncidentPattern,
    timeline: AgentTimeline,
    recovery_window: int,
) -> float:
    """Estimate what fraction of failures were followed by a success.

    For each failed event, we check whether the same agent succeeds with any
    action within ``recovery_window`` turns after the failure turn.
    """
    if not pattern.events:
        return 0.0

    recovered = 0
    for incident in pattern.events:
        key = (incident.run_id, incident.agent_id)
        agent_turns = timeline.get(key, [])
        failure_turn = incident.turn
        found_recovery = any(
            succeeded
            for turn, succeeded in agent_turns
            if failure_turn < turn <= failure_turn + recovery_window
        )
        if found_recovery:
            recovered += 1

    return recovered / len(pattern.events)


def score_patterns(
    patterns: list[IncidentPattern],
    all_events: list[TraceEvent],
    total_runs: int,
    config: ScoringConfig | None = None,
) -> list[ScoredPattern]:
    """Score each incident pattern and return a sorted list (highest first)."""
    cfg = config or ScoringConfig()

    scored: list[ScoredPattern] = []
    timeline = _build_agent_timeline(all_events)

    # Frequency normalization denominator: avoid div-by-zero
    max_freq = max((p.frequency for p in patterns), default=1)

    for pattern in patterns:
        # Frequency: normalized 0-1, then scaled to 0-10
        freq_norm = pattern.frequency / max_freq
        frequency_score = freq_norm * 10.0

        # Severity: base weight from configured table
        base_weight = cfg.weights.get(pattern.failure_classification, 0.3)

        # Recovery
        recovery_rate = _compute_recovery_rate(pattern, timeline, cfg.recovery_window)

        # Apply no-recovery multiplier when recovery rate is zero
        recovery_multiplier = 1.0 if recovery_rate > 0 else cfg.no_recovery_multiplier
        severity_score = base_weight * 10.0 * recovery_multiplier

        # Final composite: configurable weighted sum
        final_score = (
            frequency_score * cfg.frequency_weight
            + severity_score * cfg.severity_weight
        )

        scored.append(
            ScoredPattern(
                pattern=pattern,
                frequency_score=frequency_score,
                severity_score=severity_score,
                recovery_rate=recovery_rate,
                final_score=final_score,
            )
        )

    # Sort descending by final score
    scored.sort(key=lambda s: s.final_score, reverse=True)
    return scored
