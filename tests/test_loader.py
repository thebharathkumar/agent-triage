"""Tests for triage.loader."""

from __future__ import annotations

import json
from pathlib import Path

from triage.loader import load_files


def _write_ndjson(tmp_path: Path, lines: list[dict]) -> Path:
    p = tmp_path / "test.ndjson"
    p.write_text("\n".join(json.dumps(row) for row in lines))
    return p


MINIMAL_EVENT: dict = {
    "event_id": "abc-123",
    "run_id": "seed42",
    "turn": 0,
    "agent_id": "A",
    "timestamp": "2026-04-09T19:00:00+00:00",
    "latency_ms": {"llm": 100, "tool": 0, "total": 100},
    "action_taken": {"tool_name": "move", "tool_input": {"direction": "north"}},
    "action_result": {"ok": True},
    "action_succeeded": True,
    "agent_belief_state": {},
    "world_truth_state": {},
    "belief_accuracy": {},
    "divergence_fields": [],
    "divergence_age": {},
    "divergences": [],
    "message_context": {},
    "failure_classification": None,
    "reasoning": "moving north",
    "usage": {"input_tokens": 100, "output_tokens": 50},
}


class TestLoadFiles:
    def test_empty_file_list_returns_empty(self, tmp_path: Path):
        result = load_files([])
        assert result.events == []
        assert result.parse_errors == []

    def test_single_valid_event_parsed(self, tmp_path: Path):
        p = _write_ndjson(tmp_path, [MINIMAL_EVENT])
        result = load_files([p])
        assert len(result.events) == 1
        assert result.events[0].event_id == "abc-123"

    def test_multiple_events_in_one_file(self, tmp_path: Path):
        events = [dict(MINIMAL_EVENT, event_id=f"evt-{i}") for i in range(5)]
        p = _write_ndjson(tmp_path, events)
        result = load_files([p])
        assert len(result.events) == 5

    def test_null_failure_classification_parsed_as_none(self, tmp_path: Path):
        event = dict(MINIMAL_EVENT, failure_classification=None)
        p = _write_ndjson(tmp_path, [event])
        result = load_files([p])
        assert result.events[0].failure_classification is None

    def test_string_failure_classification_preserved(self, tmp_path: Path):
        event = dict(MINIMAL_EVENT, failure_classification="agent_error")
        p = _write_ndjson(tmp_path, [event])
        result = load_files([p])
        assert result.events[0].failure_classification == "agent_error"

    def test_malformed_json_line_recorded_as_error(self, tmp_path: Path):
        p = tmp_path / "bad.ndjson"
        p.write_text('{"event_id": "ok"}\nnot valid json\n')
        result = load_files([p])
        # Should parse first line successfully (or fail on validation) and record error
        assert len(result.parse_errors) >= 1

    def test_blank_lines_skipped_silently(self, tmp_path: Path):
        p = tmp_path / "blanks.ndjson"
        p.write_text(
            "\n\n"
            + json.dumps(MINIMAL_EVENT)
            + "\n\n"
            + json.dumps(dict(MINIMAL_EVENT, event_id="evt-2"))
            + "\n"
        )
        result = load_files([p])
        assert len(result.events) == 2
        assert result.parse_errors == []

    def test_multiple_files_combined(self, tmp_path: Path):
        p1 = tmp_path / "first.ndjson"
        p1.write_text(json.dumps(dict(MINIMAL_EVENT, event_id="e1", run_id="r1")))
        p2 = tmp_path / "second.ndjson"
        p2.write_text(json.dumps(dict(MINIMAL_EVENT, event_id="e2", run_id="r2")))
        result = load_files([p1, p2])
        assert len(result.events) == 2
        assert {e.run_id for e in result.events} == {"r1", "r2"}

    def test_source_files_tracked(self, tmp_path: Path):
        p = _write_ndjson(tmp_path, [MINIMAL_EVENT])
        result = load_files([p])
        assert str(p) in result.source_files
