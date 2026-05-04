# Triage Report

Generated: 2026-04-25 08:38 UTC

**1 run(s) analyzed. 6 incident pattern(s) detected. Top 3 worth your attention this morning.**

Sources: `runs/phase4/events_seed42.ndjson`

---

## #1 - [B] move / agent_error / no-divergence

**Category:** Agent Error

| Metric | Value |
|--------|-------|
| Severity Score | 10.50 / 15.00 |
| Frequency | 4 event(s) across 1 run(s) |
| Appeared in | 1/1 runs |
| Trend | insufficient data |
| Recovery Rate | [----------] 0% |
| Recovery Latency | no recoveries observed |
| Tail Risk | 4 failures unrecovered after 10 turns |
| Confidence | high (4 occurrences) |
| Final Score | **10.30** |

**Why this matters:**

Agent B hit a **Agent Error** on `move` 4 time(s) across 1 run. None of these failures were followed by a successful action within 3 turns, meaning the agent got stuck rather than adapting. With a final score of 10.30, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Inspect the agent's reasoning trace at the failing turns; check if the action selection logic handles boundary states.

---

## #2 - [A] move / agent_error / no-divergence

**Category:** Agent Error

| Metric | Value |
|--------|-------|
| Severity Score | 10.50 / 15.00 |
| Frequency | 3 event(s) across 1 run(s) |
| Appeared in | 1/1 runs |
| Trend | insufficient data |
| Recovery Rate | [----------] 0% |
| Recovery Latency | no recoveries observed |
| Tail Risk | 3 failures unrecovered after 10 turns |
| Confidence | medium (3 occurrences) |
| Final Score | **9.30** |

**Why this matters:**

Agent A hit a **Agent Error** on `move` 3 time(s) across 1 run. None of these failures were followed by a successful action within 3 turns, meaning the agent got stuck rather than adapting. With a final score of 9.30, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Inspect the agent's reasoning trace at the failing turns; check if the action selection logic handles boundary states.

---

## #3 - [A] move / coordination_failure / other_agent_position

**Category:** Coordination Failure

| Metric | Value |
|--------|-------|
| Severity Score | 10.00 / 15.00 |
| Frequency | 1 event(s) across 1 run(s) |
| Appeared in | 1/1 runs |
| Trend | insufficient data |
| Recovery Rate | [##########] 100% |
| Recovery Latency | median recovery latency: 1 turn |
| Tail Risk | no tail risk |
| Confidence | low (1 occurrence) |
| Final Score | **7.00** |

**Why this matters:**

Agent A hit a **Coordination Failure** on `move` 1 time(s) across 1 run. About 100% of occurrences were followed by a successful action within 3 turns (median latency 1 turn), suggesting partial self-correction. Belief divergence was present on field(s): other_agent_position, meaning the agent was operating on stale world state when it failed. With a final score of 7.00, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Review inter-agent messaging cadence and add position-sync checkpoints before high-stakes joint actions.

---
