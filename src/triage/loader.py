"""loader.py - parse NDJSON trace files into validated event objects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Latency(BaseModel):
    llm: int = 0
    tool: int = 0
    total: int = 0


class ActionTaken(BaseModel):
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class TraceEvent(BaseModel):
    event_id: str
    run_id: str
    turn: int
    agent_id: str
    latency_ms: Latency = Field(default_factory=Latency)
    action_taken: ActionTaken
    action_succeeded: bool
    divergence_fields: list[str] = Field(default_factory=list)
    divergence_age: dict[str, int] = Field(default_factory=dict)
    failure_classification: str | None = None
    usage: Usage = Field(default_factory=Usage)
    message_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("failure_classification", mode="before")
    @classmethod
    def coerce_null_string(cls, v: Any) -> str | None:
        if v in (None, "null", ""):
            return None
        return v


class LoadResult(BaseModel):
    events: list[TraceEvent]
    source_files: list[str]
    parse_errors: list[str]


def _extract_otlp_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract events from an OTLP resourceSpans payload."""
    events = []
    for rs in data.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                event_data: dict[str, Any] = {}
                for attr in span.get("attributes", []):
                    key = attr.get("key")
                    val_obj = attr.get("value", {})
                    
                    val: Any = None
                    if "stringValue" in val_obj:
                        val = val_obj["stringValue"]
                        if isinstance(val, str) and (val.startswith("{") or val.startswith("[")):
                            try:
                                val = json.loads(val)
                            except json.JSONDecodeError:
                                pass
                    elif "intValue" in val_obj:
                        val = int(val_obj["intValue"])
                    elif "boolValue" in val_obj:
                        val = bool(val_obj["boolValue"])
                    elif "doubleValue" in val_obj:
                        val = float(val_obj["doubleValue"])
                    elif "arrayValue" in val_obj:
                        values = val_obj["arrayValue"].get("values", [])
                        val = [v.get("stringValue", v.get("intValue")) for v in values]
                        
                    if key:
                        event_data[key] = val
                        
                if "event_id" not in event_data:
                    event_data["event_id"] = span.get("spanId", "unknown")
                if "run_id" not in event_data:
                    event_data["run_id"] = span.get("traceId", "unknown")
                    
                events.append(event_data)
    return events


def load_files(paths: list[Path]) -> LoadResult:
    """Read one or more NDJSON files and return all validated TraceEvents."""
    events: list[TraceEvent] = []
    source_files: list[str] = []
    parse_errors: list[str] = []

    for path in paths:
        source_files.append(str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            parse_errors.append(f"{path}: cannot read file - {exc}")
            continue

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "resourceSpans" in data:
                    otlp_events = _extract_otlp_events(data)
                    for ev_data in otlp_events:
                        try:
                            event = TraceEvent.model_validate(ev_data)
                            events.append(event)
                        except Exception as exc:
                            parse_errors.append(f"{path}:{lineno}: {exc}")
                else:
                    event = TraceEvent.model_validate(data)
                    events.append(event)
            except (json.JSONDecodeError, Exception) as exc:
                parse_errors.append(f"{path}:{lineno}: {exc}")

    return LoadResult(
        events=events,
        source_files=source_files,
        parse_errors=parse_errors,
    )
