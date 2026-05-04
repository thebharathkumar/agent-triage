# Triage Report

Generated: 2026-04-25 08:38 UTC

**3 run(s) analyzed. 3 incident pattern(s) detected. Top 3 worth your attention this morning.**

Sources: `runs/examples/monday.ndjson`

---

## #1 - [planner] dispatch_subtask / coordination_failure / retriever_state+task_queue_head

**Category:** Coordination Failure

| Metric | Value |
|--------|-------|
| Severity Score | 15.00 / 15.00 |
| Frequency | 11 event(s) across 3 run(s) |
| Appeared in | 3/3 runs |
| Trend | stable |
| Recovery Rate | [----------] 0% |
| Recovery Latency | no recoveries observed |
| Tail Risk | 11 failures unrecovered after 10 turns |
| Confidence | high (11 occurrences) |
| Final Score | **13.00** |

**Why this matters:**

Agent planner hit a **Coordination Failure** on `dispatch_subtask` 11 time(s) across 3 runs. Appeared in 3/3 runs; trend is stable. None of these failures were followed by a successful action within 3 turns, meaning the agent got stuck rather than adapting. Belief divergence was present on field(s): retriever_state, task_queue_head, meaning the agent was operating on stale world state when it failed. With a final score of 13.00, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Review inter-agent messaging cadence and add position-sync checkpoints before high-stakes joint actions.

---

## #2 - [executor] call_external_api / agent_error / no-divergence

**Category:** Agent Error

| Metric | Value |
|--------|-------|
| Severity Score | 10.50 / 15.00 |
| Frequency | 2 event(s) across 2 run(s) |
| Appeared in | 2/3 runs |
| Trend | new |
| Recovery Rate | [----------] 0% |
| Recovery Latency | no recoveries observed |
| Tail Risk | 2 failures unrecovered after 10 turns |
| Confidence | medium (2 occurrences) |
| Final Score | **7.03** |

**Why this matters:**

Agent executor hit a **Agent Error** on `call_external_api` 2 time(s) across 2 runs. Appeared in 2/3 runs; this is a newly-emerging pattern. None of these failures were followed by a successful action within 3 turns, meaning the agent got stuck rather than adapting. With a final score of 7.03, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Inspect the agent's reasoning trace at the failing turns; check if the action selection logic handles boundary states.

---

## #3 - [retriever] fetch_documents / information_lag / index_version

**Category:** Information Lag

| Metric | Value |
|--------|-------|
| Severity Score | 5.00 / 15.00 |
| Frequency | 6 event(s) across 3 run(s) |
| Appeared in | 3/3 runs |
| Trend | stable |
| Recovery Rate | [##########] 100% |
| Recovery Latency | median recovery latency: 2 turns |
| Tail Risk | no tail risk |
| Confidence | high (6 occurrences) |
| Final Score | **5.18** |

**Why this matters:**

Agent retriever hit a **Information Lag** on `fetch_documents` 6 time(s) across 3 runs. Appeared in 3/3 runs; trend is stable. About 100% of occurrences were followed by a successful action within 3 turns (median latency 2 turns), suggesting partial self-correction. Belief divergence was present on field(s): index_version, meaning the agent was operating on stale world state when it failed. With a final score of 5.18, this pattern ranks high because its classification carries significant weight in the scoring model.

**Suggested next action:** Increase belief-state refresh frequency or add an explicit re-observe step after N turns without a sync message.

---
