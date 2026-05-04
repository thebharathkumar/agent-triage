"""End-to-end OTLP example.

Emits a realistic sequence of agent spans (a 2-agent dungeon-navigation run
with several failure patterns) to a running ``triage-serve`` instance.

Run it like so::

    # In one terminal:
    triage-serve

    # In another:
    python examples/emit_otlp.py

Then open http://localhost:8000 to see the dashboard light up.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request

ENDPOINT = "http://localhost:8000/otlp/v1/traces"


def _attr(key: str, value: object) -> dict:
    """Build an OTLP attribute KV in JSON form."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _span(
    *,
    turn: int,
    agent: str,
    run: str,
    tool: str,
    succeeded: bool,
    failure: str | None = None,
    divergence: str = "",
    duration_ms: int = 50,
) -> dict:
    """Build one OTLP span representing a single agent action."""
    start_ns = time.time_ns()
    end_ns = start_ns + duration_ms * 1_000_000
    attrs = [
        _attr("agent.id", agent),
        _attr("run.id", run),
        _attr("turn", turn),
        _attr("action.tool", tool),
        _attr("action.succeeded", succeeded),
    ]
    if failure:
        attrs.append(_attr("failure.classification", failure))
    if divergence:
        attrs.append(_attr("divergence.fields", divergence))

    return {
        "traceId": f"{run}-trace-{turn:04x}",
        "spanId": f"{agent}-{turn:04x}",
        "name": f"agent.{tool}",
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attrs,
        "status": {"code": 1 if succeeded else 2},
    }


def build_demo_spans() -> list[dict]:
    """Compose a realistic 2-agent failure scenario."""
    rng = random.Random(42)
    spans: list[dict] = []

    # Run 1: navigator gets stuck in a position-divergence loop
    for turn in range(15):
        is_failure = turn in {3, 4, 5, 8, 9, 12}
        spans.append(
            _span(
                turn=turn,
                agent="navigator",
                run="run-001",
                tool="move",
                succeeded=not is_failure,
                failure="agent_error" if is_failure else None,
                divergence="position" if is_failure else "",
                duration_ms=rng.randint(40, 120),
            )
        )

    # Run 1: scout has occasional coordination failures with navigator
    for turn in range(15):
        is_failure = turn in {6, 11}
        spans.append(
            _span(
                turn=turn,
                agent="scout",
                run="run-001",
                tool="report",
                succeeded=not is_failure,
                failure="coordination_failure" if is_failure else None,
                divergence="last_known_position" if is_failure else "",
                duration_ms=rng.randint(30, 80),
            )
        )

    # Run 2: navigator hits the same position-divergence pattern (proves it's chronic)
    for turn in range(12):
        is_failure = turn in {2, 3, 7}
        spans.append(
            _span(
                turn=turn,
                agent="navigator",
                run="run-002",
                tool="move",
                succeeded=not is_failure,
                failure="agent_error" if is_failure else None,
                divergence="position" if is_failure else "",
                duration_ms=rng.randint(40, 120),
            )
        )

    # Run 2: scout hits an environment_constraint when scanning beyond map bounds
    spans.append(
        _span(
            turn=8,
            agent="scout",
            run="run-002",
            tool="scan",
            succeeded=False,
            failure="environment_constraint",
            duration_ms=20,
        )
    )

    return spans


def main() -> int:
    spans = build_demo_spans()

    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", "dungeon-agent-demo"),
                        _attr("service.version", "0.1.0"),
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "agent-triage-demo"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"Sending {len(spans)} spans to {ENDPOINT}...")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            print(f"  OK ({resp.status}): {resp.read().decode()}")
    except urllib.error.URLError as exc:
        print(f"  FAILED: {exc}")
        print("  Is triage-serve running? Start it with: triage-serve")
        return 1

    print("\nDone. Open http://localhost:8000 to see the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
