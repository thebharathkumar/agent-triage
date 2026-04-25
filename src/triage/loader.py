"""loader.py - parse trace files into validated event objects.

Parsing dispatch lives in `triage.adapters`. This module owns the
TraceEvent schema and the LoadResult aggregator, but the actual
file-format parsing is plugin-shaped so that new trace schemas can be
added without touching the loader.
"""

from __future__ import annotations

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
        return str(v)


class LoadResult(BaseModel):
    events: list[TraceEvent]
    source_files: list[str]
    parse_errors: list[str]


def load_files(
    paths: list[Path], format: str | None = None
) -> LoadResult:
    """Read one or more trace files and return all validated TraceEvents.

    The format is resolved per-path via `adapter_for_path`, so a single
    invocation can mix NDJSON and OTel files. Pass `format` to override
    the auto-detection for every path in the call (useful when files
    have non-standard extensions).
    """
    # Imported here to avoid a circular import: adapters imports
    # TraceEvent from this module.
    from triage.adapters import adapter_for_path

    events: list[TraceEvent] = []
    source_files: list[str] = []
    parse_errors: list[str] = []

    for path in paths:
        source_files.append(str(path))
        adapter = adapter_for_path(path, override=format)
        adapter_events, adapter_errors = adapter.load(path)
        events.extend(adapter_events)
        parse_errors.extend(adapter_errors)

    return LoadResult(
        events=events,
        source_files=source_files,
        parse_errors=parse_errors,
    )
