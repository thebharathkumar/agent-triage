"""Unit tests for reporter._recovery_bar, _explain, and CLI --top validation."""

from __future__ import annotations

from click.testing import CliRunner

from tests.conftest import make_event
from triage.cli import main
from triage.reporter import _explain, _recovery_bar
from triage.scorer import RECOVERY_WINDOW, ScoredPattern

# ---------------------------------------------------------------------------
# _recovery_bar
# ---------------------------------------------------------------------------


def test_recovery_bar_zero():
    assert _recovery_bar(0.0) == "[----------] 0%"


def test_recovery_bar_full():
    assert _recovery_bar(1.0) == "[##########] 100%"


def test_recovery_bar_half():
    bar = _recovery_bar(0.5)
    assert bar == "[#####-----] 50%"


def test_recovery_bar_small_nonzero_shows_at_least_one_block():
    # 4.5% should show 1 filled block (ceil), not 0
    bar = _recovery_bar(0.045)
    assert bar.startswith("[#")
    assert "4%" in bar


def test_recovery_bar_does_not_overflow():
    bar = _recovery_bar(1.0)
    inner = bar[1:11]
    assert len(inner) == 10
    assert "-" not in inner


# ---------------------------------------------------------------------------
# _explain
# ---------------------------------------------------------------------------


def _make_scored(
    *,
    agent_id: str = "A",
    tool_name: str = "move",
    classification: str = "agent_error",
    frequency: int = 3,
    recovery_rate: float = 0.0,
    divergence_fields: list[str] | None = None,
) -> ScoredPattern:
    events = [
        make_event(
            event_id=f"evt-{i}",
            run_id=f"run-{i}",
            agent_id=agent_id,
            tool_name=tool_name,
            action_succeeded=False,
            failure_classification=classification,
            divergence_fields=divergence_fields or [],
        )
        for i in range(frequency)
    ]
    from triage.grouper import IncidentPattern
    pattern = IncidentPattern(
        pattern_id=f"{agent_id}-{tool_name}-{classification}",
        agent_id=agent_id,
        tool_name=tool_name,
        failure_classification=classification,
        divergence_fields=frozenset(divergence_fields or []),
        events=events,
    )
    return ScoredPattern(
        pattern=pattern,
        frequency_score=3.0,
        severity_score=7.0,
        recovery_rate=recovery_rate,
        final_score=5.4,
    )


def test_explain_zero_recovery_mentions_window():
    sp = _make_scored(recovery_rate=0.0)
    text = _explain(sp, total_runs=5)
    assert str(RECOVERY_WINDOW) in text
    assert "stuck" in text


def test_explain_partial_recovery_mentions_window():
    sp = _make_scored(recovery_rate=0.5)
    text = _explain(sp, total_runs=5)
    assert str(RECOVERY_WINDOW) in text
    assert "50%" in text


def test_explain_includes_divergence_fields():
    sp = _make_scored(divergence_fields=["position", "inventory"])
    text = _explain(sp, total_runs=3)
    assert "position" in text
    assert "inventory" in text


def test_explain_no_divergence_omits_divergence_note():
    sp = _make_scored(divergence_fields=[])
    text = _explain(sp, total_runs=3)
    assert "divergence" not in text.lower()


def test_explain_unclassified():
    sp = _make_scored(classification="unclassified")
    text = _explain(sp, total_runs=2)
    assert "Unclassified" in text


# ---------------------------------------------------------------------------
# CLI --top validation
# ---------------------------------------------------------------------------


def _write_ndjson(tmp_path, events_data: list[dict]) -> str:
    import json
    p = tmp_path / "trace.ndjson"
    p.write_text("\n".join(json.dumps(e) for e in events_data), encoding="utf-8")
    return str(p)


MINIMAL_EVENT: dict = {
    "event_id": "abc-1",
    "run_id": "seed1",
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


def test_top_zero_is_rejected(tmp_path):
    runner = CliRunner()
    p = _write_ndjson(tmp_path, [MINIMAL_EVENT])
    result = runner.invoke(main, ["--top", "0", p])
    assert result.exit_code != 0
    assert "0" in result.output or "invalid" in result.output.lower()


def test_top_negative_is_rejected(tmp_path):
    runner = CliRunner()
    p = _write_ndjson(tmp_path, [MINIMAL_EVENT])
    result = runner.invoke(main, ["--top", "-1", p])
    assert result.exit_code != 0


def test_top_one_produces_single_incident(tmp_path):
    runner = CliRunner()
    p = _write_ndjson(tmp_path, [MINIMAL_EVENT])
    result = runner.invoke(main, ["--top", "1", p])
    assert result.exit_code == 0
    assert "# Triage Report" in result.output
    assert result.output.count("## #") == 1
