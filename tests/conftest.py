"""Shared test fixtures and sample data."""

from __future__ import annotations

import pytest

from triage.loader import ActionTaken, Latency, TraceEvent, Usage


def make_event(
    *,
    event_id: str = "evt-1",
    run_id: str = "run-test",
    turn: int = 0,
    agent_id: str = "A",
    tool_name: str = "move",
    action_succeeded: bool = True,
    failure_classification: str | None = None,
    divergence_fields: list[str] | None = None,
) -> TraceEvent:
    """Minimal TraceEvent factory for tests."""
    return TraceEvent(
        event_id=event_id,
        run_id=run_id,
        turn=turn,
        agent_id=agent_id,
        latency_ms=Latency(llm=100, tool=0, total=100),
        action_taken=ActionTaken(tool_name=tool_name, tool_input={}),
        action_succeeded=action_succeeded,
        divergence_fields=divergence_fields or [],
        divergence_age={},
        failure_classification=failure_classification,
        usage=Usage(input_tokens=100, output_tokens=50),
        message_context={},
    )
