"""Tests for OTLP span loading."""

from __future__ import annotations

import json
from pathlib import Path

from triage.loader import load_files

OTLP_PAYLOAD = {
    "resourceSpans": [
        {
            "resource": {},
            "scopeSpans": [
                {
                    "scope": {},
                    "spans": [
                        {
                            "traceId": "t123",
                            "spanId": "s456",
                            "name": "demo-span",
                            "attributes": [
                                {"key": "turn", "value": {"intValue": "5"}},
                                {"key": "agent_id", "value": {"stringValue": "AgentA"}},
                                {
                                    "key": "action_taken",
                                    "value": {
                                        "stringValue": '{"tool_name": "move", "tool_input": {"dir": "north"}}'
                                    },
                                },
                                {"key": "action_succeeded", "value": {"boolValue": False}},
                                {"key": "failure_classification", "value": {"stringValue": "agent_error"}},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}

def test_load_otlp_json(tmp_path: Path):
    p = tmp_path / "spans.jsonl"
    p.write_text(json.dumps(OTLP_PAYLOAD) + "\n")
    
    result = load_files([p])
    assert not result.parse_errors
    assert len(result.events) == 1
    
    ev = result.events[0]
    assert ev.run_id == "t123"
    assert ev.event_id == "s456"
    assert ev.turn == 5
    assert ev.agent_id == "AgentA"
    assert ev.action_taken.tool_name == "move"
    assert not ev.action_succeeded
    assert ev.failure_classification == "agent_error"
