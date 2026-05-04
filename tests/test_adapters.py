"""Tests for the trace-format adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from triage.adapters import (
    ADAPTERS,
    NDJSONAdapter,
    OTelAdapter,
    TraceAdapter,
    adapter_for_path,
)
from triage.loader import load_files

# ---------------------------------------------------------------------------
# Registry / dispatch
# ---------------------------------------------------------------------------


def test_registry_includes_builtin_adapters():
    assert "ndjson" in ADAPTERS
    assert "otel" in ADAPTERS


def test_adapters_implement_protocol():
    for adapter in ADAPTERS.values():
        assert isinstance(adapter, TraceAdapter)


def test_adapter_for_path_dispatches_by_extension(tmp_path: Path):
    nd = tmp_path / "x.ndjson"
    nd.write_text("")
    assert adapter_for_path(nd).name == "ndjson"

    jl = tmp_path / "x.jsonl"
    jl.write_text("")
    assert adapter_for_path(jl).name == "ndjson"

    js = tmp_path / "x.json"
    js.write_text("[]")
    assert adapter_for_path(js).name == "otel"


def test_adapter_for_path_override_wins(tmp_path: Path):
    nd = tmp_path / "x.ndjson"
    nd.write_text("")
    assert adapter_for_path(nd, override="otel").name == "otel"


def test_adapter_for_path_invalid_override(tmp_path: Path):
    nd = tmp_path / "x.ndjson"
    nd.write_text("")
    with pytest.raises(ValueError):
        adapter_for_path(nd, override="not-a-real-format")


def test_adapter_for_path_unknown_extension_falls_back_to_ndjson(tmp_path: Path):
    nd = tmp_path / "x.unknown"
    nd.write_text("")
    assert adapter_for_path(nd).name == "ndjson"


# ---------------------------------------------------------------------------
# NDJSON adapter
# ---------------------------------------------------------------------------


MINIMAL_EVENT = {
    "event_id": "e1",
    "run_id": "r1",
    "turn": 0,
    "agent_id": "A",
    "latency_ms": {"llm": 100, "tool": 0, "total": 100},
    "action_taken": {"tool_name": "move", "tool_input": {}},
    "action_succeeded": False,
    "divergence_fields": [],
    "divergence_age": {},
    "failure_classification": "agent_error",
    "message_context": {},
}


def test_ndjson_adapter_parses_valid_events(tmp_path: Path):
    p = tmp_path / "trace.ndjson"
    p.write_text(json.dumps(MINIMAL_EVENT) + "\n")
    events, errors = NDJSONAdapter().load(p)
    assert errors == []
    assert len(events) == 1
    assert events[0].event_id == "e1"


def test_ndjson_adapter_skips_blank_lines(tmp_path: Path):
    p = tmp_path / "trace.ndjson"
    p.write_text(f"\n{json.dumps(MINIMAL_EVENT)}\n\n")
    events, errors = NDJSONAdapter().load(p)
    assert errors == []
    assert len(events) == 1


def test_ndjson_adapter_records_parse_errors(tmp_path: Path):
    p = tmp_path / "trace.ndjson"
    p.write_text("not-json\n" + json.dumps(MINIMAL_EVENT) + "\n")
    events, errors = NDJSONAdapter().load(p)
    assert len(events) == 1
    assert len(errors) == 1
    assert "trace.ndjson" in errors[0]


# ---------------------------------------------------------------------------
# OTel adapter
# ---------------------------------------------------------------------------


def _otel_span(
    *,
    span_id="s1",
    trace_id="t1",
    name="dispatch_subtask",
    status_code="ERROR",
    agent_id="planner",
    classification="coordination_failure",
    turn=0,
    divergence_fields=None,
):
    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "name": name,
        "status": {"code": status_code},
        "attributes": {
            "agent.id": agent_id,
            "turn": turn,
            "failure_classification": classification,
            "divergence_fields": divergence_fields or [],
        },
    }


def test_otel_adapter_parses_top_level_list(tmp_path: Path):
    p = tmp_path / "spans.json"
    p.write_text(json.dumps([_otel_span()]))
    events, errors = OTelAdapter().load(p)
    assert errors == []
    assert len(events) == 1
    ev = events[0]
    assert ev.event_id == "s1"
    assert ev.run_id == "t1"
    assert ev.action_taken.tool_name == "dispatch_subtask"
    assert ev.action_succeeded is False
    assert ev.failure_classification == "coordination_failure"
    assert ev.agent_id == "planner"


def test_otel_adapter_parses_spans_object(tmp_path: Path):
    p = tmp_path / "spans.json"
    p.write_text(json.dumps({"spans": [_otel_span()]}))
    events, errors = OTelAdapter().load(p)
    assert errors == []
    assert len(events) == 1


def test_otel_adapter_status_ok_maps_to_succeeded(tmp_path: Path):
    p = tmp_path / "spans.json"
    p.write_text(
        json.dumps([_otel_span(status_code="OK", classification=None)])
    )
    events, _ = OTelAdapter().load(p)
    assert events[0].action_succeeded is True


def test_otel_adapter_invalid_json_emits_error(tmp_path: Path):
    p = tmp_path / "spans.json"
    p.write_text("not-json")
    events, errors = OTelAdapter().load(p)
    assert events == []
    assert len(errors) == 1


def test_otel_adapter_unexpected_shape_emits_error(tmp_path: Path):
    p = tmp_path / "spans.json"
    p.write_text(json.dumps({"foo": "bar"}))
    events, errors = OTelAdapter().load(p)
    # spans defaults to [], so 0 events and no errors. But if we pass a
    # non-list under "spans", we get an error.
    assert events == []
    p.write_text(json.dumps({"spans": "not-a-list"}))
    events, errors = OTelAdapter().load(p)
    assert events == []
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# load_files dispatching
# ---------------------------------------------------------------------------


def test_load_files_dispatches_by_extension(tmp_path: Path):
    nd = tmp_path / "trace.ndjson"
    nd.write_text(json.dumps(MINIMAL_EVENT) + "\n")
    js = tmp_path / "spans.json"
    js.write_text(json.dumps([_otel_span()]))

    result = load_files([nd, js])
    assert len(result.events) == 2
    assert result.parse_errors == []


def test_load_files_format_override(tmp_path: Path):
    """A non-standard extension should still be parsed when format
    is overridden explicitly."""
    weird = tmp_path / "spans.weird"
    weird.write_text(json.dumps([_otel_span()]))
    # Without override, falls back to NDJSON adapter and fails to parse
    # the JSON-array file as line-delimited objects.
    fallback = load_files([weird])
    assert fallback.events == []

    # With override, OTel adapter parses successfully.
    result = load_files([weird], format="otel")
    assert len(result.events) == 1
    assert result.parse_errors == []
