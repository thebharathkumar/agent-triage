"""grouper.py - detect and cluster incident patterns from trace events."""

from __future__ import annotations

from dataclasses import dataclass, field

from triage.loader import TraceEvent


@dataclass
class IncidentPattern:
    """A group of events that share the same structural failure signature."""

    pattern_id: str
    agent_id: str
    tool_name: str
    failure_classification: str
    divergence_fields: frozenset[str]
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def frequency(self) -> int:
        return len(self.events)

    @property
    def run_ids(self) -> set[str]:
        return {e.run_id for e in self.events}

    def display_name(self) -> str:
        div_part = (
            "+".join(sorted(self.divergence_fields))
            if self.divergence_fields
            else "no-divergence"
        )
        return (
            f"[{self.agent_id}] {self.tool_name} / "
            f"{self.failure_classification} / {div_part}"
        )


def _make_key(event: TraceEvent) -> tuple[str, str, str, frozenset[str]]:
    classification = event.failure_classification or "unclassified"
    return (
        event.agent_id,
        event.action_taken.tool_name,
        classification,
        frozenset(event.divergence_fields),
    )


def group_events(events: list[TraceEvent]) -> list[IncidentPattern]:
    """Cluster failed events into incident patterns.

    Only events where action_succeeded is False OR failure_classification
    is not None are considered incidents. Events with both action_succeeded
    True and no classification are normal operations and are skipped.
    """
    Key = tuple[str, str, str, frozenset[str]]
    buckets: dict[Key, IncidentPattern] = {}
    ordered: list[Key] = []

    for event in events:
        is_failure = (not event.action_succeeded) or (
            event.failure_classification is not None
        )
        if not is_failure:
            continue

        key = _make_key(event)
        if key not in buckets:
            agent_id, tool_name, classification, div_fields = key
            pattern_id = f"{agent_id}-{tool_name}-{classification}"
            buckets[key] = IncidentPattern(
                pattern_id=pattern_id,
                agent_id=agent_id,
                tool_name=tool_name,
                failure_classification=classification,
                divergence_fields=div_fields,
            )
            ordered.append(key)

        buckets[key].events.append(event)

    return [buckets[k] for k in ordered]
