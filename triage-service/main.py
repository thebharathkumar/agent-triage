"""main.py - Triage Service.

FastAPI web service that wraps the triage scoring engine and exposes:
  GET /          - Triage UI (HTML)
  GET /health    - Liveness probe
  GET /report    - Full severity report (markdown)
  GET /api/report - Scored patterns (JSON)

A background task regenerates the report every TRIAGE_REFRESH_SECONDS seconds.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# ---------------------------------------------------------------------------
# Path setup - allow running without installing the triage package
# ---------------------------------------------------------------------------
_here = Path(__file__).parent
_src = _here / "src"
if _src.exists() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from triage.grouper import group_events
from triage.loader import load_files
from triage.reporter import build_report
from triage.scorer import score_patterns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [triage-service] %(levelname)s %(message)s",
)
log = logging.getLogger("triage.service")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
RUNS_DIR = Path(os.getenv("TRIAGE_RUNS_DIR", "./runs"))
SPANS_DIR = Path(os.getenv("TRIAGE_SPANS_DIR", ""))
REFRESH_SECONDS = int(os.getenv("TRIAGE_REFRESH_SECONDS", "60"))
TOP_N = int(os.getenv("TRIAGE_TOP_N", "3"))

# ---------------------------------------------------------------------------
# State - shared across requests, updated by background task
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "report_md": None,
    "report_json": None,
    "last_updated": None,
    "total_runs": 0,
    "total_patterns": 0,
    "source_files": [],
    "error": None,
}


def _collect_input_files() -> list[Path]:
    """Glob all NDJSON and OTLP JSONL files from configured directories."""
    files: list[Path] = []
    for directory in [RUNS_DIR, SPANS_DIR]:
        if not directory or not directory.exists():
            continue
        for pattern in ("**/*.ndjson", "**/*.jsonl"):
            files.extend(sorted(directory.glob(pattern)))
    return files


def _run_triage() -> None:
    """Core scoring pipeline. Updates _state in place."""
    files = _collect_input_files()
    if not files:
        _state["error"] = (
            f"No trace files found in {RUNS_DIR} or {SPANS_DIR}. "
            "Drop .ndjson files into the runs/ directory to get started."
        )
        _state["report_md"] = _state["error"]
        _state["report_json"] = []
        _state["total_runs"] = 0
        _state["total_patterns"] = 0
        _state["source_files"] = []
        _state["last_updated"] = datetime.datetime.now(tz=datetime.timezone.utc)
        return

    result = load_files(files)
    for err in result.parse_errors:
        log.warning("Parse error: %s", err)

    patterns = group_events(result.events)
    run_ids = {e.run_id for e in result.events}
    scored = score_patterns(patterns, result.events, len(run_ids))

    report_md = build_report(
        scored=scored,
        total_runs=len(run_ids),
        total_patterns=len(patterns),
        source_files=[str(f) for f in files],
        top_n=TOP_N,
    )

    report_json = [
        {
            "rank": i + 1,
            "pattern_name": sp.pattern.display_name(),
            "agent_id": sp.pattern.agent_id,
            "tool_name": sp.pattern.tool_name,
            "failure_classification": sp.pattern.failure_classification,
            "frequency": sp.pattern.frequency,
            "run_count": len(sp.pattern.run_ids),
            "severity_score": round(sp.severity_score, 2),
            "frequency_score": round(sp.frequency_score, 2),
            "recovery_rate": round(sp.recovery_rate, 3),
            "final_score": round(sp.final_score, 2),
            "divergence_fields": list(sp.pattern.divergence_fields),
        }
        for i, sp in enumerate(scored[:TOP_N])
    ]

    _state["report_md"] = report_md
    _state["report_json"] = report_json
    _state["last_updated"] = datetime.datetime.now(tz=datetime.timezone.utc)
    _state["total_runs"] = len(run_ids)
    _state["total_patterns"] = len(patterns)
    _state["source_files"] = [str(f) for f in files]
    _state["error"] = None

    log.info(
        "Triage complete: %d runs, %d patterns, top %d scored",
        len(run_ids),
        len(patterns),
        min(TOP_N, len(patterns)),
    )


async def _refresh_loop() -> None:
    """Background task: re-runs triage on the configured interval."""
    while True:
        try:
            _run_triage()
        except Exception as exc:
            log.error("Triage run failed: %s", exc)
            _state["error"] = str(exc)
        await asyncio.sleep(REFRESH_SECONDS)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Triage Observability Service",
    description="Severity-ranked morning triage for multi-agent trace data",
    version="0.1.0",
)


@app.on_event("startup")
async def on_startup() -> None:
    log.info(
        "Triage service starting - runs_dir=%s spans_dir=%s refresh=%ds top_n=%d",
        RUNS_DIR,
        SPANS_DIR,
        REFRESH_SECONDS,
        TOP_N,
    )
    # Run once immediately so the first request is not empty
    try:
        _run_triage()
    except Exception as exc:
        log.error("Initial triage run failed: %s", exc)
        _state["error"] = str(exc)
    # Start background refresh
    asyncio.create_task(_refresh_loop())


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "last_updated": _state["last_updated"].isoformat() if _state["last_updated"] else None,
        "total_runs": _state["total_runs"],
        "total_patterns": _state["total_patterns"],
    }


@app.get("/report", response_class=PlainTextResponse)
async def report_markdown() -> str:
    if _state["report_md"] is None:
        return "No report available yet. Check /health for status."
    return _state["report_md"]


@app.get("/api/report")
async def report_json() -> JSONResponse:
    last = _state["last_updated"]
    return JSONResponse(
        content={
            "generated_at": last.isoformat() if last else None,
            "total_runs": _state["total_runs"],
            "total_patterns": _state["total_patterns"],
            "top_n": TOP_N,
            "refresh_seconds": REFRESH_SECONDS,
            "source_files": _state["source_files"],
            "incidents": _state["report_json"] or [],
            "error": _state["error"],
        }
    )


@app.get("/", response_class=HTMLResponse)
async def ui() -> str:
    html_path = Path(__file__).parent / "ui.html"
    return html_path.read_text(encoding="utf-8")
