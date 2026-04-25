"""Tests for triage.scorer."""

from __future__ import annotations

from tests.conftest import make_event
from triage.grouper import group_events
from triage.scorer import (
    CLASSIFICATION_WEIGHTS,
    CONFIDENCE_THRESHOLD,
    NO_RECOVERY_MULTIPLIER,
    TAIL_RISK_WINDOW,
    score_patterns,
)


def _build_pattern_and_events(
    *,
    classification: str,
    count: int = 1,
    recovery_turns: list[int] | None = None,
    run_id: str = "run-test",
) -> tuple:
    """Helper to build a list of events and the grouped patterns."""
    events = []
    for i in range(count):
        events.append(
            make_event(
                event_id=f"fail-{i}",
                run_id=run_id,
                turn=i * 4,
                agent_id="A",
                action_succeeded=False,
                failure_classification=classification,
            )
        )
    if recovery_turns:
        for i, rt in enumerate(recovery_turns):
            events.append(
                make_event(
                    event_id=f"ok-{i}",
                    run_id=run_id,
                    turn=rt,
                    agent_id="A",
                    action_succeeded=True,
                    failure_classification=None,
                )
            )
    patterns = group_events(events)
    return patterns, events


class TestScorePatterns:
    def test_empty_patterns_returns_empty(self):
        result = score_patterns([], [], 1)
        assert result == []

    def test_coordination_failure_has_highest_base_weight(self):
        coord_weight = CLASSIFICATION_WEIGHTS["coordination_failure"]
        for k, v in CLASSIFICATION_WEIGHTS.items():
            if k != "coordination_failure":
                assert coord_weight >= v

    def test_no_recovery_multiplier_applied_when_rate_is_zero(self):
        # Build a pattern with one failure and no success events
        patterns, events = _build_pattern_and_events(
            classification="agent_error", count=1
        )
        scored = score_patterns(patterns, events, 1)
        assert len(scored) == 1
        sp = scored[0]
        base = CLASSIFICATION_WEIGHTS["agent_error"] * 10.0
        expected_severity = base * NO_RECOVERY_MULTIPLIER
        assert abs(sp.severity_score - expected_severity) < 0.01

    def test_recovery_removes_multiplier(self):
        # Failure at turn 0, success at turn 1 (within window)
        patterns, events = _build_pattern_and_events(
            classification="agent_error",
            count=1,
            recovery_turns=[1],
        )
        scored = score_patterns(patterns, events, 1)
        sp = scored[0]
        # Recovery rate should be 1.0, multiplier should be 1.0 not 1.5
        assert sp.recovery_rate == 1.0
        base = CLASSIFICATION_WEIGHTS["agent_error"] * 10.0
        assert abs(sp.severity_score - base) < 0.01

    def test_higher_frequency_increases_final_score(self):
        patterns_low, events_low = _build_pattern_and_events(
            classification="environment_constraint", count=1
        )
        patterns_high, events_high = _build_pattern_and_events(
            classification="environment_constraint", count=5
        )
        scored_low = score_patterns(patterns_low, events_low, 1)
        scored_high = score_patterns(patterns_high, events_high, 1)
        # Both have same base severity but high frequency should push score up
        assert scored_high[0].final_score >= scored_low[0].final_score

    def test_sorted_descending_by_final_score(self):
        # One high-severity (coordination) + one low-severity (environment)
        events = [
            make_event(
                event_id="e1",
                agent_id="A",
                turn=0,
                action_succeeded=False,
                failure_classification="coordination_failure",
            ),
            make_event(
                event_id="e2",
                agent_id="B",
                turn=1,
                action_succeeded=False,
                failure_classification="environment_constraint",
            ),
        ]
        patterns = group_events(events)
        scored = score_patterns(patterns, events, 1)
        assert len(scored) == 2
        assert scored[0].final_score >= scored[1].final_score

    def test_recovery_rate_is_between_0_and_1(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error",
            count=3,
            recovery_turns=[1, 5],  # 2 of 3 failures recover
        )
        scored = score_patterns(patterns, events, 1)
        sp = scored[0]
        assert 0.0 <= sp.recovery_rate <= 1.0

    def test_unclassified_gets_default_weight(self):
        events = [
            make_event(
                event_id="e1",
                action_succeeded=False,
                failure_classification=None,
            )
        ]
        patterns = group_events(events)
        scored = score_patterns(patterns, events, 1)
        assert len(scored) == 1
        sp = scored[0]
        # unclassified weight = 0.3, with no-recovery multiplier
        expected = 0.3 * 10.0 * NO_RECOVERY_MULTIPLIER
        assert abs(sp.severity_score - expected) < 0.01

    def test_frequency_score_max_is_10_for_highest_count(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error", count=10
        )
        # With only one pattern, it should get frequency_score = 10.0
        scored = score_patterns(patterns, events, 1)
        assert abs(scored[0].frequency_score - 10.0) < 0.01


class TestConfidence:
    def test_confidence_scales_with_frequency(self):
        low_patterns, low_events = _build_pattern_and_events(
            classification="agent_error", count=1
        )
        high_patterns, high_events = _build_pattern_and_events(
            classification="agent_error", count=CONFIDENCE_THRESHOLD,
        )
        low_scored = score_patterns(low_patterns, low_events, 1)[0]
        high_scored = score_patterns(high_patterns, high_events, 1)[0]
        assert low_scored.confidence < high_scored.confidence
        assert high_scored.confidence == 1.0

    def test_confidence_caps_at_one(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error", count=CONFIDENCE_THRESHOLD * 4,
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.confidence == 1.0

    def test_confidence_label_low_for_single_occurrence(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error", count=1
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.confidence_label == "low"

    def test_confidence_label_high_at_threshold(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error",
            count=CONFIDENCE_THRESHOLD,
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.confidence_label == "high"


class TestRecoveryDynamics:
    def test_median_latency_is_none_when_no_recoveries(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error", count=2
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.median_recovery_latency is None

    def test_median_latency_reports_turns_to_first_success(self):
        # Failure at 0 recovers at turn 2; failure at 4 recovers at turn 5.
        # Latencies: [2, 1] -> median 1.5
        patterns, events = _build_pattern_and_events(
            classification="agent_error",
            count=2,
            recovery_turns=[2, 5],
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.median_recovery_latency == 1.5

    def test_tail_risk_counts_failures_unrecovered_beyond_window(self):
        # Single failure at turn 0, success only at turn TAIL_RISK_WINDOW + 5
        # -> still counted as tail-unrecovered.
        patterns, events = _build_pattern_and_events(
            classification="agent_error",
            count=1,
            recovery_turns=[TAIL_RISK_WINDOW + 5],
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.unrecovered_tail_count == 1

    def test_tail_risk_zero_when_fast_recovery(self):
        patterns, events = _build_pattern_and_events(
            classification="agent_error",
            count=1,
            recovery_turns=[1],
        )
        scored = score_patterns(patterns, events, 1)[0]
        assert scored.unrecovered_tail_count == 0
