# agent-triage

> **Rank multi-agent failures by what actually matters — not by what showed up first in the log.**

[![CI](https://github.com/thebharathkumar/agent-triage/actions/workflows/ci.yml/badge.svg)](https://github.com/thebharathkumar/agent-triage/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-triage.svg)](https://pypi.org/project/agent-triage/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen.svg)](#development)
[![mypy: strict](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)

`agent-triage` ingests trace files from multi-agent systems (NDJSON or OpenTelemetry) and produces a ranked "what actually needs your attention this morning" severity report. It ships a CLI, a web dashboard, an OTLP receiver, and optional LLM-generated root-cause narratives.

<!-- Replace this with a real screenshot once you've taken one: -->
<!-- ![Dashboard preview](docs/dashboard.png) -->

---

## Why this exists

When multi-agent systems run at scale, they generate thousands of trace events per day. Most observability tools dump everything into a dashboard and leave triage to the human. That is the wrong default.

The signal you actually need at 9 AM is not "here are 847 events from last night." It's: **"here are the three failure patterns that matter, ranked by how bad they are and whether the agents recovered."**

`agent-triage` is that tool. It reads agent trace data (NDJSON natively, OpenTelemetry spans via the built-in adapter, and any custom format you can plug in), clusters events into incident patterns, scores each pattern across three dimensions (frequency, severity, recovery), and prints a ranked short list with plain-English explanations and a suggested next action for each. It also has a `compare` mode for diffing two batches.

---

## Features

- **CLI** — pipe NDJSON traces in, get a markdown morning report out
- **Web dashboard** — interactive UI with severity charts, **7-day trend lines**, and **live updates via Server-Sent Events**
- **OTLP receiver** — accept OpenTelemetry spans directly from production agents
- **LLM root-cause analysis** — optional Claude Haiku 4.5-generated narratives with prompt caching to keep cost low
- **Persistent SQLite storage** — trace data survives restarts; supports time-series queries
- **Configurable scoring** — tune severity weights, recovery window, and composite formula via `triage.toml`
- **Webhook alerting** — fire Slack-compatible notifications when a pattern crosses your severity threshold
- **Docker-ready** — `docker compose up` and you have a dashboard
- **PyPI-published** — `pip install agent-triage`
- **Production quality** — 220+ tests, 93%+ coverage, mypy strict, ruff clean

---

## Quick start

### Option 1: Docker (recommended for trying it out)

```bash
git clone https://github.com/thebharathkumar/agent-triage.git
cd agent-triage
docker compose up
```

Then open <http://localhost:8000> and upload a trace file.

### Option 2: pip

```bash
pip install "agent-triage[server,ai]"

# CLI
triage report runs/phase4/events_seed42.ndjson

# Web dashboard
triage-serve
```

### Option 3: Stream OTLP from a running agent

```bash
# Start the receiver
triage-serve

# In another shell, send some demo spans
python examples/emit_otlp.py
```

The dashboard at <http://localhost:8000> will populate in real time.

---

## CLI commands

### `triage report` — morning severity report

```bash
triage report runs/phase4/*.ndjson --top 5
```

Add `--ai-analysis` to enrich the top incidents with LLM-generated root-cause narratives:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
triage report runs/phase4/*.ndjson --ai-analysis
```

Write to a file instead of stdout:

```bash
triage report runs/phase4/events_seed42.ndjson --output examples/seed42-report.md
```

See [`examples/seed42-report.md`](examples/seed42-report.md) for a real generated report.

### `triage compare` — diff two batches

`triage compare` answers a different question than the morning report:
not "what should I look at" but "did the change between these two
batches make agent behavior better or worse, and where". Useful when
diffing pre- and post-architecture-change runs, A/B prompts, or two
model versions.

```bash
triage compare runs/before.ndjson runs/after.ndjson
```

Each argument can be a single `.ndjson` file or a directory; passing a
directory loads every `.ndjson` in it (non-recursive), so the command
is symmetric across CI shapes:

```bash
triage compare runs/before/ runs/after/
```

**Produces:**

- a **Score Summary** panel — pattern count, total failure events,
  unrecovered events, coordination-failure events, top final score,
  and mean final score, with deltas
- incident **frequency deltas** per classification (`down 39%`, `new`,
  `resolved`, `stable`, etc.)
- **recovery latency changes** — median turns-to-first-success for
  each classification, before vs after
- **unrecovered-count changes** — number of failures that did not
  recover within the window
- **newly emerging patterns** and **resolved patterns**
- **persisting patterns** — signatures present in both, with their
  before/after counts

Changes based on small samples (fewer than 5 occurrences on the larger
side) are tagged `(tentative)` so a thin-evidence delta is not read as
a verified one. See
[examples/compare-before-after.md](examples/compare-before-after.md).

### See all options

```bash
triage --help
triage report --help
triage compare --help
```

---

## Trace formats and adapters

`triage` is plugin-shaped on the input side. Two adapters ship in the box:

| Adapter   | Extensions          | Notes |
|-----------|---------------------|-------|
| `ndjson`  | `.ndjson`, `.jsonl` | One TraceEvent per line. The native schema documented below. |
| `otel`    | `.json`             | Minimal OpenTelemetry-spans adapter; maps `span.name` → `tool_name`, `status.code` → `action_succeeded`, `attributes["agent.id"]` → `agent_id`, `attributes["failure_classification"]` → `failure_classification`, `attributes["divergence_fields"]` → `divergence_fields`. |

The format is auto-detected by extension and can be overridden:

```bash
triage report --format otel spans.json
triage compare --format ndjson before.weird after.weird
```

### Adding your own adapter

The `TraceAdapter` protocol in `src/triage/adapters.py` is intentionally
small:

```python
class TraceAdapter(Protocol):
    name: ClassVar[str]
    extensions: ClassVar[tuple[str, ...]]
    def load(self, path: Path) -> tuple[list[TraceEvent], list[str]]:
        ...
```

Any class that satisfies it can be added to `ADAPTERS` (or registered
externally) and immediately works in both `report` and `compare`. The
adapter only owns parsing — scoring, grouping, comparison, and
reporting are agnostic to the source format.

---

## Dashboard

```bash
triage-serve --host 0.0.0.0 --port 8000
```

The dashboard supports drag-and-drop NDJSON upload, live severity charts, 7-day trend lines, and per-incident AI narratives.

---

## OTLP receiver — for production agents

Configure your agent to send spans to `http://your-host:8000/otlp/v1/traces` using the standard OTLP/HTTP JSON format. Map these span attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `agent.id` | string | Which agent acted |
| `run.id` | string | Which run this event belongs to |
| `turn` | int | Turn number within the run |
| `action.tool` | string | Tool/action name |
| `action.succeeded` | bool | Whether the action succeeded |
| `failure.classification` | string | One of: `coordination_failure`, `agent_error`, `information_lag`, `environment_constraint` |
| `divergence.fields` | string | Comma-separated belief-divergence fields |

See [`examples/emit_otlp.py`](examples/emit_otlp.py) for a working end-to-end example.

---

## Configuration

Create a `triage.toml` to override scoring weights, persistence, and alerting (all sections optional):

```toml
[scoring]
recovery_window = 5
no_recovery_multiplier = 2.0

[scoring.weights]
coordination_failure = 1.0
agent_error = 0.8

[storage]
db_path = "./triage.db"   # use ":memory:" for ephemeral runs

[alerting]
webhook_url = "https://hooks.slack.com/services/T0000/B0000/XXX"
threshold = 8.0           # fire when final_score crosses this
cooldown_seconds = 1800   # don't refire same pattern within this window
```

Then pass it to either entry point:

```bash
triage runs/*.ndjson --config triage.toml
triage-serve --config triage.toml --db ./triage.db
```

A full annotated example is at [`triage.example.toml`](triage.example.toml).

---

## Live updates

The dashboard subscribes to a Server-Sent Events stream at `/api/stream`. Whenever new spans arrive (via upload or OTLP), connected browsers refresh automatically — no polling, no manual reload. The 7-day trend chart is also re-rendered, so you can see severity climb in real time during an incident.

---

## Webhook alerts

When `[alerting]` is configured, every `/api/report` evaluation checks for patterns whose `final_score` exceeds `threshold` and POSTs a Slack-compatible JSON payload to your webhook. A per-pattern cooldown prevents flooding during a continuous incident:

```json
{
  "text": ":rotating_light: Triage alert — score 12.40\n>*[navigator] move / agent_error / position*\n>...",
  "pattern_id": "navigator-move-agent_error",
  "agent_id": "navigator",
  "tool_name": "move",
  "classification": "agent_error",
  "final_score": 12.4,
  "severity_score": 14.0,
  "recovery_rate": 0.0
}
```

Compatible with Slack incoming webhooks, Discord webhooks, PagerDuty Events API v2 (with minor adapter), and any HTTP endpoint that accepts JSON.

---

## Architecture

```
                                  ┌─────────────────┐
   NDJSON file ─────────┐         │                 │
                        ├──►──────┤                 │
   OTLP/HTTP spans ─────┘         │   Loader        │
                                  │   ↓             │
                                  │   Grouper       │
                                  │   ↓             │
                                  │   Scorer        │──►── Markdown report (CLI)
                                  │   ↓             │
                                  │   Reporter      │──►── JSON API (dashboard)
                                  │                 │
                                  └─────────────────┘
                                          │
                                          ↓ (optional)
                                  ┌─────────────────┐
                                  │  Claude Haiku   │
                                  │  (cached)       │──►── Root-cause narrative
                                  └─────────────────┘
```

| Module | Responsibility |
|--------|---------------|
| `loader.py` | Parse NDJSON / OTLP into validated `TraceEvent` objects |
| `grouper.py` | Cluster events into `IncidentPattern` buckets by signature |
| `scorer.py` | Score patterns by frequency, severity, and recovery rate |
| `reporter.py` | Render the morning markdown report |
| `analyst.py` | Optional Claude-generated root-cause narratives |
| `store.py` | SQLite-backed persistent event store + time-series queries |
| `streaming.py` | In-process pub/sub for Server-Sent Events |
| `alerting.py` | Threshold-based webhook alerter with per-pattern cooldown |
| `config.py` | TOML config loader (`triage.toml`) |
| `server.py` | FastAPI app — dashboard, OTLP receiver, SSE stream, REST API |
| `cli.py` | Click-based command-line entry point |

---

## Scoring model

Each incident pattern is scored across three dimensions, then combined.

**Frequency (40% weight)** — how many times the pattern appeared, normalized 0–10 against the most frequent pattern in the batch.

**Severity (60% weight)** — weighted by failure classification:

| Classification | Weight |
|----------------|--------|
| `coordination_failure` | 1.0 |
| `agent_error` | 0.7 |
| `information_lag` | 0.5 |
| `environment_constraint` | 0.2 |

**Recovery** — did the agent succeed within 3 turns after the failure? If zero occurrences recovered, severity is multiplied by 1.5. This captures the difference between a transient hiccup and a pattern that gets agents stuck.

```
final_score = (frequency_score * 0.4) + (severity_score * 0.6)
```

### Why these weights?

Severity carries more weight than frequency because unrecovered coordination
failures propagate downstream — one planner desync can corrupt every
subsequent joint action in a run — while frequent `environment_constraint`
events (wall bumps, edge checks) tend to self-resolve within a turn or two.
A rare coordination failure is almost always worth more attention than a
common environment bump, and the 0.6 / 0.4 split reflects that ordering.

Within severity, the classification weights (`coordination_failure` 1.0 →
`environment_constraint` 0.2) were chosen so that each category maps to an
operational cost tier: *requires architectural change* (coordination),
*bug in a single agent* (agent_error), *stale belief state*
(information_lag), *expected friction* (environment_constraint).
Empirically, this ordering produced rankings on
`runs/phase4/events_seed42.ndjson` that matched the incidents a human
operator would triage first.

These weights are not a universal truth. They are starting defaults for
dungeon-trace-style workloads. If your system has different failure
economics, override them — `CLASSIFICATION_WEIGHTS` in
`src/triage/scorer.py` is a plain dict.

### Confidence

Each pattern is also tagged with a confidence level derived from how many
times it occurred:

```
confidence = min(1.0, occurrences / 5)
```

Patterns with fewer than 5 occurrences are labelled `low` or `medium`, so
that rankings built on thin evidence are surfaced as such rather than
presented with false precision. A pattern with a high final score but low
confidence should be treated as a hypothesis to investigate, not a
conclusion.

### Recovery dynamics

In addition to a binary "recovered within 3 turns" rate, the scorer reports
two dynamics signals:

- **Median recovery latency** — across events that did recover, how many
  turns it typically took. A pattern that always recovers in 1 turn is
  very different from one that always recovers in 3.
- **Tail risk** — the count of failures that remained unrecovered after
  10 turns. This is the "stuck for good" signal, distinct from "slow to
  recover".

### Cross-run recurrence

A pattern that hits once in a single run is noise. A pattern that hits in
every run is architectural. Each scored pattern exposes two recurrence
signals:

- **Appeared in X/Y runs** — how many of the analyzed runs contained this
  pattern at least once. A coordination failure appearing in 11/12 runs is
  a different animal from one that spikes in a single run.
- **Trend** — when the input has at least 6 runs (`2 ×
  TREND_WINDOW_SIZE`), the most recent 3 runs are compared against the
  3 runs immediately preceding them as a sliding window. Below 6 runs,
  the function falls back to a split-half partition so the label is
  still directional rather than always `insufficient data`. The
  pattern is labelled one of:

  | Label | Meaning |
  |-------|---------|
  | `new` | absent in the first half, present in the second — emerging |
  | `increasing` | second-half rate ≥ 1.3× first-half rate |
  | `stable` | second-half rate within ±30% of first-half rate |
  | `decreasing` | second-half rate ≤ 0.7× first-half rate |
  | `resolved` | present in the first half, absent in the second |
  | `insufficient data` | fewer than 3 runs — no trend emitted |

  Trend treats input order as a chronology proxy. If you pass files in
  a deterministic chronological order (e.g. `runs/*.ndjson` sorted by
  filename timestamp), the signal is meaningful; if you shuffle them,
  it is not. Sliding-window detection is more sensitive to recent
  changes than split-half: a regression that hits in the last few runs
  but was absent before will show as `increasing` or `new` rather than
  being averaged out by older history.

Recurrence is reported separately from severity to distinguish persistent
low-impact issues from rare catastrophic failures. Fusing it into the
final score would double-count frequency effects and hide the distinction
an operator needs to triage the two differently.

---

## When triage is *not* the right tool

`triage` is opinionated. It is useful when traces are labelled with
`failure_classification` and `action_succeeded`, multiple agents are
involved, and the same pattern recurs at least a few times. It is the
wrong tool when:

- **Single-agent pipelines.** The grouper keys on `agent_id`; with only
  one agent, incident patterns collapse into "failures by tool_name",
  which a two-line `jq` query handles better.
- **Unlabelled traces.** If `failure_classification` is null everywhere,
  every incident is `unclassified` and the severity weighting degenerates.
  Label first, then triage.
- **Streaming real-time observability.** `triage` is a batch tool
  designed for a morning summary. For sub-second alerting, use a
  streaming pipeline (OpenTelemetry, Prometheus) and reserve `triage`
  for the daily digest on top.
- **Small sample sizes (< 5 occurrences per pattern).** Confidence will
  be low across the board; the ranking is still directional but should be
  treated as a starting point for investigation, not a verdict.
- **Homogeneous agent architectures.** If every agent runs the same code
  path, `coordination_failure` vs `agent_error` stops being a meaningful
  axis and most of the signal comes from frequency alone, which is
  easier to get from a grouping query than from this tool.

---

## Observability for Agents Is Not Log Aggregation

Multi-agent systems fail in ways traditional log infrastructure was not
designed to surface. A coordination failure between two agents is
structurally different from a 500 error: it has a recovery story, a
belief-divergence story, and a recurrence story. Treating those as event
lines to aggregate loses everything that matters.

Four design choices follow from that stance, and together they are the
reason `triage` looks the way it does:

- **Top-3, not top-100.** Operators can act on three things at once. A
  list of fifty incidents is a dashboard; a dashboard is not triage.
- **Recovery latency, not just recovery.** "Did the agent succeed within
  3 turns" is a binary. "How long did it take, and is the tail bounded"
  is a control signal. Timeouts and retry budgets are tuned against the
  latter, not the former.
- **Recurrence orthogonal to severity.** Persistence and impact are
  different axes. Fusing them would let a high-frequency wall-bump
  outrank a rare planner desync — exactly the wrong prioritization for
  a multi-agent system, where coordination errors propagate downstream
  and environment errors do not.
- **Confidence as a first-class field.** A score without an error bar is
  a guess presented as a number. Surfacing `low` / `medium` / `high` per
  pattern lets the operator decide whether the ranking is a verdict or
  a hypothesis worth investigating.

These are not features. They are the modeling stance: measure dynamics
and persistence, not just events; show uncertainty rather than hide it;
rank by what an operator can act on, not by what is statistically
anomalous.

---

## Design decisions

### Why severity scoring instead of anomaly detection?
Anomaly detection tells you what is unusual. Severity scoring tells you what matters. A `coordination_failure` that happens on every run is not an anomaly — it's a chronic problem. Scoring surfaces chronic problems alongside rare-but-catastrophic ones using weights that reflect operational cost.

### Why prompt caching for the LLM?
Each pattern triggers one Claude call, but the system prompt is identical across calls. With Anthropic's `cache_control: ephemeral`, the system prompt is cached server-side after the first call and reused on subsequent ones, cutting per-call cost ~90% for batches of 3+ patterns.

### Why an embedded HTML dashboard instead of React/Next.js?
A pitch tool should run in 30 seconds, not 30 minutes. The dashboard ships as a single HTML file with Tailwind + Chart.js via CDN — no build step, no `node_modules`, no `npm install`. The template lives at `src/triage/templates/dashboard.html` and is loaded at server startup. Upgrading to a separate frontend later is straightforward; the backend already returns clean JSON.

### Why three top incidents?
Three is the number of things a person can hold in working memory while still being able to act on them. A list of 10 is noise. A list of 1 is overconfident. Three forces the model to prioritize, which is the core value proposition.

---

## Example reports

Four reports are checked in:

- [examples/monday-report.md](examples/monday-report.md) — a synthetic but
  realistic "Monday morning" report across three weekend runs. Shows the
  full set of signals: a high-confidence coordination failure with 0%
  recovery, a self-healing information lag, and a low-confidence tool
  error flagged for investigation. Generated from
  `runs/examples/monday.ndjson`.
- [examples/seed42-report.md](examples/seed42-report.md) — a report
  generated from `runs/phase4/events_seed42.ndjson` (real
  dungeon-traces output).
- [examples/compare-before-after.md](examples/compare-before-after.md) —
  a `triage compare` regression report between
  `runs/examples/before.ndjson` and `runs/examples/monday.ndjson`.
- [examples/otel-report.md](examples/otel-report.md) — same `triage
  report` invocation, but reading from `runs/examples/otel.json` (a
  small OpenTelemetry-spans file) to demonstrate that the adapter
  layer feeds the same scoring pipeline regardless of source format.

---

## Development

```bash
git clone https://github.com/thebharathkumar/agent-triage.git
cd agent-triage
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest --cov=triage --cov-report=term-missing   # tests
ruff check src/triage tests                     # lint
mypy src/triage                                 # type check
```

Quality gates enforced in CI:

- 220+ tests, 93%+ coverage
- `ruff check` clean
- `mypy --strict` clean

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for PR guidelines.

---

## Schema source

The original NDJSON trace format is compatible with [dungeon-traces](https://github.com/thebharathkumar/dungeon-traces) (`feature/viewer-enhanced` branch), a multi-agent simulation that produces NDJSON event logs for dungeon-navigation runs.

---

## License

[MIT](LICENSE) © 2026 Bharath kumar R
