# triage

A CLI tool that ingests multi-agent trace files and produces a ranked "what actually needs your attention this morning" severity report.

---

## The Problem

When multi-agent systems run at scale, they generate thousands of trace events per day. Most observability tools dump everything into a dashboard and leave triage to the human. That is the wrong default.

The signal you actually need at 9 AM is not "here are 847 events from last night." It is: "here are the three failure patterns that matter, ranked by how bad they are and whether the agents recovered."

`triage` is that tool. It reads agent trace data (NDJSON natively, OpenTelemetry spans via the built-in adapter, and any custom format you can plug in), clusters events into incident patterns, scores each pattern across three dimensions (frequency, severity, recovery), and prints a ranked short list with plain-English explanations and a suggested next action for each. It also has a `compare` mode for diffing two batches.

---

## Quick Start

### Install

```bash
# Requires Python 3.11+
pip install -e ".[dev]"
```

### Run on a trace file

```bash
triage report runs/phase4/events_seed42.ndjson
```

Or on multiple files (shell glob):

```bash
triage report runs/phase4/*.ndjson
```

Write to a file instead of stdout:

```bash
triage report runs/phase4/events_seed42.ndjson --output examples/seed42-report.md
```

### Compare two batches

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
  and mean final score, with deltas, so the "did things get better
  overall" question is answered before any drill-down
- incident **frequency deltas** per classification (`down 39%`, `new`,
  `resolved`, `stable`, etc.)
- **recovery latency changes** — median turns-to-first-success for
  each classification, before vs after
- **unrecovered-count changes** — number of failures that did not
  recover within the window
- **newly emerging patterns** — agent / tool / classification /
  divergence-fields signatures present only in `after`
- **resolved patterns** — signatures present only in `before`
- **persisting patterns** — signatures present in both, with their
  before/after counts

Changes based on small samples (fewer than 5 occurrences on the larger
side) are tagged `(tentative)` so a thin-evidence delta is not read as
a verified one. See
[examples/compare-before-after.md](examples/compare-before-after.md)
for a real comparison.

### See all options

```bash
triage --help
triage report --help
triage compare --help
```

---

## Trace formats and adapters

`triage` is plugin-shaped on the input side. Two adapters ship in the
box:

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

## Input Format

Each line in an NDJSON file represents one agent turn. Required fields:

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | Unique event identifier |
| `run_id` | string | Which run this event belongs to |
| `turn` | int | Turn number within the run |
| `agent_id` | string | Which agent acted |
| `action_taken.tool_name` | string | What action was attempted |
| `action_succeeded` | bool | Whether the action succeeded |
| `failure_classification` | string or null | One of: `coordination_failure`, `agent_error`, `information_lag`, `environment_constraint`, or null |
| `divergence_fields` | list[string] | Fields where belief diverged from world truth |

---

## Scoring Model

Each incident pattern is scored across three dimensions, then combined into a final score.

### 1. Frequency (40% weight)

How many times did this pattern appear across all analyzed runs? Normalized to a 0-10 scale relative to the most frequent pattern in the batch.

### 2. Severity (60% weight, before recovery adjustment)

Weighted by failure classification:

| Classification | Weight |
|----------------|--------|
| `coordination_failure` | 1.0 |
| `agent_error` | 0.7 |
| `information_lag` | 0.5 |
| `environment_constraint` | 0.2 |

### 3. Recovery

Did the agent succeed within 3 turns after the failure? If zero occurrences recovered, severity is multiplied by 1.5. This captures the difference between a transient hiccup and a pattern that gets agents stuck.

### Final Score

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

## Design Decisions

### Why severity scoring instead of anomaly detection?

Anomaly detection tells you what is unusual. Severity scoring tells you what matters. In a multi-agent system with nondeterministic behavior, many things will be statistically unusual that are operationally irrelevant. A `coordination_failure` that happens on every run is not an anomaly; it is a chronic problem. Scoring lets you surface chronic problems alongside rare-but-catastrophic ones using weights that reflect real operational cost.

### Why markdown output instead of JSON?

JSON is for machines. This tool is for the engineer with one cup of coffee at 9 AM. Markdown renders cleanly in GitHub, in a terminal with `glow`, in a Slack message, and in a PR comment. The goal is a report you can read in three minutes, not a format you have to parse. If you need JSON for downstream tooling, that is a valid future flag.

### Why CLI instead of a dashboard?

Dashboards require uptime, hosting, auth, and a browser. A CLI requires none of that. It runs in a CI pipeline, in a pre-commit hook, in a cron job, or interactively. The output is a file you can commit to git. Building a dashboard version on top of this scoring engine is straightforward once the scoring model is validated.

### Why 3 top incidents?

Three is the number of things a person can hold in working memory while still being able to act on them. A list of 10 incidents is noise. A list of 1 is overconfident. Three forces the model to prioritize, which is the core value proposition.

---

## Example Output

Three reports are checked in:

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
  `runs/examples/before.ndjson` and `runs/examples/monday.ndjson`,
  showing the score-summary panel, classification deltas, a
  newly-emerging tool error, and a resolved environment-constraint
  pattern.
- [examples/otel-report.md](examples/otel-report.md) — same `triage
  report` invocation, but reading from `runs/examples/otel.json` (a
  small OpenTelemetry-spans file) to demonstrate that the adapter
  layer feeds the same scoring pipeline regardless of source format.

---

## Running Tests

```bash
pytest --cov=src/triage --cov-report=term-missing
```

---

## Project Structure

```
src/triage/
  cli.py        Entry point — Click group with `report` and `compare` subcommands
  adapters.py   Pluggable trace-format adapters (NDJSON, OTel) + TraceAdapter protocol
  loader.py     TraceEvent schema + format-dispatching load_files
  grouper.py    Incident pattern detection and clustering
  scorer.py     Severity scoring + sliding-window trend detection
  comparer.py   Before/after diff: deltas, pattern set diff, score summary
  reporter.py   Markdown report generation (per-batch and comparison)

tests/
  conftest.py       Shared fixtures and event factory
  test_loader.py    Loader unit tests
  test_grouper.py   Grouper unit tests
  test_scorer.py    Scorer unit tests
  test_comparer.py  Comparer unit tests
  test_adapters.py  Adapter protocol and built-in NDJSON / OTel tests

runs/phase4/        Sample trace files
runs/examples/      Synthetic traces driving the example reports
examples/           Generated example reports
```

---

## Schema Source

Trace format is compatible with [dungeon-traces](https://github.com/thebharathkumar/dungeon-traces) (`feature/viewer-enhanced` branch), a multi-agent simulation that produces NDJSON event logs for dungeon-navigation runs.

---

## License

[MIT](LICENSE) © 2026 Bharath kumar R
