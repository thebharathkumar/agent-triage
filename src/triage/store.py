"""store.py - SQLite-backed persistent store for ingested trace events.

Replaces the in-memory list with a real database so the dashboard
survives restarts and supports time-series queries. Uses stdlib
``sqlite3`` to avoid adding a runtime dependency.

The schema keeps the full event JSON in a ``raw_json`` column so we can
reconstruct ``TraceEvent`` objects without mapping every field, while
indexed columns enable fast filtering for trend queries.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any

from triage.loader import TraceEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    action_succeeded INTEGER NOT NULL,
    failure_classification TEXT,
    timestamp TEXT,
    raw_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_run_agent ON events(run_id, agent_id);
CREATE INDEX IF NOT EXISTS idx_events_classification ON events(failure_classification);
"""


class TraceStore:
    """SQLite-backed store for ``TraceEvent`` rows."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        # check_same_thread=False because FastAPI request handlers run in a
        # threadpool. SQLite serialises writes internally so this is safe
        # for our single-process use case.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_events(self, events: list[TraceEvent]) -> int:
        if not events:
            return 0

        rows: list[tuple[Any, ...]] = []
        for ev in events:
            ts: str | None = None
            if ev.timestamp is not None:
                ts = ev.timestamp.isoformat()
            rows.append(
                (
                    ev.event_id,
                    ev.run_id,
                    ev.agent_id,
                    ev.turn,
                    ev.action_taken.tool_name,
                    1 if ev.action_succeeded else 0,
                    ev.failure_classification,
                    ts,
                    ev.model_dump_json(),
                )
            )
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO events (
                event_id, run_id, agent_id, turn, tool_name,
                action_succeeded, failure_classification, timestamp, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        return len(events)

    def all_events(self) -> list[TraceEvent]:
        rows = self._conn.execute(
            "SELECT raw_json FROM events ORDER BY inserted_at, turn"
        ).fetchall()
        return [TraceEvent.model_validate_json(r[0]) for r in rows]

    def count(self) -> int:
        result = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(result[0]) if result else 0

    def clear(self) -> None:
        self._conn.execute("DELETE FROM events")
        self._conn.commit()

    def daily_severity_counts(self, days: int = 7) -> list[dict[str, Any]]:
        """Return per-day failure counts grouped by classification.

        Output shape::

            [
                {"date": "2026-04-29", "classification": "agent_error", "count": 12},
                ...
            ]

        Days with no data are omitted; the dashboard fills gaps client-side.
        """
        cutoff = (
            datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days)
        ).date().isoformat()
        rows = self._conn.execute(
            """
            SELECT
                date(timestamp) AS day,
                COALESCE(failure_classification, 'success') AS cls,
                COUNT(*) AS n
            FROM events
            WHERE timestamp IS NOT NULL
              AND date(timestamp) >= ?
              AND action_succeeded = 0
            GROUP BY day, cls
            ORDER BY day, cls
            """,
            (cutoff,),
        ).fetchall()
        return [{"date": r[0], "classification": r[1], "count": r[2]} for r in rows]

    def pattern_daily_counts(self, days: int = 7) -> dict[str, list[dict[str, Any]]]:
        """Return per-pattern, per-day failure counts.

        Pattern key is ``agent_id|tool_name|classification``; this matches the
        coarse pattern signature used by ``grouper.py`` (without the divergence
        fields, since we don't index them — close enough for trend display).
        """
        cutoff = (
            datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=days)
        ).date().isoformat()
        rows = self._conn.execute(
            """
            SELECT
                agent_id || '|' || tool_name || '|'
                    || COALESCE(failure_classification, 'unclassified') AS key,
                date(timestamp) AS day,
                COUNT(*) AS n
            FROM events
            WHERE timestamp IS NOT NULL
              AND date(timestamp) >= ?
              AND action_succeeded = 0
            GROUP BY key, day
            ORDER BY key, day
            """,
            (cutoff,),
        ).fetchall()

        out: dict[str, list[dict[str, Any]]] = {}
        for key, day, n in rows:
            out.setdefault(key, []).append({"date": day, "count": n})
        return out

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton management
# ---------------------------------------------------------------------------

_singleton: TraceStore | None = None


def get_store(db_path: str | None = None) -> TraceStore:
    """Return the process-wide TraceStore singleton, creating it on first call.

    Resolution order: explicit ``db_path`` argument > ``TRIAGE_DB_PATH`` env
    var > in-memory.
    """
    global _singleton
    if _singleton is None:
        path = db_path or os.environ.get("TRIAGE_DB_PATH") or ":memory:"
        _singleton = TraceStore(path)
    return _singleton


def reset_store(db_path: str | None = None) -> TraceStore:
    """Close and recreate the singleton. Used by tests for isolation."""
    global _singleton
    if _singleton is not None:
        _singleton.close()
        _singleton = None
    return get_store(db_path)
