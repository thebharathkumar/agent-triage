"""server.py - FastAPI web dashboard, OTLP receiver, SSE stream, alerting."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import click
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from triage.adapters import (
    OTLP_AGENT_ID_KEYS,
    OTLP_CLASSIFICATION_KEY,
    OTLP_DIVERGENCE_KEY,
    OTLP_LATENCY_LLM_KEY,
    OTLP_LATENCY_TOOL_KEY,
    OTLP_RUN_ID_KEYS,
    OTLP_SUCCEEDED_KEY,
    OTLP_TOOL_NAME_KEYS,
    OTLP_TURN_KEY,
)
from triage.alerting import Alerter
from triage.config import TriageConfig
from triage.grouper import IncidentPattern, group_events
from triage.loader import ActionTaken, Latency, LoadResult, TraceEvent, Usage, load_files
from triage.reporter import CLASSIFICATION_LABELS, NEXT_ACTIONS, build_report
from triage.scorer import ScoredPattern, score_patterns
from triage.store import get_store, reset_store
from triage.streaming import get_bus

# ---------------------------------------------------------------------------
# Persistent event store backed by SQLite (see triage.store)
# ---------------------------------------------------------------------------


def _compute_scored(
    events: list[TraceEvent],
) -> tuple[list[IncidentPattern], list[ScoredPattern], int]:
    """Run grouper + scorer; return (patterns, scored, total_runs)."""
    patterns = group_events(events)
    total_runs = len({e.run_id for e in events})
    scored = score_patterns(patterns, events, total_runs, config=get_config().scoring)
    return patterns, scored, total_runs


def _clear_store() -> None:
    """Wipe the store. Tests use this to reset between cases."""
    get_store().clear()


def _add_events(events: list[TraceEvent]) -> None:
    """Persist events and broadcast a 'new events' SSE notification."""
    if not events:
        return
    store = get_store()
    store.add_events(events)
    retention = get_config().storage.retention_days
    if retention is not None and retention > 0:
        store.delete_older_than(retention)
    bus = get_bus()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    payload = {"type": "events_added", "count": len(events)}
    if loop is not None:
        loop.create_task(bus.publish(payload))
    else:
        # Synchronous context (e.g. CLI tests): just skip the notification.
        pass


# ---------------------------------------------------------------------------
# Configuration loaded from TRIAGE_CONFIG env var (set by CLI), plus alerter.
# ---------------------------------------------------------------------------

_config: TriageConfig | None = None
_alerter: Alerter | None = None


def get_config() -> TriageConfig:
    global _config
    if _config is None:
        path = os.environ.get("TRIAGE_CONFIG")
        if path and Path(path).exists():
            _config = TriageConfig.from_file(Path(path))
        else:
            _config = TriageConfig.default()
    return _config


def get_alerter() -> Alerter:
    global _alerter
    if _alerter is None:
        _alerter = Alerter(get_config().alerting)
    return _alerter


def reset_runtime() -> None:
    """Test helper: clear cached config + alerter + store + bus."""
    global _config, _alerter
    _config = None
    _alerter = None
    reset_store()
    from triage.streaming import reset_bus

    reset_bus()


# ---------------------------------------------------------------------------
# OTLP span -> TraceEvent conversion
# ---------------------------------------------------------------------------

def _otlp_attr(attrs: list[dict[str, Any]], key: str) -> Any:
    """Extract a typed value from an OTLP attribute list."""
    for item in attrs:
        if item.get("key") == key:
            v = item.get("value", {})
            if "stringValue" in v:
                return str(v["stringValue"])
            if "intValue" in v:
                return int(v["intValue"])
            if "boolValue" in v:
                return bool(v["boolValue"])
            if "doubleValue" in v:
                return float(v["doubleValue"])
    return None


def _first_otlp_attr(attrs: list[dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = _otlp_attr(attrs, k)
        if v is not None:
            return v
    return None


def _span_to_event(span: dict[str, Any], resource_attrs: list[dict[str, Any]]) -> TraceEvent | None:
    """Convert a single OTLP span dict to a TraceEvent, returning None on failure."""
    attrs: list[dict[str, Any]] = span.get("attributes", [])
    all_attrs = resource_attrs + attrs

    agent_id = _first_otlp_attr(all_attrs, OTLP_AGENT_ID_KEYS)
    run_id = _first_otlp_attr(all_attrs, OTLP_RUN_ID_KEYS)
    turn = _otlp_attr(all_attrs, OTLP_TURN_KEY)
    tool_name = _first_otlp_attr(all_attrs, OTLP_TOOL_NAME_KEYS) or span.get("name", "unknown")
    succeeded = _otlp_attr(all_attrs, OTLP_SUCCEEDED_KEY)
    classification = _otlp_attr(all_attrs, OTLP_CLASSIFICATION_KEY)

    raw_div = _otlp_attr(all_attrs, OTLP_DIVERGENCE_KEY) or ""
    divergence_fields = [f.strip() for f in str(raw_div).split(",") if f.strip()]

    span_id = span.get("spanId") or span.get("traceId") or str(uuid.uuid4())
    event_id = hashlib.md5(span_id.encode()).hexdigest()[:12]  # noqa: S324

    ns = span.get("startTimeUnixNano")

    llm_ms = int(_otlp_attr(all_attrs, OTLP_LATENCY_LLM_KEY) or 0)
    tool_ms = int(_otlp_attr(all_attrs, OTLP_LATENCY_TOOL_KEY) or 0)
    end_ns = span.get("endTimeUnixNano")
    if end_ns and ns:
        total_ms = int((int(end_ns) - int(ns)) / 1e6)
    else:
        total_ms = llm_ms + tool_ms

    try:
        return TraceEvent(
            event_id=event_id,
            run_id=str(run_id or "otlp-default"),
            turn=int(turn) if turn is not None else 0,
            agent_id=str(agent_id or "unknown"),
            latency_ms=Latency(llm=llm_ms, tool=tool_ms, total=total_ms),
            action_taken=ActionTaken(
                tool_name=str(tool_name),
                tool_input={},
            ),
            action_succeeded=bool(succeeded) if succeeded is not None else True,
            divergence_fields=divergence_fields,
            divergence_age={},
            failure_classification=str(classification) if classification else None,
            usage=Usage(),
            message_context={},
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_DASHBOARD_HTML = (_TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
_SSE_KEEPALIVE_SECONDS = 15.0

app = FastAPI(
    title="agent-triage",
    description="Analyze agent trace failures and serve an interactive severity dashboard.",
    version="0.1.0",
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)


@app.post("/upload")
async def upload_traces(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Upload one or more NDJSON trace files and persist their events."""
    import tempfile

    paths: list[Path] = []
    tmp_dir = tempfile.mkdtemp()

    for uf in files:
        dest = Path(tmp_dir) / (uf.filename or "trace.ndjson")
        content = await uf.read()
        dest.write_bytes(content)
        paths.append(dest)

    result: LoadResult = load_files(paths)
    _add_events(result.events)

    return {
        "events_loaded": len(result.events),
        "errors": len(result.parse_errors),
        "parse_errors": result.parse_errors[:10],
    }


@app.post("/otlp/v1/traces")
async def receive_otlp(request: Request) -> dict[str, Any]:
    """OTLP HTTP/JSON trace receiver.

    Accepts the standard OTLP JSON format (``resourceSpans``) and converts
    each span to a ``TraceEvent`` using ``agent.*`` / ``run.*`` span attributes.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    events: list[TraceEvent] = []
    for rs in body.get("resourceSpans", []):
        resource_attrs: list[dict[str, Any]] = (
            rs.get("resource", {}).get("attributes", [])
        )
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                ev = _span_to_event(span, resource_attrs)
                if ev is not None:
                    events.append(ev)

    _add_events(events)
    return {"events_accepted": len(events)}


def _parse_since(since: str | None) -> datetime.datetime | None:
    if not since:
        return None
    try:
        return datetime.datetime.fromisoformat(since)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid 'since' timestamp (expected ISO-8601): {exc}",
        ) from exc


@app.get("/api/report")
async def api_report(
    top_n: int = 10,
    agent_id: str | None = None,
    run_id: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """Run the triage pipeline on stored events (optionally filtered) and return JSON."""
    store = get_store()
    events = store.filtered_events(
        agent_id=agent_id,
        run_id=run_id,
        since=_parse_since(since),
    )

    if not events:
        return {
            "total_runs": 0,
            "total_events": 0,
            "total_patterns": 0,
            "generated_at": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            "source_count": 0,
            "patterns": [],
            "alerts_fired": [],
            "filters": {"agent_id": agent_id, "run_id": run_id, "since": since},
        }

    patterns, scored, total_runs = _compute_scored(events)
    # Skip alerts when the result reflects a user filter — alerting should
    # only fire on the unfiltered "real" view, not someone exploring slices.
    alerts_fired: list[dict[str, object]] = []
    if not (agent_id or run_id or since):
        alerts_fired = await get_alerter().maybe_alert(scored)

    pattern_data: list[dict[str, Any]] = []
    for rank, sp in enumerate(scored[:top_n], start=1):
        p = sp.pattern
        pattern_data.append(
            {
                "rank": rank,
                "pattern_id": p.pattern_id,
                "display_name": p.display_name(),
                "agent_id": p.agent_id,
                "tool_name": p.tool_name,
                "failure_classification": p.failure_classification,
                "frequency": p.frequency,
                "run_count": len(p.run_ids),
                "severity_score": sp.severity_score,
                "recovery_rate": sp.recovery_rate,
                "final_score": sp.final_score,
                "suggested_action": NEXT_ACTIONS.get(
                    p.failure_classification, NEXT_ACTIONS["unclassified"]
                ),
                "classification_label": CLASSIFICATION_LABELS.get(
                    p.failure_classification, p.failure_classification
                ),
                "ai_narrative": None,
            }
        )

    return {
        "total_runs": total_runs,
        "total_events": len(events),
        "total_patterns": len(patterns),
        "generated_at": datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "source_count": total_runs,
        "patterns": pattern_data,
        "alerts_fired": alerts_fired,
        "filters": {"agent_id": agent_id, "run_id": run_id, "since": since},
    }


@app.get("/api/report/markdown")
async def api_report_markdown(top_n: int = 3) -> JSONResponse:
    """Return the full triage report as a Markdown string."""
    events = get_store().all_events()
    if not events:
        return JSONResponse({"markdown": "No data loaded yet."})

    patterns, scored, total_runs = _compute_scored(events)
    md = build_report(
        scored=scored,
        total_runs=total_runs,
        total_patterns=len(patterns),
        source_files=["dashboard-upload"],
        top_n=top_n,
    )
    return JSONResponse({"markdown": md})


@app.get("/api/trends")
async def api_trends(days: int = 7) -> dict[str, Any]:
    """Per-pattern, per-day failure counts for the last ``days`` days."""
    store = get_store()
    return {
        "days": days,
        "by_classification": store.daily_severity_counts(days),
        "by_pattern": store.pattern_daily_counts(days),
    }


@app.get("/api/stream", include_in_schema=False)
async def stream(request: Request) -> StreamingResponse:
    """Server-Sent Events endpoint that pushes 'new events' notifications.

    Lets the dashboard refresh automatically when fresh OTLP spans arrive
    instead of requiring the user to click Refresh. The keep-alive comment
    every ``_SSE_KEEPALIVE_SECONDS`` doubles as a disconnect-detection tick
    so a quiet bus never pins a dangling subscriber forever.
    """
    bus = get_bus()

    async def event_generator() -> Any:
        yield "event: ping\ndata: connected\n\n"
        sub = bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        sub.__anext__(), timeout=_SSE_KEEPALIVE_SECONDS
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"  # SSE comment line, ignored by clients
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        finally:
            await sub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/events")
async def clear_events() -> dict[str, str]:
    """Clear all stored events."""
    _clear_store()
    return {"status": "cleared"}


@app.get("/api/events/count")
async def event_count() -> dict[str, int]:
    return {"count": get_store().count()}


@app.get("/api/filters")
async def api_filters() -> dict[str, list[str]]:
    """Return the distinct agent_ids and run_ids available for filtering."""
    store = get_store()
    return {
        "agents": store.distinct_agents(),
        "runs": store.distinct_runs(),
    }


@app.get("/api/config")
async def api_config() -> dict[str, Any]:
    """Expose the active configuration (sanitised — no secrets)."""
    cfg = get_config()
    return {
        "scoring": {
            "recovery_window": cfg.scoring.recovery_window,
            "no_recovery_multiplier": cfg.scoring.no_recovery_multiplier,
            "frequency_weight": cfg.scoring.frequency_weight,
            "severity_weight": cfg.scoring.severity_weight,
            "weights": cfg.scoring.weights,
        },
        "storage": {"db_path": cfg.storage.db_path},
        "alerting": {
            # Don't leak the webhook URL — just whether one is configured.
            "enabled": bool(cfg.alerting.webhook_url),
            "threshold": cfg.alerting.threshold,
            "cooldown_seconds": cfg.alerting.cooldown_seconds,
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host.")
@click.option("--port", default=8000, show_default=True, help="Bind port.")
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on file changes.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a triage.toml config file.",
)
@click.option(
    "--db",
    "db_path",
    default=None,
    help="SQLite database path (overrides config; defaults to in-memory).",
)
def serve(host: str, port: int, reload: bool, config_path: Path | None, db_path: str | None) -> None:
    """Start the agent-triage web dashboard.

    Open http://localhost:8000 in your browser to see the dashboard.
    Upload NDJSON trace files via the UI or POST to /otlp/v1/traces
    from any OpenTelemetry-instrumented agent.
    """
    if config_path is not None:
        os.environ["TRIAGE_CONFIG"] = str(config_path)
        click.echo(f"Using config: {config_path}", err=True)
    if db_path is not None:
        os.environ["TRIAGE_DB_PATH"] = db_path
        click.echo(f"Using database: {db_path}", err=True)

    click.echo(f"Starting agent-triage dashboard on http://{host}:{port}", err=True)
    uvicorn.run(
        "triage.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )
