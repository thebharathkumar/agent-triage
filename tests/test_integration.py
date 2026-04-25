"""Integration tests for CLI and reporter."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from tests.conftest import make_event
from triage.cli import main
from triage.grouper import group_events
from triage.reporter import CLASSIFICATION_LABELS, build_report
from triage.scorer import score_patterns

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

MINIMAL_EVENT: dict = {
    "event_id": "abc-123",
    "run_id": "seed42",
    "turn": 0,
    "agent_id": "A",
    "timestamp": "2026-04-09T19:00:00+00:00",
    "latency_ms": {"llm": 100, "tool": 0, "total": 100},
    "action_taken": {"tool_name": "move", "tool_input": {"direction": "north"}},
    "action_result": {"ok": True},
    "action_succeeded": False,
    "agent_belief_state": {},
    "world_truth_state": {},
    "belief_accuracy": {},
    "divergence_fields": [],
    "divergence_age": {},
    "divergences": [],
    "message_context": {},
    "failure_classification": "agent_error",
    "reasoning": "moving north",
    "usage": {"input_tokens": 100, "output_tokens": 50},
}


# ---------------------------------------------------------------------------
# Reporter unit tests
# ---------------------------------------------------------------------------


class TestBuildReport:
    def _make_report(self, classification: str = "agent_error", count: int = 1) -> str:
        events = [
            make_event(
                event_id=f"e{i}",
                action_succeeded=False,
                failure_classification=classification,
            )
            for i in range(count)
        ]
        patterns = group_events(events)
        scored = score_patterns(patterns, events, 1)
        return build_report(
            scored=scored,
            total_runs=1,
            total_patterns=len(patterns),
            source_files=["test.ndjson"],
        )

    def test_report_contains_title(self):
        report = self._make_report()
        assert "# Triage Report" in report

    def test_report_contains_pattern_name(self):
        report = self._make_report("coordination_failure")
        assert "coordination_failure" in report

    def test_report_contains_severity_score(self):
        report = self._make_report()
        assert "Severity Score" in report

    def test_report_contains_frequency(self):
        report = self._make_report(count=3)
        assert "Frequency" in report

    def test_report_contains_recovery_bar(self):
        report = self._make_report()
        assert "[" in report and "]" in report

    def test_report_contains_next_action(self):
        report = self._make_report()
        assert "Suggested next action" in report

    def test_report_contains_confidence_row(self):
        report = self._make_report()
        assert "Confidence" in report

    def test_report_contains_recovery_latency_row(self):
        report = self._make_report()
        assert "Recovery Latency" in report

    def test_report_contains_tail_risk_row(self):
        report = self._make_report()
        assert "Tail Risk" in report

    def test_report_contains_appeared_in_row(self):
        report = self._make_report()
        assert "Appeared in" in report

    def test_report_contains_trend_row(self):
        report = self._make_report()
        assert "Trend" in report

    def test_empty_scored_shows_no_incidents_message(self):
        report = build_report(
            scored=[],
            total_runs=1,
            total_patterns=0,
            source_files=["test.ndjson"],
        )
        assert "No incidents detected" in report

    def test_top_n_limits_output(self):
        events = [
            make_event(
                event_id=f"e{i}",
                agent_id=["A", "B", "C"][i % 3],
                action_succeeded=False,
                failure_classification=[
                    "agent_error", "coordination_failure", "information_lag"
                ][i % 3],
            )
            for i in range(3)
        ]
        patterns = group_events(events)
        scored = score_patterns(patterns, events, 1)
        report = build_report(
            scored=scored,
            total_runs=1,
            total_patterns=len(patterns),
            source_files=["test.ndjson"],
            top_n=1,
        )
        assert "## #1" in report
        assert "## #2" not in report

    def test_classification_labels_applied_in_report(self):
        for classification, label in CLASSIFICATION_LABELS.items():
            report = self._make_report(classification=classification)
            assert label in report

    def test_report_no_em_dashes(self):
        report = self._make_report()
        assert "\u2014" not in report  # em dash character

    def test_recovery_note_changes_for_zero_recovery(self):
        report = self._make_report()
        assert "stuck" in report or "None of these failures" in report

    def test_source_files_listed(self):
        report = build_report(
            scored=[],
            total_runs=1,
            total_patterns=0,
            source_files=["my-trace.ndjson"],
        )
        assert "my-trace.ndjson" in report


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCLI:
    def _write_ndjson(self, tmp_path: Path, events: list[dict]) -> Path:
        p = tmp_path / "trace.ndjson"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return p

    def test_cli_no_args_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code != 0

    def test_cli_valid_file_exits_zero(self, tmp_path: Path):
        p = self._write_ndjson(tmp_path, [MINIMAL_EVENT])
        runner = CliRunner()
        result = runner.invoke(main, [str(p)])
        assert result.exit_code == 0

    def test_cli_output_contains_report_header(self, tmp_path: Path):
        p = self._write_ndjson(tmp_path, [MINIMAL_EVENT])
        runner = CliRunner()
        result = runner.invoke(main, [str(p)])
        assert "Triage Report" in result.output

    def test_cli_output_flag_writes_file(self, tmp_path: Path):
        p = self._write_ndjson(tmp_path, [MINIMAL_EVENT])
        out = tmp_path / "report.md"
        runner = CliRunner()
        result = runner.invoke(main, [str(p), "--output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "Triage Report" in out.read_text()

    def test_cli_top_flag_respected(self, tmp_path: Path):
        events = [
            dict(
                MINIMAL_EVENT,
                event_id=f"e{i}",
                agent_id=["A", "B", "C"][i],
                failure_classification=[
                    "agent_error", "coordination_failure", "information_lag"
                ][i],
            )
            for i in range(3)
        ]
        p = self._write_ndjson(tmp_path, events)
        runner = CliRunner()
        result = runner.invoke(main, [str(p), "--top", "1"])
        assert result.exit_code == 0
        assert "## #1" in result.output
        assert "## #2" not in result.output

    def test_cli_multiple_files(self, tmp_path: Path):
        p1 = tmp_path / "a.ndjson"
        p1.write_text(json.dumps(dict(MINIMAL_EVENT, event_id="e1", run_id="r1")))
        p2 = tmp_path / "b.ndjson"
        p2.write_text(json.dumps(dict(MINIMAL_EVENT, event_id="e2", run_id="r2")))
        runner = CliRunner()
        result = runner.invoke(main, [str(p1), str(p2)])
        assert result.exit_code == 0
        assert "2 run" in result.output

    def test_cli_empty_file_exits_nonzero(self, tmp_path: Path):
        p = tmp_path / "empty.ndjson"
        p.write_text("")
        runner = CliRunner()
        result = runner.invoke(main, [str(p)])
        assert result.exit_code != 0
