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
        # before: 4 failures, none recover -> 4 unrecovered
        before = _failures(classification="agent_error", count=4)
        # after: same 4 failures, plus a successful action one turn after
        # each so every failure recovers within the window.
        failures = _failures(classification="agent_error", count=4)
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
        assert agent_error.before_unrecovered == 4
        assert agent_error.after_unrecovered == 0
        assert agent_error.unrecovered_change == "resolved"

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
