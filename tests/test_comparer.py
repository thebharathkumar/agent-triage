"""Tests for triage.comparer and the comparison report renderer."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from tests.conftest import make_event
from triage.cli import main
from triage.comparer import _pct_change, compare_event_batches
from triage.reporter import build_comparison_report


def _failures(
    *,
    classification: str,
    count: int,
    run_id: str = "r1",
    agent_id: str = "A",
    tool_name: str = "move",
    divergence_fields: list[str] | None = None,
):
    return [
        make_event(
            event_id=f"{run_id}-{classification}-{i}",
            run_id=run_id,
            turn=i * 2,
            agent_id=agent_id,
            tool_name=tool_name,
            action_succeeded=False,
            failure_classification=classification,
            divergence_fields=divergence_fields,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# _pct_change
# ---------------------------------------------------------------------------


class TestPctChange:
    def test_stable_when_unchanged(self):
        assert _pct_change(5, 5) == "stable"

    def test_stable_when_both_zero(self):
        assert _pct_change(0, 0) == "stable"

    def test_new_when_zero_to_nonzero(self):
        assert _pct_change(0, 3) == "new"

    def test_resolved_when_nonzero_to_zero(self):
        assert _pct_change(4, 0) == "resolved"

    def test_down_for_decrease(self):
        assert _pct_change(10, 5) == "down 50%"

    def test_up_for_increase(self):
        assert _pct_change(2, 3) == "up 50%"

    def test_rounds_pct_to_integer(self):
        # 11 -> 6 = -45.45...% -> "down 45%"
        assert _pct_change(11, 6) == "down 45%"


# ---------------------------------------------------------------------------
# compare_event_batches
# ---------------------------------------------------------------------------


class TestCompare:
    def test_identical_batches_have_no_new_or_resolved(self):
        events = _failures(classification="agent_error", count=3)
        report = compare_event_batches(events, list(events))
        assert report.new_patterns == []
        assert report.resolved_patterns == []
        assert len(report.persisting_patterns) == 1

    def test_new_pattern_only_in_after(self):
        before = _failures(classification="agent_error", count=2)
        after = before + _failures(
            classification="coordination_failure",
            count=4,
            agent_id="B",
            tool_name="dispatch",
            run_id="r2",
        )
        report = compare_event_batches(before, after)
        assert len(report.new_patterns) == 1
        assert (
            report.new_patterns[0].pattern.failure_classification
            == "coordination_failure"
        )

    def test_resolved_pattern_only_in_before(self):
        before = _failures(classification="information_lag", count=3)
        after: list = []
        report = compare_event_batches(before, after)
        assert len(report.resolved_patterns) == 1
        assert (
            report.resolved_patterns[0].pattern.failure_classification
            == "information_lag"
        )

    def test_classification_delta_is_computed(self):
        before = _failures(classification="agent_error", count=10)
        after = _failures(classification="agent_error", count=5)
        report = compare_event_batches(before, after)
        agent_error = next(
            d for d in report.deltas if d.classification == "agent_error"
        )
        assert agent_error.before_frequency == 10
        assert agent_error.after_frequency == 5
        assert agent_error.frequency_change == "down 50%"

    def test_unrecovered_count_decreases_when_recovery_added(self):
        # Use 5 failures (== CONFIDENCE_THRESHOLD) so the change label is
        # not flagged "tentative".
        before = _failures(classification="agent_error", count=5)
        failures = _failures(classification="agent_error", count=5)
        recoveries = [
            make_event(
                event_id=f"ok-{i}",
                run_id=ev.run_id,
                turn=ev.turn + 1,
                agent_id=ev.agent_id,
                tool_name=ev.action_taken.tool_name,
                action_succeeded=True,
                failure_classification=None,
            )
            for i, ev in enumerate(failures)
        ]
        after = failures + recoveries

        report = compare_event_batches(before, after)
        agent_error = next(
            d for d in report.deltas if d.classification == "agent_error"
        )
        assert agent_error.before_unrecovered == 5
        assert agent_error.after_unrecovered == 0
        assert agent_error.unrecovered_change == "resolved"

    def test_patterns_distinguished_by_divergence_fields(self):
        # Two patterns with identical (agent, tool, classification) but
        # different divergence_fields must NOT collide on pattern_id, or
        # compare-mode would mis-classify them as a single persisting
        # pattern.
        before = _failures(
            classification="coordination_failure",
            count=2,
            divergence_fields=["other_position"],
        )
        after = _failures(
            classification="coordination_failure",
            count=2,
            divergence_fields=["task_queue_head"],
        )
        report = compare_event_batches(before, after)
        assert len(report.new_patterns) == 1
        assert len(report.resolved_patterns) == 1
        assert (
            report.new_patterns[0].pattern.divergence_fields
            != report.resolved_patterns[0].pattern.divergence_fields
        )

    def test_event_and_run_counts(self):
        before = _failures(classification="agent_error", count=3, run_id="rA")
        after = _failures(
            classification="agent_error", count=2, run_id="rB"
        ) + _failures(
            classification="agent_error", count=2, run_id="rC"
        )
        report = compare_event_batches(before, after)
        assert report.before_event_count == 3
        assert report.after_event_count == 4
        assert report.before_run_count == 1
        assert report.after_run_count == 2


# ---------------------------------------------------------------------------
# build_comparison_report
# ---------------------------------------------------------------------------


class TestBuildComparisonReport:
    def test_report_contains_headers(self):
        before = _failures(classification="agent_error", count=2)
        after = _failures(classification="agent_error", count=1)
        comparison = compare_event_batches(before, after)
        text = build_comparison_report(
            comparison, before_path="b.ndjson", after_path="a.ndjson"
        )
        assert "# Triage Comparison Report" in text
        assert "Headline" in text
        assert "Classification deltas" in text
        assert "New patterns" in text
        assert "Resolved patterns" in text
        assert "Persisting patterns" in text

    def test_headline_lists_a_change(self):
        before = _failures(classification="agent_error", count=10)
        after = _failures(classification="agent_error", count=2)
        text = build_comparison_report(
            compare_event_batches(before, after),
            before_path="b.ndjson",
            after_path="a.ndjson",
        )
        assert "down" in text.lower()

    def test_new_pattern_appears_in_new_section(self):
        before: list = []
        after = _failures(classification="coordination_failure", count=3)
        text = build_comparison_report(
            compare_event_batches(before, after),
            before_path="b.ndjson",
            after_path="a.ndjson",
        )
        assert "coordination_failure" in text


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


MINIMAL_FAILURE: dict = {
    "event_id": "evt-cmp-1",
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


def _write_ndjson(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events))


def test_cli_compare_subcommand_runs(tmp_path: Path):
    before = tmp_path / "before.ndjson"
    after = tmp_path / "after.ndjson"
    _write_ndjson(before, [dict(MINIMAL_FAILURE, event_id="b1")])
    _write_ndjson(after, [
        dict(MINIMAL_FAILURE, event_id="a1"),
        dict(
            MINIMAL_FAILURE,
            event_id="a2",
            agent_id="B",
            failure_classification="coordination_failure",
        ),
    ])
    runner = CliRunner()
    result = runner.invoke(main, ["compare", str(before), str(after)])
    assert result.exit_code == 0, result.output
    assert "# Triage Comparison Report" in result.output
    assert "coordination_failure" in result.output


def test_cli_compare_writes_output_file(tmp_path: Path):
    before = tmp_path / "before.ndjson"
    after = tmp_path / "after.ndjson"
    out = tmp_path / "diff.md"
    _write_ndjson(before, [dict(MINIMAL_FAILURE, event_id="b1")])
    _write_ndjson(after, [dict(MINIMAL_FAILURE, event_id="a1")])
    runner = CliRunner()
    result = runner.invoke(
        main, ["compare", str(before), str(after), "--output", str(out)]
    )
    assert result.exit_code == 0
    assert out.exists()
    assert "Triage Comparison Report" in out.read_text()


def test_cli_compare_missing_arg_fails(tmp_path: Path):
    before = tmp_path / "before.ndjson"
    _write_ndjson(before, [dict(MINIMAL_FAILURE)])
    runner = CliRunner()
    result = runner.invoke(main, ["compare", str(before)])
    assert result.exit_code != 0


def test_cli_compare_accepts_directories(tmp_path: Path):
    before_dir = tmp_path / "before"
    before_dir.mkdir()
    after_dir = tmp_path / "after"
    after_dir.mkdir()
    _write_ndjson(before_dir / "a.ndjson", [dict(MINIMAL_FAILURE, run_id="b1")])
    _write_ndjson(before_dir / "b.ndjson", [dict(MINIMAL_FAILURE, run_id="b2", event_id="b-2")])
    _write_ndjson(after_dir / "a.ndjson", [dict(MINIMAL_FAILURE, run_id="a1", event_id="a-1")])
    runner = CliRunner()
    result = runner.invoke(main, ["compare", str(before_dir), str(after_dir)])
    assert result.exit_code == 0, result.output
    assert "Triage Comparison Report" in result.output
    # The label should annotate the directory with the file count.
    assert "2 file" in result.output


def test_cli_compare_empty_directory_fails(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    after = tmp_path / "after.ndjson"
    _write_ndjson(after, [dict(MINIMAL_FAILURE)])
    runner = CliRunner()
    result = runner.invoke(main, ["compare", str(empty_dir), str(after)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Confidence-aware deltas and recovery latency change
# ---------------------------------------------------------------------------


class TestTentativeAndLatency:
    def test_tentative_marker_below_threshold(self):
        before = _failures(classification="agent_error", count=1)
        after = _failures(classification="agent_error", count=2)
        report = compare_event_batches(before, after)
        d = next(d for d in report.deltas if d.classification == "agent_error")
        assert d.is_tentative
        assert "tentative" in d.frequency_change

    def test_no_tentative_marker_above_threshold(self):
        # CONFIDENCE_THRESHOLD is 5; both sides at or above it.
        before = _failures(classification="agent_error", count=8)
        after = _failures(classification="agent_error", count=6)
        report = compare_event_batches(before, after)
        d = next(d for d in report.deltas if d.classification == "agent_error")
        assert not d.is_tentative
        assert "tentative" not in d.frequency_change

    def test_latency_change_new_when_only_after_recovers(self):
        before = _failures(classification="agent_error", count=2)
        # after has same failures plus a recovery one turn later
        failures = _failures(classification="agent_error", count=2)
        recoveries = [
            make_event(
                event_id=f"ok-{i}",
                run_id=ev.run_id,
                turn=ev.turn + 1,
                agent_id=ev.agent_id,
                tool_name=ev.action_taken.tool_name,
                action_succeeded=True,
            )
            for i, ev in enumerate(failures)
        ]
        after = failures + recoveries
        report = compare_event_batches(before, after)
        d = next(d for d in report.deltas if d.classification == "agent_error")
        assert d.before_median_latency is None
        assert d.after_median_latency == 1.0
        assert "new" in d.latency_change

    def test_latency_change_stable_when_unchanged(self):
        # Same recovery dynamics in both batches.
        def with_recovery(run_id: str, count: int):
            failures = _failures(
                classification="agent_error", count=count, run_id=run_id
            )
            recoveries = [
                make_event(
                    event_id=f"ok-{run_id}-{i}",
                    run_id=run_id,
                    turn=ev.turn + 1,
                    agent_id=ev.agent_id,
                    tool_name=ev.action_taken.tool_name,
                    action_succeeded=True,
                )
                for i, ev in enumerate(failures)
            ]
            return failures + recoveries

        before = with_recovery("r1", 3)
        after = with_recovery("r2", 3)
        report = compare_event_batches(before, after)
        d = next(d for d in report.deltas if d.classification == "agent_error")
        assert d.latency_change.startswith("stable")


class TestScoreSummary:
    def test_summary_is_populated_for_both_sides(self):
        before = _failures(classification="agent_error", count=3)
        after = _failures(classification="agent_error", count=1)
        report = compare_event_batches(before, after)
        assert report.before_summary is not None
        assert report.after_summary is not None
        assert report.before_summary.failure_event_count == 3
        assert report.after_summary.failure_event_count == 1

    def test_summary_counts_distinct_patterns(self):
        before = _failures(
            classification="agent_error", count=2
        ) + _failures(
            classification="coordination_failure",
            count=2,
            agent_id="B",
            tool_name="dispatch",
        )
        after = _failures(classification="agent_error", count=2)
        report = compare_event_batches(before, after)
        assert report.before_summary.pattern_count == 2
        assert report.after_summary.pattern_count == 1

    def test_summary_counts_coordination_failures(self):
        before = _failures(classification="agent_error", count=2)
        after = _failures(
            classification="coordination_failure",
            count=4,
            agent_id="B",
            tool_name="dispatch",
        )
        report = compare_event_batches(before, after)
        assert report.before_summary.coordination_failure_count == 0
        assert report.after_summary.coordination_failure_count == 4

    def test_summary_empty_batch(self):
        report = compare_event_batches([], _failures(
            classification="agent_error", count=1
        ))
        assert report.before_summary is not None
        assert report.before_summary.pattern_count == 0
        assert report.before_summary.failure_event_count == 0


def test_score_summary_renders_in_report():
    before = _failures(classification="agent_error", count=3)
    after = _failures(classification="agent_error", count=1)
    text = build_comparison_report(
        compare_event_batches(before, after),
        before_path="b.ndjson",
        after_path="a.ndjson",
    )
    assert "Score Summary" in text
    assert "Distinct incident patterns" in text
    assert "Failure events" in text


