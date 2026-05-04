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
from statistics import median

from triage.grouper import IncidentPattern, group_events
from triage.loader import TraceEvent
from triage.scorer import (
    CONFIDENCE_THRESHOLD,
    RECOVERY_WINDOW,
    ScoredPattern,
    _build_agent_timeline,
    score_patterns,
)


@dataclass
class ClassificationDelta:
    """Frequency, unrecovered-count, and median-latency change for one
    failure classification.
    """

    classification: str
    before_frequency: int
    after_frequency: int
    before_unrecovered: int
    after_unrecovered: int
    before_median_latency: float | None
    after_median_latency: float | None

    @property
    def frequency_change(self) -> str:
        return _pct_change(
            self.before_frequency,
            self.after_frequency,
            tentative=self.is_tentative,
        )

    @property
    def unrecovered_change(self) -> str:
        return _pct_change(
            self.before_unrecovered,
            self.after_unrecovered,
            tentative=self.is_tentative,
        )

    @property
    def latency_change(self) -> str:
        return _format_latency_change(
            self.before_median_latency, self.after_median_latency
        )

    @property
    def is_tentative(self) -> bool:
        """True when either side has too few occurrences for the change
        label to be statistically meaningful.
        """
        max_side = max(self.before_frequency, self.after_frequency)
        return max_side < CONFIDENCE_THRESHOLD


@dataclass
class ScoreSummary:
    """Aggregate scoring metrics for a single batch.

    Lets a comparison report show an at-a-glance "did things get
    better overall" panel before drilling into per-classification
    deltas. All fields are computed on the scored patterns the batch
    produced, so the same numbers are visible to a reader who runs
    `triage report` on either side independently.
    """

    pattern_count: int
    failure_event_count: int
    unrecovered_event_count: int
    top_final_score: float
    mean_final_score: float
    coordination_failure_count: int


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
    before_summary: ScoreSummary | None = None
    after_summary: ScoreSummary | None = None


def _pct_change(before: int, after: int, tentative: bool = False) -> str:
    """Render a before/after pair as 'down N%', 'up N%', 'stable', or
    'new'/'resolved' for from-zero / to-zero transitions.

    When `tentative` is True and the change is directional, append
    "(tentative)" so a reader can tell at a glance that the delta is
    based on a small sample. Avoids arrow characters so the output
    renders identically in any terminal.
    """
    if before == 0 and after == 0:
        return "stable"
    if before == 0:
        label = "new"
    elif after == 0:
        label = "resolved"
    elif before == after:
        return "stable"
    else:
        pct = round((after - before) / before * 100)
        if pct == 0:
            return "stable"
        label = f"up {pct}%" if pct > 0 else f"down {abs(pct)}%"
    if tentative and label not in ("stable",):
        return f"{label} (tentative)"
    return label


def _format_latency_change(
    before: float | None, after: float | None
) -> str:
    """Render the change in median recovery latency between two batches."""
    if before is None and after is None:
        return "n/a"
    if before is None:
        return f"new ({after:g} turn{'s' if after != 1 else ''})"
    if after is None:
        return f"no recoveries (was {before:g} turn{'s' if before != 1 else ''})"
    delta = after - before
    if abs(delta) < 0.05:
        return f"stable ({after:g} turn{'s' if after != 1 else ''})"
    sign = "+" if delta > 0 else "-"
    return (
        f"{sign}{abs(delta):.1f} turns ({before:g} -> {after:g})"
    )


def _classification_median_latencies(
    events: list[TraceEvent],
) -> dict[str, float | None]:
    """Median turns-to-first-success per classification, across recovered
    failures within RECOVERY_WINDOW.
    """
    timeline = _build_agent_timeline(events)
    by_class: dict[str, list[int]] = defaultdict(list)
    for ev in events:
        if ev.action_succeeded or ev.failure_classification is None:
            continue
        agent_turns = timeline.get((ev.run_id, ev.agent_id), [])
        for turn, succeeded in agent_turns:
            if turn <= ev.turn or not succeeded:
                continue
            offset = turn - ev.turn
            if offset <= RECOVERY_WINDOW:
                by_class[ev.failure_classification].append(offset)
            break
    return {
        cls: float(median(latencies)) if latencies else None
        for cls, latencies in by_class.items()
    }


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


def _summarize(scored: list[ScoredPattern]) -> ScoreSummary:
    """Aggregate ScoredPatterns into a one-line health signature."""
    if not scored:
        return ScoreSummary(
            pattern_count=0,
            failure_event_count=0,
            unrecovered_event_count=0,
            top_final_score=0.0,
            mean_final_score=0.0,
            coordination_failure_count=0,
        )

    failure_total = sum(sp.pattern.frequency for sp in scored)
    unrecovered_total = sum(
        round(sp.pattern.frequency * (1 - sp.recovery_rate)) for sp in scored
    )
    final_scores = [sp.final_score for sp in scored]
    coord_total = sum(
        sp.pattern.frequency
        for sp in scored
        if sp.pattern.failure_classification == "coordination_failure"
    )
    return ScoreSummary(
        pattern_count=len(scored),
        failure_event_count=failure_total,
        unrecovered_event_count=unrecovered_total,
        top_final_score=max(final_scores),
        mean_final_score=sum(final_scores) / len(final_scores),
        coordination_failure_count=coord_total,
    )


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
    before_lat = _classification_median_latencies(before_events)
    after_lat = _classification_median_latencies(after_events)

    classifications = sorted(set(before_class) | set(after_class))
    deltas = [
        ClassificationDelta(
            classification=c,
            before_frequency=before_class.get(c, 0),
            after_frequency=after_class.get(c, 0),
            before_unrecovered=before_unrec.get(c, 0),
            after_unrecovered=after_unrec.get(c, 0),
            before_median_latency=before_lat.get(c),
            after_median_latency=after_lat.get(c),
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
        before_summary=_summarize(before_scored),
        after_summary=_summarize(after_scored),
    )


__all__ = [
    "ClassificationDelta",
    "ComparisonReport",
    "compare_event_batches",
    "RECOVERY_WINDOW",
]
