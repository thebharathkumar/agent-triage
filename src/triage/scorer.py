"""scorer.py - compute severity scores for incident patterns."""

from __future__ import annotations

from dataclasses import dataclass

from triage.grouper import IncidentPattern
from triage.loader import TraceEvent

# Severity weights per failure classification
CLASSIFICATION_WEIGHTS: dict[str, float] = {
    "coordination_failure": 1.0,
    "agent_error": 0.7,
    "information_lag": 0.5,
    "environment_constraint": 0.2,
    "unclassified": 0.3,
}

# Multiplier applied to patterns with zero recovery
NO_RECOVERY_MULTIPLIER = 1.5

# How many turns after a failure to look for recovery
RECOVERY_WINDOW = 3


@dataclass
class ScoredPattern:
    pattern: IncidentPattern
    frequency_score: float
    severity_score: float
    recovery_rate: float
    final_score: float


def _compute_recovery_rate(pattern: IncidentPattern, all_events: list[TraceEvent]) -> float:
    """Estimate what fraction of failures were followed by a success.

    For each failed event, we check whether the same agent succeeds with any
    action within RECOVERY_WINDOW turns after the failure turn.
    """
    if not pattern.events:
        return 0.0

    # Index all events by (run_id, agent_id) -> sorted list of (turn, succeeded)
    agent_timeline: dict[tuple[str, str], list[tuple[int, bool]]] = {}
    for ev in all_events:
        key = (ev.run_id, ev.agent_id)
        if key not in agent_timeline:
            agent_timeline[key] = []
        agent_timeline[key].append((ev.turn, ev.action_succeeded))

    # Sort each timeline by turn
    for key in agent_timeline:
        agent_timeline[key].sort(key=lambda x: x[0])

    recovered = 0
    for incident in pattern.events:
        key = (incident.run_id, incident.agent_id)
        timeline = agent_timeline.get(key, [])
        failure_turn = incident.turn
        # Look for any success within RECOVERY_WINDOW turns after failure
        found_recovery = any(
            succeeded
            for turn, succeeded in timeline
            if failure_turn < turn <= failure_turn + RECOVERY_WINDOW
        )
        if found_recovery:
            recovered += 1

    return recovered / len(pattern.events)


def score_patterns(
    patterns: list[IncidentPattern],
    all_events: list[TraceEvent],
    total_runs: int,
) -> list[ScoredPattern]:
    """Score each incident pattern and return a sorted list (highest first)."""
    scored: list[ScoredPattern] = []

    # Frequency normalization denominator: avoid div-by-zero
    max_freq = max((p.frequency for p in patterns), default=1)

    for pattern in patterns:
        # Frequency: normalized 0-1, then scaled to 0-10
        freq_norm = pattern.frequency / max_freq
        frequency_score = freq_norm * 10.0

        # Severity: base weight from classification table
        base_weight = CLASSIFICATION_WEIGHTS.get(
            pattern.failure_classification, 0.3
        )

        # Recovery
        recovery_rate = _compute_recovery_rate(pattern, all_events)

        # Apply no-recovery multiplier when recovery rate is zero
        recovery_multiplier = 1.0 if recovery_rate > 0 else NO_RECOVERY_MULTIPLIER
        severity_score = base_weight * 10.0 * recovery_multiplier

        # Final composite: weighted sum
        final_score = (frequency_score * 0.4) + (severity_score * 0.6)

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
