"""adapters.py - pluggable trace-format adapters.

Each adapter parses a single file in a specific format and yields
TraceEvent objects. The CLI dispatches by either an explicit --format
flag or by file-extension auto-detection. New formats register
themselves in ADAPTERS so a downstream user can support a new trace
schema without modifying the CLI or the loader.

The TraceAdapter protocol defines exactly what an integration needs to
provide: a name, the extensions it claims, and a `load` method. Keeping
the contract this small is intentional — adapters should not own
ranking, scoring, or rendering logic.

Built-in adapters:

- NDJSONAdapter: the original schema, one JSON object per line, each
  validating against TraceEvent.
- OTelAdapter: a minimal OpenTelemetry-spans adapter that maps OTLP
  fields onto TraceEvent. Demonstrates the extension shape; not a
  full OTLP implementation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from triage.loader import ActionTaken, Latency, TraceEvent, Usage


@runtime_checkable
class TraceAdapter(Protocol):
    """Plugin contract for parsing a trace file into TraceEvents."""

    name: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]

    def load(self, path: Path) -> tuple[list[TraceEvent], list[str]]:
        """Read `path` and return (events, parse_errors).

        parse_errors is a list of human-readable strings; the loader
        does not raise on a malformed line so that a single bad event
        does not abort an otherwise-complete batch.
        """
        ...


class NDJSONAdapter:
    """One TraceEvent per line; the original schema."""

    name: ClassVar[str] = "ndjson"
    extensions: ClassVar[tuple[str, ...]] = (".ndjson", ".jsonl")

    def load(self, path: Path) -> tuple[list[TraceEvent], list[str]]:
        events: list[TraceEvent] = []
        errors: list[str] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: cannot read file - {exc}")
            return events, errors

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(TraceEvent.model_validate(data))
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{path}:{lineno}: {exc}")
        return events, errors


class OTelAdapter:
    """Minimal OpenTelemetry-spans -> TraceEvent adapter.

    Reads a JSON file containing either a list of spans or an object
    with a top-level `spans` key. Each span is mapped as follows:

        OTLP field                              -> TraceEvent field
        --------------------------------------- -> -----------------
        span.span_id                            -> event_id
        span.trace_id                           -> run_id
        span.attributes["agent.id"]             -> agent_id
        span.attributes["turn"]                 -> turn (default 0)
        span.name                               -> action_taken.tool_name
        span.status.code in {OK, UNSET}         -> action_succeeded
        span.attributes["failure_classification"]-> failure_classification
        span.attributes["divergence_fields"]    -> divergence_fields

    This is deliberately a starting shape, not a full OTLP spec
    implementation. The point is to show that adding a new format does
    not require touching the scorer, grouper, comparer, or reporter.
    """

    name: ClassVar[str] = "otel"
    extensions: ClassVar[tuple[str, ...]] = (".json",)

    _SUCCESS_STATUS_CODES = {"OK", "UNSET", 0, 1}

    def load(self, path: Path) -> tuple[list[TraceEvent], list[str]]:
        events: list[TraceEvent] = []
        errors: list[str] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{path}: cannot read file - {exc}")
            return events, errors

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}: {exc}")
            return events, errors

        spans = data if isinstance(data, list) else data.get("spans", [])
        if not isinstance(spans, list):
            errors.append(f"{path}: expected a list of spans or {{'spans': [...]}}")
            return events, errors

        for i, span in enumerate(spans):
            try:
                events.append(self._span_to_event(span))
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(f"{path}[{i}]: {exc}")
        return events, errors

    def _span_to_event(self, span: dict[str, Any]) -> TraceEvent:
        attrs = span.get("attributes", {}) or {}
        status = span.get("status", {}) or {}
        status_code = status.get("code", "UNSET")

        return TraceEvent(
            event_id=str(span.get("span_id") or span.get("spanId") or ""),
            run_id=str(span.get("trace_id") or span.get("traceId") or ""),
            turn=int(attrs.get("turn", 0)),
            agent_id=str(attrs.get("agent.id") or attrs.get("agent_id") or ""),
            latency_ms=Latency(),
            action_taken=ActionTaken(
                tool_name=str(span.get("name", "")),
                tool_input=attrs.get("tool_input", {}) or {},
            ),
            action_succeeded=status_code in self._SUCCESS_STATUS_CODES,
            divergence_fields=list(attrs.get("divergence_fields", []) or []),
            divergence_age=dict(attrs.get("divergence_age", {}) or {}),
            failure_classification=attrs.get("failure_classification"),
            usage=Usage(),
            message_context={},
        )


ADAPTERS: dict[str, TraceAdapter] = {
    NDJSONAdapter.name: NDJSONAdapter(),
    OTelAdapter.name: OTelAdapter(),
}


def adapter_for_path(path: Path, override: str | None = None) -> TraceAdapter:
    """Resolve which adapter should handle `path`.

    `override` wins if given. Otherwise, the file extension is matched
    against each registered adapter's `extensions` tuple. Falls back to
    the NDJSON adapter so an unknown extension still produces a
    parse-error report rather than a hard failure.
    """
    if override is not None:
        if override not in ADAPTERS:
            known = ", ".join(sorted(ADAPTERS))
            raise ValueError(
                f"Unknown trace format '{override}'. Known formats: {known}"
            )
        return ADAPTERS[override]

    suffix = path.suffix.lower()
    for adapter in ADAPTERS.values():
        if suffix in adapter.extensions:
            return adapter
    return ADAPTERS[NDJSONAdapter.name]


__all__ = [
    "ADAPTERS",
    "NDJSONAdapter",
    "OTelAdapter",
    "TraceAdapter",
    "adapter_for_path",
]
