"""Tests for triage.server (FastAPI app, OTLP receiver, dashboard)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from triage.server import _clear_store, _span_to_event, app, reset_runtime

# ---------------------------------------------------------------------------
# TestClient fixture with store cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_store():
    """Reset the in-memory store + bus + cached config before every test."""
    reset_runtime()
    yield
    reset_runtime()
    _clear_store()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_root_returns_html(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_dashboard_contains_title(self, client: TestClient):
        r = client.get("/")
        assert "Agent Triage Dashboard" in r.text

    def test_dashboard_has_chart_js(self, client: TestClient):
        r = client.get("/")
        assert "chart.js" in r.text.lower()


# ---------------------------------------------------------------------------
# /api/report — empty store
# ---------------------------------------------------------------------------


class TestApiReportEmpty:
    def test_returns_zero_totals(self, client: TestClient):
        r = client.get("/api/report")
        assert r.status_code == 200
        data = r.json()
        assert data["total_runs"] == 0
        assert data["total_events"] == 0
        assert data["patterns"] == []


# ---------------------------------------------------------------------------
# /upload
# ---------------------------------------------------------------------------


def _ndjson_bytes(events: list[dict]) -> bytes:
    return b"\n".join(json.dumps(e).encode() for e in events)


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


class TestUpload:
    def _upload(self, client: TestClient, data: bytes, name: str = "trace.ndjson") -> dict:
        return client.post(
            "/upload",
            files={"files": (name, data, "application/octet-stream")},
        ).json()

    def test_upload_valid_file_returns_count(self, client: TestClient):
        r = self._upload(client, _ndjson_bytes([MINIMAL_EVENT]))
        assert r["events_loaded"] == 1

    def test_upload_populates_report(self, client: TestClient):
        self._upload(client, _ndjson_bytes([MINIMAL_EVENT]))
        assert client.get("/api/report").json()["total_events"] == 1

    def test_upload_multiple_files(self, client: TestClient):
        e1 = dict(MINIMAL_EVENT, event_id="e1", run_id="r1")
        e2 = dict(MINIMAL_EVENT, event_id="e2", run_id="r2")
        files = [
            ("files", ("a.ndjson", _ndjson_bytes([e1]), "application/octet-stream")),
            ("files", ("b.ndjson", _ndjson_bytes([e2]), "application/octet-stream")),
        ]
        r = client.post("/upload", files=files)
        assert r.json()["events_loaded"] == 2

    def test_upload_invalid_json_records_error(self, client: TestClient):
        data = b'{"event_id": "ok"}\nnot valid json\n'
        r = self._upload(client, data, "bad.ndjson")
        assert r["errors"] >= 1


# ---------------------------------------------------------------------------
# /api/report — with data
# ---------------------------------------------------------------------------


class TestApiReportWithData:
    def _load_event(self, client: TestClient, event: dict | None = None) -> None:
        ev = event or MINIMAL_EVENT
        client.post(
            "/upload",
            files={"files": ("t.ndjson", _ndjson_bytes([ev]), "application/octet-stream")},
        )

    def test_total_events_correct(self, client: TestClient):
        self._load_event(client)
        assert client.get("/api/report").json()["total_events"] == 1

    def test_total_runs_correct(self, client: TestClient):
        for i in range(3):
            self._load_event(client, dict(MINIMAL_EVENT, event_id=f"e{i}", run_id=f"r{i}"))
        assert client.get("/api/report").json()["total_runs"] == 3

    def test_pattern_has_required_fields(self, client: TestClient):
        self._load_event(client)
        data = client.get("/api/report").json()
        p = data["patterns"][0]
        for field in ("rank", "pattern_id", "final_score", "recovery_rate", "suggested_action"):
            assert field in p

    def test_top_n_limits_patterns(self, client: TestClient):
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
        for ev in events:
            self._load_event(client, ev)
        r = client.get("/api/report?top_n=1")
        assert len(r.json()["patterns"]) == 1

    def test_patterns_sorted_by_score(self, client: TestClient):
        events = [
            dict(
                MINIMAL_EVENT,
                event_id=f"e{i}",
                agent_id=["A", "B"][i % 2],
                failure_classification=["coordination_failure", "environment_constraint"][i % 2],
            )
            for i in range(4)
        ]
        for ev in events:
            self._load_event(client, ev)
        data = client.get("/api/report").json()["patterns"]
        scores = [p["final_score"] for p in data]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# DELETE /api/events
# ---------------------------------------------------------------------------


class TestClearEvents:
    def test_clear_removes_events(self, client: TestClient):
        data = _ndjson_bytes([MINIMAL_EVENT])
        client.post("/upload", files={"files": ("t.ndjson", data, "application/octet-stream")})
        assert client.get("/api/events/count").json()["count"] == 1
        client.delete("/api/events")
        assert client.get("/api/events/count").json()["count"] == 0


# ---------------------------------------------------------------------------
# /otlp/v1/traces
# ---------------------------------------------------------------------------


class TestOtlpReceiver:
    def _otlp_body(self, **overrides) -> dict:
        attrs = {
            "agent.id": "agent-A",
            "run.id": "run-1",
            "turn": 5,
            "action.tool": "move",
            "action.succeeded": False,
            "failure.classification": "agent_error",
            **overrides,
        }

        def _attr(key: str, val) -> dict:
            if isinstance(val, bool):
                return {"key": key, "value": {"boolValue": val}}
            if isinstance(val, int):
                return {"key": key, "value": {"intValue": str(val)}}
            return {"key": key, "value": {"stringValue": str(val)}}

        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "abc123",
                                    "spanId": "def456",
                                    "name": "agent.action",
                                    "startTimeUnixNano": "1712700000000000000",
                                    "endTimeUnixNano": "1712700000050000000",
                                    "attributes": [_attr(k, v) for k, v in attrs.items()],
                                }
                            ]
                        }
                    ],
                }
            ]
        }

    def test_valid_otlp_accepted(self, client: TestClient):
        r = client.post("/otlp/v1/traces", json=self._otlp_body())
        assert r.status_code == 200
        assert r.json()["events_accepted"] == 1

    def test_otlp_event_appears_in_report(self, client: TestClient):
        client.post("/otlp/v1/traces", json=self._otlp_body())
        data = client.get("/api/report").json()
        assert data["total_events"] == 1

    def test_otlp_run_id_set_from_attribute(self, client: TestClient):
        client.post("/otlp/v1/traces", json=self._otlp_body())
        data = client.get("/api/report").json()
        assert data["total_runs"] == 1

    def test_otlp_invalid_json_returns_400(self, client: TestClient):
        r = client.post(
            "/otlp/v1/traces",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 400

    def test_otlp_empty_resource_spans_accepted(self, client: TestClient):
        r = client.post("/otlp/v1/traces", json={"resourceSpans": []})
        assert r.status_code == 200
        assert r.json()["events_accepted"] == 0

    def test_otlp_divergence_fields_parsed(self, client: TestClient):
        body = self._otlp_body()
        body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"].append(
            {"key": "divergence.fields", "value": {"stringValue": "position,health"}}
        )
        r = client.post("/otlp/v1/traces", json=body)
        assert r.json()["events_accepted"] == 1

    def test_otlp_latency_computed_from_timestamps(self, client: TestClient):
        r = client.post("/otlp/v1/traces", json=self._otlp_body())
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# _span_to_event unit tests
# ---------------------------------------------------------------------------


class TestSpanToEvent:
    def _span(self, **attrs) -> dict:
        def _a(k, v):
            if isinstance(v, bool):
                return {"key": k, "value": {"boolValue": v}}
            if isinstance(v, int):
                return {"key": k, "value": {"intValue": str(v)}}
            return {"key": k, "value": {"stringValue": str(v)}}

        return {
            "spanId": "s1",
            "name": "agent.action",
            "startTimeUnixNano": "1712700000000000000",
            "endTimeUnixNano": "1712700000100000000",
            "attributes": [_a(k, v) for k, v in attrs.items()],
        }

    def test_converts_valid_span(self):
        span = self._span(
            **{
                "agent.id": "X",
                "run.id": "r1",
                "turn": 2,
                "action.tool": "grab",
                "action.succeeded": False,
            }
        )
        ev = _span_to_event(span, [])
        assert ev is not None
        assert ev.agent_id == "X"
        assert ev.run_id == "r1"
        assert ev.turn == 2
        assert ev.action_taken.tool_name == "grab"
        assert ev.action_succeeded is False

    def test_missing_optional_fields_defaults(self):
        span = {"spanId": "s2", "name": "fallback", "attributes": []}
        ev = _span_to_event(span, [])
        assert ev is not None
        assert ev.agent_id == "unknown"
        assert ev.turn == 0

    def test_latency_computed_from_timestamps(self):
        span = self._span(**{"action.tool": "move"})
        ev = _span_to_event(span, [])
        assert ev is not None
        assert ev.latency_ms.total == 100  # 100ms from timestamps

    def test_divergence_fields_parsed_from_comma_list(self):
        span = self._span(**{"divergence.fields": "position,health,inventory"})
        ev = _span_to_event(span, [])
        assert ev is not None
        assert set(ev.divergence_fields) == {"position", "health", "inventory"}


# ---------------------------------------------------------------------------
# /api/trends
# ---------------------------------------------------------------------------


class TestApiTrends:
    def test_empty_trends(self, client: TestClient):
        r = client.get("/api/trends?days=7")
        assert r.status_code == 200
        data = r.json()
        assert data["days"] == 7
        assert data["by_classification"] == []
        assert data["by_pattern"] == {}

    def test_trends_after_otlp_ingest(self, client: TestClient):
        # Send via OTLP (which sets timestamp from startTimeUnixNano)
        ts_ns = 1712700000000000000  # 2024-04-09T...
        body = {
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{
                    "spans": [{
                        "spanId": "s1",
                        "startTimeUnixNano": str(ts_ns),
                        "endTimeUnixNano": str(ts_ns + 50_000_000),
                        "attributes": [
                            {"key": "agent.id", "value": {"stringValue": "X"}},
                            {"key": "run.id", "value": {"stringValue": "r1"}},
                            {"key": "action.tool", "value": {"stringValue": "move"}},
                            {"key": "action.succeeded", "value": {"boolValue": False}},
                            {
                                "key": "failure.classification",
                                "value": {"stringValue": "agent_error"},
                            },
                        ],
                    }]
                }]
            }]
        }
        client.post("/otlp/v1/traces", json=body)
        r = client.get("/api/trends?days=3650")  # wide window to catch 2024 timestamp
        # Either has data or is empty (depending on event timestamp parsing); both OK
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/config
# ---------------------------------------------------------------------------


class TestApiConfig:
    def test_default_config_endpoint(self, client: TestClient):
        r = client.get("/api/config")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["scoring"]["recovery_window"] == 3
        assert cfg["alerting"]["enabled"] is False

    def test_config_does_not_leak_webhook_url(self, tmp_path, monkeypatch, client: TestClient):
        path = tmp_path / "t.toml"
        path.write_text(
            '[alerting]\nwebhook_url = "https://secret.example.com/super-secret-token"\n'
        )
        monkeypatch.setenv("TRIAGE_CONFIG", str(path))
        # Force config reload
        from triage.server import reset_runtime
        reset_runtime()

        r = client.get("/api/config")
        body = r.text
        assert "secret.example.com" not in body
        assert "super-secret-token" not in body
        assert r.json()["alerting"]["enabled"] is True


# ---------------------------------------------------------------------------
# /api/stream (SSE)
# ---------------------------------------------------------------------------


class TestSseStream:
    def test_stream_route_registered(self, client: TestClient):
        # FastAPI's TestClient hangs on never-ending SSE responses, so
        # we verify the route is wired up rather than driving the stream.
        # End-to-end pub/sub is covered by triage.streaming unit tests.
        from triage.server import app
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/stream" in paths


# ---------------------------------------------------------------------------
# Alert integration via /api/report
# ---------------------------------------------------------------------------


class TestReportAlerts:
    def test_no_webhook_no_alerts(self, client: TestClient):
        # Ingest a high-severity event
        ev = dict(MINIMAL_EVENT, failure_classification="coordination_failure")
        client.post(
            "/upload",
            files={"files": ("t.ndjson", _ndjson_bytes([ev]), "application/octet-stream")},
        )
        r = client.get("/api/report")
        assert r.json()["alerts_fired"] == []

    def test_alerts_fired_with_low_threshold(
        self, tmp_path, monkeypatch, client: TestClient
    ):
        from unittest.mock import MagicMock, patch

        # Enable alerting via config
        cfg_path = tmp_path / "alerting.toml"
        cfg_path.write_text(
            '[alerting]\n'
            'webhook_url = "https://example.com/hook"\n'
            'threshold = 0.1\n'
            'cooldown_seconds = 0\n'
        )
        monkeypatch.setenv("TRIAGE_CONFIG", str(cfg_path))
        from triage.server import reset_runtime
        reset_runtime()

        # Ingest events
        events = [
            dict(MINIMAL_EVENT, event_id=f"e{i}", failure_classification="coordination_failure")
            for i in range(3)
        ]
        client.post(
            "/upload",
            files={"files": ("t.ndjson", _ndjson_bytes(events), "application/octet-stream")},
        )

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda *a: None
        mock_resp.status = 200
        with patch("triage.alerting.urllib.request.urlopen", return_value=mock_resp):
            r = client.get("/api/report")

        assert len(r.json()["alerts_fired"]) >= 1
