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
    get_store().add_events(events)
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


def _span_to_event(span: dict[str, Any], resource_attrs: list[dict[str, Any]]) -> TraceEvent | None:
    """Convert a single OTLP span dict to a TraceEvent, returning None on failure."""
    attrs: list[dict[str, Any]] = span.get("attributes", [])
    all_attrs = resource_attrs + attrs

    agent_id = _otlp_attr(all_attrs, "agent.id") or _otlp_attr(all_attrs, "agent_id")
    run_id = _otlp_attr(all_attrs, "run.id") or _otlp_attr(all_attrs, "run_id")
    turn = _otlp_attr(all_attrs, "turn")
    tool_name = (
        _otlp_attr(all_attrs, "action.tool")
        or _otlp_attr(all_attrs, "tool_name")
        or span.get("name", "unknown")
    )
    succeeded = _otlp_attr(all_attrs, "action.succeeded")
    classification = _otlp_attr(all_attrs, "failure.classification")

    raw_div = _otlp_attr(all_attrs, "divergence.fields") or ""
    divergence_fields = [f.strip() for f in str(raw_div).split(",") if f.strip()]

    span_id = span.get("spanId") or span.get("traceId") or str(uuid.uuid4())
    event_id = hashlib.md5(span_id.encode()).hexdigest()[:12]  # noqa: S324

    ns = span.get("startTimeUnixNano")

    llm_ms = int(_otlp_attr(all_attrs, "latency.llm_ms") or 0)
    tool_ms = int(_otlp_attr(all_attrs, "latency.tool_ms") or 0)
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
# Dashboard HTML (embedded — no build step required)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Agent Triage Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    body { background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.5rem; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
    .bar-fill { height: 8px; border-radius: 4px; background: #3b82f6; transition: width 0.4s; }
    .bar-track { height: 8px; border-radius: 4px; background: #334155; }
    .score-ring { width: 56px; height: 56px; }
  </style>
</head>
<body class="p-6 md:p-10">

  <!-- Header -->
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-2xl font-bold tracking-tight">Agent Triage Dashboard</h1>
      <p id="subtitle" class="text-slate-400 text-sm mt-1">Loading...</p>
    </div>
    <div class="flex gap-3">
      <button onclick="uploadModal()" class="px-4 py-2 text-sm bg-slate-700 hover:bg-slate-600 rounded-lg font-medium">
        Upload Traces
      </button>
      <button onclick="refresh()" class="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg font-medium">
        Refresh
      </button>
    </div>
  </div>

  <!-- Summary row -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8" id="stats-row"></div>

  <!-- Main content -->
  <div class="grid grid-cols-1 xl:grid-cols-3 gap-6">
    <!-- Pattern cards -->
    <div class="xl:col-span-2 space-y-4" id="patterns-col">
      <p class="text-slate-400">Loading incident patterns...</p>
    </div>
    <!-- Chart column -->
    <div class="space-y-6">
      <div class="card">
        <h2 class="font-semibold text-slate-300 mb-4 text-sm uppercase tracking-wide">Severity by Pattern</h2>
        <canvas id="scoreChart" height="300"></canvas>
        <p id="chart-empty" class="text-slate-500 text-sm mt-4 hidden">No data yet. Upload a trace file to begin.</p>
      </div>
      <div class="card">
        <h2 class="font-semibold text-slate-300 mb-4 text-sm uppercase tracking-wide">7-Day Failure Trend</h2>
        <canvas id="trendChart" height="220"></canvas>
        <p id="trend-empty" class="text-slate-500 text-sm mt-4 hidden">No timestamped events in the last 7 days yet.</p>
      </div>
    </div>
  </div>

  <!-- Upload modal (hidden) -->
  <div id="modal" class="fixed inset-0 bg-black/60 flex items-center justify-center hidden z-50">
    <div class="card w-full max-w-md">
      <h2 class="font-semibold mb-4">Upload NDJSON Trace Files</h2>
      <input type="file" id="file-input" accept=".ndjson,.jsonl" multiple
             class="block w-full text-sm text-slate-400 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:bg-blue-600 file:text-white hover:file:bg-blue-500 mb-4" />
      <div class="flex gap-3 justify-end">
        <button onclick="closeModal()" class="px-4 py-2 text-sm bg-slate-600 hover:bg-slate-500 rounded-lg">Cancel</button>
        <button onclick="doUpload()" class="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg font-medium">Upload &amp; Analyze</button>
      </div>
      <p id="upload-msg" class="text-sm mt-3 text-slate-400"></p>
    </div>
  </div>

<script>
const CLASSIFICATION_COLOR = {
  coordination_failure: '#f59e0b',
  agent_error: '#ef4444',
  information_lag: '#3b82f6',
  environment_constraint: '#8b5cf6',
  unclassified: '#6b7280',
};

let chartInstance = null;

function stat(label, value, sub) {
  return `<div class="card text-center">
    <div class="text-3xl font-bold text-white">${value}</div>
    <div class="text-slate-400 text-sm mt-1">${label}</div>
    ${sub ? `<div class="text-slate-500 text-xs mt-1">${sub}</div>` : ''}
  </div>`;
}

function recoveryBar(rate) {
  const pct = Math.floor(Math.min(rate, 1) * 100);
  const fill = Math.max(0, Math.floor(Math.min(rate, 1) * 100));
  return `<div class="bar-track mt-1"><div class="bar-fill" style="width:${fill}%"></div></div>
          <span class="text-xs text-slate-400">${pct}% recovered within window</span>`;
}

function badge(cls) {
  const color = CLASSIFICATION_COLOR[cls] || '#6b7280';
  return `<span class="badge" style="background:${color}20;color:${color}">${cls.replace(/_/g,' ')}</span>`;
}

function patternCard(p, rank) {
  const scoreColor = p.final_score > 10 ? '#ef4444' : p.final_score > 6 ? '#f59e0b' : '#22c55e';
  return `
  <div class="card">
    <div class="flex items-start justify-between gap-4">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-2 flex-wrap">
          <span class="text-slate-500 text-sm font-mono">#${rank}</span>
          ${badge(p.failure_classification)}
          <span class="text-slate-500 text-xs">${p.agent_id} &middot; ${p.tool_name}</span>
        </div>
        <h3 class="font-semibold text-white text-sm mb-3 truncate" title="${p.display_name}">${p.display_name}</h3>
        <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm mb-3">
          <div><span class="text-slate-400">Frequency</span><br/><span class="text-white font-medium">${p.frequency} event(s)</span></div>
          <div><span class="text-slate-400">Runs affected</span><br/><span class="text-white font-medium">${p.run_count}</span></div>
          <div><span class="text-slate-400">Severity score</span><br/><span class="text-white font-medium">${p.severity_score.toFixed(2)}</span></div>
          <div><span class="text-slate-400">Final score</span><br/><span class="font-bold" style="color:${scoreColor}">${p.final_score.toFixed(2)}</span></div>
        </div>
        <div class="mb-3">${recoveryBar(p.recovery_rate)}</div>
        ${p.ai_narrative ? `
        <div class="bg-slate-900 rounded-lg p-3 mb-3 border border-slate-700">
          <p class="text-xs text-blue-400 font-semibold uppercase mb-1">AI Root-Cause Analysis</p>
          <p class="text-slate-300 text-sm leading-relaxed">${p.ai_narrative}</p>
        </div>` : ''}
        <div class="text-slate-400 text-xs border-t border-slate-700 pt-3">
          <span class="font-semibold text-slate-300">Suggested action: </span>${p.suggested_action}
        </div>
      </div>
      <div class="flex-shrink-0 text-right">
        <div class="text-2xl font-bold" style="color:${scoreColor}">${p.final_score.toFixed(1)}</div>
        <div class="text-slate-500 text-xs">score</div>
      </div>
    </div>
  </div>`;
}

function renderChart(patterns) {
  const ctx = document.getElementById('scoreChart').getContext('2d');
  if (chartInstance) chartInstance.destroy();
  if (!patterns.length) {
    document.getElementById('chart-empty').classList.remove('hidden');
    return;
  }
  document.getElementById('chart-empty').classList.add('hidden');
  const labels = patterns.map(p => `#${p.rank} ${p.agent_id}/${p.tool_name}`);
  const scores = patterns.map(p => p.final_score);
  const colors = patterns.map(p => CLASSIFICATION_COLOR[p.failure_classification] || '#6b7280');
  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Final Score',
        data: scores,
        backgroundColor: colors.map(c => c + '99'),
        borderColor: colors,
        borderWidth: 2,
        borderRadius: 6,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } },
        y: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 11 } } }
      }
    }
  });
}

async function refresh() {
  try {
    const res = await fetch('/api/report');
    const data = await res.json();
    if (data.error) { showError(data.error); return; }

    document.getElementById('subtitle').textContent =
      `Generated ${data.generated_at} · ${data.source_count} source(s)`;

    const topScore = data.patterns[0]?.final_score ?? 0;
    document.getElementById('stats-row').innerHTML = [
      stat('Runs Analyzed', data.total_runs),
      stat('Incident Patterns', data.total_patterns),
      stat('Top Severity', topScore.toFixed(2), 'out of 15.00'),
      stat('Events Stored', data.total_events),
    ].join('');

    const col = document.getElementById('patterns-col');
    if (!data.patterns.length) {
      col.innerHTML = '<p class="text-slate-400">No incidents detected. Upload a trace file to begin.</p>';
    } else {
      col.innerHTML = data.patterns.map(p => patternCard(p, p.rank)).join('');
    }
    renderChart(data.patterns);
  } catch(e) {
    showError('Failed to load report: ' + e.message);
  }
}

function showError(msg) {
  document.getElementById('patterns-col').innerHTML =
    `<div class="card border-red-500/30 text-red-400">${msg}</div>`;
}

function uploadModal() {
  document.getElementById('modal').classList.remove('hidden');
  document.getElementById('upload-msg').textContent = '';
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
}

async function doUpload() {
  const input = document.getElementById('file-input');
  if (!input.files.length) return;
  const form = new FormData();
  for (const f of input.files) form.append('files', f);
  document.getElementById('upload-msg').textContent = 'Uploading...';
  try {
    const res = await fetch('/upload', { method: 'POST', body: form });
    const data = await res.json();
    document.getElementById('upload-msg').textContent =
      `Loaded ${data.events_loaded} event(s). ${data.errors} parse error(s).`;
    if (data.events_loaded > 0) { closeModal(); refresh(); }
  } catch(e) {
    document.getElementById('upload-msg').textContent = 'Upload failed: ' + e.message;
  }
}

// --- Time-series trends ---------------------------------------------------
let trendChart = null;

function fillDayGaps(byCls, days) {
  const today = new Date();
  const dates = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    dates.push(d.toISOString().slice(0, 10));
  }
  const byClsByDate = {};
  for (const row of byCls) {
    if (!byClsByDate[row.classification]) byClsByDate[row.classification] = {};
    byClsByDate[row.classification][row.date] = row.count;
  }
  const datasets = Object.keys(byClsByDate).map(cls => ({
    label: cls,
    data: dates.map(d => byClsByDate[cls][d] ?? 0),
    borderColor: CLASSIFICATION_COLOR[cls] || '#6b7280',
    backgroundColor: (CLASSIFICATION_COLOR[cls] || '#6b7280') + '33',
    tension: 0.3,
    fill: true,
  }));
  return { dates, datasets };
}

async function refreshTrends() {
  try {
    const res = await fetch('/api/trends?days=7');
    const data = await res.json();
    const ctx = document.getElementById('trendChart').getContext('2d');
    const { dates, datasets } = fillDayGaps(data.by_classification, data.days);
    if (trendChart) trendChart.destroy();
    if (!datasets.length) {
      document.getElementById('trend-empty').classList.remove('hidden');
      return;
    }
    document.getElementById('trend-empty').classList.add('hidden');
    trendChart = new Chart(ctx, {
      type: 'line',
      data: { labels: dates, datasets },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } },
        scales: {
          x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
          y: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' }, beginAtZero: true }
        }
      }
    });
  } catch(e) { console.warn('trends fetch failed', e); }
}

// --- Server-Sent Events: auto-refresh on new data ------------------------
function connectStream() {
  const es = new EventSource('/api/stream');
  es.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data);
      if (data.type === 'events_added') {
        clearTimeout(window._refreshTimer);
        window._refreshTimer = setTimeout(() => { refresh(); refreshTrends(); }, 500);
      }
    } catch(e) { /* ignore non-JSON pings */ }
  };
  es.onerror = () => { es.close(); setTimeout(connectStream, 5000); };
}

refresh();
refreshTrends();
connectStream();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

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


@app.get("/api/report")
async def api_report(top_n: int = 10) -> dict[str, Any]:
    """Run the triage pipeline on stored events and return JSON."""
    store = get_store()
    events = store.all_events()

    if not events:
        return {
            "total_runs": 0,
            "total_events": 0,
            "total_patterns": 0,
            "generated_at": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            "source_count": 0,
            "patterns": [],
            "alerts_fired": [],
        }

    patterns, scored, total_runs = _compute_scored(events)
    alerts_fired = get_alerter().maybe_alert(scored)

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
    instead of requiring the user to click Refresh.
    """
    bus = get_bus()

    async def event_generator() -> Any:
        # Initial heartbeat so the client knows the connection opened.
        yield "event: ping\ndata: connected\n\n"
        try:
            async for event in bus.subscribe():
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            pass

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
