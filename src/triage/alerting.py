"""alerting.py - fire webhooks (Slack-compatible) when severity crosses threshold."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from triage.config import AlertConfig
from triage.scorer import ScoredPattern

logger = logging.getLogger(__name__)


class Alerter:
    """Threshold-based webhook alerter with per-pattern cooldown.

    Each ``maybe_alert()`` call inspects scored patterns and fires the
    configured webhook for any whose ``final_score`` exceeds
    ``config.threshold``, subject to a per-pattern cooldown so the same
    pattern doesn't spam during a continuous incident.
    """

    def __init__(self, config: AlertConfig) -> None:
        self.config = config
        self._last_fired: dict[str, float] = {}

    def maybe_alert(self, scored: list[ScoredPattern]) -> list[dict[str, object]]:
        """Send alerts for patterns above threshold; return list of fired alerts."""
        if not self.config.webhook_url:
            return []

        now = time.time()
        fired: list[dict[str, object]] = []
        for sp in scored:
            if sp.final_score < self.config.threshold:
                continue
            pid = sp.pattern.pattern_id
            last = self._last_fired.get(pid, 0.0)
            if now - last < self.config.cooldown_seconds:
                continue

            ok = self._send(sp)
            if ok:
                self._last_fired[pid] = now
                fired.append(
                    {
                        "pattern_id": pid,
                        "score": sp.final_score,
                        "delivered": True,
                    }
                )
        return fired

    def _send(self, sp: ScoredPattern) -> bool:
        """POST a Slack-compatible JSON payload. Returns True on 2xx."""
        assert self.config.webhook_url is not None  # guarded by caller

        p = sp.pattern
        text = (
            f":rotating_light: *Triage alert* — score {sp.final_score:.2f}\n"
            f">*{p.display_name()}*\n"
            f">Frequency: {p.frequency} event(s) across {len(p.run_ids)} run(s)\n"
            f">Recovery rate: {sp.recovery_rate:.0%}"
        )
        payload = {
            "text": text,
            # Plain text fallback so non-Slack webhooks still get something useful
            "pattern_id": p.pattern_id,
            "agent_id": p.agent_id,
            "tool_name": p.tool_name,
            "classification": p.failure_classification,
            "final_score": sp.final_score,
            "severity_score": sp.severity_score,
            "recovery_rate": sp.recovery_rate,
        }

        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.config.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                status: int = int(resp.status)
                return 200 <= status < 300
        except urllib.error.URLError as exc:
            logger.warning("Alert webhook failed: %s", exc)
            return False
