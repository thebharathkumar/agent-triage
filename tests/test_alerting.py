"""Tests for triage.alerting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests.conftest import make_event
from triage.alerting import Alerter
from triage.config import AlertConfig
from triage.grouper import group_events
from triage.scorer import score_patterns


def _make_high_severity_pattern():
    """Force a high final_score by using lots of coordination failures."""
    events = [
        make_event(
            event_id=f"e{i}",
            action_succeeded=False,
            failure_classification="coordination_failure",
        )
        for i in range(5)
    ]
    patterns = group_events(events)
    return score_patterns(patterns, events, 1)


class TestAlerterDisabled:
    def test_no_webhook_returns_empty(self):
        alerter = Alerter(AlertConfig(webhook_url=None))
        scored = _make_high_severity_pattern()
        assert alerter.maybe_alert(scored) == []


class TestAlerterFiring:
    @patch("triage.alerting.urllib.request.urlopen")
    def test_fires_when_above_threshold(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: None
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        alerter = Alerter(AlertConfig(
            webhook_url="https://example.com/hook",
            threshold=1.0,  # very low threshold so it fires
            cooldown_seconds=0,
        ))
        scored = _make_high_severity_pattern()
        fired = alerter.maybe_alert(scored)

        assert len(fired) == 1
        assert mock_urlopen.called

    @patch("triage.alerting.urllib.request.urlopen")
    def test_does_not_fire_below_threshold(self, mock_urlopen):
        alerter = Alerter(AlertConfig(
            webhook_url="https://example.com/hook",
            threshold=999.0,
        ))
        scored = _make_high_severity_pattern()
        fired = alerter.maybe_alert(scored)

        assert fired == []
        assert not mock_urlopen.called

    @patch("triage.alerting.urllib.request.urlopen")
    def test_cooldown_suppresses_repeat(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: None
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        alerter = Alerter(AlertConfig(
            webhook_url="https://example.com/hook",
            threshold=1.0,
            cooldown_seconds=3600,
        ))
        scored = _make_high_severity_pattern()
        first = alerter.maybe_alert(scored)
        second = alerter.maybe_alert(scored)

        assert len(first) == 1
        assert second == []  # cooldown active

    @patch("triage.alerting.urllib.request.urlopen")
    def test_zero_cooldown_allows_immediate_refire(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: None
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        alerter = Alerter(AlertConfig(
            webhook_url="https://example.com/hook",
            threshold=1.0,
            cooldown_seconds=0,
        ))
        scored = _make_high_severity_pattern()
        first = alerter.maybe_alert(scored)
        second = alerter.maybe_alert(scored)

        assert len(first) == 1
        assert len(second) == 1

    @patch("triage.alerting.urllib.request.urlopen")
    def test_failed_webhook_skips_cooldown_set(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        alerter = Alerter(AlertConfig(
            webhook_url="https://example.com/hook",
            threshold=1.0,
            cooldown_seconds=3600,
        ))
        scored = _make_high_severity_pattern()
        fired = alerter.maybe_alert(scored)

        # Failed delivery: nothing fired, cooldown not set, would retry next time
        assert fired == []

    @patch("triage.alerting.urllib.request.urlopen")
    def test_payload_includes_pattern_id_and_score(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: None
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        alerter = Alerter(AlertConfig(
            webhook_url="https://example.com/hook",
            threshold=1.0,
            cooldown_seconds=0,
        ))
        scored = _make_high_severity_pattern()
        alerter.maybe_alert(scored)

        # Inspect the request body
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        import json
        payload = json.loads(req.data.decode())
        assert "pattern_id" in payload
        assert "final_score" in payload
        assert "text" in payload  # Slack-compatible
