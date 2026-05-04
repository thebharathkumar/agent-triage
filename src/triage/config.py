"""config.py - load runtime configuration from a TOML file.

Lets users tune the scoring model, alerting thresholds, and storage path
without forking the project. All sections are optional; defaults match
the historical hardcoded behavior so legacy callers see no change.

Example ``triage.toml``::

    [scoring]
    recovery_window = 5
    no_recovery_multiplier = 2.0
    frequency_weight = 0.3
    severity_weight = 0.7

    [scoring.weights]
    coordination_failure = 1.0
    agent_error = 0.8
    information_lag = 0.4

    [storage]
    db_path = "./triage.db"

    [alerting]
    webhook_url = "https://hooks.slack.com/services/..."
    threshold = 8.0
    cooldown_seconds = 1800
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WEIGHTS: dict[str, float] = {
    "coordination_failure": 1.0,
    "agent_error": 0.7,
    "information_lag": 0.5,
    "environment_constraint": 0.2,
    "unclassified": 0.3,
}


@dataclass
class ScoringConfig:
    recovery_window: int = 3
    no_recovery_multiplier: float = 1.5
    frequency_weight: float = 0.4
    severity_weight: float = 0.6
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


@dataclass
class StorageConfig:
    db_path: str = ":memory:"


@dataclass
class AlertConfig:
    webhook_url: str | None = None
    threshold: float = 10.0
    cooldown_seconds: int = 3600


@dataclass
class TriageConfig:
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    alerting: AlertConfig = field(default_factory=AlertConfig)

    @classmethod
    def default(cls) -> TriageConfig:
        return cls()

    @classmethod
    def from_file(cls, path: Path) -> TriageConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        scoring_data = data.get("scoring", {})
        weights = scoring_data.pop("weights", None)
        scoring = ScoringConfig(**scoring_data)
        if weights is not None:
            # Merge user-supplied weights over defaults so unspecified
            # classifications still get a sensible weight.
            merged = dict(DEFAULT_WEIGHTS)
            merged.update({str(k): float(v) for k, v in weights.items()})
            scoring.weights = merged

        storage = StorageConfig(**data.get("storage", {}))
        alerting = AlertConfig(**data.get("alerting", {}))

        return cls(scoring=scoring, storage=storage, alerting=alerting)
