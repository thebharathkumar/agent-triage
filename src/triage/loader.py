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
        return str(v)


class LoadResult(BaseModel):
    events: list[TraceEvent]
    source_files: list[str]
    parse_errors: list[str]


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
                event = TraceEvent.model_validate(data)
                events.append(event)
            except (json.JSONDecodeError, ValueError) as exc:
                parse_errors.append(f"{path}:{lineno}: {exc}")

    return LoadResult(
        events=events,
        source_files=source_files,
        parse_errors=parse_errors,
    )
