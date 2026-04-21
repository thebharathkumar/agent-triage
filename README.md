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

See [examples/seed42-report.md](examples/seed42-report.md) for a real report generated from `runs/phase4/events_seed42.ndjson`.

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
