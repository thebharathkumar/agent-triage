"""comparer.py - diff two batches of trace events.

Compare answers a different question than the per-batch ranker. Instead
of "what is worth your attention this morning", it answers "did the
change between these two runs make things better or worse, and by how
much". The output is two-axis:

- per-classification frequency and unrecovered-count deltas
- pattern-level set diff: what is new, what was resolved, what persists

A pattern's identity here is the same key the grouper uses
(agent / tool / classification / divergence-fields), so a coordination
failure on `dispatch_subtask` between agent A and agent B is the same
pattern across runs even if the surrounding event volume is different.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from triage.grouper import IncidentPattern, group_events
from triage.loader import TraceEvent
from triage.scorer import RECOVERY_WINDOW, ScoredPattern, score_patterns


@dataclass
class ClassificationDelta:
    """Frequency and unrecovered-count change for a single classification."""

    classification: str
    before_frequency: int
    after_frequency: int
    before_unrecovered: int
    after_unrecovered: int

    @property
    def frequency_change(self) -> str:
        return _pct_change(self.before_frequency, self.after_frequency)

    @property
    def unrecovered_change(self) -> str:
        return _pct_change(self.before_unrecovered, self.after_unrecovered)


@dataclass
class ComparisonReport:
    before_run_count: int
    after_run_count: int
    before_event_count: int
    after_event_count: int
    deltas: list[ClassificationDelta] = field(default_factory=list)
    new_patterns: list[ScoredPattern] = field(default_factory=list)
    resolved_patterns: list[ScoredPattern] = field(default_factory=list)
    persisting_patterns: list[tuple[ScoredPattern, ScoredPattern]] = field(
        default_factory=list
    )


def _pct_change(before: int, after: int) -> str:
    """Render a before/after pair as 'down N%', 'up N%', 'stable', or
    'new'/'resolved' for from-zero / to-zero transitions.

    Avoids arrow characters so the output renders identically in any
    terminal and matches the report's all-ASCII convention.
    """
    if before == 0 and after == 0:
        return "stable"
    if before == 0:
        return "new"
    if after == 0:
        return "resolved"
    if before == after:
        return "stable"
    pct = round((after - before) / before * 100)
    if pct == 0:
        return "stable"
    if pct > 0:
        return f"up {pct}%"
    return f"down {abs(pct)}%"


def _count_unrecovered(scored: list[ScoredPattern]) -> dict[str, int]:
    """Map classification -> count of failures unrecovered within window."""
    out: dict[str, int] = defaultdict(int)
    for sp in scored:
        unrecovered = round(sp.pattern.frequency * (1 - sp.recovery_rate))
        out[sp.pattern.failure_classification] += unrecovered
    return out


def _count_classification_frequency(
    patterns: list[IncidentPattern],
) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for p in patterns:
        out[p.failure_classification] += p.frequency
    return out


def _index_by_pattern_id(scored: list[ScoredPattern]) -> dict[str, ScoredPattern]:
    return {sp.pattern.pattern_id: sp for sp in scored}


def compare_event_batches(
    before_events: list[TraceEvent],
    after_events: list[TraceEvent],
) -> ComparisonReport:
    """Build a ComparisonReport from two batches of events.

    Each side is grouped and scored independently, then matched on the
    shared pattern_id space.
    """
    before_patterns = group_events(before_events)
    after_patterns = group_events(after_events)

    before_runs = {ev.run_id for ev in before_events}
    after_runs = {ev.run_id for ev in after_events}

    before_scored = score_patterns(before_patterns, before_events, len(before_runs))
    after_scored = score_patterns(after_patterns, after_events, len(after_runs))

    before_class = _count_classification_frequency(before_patterns)
    after_class = _count_classification_frequency(after_patterns)
    before_unrec = _count_unrecovered(before_scored)
    after_unrec = _count_unrecovered(after_scored)

    classifications = sorted(set(before_class) | set(after_class))
    deltas = [
        ClassificationDelta(
            classification=c,
            before_frequency=before_class.get(c, 0),
            after_frequency=after_class.get(c, 0),
            before_unrecovered=before_unrec.get(c, 0),
            after_unrecovered=after_unrec.get(c, 0),
        )
        for c in classifications
    ]

    before_idx = _index_by_pattern_id(before_scored)
    after_idx = _index_by_pattern_id(after_scored)
    before_keys = set(before_idx)
    after_keys = set(after_idx)

    new_patterns = sorted(
        (after_idx[k] for k in after_keys - before_keys),
        key=lambda sp: sp.pattern.frequency,
        reverse=True,
    )
    resolved_patterns = sorted(
        (before_idx[k] for k in before_keys - after_keys),
        key=lambda sp: sp.pattern.frequency,
        reverse=True,
    )
    persisting = sorted(
        ((before_idx[k], after_idx[k]) for k in before_keys & after_keys),
        key=lambda pair: pair[1].pattern.frequency,
        reverse=True,
    )

    return ComparisonReport(
        before_run_count=len(before_runs),
        after_run_count=len(after_runs),
        before_event_count=len(before_events),
        after_event_count=len(after_events),
        deltas=deltas,
        new_patterns=new_patterns,
        resolved_patterns=resolved_patterns,
        persisting_patterns=persisting,
    )


__all__ = [
    "ClassificationDelta",
    "ComparisonReport",
    "compare_event_batches",
    "RECOVERY_WINDOW",
]
