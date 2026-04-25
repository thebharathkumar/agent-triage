"""scorer.py - compute severity scores for incident patterns."""

from __future__ import annotations

from collections import Counter
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

# Relative change thresholds for trend classification. recent-rate vs
# baseline-rate ratios outside [1 - band, 1 + band] flip to decreasing /
# increasing; inside, the pattern is "stable".
TREND_BAND = 0.3

# Minimum number of runs required to emit a directional trend. Below this
# threshold we report "insufficient data" instead of guessing.
TREND_MIN_RUNS = 3

# Sliding-window size for trend detection. The most recent
# TREND_WINDOW_SIZE runs are compared against the prior
# TREND_WINDOW_SIZE runs. Sliding window kicks in when the input has
# at least 2 * TREND_WINDOW_SIZE runs; below that we fall back to the
# split-half partition so behavior remains useful on small batches.
TREND_WINDOW_SIZE = 3


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
    runs_seen_in: int
    runs_total: int
    trend: str

    @property
    def confidence_label(self) -> str:
        if self.confidence >= 0.8:
            return "high"
        if self.confidence >= 0.4:
            return "medium"
        return "low"

    @property
    def run_coverage(self) -> float:
        if self.runs_total == 0:
            return 0.0
        return self.runs_seen_in / self.runs_total


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


def _ordered_run_ids(all_events: list[TraceEvent]) -> list[str]:
    """Runs ordered by the turn-0 event that appears first in the input.

    We deliberately preserve input order rather than sorting
    lexicographically: the input is a batch of ndjson files that was
    presented to the CLI in some order, and that order is the closest
    proxy to chronology we have without a timestamp field we trust.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for ev in all_events:
        if ev.run_id in seen_set:
            continue
        seen.append(ev.run_id)
        seen_set.add(ev.run_id)
    return seen


def _compute_trend(
    pattern: IncidentPattern, ordered_runs: list[str]
) -> str:
    """Classify the recurrence trend across runs.

    Uses a sliding-window comparison when there are at least
    2 * TREND_WINDOW_SIZE runs: per-run occurrence rate in the most
    recent window is compared against the immediately prior window.
    For shorter histories the function falls back to split-half so the
    label remains directional on small batches rather than always
    "insufficient data".

    Returns one of: "insufficient data", "new", "resolved",
    "increasing", "decreasing", "stable".
    """
    n = len(ordered_runs)
    if n < TREND_MIN_RUNS:
        return "insufficient data"

    if n >= 2 * TREND_WINDOW_SIZE:
        baseline_runs = ordered_runs[-2 * TREND_WINDOW_SIZE : -TREND_WINDOW_SIZE]
        recent_runs = ordered_runs[-TREND_WINDOW_SIZE:]
    else:
        midpoint = n // 2
        baseline_runs = ordered_runs[:midpoint]
        recent_runs = ordered_runs[midpoint:]

    return _classify_window_change(pattern, baseline_runs, recent_runs)


def _classify_window_change(
    pattern: IncidentPattern,
    baseline_runs: list[str],
    recent_runs: list[str],
) -> str:
    """Per-run occurrence rate comparison between two run windows."""
    run_counts: Counter[str] = Counter(ev.run_id for ev in pattern.events)
    baseline_total = sum(run_counts[r] for r in baseline_runs)
    recent_total = sum(run_counts[r] for r in recent_runs)

    baseline_rate = (
        baseline_total / len(baseline_runs) if baseline_runs else 0.0
    )
    recent_rate = recent_total / len(recent_runs) if recent_runs else 0.0

    if baseline_rate == 0 and recent_rate > 0:
        return "new"
    if baseline_rate > 0 and recent_rate == 0:
        return "resolved"
    if baseline_rate == 0 and recent_rate == 0:
        return "stable"

    ratio = recent_rate / baseline_rate
    if ratio >= 1 + TREND_BAND:
        return "increasing"
    if ratio <= 1 - TREND_BAND:
        return "decreasing"
    return "stable"


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
    ordered_runs = _ordered_run_ids(all_events)

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
                runs_seen_in=len(pattern.run_ids),
                runs_total=total_runs,
                trend=_compute_trend(pattern, ordered_runs),
            )
        )

    # Sort descending by final score
    scored.sort(key=lambda s: s.final_score, reverse=True)
    return scored
