"""Tests for triage.grouper."""

from __future__ import annotations

from tests.conftest import make_event
from triage.grouper import group_events


class TestGroupEvents:
    def test_no_events_returns_empty(self):
        assert group_events([]) == []

    def test_successful_events_are_excluded(self):
        events = [
            make_event(event_id="e1", action_succeeded=True, failure_classification=None),
            make_event(event_id="e2", action_succeeded=True, failure_classification=None),
        ]
        patterns = group_events(events)
        assert len(patterns) == 0

    def test_failed_event_creates_one_pattern(self):
        events = [
            make_event(
                event_id="e1",
                action_succeeded=False,
                failure_classification="agent_error",
            )
        ]
        patterns = group_events(events)
        assert len(patterns) == 1
        assert patterns[0].frequency == 1

    def test_same_signature_merged_into_one_pattern(self):
        events = [
            make_event(
                event_id=f"e{i}",
                turn=i,
                action_succeeded=False,
                failure_classification="agent_error",
            )
            for i in range(5)
        ]
        patterns = group_events(events)
        assert len(patterns) == 1
        assert patterns[0].frequency == 5

    def test_different_agents_create_separate_patterns(self):
        events = [
            make_event(
                event_id="e1",
                agent_id="A",
                action_succeeded=False,
                failure_classification="agent_error",
            ),
            make_event(
                event_id="e2",
                agent_id="B",
                action_succeeded=False,
                failure_classification="agent_error",
            ),
        ]
        patterns = group_events(events)
        assert len(patterns) == 2
        agent_ids = {p.agent_id for p in patterns}
        assert agent_ids == {"A", "B"}

    def test_different_classifications_create_separate_patterns(self):
        events = [
            make_event(
                event_id="e1",
                action_succeeded=False,
                failure_classification="agent_error",
            ),
            make_event(
                event_id="e2",
                action_succeeded=False,
                failure_classification="coordination_failure",
            ),
        ]
        patterns = group_events(events)
        assert len(patterns) == 2

    def test_different_divergence_fields_create_separate_patterns(self):
        events = [
            make_event(
                event_id="e1",
                action_succeeded=False,
                failure_classification="information_lag",
                divergence_fields=["key_position"],
            ),
            make_event(
                event_id="e2",
                action_succeeded=False,
                failure_classification="information_lag",
                divergence_fields=["other_agent_position"],
            ),
        ]
        patterns = group_events(events)
        assert len(patterns) == 2

    def test_null_classification_event_with_action_failed_is_included(self):
        events = [
            make_event(
                event_id="e1",
                action_succeeded=False,
                failure_classification=None,
            )
        ]
        patterns = group_events(events)
        assert len(patterns) == 1
        assert patterns[0].failure_classification == "unclassified"

    def test_display_name_includes_agent_tool_classification(self):
        events = [
            make_event(
                event_id="e1",
                agent_id="A",
                tool_name="move",
                action_succeeded=False,
                failure_classification="agent_error",
            )
        ]
        patterns = group_events(events)
        name = patterns[0].display_name()
        assert "A" in name
        assert "move" in name
        assert "agent_error" in name

    def test_run_ids_tracks_multiple_runs(self):
        events = [
            make_event(
                event_id="e1",
                run_id="run-1",
                action_succeeded=False,
                failure_classification="agent_error",
            ),
            make_event(
                event_id="e2",
                run_id="run-2",
                action_succeeded=False,
                failure_classification="agent_error",
            ),
        ]
        patterns = group_events(events)
        assert len(patterns) == 1
        assert patterns[0].run_ids == {"run-1", "run-2"}

    def test_event_with_classification_but_success_is_included(self):
        # A succeeded event tagged with a classification still counts as incident
        events = [
            make_event(
                event_id="e1",
                action_succeeded=True,
                failure_classification="information_lag",
            )
        ]
        patterns = group_events(events)
        assert len(patterns) == 1
