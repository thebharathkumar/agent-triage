# triage

A CLI tool that ingests multi-agent trace files and produces a ranked "what actually needs your attention this morning" severity report.

---

## The Problem

When multi-agent systems run at scale, they generate thousands of trace events per day. Most observability tools dump everything into a dashboard and leave triage to the human. That is the wrong default.

The signal you actually need at 9 AM is not "here are 847 events from last night." It is: "here are the three failure patterns that matter, ranked by how bad they are and whether the agents recovered."

`triage` is that tool. It reads agent trace data in NDJSON format, clusters events into incident patterns, scores each pattern across three dimensions (frequency, severity, recovery), and prints a ranked short list with plain-English explanations and a suggested next action for each.

---

## Quick Start

### Install

```bash
# Requires Python 3.11+
pip install -e ".[dev]"
```

### Run on a trace file

```bash
triage runs/phase4/events_seed42.ndjson
```

Or on multiple files (shell glob):

```bash
triage runs/phase4/*.ndjson
```

Write to a file instead of stdout:

```bash
triage runs/phase4/events_seed42.ndjson --output examples/seed42-report.md
```

### See all options

```bash
triage --help
```

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

Two reports are checked in:

- [examples/monday-report.md](examples/monday-report.md) — a synthetic but
  realistic "Monday morning" report across three weekend runs. Shows the
  full set of signals: a high-confidence coordination failure with 0%
  recovery, a self-healing information lag, and a low-confidence tool
  error flagged for investigation. Generated from
  `runs/examples/monday.ndjson`.
- [examples/seed42-report.md](examples/seed42-report.md) — a report
  generated from `runs/phase4/events_seed42.ndjson` (real
  dungeon-traces output).

---

## Running Tests

```bash
pytest --cov=src/triage --cov-report=term-missing
```

---

## Project Structure

```
src/triage/
  cli.py        Entry point (argparse via Click)
  loader.py     NDJSON parsing and Pydantic schema validation
  grouper.py    Incident pattern detection and clustering
  scorer.py     Severity scoring model
  reporter.py   Markdown report generation

tests/
  conftest.py       Shared fixtures and event factory
  test_loader.py    Loader unit tests
  test_grouper.py   Grouper unit tests
  test_scorer.py    Scorer unit tests

runs/phase4/        Sample trace files
examples/           Generated example reports
```

---

## Schema Source

Trace format is compatible with [dungeon-traces](https://github.com/thebharathkumar/dungeon-traces) (`feature/viewer-enhanced` branch), a multi-agent simulation that produces NDJSON event logs for dungeon-navigation runs.
