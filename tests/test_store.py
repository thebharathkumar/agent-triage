"""Tests for triage.store."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from tests.conftest import make_event
from triage.store import TraceStore, get_store, reset_store


@pytest.fixture(autouse=True)
def fresh_singleton():
    reset_store(":memory:")
    yield
    reset_store(":memory:")


def _ts(days_ago: int) -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days_ago)


class TestStoreBasics:
    def test_empty_count_zero(self):
        s = TraceStore(":memory:")
        assert s.count() == 0

    def test_add_then_count(self):
        s = TraceStore(":memory:")
        s.add_events([make_event(event_id="e1"), make_event(event_id="e2")])
        assert s.count() == 2

    def test_add_empty_no_op(self):
        s = TraceStore(":memory:")
        assert s.add_events([]) == 0

    def test_clear_removes_all(self):
        s = TraceStore(":memory:")
        s.add_events([make_event()])
        s.clear()
        assert s.count() == 0

    def test_round_trip_preserves_event_data(self):
        s = TraceStore(":memory:")
        ev = make_event(
            event_id="rt-1",
            agent_id="A",
            tool_name="grab",
            failure_classification="agent_error",
            divergence_fields=["position", "health"],
        )
        s.add_events([ev])
        roundtripped = s.all_events()[0]
        assert roundtripped.event_id == "rt-1"
        assert roundtripped.agent_id == "A"
        assert roundtripped.action_taken.tool_name == "grab"
        assert roundtripped.failure_classification == "agent_error"
        assert set(roundtripped.divergence_fields) == {"position", "health"}

    def test_duplicate_event_id_replaces(self):
        s = TraceStore(":memory:")
        s.add_events([make_event(event_id="dup", agent_id="A")])
        s.add_events([make_event(event_id="dup", agent_id="B")])
        assert s.count() == 1
        assert s.all_events()[0].agent_id == "B"


class TestPersistence:
    def test_survives_close_reopen(self, tmp_path: Path):
        db = tmp_path / "test.db"
        s1 = TraceStore(str(db))
        s1.add_events([make_event(event_id="persisted")])
        s1.close()

        s2 = TraceStore(str(db))
        assert s2.count() == 1
        assert s2.all_events()[0].event_id == "persisted"


class TestSingleton:
    def test_get_store_returns_same_instance(self):
        a = get_store()
        b = get_store()
        assert a is b

    def test_reset_replaces_instance(self):
        a = get_store()
        b = reset_store(":memory:")
        assert a is not b

    def test_env_var_db_path(self, monkeypatch):
        monkeypatch.setenv("TRIAGE_DB_PATH", ":memory:")
        reset_store()
        s = get_store()
        assert s.db_path == ":memory:"


class TestTimeSeries:
    def _make_dated_event(self, eid: str, days_ago: int, classification: str):
        ev = make_event(
            event_id=eid,
            action_succeeded=False,
            failure_classification=classification,
        )
        ev.timestamp = _ts(days_ago)
        return ev

    def test_daily_severity_counts_buckets_by_day(self):
        s = TraceStore(":memory:")
        s.add_events([
            self._make_dated_event("e1", 1, "agent_error"),
            self._make_dated_event("e2", 1, "agent_error"),
            self._make_dated_event("e3", 2, "coordination_failure"),
        ])
        rows = s.daily_severity_counts(days=7)
        assert len(rows) >= 2
        agent_errors_today = [
            r for r in rows
            if r["classification"] == "agent_error" and r["count"] == 2
        ]
        assert len(agent_errors_today) == 1

    def test_pattern_daily_counts_groups_by_pattern(self):
        s = TraceStore(":memory:")
        s.add_events([
            self._make_dated_event("e1", 0, "agent_error"),
            self._make_dated_event("e2", 0, "agent_error"),
        ])
        out = s.pattern_daily_counts(days=7)
        # Default agent/tool from make_event = "A" / "move"
        assert "A|move|agent_error" in out
        assert out["A|move|agent_error"][0]["count"] == 2

    def test_events_outside_window_excluded(self):
        s = TraceStore(":memory:")
        s.add_events([
            self._make_dated_event("old", 30, "agent_error"),
            self._make_dated_event("new", 1, "agent_error"),
        ])
        rows = s.daily_severity_counts(days=7)
        assert all(r["count"] == 1 for r in rows)  # only the "new" one

    def test_events_without_timestamp_excluded_from_trends(self):
        s = TraceStore(":memory:")
        s.add_events([make_event(action_succeeded=False, failure_classification="agent_error")])
        assert s.daily_severity_counts(days=7) == []


class TestRetention:
    def _dated(self, eid: str, days_ago: int):
        ev = make_event(event_id=eid)
        ev.timestamp = _ts(days_ago)
        return ev

    def test_delete_older_than_drops_old_events(self):
        s = TraceStore(":memory:")
        s.add_events([self._dated("old", 40), self._dated("new", 1)])
        deleted = s.delete_older_than(30)
        assert deleted == 1
        assert s.count() == 1

    def test_delete_older_than_keeps_undated_events(self):
        s = TraceStore(":memory:")
        undated = make_event(event_id="no_ts")
        s.add_events([self._dated("old", 40), undated])
        s.delete_older_than(30)
        assert s.count() == 1  # undated event survives

    def test_delete_older_than_zero_match_returns_zero(self):
        s = TraceStore(":memory:")
        s.add_events([self._dated("recent", 1)])
        assert s.delete_older_than(30) == 0
        assert s.count() == 1
