"""Tests for triage.analyst."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from tests.conftest import make_event
from triage.analyst import AnalysisResult, analyze_pattern, analyze_patterns
from triage.grouper import group_events
from triage.scorer import score_patterns

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scored(classification: str = "agent_error", count: int = 3):
    events = [
        make_event(
            event_id=f"e{i}",
            action_succeeded=False,
            failure_classification=classification,
            divergence_fields=["position"] if i % 2 == 0 else [],
        )
        for i in range(count)
    ]
    patterns = group_events(events)
    scored = score_patterns(patterns, events, 1)
    return scored[0]


def _mock_anthropic_module() -> MagicMock:
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="Agent A hit a coordination failure on turn 3.")]
    mock_msg.model = "claude-haiku-4-5-20251001"
    mock_msg.usage = MagicMock(cache_read_input_tokens=0)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    mock_mod = MagicMock()
    mock_mod.Anthropic.return_value = mock_client
    return mock_mod


# ---------------------------------------------------------------------------
# analyze_pattern
# ---------------------------------------------------------------------------


class TestAnalyzePattern:
    def test_returns_analysis_result(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        result = analyze_pattern(sp)

        assert isinstance(result, AnalysisResult)
        assert result.pattern_id == sp.pattern.pattern_id
        assert len(result.narrative) > 0
        assert result.model == "claude-haiku-4-5-20251001"

    def test_uses_provided_api_key(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)

        analyze_pattern(sp, api_key="explicit-key")

        mock_mod.Anthropic.assert_called_once_with(api_key="explicit-key")

    def test_raises_without_api_key(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            analyze_pattern(sp)

    def test_raises_without_anthropic_package(self, monkeypatch):
        sp = _make_scored()
        # Remove anthropic from sys.modules so the import fails
        monkeypatch.setitem(sys.modules, "anthropic", None)  # type: ignore[arg-type]

        with pytest.raises((RuntimeError, ImportError)):
            analyze_pattern(sp, api_key="key")

    def test_cached_flag_true_when_cache_tokens_present(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        create_rv = mock_mod.Anthropic.return_value.messages.create.return_value
        create_rv.usage.cache_read_input_tokens = 50
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        result = analyze_pattern(sp)
        assert result.cached is True

    def test_cached_flag_false_when_no_cache_tokens(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        result = analyze_pattern(sp)
        assert result.cached is False

    def test_system_prompt_uses_cache_control(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        analyze_pattern(sp)

        call_kwargs = mock_mod.Anthropic.return_value.messages.create.call_args
        system = call_kwargs.kwargs["system"]
        assert any(
            block.get("cache_control", {}).get("type") == "ephemeral"
            for block in system
        )

    def test_model_is_haiku(self, monkeypatch):
        sp = _make_scored()
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        analyze_pattern(sp)

        call_kwargs = mock_mod.Anthropic.return_value.messages.create.call_args
        assert "haiku" in call_kwargs.kwargs["model"]

    def test_user_message_includes_pattern_info(self, monkeypatch):
        sp = _make_scored("coordination_failure")
        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        analyze_pattern(sp)

        call_kwargs = mock_mod.Anthropic.return_value.messages.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = messages[0]["content"]
        assert "coordination_failure" in user_content


# ---------------------------------------------------------------------------
# analyze_patterns
# ---------------------------------------------------------------------------


class TestAnalyzePatterns:
    def test_returns_dict_keyed_by_pattern_id(self, monkeypatch):
        events = [
            make_event(
                event_id=f"e{i}", action_succeeded=False, failure_classification="agent_error"
            )
            for i in range(3)
        ]
        patterns = group_events(events)
        scored = score_patterns(patterns, events, 1)

        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        results = analyze_patterns(scored, top_n=1)

        assert len(results) == 1
        assert scored[0].pattern.pattern_id in results

    def test_top_n_limits_calls(self, monkeypatch):
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

        mock_mod = _mock_anthropic_module()
        monkeypatch.setitem(sys.modules, "anthropic", mock_mod)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        results = analyze_patterns(scored, top_n=2)

        assert len(results) == 2
        assert mock_mod.Anthropic.return_value.messages.create.call_count == 2
