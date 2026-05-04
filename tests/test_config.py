"""Tests for triage.config."""

from __future__ import annotations

from pathlib import Path

from triage.config import (
    DEFAULT_WEIGHTS,
    AlertConfig,
    ScoringConfig,
    StorageConfig,
    TriageConfig,
)


class TestDefaults:
    def test_default_scoring(self):
        s = ScoringConfig()
        assert s.recovery_window == 3
        assert s.no_recovery_multiplier == 1.5
        assert s.frequency_weight == 0.4
        assert s.severity_weight == 0.6
        assert s.weights == DEFAULT_WEIGHTS

    def test_default_storage_in_memory(self):
        assert StorageConfig().db_path == ":memory:"

    def test_default_alerting_disabled(self):
        a = AlertConfig()
        assert a.webhook_url is None
        assert a.threshold == 10.0

    def test_triage_config_default(self):
        cfg = TriageConfig.default()
        assert isinstance(cfg.scoring, ScoringConfig)
        assert isinstance(cfg.storage, StorageConfig)
        assert isinstance(cfg.alerting, AlertConfig)


class TestFromFile:
    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "triage.toml"
        p.write_text(body)
        return p

    def test_partial_scoring_overrides(self, tmp_path: Path):
        p = self._write(
            tmp_path,
            """
            [scoring]
            recovery_window = 5
            """,
        )
        cfg = TriageConfig.from_file(p)
        assert cfg.scoring.recovery_window == 5
        # Unspecified fields stay at defaults
        assert cfg.scoring.no_recovery_multiplier == 1.5

    def test_weight_overrides_merge_with_defaults(self, tmp_path: Path):
        p = self._write(
            tmp_path,
            """
            [scoring.weights]
            agent_error = 0.95
            """,
        )
        cfg = TriageConfig.from_file(p)
        assert cfg.scoring.weights["agent_error"] == 0.95
        # Unspecified classifications keep defaults
        assert cfg.scoring.weights["coordination_failure"] == 1.0

    def test_storage_path_loaded(self, tmp_path: Path):
        p = self._write(
            tmp_path,
            """
            [storage]
            db_path = "./mydb.sqlite"
            """,
        )
        cfg = TriageConfig.from_file(p)
        assert cfg.storage.db_path == "./mydb.sqlite"

    def test_alerting_loaded(self, tmp_path: Path):
        p = self._write(
            tmp_path,
            """
            [alerting]
            webhook_url = "https://example.com/hook"
            threshold = 7.5
            cooldown_seconds = 60
            """,
        )
        cfg = TriageConfig.from_file(p)
        assert cfg.alerting.webhook_url == "https://example.com/hook"
        assert cfg.alerting.threshold == 7.5
        assert cfg.alerting.cooldown_seconds == 60

    def test_empty_file_returns_defaults(self, tmp_path: Path):
        p = self._write(tmp_path, "")
        cfg = TriageConfig.from_file(p)
        assert cfg.scoring.recovery_window == 3

    def test_only_section_headers_returns_defaults(self, tmp_path: Path):
        p = self._write(tmp_path, "[scoring]\n[storage]\n[alerting]\n")
        cfg = TriageConfig.from_file(p)
        assert cfg.scoring.recovery_window == 3
        assert cfg.alerting.webhook_url is None
