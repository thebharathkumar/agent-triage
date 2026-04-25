"""scorer.py - compute severity scores for incident patterns."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

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

# Window used for the tail-risk signal. Failures that stay unrecovered after
# this many turns are treated as "stuck for good", not merely slow.
TAIL_RISK_WINDOW = 10

# Occurrence count at which confidence reaches 1.0.
# Below this, confidence scales linearly, so small samples are visibly low.
CONFIDENCE_THRESHOLD = 5


@dataclass
class ScoredPattern:
    pattern: IncidentPattern
    frequency_score: float
    severity_score: float
    recovery_rate: float
    final_score: float
    confidence: float
    median_recovery_latency: float | None
    unrecovered_tail_count: int

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 0.8:
            return "high"
        if self.confidence >= 0.4:
            return "medium"
        return "low"


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


@dataclass
class _RecoveryStats:
    recovery_rate: float
    median_latency: float | None
    unrecovered_tail_count: int


def _compute_recovery_stats(
    pattern: IncidentPattern, timeline: AgentTimeline
) -> _RecoveryStats:
    """Measure how and how quickly the agent recovered after each failure.

    Returns:
        recovery_rate: fraction of failures followed by a success within
            RECOVERY_WINDOW turns.
        median_latency: median turns-to-first-success across recovered events,
            or None if no events recovered.
        unrecovered_tail_count: failures with no success within
            TAIL_RISK_WINDOW turns. This is the "still stuck after a long
            while" signal.
    """
    if not pattern.events:
        return _RecoveryStats(0.0, None, 0)

    recovered = 0
    latencies: list[int] = []
    tail_unrecovered = 0

    for incident in pattern.events:
        key = (incident.run_id, incident.agent_id)
        agent_turns = timeline.get(key, [])
        failure_turn = incident.turn

        first_success_offset: int | None = None
        for turn, succeeded in agent_turns:
            if turn <= failure_turn or not succeeded:
                continue
            first_success_offset = turn - failure_turn
            break

        if first_success_offset is not None and first_success_offset <= RECOVERY_WINDOW:
            recovered += 1
            latencies.append(first_success_offset)

        if first_success_offset is None or first_success_offset > TAIL_RISK_WINDOW:
            tail_unrecovered += 1

    return _RecoveryStats(
        recovery_rate=recovered / len(pattern.events),
        median_latency=float(median(latencies)) if latencies else None,
        unrecovered_tail_count=tail_unrecovered,
    )


def _compute_confidence(frequency: int) -> float:
    """Linearly scale confidence from 0 to 1 as occurrences approach threshold.

    Small samples produce low confidence so that rankings built on thin
    evidence are surfaced as such rather than presented with false precision.
    """
    return min(1.0, frequency / CONFIDENCE_THRESHOLD)


def score_patterns(
    patterns: list[IncidentPattern],
    all_events: list[TraceEvent],
    total_runs: int,
) -> list[ScoredPattern]:
    """Score each incident pattern and return a sorted list (highest first)."""
    scored: list[ScoredPattern] = []
    timeline = _build_agent_timeline(all_events)

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

        # Recovery dynamics
        recovery = _compute_recovery_stats(pattern, timeline)

        # Apply no-recovery multiplier when recovery rate is zero
        recovery_multiplier = (
            1.0 if recovery.recovery_rate > 0 else NO_RECOVERY_MULTIPLIER
        )
        severity_score = base_weight * 10.0 * recovery_multiplier

        # Final composite: weighted sum
        final_score = (frequency_score * 0.4) + (severity_score * 0.6)

        scored.append(
            ScoredPattern(
                pattern=pattern,
                frequency_score=frequency_score,
                severity_score=severity_score,
                recovery_rate=recovery.recovery_rate,
                final_score=final_score,
                confidence=_compute_confidence(pattern.frequency),
                median_recovery_latency=recovery.median_latency,
                unrecovered_tail_count=recovery.unrecovered_tail_count,
            )
        )

    # Sort descending by final score
    scored.sort(key=lambda s: s.final_score, reverse=True)
    return scored
